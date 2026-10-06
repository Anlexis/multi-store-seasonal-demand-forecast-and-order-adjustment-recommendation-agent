# RET-C2-344 — The output boundary: what may be released, and what must not be.
#
# Two properties, deliberately kept apart because they are different and each
# has its own mutant:
#
#   containment — a violation must not carry the rejected text onward. The
#       output gate is the ONLY containment point on the error path: the
#       backbone's status route sends every non-success straight to finalize,
#       skipping post_process, and the framework's own get_output returns
#       `formatted_output or result` with no status check. Returning an error
#       is not containment; replacing the released text is.
#
#   closed-set error contract — the caller-visible failure carries only values
#       this template chose from a declared set. Never an exception's message,
#       an upstream response body, a path, or anything the caller supplied.
#
# The boundary measured here is the INNER graph's own output — the mapping
# _DomainGraphNode.merge_output() consumes. That is the boundary this gate
# guards, and it is where removing the gate's clearing is observable.
#
# A NOTE ON WHAT THIS CANNOT PROVE, stated rather than left implicit: on the
# error path the outer GraphNode raises SubgraphError BEFORE merge_output runs,
# so the inner text does not reach the caller today even with no clearing at
# all. That makes the shipped leak MASKED rather than live — the gate was one
# strategy change away from publishing the rejected report, not publishing it
# this morning. Containment is therefore asserted at the inner boundary, where
# it is falsifiable, and the caller envelope is asserted separately as a
# backstop in tests/integration/test_invoke_e2e.py.

import json

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.domain_workflow_graph import DomainWorkflowGraph
from src.nodes.output_validate_node import (
    REASON_DISCLAIMER_MISSING,
    REASON_EMPTY_OUTPUT,
    REASON_SECRET_IN_OUTPUT,
    REASON_UPSTREAM_FAILURE,
    WITHHELD_NOTICE,
    OutputValidateNode,
)
from src.schemas.state import to_json

_DISCLAIMER = "--- DISCLAIMER ---\nAI advisory only.\n--- END DISCLAIMER ---"


def _secret() -> str:
    """Assembled at runtime — a literal would be a finding in its own right."""
    return "AKIA" + "Q" * 16


def _sentinel() -> str:
    """A recognisable string standing in for node-authored error text.

    Shaped like the thing the closed-set rule exists to keep out: a record
    identifier and a name, carried inside an upstream response body.
    """
    return "boom: upstream said {'customer':'A. Tanaka','order':'ORD-99812'}"


def _report_with(extra: str) -> str:
    return f"Demand Forecast Report\n{extra}\n\n{_DISCLAIMER}\n"


# ── containment ─────────────────────────────────────────────────────────────


class TestContainment:
    def setup_method(self):
        self.gate = OutputValidateNode()

    def test_a_released_secret_is_replaced_not_merely_flagged(self):
        secret = _secret()
        result = self.gate.execute({"output_text": _report_with(f"token {secret}"), "disclaimer_present": True})

        assert result["status"] == AgentStatus.ERROR.value
        assert result["error_code"] == REASON_SECRET_IN_OUTPUT
        assert result["output_text"] == WITHHELD_NOTICE
        assert secret not in json.dumps(result, default=str)
        # And the rest of the report goes with it — withholding is not redaction.
        assert "Demand Forecast Report" not in json.dumps(result, default=str)

    def test_every_output_bearing_field_is_written_not_merely_omitted(self):
        """Partial deltas are MERGED, so an omitted field keeps its old value.

        A gate that returns `{"status": "error"}` and nothing else leaves the
        rejected text exactly where it was.
        """
        result = self.gate.execute({"output_text": _report_with(f"token {_secret()}"), "disclaimer_present": True})
        assert "output_text" in result
        assert "disclaimer_present" in result

    def test_the_replacement_is_truthy(self):
        """A falsy replacement re-opens the framework's `formatted_output or result`.

        The fallback has no status check, so an empty string here would hand
        the caller whatever else survived in state.
        """
        result = self.gate.execute({"output_text": _report_with("x"), "disclaimer_present": False})
        assert bool(result["output_text"]) is True

    def test_the_inner_graph_output_carries_no_released_text(self):
        """The boundary merge_output() consumes.

        Driven through the real inner graph, not by calling the node: this is
        the mapping that would cross into the outer state.
        """
        graph = DomainWorkflowGraph(config={"timeout_s": 30})
        secret = _secret()
        sub_result = graph.get_output(
            {
                "output_text": WITHHELD_NOTICE,
                "status": AgentStatus.ERROR.value,
                "agent_status": AgentStatus.ERROR,
                "error_code": REASON_SECRET_IN_OUTPUT,
                "error_message": REASON_SECRET_IN_OUTPUT,
            }
        )
        assert sub_result["output_text"] == WITHHELD_NOTICE
        assert sub_result["result"] == WITHHELD_NOTICE
        assert secret not in json.dumps(sub_result, default=str)

    @staticmethod
    def _pipeline_with_leaky_store_id(secret: str):
        """Build the real inner graph with a client that renders *secret*.

        The forecast client is the DATA path. The gate itself is untouched, so
        what this measures is the gate rather than the fault injection.
        """
        graph = DomainWorkflowGraph(config={"timeout_s": 30})
        # compile() calls register_nodes() itself, replacing every node
        # instance — so the client has to be swapped AFTER it, or the patch is
        # silently discarded and the test measures an unmodified pipeline.
        graph.compile()
        graph._nodes["forecast_call"]._client = lambda store_id, sku, horizon: {
            "store_id": f"store {secret}",
            "sku_category": sku,
            "horizon_days": horizon,
            "forecast_units": [10] * horizon,
            "confidence_low": [8] * horizon,
            "confidence_high": [12] * horizon,
            "model": "probe",
        }
        # The invocation context carries the caller's trust level. Without it
        # every domain node is denied at the trust gate and the pipeline
        # produces nothing — which would make the assertions below pass while
        # measuring an empty run rather than the gate.
        return graph.invoke(
            json.dumps({"raw_input": "yogurt kanto_12 14 days"}),
            ctx=InvocationContext(session_id="containment", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL),
        )

    @pytest.mark.parametrize(
        "secret",
        [" ".join(["4111"] * 4), "password=hunter2please"],
        ids=["card number", "assignment form"],
    )
    def test_a_secret_the_framework_does_not_model_is_contained_by_this_gate(self, secret):
        """The local half of the union, proven end to end.

        Neither of these shapes appears in the framework's pattern set, so
        each reaches the renderer and is caught only because the local
        patterns were kept rather than delegated away. Swapping the local set
        for the framework's would make the gate narrower while looking like a
        tightening — these two cases are what would stop failing.
        """
        out = self._pipeline_with_leaky_store_id(secret)

        assert out["status"] == AgentStatus.ERROR.value
        assert out["error_code"] == REASON_SECRET_IN_OUTPUT
        assert out["output_text"] == WITHHELD_NOTICE
        assert secret not in json.dumps(out, default=str)

    def test_a_secret_the_framework_does_model_never_reaches_this_gate(self):
        """The framework's own output scan fires first, upstream of the gate.

        Recorded because it explains a difference in the envelope, not because
        it is a weaker outcome: the run ends with no error_code of ours and an
        empty output, since the framework's error delta carries neither. The
        secret is contained either way; the caller just learns less.
        """
        out = self._pipeline_with_leaky_store_id(_secret())

        assert out["status"] == AgentStatus.ERROR.value
        assert _secret() not in json.dumps(out, default=str)

    def test_the_same_pipeline_produces_a_real_report_without_the_secret(self):
        """The positive control for the test above.

        Without it, a pipeline that produced nothing at all would look like a
        pipeline whose gate contained a leak.
        """
        graph = DomainWorkflowGraph(config={"timeout_s": 30})
        graph.compile()
        out = graph.invoke(
            json.dumps({"raw_input": "yogurt kanto_12 14 days"}),
            ctx=InvocationContext(session_id="control", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL),
        )

        assert out["status"] == AgentStatus.SUCCESS.value
        assert "Demand Forecast Report" in out["output_text"]
        assert "--- DISCLAIMER ---" in out["output_text"]


# ── the closed-set error contract ───────────────────────────────────────────


_ERROR_PATH_STATES = {
    "empty output": ({"output_text": "", "disclaimer_present": False}, REASON_EMPTY_OUTPUT),
    "no disclaimer": (
        {"output_text": "a report", "disclaimer_present": False},
        REASON_DISCLAIMER_MISSING,
    ),
    "flag set but marker absent": (
        {"output_text": "a report", "disclaimer_present": True},
        REASON_DISCLAIMER_MISSING,
    ),
    "secret in output": (
        {"output_text": _report_with("token " + "AKIA" + "Q" * 16), "disclaimer_present": True},
        REASON_SECRET_IN_OUTPUT,
    ),
}


class TestClosedSetErrorContract:
    def setup_method(self):
        self.gate = OutputValidateNode()

    @pytest.mark.parametrize(
        "state,expected",
        list(_ERROR_PATH_STATES.values()),
        ids=list(_ERROR_PATH_STATES),
    )
    def test_every_error_path_uses_a_declared_label(self, state, expected):
        """Parameterised over EVERY path, not one of them.

        A contract asserted on a single path says nothing about the others,
        and it is always one of the others that carries the text.
        """
        result = self.gate.execute(dict(state))
        assert result["error_code"] == expected
        assert result["error_message"] == expected

    @pytest.mark.parametrize("state", list(s for s, _ in _ERROR_PATH_STATES.values()), ids=list(_ERROR_PATH_STATES))
    def test_a_node_authored_sentinel_appears_nowhere_in_the_returned_mapping(self, state):
        """Seed the internal channels with recognisable text and walk the result.

        error_log, error_message and validation_error are all internal
        channels a node may write. None of them may be projected into what the
        caller reads.
        """
        sentinel = _sentinel()
        seeded = dict(state)
        seeded["error_log"] = [sentinel]
        seeded["error_message"] = sentinel
        seeded["validation_error"] = sentinel

        result = self.gate.execute(seeded)
        assert sentinel not in json.dumps(result, default=str)

    def test_an_upstream_failure_is_reported_as_a_label_not_as_its_message(self):
        sentinel = _sentinel()
        result = self.gate.execute(
            {
                "status": AgentStatus.ERROR.value,
                "agent_status": AgentStatus.ERROR,
                "error_code": sentinel,
                "error_message": sentinel,
                "output_text": _report_with("partial"),
                "disclaimer_present": True,
            }
        )
        # An error_code that is not one of ours is not passed through.
        assert result["error_code"] == REASON_UPSTREAM_FAILURE
        assert sentinel not in json.dumps(result, default=str)
        assert result["output_text"] == WITHHELD_NOTICE

    def test_an_upstream_label_from_our_own_set_is_preserved(self):
        """The negative control: the filter is not simply discarding everything."""
        result = self.gate.execute(
            {
                "status": AgentStatus.ERROR.value,
                "error_code": REASON_EMPTY_OUTPUT,
                "output_text": _report_with("partial"),
                "disclaimer_present": True,
            }
        )
        assert result["error_code"] == REASON_EMPTY_OUTPUT


# ── a failure upstream is not erased by the next node's success ──────────────


class TestUpstreamFailurePropagates:
    """The domain topology is linear, so without this the last writer wins.

    A node that failed would be followed by one that succeeded, and the run
    would report success with a degraded report — the failure not handled but
    erased.
    """

    @pytest.mark.parametrize(
        "node_path,extra_state",
        [
            ("src.nodes.data_validate_node.DataValidateNode", {}),
            ("src.nodes.multi_store_forecast_node.MultiStoreForecastCallNode", {}),
            ("src.nodes.forecast_aggregation_node.ForecastAggregationNode", {}),
            ("src.nodes.food_waste_risk_node.FoodWasteRiskCalcNode", {}),
            ("src.nodes.order_adjust_node.OrderAdjustCalcNode", {}),
            ("src.nodes.output_format_node.OutputFormatNode", {}),
        ],
    )
    def test_a_failed_run_passes_through_untouched(self, node_path, extra_state):
        import importlib

        module_name, class_name = node_path.rsplit(".", 1)
        node = getattr(importlib.import_module(module_name), class_name)()
        state = {
            "status": AgentStatus.ERROR.value,
            "agent_status": AgentStatus.ERROR,
            "error_code": "forecast_call_failed",
            "aggregated_forecast": to_json({"peak_demand_total": 1}),
            "raw_forecast_results": to_json([{"store_id": "s", "forecast_units": [1]}]),
            "sku_category": "yogurt",
            **extra_state,
        }
        assert node.execute(state) == {}

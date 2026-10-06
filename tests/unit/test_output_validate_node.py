# RET-C2-344 — Unit Tests: OutputValidateNode (S-3 output gate)
#
# Inner DomainWorkflowGraph node 8 (final). The S-3 gate is implemented as the
# custom-named ADR-017 `_run_s3_domain_gate` domain method (NOT the SDK-auto-wrapped
# `_extra_security_gate_output` hook); execute() delegates straight to it.
# Asserted against the MERGED develop logic:
#   Check 1: output_text None/empty -> agent_status=ERROR.
#   Check 2: disclaimer_present False OR "--- DISCLAIMER ---" absent from text
#            -> agent_status=ERROR, error_message mentions DISCLAIMER.
#   Check 3: a 16-digit card-number PII pattern in output_text -> agent_status=ERROR.
#   Pass:    non-empty text + disclaimer + no PII -> agent_status=SUCCESS.
#   agent_status is the AgentStatus enum (asserted via .name).
#
# S-4 audit muted at the node module via an autouse fixture (NEVER stub shared.*).

import pytest

from src.nodes.output_validate_node import (
    REASON_DISCLAIMER_MISSING,
    REASON_EMPTY_OUTPUT,
    REASON_SECRET_IN_OUTPUT,
    WITHHELD_NOTICE,
    OutputValidateNode,
)
from framework.schemas.agent_status import AgentStatus


@pytest.fixture(autouse=True)
def _mute_audit(monkeypatch):
    """Silence the S-4 audit free function at the node module under test."""
    monkeypatch.setattr(
        "src.nodes.output_validate_node.emit_trace_event",
        lambda *a, **k: None,
    )


_GOOD_OUTPUT = (
    "Demand Forecast Report\nRegional Summary ...\n\n" "--- DISCLAIMER ---\nAI advisory only.\n--- END DISCLAIMER ---\n"
)


class TestOutputValidateNode:
    """Unit tests for OutputValidateNode (S-3 gate via _run_s3_domain_gate)."""

    def setup_method(self):
        self.node = OutputValidateNode()

    def test_pass_with_disclaimer(self):
        """Valid output with DISCLAIMER -> agent_status=SUCCESS."""
        result = self.node.execute({"output_text": _GOOD_OUTPUT, "disclaimer_present": True})

        assert result["agent_status"] is AgentStatus.SUCCESS
        assert result["status"] == getattr(AgentStatus.SUCCESS, "value")
        assert result["error_message"] is None

    def test_missing_disclaimer_is_error(self):
        """disclaimer_present False (and no marker) -> ERROR, text withheld.

        The label is asserted as the module's own constant rather than as a
        substring of prose: an assertion on wording passes on a gate that
        changed its message and fails on one that only reworded it.
        """
        result = self.node.execute({"output_text": "forecast without the block", "disclaimer_present": False})

        assert result["agent_status"] is AgentStatus.ERROR
        assert result["agent_status"].name == "ERROR"
        assert result["error_code"] == REASON_DISCLAIMER_MISSING
        assert result["output_text"] == WITHHELD_NOTICE
        assert "forecast without the block" not in str(result)

    def test_flag_true_but_marker_absent_is_error(self):
        """Even if disclaimer_present=True, the gate cross-checks the text marker."""
        result = self.node.execute({"output_text": "no marker here at all", "disclaimer_present": True})

        assert result["agent_status"] is AgentStatus.ERROR
        assert result["error_code"] == REASON_DISCLAIMER_MISSING
        assert result["output_text"] == WITHHELD_NOTICE

    def test_none_output_is_error(self):
        """output_text=None -> ERROR (empty-output check)."""
        result = self.node.execute({"output_text": None, "disclaimer_present": False})

        assert result["agent_status"] is AgentStatus.ERROR
        assert result["error_code"] == REASON_EMPTY_OUTPUT

    def test_empty_string_output_is_error(self):
        """output_text='' -> ERROR (empty-output check)."""
        result = self.node.execute({"output_text": "", "disclaimer_present": False})

        assert result["agent_status"] is AgentStatus.ERROR
        assert result["error_code"] == REASON_EMPTY_OUTPUT

    def test_card_number_pii_is_error(self):
        """A 16-digit card-number pattern trips the output guard -> ERROR.

        The framework's own credential detector carries no card-number
        pattern, so this case is only caught because the local pattern set is
        kept as part of the union rather than delegated away.
        """
        card = "4111-1111-1111-1111"
        text = _GOOD_OUTPUT + f"\nLeaked card {card} oops"
        result = self.node.execute({"output_text": text, "disclaimer_present": True})

        assert result["agent_status"] is AgentStatus.ERROR
        assert result["error_code"] == REASON_SECRET_IN_OUTPUT
        # The point of the gate: the violating text does not travel onward.
        assert card not in str(result)
        assert result["output_text"] == WITHHELD_NOTICE

    def test_run_s3_domain_gate_is_used(self):
        """execute() delegates to the ADR-017 _run_s3_domain_gate domain method.

        The gate is intentionally NOT named _extra_security_gate_output: the
        released SDK auto-wraps that hook into the LangGraph chain (clean path
        returns None → next node gets state=None → AttributeError at .invoke()).
        execute() calls the custom-named _run_s3_domain_gate explicitly.
        """
        # The custom-named gate exists and returns the partial dict directly ...
        assert hasattr(self.node, "_run_s3_domain_gate")
        assert "_run_s3_domain_gate" in OutputValidateNode.__dict__
        # ... and the SDK-auto-wrapped hook must NOT be defined on the subclass.
        assert "_extra_security_gate_output" not in OutputValidateNode.__dict__, (
            "_extra_security_gate_output is SDK-auto-wrapped — must not be defined; "
            "use the explicit _run_s3_domain_gate instead"
        )
        direct = self.node._run_s3_domain_gate({"output_text": _GOOD_OUTPUT, "disclaimer_present": True})
        assert direct["agent_status"] is AgentStatus.SUCCESS

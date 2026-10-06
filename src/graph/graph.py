"""AgentCore Platform v1.0"""

# RET-C2-344 — Outer graph (AgentBaseGraph; Cat 2 two-layer nested architecture)
#
# Architecture (Cat 2 ToolCalling, L1-direct):
#
#   Outer backbone (fixed — identical to Cat 1, do NOT override add_edges()):
#     START → initialize → pre_process → main → {route} → post_process → finalize → END
#                                             ↓ (RETRY, bounded by max_retry)
#                                          pre_process
#
#   The `main` slot is a GraphNode subclass (_DomainGraphNode) that delegates
#   the full domain workflow to DomainWorkflowGraph (inner BaseGraph). All eight
#   domain steps — including the input gate (DataValidate) and the output gate
#   (OutputValidate) — live inside the inner graph; the outer backbone is never
#   modified.
#
# ⚠️ route() sends every non-SUCCESS status to `finalize`, skipping
#    post_process. So the outer post_process slot is NOT a containment point:
#    anything that must hold on an error path has to hold in the node that
#    produced the error. The output gate inside the inner graph is where that
#    happens.
#
# Directory layout:
#   src/graph/graph.py                 ← outer graph (this file)
#   src/graph/domain_workflow_graph.py ← inner graph (8-node domain topology)
#   src/graph/context_bridge.py        ← validated-request hand-off across the boundary

import json
import logging
from typing import Any, ClassVar, Dict

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.function_node import FunctionNode
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event
from src.graph.context_bridge import clear_forecast_request, set_forecast_request
from src.schemas.state import State, from_json, to_json
from src.services import input_guard

logger = logging.getLogger(__name__)

# Caller-visible notice for a request refused at the outer boundary. A constant:
# it names no field value and carries no node-authored text. It is truthy on
# purpose — AgentBaseGraph.get_output() selects `formatted_output or result`
# with no status check, so a falsy value here would re-open the fallback onto
# whatever else survived in state.
_REFUSED_NOTICE = "Request refused: the forecast request did not satisfy the input contract."


class _PreProcessPassthroughNode(FunctionNode):
    """Outer pre_process slot — the caller contract for the registry path.

    src/api/server.py validates the same contract, but the registry calls
    agent.invoke() directly and never loads that module. So the check has to
    exist here as well, or it is absent on the path most deployments use.

    Refusal is enforced HERE rather than left to the framework's input gate:
    where that gate is configured off, a payload it would have blocked reaches
    the answer path instead, which is a fail-open on the decision the screen
    exists to make.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        query = state.get("user_input", "")
        accepted_context, refusals = input_guard.validate_context(state.get("input_context"))
        _, query_reason = input_guard.validate_query(query)

        if query_reason or refusals:
            reason = query_reason or refusals[0][1]
            field = "user_input" if query_reason else refusals[0][0]
            emit_trace_event(
                "caller_contract_refused",
                {"field": field, "reason": reason},
                state,
            )
            logger.info("pre_process: refused caller contract (field=%s)", field)
            return {
                # Truthy, constant, and free of anything the caller supplied.
                "result": _REFUSED_NOTICE,
                "output_text": _REFUSED_NOTICE,
                "error_code": reason,
                "error_message": reason,
                "status": AgentStatus.ERROR.value,
                "agent_status": AgentStatus.ERROR,
            }

        emit_trace_event(
            "caller_contract_accepted",
            {"context_fields": sorted(accepted_context)},
            state,
        )
        return {
            "validated_request": to_json(accepted_context),
            "status": AgentStatus.SUCCESS.value,
            "agent_status": AgentStatus.SUCCESS,
        }


class _PostProcessPassthroughNode(FunctionNode):
    """Outer post_process slot — reached only on the SUCCESS route.

    The output gate runs inside the inner graph (OutputValidateNode). This slot
    records that the answer crossed the outer boundary and carries the status
    forward; it deliberately adds no second output guard, because a guard here
    would contain the inner gate's failures on its own and make the inner
    gate's own clearing impossible to falsify.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        emit_trace_event(
            "answer_released",
            {"length": len(state.get("output_text") or "")},
            state,
        )
        return {
            "status": state.get("status", AgentStatus.SUCCESS.value),
            "agent_status": state.get("agent_status", AgentStatus.SUCCESS),
        }


class _DomainGraphNode(GraphNode):
    """GraphNode subclass assigned to the `main` slot of DemandForecastAgent.

    Wraps DomainWorkflowGraph (inner Cat 2 BaseGraph). Called by the
    AgentBaseGraph backbone after pre_process and before route().

    Contracts:
      get_subgraph()  — instantiate and return DomainWorkflowGraph
      extract_input() — the request string handed to inner_graph.invoke()
      merge_output()  — map the inner sub_result into the outer state delta
                        (changed keys only)
      error_strategy  — "propagate": re-raise inner errors as SubgraphError
                        (fail-fast default)
    """

    # GraphNode is not covered by the framework's declare-or-raise rule (that
    # applies to FunctionNode subclasses), so an omission here does not fail
    # loudly — it silently inherits the permissive base default and the main
    # slot runs at ANONYMOUS while every node inside it requires more.
    # Declared explicitly, at the level the manifest declares for the agent.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    # "propagate": re-raise inner graph exceptions as SubgraphError (fail fast).
    error_strategy: ClassVar[str] = "propagate"

    # False: interrupts are handled inside the inner graph only.
    propagate_hitl: ClassVar[bool] = False

    def __init__(self, inner_config: Dict[str, Any] | None = None) -> None:
        super().__init__()
        # The declared runtime configuration, forwarded to the inner graph.
        # Constructing the inner graph bare would leave every value in
        # config/config.yaml unread while still looking configured.
        self._inner_config: Dict[str, Any] = dict(inner_config or {})

    def get_subgraph(self) -> Any:
        """Instantiate and return the inner domain workflow graph.

        Imported lazily to avoid circular-import risk at module load time and
        to match the Cat 2 nested pattern.
        """
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        return DomainWorkflowGraph(config=self._inner_config)

    def extract_input(self, state: AgentState) -> str:
        """Return the input passed into inner_graph.invoke() as a JSON string.

        The framework GraphNode.extract_input() contract requires a `str`.
        Two things cross the boundary and they cross differently:

          - the free-text query travels in the request string, because that is
            the only argument invoke() forwards;
          - the VALIDATED request context travels through context_bridge,
            because invoke() forwards no structured parameters at all.

        Only validated values are stashed. The raw request context never
        travels, so a field the contract does not declare cannot reach the
        inner pipeline by riding along here.
        """
        # Drop anything an earlier invocation on this thread may have left, so
        # a failure between stash and read cannot serve a stale request.
        clear_forecast_request()
        validated = from_json(state.get("validated_request"), {}) or {}
        set_forecast_request(validated)

        raw = state.get("raw_input")
        if raw is None:
            raw = state.get("user_input", "")
        return json.dumps({"raw_input": raw}, default=str)

    def merge_output(self, state: AgentState, sub_result: dict[str, Any]) -> dict[str, Any]:
        """Map the inner sub_result back into the outer state delta.

        Returns ONLY changed keys — never the full state.

        Key coupling (designed together with DomainWorkflowGraph.get_output()):
          Inner get_output()   emits: "result", "output_text", "status",
                                       "agent_status", "out_of_scope",
                                       "error_code", "error_message"
          This merge_output() reads: sub_result.get("output_text"),
                                     sub_result.get("status"), ...

        Nothing is re-gated here. The inner output gate is the single point
        that decides what may be released, and a second filter at this
        boundary would mask whether that gate works.
        """
        return {
            "result": sub_result.get("output_text"),
            "output_text": sub_result.get("output_text"),
            "status": sub_result.get("status"),
            "agent_status": sub_result.get("agent_status"),
            "out_of_scope": sub_result.get("out_of_scope", False),
            "error_code": sub_result.get("error_code"),
            "error_message": sub_result.get("error_message"),
        }


class DemandForecastAgent(AgentBaseGraph):
    """Outer graph for RET-C2-344 (Cat 2 ToolCalling, nested).

    Inherits AgentBaseGraph directly (L1 base). Domain logic is fully
    encapsulated in _DomainGraphNode (main slot), which delegates to
    DomainWorkflowGraph (inner BaseGraph).

    Backbone (fixed — identical to Cat 1):
        START → initialize → pre_process → main → post_process → finalize → END

    register_nodes() is the ONLY override:
      - super().register_nodes() fills: initialize, finalize (framework defaults)
      - pre_process:  the caller contract
      - main:         _DomainGraphNode (delegates to DomainWorkflowGraph)
      - post_process: release record on the success route

    add_edges() is NOT overridden — backbone wiring belongs to the framework.
    """

    @property
    def name(self) -> str:
        """Agent identifier registered with AgentRegistry."""
        return "DemandForecastAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        """Fill all 5 backbone slots.

        super().register_nodes() MUST be called first — it injects the
        framework's default InitializeNode and FinalizeNode.
        """
        super().register_nodes()  # fills: initialize, finalize

        self._nodes["pre_process"] = _PreProcessPassthroughNode()
        self._nodes["main"] = _DomainGraphNode(inner_config=self.config)
        self._nodes["post_process"] = _PostProcessPassthroughNode()

    # add_edges() is NOT overridden — backbone wiring belongs to the framework.


# Back-compat alias — callers may reference either name.
Graph = DemandForecastAgent

"""AgentCore Platform v1.0"""

# RET-C2-344 — DomainWorkflowGraph (inner BaseGraph)
#
# This is the INNER graph for the Cat 2 two-layer nested architecture.
# It encapsulates the full multi-store seasonal demand-forecast domain
# workflow:
#
#   START → input_parse → data_validate (S-1) → forecast_call
#         → forecast_agg → waste_risk → order_adjust
#         → output_format (DISCLAIMER) → output_validate (S-3) → END
#
# Called by _DomainGraphNode.get_subgraph() (graph.py).
# get_output() shapes the sub_result dict consumed by merge_output() there.
#
# Rules enforced (matches the verified-green reference BaseGraph shape):
#   ✅ Inherits BaseGraph (fully custom topology — no forced backbone)
#   ✅ Implements all 7 BaseGraph ABC methods
#      (name, state_schema, _validate_config, register_nodes, add_edges,
#       route, get_output)
#   ✅ register_nodes() does NOT call super() (abstract in BaseGraph)
#   ✅ Does NOT register initialize / finalize (outer backbone concerns)
#   ✅ get_output() designed together with _DomainGraphNode.merge_output()
#   ❌ No Level 0 platform SDK imports
#
# NOTE on the ABC: the coding-issue sketch used build_state()/build_nodes()/
# get_entry_point()/get_exit_point()/get_initial_state(). The released SDK
# v1.0.0rc1 BaseGraph (per the green reference implementation) instead requires
# name / state_schema / _validate_config / register_nodes / add_edges / route /
# get_output, wiring edges through self._sg with START/END. This file follows
# the real ABC so it compiles in CI; get_initial_state semantics are folded
# into the inner nodes (InputParse reads raw_input directly).

from typing import Any, Dict

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_status import AgentStatus
from src.graph.context_bridge import get_forecast_request
from src.nodes.input_parse_node import InputParseNode
from src.nodes.data_validate_node import DataValidateNode
from src.nodes.multi_store_forecast_node import MultiStoreForecastCallNode
from src.nodes.forecast_aggregation_node import ForecastAggregationNode
from src.nodes.food_waste_risk_node import FoodWasteRiskCalcNode
from src.nodes.order_adjust_node import OrderAdjustCalcNode
from src.nodes.output_format_node import OutputFormatNode
from src.nodes.output_validate_node import OutputValidateNode
from src.schemas.state import State, to_json
from framework.errors import ConfigError
from src.services.runtime_config import finite_in_range


class DomainWorkflowGraph(BaseGraph):
    """Inner domain workflow graph for RET-C2-344.

    Inherits BaseGraph directly for a fully custom node topology.
    Called by _DomainGraphNode.get_subgraph() in graph.py.

    Pipeline (linear, 8 nodes):
        START
          → input_parse      (InputParseNode)
          → data_validate    (DataValidateNode — S-1 gate)
          → forecast_call    (MultiStoreForecastCallNode — BigQuery ML stub)
          → forecast_agg     (ForecastAggregationNode)
          → waste_risk       (FoodWasteRiskCalcNode — 食品ロス法)
          → order_adjust     (OrderAdjustCalcNode)
          → output_format    (OutputFormatNode — DISCLAIMER mandatory)
          → output_validate  (OutputValidateNode — S-3 gate)
          → END

    All nodes are FunctionNode subclasses returning partial-dict state updates.
    initialize / finalize are outer backbone concerns — not registered here.
    """

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        """Unique identifier for this inner graph."""
        return "ret_c2_344_domain_workflow"

    @property
    def state_schema(self) -> type:
        """TypedDict subclass shared across the inner and outer graph."""
        return State

    # ── Config validation ─────────────────────────────────────────────────────

    def _validate_config(self) -> None:
        """Validate inner graph config before compilation.

        The runtime values arrive from the outer graph, which read them from
        config/config.yaml through src/services/runtime_config.py — they are
        already finite and in range by the time they get here. What this check
        adds is the case where the inner graph is constructed directly (a test,
        or a future caller) with a value that never went through that loader.
        """
        timeout_s = self.config.get("timeout_s")
        if timeout_s is not None and finite_in_range(timeout_s, 1, 600) is None:
            raise ConfigError(f"[{self.__class__.__name__}] 'timeout_s' must be a finite " f"number in [1, 600]")

    # ── Cross-boundary request hand-off ───────────────────────────────────────

    def _extra_initial_state(self) -> Dict[str, Any]:
        """Seed the validated caller request into the inner initial state.

        GraphNode calls `subgraph.invoke(user_input, session_id=..., ctx=...)`
        and forwards no structured parameters, so the outer layer stashes the
        validated request in a ContextVar just before that call and this hook
        reads it back. See src/graph/context_bridge.py.
        """
        return {"forecast_request": to_json(get_forecast_request())}

    # ── Node registration ─────────────────────────────────────────────────────

    def register_nodes(self) -> None:
        """Register all 8 domain nodes.

        No super() call — BaseGraph.register_nodes() is abstract.
        Do NOT register initialize or finalize; those are outer backbone
        concerns handled by AgentBaseGraph in graph.py.
        Every key registered here is referenced in add_edges().

        Production wiring of the real BigQuery ML AI.FORECAST client:
            self._nodes["forecast_call"] = MultiStoreForecastCallNode(
                forecast_client=<your_bigquery_ml_client>
            )
        """
        self._nodes["input_parse"] = InputParseNode()
        self._nodes["data_validate"] = DataValidateNode()
        # timeout_s is the wall-clock budget for the per-store fan-out — the
        # one call in this pipeline a production client turns into network I/O.
        # Passing it here is what makes the declared value reach the code.
        self._nodes["forecast_call"] = MultiStoreForecastCallNode(
            node_config={"timeout_s": self.config.get("timeout_s")}
        )
        self._nodes["forecast_agg"] = ForecastAggregationNode()
        self._nodes["waste_risk"] = FoodWasteRiskCalcNode()
        self._nodes["order_adjust"] = OrderAdjustCalcNode()
        self._nodes["output_format"] = OutputFormatNode()
        self._nodes["output_validate"] = OutputValidateNode()

    # ── Edge wiring ───────────────────────────────────────────────────────────

    def add_edges(self) -> None:
        """Wire the linear 8-node demand-forecast domain topology.

        Each step passes its partial-dict output into the shared State.
        The topology is intentionally linear — no conditional branching between
        domain nodes (each node self-guards on validation_passed / out_of_scope
        / empty inputs and returns a graceful SUCCESS passthrough). route() is
        implemented as required by the ABC but add_conditional_edges() is not
        used.

        Flow:
          input_parse → data_validate → forecast_call → forecast_agg
                      → waste_risk → order_adjust → output_format → output_validate
        """
        self._sg.add_edge(START, "input_parse")
        self._sg.add_edge("input_parse", "data_validate")
        self._sg.add_edge("data_validate", "forecast_call")
        self._sg.add_edge("forecast_call", "forecast_agg")
        self._sg.add_edge("forecast_agg", "waste_risk")
        self._sg.add_edge("waste_risk", "order_adjust")
        self._sg.add_edge("order_adjust", "output_format")
        self._sg.add_edge("output_format", "output_validate")
        self._sg.add_edge("output_validate", END)

    # ── Routing ───────────────────────────────────────────────────────────────

    def route(self, state: State) -> str:
        """Conditional routing — required by BaseGraph ABC.

        For this linear topology add_conditional_edges() is not used, so this
        method is never called at runtime. It is implemented to satisfy the ABC
        contract. Returns END on error so an unexpected call does not re-enter a
        processing node.

        Annotated with this graph's OWN State rather than the framework base:
        the graph library reads a path callable's annotation as its input
        schema and projects away every field the annotation does not declare,
        so a base-class annotation would make the domain fields it reads
        permanently absent the moment this callable is ever wired up.
        """
        if state.get("status") == AgentStatus.ERROR.value:
            return END
        return "output_validate"

    # ── Output shape ──────────────────────────────────────────────────────────

    def get_output(self, state: State) -> dict[str, Any]:
        """Shape the output dict returned to the outer graph as sub_result.

        Received by _DomainGraphNode.merge_output() in graph.py as the
        `sub_result` argument. Both methods are designed together to guarantee
        field-name consistency:

            Inner get_output()   emits: "output_text", "result", "status",
                                        "agent_status", "out_of_scope",
                                        "error_code", "error_message",
                                        "node_history"
            Outer merge_output() reads: sub_result.get("output_text"),
                                        sub_result.get("status"), ...

        `output_text` is emitted as-is. It is whatever the output gate decided
        may be released — on a violation the gate has already replaced the
        answer with a constant notice, so there is nothing to re-filter here.
        Filtering again at this boundary would contain the gate's failures on
        its own and leave the gate itself untestable.
        """
        agent_status = state.get("agent_status")
        # Normalise agent_status to its string value for the outer status mirror.
        status_value = state.get("status")
        if status_value is None and agent_status is not None:
            status_value = getattr(agent_status, "value", agent_status)

        return {
            "output_text": state.get("output_text"),
            "result": state.get("output_text"),
            "status": status_value,
            "agent_status": agent_status,
            "out_of_scope": state.get("out_of_scope", False),
            "error_code": state.get("error_code"),
            "error_message": state.get("error_message"),
            "node_history": state.get("node_history", []),
        }

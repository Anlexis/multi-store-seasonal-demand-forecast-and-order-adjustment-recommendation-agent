# RET-C2-344 — Unit Tests: outer graph (DemandForecastAgent) +
#                          inner graph (DomainWorkflowGraph) composition
#
# Asserted against the MERGED develop implementation:
#   - DemandForecastAgent (outer AgentBaseGraph) registers _DomainGraphNode in
#     the "main" slot and does NOT override add_edges().
#   - DomainWorkflowGraph (inner BaseGraph) registers all 8 domain nodes in
#     pipeline order: input_parse -> data_validate -> forecast_call ->
#     forecast_agg -> waste_risk -> order_adjust -> output_format -> output_validate.
#   - _DomainGraphNode.merge_output() maps sub_result -> outer state delta
#     (changed keys only; echoes neither user_input nor session_id).
#   - Graph alias == DemandForecastAgent.
#
# Framework-dependent instantiation (AgentBaseGraph/BaseGraph compile) may not
# import under a bare local python, so those constructions are wrapped in
# ImportError skips (mirrors the reference template). Pure dict-level merge_output checks run
# unconditionally once the class imports.

import pytest


class TestInnerDomainWorkflowGraph:
    """Inner BaseGraph: identity, 8-node registration, output shaping."""

    def test_inner_graph_instantiates(self):
        """DomainWorkflowGraph() must not raise NotImplementedError (ABCs filled)."""
        try:
            from src.graph.domain_workflow_graph import DomainWorkflowGraph

            graph = DomainWorkflowGraph()
            assert graph is not None
        except ImportError as exc:
            pytest.skip(f"Framework not installed in CI: {exc}")
        except NotImplementedError as exc:  # pragma: no cover
            pytest.fail(f"DomainWorkflowGraph has unimplemented ABC methods: {exc}")

    def test_inner_graph_identity(self):
        """Inner graph name + state_schema are the shared workflow id and State."""
        try:
            from src.graph.domain_workflow_graph import DomainWorkflowGraph
            from src.schemas.state import State
        except ImportError as exc:
            pytest.skip(f"Framework not installed in CI: {exc}")

        graph = DomainWorkflowGraph()
        assert graph.name == "ret_c2_344_domain_workflow"
        assert graph.state_schema is State

    def test_register_nodes_all_eight_in_pipeline_order(self):
        """All 8 domain nodes register in the linear pipeline order."""
        try:
            from src.graph.domain_workflow_graph import DomainWorkflowGraph
            from src.nodes.input_parse_node import InputParseNode
            from src.nodes.data_validate_node import DataValidateNode
            from src.nodes.multi_store_forecast_node import MultiStoreForecastCallNode
            from src.nodes.forecast_aggregation_node import ForecastAggregationNode
            from src.nodes.food_waste_risk_node import FoodWasteRiskCalcNode
            from src.nodes.order_adjust_node import OrderAdjustCalcNode
            from src.nodes.output_format_node import OutputFormatNode
            from src.nodes.output_validate_node import OutputValidateNode
        except ImportError as exc:
            pytest.skip(f"Framework not installed in CI: {exc}")

        graph = DomainWorkflowGraph()
        # _nodes is populated by register_nodes(); call it directly to avoid
        # depending on a full compile().
        graph._nodes = {}
        graph.register_nodes()

        keys = list(graph._nodes.keys())
        assert keys == [
            "input_parse",
            "data_validate",
            "forecast_call",
            "forecast_agg",
            "waste_risk",
            "order_adjust",
            "output_format",
            "output_validate",
        ], f"Domain nodes must register in pipeline order, got {keys}"
        assert isinstance(graph._nodes["input_parse"], InputParseNode)
        assert isinstance(graph._nodes["data_validate"], DataValidateNode)
        assert isinstance(graph._nodes["forecast_call"], MultiStoreForecastCallNode)
        assert isinstance(graph._nodes["forecast_agg"], ForecastAggregationNode)
        assert isinstance(graph._nodes["waste_risk"], FoodWasteRiskCalcNode)
        assert isinstance(graph._nodes["order_adjust"], OrderAdjustCalcNode)
        assert isinstance(graph._nodes["output_format"], OutputFormatNode)
        assert isinstance(graph._nodes["output_validate"], OutputValidateNode)

    def test_get_output_surfaces_output_text(self):
        """get_output() emits output_text + status + out_of_scope + node_history."""
        try:
            from src.graph.domain_workflow_graph import DomainWorkflowGraph
        except ImportError as exc:
            pytest.skip(f"Framework not installed in CI: {exc}")

        graph = DomainWorkflowGraph()
        state = {
            "output_text": "report\n--- DISCLAIMER ---\n",
            "status": "success",
            "out_of_scope": False,
            "node_history": ["input_parse"],
        }
        out = graph.get_output(state)
        assert out["output_text"] == "report\n--- DISCLAIMER ---\n"
        assert out["result"] == out["output_text"]
        assert out["status"] == "success"
        assert out["out_of_scope"] is False
        assert out["node_history"] == ["input_parse"]


class TestOuterGraphComposition:
    """Outer DemandForecastAgent + _DomainGraphNode mapping methods."""

    def test_outer_agent_registers_main_graph_node(self):
        """register_nodes() puts _DomainGraphNode in the 'main' slot."""
        try:
            from src.graph.graph import DemandForecastAgent, _DomainGraphNode
        except ImportError as exc:
            pytest.skip(f"Framework not installed in CI: {exc}")

        agent = DemandForecastAgent()
        agent._nodes = {}
        agent.register_nodes()

        assert "main" in agent._nodes
        assert isinstance(agent._nodes["main"], _DomainGraphNode)
        # The pre_process / post_process passthrough slots are also filled.
        assert "pre_process" in agent._nodes
        assert "post_process" in agent._nodes

    def test_outer_graph_does_not_override_add_edges(self):
        """The outer backbone wiring belongs to the framework — add_edges not overridden."""
        try:
            from src.graph.graph import DemandForecastAgent
        except ImportError as exc:
            pytest.skip(f"Framework not installed in CI: {exc}")

        assert "add_edges" not in DemandForecastAgent.__dict__

    def test_outer_agent_identity(self):
        """DemandForecastAgent name + state_schema + Graph alias are correct."""
        try:
            from src.graph.graph import DemandForecastAgent, Graph
            from src.schemas.state import State
        except ImportError as exc:
            pytest.skip(f"Framework not installed in CI: {exc}")

        agent = DemandForecastAgent()
        assert agent.name == "DemandForecastAgent"
        assert agent.state_schema is State
        assert Graph is DemandForecastAgent

    def test_merge_output_maps_output_text_to_result(self):
        """merge_output maps sub_result['output_text'] -> 'result' + 'output_text'."""
        try:
            from src.graph.graph import _DomainGraphNode
        except ImportError as exc:
            pytest.skip(f"Framework not installed in CI: {exc}")

        node = _DomainGraphNode()
        sub_result = {
            "output_text": "T",
            "status": "success",
            "agent_status": "anything",
            "out_of_scope": True,
            "error_message": None,
        }
        merged = node.merge_output({}, sub_result)
        assert merged["result"] == "T"
        assert merged["output_text"] == "T"
        assert merged["status"] == "success"
        assert merged["out_of_scope"] is True

    def test_merge_output_returns_only_changed_keys(self):
        """merge_output must NOT echo the outer state back."""
        try:
            from src.graph.graph import _DomainGraphNode
        except ImportError as exc:
            pytest.skip(f"Framework not installed in CI: {exc}")

        node = _DomainGraphNode()
        outer_state = {"user_input": "raw", "session_id": "S-1"}
        sub_result = {"output_text": "x", "status": "success"}

        merged = node.merge_output(outer_state, sub_result)
        assert "user_input" not in merged
        assert "session_id" not in merged
        assert set(merged.keys()) == {
            "result",
            "output_text",
            "status",
            "agent_status",
            "out_of_scope",
            "error_code",
            "error_message",
        }

    def test_merge_output_defaults_out_of_scope_false(self):
        """merge_output defaults out_of_scope to False when absent in sub_result."""
        try:
            from src.graph.graph import _DomainGraphNode
        except ImportError as exc:
            pytest.skip(f"Framework not installed in CI: {exc}")

        node = _DomainGraphNode()
        merged = node.merge_output({}, {"output_text": "x", "status": "success"})
        assert merged["out_of_scope"] is False

    def test_get_subgraph_returns_inner_graph(self):
        """get_subgraph() returns a DomainWorkflowGraph instance."""
        try:
            from src.graph.graph import _DomainGraphNode
            from src.graph.domain_workflow_graph import DomainWorkflowGraph
        except ImportError as exc:
            pytest.skip(f"Framework not installed in CI: {exc}")

        node = _DomainGraphNode()
        assert isinstance(node.get_subgraph(), DomainWorkflowGraph)

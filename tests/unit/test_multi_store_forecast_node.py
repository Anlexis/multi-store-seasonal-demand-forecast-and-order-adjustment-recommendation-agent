# RET-C2-344 — Unit Tests: MultiStoreForecastCallNode
#
# Inner DomainWorkflowGraph node 3: deterministic synthetic forecast (one entry
# per store in the group). Asserted against the MERGED develop logic:
#   - Guard: validation_passed False OR out_of_scope True -> raw_forecast_results=[]
#     + SUCCESS (no forecast).
#   - kanto_12 -> 12 stores; each result carries store_id / sku_category /
#     horizon_days / forecast_units / confidence_low / confidence_high / model.
#   - Stub ramp: forecast_units[d] = round(base * (1 + 0.02*d)); base(yogurt)=120
#     -> day0=120, day6=134 for horizon 7.
#   - forecast_client is constructor-injectable (production BigQuery ML hook).
#   - agent_status is the AgentStatus enum.
#
# ADR-005: raw_forecast_results is written as a JSON string (to_json); these
# tests deserialize the node output with from_json() before asserting on it.
#
# S-4 audit muted at the node module via an autouse fixture (NEVER stub shared.*).

import pytest

from src.nodes.multi_store_forecast_node import (
    MultiStoreForecastCallNode,
    STORE_GROUP_MEMBERS,
)
from src.schemas.state import from_json
from framework.schemas.agent_status import AgentStatus


@pytest.fixture(autouse=True)
def _mute_audit(monkeypatch):
    """Silence the S-4 audit free function at the node module under test."""
    monkeypatch.setattr(
        "src.nodes.multi_store_forecast_node.emit_trace_event",
        lambda *a, **k: None,
    )


def _valid_state(**overrides):
    base = {
        "store_group": "kanto_12",
        "sku_category": "yogurt",
        "horizon_days": 7,
        "validation_passed": True,
        "out_of_scope": False,
    }
    base.update(overrides)
    return base


class TestMultiStoreForecastCallNode:
    """Unit tests for MultiStoreForecastCallNode (synthetic forecast stub)."""

    def setup_method(self):
        self.node = MultiStoreForecastCallNode()

    def test_happy_path_one_result_per_store(self):
        """kanto_12 -> 12 store forecasts, SUCCESS."""
        result = self.node.execute(_valid_state())

        assert result["agent_status"] is AgentStatus.SUCCESS
        results = from_json(result["raw_forecast_results"], [])
        assert len(results) == len(STORE_GROUP_MEMBERS["kanto_12"]) == 12

    def test_result_shape_and_deterministic_ramp(self):
        """Each result has the contract keys; the yogurt ramp is deterministic."""
        result = self.node.execute(_valid_state())
        first = from_json(result["raw_forecast_results"], [])[0]

        for key in (
            "store_id",
            "sku_category",
            "horizon_days",
            "forecast_units",
            "confidence_low",
            "confidence_high",
            "model",
        ):
            assert key in first, f"forecast result missing key '{key}'"

        # base(yogurt)=120, +2%/day, rounded.
        assert first["forecast_units"] == [120, 122, 125, 127, 130, 132, 134]
        assert len(first["forecast_units"]) == 7
        assert first["sku_category"] == "yogurt"

    def test_guard_skips_when_validation_failed(self):
        """validation_passed=False -> empty results, SUCCESS (no forecast)."""
        result = self.node.execute(_valid_state(validation_passed=False))

        assert from_json(result["raw_forecast_results"], []) == []
        assert result["agent_status"] is AgentStatus.SUCCESS

    def test_guard_skips_when_out_of_scope(self):
        """out_of_scope=True -> empty results, SUCCESS (no forecast)."""
        result = self.node.execute(_valid_state(out_of_scope=True))

        assert from_json(result["raw_forecast_results"], []) == []
        assert result["agent_status"] is AgentStatus.SUCCESS

    def test_injected_client_is_used(self):
        """A constructor-injected forecast_client overrides the stub."""
        sentinel = {
            "store_id": "X",
            "sku_category": "yogurt",
            "horizon_days": 7,
            "forecast_units": [1, 2, 3, 4, 5, 6, 7],
            "confidence_low": [0] * 7,
            "confidence_high": [9] * 7,
            "model": "injected-test-client",
        }
        node = MultiStoreForecastCallNode(forecast_client=lambda **kw: dict(sentinel))
        result = node.execute(_valid_state())

        assert result["agent_status"] is AgentStatus.SUCCESS
        results = from_json(result["raw_forecast_results"], [])
        assert all(r["model"] == "injected-test-client" for r in results)

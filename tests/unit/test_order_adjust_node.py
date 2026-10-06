# RET-C2-344 — Unit Tests: OrderAdjustCalcNode
#
# Inner DomainWorkflowGraph node 6: per-store ±% order-adjustment rules.
# Asserted against the MERGED develop logic:
#   - Empty raw_forecast_results OR aggregated_forecast -> empty list + SUCCESS.
#   - Base adjustment by risk level: HIGH=-10, MEDIUM=-5, LOW=0.
#   - region_avg = aggregated_forecast.avg_forecast_by_day[peak_day_index].
#   - store_peak = store.forecast_units[peak_day_index].
#   - ratio store_peak/region_avg > 1.20 -> additional -5% (overstock_guard);
#                                    < 0.80 -> additional +5% (understock_guard).
#   - recommended_order = round(store_peak * (1 + adj_pct/100)).
#   - agent_status is the AgentStatus enum.
#
# ADR-005: aggregated_forecast / raw_forecast_results / food_waste_risk_flags
# (inputs) are passed as JSON strings (to_json); order_adjustments (output) is
# read back with from_json() before asserting.
#
# S-4 audit muted at the node module via an autouse fixture (NEVER stub shared.*).

import pytest

from src.nodes.order_adjust_node import OrderAdjustCalcNode
from src.schemas.state import from_json, to_json
from framework.schemas.agent_status import AgentStatus


@pytest.fixture(autouse=True)
def _mute_audit(monkeypatch):
    """Silence the S-4 audit free function at the node module under test."""
    monkeypatch.setattr(
        "src.nodes.order_adjust_node.emit_trace_event",
        lambda *a, **k: None,
    )


def _state(stores_units, risk="LOW", peak_idx=0, region_avg=100.0):
    """Build a state with explicit region average + per-store forecast units.

    Structured fields are serialized to JSON strings (ADR-005) to match the
    on-state contract the node now reads via from_json().
    """
    avg_by_day = [0.0] * (peak_idx + 1)
    avg_by_day[peak_idx] = region_avg
    return {
        "aggregated_forecast": to_json(
            {
                "peak_day_index": peak_idx,
                "avg_forecast_by_day": avg_by_day,
            }
        ),
        "raw_forecast_results": to_json(
            [{"store_id": f"s{i}", "forecast_units": u} for i, u in enumerate(stores_units)]
        ),
        "food_waste_risk_flags": to_json([{"risk_level": risk}] if risk else []),
        "sku_category": "yogurt",
    }


class TestOrderAdjustCalcNode:
    """Unit tests for OrderAdjustCalcNode."""

    def setup_method(self):
        self.node = OrderAdjustCalcNode()

    def test_empty_inputs_passthrough(self):
        """No forecasts -> empty adjustments + SUCCESS."""
        result = self.node.execute({"raw_forecast_results": to_json([]), "aggregated_forecast": to_json({})})

        assert from_json(result["order_adjustments"], []) == []
        assert result["agent_status"] is AgentStatus.SUCCESS

    def test_low_risk_normal_store_zero_adjustment(self):
        """LOW risk + store at region average -> 0% adjustment, recommended == peak."""
        result = self.node.execute(_state([[100]], risk="LOW", region_avg=100.0))
        adj = from_json(result["order_adjustments"], [])[0]

        assert adj["adjustment_pct"] == 0.0
        assert adj["current_order_estimate"] == 100
        assert adj["recommended_order"] == 100
        assert adj["adjustment_reason"] == "food_waste_risk:LOW"
        assert result["agent_status"] is AgentStatus.SUCCESS

    def test_medium_risk_applies_minus5(self):
        """MEDIUM risk -> base -5%; store at avg -> recommended round(100*0.95)=95."""
        result = self.node.execute(_state([[100]], risk="MEDIUM", region_avg=100.0))
        adj = from_json(result["order_adjustments"], [])[0]

        assert adj["adjustment_pct"] == -5.0
        assert adj["recommended_order"] == 95
        assert "food_waste_risk:MEDIUM" in adj["adjustment_reason"]

    def test_overstock_guard_adds_minus5(self):
        """store_peak 150 vs region avg 100 (ratio 1.5>1.2) -> extra -5% (LOW base)."""
        result = self.node.execute(_state([[150]], risk="LOW", region_avg=100.0))
        adj = from_json(result["order_adjustments"], [])[0]

        assert adj["adjustment_pct"] == -5.0  # 0 + (-5) overstock
        assert adj["recommended_order"] == 142  # round(150*0.95)
        assert "overstock_guard:-5%" in adj["adjustment_reason"]

    def test_understock_guard_adds_plus5(self):
        """store_peak 50 vs region avg 100 (ratio 0.5<0.8) -> extra +5% (LOW base)."""
        result = self.node.execute(_state([[50]], risk="LOW", region_avg=100.0))
        adj = from_json(result["order_adjustments"], [])[0]

        assert adj["adjustment_pct"] == 5.0  # 0 + 5 understock
        assert adj["recommended_order"] == 52  # round(50*1.05)
        assert "understock_guard:+5%" in adj["adjustment_reason"]

    def test_high_risk_plus_overstock_stacks(self):
        """HIGH base -10% + overstock -5% = -15%; round(150*0.85)=128."""
        result = self.node.execute(_state([[150]], risk="HIGH", region_avg=100.0))
        adj = from_json(result["order_adjustments"], [])[0]

        assert adj["adjustment_pct"] == -15.0
        assert adj["recommended_order"] == 128
        assert "food_waste_risk:HIGH" in adj["adjustment_reason"]
        assert "overstock_guard:-5%" in adj["adjustment_reason"]

    def test_one_adjustment_per_store(self):
        """One adjustment entry is produced per store in raw_forecast_results."""
        result = self.node.execute(_state([[100], [100], [100]], risk="LOW", region_avg=100.0))
        adjustments = from_json(result["order_adjustments"], [])

        assert len(adjustments) == 3
        assert {a["store_id"] for a in adjustments} == {"s0", "s1", "s2"}

# RET-C2-344 — Unit Tests: FoodWasteRiskCalcNode (食品ロス削減推進法 thresholds)
#
# Inner DomainWorkflowGraph node 5. Asserted against the MERGED develop logic:
#   - Empty/absent aggregated_forecast OR sku -> empty flags list + SUCCESS.
#   - est_waste = round(peak_demand_total * waste_risk_pct[sku]).
#   - risk_level: HIGH if est_waste > peak*0.15, MEDIUM if > peak*0.08, else LOW.
#       NOTE: with the real thresholds (max waste_risk_pct=0.12 < 0.15), HIGH is
#       unreachable for any known SKU; the highest realistic level is MEDIUM.
#       The HIGH branch is exercised directly against the _risk_level helper.
#   - Each flag carries the 食品ロス削減推進法 law_reference string and shelf-day window.
#   - agent_status is the AgentStatus enum.
#
# ADR-005: aggregated_forecast (input) is passed as a JSON string (to_json);
# food_waste_risk_flags (output) is read back with from_json() before asserting.
#
# S-4 audit muted at the node module via an autouse fixture (NEVER stub shared.*).

import pytest

from src.nodes.food_waste_risk_node import (
    FoodWasteRiskCalcNode,
    _risk_level,
    FOOD_WASTE_THRESHOLDS,
    _LAW_REFERENCE,
)
from src.schemas.state import from_json, to_json
from framework.schemas.agent_status import AgentStatus


@pytest.fixture(autouse=True)
def _mute_audit(monkeypatch):
    """Silence the S-4 audit free function at the node module under test."""
    monkeypatch.setattr(
        "src.nodes.food_waste_risk_node.emit_trace_event",
        lambda *a, **k: None,
    )


def _state(sku="yogurt", peak=1000):
    return {
        "aggregated_forecast": to_json({"peak_demand_total": peak}),
        "sku_category": sku,
    }


class TestFoodWasteRiskCalcNode:
    """Unit tests for FoodWasteRiskCalcNode."""

    def setup_method(self):
        self.node = FoodWasteRiskCalcNode()

    def test_empty_aggregate_passthrough(self):
        """Absent aggregated_forecast -> empty flags + SUCCESS."""
        result = self.node.execute({"aggregated_forecast": to_json({}), "sku_category": "yogurt"})

        assert from_json(result["food_waste_risk_flags"], []) == []
        assert result["agent_status"] is AgentStatus.SUCCESS

    def test_missing_sku_passthrough(self):
        """Absent sku_category -> empty flags + SUCCESS."""
        result = self.node.execute({"aggregated_forecast": to_json({"peak_demand_total": 1000}), "sku_category": ""})

        assert from_json(result["food_waste_risk_flags"], []) == []
        assert result["agent_status"] is AgentStatus.SUCCESS

    def test_yogurt_peak1000_is_medium(self):
        """yogurt @ peak 1000: est_waste=round(1000*0.12)=120 > 0.08*1000=80 -> MEDIUM."""
        result = self.node.execute(_state(sku="yogurt", peak=1000))
        flag = from_json(result["food_waste_risk_flags"], [])[0]

        assert flag["estimated_waste_units"] == 120
        assert flag["risk_level"] == "MEDIUM"
        assert flag["waste_risk_threshold_pct"] == 0.12
        assert flag["risk_window_days"] == FOOD_WASTE_THRESHOLDS["yogurt"]["shelf_days"]
        assert flag["peak_demand_total"] == 1000
        assert flag["sku_category"] == "yogurt"
        assert flag["law_reference"] == _LAW_REFERENCE
        assert result["agent_status"] is AgentStatus.SUCCESS

    def test_bento_peak1000_is_low(self):
        """bento @ peak 1000: est_waste=round(1000*0.05)=50, 50 <= 0.08*1000=80 -> LOW."""
        result = self.node.execute(_state(sku="bento", peak=1000))
        flag = from_json(result["food_waste_risk_flags"], [])[0]

        assert flag["estimated_waste_units"] == 50
        assert flag["risk_level"] == "LOW"

    def test_law_reference_is_food_loss_act(self):
        """Every flag surfaces the 食品ロス削減推進法 FY2026 mandatory-reporting reference."""
        result = self.node.execute(_state(sku="yogurt", peak=1608))
        flag = from_json(result["food_waste_risk_flags"], [])[0]

        assert "食品ロス削減推進法" in flag["law_reference"]
        # peak 1608 (the kanto_12 golden peak): round(1608*0.12)=193 -> still MEDIUM.
        assert flag["estimated_waste_units"] == 193
        assert flag["risk_level"] == "MEDIUM"

    def test_risk_level_helper_high_branch(self):
        """_risk_level HIGH branch: est_waste strictly above 15% of peak -> HIGH."""
        # peak=100: HIGH when est_waste > 15, MEDIUM when > 8, else LOW.
        assert _risk_level(16, 100) == "HIGH"
        assert _risk_level(15, 100) == "MEDIUM"  # not strictly > 15
        assert _risk_level(9, 100) == "MEDIUM"
        assert _risk_level(8, 100) == "LOW"  # not strictly > 8
        assert _risk_level(0, 100) == "LOW"

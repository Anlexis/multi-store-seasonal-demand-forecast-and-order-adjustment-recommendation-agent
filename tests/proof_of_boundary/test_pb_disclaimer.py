# RET-C2-344 — Proof-of-Boundary: DISCLAIMER in all output paths
#
# PoB: the mandatory DISCLAIMER block ("--- DISCLAIMER ---") MUST appear in EVERY
# OutputFormatNode output path — happy, out-of-scope, and validation-error — and
# disclaimer_present must be True in each.
#
# Asserted against the MERGED develop OutputFormatNode (DISCLAIMER appended in all
# branches; disclaimer_present derived from DISCLAIMER_START in output_text).
#
# S-4 audit muted at the node module via an autouse fixture (NEVER stub shared.*).

import pytest

from src.nodes.output_format_node import OutputFormatNode
from framework.schemas.agent_status import AgentStatus

DISCLAIMER_MARKER = "--- DISCLAIMER ---"


@pytest.fixture(autouse=True)
def _mute_audit(monkeypatch):
    """Silence the S-4 audit free function at the node module under test."""
    monkeypatch.setattr(
        "src.nodes.output_format_node.emit_trace_event",
        lambda *a, **k: None,
    )


def _make_state(**kwargs):
    base = {
        "aggregated_forecast": {
            "store_group": "kanto_12",
            "sku_category": "yogurt",
            "horizon_days": 7,
            "store_count": 12,
            "total_forecast_by_day": [1200] * 7,
            "avg_forecast_by_day": [100.0] * 7,
            "peak_day_index": 0,
            "peak_demand_total": 1200,
            "model": "stub",
        },
        "food_waste_risk_flags": [
            {
                "sku_category": "yogurt",
                "risk_level": "LOW",
                "estimated_waste_units": 10,
                "law_reference": "食品ロス削減推進法 FY2026 mandatory reporting",
                "risk_window_days": 14,
                "peak_demand_total": 1200,
                "waste_risk_threshold_pct": 0.12,
            }
        ],
        "order_adjustments": [],
        "store_group": "kanto_12",
        "sku_category": "yogurt",
        "horizon_days": 7,
        "out_of_scope": False,
        "validation_error": None,
    }
    base.update(kwargs)
    return base


class TestPoBDisclaimer:
    """PoB: the DISCLAIMER block is unconditional across every output path."""

    def setup_method(self):
        self.node = OutputFormatNode()

    def test_disclaimer_in_happy_path(self):
        """Happy path must carry the DISCLAIMER block."""
        result = self.node.execute(_make_state())

        assert DISCLAIMER_MARKER in result["output_text"], "Happy path must have DISCLAIMER"
        assert result["disclaimer_present"] is True
        assert result["agent_status"] is AgentStatus.SUCCESS

    def test_disclaimer_in_out_of_scope(self):
        """Out-of-scope path must carry the DISCLAIMER block."""
        result = self.node.execute(_make_state(out_of_scope=True, store_group="unknown_store"))

        assert DISCLAIMER_MARKER in result["output_text"], "Out-of-scope path must have DISCLAIMER"
        assert result["disclaimer_present"] is True

    def test_disclaimer_in_validation_error(self):
        """Validation-error path must carry the DISCLAIMER block."""
        result = self.node.execute(_make_state(validation_error="horizon_days must be 1-90"))

        assert DISCLAIMER_MARKER in result["output_text"], "Validation error path must have DISCLAIMER"
        assert result["disclaimer_present"] is True

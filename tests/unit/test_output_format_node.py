# RET-C2-344 — Unit Tests: OutputFormatNode
#
# Inner DomainWorkflowGraph node 7: render output text. Asserted against the
# MERGED develop logic:
#   - The mandatory DISCLAIMER block ("--- DISCLAIMER ---") appears in EVERY
#     output path (happy / out-of-scope / validation-error) and disclaimer_present
#     is set from its presence.
#   - Happy path renders the regional summary, food-waste risk, and per-store
#     order adjustments.
#   - out_of_scope=True renders the unsupported-combination message.
#   - validation_error renders the validation-failure message.
#   - agent_status is the AgentStatus enum.
#
# ADR-005: aggregated_forecast / food_waste_risk_flags / order_adjustments
# (inputs) are passed as JSON strings (to_json) to match the on-state contract
# the node now reads via from_json().
#
# S-4 audit muted at the node module via an autouse fixture (NEVER stub shared.*).

import pytest

from src.nodes.output_format_node import OutputFormatNode, DISCLAIMER_START
from src.schemas.state import to_json
from framework.schemas.agent_status import AgentStatus


@pytest.fixture(autouse=True)
def _mute_audit(monkeypatch):
    """Silence the S-4 audit free function at the node module under test."""
    monkeypatch.setattr(
        "src.nodes.output_format_node.emit_trace_event",
        lambda *a, **k: None,
    )


def _happy_state(**overrides):
    base = {
        "aggregated_forecast": to_json(
            {
                "store_group": "kanto_12",
                "sku_category": "yogurt",
                "horizon_days": 7,
                "store_count": 12,
                "total_forecast_by_day": [1440, 1464, 1500, 1524, 1560, 1584, 1608],
                "avg_forecast_by_day": [120.0, 122.0, 125.0, 127.0, 130.0, 132.0, 134.0],
                "peak_day_index": 6,
                "peak_demand_total": 1608,
                "model": "TimesFM-2.5-stub-v1",
            }
        ),
        "food_waste_risk_flags": to_json(
            [
                {
                    "sku_category": "yogurt",
                    "risk_level": "MEDIUM",
                    "estimated_waste_units": 193,
                    "law_reference": "食品ロス削減推進法 FY2026 mandatory reporting",
                }
            ]
        ),
        "order_adjustments": to_json(
            [
                {
                    "store_id": "kanto_store_01",
                    "current_order_estimate": 134,
                    "recommended_order": 127,
                    "adjustment_pct": -5.0,
                    "adjustment_reason": "food_waste_risk:MEDIUM",
                }
            ]
        ),
        "store_group": "kanto_12",
        "sku_category": "yogurt",
        "out_of_scope": False,
        "validation_error": None,
    }
    base.update(overrides)
    return base


class TestOutputFormatNode:
    """Unit tests for OutputFormatNode (rendering + mandatory DISCLAIMER)."""

    def setup_method(self):
        self.node = OutputFormatNode()

    def test_happy_path_has_disclaimer_and_report(self):
        """Happy path: DISCLAIMER present + key report sections rendered."""
        result = self.node.execute(_happy_state())

        assert result["agent_status"] is AgentStatus.SUCCESS
        assert result["disclaimer_present"] is True
        text = result["output_text"]
        assert DISCLAIMER_START in text
        assert "Demand Forecast Report" in text
        assert "kanto_12" in text
        assert "Order Adjustment Recommendations" in text
        assert "kanto_store_01" in text

    def test_out_of_scope_path_has_disclaimer(self):
        """Out-of-scope: DISCLAIMER present + the supported-combination hint.

        The rejected store group and SKU are NOT echoed. They are caller data
        and this text is caller-facing output, so the refusal names what IS
        supported instead of repeating what was sent.
        """
        result = self.node.execute(
            _happy_state(
                out_of_scope=True,
                store_group="unknown_store",
                sku_category="mystery",
                validation_error="unsupported_store_group",
            )
        )

        assert result["disclaimer_present"] is True
        assert DISCLAIMER_START in result["output_text"]
        assert "could not be answered" in result["output_text"].lower()
        assert "unknown_store" not in result["output_text"]
        assert "mystery" not in result["output_text"]

    def test_validation_error_path_has_disclaimer(self):
        """Validation-error: DISCLAIMER present + the closed-set label as prose.

        The label travels as a value; only this node turns it into a sentence,
        so a label that is not mapped renders the generic line rather than
        printing its own text into the answer.
        """
        result = self.node.execute(_happy_state(validation_error="horizon_days_out_of_supported_range"))

        assert result["disclaimer_present"] is True
        assert DISCLAIMER_START in result["output_text"]
        assert "between 1 and 90 days" in result["output_text"]
        # The label itself is never printed — it is an internal value.
        assert "horizon_days_out_of_supported_range" not in result["output_text"]

    def test_unmapped_validation_label_renders_the_generic_line(self):
        """A label with no prose mapping must not print itself into the answer."""
        result = self.node.execute(_happy_state(validation_error="some_future_label_nobody_mapped"))

        assert "some_future_label_nobody_mapped" not in result["output_text"]
        assert "did not satisfy the input contract" in result["output_text"]

    def test_out_of_scope_takes_priority_over_validation_error(self):
        """Out-of-scope and validation-failure share one refusal rendering."""
        result = self.node.execute(_happy_state(out_of_scope=True, validation_error="unsupported_sku_category"))

        assert "could not be answered" in result["output_text"].lower()
        assert "SKU category is not supported" in result["output_text"]
        assert result["disclaimer_present"] is True

    def test_disclaimer_present_flag_matches_marker(self):
        """disclaimer_present is True exactly when the marker is in output_text."""
        result = self.node.execute(_happy_state())
        assert (DISCLAIMER_START in result["output_text"]) == result["disclaimer_present"]

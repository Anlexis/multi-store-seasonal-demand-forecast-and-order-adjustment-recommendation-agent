# RET-C2-344 — Unit Tests: ForecastAggregationNode
#
# Inner DomainWorkflowGraph node 4: aggregate per-store forecasts into a
# region-level summary. Asserted against the MERGED develop logic:
#   - Empty raw_forecast_results -> aggregated_forecast={} + SUCCESS (passthrough).
#   - horizon = min(len(forecast_units)) across stores.
#   - total_forecast_by_day[d] = sum across stores; avg = round(total/n, 2).
#   - peak_day_index = index of max total; peak_demand_total = total at that index.
#   - model copied from the first store result.
#   - agent_status is the AgentStatus enum.
#
# All output values are JSON-serializable primitives.
#
# ADR-005: raw_forecast_results (input) is passed as a JSON string (to_json);
# aggregated_forecast (output) is read back with from_json() before asserting.
#
# S-4 audit muted at the node module via an autouse fixture (NEVER stub shared.*).

import pytest

from src.nodes.forecast_aggregation_node import ForecastAggregationNode
from src.schemas.state import from_json, to_json
from framework.schemas.agent_status import AgentStatus


@pytest.fixture(autouse=True)
def _mute_audit(monkeypatch):
    """Silence the S-4 audit free function at the node module under test."""
    monkeypatch.setattr(
        "src.nodes.forecast_aggregation_node.emit_trace_event",
        lambda *a, **k: None,
    )


def _store(units, model="TimesFM-2.5-stub-v1"):
    return {
        "store_id": "s",
        "sku_category": "yogurt",
        "horizon_days": len(units),
        "forecast_units": units,
        "model": model,
    }


class TestForecastAggregationNode:
    """Unit tests for ForecastAggregationNode."""

    def setup_method(self):
        self.node = ForecastAggregationNode()

    def test_empty_results_passthrough(self):
        """No forecasts -> aggregated_forecast={} + SUCCESS."""
        result = self.node.execute({"raw_forecast_results": to_json([])})

        assert from_json(result["aggregated_forecast"], {}) == {}
        assert result["agent_status"] is AgentStatus.SUCCESS

    def test_aggregation_totals_avgs_and_peak(self):
        """Totals/avgs/peak are computed from the per-store forecast_units."""
        # 3 identical stores, ascending units => peak is the last day.
        results = [_store([10, 20, 30]) for _ in range(3)]
        out = from_json(
            self.node.execute(
                {
                    "raw_forecast_results": to_json(results),
                    "store_group": "kanto_12",
                    "sku_category": "yogurt",
                }
            )["aggregated_forecast"],
            {},
        )

        assert out["store_count"] == 3
        assert out["horizon_days"] == 3
        assert out["total_forecast_by_day"] == [30, 60, 90]
        assert out["avg_forecast_by_day"] == [10.0, 20.0, 30.0]
        assert out["peak_day_index"] == 2
        assert out["peak_demand_total"] == 90
        assert out["model"] == "TimesFM-2.5-stub-v1"
        assert out["store_group"] == "kanto_12"

    def test_yogurt_kanto12_golden_aggregate(self):
        """12 yogurt stores (stub ramp) -> peak day index 6, total 1608."""
        units = [120, 122, 125, 127, 130, 132, 134]  # base(yogurt)=120, +2%/day
        results = [_store(units) for _ in range(12)]
        out = from_json(
            self.node.execute({"raw_forecast_results": to_json(results), "sku_category": "yogurt"})[
                "aggregated_forecast"
            ],
            {},
        )

        assert out["total_forecast_by_day"] == [1440, 1464, 1500, 1524, 1560, 1584, 1608]
        assert out["peak_day_index"] == 6
        assert out["peak_demand_total"] == 1608

    def test_horizon_is_min_across_stores(self):
        """A ragged store (shorter units) clamps the aggregation horizon."""
        results = [_store([10, 20, 30]), _store([5, 5])]
        out = from_json(
            self.node.execute({"raw_forecast_results": to_json(results)})["aggregated_forecast"],
            {},
        )

        assert out["horizon_days"] == 2
        assert out["total_forecast_by_day"] == [15, 25]

    def test_aggregate_is_json_serializable(self):
        """aggregated_forecast carries only JSON-safe primitives (no numpy/datetime)."""
        import json

        results = [_store([10, 20, 30]) for _ in range(2)]
        out = from_json(
            self.node.execute({"raw_forecast_results": to_json(results)})["aggregated_forecast"],
            {},
        )
        # round-trips cleanly => no numpy / datetime leaked in.
        assert json.loads(json.dumps(out)) == out

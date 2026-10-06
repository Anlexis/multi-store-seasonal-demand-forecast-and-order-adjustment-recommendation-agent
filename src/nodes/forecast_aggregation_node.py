"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state, config=None) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants — never plain strings [A1]
#  - Never import from mediator/, api/, or other agents
#
# RET-C2-344 — ForecastAggregationNode
# Inner DomainWorkflowGraph node 4: aggregate the per-store raw forecasts
# into a single region-level summary dict (total/avg by day, peak day).
# All output values are JSON-serializable primitives (no numpy, no datetime).
#
# Input state keys:
#   raw_forecast_results: JSON STRING of list[dict]  (from MultiStoreForecastCall)
#   store_group, sku_category, horizon_days  (for labelling)
#
# Output state keys (partial dict):
#   aggregated_forecast: JSON STRING (to_json) of dict
#   agent_status:        AgentStatus
#   status:              backbone mirror
#   error_message:       str | None
#
# ADR-005: raw_forecast_results is read back with from_json(); aggregated_forecast
#          is written as a JSON string with to_json() (msgpack safety).
#
# S-4: emit_trace_event free-function import.

import logging
from typing import Any, ClassVar, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json, has_failed, to_json

logger = logging.getLogger(__name__)

# Closed-set failure label — the caller learns the stage, not the exception.
REASON_AGGREGATION_FAILED = "forecast_aggregation_failed"


class ForecastAggregationNode(FunctionNode):
    """Aggregate per-store forecasts into a region-level summary.

    ``aggregated_forecast`` schema:
        store_group:           str
        sku_category:          str
        horizon_days:          int
        store_count:           int
        total_forecast_by_day: list[int]    (sum across stores per day)
        avg_forecast_by_day:   list[float]  (average per store per day)
        peak_day_index:        int
        peak_demand_total:     int
        model:                 str          (from the first store result)

    Empty ``raw_forecast_results`` => aggregated_forecast={} and SUCCESS
    (out-of-scope / validation-failure passthrough).

    Output (partial dict): aggregated_forecast (JSON string), agent_status,
    status, error_message.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, node_config: Optional[dict[str, Any]] = None):
        super().__init__()
        self._node_config = node_config or {}

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if has_failed(state):
            return {}

        results = from_json(state.get("raw_forecast_results"), [])
        if not results:
            return {
                "aggregated_forecast": to_json({}),
                "agent_status": AgentStatus.SUCCESS,
                "status": AgentStatus.SUCCESS.value,
                "error_code": None,
                "error_message": None,
            }

        try:
            # Derive horizon from the data so aggregation is robust to a
            # horizon that differs from the (validated) request value.
            horizon = min(len(r["forecast_units"]) for r in results)

            totals = [int(sum(r["forecast_units"][d] for r in results)) for d in range(horizon)]
            avgs = [round(totals[d] / len(results), 2) for d in range(horizon)]
            peak_idx = totals.index(max(totals)) if totals else 0

            agg = {
                "store_group": state.get("store_group"),
                "sku_category": state.get("sku_category"),
                "horizon_days": horizon,
                "store_count": len(results),
                "total_forecast_by_day": totals,
                "avg_forecast_by_day": avgs,
                "peak_day_index": peak_idx,
                "peak_demand_total": totals[peak_idx] if totals else 0,
                "model": results[0].get("model", "unknown"),
            }

            logger.info(
                "ForecastAggregationNode: aggregated %d stores over %d days, peak day=%d",
                len(results),
                horizon,
                peak_idx,
            )

            emit_trace_event(
                "forecast_aggregated",
                {
                    "store_count": len(results),
                    "horizon_days": horizon,
                    "peak_day_index": peak_idx,
                    "peak_demand_total": agg["peak_demand_total"],
                },
                state,
            )

            return {
                "aggregated_forecast": to_json(agg),
                "agent_status": AgentStatus.SUCCESS,
                "status": AgentStatus.SUCCESS.value,
                "error_code": None,
                "error_message": None,
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("ForecastAggregationNode: aggregation failed")
            emit_trace_event(
                "forecast_aggregation_failed",
                {"reason": REASON_AGGREGATION_FAILED, "exception": type(exc).__name__},
                state,
            )
            return {
                "aggregated_forecast": to_json({}),
                "agent_status": AgentStatus.ERROR,
                "status": AgentStatus.ERROR.value,
                "error_code": REASON_AGGREGATION_FAILED,
                "error_message": REASON_AGGREGATION_FAILED,
            }

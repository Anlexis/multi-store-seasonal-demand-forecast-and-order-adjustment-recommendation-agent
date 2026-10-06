"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state, config=None) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants — never plain strings [A1]
#  - Never import from mediator/, api/, or other agents
#
# RET-C2-344 — OrderAdjustCalcNode
# Inner DomainWorkflowGraph node 6: compute per-store order adjustment
# recommendations (±% from baseline) from the aggregated forecast and the
# food waste risk level. Deterministic v1 rules (no LLM).
#
# Input state keys:
#   aggregated_forecast:   JSON STRING of dict  (avg_forecast_by_day, peak_day_index)
#   raw_forecast_results:  JSON STRING of list  (per-store forecasts)
#   food_waste_risk_flags: JSON STRING of list  (risk level per SKU)
#   sku_category:          str   (labelling)
#
# Output state keys (partial dict):
#   order_adjustments: JSON STRING (to_json) of list[dict]  (one per store)
#   agent_status:      AgentStatus
#   status:            backbone mirror
#   error_message:     str | None
#
# ADR-005: aggregated_forecast / raw_forecast_results / food_waste_risk_flags
#          are read back with from_json(); order_adjustments is written as a
#          JSON string with to_json() (msgpack safety).
#
# S-4: emit_trace_event free-function import.

import logging
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json, has_failed, to_json

logger = logging.getLogger(__name__)

# Closed-set failure label — the caller learns the stage, not the exception.
REASON_ADJUSTMENT_FAILED = "order_adjustment_calculation_failed"

# Base adjustment (%) driven by the food-waste risk level.
ADJUSTMENT_RULES = {"HIGH": -10.0, "MEDIUM": -5.0, "LOW": 0.0}

# Per-store overstock / understock guard band (ratio vs region average).
_OVERSTOCK_RATIO = 1.20
_UNDERSTOCK_RATIO = 0.80
_OVERSTOCK_ADJ = -5.0
_UNDERSTOCK_ADJ = 5.0


class OrderAdjustCalcNode(FunctionNode):
    """Compute per-store ±% order-adjustment recommendations.

    Adjustment logic (v1 deterministic rules):
      1. Base ``current_order_estimate`` = store's forecast_units[peak_day_index].
      2. food_waste_risk_flags risk_level=HIGH   => apply -10%.
      3. risk_level=MEDIUM                        => apply -5%.
      4. risk_level=LOW                           => 0%.
      5. store peak > 20% above region average    => additional -5% (overstock guard).
      6. store peak < 80% of region average        => additional +5% (understock guard).

    Each ``order_adjustments`` entry:
        store_id, sku_category, current_order_estimate, recommended_order,
        adjustment_pct, adjustment_reason.

    Empty ``raw_forecast_results`` or ``aggregated_forecast`` => empty list +
    SUCCESS (passthrough).

    Output (partial dict): order_adjustments (JSON string), agent_status,
    status, error_message.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, node_config: Optional[dict[str, Any]] = None):
        super().__init__()
        self._node_config = node_config or {}

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if has_failed(state):
            return {}

        agg = from_json(state.get("aggregated_forecast"), {})
        raw_results = from_json(state.get("raw_forecast_results"), [])
        risk_flags = from_json(state.get("food_waste_risk_flags"), [])
        sku = state.get("sku_category", "")

        if not raw_results or not agg:
            return {
                "order_adjustments": to_json([]),
                "agent_status": AgentStatus.SUCCESS,
                "status": AgentStatus.SUCCESS.value,
                "error_code": None,
                "error_message": None,
            }

        try:
            peak_idx = agg.get("peak_day_index", 0)
            avg_by_day = agg.get("avg_forecast_by_day", [0])
            region_avg = avg_by_day[peak_idx] if peak_idx < len(avg_by_day) else 0
            risk_level = risk_flags[0]["risk_level"] if risk_flags else "LOW"
            base_adj = ADJUSTMENT_RULES.get(risk_level, 0.0)

            adjustments: List[Dict[str, Any]] = []
            for store in raw_results:
                units = store.get("forecast_units", [])
                store_peak = units[peak_idx] if peak_idx < len(units) else 0

                adj_pct = base_adj
                reason_parts = [f"food_waste_risk:{risk_level}"]

                if region_avg > 0:
                    ratio = store_peak / region_avg
                    if ratio > _OVERSTOCK_RATIO:
                        adj_pct += _OVERSTOCK_ADJ
                        reason_parts.append("overstock_guard:-5%")
                    elif ratio < _UNDERSTOCK_RATIO:
                        adj_pct += _UNDERSTOCK_ADJ
                        reason_parts.append("understock_guard:+5%")

                recommended = round(store_peak * (1 + adj_pct / 100))
                adjustments.append(
                    {
                        "store_id": store.get("store_id"),
                        "sku_category": sku,
                        "current_order_estimate": int(store_peak),
                        "recommended_order": int(recommended),
                        "adjustment_pct": round(adj_pct, 1),
                        "adjustment_reason": "; ".join(reason_parts),
                    }
                )

            logger.info(
                "OrderAdjustCalcNode: %d store adjustments, base risk=%s",
                len(adjustments),
                risk_level,
            )

            emit_trace_event(
                "order_adjustments_calculated",
                {
                    "store_count": len(adjustments),
                    "risk_level": risk_level,
                    "sku_category": sku,
                },
                state,
            )

            return {
                "order_adjustments": to_json(adjustments),
                "agent_status": AgentStatus.SUCCESS,
                "status": AgentStatus.SUCCESS.value,
                "error_code": None,
                "error_message": None,
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("OrderAdjustCalcNode: adjustment calc failed")
            emit_trace_event(
                "order_adjustments_failed",
                {"reason": REASON_ADJUSTMENT_FAILED, "exception": type(exc).__name__},
                state,
            )
            return {
                "order_adjustments": to_json([]),
                "agent_status": AgentStatus.ERROR,
                "status": AgentStatus.ERROR.value,
                "error_code": REASON_ADJUSTMENT_FAILED,
                "error_message": REASON_ADJUSTMENT_FAILED,
            }

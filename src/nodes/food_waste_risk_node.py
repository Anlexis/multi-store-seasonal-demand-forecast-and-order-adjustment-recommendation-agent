"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state, config=None) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants — never plain strings [A1]
#  - Never import from mediator/, api/, or other agents
#
# RET-C2-344 — FoodWasteRiskCalcNode
# Inner DomainWorkflowGraph node 5: compute per-SKU food waste risk flags per
# 食品ロス削減推進法 (Food Loss Reduction Promotion Act) FY2026 risk thresholds.
#
# Input state keys:
#   aggregated_forecast: JSON STRING of dict  (region summary; peak_demand_total)
#   sku_category: str          (threshold lookup key)
#
# Output state keys (partial dict):
#   food_waste_risk_flags: JSON STRING (to_json) of list[dict]
#   agent_status:          AgentStatus
#   status:                backbone mirror
#   error_message:         str | None
#
# ADR-005: aggregated_forecast is read back with from_json(); food_waste_risk_flags
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
REASON_RISK_CALC_FAILED = "food_waste_risk_calculation_failed"

# 食品ロス削減推進法 FY2026 mandatory-reporting risk thresholds per SKU.
FOOD_WASTE_THRESHOLDS = {
    "yogurt": {"shelf_days": 14, "waste_risk_pct": 0.12},  # 12% overstock = waste risk
    "fresh_bread": {"shelf_days": 3, "waste_risk_pct": 0.08},
    "chilled_dessert": {"shelf_days": 7, "waste_risk_pct": 0.10},
    "bento": {"shelf_days": 1, "waste_risk_pct": 0.05},
    "onigiri": {"shelf_days": 1, "waste_risk_pct": 0.05},
}

# Fallback threshold for any SKU not in the table (defensive).
_DEFAULT_THRESHOLD = {"shelf_days": 7, "waste_risk_pct": 0.10}

# Legal reference string surfaced in every flag.
_LAW_REFERENCE = "食品ロス削減推進法 FY2026 mandatory reporting"

# Risk-level boundary fractions of peak demand.
_HIGH_FRACTION = 0.15
_MEDIUM_FRACTION = 0.08


def _risk_level(estimated_waste_units: int, peak: int) -> str:
    """Classify HIGH / MEDIUM / LOW from the estimated waste fraction of peak."""
    if estimated_waste_units > peak * _HIGH_FRACTION:
        return "HIGH"
    if estimated_waste_units > peak * _MEDIUM_FRACTION:
        return "MEDIUM"
    return "LOW"


class FoodWasteRiskCalcNode(FunctionNode):
    """Compute 食品ロス法 food-waste risk flags from the aggregated forecast.

    Each ``food_waste_risk_flags`` entry:
        sku_category:             str
        risk_window_days:         int    (shelf_days for the SKU)
        peak_demand_total:        int    (from aggregated_forecast)
        waste_risk_threshold_pct: float  (from the threshold table)
        estimated_waste_units:    int    (peak × waste_risk_pct)
        risk_level:               str    ("HIGH" / "MEDIUM" / "LOW")
        law_reference:            str    (食品ロス削減推進法 FY2026 ...)

    Empty/absent ``aggregated_forecast`` or ``sku_category`` => empty flags
    list + SUCCESS (passthrough).

    Output (partial dict): food_waste_risk_flags (JSON string), agent_status,
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
        sku = state.get("sku_category", "")
        if not agg or not sku:
            return {
                "food_waste_risk_flags": to_json([]),
                "agent_status": AgentStatus.SUCCESS,
                "status": AgentStatus.SUCCESS.value,
                "error_code": None,
                "error_message": None,
            }

        try:
            threshold = FOOD_WASTE_THRESHOLDS.get(sku, _DEFAULT_THRESHOLD)
            peak = int(agg.get("peak_demand_total", 0))
            est_waste = round(peak * threshold["waste_risk_pct"])
            level = _risk_level(est_waste, peak)

            flag = {
                "sku_category": sku,
                "risk_window_days": threshold["shelf_days"],
                "peak_demand_total": peak,
                "waste_risk_threshold_pct": threshold["waste_risk_pct"],
                "estimated_waste_units": est_waste,
                "risk_level": level,
                "law_reference": _LAW_REFERENCE,
            }

            logger.info(
                "FoodWasteRiskCalcNode: sku=%s peak=%d est_waste=%d level=%s",
                sku,
                peak,
                est_waste,
                level,
            )

            emit_trace_event(
                "food_waste_risk_calculated",
                {
                    "sku_category": sku,
                    "risk_level": level,
                    "estimated_waste_units": est_waste,
                },
                state,
            )

            return {
                "food_waste_risk_flags": to_json([flag]),
                "agent_status": AgentStatus.SUCCESS,
                "status": AgentStatus.SUCCESS.value,
                "error_code": None,
                "error_message": None,
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("FoodWasteRiskCalcNode: risk calc failed")
            emit_trace_event(
                "food_waste_risk_failed",
                {"reason": REASON_RISK_CALC_FAILED, "exception": type(exc).__name__},
                state,
            )
            return {
                "food_waste_risk_flags": to_json([]),
                "agent_status": AgentStatus.ERROR,
                "status": AgentStatus.ERROR.value,
                "error_code": REASON_RISK_CALC_FAILED,
                "error_message": REASON_RISK_CALC_FAILED,
            }

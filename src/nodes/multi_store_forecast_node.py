"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state, config=None) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants — never plain strings [A1]
#  - Never import from mediator/, api/, or other agents
#
# RET-C2-344 — MultiStoreForecastCallNode
# Inner DomainWorkflowGraph node 3: calls BigQuery ML AI.FORECAST (TimesFM)
# once per store in the group.
#
# v1: constructor-injectable forecast client; DETERMINISTIC synthetic forecast.
#     No live BigQuery ML call. Production wires the real BigQuery ML client
#     via the `forecast_client` constructor argument (see DomainWorkflowGraph
#     .register_nodes()).
#
# Input state keys:
#   store_group, sku_category, horizon_days  (validated upstream)
#   validation_passed: bool   (guard — skip if False)
#   out_of_scope: bool        (guard — skip if True)
#
# Output state keys (partial dict):
#   raw_forecast_results: JSON STRING (to_json) of list[dict]  (one per store)
#   agent_status:         AgentStatus
#   status:               backbone mirror
#   error_message:        str | None
#
# ADR-005: raw_forecast_results is written as a JSON string via to_json()
#          (msgpack safety); ForecastAggregation / OrderAdjustCalc read it
#          back with from_json().
#
# S-4: emit_trace_event free-function import (never self.).

import logging
import time
from typing import Any, Callable, ClassVar, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import has_failed, to_json
from src.services.runtime_config import finite_in_range

logger = logging.getLogger(__name__)

# Closed-set failure labels. The caller learns which stage failed and nothing
# else — never an exception's message, never an upstream response body.
REASON_FORECAST_CALL_FAILED = "forecast_call_failed"
REASON_FORECAST_BUDGET_EXCEEDED = "forecast_call_budget_exceeded"

# Fallback when no budget is configured — the same default config/config.yaml
# declares, so an unconfigured graph behaves like a configured one.
_DEFAULT_TIMEOUT_S = 30.0

# Deterministic store membership per group.
STORE_GROUP_MEMBERS = {
    "kanto_12": [f"kanto_store_{i:02d}" for i in range(1, 13)],
    "kansai_8": [f"kansai_store_{i:02d}" for i in range(1, 9)],
    "chubu_6": [f"chubu_store_{i:02d}" for i in range(1, 7)],
    "kyushu_5": [f"kyushu_store_{i:02d}" for i in range(1, 6)],
}

# Per-SKU baseline daily demand used by the synthetic forecast stub.
_BASE_DEMAND = {
    "yogurt": 120,
    "fresh_bread": 85,
    "chilled_dessert": 60,
    "bento": 200,
    "onigiri": 350,
}


def _stub_forecast_client(store_id: str, sku: str, horizon: int) -> dict[str, Any]:
    """Deterministic synthetic forecast for one store.

    Replace with the real BigQuery ML AI.FORECAST (TimesFM) client in
    production via the MultiStoreForecastCallNode(forecast_client=...) hook.
    The shape returned here is the contract consumed by ForecastAggregation /
    OrderAdjustCalc: store_id, sku_category, horizon_days, forecast_units,
    confidence_low, confidence_high, model.
    """
    base = _BASE_DEMAND.get(sku, 100)
    return {
        "store_id": store_id,
        "sku_category": sku,
        "horizon_days": horizon,
        # Gentle deterministic upward seasonal ramp (+2%/day).
        "forecast_units": [round(base * (1 + 0.02 * d)) for d in range(horizon)],
        "confidence_low": [round(base * 0.85)] * horizon,
        "confidence_high": [round(base * 1.15)] * horizon,
        "model": "TimesFM-2.5-stub-v1",
    }


class MultiStoreForecastCallNode(FunctionNode):
    """Per-store demand forecast caller (BigQuery ML AI.FORECAST in production).

    v1 uses a deterministic synthetic stub; the client is constructor-injectable
    so production can wire the real BigQuery ML client without code change here.

    Guards: returns an empty ``raw_forecast_results`` (and SUCCESS) when
    ``validation_passed`` is False or ``out_of_scope`` is True.

    Output (partial dict): raw_forecast_results (JSON string), agent_status,
    status, error_message.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(
        self,
        node_config: Optional[dict[str, Any]] = None,
        forecast_client: Optional[Callable[..., dict[str, Any]]] = None,
    ):
        super().__init__()
        self._node_config = node_config or {}
        # Wall-clock budget for the whole fan-out, from config/config.yaml via
        # the graph constructor. A non-finite or out-of-range value falls back
        # to the declared default rather than removing the bound entirely —
        # NaN compares False against every deadline, so an unchecked value
        # would make the budget unreachable instead of generous.
        budget = finite_in_range(self._node_config.get("timeout_s"), 1, 600)
        self._timeout_s: float = budget if budget is not None else _DEFAULT_TIMEOUT_S
        # production wires the real BigQuery ML client here
        self._client = forecast_client or _stub_forecast_client

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if has_failed(state):
            return {}

        # Guard: skip forecast on validation failure / out-of-scope.
        if not state.get("validation_passed") or state.get("out_of_scope"):
            return {
                "raw_forecast_results": to_json([]),
                "agent_status": AgentStatus.SUCCESS,
                "status": AgentStatus.SUCCESS.value,
                "error_code": None,
                "error_message": None,
            }

        try:
            store_group = state["store_group"]
            sku_category = state["sku_category"]
            horizon_days = state["horizon_days"]
            stores = STORE_GROUP_MEMBERS[store_group]

            deadline = time.monotonic() + self._timeout_s
            results: list[dict[str, Any]] = []
            for store_id in stores:
                if time.monotonic() > deadline:
                    logger.warning(
                        "MultiStoreForecastCallNode: budget of %ss exhausted after %d stores",
                        self._timeout_s,
                        len(results),
                    )
                    emit_trace_event(
                        "multi_store_forecast_budget_exceeded",
                        {"stores_completed": len(results), "budget_s": self._timeout_s},
                        state,
                    )
                    return {
                        "raw_forecast_results": to_json([]),
                        "agent_status": AgentStatus.ERROR,
                        "status": AgentStatus.ERROR.value,
                        "error_code": REASON_FORECAST_BUDGET_EXCEEDED,
                        "error_message": REASON_FORECAST_BUDGET_EXCEEDED,
                    }
                results.append(self._client(store_id=store_id, sku=sku_category, horizon=horizon_days))

            logger.info(
                "MultiStoreForecastCallNode: forecast for group=%s sku=%s stores=%d",
                store_group,
                sku_category,
                len(results),
            )

            emit_trace_event(
                "multi_store_forecast_called",
                {
                    "store_group": store_group,
                    "store_count": len(results),
                    "sku": sku_category,
                },
                state,
            )

            return {
                "raw_forecast_results": to_json(results),
                "agent_status": AgentStatus.SUCCESS,
                "status": AgentStatus.SUCCESS.value,
                "error_code": None,
                "error_message": None,
            }
        except Exception as exc:  # noqa: BLE001
            # The exception's text stays in the server log. It can carry an
            # upstream response body, a path, or a record identifier, none of
            # which belong in a value the caller reads — so what travels
            # forward is the closed-set label and the exception's CLASS.
            logger.exception("MultiStoreForecastCallNode: forecast failed")
            emit_trace_event(
                "multi_store_forecast_failed",
                {"reason": REASON_FORECAST_CALL_FAILED, "exception": type(exc).__name__},
                state,
            )
            return {
                "raw_forecast_results": to_json([]),
                "agent_status": AgentStatus.ERROR,
                "status": AgentStatus.ERROR.value,
                "error_code": REASON_FORECAST_CALL_FAILED,
                "error_message": REASON_FORECAST_CALL_FAILED,
            }

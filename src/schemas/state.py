"""AgentCore Platform v1.0"""

# ADR-005: State must be a flat TypedDict — never Pydantic BaseModel.
# LangGraph checkpoints use msgpack serialization; Pydantic objects
# cause silent corruption.  Extend AgentState with agent-specific
# fields only.  Do NOT add credentials, secrets, or Pydantic models.
#
# ⚠️ ADR-005 (msgpack safety): structured fields (dict / list[dict]) are stored
# as JSON STRINGS, not bare Python containers — a bare dict/list in a
# checkpointed State field violates the gate-state-safety rule. Producers
# serialize with to_json() on write; consumers deserialize with from_json()
# on read.  (SEC-RET-C2-344-002.)
#
# RET-C2-344 — Multi-Store Seasonal Demand Forecast & Order-Adjustment
# Recommendation Agent.  Two-layer nested Cat 2 ToolCalling graph:
# outer backbone (AgentBaseGraph) + inner domain workflow (BaseGraph).
# The fields below cover the full 8-node inner pipeline:
#
#   InputParse -> DataValidate (S-1) -> MultiStoreForecastCall
#     -> ForecastAggregation -> FoodWasteRiskCalc -> OrderAdjustCalc
#     -> OutputFormat (DISCLAIMER) -> OutputValidate (S-3)
#
# S-4: every node emits an audit trace via the free-function
#      emit_trace_event(); the trace_id below correlates them.
# S-5: no credential-like keys are permitted in State.

import json
from typing import Any, Optional

from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus


def to_json(value: Any) -> Optional[str]:
    """Serialize a dict/list State field to a JSON string (ADR-005 msgpack safety).

    None passes through unchanged so an 'unset' field stays distinguishable
    from an empty container.
    """
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def has_failed(state: Any) -> bool:
    """True when an earlier node in the pipeline already failed.

    The domain topology is linear, so every node runs whatever the one before
    it decided. Without this check the next node's own SUCCESS overwrites the
    failure and the request comes back reporting success with a degraded
    report — the failure is not handled, it is erased.
    """
    if state.get("status") == AgentStatus.ERROR.value:
        return True
    return state.get("agent_status") is AgentStatus.ERROR


def from_json(value: Optional[str], default: Any = None) -> Any:
    """Deserialize a JSON-string State field back to its dict/list.

    None / empty / malformed input → the supplied ``default`` so a missing or
    corrupt field is non-fatal for the consuming node.
    """
    if not value or not isinstance(value, str):
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


class State(AgentState):
    """Flat TypedDict for RET-C2-344.

    All shared framework fields (user_input, session_id, correlation_id,
    trace_id, node_history, error_log, etc.) are inherited from AgentState.
    Only the RET-C2-344 domain fields are declared here.

    Structured fields (dict / list[dict]) are stored as JSON STRINGS — ADR-005
    msgpack safety.  Producers serialize with to_json() on write; consumers
    deserialize with from_json() on read.

    Producer / Consumer key contract (see the MR description for the full
    cross-node map):

        raw_input             caller        -> InputParse
        store_group           InputParse    -> DataValidate, MultiStoreForecastCall,
                                               ForecastAggregation, OutputFormat
        sku_category          InputParse    -> DataValidate, MultiStoreForecastCall,
                                               ForecastAggregation, FoodWasteRiskCalc,
                                               OrderAdjustCalc, OutputFormat
        horizon_days          InputParse    -> DataValidate, MultiStoreForecastCall,
                                               ForecastAggregation
        validation_passed     DataValidate  -> MultiStoreForecastCall (guard)
        validation_error      DataValidate  -> OutputFormat
        out_of_scope          DataValidate  -> MultiStoreForecastCall (guard), OutputFormat
        raw_forecast_results  MultiStoreForecastCall -> ForecastAggregation, OrderAdjustCalc  (to_json/from_json)
        aggregated_forecast   ForecastAggregation    -> FoodWasteRiskCalc, OrderAdjustCalc,
                                                        OutputFormat                          (to_json/from_json)
        food_waste_risk_flags FoodWasteRiskCalc      -> OrderAdjustCalc, OutputFormat         (to_json/from_json)
        order_adjustments     OrderAdjustCalc        -> OutputFormat                          (to_json/from_json)
        output_text           OutputFormat  -> OutputValidate (_extra_security_gate_output)
        disclaimer_present    OutputFormat  -> OutputValidate (_extra_security_gate_output)
        status                every node    -> finalize / route guards
        agent_status          every node    -> finalize (domain mirror of status)
        error_message         any node      -> OutputFormat / finalize
    """

    # ------------------------------------------------------------------
    # Caller contract (outer boundary → inner pipeline)
    # ------------------------------------------------------------------

    # The validated request context, produced by the outer pre_process slot
    # from the caller's input_context. Only the declared fields survive
    # validation (store_group / sku_category / horizon_days), each already
    # checked against an inert alphabet or a finite range, so this mapping is
    # safe to carry and safe to render. Unsupported keys are dropped before
    # it exists. Outer-graph field; the inner graph receives it as
    # `forecast_request` through src/graph/context_bridge.py.
    #
    # JSON STRING (to_json), like every other structured field here.
    validated_request: Optional[str]

    # The same validated request, seeded into the INNER graph's initial state
    # by DomainWorkflowGraph._extra_initial_state(). Separate name because the
    # two graphs have separate state instances and the hand-off between them
    # is explicit.  JSON STRING (to_json).
    forecast_request: Optional[str]

    # ------------------------------------------------------------------
    # InputParse
    # ------------------------------------------------------------------

    # Raw natural-language query from the invoke caller (e.g.
    # "Forecast yogurt demand for kanto_12 over 14 days").
    raw_input: str

    # Extracted store group id (e.g. "kanto_12").  None when unrecognised.
    store_group: Optional[str]

    # Extracted SKU category (e.g. "yogurt").  None when unrecognised.
    sku_category: Optional[str]

    # Forecast horizon in days (e.g. 14).  None when no horizon was found.
    horizon_days: Optional[int]

    # ------------------------------------------------------------------
    # DataValidate (S-1 input gate)
    # ------------------------------------------------------------------

    # True when store_group / sku_category / horizon all pass the S-1 gate.
    validation_passed: bool

    # Human-readable reason set on validation failure (bad horizon, etc.).
    validation_error: Optional[str]

    # True when store_group or sku_category is unknown.  Handled gracefully
    # (status stays SUCCESS); merge_output / OutputFormat shape the response.
    out_of_scope: bool

    # ------------------------------------------------------------------
    # MultiStoreForecastCall
    # ------------------------------------------------------------------

    # JSON STRING (to_json) of list[dict] — one deterministic synthetic forecast
    # per store in the group.  JSON-serializable primitives only (no numpy /
    # datetime).  ForecastAggregation / OrderAdjustCalc read it via from_json().
    raw_forecast_results: Optional[str]

    # ------------------------------------------------------------------
    # ForecastAggregation
    # ------------------------------------------------------------------

    # JSON STRING (to_json) of the region-level summary dict (store_count,
    # total/avg by day, peak, model).  JSON-serializable primitives only.
    # FoodWasteRiskCalc / OrderAdjustCalc / OutputFormat read it via from_json().
    aggregated_forecast: Optional[str]

    # ------------------------------------------------------------------
    # FoodWasteRiskCalc (食品ロス削減推進法 thresholds)
    # ------------------------------------------------------------------

    # JSON STRING (to_json) of list[dict] — per-SKU food waste risk flags with
    # law reference.  OrderAdjustCalc / OutputFormat read it via from_json().
    food_waste_risk_flags: Optional[str]

    # ------------------------------------------------------------------
    # OrderAdjustCalc
    # ------------------------------------------------------------------

    # JSON STRING (to_json) of list[dict] — per-store ±% order adjustment
    # recommendations.  OutputFormat reads it via from_json().
    order_adjustments: Optional[str]

    # ------------------------------------------------------------------
    # OutputFormat
    # ------------------------------------------------------------------

    # Rendered output text.  MUST contain the mandatory DISCLAIMER block;
    # the S-3 gate (OutputValidate) rejects output missing it.
    output_text: Optional[str]

    # True when "--- DISCLAIMER ---" was found in output_text by OutputFormat.
    disclaimer_present: bool

    # ------------------------------------------------------------------
    # Status / audit (populated by all nodes)
    # ------------------------------------------------------------------

    # AgentStatus enum value set by each node.  SUCCESS or ERROR only —
    # out-of-scope is NOT a separate status (out_of_scope flag handles it).
    # Declared explicitly so the outer merge_output() can read it from the
    # inner graph's merged state without an attribute-access miss.
    agent_status: AgentStatus

    # String status mirror ("success"/"error") read by the AgentBaseGraph
    # backbone route() and by the outer merge_output().
    status: Optional[str]

    # Closed-set failure label. Every value assigned to this field is one of
    # the module-level constants declared in src/services/input_guard.py or
    # src/nodes/output_validate_node.py — never an exception's message, an
    # upstream response body, or any value the caller supplied. It is the only
    # thing the caller learns about WHY a request failed, so it has to be a
    # label rather than a description.
    error_code: Optional[str]

    # Caller-visible failure label. Drawn from the same closed set as
    # error_code; kept as a separate field because the two are read by
    # different consumers (the outer merge and the rendered report).
    error_message: Optional[str]

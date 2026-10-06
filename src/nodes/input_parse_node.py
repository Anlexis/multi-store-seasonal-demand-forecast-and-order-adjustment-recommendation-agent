"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants — never plain strings [A1]
#  - Never import from mediator/, api/, or other agents
#
# RET-C2-344 — InputParseNode
# Inner DomainWorkflowGraph node 1: resolve the forecast request into
# structured state keys (store_group, sku_category, horizon_days).
# Deterministic — no model is called.
#
# Two sources, in priority order:
#   1. `forecast_request` — the VALIDATED request context, seeded by
#      DomainWorkflowGraph._extra_initial_state() from the outer boundary.
#      Every field in it has already passed an inert-alphabet or finite-range
#      check, so it is used as-is.
#   2. the free-text query, for callers that send only a sentence. Keyword and
#      regex extraction against the same allowlists the input gate enforces.
#
# This node owns the caller contract for the inner pipeline, so it screens the
# query itself rather than relying on the framework's input gate: where that
# gate is configured off, a payload it would have blocked reaches the answer
# path and comes back inside a success envelope.
#
# Input state keys:
#   forecast_request: JSON STRING of the validated request context
#   raw_input / user_input: str — the free-text query
#
# Output state keys (partial dict):
#   store_group:   str | None
#   sku_category:  str | None
#   horizon_days:  int | None
#   agent_status:  AgentStatus.SUCCESS | AgentStatus.ERROR
#   status:        "success" | "error" (backbone mirror)
#   error_code:    closed-set label | None
#   error_message: closed-set label | None
#
# Audit: emit_trace_event used as the free-function import (never self.).

import json
import logging
import re
from typing import Any, ClassVar, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json
from src.services import input_guard

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Allowlists (mirrored by DataValidateNode for the input gate)
# ---------------------------------------------------------------------------

VALID_STORE_GROUPS = {"kanto_12", "kansai_8", "chubu_6", "kyushu_5"}
VALID_SKU_CATEGORIES = {"yogurt", "fresh_bread", "chilled_dessert", "bento", "onigiri"}

# Horizon patterns: "14 days", "2 weeks", "next 2 weeks", etc.
# The digit run is bounded: an unbounded one is a structural attack of its own,
# because int() on a very long digit string raises rather than returning a
# number, turning a malformed request into an exception path.
_HORIZON_DAYS_RE = re.compile(r"(\d{1,4})\s*(?:day|days)\b", re.IGNORECASE)
_HORIZON_WEEKS_RE = re.compile(r"(\d{1,4})\s*(?:week|weeks)\b", re.IGNORECASE)


def _extract_store_group(raw: str) -> Optional[str]:
    """Return the first recognised store group id mentioned in *raw*, else None."""
    lowered = raw.lower()
    for group in sorted(VALID_STORE_GROUPS):
        if group in lowered:
            return group
    return None


def _extract_sku_category(raw: str) -> Optional[str]:
    """Return the first recognised SKU category mentioned in *raw*, else None.

    Matches both the canonical key (``fresh_bread``) and the space form
    (``fresh bread``) so natural-language queries resolve.
    """
    lowered = raw.lower()
    for sku in sorted(VALID_SKU_CATEGORIES):
        if sku in lowered or sku.replace("_", " ") in lowered:
            return sku
    return None


def _extract_horizon(raw: str) -> Optional[int]:
    """Return the forecast horizon in days, or None when no horizon is found.

    "N days" wins over "N weeks"; weeks are converted to days (x7). The result
    is not range-checked here — DataValidateNode owns that decision, and doing
    it in one place keeps the two from drifting apart.
    """
    day_match = _HORIZON_DAYS_RE.search(raw)
    if day_match:
        return int(day_match.group(1))
    week_match = _HORIZON_WEEKS_RE.search(raw)
    if week_match:
        return int(week_match.group(1)) * 7
    return None


def _recover_query(state: dict[str, Any]) -> Any:
    """Return the free-text query from state, WITHOUT coercing its type.

    Unit tests hand `raw_input` directly. In the assembled agent the outer
    hand-off serializes ``{"raw_input": ...}`` into `user_input`, because
    GraphNode.extract_input() must return a string — so recover it from there
    when the direct field is absent. A plain (non-JSON) `user_input` is used
    as-is, which is what a caller posting a bare sentence produces.

    A non-string is returned unchanged rather than replaced by "": coercing it
    here would turn a malformed request into an empty one and let it through
    the screen, which is the caller's type error becoming the agent's silent
    default. validate_query() owns that refusal.
    """
    raw = state.get("raw_input")
    if raw is not None:
        return raw
    user_input = state.get("user_input", "")
    if not isinstance(user_input, str) or not user_input.strip():
        return user_input
    try:
        parsed = json.loads(user_input)
    except (json.JSONDecodeError, ValueError):
        return user_input
    if isinstance(parsed, dict):
        return parsed.get("raw_input", "")
    return user_input


class InputParseNode(FunctionNode):
    """Resolve the forecast request into structured fields.

    First node in the inner DomainWorkflowGraph for RET-C2-344.

    Unmatched fields are returned as ``None`` so the next node
    (DataValidate) can set ``out_of_scope=True`` gracefully. A query that
    fails the caller-contract screen is refused here with a closed-set label
    and never reaches the pipeline.

    Output (partial dict — only the keys this node writes):
        store_group, sku_category, horizon_days,
        agent_status, status, error_code, error_message.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, node_config: Optional[dict[str, Any]] = None):
        super().__init__()
        self._node_config = node_config or {}

    def _refuse(self, reason: str, state: dict[str, Any]) -> dict[str, Any]:
        """Refuse the request with a closed-set label and nothing else.

        The rejected value is never echoed — not here, not in the audit event.
        """
        emit_trace_event("input_refused", {"reason": reason}, state)
        logger.info("InputParseNode: refused request (%s)", reason)
        return {
            "store_group": None,
            "sku_category": None,
            "horizon_days": None,
            "agent_status": AgentStatus.ERROR,
            "status": AgentStatus.ERROR.value,
            "error_code": reason,
            "error_message": reason,
        }

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        raw = _recover_query(state)

        accepted_query, reason = input_guard.validate_query(raw)
        if reason is not None or accepted_query is None:
            return self._refuse(reason or input_guard.REASON_NOT_A_STRING, state)

        request = from_json(state.get("forecast_request"), {}) or {}

        # The validated context wins over anything read out of the sentence:
        # it went through the contract, the sentence did not.
        store_group = request.get("store_group") or _extract_store_group(accepted_query)
        sku_category = request.get("sku_category") or _extract_sku_category(accepted_query)
        horizon_days = request.get("horizon_days")
        if horizon_days is None:
            horizon_days = _extract_horizon(accepted_query)

        logger.info(
            "InputParseNode: resolved store_group=%s sku_category=%s horizon_days=%s",
            store_group,
            sku_category,
            horizon_days,
        )

        emit_trace_event(
            "input_parsed",
            {
                "store_group": store_group,
                "sku_category": sku_category,
                "horizon_days": horizon_days,
                "from_context": sorted(request),
            },
            state,
        )

        return {
            "store_group": store_group,
            "sku_category": sku_category,
            "horizon_days": horizon_days,
            "agent_status": AgentStatus.SUCCESS,
            "status": AgentStatus.SUCCESS.value,
            "error_code": None,
            "error_message": None,
        }

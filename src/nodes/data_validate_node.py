"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants — never plain strings [A1]
#  - Never import from mediator/, api/, or other agents
#
# RET-C2-344 — DataValidateNode
# Inner DomainWorkflowGraph node 2: the input validation gate.
# Validates store_group / sku_category against allowlists and horizon_days
# against the [1, 90] range.  Unknown store/SKU => out_of_scope=True
# (graceful, status stays SUCCESS); bad horizon => validation_passed=False.
#
# Every reason this node produces is a CLOSED-SET label. The earlier version
# interpolated the rejected values into the reason string, and that string is
# rendered into the caller-facing report by OutputFormatNode — so a value the
# gate had just refused came back to the caller inside the answer.
#
# Input state keys:
#   store_group:  str | None  (from InputParse)
#   sku_category: str | None  (from InputParse)
#   horizon_days: int | None  (from InputParse)
#
# Output state keys (partial dict):
#   validation_passed: bool
#   validation_error:  closed-set label | None
#   out_of_scope:      bool
#   agent_status:      AgentStatus.SUCCESS (even out-of-scope) | ERROR on failure
#   status:            backbone string mirror
#   error_code:        closed-set label | None
#   error_message:     closed-set label | None
#
# Audit: emit_trace_event free-function import.

import logging
from typing import Any, ClassVar, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import has_failed

logger = logging.getLogger(__name__)

# Input allowlists (kept in sync with InputParseNode).
VALID_STORE_GROUPS = {"kanto_12", "kansai_8", "chubu_6", "kyushu_5"}
VALID_SKU_CATEGORIES = {"yogurt", "fresh_bread", "chilled_dessert", "bento", "onigiri"}

# Horizon bounds (inclusive).
_HORIZON_MIN = 1
_HORIZON_MAX = 90

# ── Closed-set validation labels ────────────────────────────────────────────
# These are the ONLY reasons this node ever produces. Each names the field and
# the rule, never the value the caller supplied.

REASON_UNKNOWN_STORE_GROUP = "unsupported_store_group"
REASON_UNKNOWN_SKU_CATEGORY = "unsupported_sku_category"
REASON_HORIZON_OUT_OF_RANGE = "horizon_days_out_of_supported_range"


def _horizon_in_range(value: object) -> bool:
    """True when *value* is an int within the supported horizon.

    Bools are rejected explicitly: ``True`` is an int in Python and would pass
    an isinstance check, giving a one-day horizon nobody asked for. Floats are
    rejected too — a non-integral horizon has no meaning here, and accepting
    ``float`` would let NaN through, which compares False against both bounds
    and so passes an ``a <= x <= b`` check written the other way round.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return _HORIZON_MIN <= value <= _HORIZON_MAX


class DataValidateNode(FunctionNode):
    """Input gate for RET-C2-344.

    Validation rules:
      1. store_group must be in VALID_STORE_GROUPS — else out_of_scope=True.
      2. sku_category must be in VALID_SKU_CATEGORIES — else out_of_scope=True.
      3. horizon_days must be an int in [1, 90] — else validation_passed=False.
      4. Out-of-scope => agent_status=SUCCESS (NOT an error; handled gracefully).
      5. Bad horizon => validation_passed=False, validation_error set,
         agent_status=SUCCESS (flow continues to OutputFormat with the label).

    Output (partial dict): validation_passed, validation_error, out_of_scope,
    agent_status, status, error_code, error_message.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, node_config: Optional[dict[str, Any]] = None):
        super().__init__()
        self._node_config = node_config or {}

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        # A node upstream already refused this request; the linear topology
        # would otherwise let this node's SUCCESS erase that decision.
        if has_failed(state):
            return {}

        store_group = state.get("store_group")
        sku_category = state.get("sku_category")
        horizon_days = state.get("horizon_days")

        # Rules 1 + 2: out-of-scope check (unknown store or SKU) — graceful.
        if store_group not in VALID_STORE_GROUPS or sku_category not in VALID_SKU_CATEGORIES:
            reason = (
                REASON_UNKNOWN_STORE_GROUP if store_group not in VALID_STORE_GROUPS else REASON_UNKNOWN_SKU_CATEGORY
            )
            logger.info("DataValidateNode: out of scope — %s", reason)
            emit_trace_event("input_out_of_scope", {"reason": reason}, state)
            return {
                "validation_passed": False,
                "out_of_scope": True,
                "validation_error": reason,
                "agent_status": AgentStatus.SUCCESS,
                "status": AgentStatus.SUCCESS.value,
                "error_code": None,
                "error_message": None,
            }

        # Rule 3: horizon range check — soft failure, SUCCESS.
        if not _horizon_in_range(horizon_days):
            reason = REASON_HORIZON_OUT_OF_RANGE
            logger.info("DataValidateNode: horizon invalid — %s", reason)
            emit_trace_event("input_validation_failed", {"reason": reason}, state)
            return {
                "validation_passed": False,
                "out_of_scope": False,
                "validation_error": reason,
                "agent_status": AgentStatus.SUCCESS,
                "status": AgentStatus.SUCCESS.value,
                "error_code": None,
                "error_message": None,
            }

        # All checks passed.
        logger.info(
            "DataValidateNode: validated store_group=%s sku=%s horizon=%d",
            store_group,
            sku_category,
            horizon_days,
        )
        emit_trace_event(
            "input_validated",
            {
                "store_group": store_group,
                "sku_category": sku_category,
                "horizon_days": horizon_days,
            },
            state,
        )
        return {
            "validation_passed": True,
            "out_of_scope": False,
            "validation_error": None,
            "agent_status": AgentStatus.SUCCESS,
            "status": AgentStatus.SUCCESS.value,
            "error_code": None,
            "error_message": None,
        }

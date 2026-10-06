"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants — never plain strings [A1]
#  - Never import from mediator/, api/, or other agents
#
# RET-C2-344 — OutputValidateNode
# Inner DomainWorkflowGraph node 8 (final): the output security gate.
#
# ⚠️ The gate runs as a custom-named domain method (`_run_s3_domain_gate`)
#    that execute() calls EXPLICITLY — it is NOT a framework hook.
#    DO NOT name it `_extra_security_gate_output` / `_extra_security_gate_input`:
#    the real agenticstar-agentcore wheel auto-wraps those hook names into the
#    graph chain (returns None on the clean path → next node gets state=None
#    → AttributeError at .invoke()). And DO NOT override `_security_gate_output`
#    either — it is @final on the framework base.
#
# ⚠️ THIS NODE IS THE ONLY CONTAINMENT POINT ON THE ERROR PATH.
#    AgentBaseGraph.route() sends every non-SUCCESS status straight to
#    `finalize`, so the outer post_process slot never runs on a violation.
#    And AgentBaseGraph.get_output() returns `formatted_output or result`
#    with no status check, while the inner get_output() maps `output_text`
#    into `result` — so an ERROR that leaves `output_text` in place ships the
#    un-gated answer to the caller inside the error envelope. Returning ERROR
#    is not containment; REPLACING the released text is.
#
# Gate rules:
#   1. No upstream failure recorded.
#   2. output_text must not be None or empty.
#   3. disclaimer_present must be True (DISCLAIMER block mandatory).
#   4. output_text must contain no secret-shaped string, measured by the
#      union of the framework's own detector and this template's local
#      patterns (src/services/input_guard.detect_secret_kinds).
#
# On any failure the released text is replaced by a constant notice carrying a
# closed-set label, and the status becomes ERROR. The notice is truthy on
# purpose: a falsy replacement re-opens the `formatted_output or result`
# fallback onto whatever else survived in state.
#
# Output-invariant note: this template renders unit counts, percentages and
# inert identifiers — it renders no monetary aggregate anywhere, so the
# nearest-1,000 precision grid used by the financial templates does not apply
# here. The invariant this gate enforces is the one this template actually
# states: the DISCLAIMER block is present, and no secret-shaped string is
# released.
#
# Audit: emit_trace_event free-function import.

import logging
from typing import Any, ClassVar, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event
from src.services.input_guard import detect_secret_kinds

logger = logging.getLogger(__name__)

# DISCLAIMER marker (kept in sync with OutputFormatNode.DISCLAIMER_START).
_DISCLAIMER_START = "--- DISCLAIMER ---"

# ── Closed-set gate labels ──────────────────────────────────────────────────
# Everything the caller is ever told about a withheld answer comes from here.

REASON_EMPTY_OUTPUT = "output_missing"
REASON_DISCLAIMER_MISSING = "required_disclaimer_missing"
REASON_SECRET_IN_OUTPUT = "secret_shaped_value_in_output"
REASON_UPSTREAM_FAILURE = "upstream_stage_failed"

_GATE_REASONS = frozenset(
    {
        REASON_EMPTY_OUTPUT,
        REASON_DISCLAIMER_MISSING,
        REASON_SECRET_IN_OUTPUT,
        REASON_UPSTREAM_FAILURE,
    }
)

# The caller-visible replacement. Constant: no rendered text, no field value,
# no exception message, no path. Truthy so the framework's output fallback
# stays closed.
WITHHELD_NOTICE = (
    "Output withheld: the generated report did not satisfy this agent's " "output policy and has not been released."
)


class OutputValidateNode(FunctionNode):
    """Output gate — decide what may be released, and withhold the rest.

    Implemented as a custom-named domain method (``_run_s3_domain_gate``) that
    ``execute()`` calls explicitly — NOT a framework hook. The framework's
    ``_security_gate_output`` is @final and MUST NOT be overridden.

    Output (partial dict): output_text, disclaimer_present, agent_status,
    status, error_code, error_message.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, node_config: Optional[dict[str, Any]] = None):
        super().__init__()
        self._node_config = node_config or {}

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        return self._run_s3_domain_gate(state)

    def _withhold(self, reason: str, state: dict[str, Any], detail: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Return an ERROR delta that carries no released text.

        Every output-bearing field is written — present and replaced, not
        merely absent — because the graph merges partial deltas: a field this
        node does not write keeps the value the previous node left there, and
        that value is exactly the one being withheld.
        """
        payload = {"reason": reason}
        if detail:
            payload.update(detail)
        emit_trace_event("output_withheld", payload, state)
        logger.error("OutputValidateNode: output withheld — %s", reason)
        return {
            "output_text": WITHHELD_NOTICE,
            "disclaimer_present": False,
            "agent_status": AgentStatus.ERROR,
            "status": AgentStatus.ERROR.value,
            "error_code": reason,
            "error_message": reason,
        }

    def _run_s3_domain_gate(self, state: dict[str, Any]) -> dict[str, Any]:
        """The output gate — a domain method called inside execute(); NOT the
        framework base hook. Renamed off ``_extra_security_gate_output``
        because the real agenticstar-agentcore wheel auto-wraps that hook name
        into the graph chain (returns None on the clean path → next node gets
        state=None → AttributeError at ``.invoke()``). Returns a partial state
        dict."""
        # Check 0: an upstream stage already failed. The topology is linear, so
        # without this the gate would pass on whatever partial text survived
        # and report success for a run that did not complete.
        if state.get("status") == AgentStatus.ERROR.value or (state.get("agent_status") is AgentStatus.ERROR):
            upstream = state.get("error_code")
            reason = upstream if upstream in _GATE_REASONS else REASON_UPSTREAM_FAILURE
            return self._withhold(reason, state)

        output_text = state.get("output_text")
        disclaimer_present = state.get("disclaimer_present", False)

        # Check 1: output must exist.
        if not output_text or not isinstance(output_text, str):
            return self._withhold(REASON_EMPTY_OUTPUT, state)

        # Check 2: DISCLAIMER mandatory — trust the flag, but cross-check the text.
        if not disclaimer_present or _DISCLAIMER_START not in output_text:
            return self._withhold(REASON_DISCLAIMER_MISSING, state)

        # Check 3: no secret-shaped string in the released text. The union of
        # the framework's detector and the local patterns; the KINDS reach the
        # audit trail, the matched text never does.
        kinds: List[str] = detect_secret_kinds(output_text)
        if kinds:
            return self._withhold(REASON_SECRET_IN_OUTPUT, state, {"kinds": sorted(kinds)})

        logger.info("OutputValidateNode: gate PASS — disclaimer verified")
        emit_trace_event(
            "output_validated",
            {"disclaimer_verified": True, "length": len(output_text)},
            state,
        )
        return {
            "agent_status": AgentStatus.SUCCESS,
            "status": AgentStatus.SUCCESS.value,
            "error_code": None,
            "error_message": None,
        }

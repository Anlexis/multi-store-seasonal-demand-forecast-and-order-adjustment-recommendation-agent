"""AgentCore Platform v1.0"""

# Caller-request bridge across the outer/inner graph boundary.
#
# Why it exists: the framework invokes a nested graph as
# `subgraph.invoke(user_input, session_id=..., ctx=...)`. Only the request
# STRING crosses — the caller's structured request parameters are not
# forwarded, so anything the outer layer validated would never reach the
# forecast pipeline.
#
# The two sanctioned subclass hooks bridge it:
#
#   _DomainGraphNode.extract_input(state)        [runs BEFORE subgraph.invoke]
#       -> set_forecast_request(<validated request>)
#   DomainWorkflowGraph._extra_initial_state()   [runs INSIDE subgraph.invoke]
#       -> seeds the request into the inner state
#
# What crosses is the VALIDATED request only: every field has already passed
# its bounded, inert shape check. The raw request body never travels.
#
# A ContextVar keeps the hand-off correct per thread and per task, so
# concurrent invocations inside one process cannot see each other's request.

from contextvars import ContextVar
from typing import Any, Dict, Optional

_FORECAST_REQUEST: ContextVar[Optional[Dict[str, Any]]] = ContextVar("ret_c2_344_forecast_request", default=None)


def set_forecast_request(request: Optional[Dict[str, Any]]) -> None:
    """Stash the validated forecast request for the imminent inner-graph invoke."""
    _FORECAST_REQUEST.set(dict(request) if request else {})


def get_forecast_request() -> Dict[str, Any]:
    """Read (without consuming) the stashed request; {} when none was set."""
    return _FORECAST_REQUEST.get() or {}


def clear_forecast_request() -> None:
    """Drop any stashed request.

    Called at the start of every outer hand-off so a value left behind by an
    earlier invocation on the same thread cannot be read as this caller's.
    """
    _FORECAST_REQUEST.set(None)

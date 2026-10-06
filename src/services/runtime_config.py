"""AgentCore Platform v1.0"""

# Runtime configuration loader.
#
# config/agent.yaml is the static manifest the registry reads to find and admit
# this agent. Runtime values live in config/config.yaml and are handed to the
# graph constructor. Nothing reads them implicitly: a graph constructed with no
# config runs entirely on framework defaults, and every value declared in
# config.yaml is then silently unused while still looking configured.
#
# Every number is parsed through a finite and bounded check. float("nan") and
# float("inf") both parse, and every comparison against NaN is False — so a
# non-finite retry budget or timeout does not raise, it quietly removes the
# bound it was meant to impose.

from pathlib import Path
from typing import Any, Dict, Optional

from framework.utils.config_loader import load_config

# src/services/<this file> -> parents[2] is the repository root.
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"

# Bounds for the declared runtime values. These are operator settings, not
# caller input, so a value outside its bound is a deployment defect and fails
# loudly rather than falling back to a default nobody asked for.
#
# max_retry: read by the framework backbone's retry route.
# timeout_s: wall-clock budget for the per-store forecast fan-out
#            (MultiStoreForecastCallNode) — the one place this template makes
#            a call that a production client turns into network I/O.
_BOUNDS: Dict[str, tuple[float, float]] = {
    "max_retry": (0, 10),
    "timeout_s": (1, 600),
}

_DEFAULTS: Dict[str, int] = {
    "max_retry": 3,
    "timeout_s": 30,
}


def finite_in_range(value: Any, low: float, high: float) -> Optional[float]:
    """Return *value* as a float when it is finite and within [low, high], else None.

    Rejects bools (``True`` is an int in Python and passes a naive numeric
    check), non-numeric values, and NaN / +-Infinity. NaN fails both
    comparisons and the infinities fail one, so no non-finite value survives.
    """
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not (low <= number <= high):
        return None
    return number


def load_runtime_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Read config/config.yaml and return the validated runtime mapping.

    A missing file yields the declared defaults. A present file whose values
    are out of range raises: a silently-substituted default is how a declared
    setting stops being a setting.
    """
    config_path = path or _CONFIG_PATH
    raw: Dict[str, Any] = {}
    if config_path.is_file():
        loaded = load_config(str(config_path))
        if isinstance(loaded, dict):
            raw = loaded

    resolved: Dict[str, Any] = {}
    for key, default in _DEFAULTS.items():
        if key not in raw:
            resolved[key] = default
            continue
        low, high = _BOUNDS[key]
        number = finite_in_range(raw[key], low, high)
        if number is None:
            raise ValueError(f"{config_path.name}: {key} must be a finite number in [{low}, {high}]")
        resolved[key] = int(number)

    # Any key the file declares beyond those above is carried through unchanged,
    # so adding a setting does not require editing this loader.
    for key, value in raw.items():
        resolved.setdefault(key, value)
    return resolved


def agent_config() -> Dict[str, Any]:
    """The mapping passed to the graph constructor."""
    return load_runtime_config()

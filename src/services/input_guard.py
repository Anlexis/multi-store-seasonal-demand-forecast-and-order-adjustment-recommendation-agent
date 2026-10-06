"""AgentCore Platform v1.0"""

# The caller contract for this agent, in one place.
#
# Two callers exist and they do not share a path. The HTTP entry point in
# src/api/server.py is one; the registry calls agent.invoke() directly and
# never touches that module. So every guarantee here is enforced by the node
# that owns the caller contract (InputParseNode) as well as at the adapter —
# a check that lives only in the adapter is absent on the path most
# deployments actually use.
#
# Three screens, applied to every caller-supplied string:
#
#   1. Chat-template control markers as a CLASS, not a phrase list. The
#      framework's own scoring does not flag `<<SYS>>` at all, and a directive
#      wrapped in one of these markers is the form an attack actually takes.
#      Screened RAW and again after markup is stripped: stripping alone turns
#      a detectable `<|im_start|>` into undetectable plain text, and a spliced
#      `ig<b>nore` only becomes visible once the markup is gone.
#   2. Directive phrases, anchored so that ordinary retail prose does not trip
#      them. A screen that refuses "act as a supplier" refuses real work.
#   3. Credential shapes, using the framework's own detector as the FLOOR and
#      adding the assignment forms it does not model. The framework patterns
#      describe credential FORMATS, so `password=hunter2` matches none of
#      them; dropping the local patterns in favour of the framework's would
#      make this screen narrower while looking like a tightening.
#
# The scan walks parsed structures depth-first and reads KEYS as well as
# values, so a JSON payload cannot hide a directive in a field name, and a
# \u-escaped payload cannot evade it (the escape is gone once parsed).
#
# Refusals name the FIELD and a reason drawn from a closed set. The rejected
# value is never echoed — not into an error, not into a log, not into the
# audit trail.

import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from framework.security.credential_detector import detect_credentials

# ── Closed-set refusal reasons ──────────────────────────────────────────────
# Everything the caller is ever told about a rejection comes from this set.

REASON_UNSUPPORTED = "unsupported_field"
REASON_NOT_A_STRING = "not_a_string"
REASON_TOO_LONG = "too_long"
REASON_NOT_INERT = "not_an_inert_identifier"
REASON_NOT_FINITE = "not_a_finite_value_in_range"
REASON_CONTROL_TOKEN = "control_marker_in_text"
REASON_DIRECTIVE = "directive_phrase_in_text"
REASON_CREDENTIAL = "credential_shaped_value"
REASON_TOO_MANY_FIELDS = "too_many_fields"

# ── Structural caps ─────────────────────────────────────────────────────────

MAX_QUERY_CHARS = 4096
MAX_IDENTIFIER_CHARS = 32
MAX_CONTEXT_FIELDS = 16
# Horizon bounds — the same range DataValidateNode enforces.
HORIZON_MIN = 1
HORIZON_MAX = 90

# ── Inert alphabet ──────────────────────────────────────────────────────────
# Every caller string that reaches rendered output is locked to this shape.
# It is the alphabet this template's own identifiers render in — store groups
# (`kanto_12`) and SKU categories (`fresh_bread`) — so `_` and digits are in
# the class deliberately.
_INERT_IDENTIFIER_RE = re.compile(r"^[a-z0-9_]{1,%d}$" % MAX_IDENTIFIER_CHARS)

# ── Screen 1: chat-template control markers ─────────────────────────────────
# A class, not an enumeration of known attack strings.
_CONTROL_MARKER_RES: Tuple[re.Pattern[str], ...] = (
    re.compile(r"<\|[^|>]{0,64}\|>"),  # <|im_start|>, <|endoftext|>, ...
    re.compile(r"\[/?INST\]", re.IGNORECASE),  # [INST] / [/INST]
    re.compile(r"<</?SYS>>", re.IGNORECASE),  # <<SYS>> / <</SYS>>
    re.compile(r"</?s>", re.IGNORECASE),  # <s> / </s>
    re.compile(r"</?system>", re.IGNORECASE),  # <system> / </system>
    re.compile(r"<\|?(?:assistant|user)\|?>", re.IGNORECASE),
)

# ── Screen 2: directive phrases ─────────────────────────────────────────────
# Anchored on an imperative verb followed by the thing being overridden, so
# ordinary retail sentences are unaffected. Probed against this template's own
# fixtures in tests/unit/test_input_guard.py.
_DIRECTIVE_RES: Tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\bignore\s+(?:all\s+|any\s+|the\s+)?(?:previous\s+|prior\s+|above\s+)?"
        r"(?:instruction|rule|prompt|direction|constraint)s?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bdisregard\s+(?:all\s+|any\s+|the\s+)?(?:previous\s+|prior\s+|above\s+)?"
        r"(?:instruction|rule|prompt|direction|constraint)s?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bforget\s+(?:all\s+|any\s+|the\s+)?(?:previous\s+|prior\s+|above\s+)?"
        r"(?:instruction|rule|prompt|direction|constraint)s?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\byou\s+are\s+now\s+(?:a|an|the)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:reveal|print|repeat|output|show)\s+(?:me\s+)?(?:your|the)\s+" r"(?:system\s+)?prompt\b", re.IGNORECASE
    ),
    re.compile(r"\b(?:developer|system)\s+mode\s+(?:on|enabled|activated)\b", re.IGNORECASE),
)

# ── Screen 3: credential shapes the framework detector does not model ───────
# The framework matches credential FORMATS (sk-…, AKIA…, eyJ…, Bearer …,
# db URIs). It matches no assignment form at all, which is the shape a pasted
# secret most often takes in prose. These are kept as a UNION with the
# framework detector, never as a replacement for it.
_LOCAL_CREDENTIAL_RES: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    (
        "assignment_form",
        re.compile(
            r"\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|"
            r"private[_-]?key|client[_-]?secret)\s*[:=]\s*\S{6,}",
            re.IGNORECASE,
        ),
    ),
    # GitLab personal access token
    ("gitlab_token", re.compile(r"\bglpat-[A-Za-z0-9_\-]{16,}")),
    # GitHub token families
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    # Payment card number — four groups of four. The framework detector models
    # credential FORMATS and carries nothing for this, so it is kept here and
    # taken as part of the union rather than delegated away.
    ("card_number", re.compile(r"\b\d{4}[\-\s]\d{4}[\-\s]\d{4}[\-\s]\d{4}\b")),
)

# The local patterns, exposed so the output boundary uses the SAME set as the
# input screen. Whichever side is narrower is the one that leaks, so there is
# one set and both sides read it.
LOCAL_CREDENTIAL_KINDS: Tuple[str, ...] = tuple(kind for kind, _ in _LOCAL_CREDENTIAL_RES)


def detect_secret_kinds(text: str) -> List[str]:
    """Return the KINDS of secret-shaped strings found in *text*, never the values.

    The union of the framework's own detector and the local patterns above:

      - the framework's set is the FLOOR. A value it catches and this template
        misses makes the framework raise inside the node that produced the
        output, and the wrapper then discards whatever containment this
        template had arranged — a detector gap is a containment bypass.
      - the local set is kept because the framework's patterns describe
        credential FORMATS and match none of the assignment forms, none of the
        two forge-tokens above, and no card number. Replacing the local set
        with the framework's would make this screen NARROWER while looking
        like a tightening.

    Wider is safe here; narrower is a bypass.
    """
    kinds = sorted({finding["type"] for finding in detect_credentials(text)})
    for kind, pattern in _LOCAL_CREDENTIAL_RES:
        if pattern.search(text):
            kinds.append(kind)
    return sorted(set(kinds))


# Markup strip used for the second pass of screens 1 and 2. Removing tags
# re-assembles a directive that was spliced apart by inline markup.
_MARKUP_RE = re.compile(r"<[^>]{0,200}>")


def _strip_markup(text: str) -> str:
    """Return *text* with inline markup removed and unicode normalised.

    Normalisation first: a fullwidth or compatibility-form character would
    otherwise carry a directive past a pattern written in ASCII.
    """
    normalised = unicodedata.normalize("NFKC", text)
    return _MARKUP_RE.sub("", normalised)


def screen_text(text: str) -> Optional[str]:
    """Return a closed-set reason when *text* must be refused, else None.

    Both the raw string and its markup-stripped form are screened. A sanitizer
    is not a refusal: stripping `<|im_start|>` would remove the marker and
    forward the directive residue as ordinary text, so the raw pass has to run
    first and the stripped pass has to run as well.
    """
    stripped = _strip_markup(text)
    for candidate in (text, stripped):
        for pattern in _CONTROL_MARKER_RES:
            if pattern.search(candidate):
                return REASON_CONTROL_TOKEN
    for candidate in (text, stripped):
        for pattern in _DIRECTIVE_RES:
            if pattern.search(candidate):
                return REASON_DIRECTIVE
    for candidate in (text, stripped):
        if detect_secret_kinds(candidate):
            return REASON_CREDENTIAL
    return None


def screen_structure(value: Any, _depth: int = 0) -> Optional[str]:
    """Screen a parsed structure depth-first, reading keys as well as values.

    A payload can carry a directive in a field NAME as easily as in a field
    value, and a \\u-escaped attack is plain text by the time it is parsed —
    so the scan runs post-parse over the whole structure.
    """
    if _depth > 8:
        return REASON_TOO_LONG
    if isinstance(value, str):
        return screen_text(value)
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str):
                reason = screen_text(key)
                if reason:
                    return reason
            reason = screen_structure(nested, _depth + 1)
            if reason:
                return reason
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            reason = screen_structure(item, _depth + 1)
            if reason:
                return reason
        return None
    return None


def validate_query(text: Any) -> Tuple[Optional[str], Optional[str]]:
    """Validate the free-text forecast query.

    Returns ``(accepted_text, reason)`` — exactly one is None.
    """
    if not isinstance(text, str):
        return None, REASON_NOT_A_STRING
    if len(text) > MAX_QUERY_CHARS:
        return None, REASON_TOO_LONG
    reason = screen_text(text)
    if reason:
        return None, reason
    return text, None


def validate_identifier(value: Any) -> Tuple[Optional[str], Optional[str]]:
    """Validate a caller-supplied identifier against the inert alphabet."""
    if not isinstance(value, str):
        return None, REASON_NOT_A_STRING
    if len(value) > MAX_IDENTIFIER_CHARS:
        return None, REASON_TOO_LONG
    if not _INERT_IDENTIFIER_RE.match(value):
        return None, REASON_NOT_INERT
    return value, None


def validate_horizon(value: Any) -> Tuple[Optional[int], Optional[str]]:
    """Validate a caller-supplied forecast horizon in days.

    Finite and bounded. NaN and the infinities parse through float() and then
    compare False against every bound, which would leave the horizon
    unconstrained on exactly the value the check exists to constrain.
    """
    if isinstance(value, bool):
        return None, REASON_NOT_FINITE
    if isinstance(value, str):
        # A digit run of arbitrary length is a structural attack in its own
        # right: int() on a very long string raises, and the length check has
        # to come first for the refusal to stay readable.
        if len(value) > MAX_IDENTIFIER_CHARS:
            return None, REASON_TOO_LONG
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, REASON_NOT_FINITE
    if not (HORIZON_MIN <= number <= HORIZON_MAX):
        return None, REASON_NOT_FINITE
    if number != int(number):
        return None, REASON_NOT_FINITE
    return int(number), None


# ── The request context contract ────────────────────────────────────────────
# Exactly three optional fields, each inert or finite. Anything else is
# DROPPED rather than refused: an unsupported key is not merely ignored
# downstream — it stays on the context channel, the framework's first node
# returns that channel verbatim inside its own result, and the framework's
# output scan then fails the whole run with an error the caller cannot act on.

_CONTEXT_VALIDATORS = {
    "store_group": validate_identifier,
    "sku_category": validate_identifier,
    "horizon_days": validate_horizon,
}

SUPPORTED_CONTEXT_FIELDS: Tuple[str, ...] = tuple(_CONTEXT_VALIDATORS)


def validate_context(
    raw: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, Any], List[Tuple[str, str]]]:
    """Reduce a request context to the supported, inert subset.

    Returns ``(accepted, refusals)``. ``refusals`` is a list of
    ``(field, reason)`` pairs for SUPPORTED fields whose value is unusable —
    the value itself is never returned. Unsupported keys are dropped silently
    and do not appear in either result, which is what makes the accepted
    mapping structurally safe to hand to invoke().
    """
    if raw is None:
        return {}, []
    if not isinstance(raw, dict):
        return {}, [("input_context", REASON_NOT_A_STRING)]
    if len(raw) > MAX_CONTEXT_FIELDS:
        return {}, [("input_context", REASON_TOO_MANY_FIELDS)]

    accepted: Dict[str, Any] = {}
    refusals: List[Tuple[str, str]] = []
    for index, (key, value) in enumerate(raw.items()):
        validator = _CONTEXT_VALIDATORS.get(key) if isinstance(key, str) else None
        if validator is None:
            # Unsupported — dropped, never carried onto the context channel.
            continue
        # The credential screen runs on supported fields before their shape
        # check, so a credential-shaped value is refused as a credential
        # rather than as a malformed identifier.
        if isinstance(value, str) and screen_text(value) == REASON_CREDENTIAL:
            refusals.append((_safe_field_name(key, index), REASON_CREDENTIAL))
            continue
        cleaned, reason = validator(value)
        if reason:
            refusals.append((_safe_field_name(key, index), reason))
            continue
        accepted[key] = cleaned
    return accepted, refusals


def _safe_field_name(key: str, index: int) -> str:
    """Echo a field name only when the name is itself inert.

    Field names are caller data too. A name that is not an inert identifier is
    reported positionally instead.
    """
    if _INERT_IDENTIFIER_RE.match(key):
        return key
    return f"field #{index}"

# RET-C2-344 — Unit Tests: InputParseNode
#
# Inner DomainWorkflowGraph node 1: deterministic keyword/regex extraction of
# store_group / sku_category / horizon_days from the raw query.
#
# Asserted against the MERGED develop implementation:
#   - VALID_STORE_GROUPS = {kanto_12, kansai_8, chubu_6, kyushu_5}
#   - VALID_SKU_CATEGORIES = {yogurt, fresh_bread, chilled_dessert, bento, onigiri}
#   - "N days" wins over "N weeks"; weeks -> days (x7).
#   - SKU space form ("fresh bread") resolves to the canonical key.
#   - Unmatched fields return None (graceful — DataValidate handles scope).
#   - agent_status is the AgentStatus enum; status is its string-form mirror.
#   - Non-string raw_input -> agent_status=ERROR.
#
# S-4 audit muted at the node module via an autouse fixture (NEVER stub shared.*
# in sys.modules — the CI wheel ships a real shared package).

import pytest

from src.nodes.input_parse_node import InputParseNode
from src.services import input_guard
from framework.schemas.agent_status import AgentStatus


@pytest.fixture(autouse=True)
def _mute_audit(monkeypatch):
    """Silence the S-4 audit free function at the node module under test."""
    monkeypatch.setattr(
        "src.nodes.input_parse_node.emit_trace_event",
        lambda *a, **k: None,
    )


class TestInputParseNode:
    """Unit tests for InputParseNode (deterministic extraction)."""

    def setup_method(self):
        self.node = InputParseNode()

    def test_happy_path_parses_all_fields(self):
        """A full query yields store_group, sku_category, horizon_days + SUCCESS."""
        result = self.node.execute({"raw_input": "Forecast yogurt demand for kanto_12 over 14 days"})

        assert result["store_group"] == "kanto_12"
        assert result["sku_category"] == "yogurt"
        assert result["horizon_days"] == 14
        assert result["agent_status"] is AgentStatus.SUCCESS
        assert result["status"] == getattr(AgentStatus.SUCCESS, "value")
        assert result["error_message"] is None

    def test_weeks_converted_to_days(self):
        """'2 weeks' -> 14 days (weeks x7) when no explicit 'days' is present."""
        result = self.node.execute({"raw_input": "kansai_8 onigiri demand next 2 weeks"})

        assert result["store_group"] == "kansai_8"
        assert result["sku_category"] == "onigiri"
        assert result["horizon_days"] == 14

    def test_days_wins_over_weeks(self):
        """When both appear, the 'N days' pattern takes precedence over weeks."""
        result = self.node.execute({"raw_input": "chubu_6 bento 10 days not 3 weeks"})

        assert result["horizon_days"] == 10

    def test_sku_space_form_resolves_to_canonical_key(self):
        """'fresh bread' (space form) maps to the canonical 'fresh_bread' key."""
        result = self.node.execute({"raw_input": "kyushu_5 fresh bread for 7 days"})

        assert result["sku_category"] == "fresh_bread"

    def test_unmatched_fields_return_none(self):
        """Unknown store/SKU and no horizon return None (graceful, SUCCESS)."""
        result = self.node.execute({"raw_input": "tell me something nice"})

        assert result["store_group"] is None
        assert result["sku_category"] is None
        assert result["horizon_days"] is None
        # Parse never errors on a benign string — it just yields Nones.
        assert result["agent_status"] is AgentStatus.SUCCESS

    def test_missing_raw_input_defaults_to_empty_string(self):
        """Absent raw_input -> "" default -> all None, SUCCESS (no crash)."""
        result = self.node.execute({})

        assert result["store_group"] is None
        assert result["sku_category"] is None
        assert result["horizon_days"] is None
        assert result["agent_status"] is AgentStatus.SUCCESS

    def test_non_string_raw_input_is_refused(self):
        """A non-string query is refused with a closed-set label, not an exception.

        The earlier implementation reached this case by raising TypeError and
        then interpolating the exception's message into a field that is
        rendered into the caller-facing report.
        """
        result = self.node.execute({"raw_input": 12345})

        assert result["agent_status"] is AgentStatus.ERROR
        assert result["status"] == getattr(AgentStatus.ERROR, "value")
        assert result["error_code"] == input_guard.REASON_NOT_A_STRING
        assert result["error_message"] == input_guard.REASON_NOT_A_STRING
        assert result["store_group"] is None
        # The rejected value is never echoed back.
        assert "12345" not in str(result)

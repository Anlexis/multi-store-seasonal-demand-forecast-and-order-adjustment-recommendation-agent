# RET-C2-344 — Proof-of-Boundary: out-of-scope returns SUCCESS
#
# PoB: an unknown store_group / sku_category must be handled GRACEFULLY — the
# S-1 gate (DataValidateNode) sets out_of_scope=True and keeps agent_status at
# SUCCESS (NOT ERROR). Out-of-scope is not an error boundary.
#
# Asserted against the MERGED develop DataValidateNode (Rule 1+2: unknown store
# or SKU -> out_of_scope=True, agent_status=SUCCESS). AgentStatus has only
# SUCCESS / ERROR — there is no OUT_OF_SCOPE member.
#
# S-4 audit muted at the node module via an autouse fixture (NEVER stub shared.*).

import pytest

from src.nodes.data_validate_node import DataValidateNode
from framework.schemas.agent_status import AgentStatus


@pytest.fixture(autouse=True)
def _mute_audit(monkeypatch):
    """Silence the S-4 audit free function at the node module under test."""
    monkeypatch.setattr(
        "src.nodes.data_validate_node.emit_trace_event",
        lambda *a, **k: None,
    )


class TestPoBOutOfScope:
    """PoB: out-of-scope is graceful SUCCESS, not an error boundary."""

    def setup_method(self):
        self.node = DataValidateNode()

    def test_unknown_store_group_is_out_of_scope_not_error(self):
        """Unknown store_group -> out_of_scope=True, agent_status=SUCCESS."""
        state = {"store_group": "unknown_region_99", "sku_category": "yogurt", "horizon_days": 14}
        result = self.node.execute(state)

        assert result["out_of_scope"] is True
        assert result["agent_status"].name == "SUCCESS", "out_of_scope must be SUCCESS, not ERROR"
        assert result["agent_status"] is AgentStatus.SUCCESS

    def test_unknown_sku_is_out_of_scope_not_error(self):
        """Unknown sku_category -> out_of_scope=True, agent_status=SUCCESS."""
        state = {"store_group": "kanto_12", "sku_category": "mystery_product", "horizon_days": 14}
        result = self.node.execute(state)

        assert result["out_of_scope"] is True
        assert result["agent_status"].name == "SUCCESS"

    def test_both_unknown_is_out_of_scope(self):
        """Unknown store AND SKU -> out_of_scope=True, agent_status=SUCCESS."""
        state = {"store_group": "unknown", "sku_category": "unknown", "horizon_days": 14}
        result = self.node.execute(state)

        assert result["out_of_scope"] is True
        assert result["agent_status"].name == "SUCCESS"

    def test_out_of_scope_status_is_not_error_string(self):
        """The backbone status mirror is the SUCCESS string, never ERROR."""
        state = {"store_group": "unknown", "sku_category": "unknown", "horizon_days": 14}
        result = self.node.execute(state)

        assert result["status"] == getattr(AgentStatus.SUCCESS, "value")
        assert result["status"] != getattr(AgentStatus.ERROR, "value")

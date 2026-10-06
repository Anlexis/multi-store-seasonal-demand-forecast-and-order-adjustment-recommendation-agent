# RET-C2-344 — Unit Tests: DataValidateNode (S-1 input gate)
#
# Inner DomainWorkflowGraph node 2. Asserted against the MERGED develop logic:
#   Rule 1+2: store_group not in allowlist OR sku not in allowlist
#             -> out_of_scope=True, validation_passed=False, agent_status=SUCCESS.
#   Rule 3:   horizon_days not int-in-[1,90] (bool excluded) -> validation_passed=
#             False, validation_error set, out_of_scope=False, agent_status=SUCCESS.
#   All-pass: validation_passed=True, out_of_scope=False, validation_error=None.
#   agent_status is the AgentStatus enum (SUCCESS even when out-of-scope/bad-horizon).
#
# Out-of-scope is NOT an error — agent_status stays SUCCESS, out_of_scope=True.
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


class TestDataValidateNode:
    """Unit tests for DataValidateNode (S-1 gate)."""

    def setup_method(self):
        self.node = DataValidateNode()

    def test_all_valid_passes(self):
        """Known store + SKU + horizon in range -> validation_passed=True, SUCCESS."""
        result = self.node.execute({"store_group": "kanto_12", "sku_category": "yogurt", "horizon_days": 14})

        assert result["validation_passed"] is True
        assert result["out_of_scope"] is False
        assert result["validation_error"] is None
        assert result["agent_status"] is AgentStatus.SUCCESS
        assert result["status"] == getattr(AgentStatus.SUCCESS, "value")

    def test_unknown_store_group_is_out_of_scope_success(self):
        """Unknown store_group -> out_of_scope=True, SUCCESS (NOT error)."""
        result = self.node.execute({"store_group": "unknown_region_99", "sku_category": "yogurt", "horizon_days": 14})

        assert result["out_of_scope"] is True
        assert result["validation_passed"] is False
        assert result["agent_status"] is AgentStatus.SUCCESS
        # validation_error carries the human-readable reason on the out-of-scope path.
        assert result["validation_error"]

    def test_unknown_sku_is_out_of_scope_success(self):
        """Unknown sku_category -> out_of_scope=True, SUCCESS."""
        result = self.node.execute({"store_group": "kanto_12", "sku_category": "mystery_product", "horizon_days": 14})

        assert result["out_of_scope"] is True
        assert result["agent_status"] is AgentStatus.SUCCESS

    def test_horizon_zero_is_validation_failure_not_out_of_scope(self):
        """horizon_days=0 (below min 1) -> validation_passed=False, out_of_scope=False."""
        result = self.node.execute({"store_group": "kanto_12", "sku_category": "yogurt", "horizon_days": 0})

        assert result["validation_passed"] is False
        assert result["out_of_scope"] is False
        assert result["validation_error"]
        # Soft failure — flow continues, so agent_status stays SUCCESS.
        assert result["agent_status"] is AgentStatus.SUCCESS

    def test_horizon_above_max_is_validation_failure(self):
        """horizon_days=91 (above max 90) -> validation_passed=False, SUCCESS."""
        result = self.node.execute({"store_group": "kanto_12", "sku_category": "yogurt", "horizon_days": 91})

        assert result["validation_passed"] is False
        assert result["out_of_scope"] is False
        assert result["agent_status"] is AgentStatus.SUCCESS

    def test_horizon_boundary_values_pass(self):
        """horizon_days at the inclusive bounds 1 and 90 both validate."""
        for h in (1, 90):
            result = self.node.execute({"store_group": "kansai_8", "sku_category": "bento", "horizon_days": h})
            assert result["validation_passed"] is True, f"horizon={h} must pass"

    def test_bool_horizon_rejected(self):
        """A bool horizon (True) is explicitly rejected even though bool is an int."""
        result = self.node.execute({"store_group": "kanto_12", "sku_category": "yogurt", "horizon_days": True})

        assert result["validation_passed"] is False
        assert result["out_of_scope"] is False
        assert result["agent_status"] is AgentStatus.SUCCESS

    def test_none_horizon_is_validation_failure(self):
        """horizon_days=None -> not an int -> validation_passed=False, SUCCESS."""
        result = self.node.execute({"store_group": "kanto_12", "sku_category": "yogurt", "horizon_days": None})

        assert result["validation_passed"] is False
        assert result["out_of_scope"] is False
        assert result["agent_status"] is AgentStatus.SUCCESS

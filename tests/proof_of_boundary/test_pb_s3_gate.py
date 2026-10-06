# RET-C2-344 — Proof-of-Boundary: S-3 output gate enforcement
#
# PoB: OutputValidateNode._run_s3_domain_gate (the S-3 gate, called explicitly
# from execute()) MUST reject output that is missing the mandatory DISCLAIMER
# block, and reject empty output.
#
# Asserted against the MERGED develop implementation: the gate returns
# agent_status=ERROR (the AgentStatus enum) on any violation — it does not raise.
# On missing DISCLAIMER the error_message contains "DISCLAIMER".
#
# S-4 audit muted at the node module via an autouse fixture (NEVER stub shared.*
# in sys.modules — the CI wheel ships a real shared package).

import pytest

from src.nodes.output_validate_node import (
    REASON_DISCLAIMER_MISSING,
    WITHHELD_NOTICE,
    OutputValidateNode,
)
from framework.schemas.agent_status import AgentStatus


@pytest.fixture(autouse=True)
def _mute_audit(monkeypatch):
    """Silence the S-4 audit free function at the node module under test."""
    monkeypatch.setattr(
        "src.nodes.output_validate_node.emit_trace_event",
        lambda *a, **k: None,
    )


class TestPoBS3Gate:
    """PoB: the S-3 output gate cannot be bypassed."""

    def setup_method(self):
        self.node = OutputValidateNode()

    def test_s3_rejects_missing_disclaimer(self):
        """Output without the DISCLAIMER block -> ERROR mentioning DISCLAIMER."""
        state = {
            "output_text": "Some forecast result without disclaimer block.",
            "disclaimer_present": False,
            "agent_status": None,
            "error_message": None,
        }
        result = self.node.execute(state)

        assert result["agent_status"].name == "ERROR", "S-3 must reject output missing DISCLAIMER"
        assert result["agent_status"] is AgentStatus.ERROR
        assert result["error_code"] == REASON_DISCLAIMER_MISSING
        # Rejecting is not containing: the text the gate refused must not
        # travel onward in the delta it returns.
        assert result["output_text"] == WITHHELD_NOTICE
        assert "Some forecast result" not in str(result)

    def test_s3_rejects_empty_output(self):
        """output_text=None -> ERROR (empty-output check)."""
        result = self.node.execute(
            {"output_text": None, "disclaimer_present": False, "agent_status": None, "error_message": None}
        )

        assert result["agent_status"].name == "ERROR"
        assert result["agent_status"] is AgentStatus.ERROR

    def test_s3_rejects_empty_string(self):
        """output_text='' -> ERROR (empty-output check)."""
        result = self.node.execute(
            {"output_text": "", "disclaimer_present": False, "agent_status": None, "error_message": None}
        )

        assert result["agent_status"].name == "ERROR"

    def test_s3_rejects_flag_true_but_marker_absent(self):
        """A spoofed disclaimer_present=True with no marker still fails the gate."""
        result = self.node.execute(
            {"output_text": "no marker", "disclaimer_present": True, "agent_status": None, "error_message": None}
        )

        assert result["agent_status"].name == "ERROR"
        assert result["error_code"] == REASON_DISCLAIMER_MISSING
        assert result["output_text"] == WITHHELD_NOTICE

    def test_s3_passes_with_disclaimer(self):
        """Valid output carrying the DISCLAIMER block -> SUCCESS."""
        state = {
            "output_text": "Forecast results...\n--- DISCLAIMER ---\nAI advisory only.\n--- END DISCLAIMER ---",
            "disclaimer_present": True,
            "agent_status": None,
            "error_message": None,
        }
        result = self.node.execute(state)

        assert result["agent_status"].name == "SUCCESS"
        assert result["agent_status"] is AgentStatus.SUCCESS

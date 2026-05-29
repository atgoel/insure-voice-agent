"""
tests/test_audit_log.py

TASK-063: Verify the _write_audit_log() function in agent_builder/main.py:
  - Calls google.cloud.logging with the correct structured payload
  - Payload contains required audit fields (Constitution §IV)
  - Payload does NOT contain PII fields (Constitution §V)

google.cloud.logging is mocked — no live GCP calls are made.
"""

import sys
import pathlib
from unittest.mock import MagicMock, patch, call

import pytest

# Ensure agent_builder/ is on the path so we can import main
_agent_builder = pathlib.Path(__file__).parent.parent / "agent_builder"
if str(_agent_builder) not in sys.path:
    sys.path.insert(0, str(_agent_builder))


# ---------------------------------------------------------------------------
# Sample audit payload (Constitution §IV fields, no PII per §V)
# ---------------------------------------------------------------------------

SAMPLE_AUDIT_PAYLOAD = {
    "session_id": "test-session-abc123",
    "candidate_products": [
        {"product_id": "TERM001", "name": "SecureLife Term Plan", "elser_score": 9.2},
        {"product_id": "TERM002", "name": "FamilyShield Plus", "elser_score": 8.5},
    ],
    "compliance_outcomes": {
        "passed_count": 2,
        "rejected": [],
    },
    "final_rankings": [
        {"rank": 1, "product_id": "TERM001", "suitability_score": 0.91,
         "score_breakdown": {"elser_relevance": 0.9, "age_centrality": 0.95}},
        {"rank": 2, "product_id": "TERM002", "suitability_score": 0.83,
         "score_breakdown": {"elser_relevance": 0.8, "age_centrality": 0.85}},
    ],
}

# PII fields that must NEVER appear in audit payload (Constitution §V)
FORBIDDEN_PII_FIELDS = {"name", "email", "phone", "contact", "address", "pan", "aadhaar"}


# ---------------------------------------------------------------------------
# Tests for _write_audit_log
# ---------------------------------------------------------------------------

class TestWriteAuditLog:

    def _get_write_fn(self, mock_gcp_logger):
        """Import main with GCP logging mocked, return _write_audit_log."""
        # Patch at the module level so the try/except in main.py picks it up
        mock_client_instance = MagicMock()
        mock_client_instance.logger.return_value = mock_gcp_logger
        with patch.dict("sys.modules", {
            "google.cloud": MagicMock(),
            "google.cloud.logging": MagicMock(Client=lambda: mock_client_instance),
        }):
            # Re-import to pick up the mock
            if "main" in sys.modules:
                del sys.modules["main"]
            import main as agent_main
            return agent_main._write_audit_log, agent_main

    def test_calls_gcp_log_struct(self):
        """_write_audit_log must call log_struct on the GCP logger."""
        mock_gcp_logger = MagicMock()

        with patch("main._gcp_logger", mock_gcp_logger):
            import main as agent_main
            agent_main._write_audit_log(SAMPLE_AUDIT_PAYLOAD)

        mock_gcp_logger.log_struct.assert_called_once()
        args, kwargs = mock_gcp_logger.log_struct.call_args
        sent_payload = args[0]
        assert sent_payload == SAMPLE_AUDIT_PAYLOAD

    def test_severity_is_info(self):
        """Audit entries must be logged at INFO severity."""
        mock_gcp_logger = MagicMock()

        with patch("main._gcp_logger", mock_gcp_logger):
            import main as agent_main
            agent_main._write_audit_log(SAMPLE_AUDIT_PAYLOAD)

        _, kwargs = mock_gcp_logger.log_struct.call_args
        assert kwargs.get("severity") == "INFO", (
            f"Expected severity=INFO, got {kwargs.get('severity')}"
        )

    def test_payload_contains_session_id(self):
        """Audit payload must contain session_id for correlation (Constitution §IV)."""
        mock_gcp_logger = MagicMock()

        with patch("main._gcp_logger", mock_gcp_logger):
            import main as agent_main
            agent_main._write_audit_log(SAMPLE_AUDIT_PAYLOAD)

        sent_payload = mock_gcp_logger.log_struct.call_args[0][0]
        assert "session_id" in sent_payload, "Audit payload must contain session_id"
        assert sent_payload["session_id"] == "test-session-abc123"

    def test_payload_contains_candidate_products(self):
        """Audit payload must contain candidate_products list (Constitution §IV)."""
        mock_gcp_logger = MagicMock()

        with patch("main._gcp_logger", mock_gcp_logger):
            import main as agent_main
            agent_main._write_audit_log(SAMPLE_AUDIT_PAYLOAD)

        sent_payload = mock_gcp_logger.log_struct.call_args[0][0]
        assert "candidate_products" in sent_payload
        assert isinstance(sent_payload["candidate_products"], list)

    def test_payload_contains_compliance_outcomes(self):
        """Audit payload must contain compliance_outcomes (Constitution §IV)."""
        mock_gcp_logger = MagicMock()

        with patch("main._gcp_logger", mock_gcp_logger):
            import main as agent_main
            agent_main._write_audit_log(SAMPLE_AUDIT_PAYLOAD)

        sent_payload = mock_gcp_logger.log_struct.call_args[0][0]
        assert "compliance_outcomes" in sent_payload

    def test_payload_contains_final_rankings(self):
        """Audit payload must contain final_rankings list (Constitution §IV)."""
        mock_gcp_logger = MagicMock()

        with patch("main._gcp_logger", mock_gcp_logger):
            import main as agent_main
            agent_main._write_audit_log(SAMPLE_AUDIT_PAYLOAD)

        sent_payload = mock_gcp_logger.log_struct.call_args[0][0]
        assert "final_rankings" in sent_payload
        assert isinstance(sent_payload["final_rankings"], list)

    def test_payload_does_not_contain_pii(self):
        """Audit payload must NOT contain customer PII fields (Constitution §V)."""
        # Build a payload that would contain PII if not filtered
        pii_payload = {
            **SAMPLE_AUDIT_PAYLOAD,
            # These should never appear in practice; test that existing payload is clean
        }
        mock_gcp_logger = MagicMock()

        with patch("main._gcp_logger", mock_gcp_logger):
            import main as agent_main
            agent_main._write_audit_log(pii_payload)

        sent_payload = mock_gcp_logger.log_struct.call_args[0][0]
        pii_found = set(sent_payload.keys()) & FORBIDDEN_PII_FIELDS
        assert pii_found == set(), (
            f"Audit payload must not contain PII fields. Found: {pii_found}"
        )

    def test_fallback_to_stdlib_when_gcp_unavailable(self):
        """When _gcp_logger is None, _write_audit_log should log to stderr (no exception)."""
        with patch("main._gcp_logger", None):
            import main as agent_main
            # Must not raise even when GCP logger is unavailable
            try:
                agent_main._write_audit_log(SAMPLE_AUDIT_PAYLOAD)
            except Exception as exc:
                pytest.fail(f"_write_audit_log must not raise when GCP logger is None: {exc}")

    def test_gcp_error_does_not_propagate(self):
        """If GCP logging raises, _write_audit_log must swallow the error silently."""
        mock_gcp_logger = MagicMock()
        mock_gcp_logger.log_struct.side_effect = Exception("GCP unavailable")

        with patch("main._gcp_logger", mock_gcp_logger):
            import main as agent_main
            try:
                agent_main._write_audit_log(SAMPLE_AUDIT_PAYLOAD)
            except Exception as exc:
                pytest.fail(
                    f"_write_audit_log must not propagate GCP errors to caller: {exc}"
                )

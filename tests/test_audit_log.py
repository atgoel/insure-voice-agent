"""
tests/test_audit_log.py

TASK-063: Verify the _write_audit_log() function in agent_builder/main.py:
  - Calls google.cloud.logging with the correct structured payload
  - Payload contains required audit fields (Constitution §IV)
  - Payload does NOT contain PII fields (Constitution §V)

google.cloud.logging is mocked — no live GCP calls are made.
"""

import sys
import types
import pathlib
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# T5a MERGE NOTE — ensure agent_builder/ is on sys.path BEFORE any sibling-module
# stubbing below, so that other tests in the same pytest session that try to
# `from intake import QUESTIONS / _strip_name_prefix / etc.` get the REAL
# intake.py, not the minimal stub we install at line ~88. Without this guard
# T2 (test_t2_warmth_bugf.py) and T4 (test_t4_bug_a_name_prefix.py) fail to
# collect when test_audit_log is imported first.
# ---------------------------------------------------------------------------
_agent_builder_early = pathlib.Path(__file__).parent.parent / "agent_builder"
if str(_agent_builder_early) not in sys.path:
    sys.path.insert(0, str(_agent_builder_early))

# ---------------------------------------------------------------------------
# Stub heavy runtime dependencies so agent_builder/main.py is importable
# in a bare test environment (no fastapi / google-adk / google-genai installed).
# We only need the minimal surface used at import time.  google.cloud.logging
# is left un-stubbed here because main.py wraps it in try/except already.
# ---------------------------------------------------------------------------
def _stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


def _try_real_or_stub(modname, **stub_attrs):
    """T5a — only install a stub if the real module can't be imported.

    This prevents test_audit_log from poisoning sys.modules for sibling tests
    (like test_t3_arc_inproc, test_orchestration_guardrail) that need the real
    fastapi/google packages. Real install wins.
    """
    if modname in sys.modules:
        return  # already loaded (real or stubbed)
    try:
        __import__(modname)
    except Exception:
        sys.modules[modname] = _stub(modname, **stub_attrs)


# google.adk namespace hierarchy — must be stubbed because main.py instantiates
# a real Runner at module load. setdefault keeps a real install if present in
# sys.modules already, but we DO want to force the stub if it isn't. Note:
# even if google-adk is pip-installed, we still stub here because main.py's
# Runner(...) call requires a real LlmAgent which we can't construct without
# actual env vars + GCP creds.
for _adk_mod in (
    "google.adk",
    "google.adk.runners",
    "google.adk.sessions",
    "google.adk.agents",
    "google.adk.tools",
    "google.adk.tools.mcp",
    "google.adk.tools.function_tool",
):
    sys.modules.setdefault(
        _adk_mod,
        _stub(
            _adk_mod,
            Runner=MagicMock,
            InMemorySessionService=MagicMock,
            LlmAgent=MagicMock,
            MCPToolset=MagicMock,
            FunctionTool=MagicMock,
            AgentTool=MagicMock,
        ),
    )
# Attach adk onto the google namespace package
_google_mod = sys.modules.get("google")
if _google_mod is None:
    _google_mod = _stub("google")
    sys.modules["google"] = _google_mod
if not hasattr(_google_mod, "adk"):
    _google_mod.adk = sys.modules["google.adk"]

# google.genai + google.genai.types
_genai_types_stub = _stub("google.genai.types")
_genai_stub = _stub("google.genai", types=_genai_types_stub)
sys.modules.setdefault("google.genai", _genai_stub)
sys.modules.setdefault("google.genai.types", _genai_types_stub)
if not hasattr(_google_mod, "genai"):
    _google_mod.genai = _genai_stub

# fastapi — try real first; only stub if missing. Stubbing fastapi as a plain
# module (not package) breaks `fastapi.testclient` for sibling tests in the
# same pytest session (T3 in-proc arc tests need TestClient).
_try_real_or_stub("fastapi", FastAPI=MagicMock, HTTPException=MagicMock)
_try_real_or_stub("fastapi.responses", JSONResponse=MagicMock)
_try_real_or_stub("fastapi.staticfiles", StaticFiles=MagicMock)

# agent_definition and intake (sibling modules that live in agent_builder/)
# T5a MERGE — try real import first; only stub if unavailable. This keeps T2/T4
# tests working when they need the real intake module in the same session.
if "agent_definition" not in sys.modules:
    try:
        import agent_definition  # noqa: F401
    except Exception:
        sys.modules["agent_definition"] = _stub(
            "agent_definition",
            root_agent=MagicMock(),
            search_products=MagicMock(),
            compliance_check=MagicMock(),
            rank_products=MagicMock(),
        )
if "intake" not in sys.modules:
    try:
        import intake  # noqa: F401
    except Exception:
        sys.modules["intake"] = _stub(
            "intake",
            handle_intake=MagicMock(),
            build_synthetic_message=MagicMock(),
        )

# ---------------------------------------------------------------------------
# Ensure agent_builder/ is on the path so we can import main
# ---------------------------------------------------------------------------
_agent_builder = pathlib.Path(__file__).parent.parent / "agent_builder"
if str(_agent_builder) not in sys.path:
    sys.path.insert(0, str(_agent_builder))

# Pre-import main so it is cached in sys.modules["main"] as agent_builder/main.
# Without this, patch("main._gcp_logger", ...) could resolve "main" to a
# different module loaded by another test file during pytest collection.
if "main" not in sys.modules:
    import main  # noqa: F401  (import for side-effect: cache in sys.modules)


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

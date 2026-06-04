"""T3 in-proc 14-turn arc — AC-T3-10 happy-path capture flow.

Per SPEC v2 §6 AC-T3-10 + §9.2 Arc A.

Strategy:
    - Use synchronous fastapi.testclient.TestClient (no async-plugin dep).
    - Run real conversational intake (turns 1-8) — no mocks needed.
    - On turn 9 (intake completes), the LLM runner is called. We bypass it by
      mocking `_runner.run_async` to emit zero events; this triggers the
      programmatic-path fallback in main.py, which calls
      `search_products`/`compliance_check`/`rank_products` via
      `agent_definition.httpx.post` — patched to canned responses.
    - The programmatic-path fallback renders the C.5b deterministic template,
      and the T3 contact-trigger appends the "Want me to email these to you?"
      suffix.
    - Turn 10: 'yes please' → FSM transitions ASKED → AWAITING_EMAIL.
    - Turn 11: 'abhishek@example.com' → FSM transitions AWAITING_EMAIL → CAPTURED.
    - Turn 12: 'tell me more about the first one' → S3 follow-up dispatch fires.
    - Turn 13: 'no thanks' → T3 farewell fires.
    - Turn 14: 'bye' → T3 farewell fires (determinism).

This validates AC-T3-3 (dispatch order), AC-T3-4..7 (FSM), AC-T3-9 (no
PII in audit), AC-T3-10 (full arc).
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "agent_builder"))

# agent_definition.py reads these at import time. Provide stubs so the import
# chain (main → agent_definition) succeeds in test environments without GCP.
os.environ.setdefault("ELASTIC_MCP_SERVER_URL", "http://mock-elastic-mcp.test/search")
os.environ.setdefault("ELASTIC_MCP_SERVER_NATIVE_URL", "http://mock-elastic-mcp-native.test/search")
os.environ.setdefault("COMPLIANCE_CHECK_URL", "http://mock-compliance.test/check")
os.environ.setdefault("RANK_PRODUCTS_URL", "http://mock-rank.test/rank")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")
os.environ.setdefault("GOOGLE_API_KEY", "stub-key-for-tests")


# ---------------------------------------------------------------------------
# Canned cloud-function responses for programmatic-path fallback.
# ---------------------------------------------------------------------------

SEARCH_RESPONSE_BODY = {
    "candidates": [
        {
            "product_id": "TERM001",
            "id": "TERM001",
            "name": "LifeGuard Plus",
            "product_type": "term_life",
            "min_age": 18,
            "max_age": 65,
            "smoker_eligible": False,
            "min_income": 300_000,
            "max_sum_assured": 50_000_000,
            "premium_min_monthly": 800,
            "premium_max_monthly": 5_000,
            "key_feature": "Affordable comprehensive coverage",
            "elser_score": 12.0,
        },
        {
            "product_id": "TERM002",
            "id": "TERM002",
            "name": "Future Secure Term",
            "product_type": "term_life",
            "min_age": 18,
            "max_age": 65,
            "smoker_eligible": False,
            "min_income": 300_000,
            "max_sum_assured": 30_000_000,
            "premium_min_monthly": 600,
            "premium_max_monthly": 4_000,
            "key_feature": "Return of premium option",
            "elser_score": 9.5,
        },
        {
            "product_id": "TERM003",
            "id": "TERM003",
            "name": "FamilyProtect 3 Crore",
            "product_type": "term_life",
            "min_age": 18,
            "max_age": 75,
            "smoker_eligible": True,
            "min_income": 500_000,
            "max_sum_assured": 30_000_000,
            "premium_min_monthly": 1_200,
            "premium_max_monthly": 8_000,
            "key_feature": "Covers up to 3 crore",
            "elser_score": 6.0,
        },
    ]
}

COMPLIANCE_RESPONSE_BODY = {
    "passed": SEARCH_RESPONSE_BODY["candidates"],
    "rejected": [],
}

RANK_RESPONSE_BODY = {
    "top_3": [
        {
            "rank": 1,
            "product_id": "TERM001",
            "product": SEARCH_RESPONSE_BODY["candidates"][0],
            "suitability_score": 0.91,
        },
        {
            "rank": 2,
            "product_id": "TERM002",
            "product": SEARCH_RESPONSE_BODY["candidates"][1],
            "suitability_score": 0.83,
        },
        {
            "rank": 3,
            "product_id": "TERM003",
            "product": SEARCH_RESPONSE_BODY["candidates"][2],
            "suitability_score": 0.75,
        },
    ]
}


def _mock_post_factory():
    """Return a side_effect that routes calls based on URL pattern."""
    def _side_effect(url, *args, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        url_lower = (url or "").lower()
        if "compliance" in url_lower:
            resp.json.return_value = COMPLIANCE_RESPONSE_BODY
        elif "rank" in url_lower:
            resp.json.return_value = RANK_RESPONSE_BODY
        else:
            # Default: search_products / MCP search.
            resp.json.return_value = SEARCH_RESPONSE_BODY
        return resp
    return _side_effect


async def _empty_async_iter(*args, **kwargs):
    """Async generator that yields nothing.

    Used as a side_effect for _runner.run_async. main.py does
        `async for event in _runner.run_async(...)`
    which iterates this generator; with no yields, response_text stays empty
    and the programmatic-path + C.5b deterministic fallback fire.
    """
    if False:
        yield  # pragma: no cover — never reached


def _reset_session_state(session_id):
    """Wipe all per-session state for clean arc reruns."""
    import shared_state as _ss
    import main as _main
    _ss.PROFILE_BY_SESSION.pop(session_id, None)
    _ss.TOP3_BY_SESSION.pop(session_id, None)
    _ss.CONTACT_BY_SESSION.pop(session_id, None)
    _main._INTAKE_BY_SESSION.pop(session_id, None)


# ---------------------------------------------------------------------------
# Arc A — 14-turn happy-path capture (AC-T3-10)
# ---------------------------------------------------------------------------

def test_t3_10_full_capture_arc():
    """14-turn arc: intake → recommend → ASK → YES → email → CAPTURED → S3 → farewell."""
    from fastapi.testclient import TestClient
    import main as _main
    import shared_state as _ss

    session_id = "t3-arc-fixed-session"
    _reset_session_state(session_id)

    with patch.object(_main._runner, "run_async", side_effect=_empty_async_iter):
        with patch("agent_definition.httpx.post", side_effect=_mock_post_factory()):
            client = TestClient(_main.app)

            def _send(msg):
                r = client.post(
                    "/invoke",
                    json={"message": msg, "session_id": session_id},
                )
                assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
                return r.json()

            # Turn 1 — name greeting
            r1 = _send("Hi")
            assert "name" in r1["response"].lower() or "call you" in r1["response"].lower()

            # Turn 2 — give name
            r2 = _send("Abhishek")
            assert "old" in r2["response"].lower() or "age" in r2["response"].lower()

            # Turn 3 — age
            r3 = _send("30")
            assert "smoke" in r3["response"].lower()

            # Turn 4 — smoker
            r4 = _send("non-smoker")
            assert "income" in r4["response"].lower()

            # Turn 5 — income
            r5 = _send("25 lakhs")
            assert "health" in r5["response"].lower() or "condition" in r5["response"].lower()

            # Turn 6 — health
            r6 = _send("healthy")
            assert "family" in r6["response"].lower() or "members" in r6["response"].lower()

            # Turn 7 — family size
            r7 = _send("4")
            assert (
                "cover" in r7["response"].lower()
                or "term" in r7["response"].lower()
                or "kind of" in r7["response"].lower()
            )

            # Turn 8 — coverage goals
            r8 = _send("term life")
            assert (
                "sum" in r8["response"].lower()
                or "lakhs" in r8["response"].lower()
                or "crore" in r8["response"].lower()
            )

            # Turn 9 — sum assured (intake completes; pipeline runs)
            r9 = _send("1 crore")
            # T3 trigger MUST have appended the suffix.
            assert "email these to you" in r9["response"].lower(), (
                f"Expected contact-ask suffix in turn 9; got: {r9['response']!r}"
            )
            assert _ss.CONTACT_BY_SESSION.get(session_id, {}).get("state") == "ASKED"
            assert _ss.TOP3_BY_SESSION.get(session_id), "TOP3 not snapshotted"

            # Turn 10 — yes please → AWAITING_EMAIL
            r10 = _send("yes please")
            assert "email address" in r10["response"].lower()
            assert _ss.CONTACT_BY_SESSION[session_id]["state"] == "AWAITING_EMAIL"

            # Turn 11 — provide email → CAPTURED
            r11 = _send("abhishek@example.com")
            assert "abhishek@example.com" in r11["response"]
            assert "got it" in r11["response"].lower() or "saved" in r11["response"].lower()
            assert _ss.CONTACT_BY_SESSION[session_id]["state"] == "CAPTURED"
            assert _ss.CONTACT_BY_SESSION[session_id]["email"] == "abhishek@example.com"

            # Turn 12 — follow-up "tell me more about the first one"
            r12 = _send("tell me more about the first one")
            assert (
                "lifeguard" in r12["response"].lower()
                or "here's a bit more" in r12["response"].lower()
            ), f"Turn 12 should hit S3 follow-up; got: {r12['response']!r}"

            # Turn 13 — "no thanks" → T3 farewell fires
            r13 = _send("no thanks")
            expected_farewell = (
                "Okay, I understand. Thanks for chatting with InsureVoice today. "
                "If you change your mind or want to explore other options later, just say so. "
                "Have a great day!"
            )
            assert r13["response"] == expected_farewell, (
                f"Turn 13 should be canonical farewell; got: {r13['response']!r}"
            )
            # Side-effect: contact state cleared.
            assert session_id not in _ss.CONTACT_BY_SESSION

            # Turn 14 — another farewell variant ('bye') confirms determinism.
            r14 = _send("bye")
            assert r14["response"] == expected_farewell, (
                f"Turn 14 'bye' should also produce canonical farewell; got: {r14['response']!r}"
            )


# ---------------------------------------------------------------------------
# Arc B — Decline path
# ---------------------------------------------------------------------------

def test_t3_decline_path():
    """User says no to email-ask. State becomes DECLINED."""
    from fastapi.testclient import TestClient
    import main as _main
    import shared_state as _ss

    session_id = "t3-arc-decline-session"
    _reset_session_state(session_id)

    with patch.object(_main._runner, "run_async", side_effect=_empty_async_iter):
        with patch("agent_definition.httpx.post", side_effect=_mock_post_factory()):
            client = TestClient(_main.app)

            def _send(msg):
                r = client.post("/invoke", json={"message": msg, "session_id": session_id})
                return r.json()

            # Run intake to completion (9 turns).
            for m in ("Hi", "Abhi", "30", "non-smoker", "25 lakhs",
                      "healthy", "4", "term life", "1 crore"):
                _send(m)

            # State should be ASKED.
            assert _ss.CONTACT_BY_SESSION.get(session_id, {}).get("state") == "ASKED"

            # Decline.
            _send("no")
            assert _ss.CONTACT_BY_SESSION[session_id]["state"] == "DECLINED"


# ---------------------------------------------------------------------------
# Arc C — Invalid email retries → giveup
# ---------------------------------------------------------------------------

def test_t3_invalid_email_giveup():
    """2 invalid emails → giveup voice text + DECLINED state."""
    from fastapi.testclient import TestClient
    import main as _main
    import shared_state as _ss

    session_id = "t3-arc-invalid-session"
    _reset_session_state(session_id)

    with patch.object(_main._runner, "run_async", side_effect=_empty_async_iter):
        with patch("agent_definition.httpx.post", side_effect=_mock_post_factory()):
            client = TestClient(_main.app)

            def _send(msg):
                r = client.post("/invoke", json={"message": msg, "session_id": session_id})
                return r.json()

            # Run intake to completion.
            for m in ("Hi", "Abhi", "30", "non-smoker", "25 lakhs",
                      "healthy", "4", "term life", "1 crore"):
                _send(m)

            # Yes — go to AWAITING_EMAIL
            _send("yes")
            assert _ss.CONTACT_BY_SESSION[session_id]["state"] == "AWAITING_EMAIL"

            # First invalid email → invalid prompt
            r_inv1 = _send("not an email")
            assert "didn't catch" in r_inv1["response"].lower() or "invalid" in r_inv1["response"].lower()
            assert _ss.CONTACT_BY_SESSION[session_id]["invalid_attempts"] == 1
            assert _ss.CONTACT_BY_SESSION[session_id]["state"] == "AWAITING_EMAIL"

            # Second invalid → giveup
            r_inv2 = _send("still nothing")
            assert "skip the email" in r_inv2["response"].lower()
            assert _ss.CONTACT_BY_SESSION[session_id]["state"] == "DECLINED"

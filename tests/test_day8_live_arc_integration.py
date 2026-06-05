"""Day 8 live-arc integration test — replays the verbatim 11-turn conversation
that broke the demo on rev `00034-v8q` (2026-06-05 ~15:40 IST) against the
patched stable_v4 source code.

Live evidence:
    tasks/2026-06-05_hackathon_day8_tier_b_implementation/data/live_console_logs.txt
Spec under test:
    tasks/2026-06-05_hackathon_day8_tier_b_implementation/reports/Live_Bug_Fix_Spec_v2.md

Patches under test:
    - intake.py             (Implementer-1: B-LIVE-2 / B-LIVE-3 / B-LIVE-4 validators)
    - main.py               (Implementer-2: B-LIVE-1A INVOKE_IN  + B-LIVE-1B INVOKE_OUT_INTAKE logs)
    - frontend/simulation.js (Implementer-3: NOT exercised here — JS file, browser-only)

What this test enforces (acceptance criteria from the Tester brief):

A. Replays the verbatim 11-turn arc through `POST /invoke` with the SAME session_id.
   - Turns 6, 7 (`"perfectly in good health"`, `"in good health"`) MUST advance
     past health_status — i.e. the response must NOT be the health_status reprompt.
   - Turn 9 (`"It's for me and my family."`) MUST hit the new T3 family-shape
     branch — friendlier "How many people" reprompt, NOT the cold "as a number" one.
   - Turn 10 (`"three"`) MUST advance to coverage_goals — NOT replay the welcome.

B. Diagnostic logging fires:
   - Every turn produces an `INVOKE_IN session=<8-char> next_field=<...>` log line.
   - Every still-in-progress intake turn produces an `INVOKE_OUT_INTAKE session=<8-char>
     field=<...> reply_len=<int>` log line.
   - `next_field` is NEVER the literal string `?` (B-LIVE-6 regression guard:
     the phantom `from shared_state import _INTAKE_BY_SESSION` is gone).

C. Session-id stability:
   - Across all 11 turns, the server-returned session_id matches what was sent
     (no silent server-side session loss).

D. The validator unit fixes (already passing in tests/test_day8_live_validator_fixes.py)
   ALSO pass when exercised through the full /invoke handler.

E. External services are stubbed:
   - `_runner.run_async` is mocked to an empty async generator so the LLM never
     fires (matches the pattern in tests/test_t3_arc_inproc.py).
   - `agent_definition.httpx.post` is mocked to canned responses for any
     downstream Cloud Function calls. We stop after turn 10 confirms the
     server-side intake state correctly transitions into `coverage_goals`
     (i.e. intake is still in progress, no LLM/pipeline involvement yet).
"""

import logging
import os
import re
import sys
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Path + env setup (mirrors test_t3_arc_inproc.py / test_day8_live_validator_fixes.py)
# ---------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "agent_builder"))

# agent_definition.py reads these at import time. Stubbed so the import chain
# (main → agent_definition) succeeds in test environments without GCP.
os.environ.setdefault("ELASTIC_MCP_SERVER_URL", "http://mock-elastic-mcp.test/search")
os.environ.setdefault("ELASTIC_MCP_SERVER_NATIVE_URL", "http://mock-elastic-mcp-native.test/search")
os.environ.setdefault("COMPLIANCE_CHECK_URL", "http://mock-compliance.test/check")
os.environ.setdefault("RANK_PRODUCTS_URL", "http://mock-rank.test/rank")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")
os.environ.setdefault("GOOGLE_API_KEY", "stub-key-for-tests")
# Skip the LLM intent classifier path — purely an intake test.
os.environ.setdefault("USE_LLM_INTENT_CLASSIFIER", "false")


# ---------------------------------------------------------------------------
# Canned cloud-function responses (only used if the test ever crosses the
# intake-completion boundary; turns 1-10 should not).
# ---------------------------------------------------------------------------

_CANNED_SEARCH = {
    "candidates": [
        {
            "product_id": "TERM001", "id": "TERM001", "name": "LifeGuard Plus",
            "product_type": "term_life", "min_age": 18, "max_age": 65,
            "smoker_eligible": False, "min_income": 300_000,
            "max_sum_assured": 50_000_000, "premium_min_monthly": 800,
            "premium_max_monthly": 5_000, "key_feature": "Affordable comprehensive coverage",
            "elser_score": 12.0,
        }
    ]
}
_CANNED_COMPLIANCE = {"passed": _CANNED_SEARCH["candidates"], "rejected": []}
_CANNED_RANK = {
    "top_3": [
        {"rank": 1, "product_id": "TERM001",
         "product": _CANNED_SEARCH["candidates"][0],
         "suitability_score": 0.91}
    ]
}


def _mock_post_factory():
    def _side_effect(url, *args, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        url_lower = (url or "").lower()
        if "compliance" in url_lower:
            resp.json.return_value = _CANNED_COMPLIANCE
        elif "rank" in url_lower:
            resp.json.return_value = _CANNED_RANK
        else:
            resp.json.return_value = _CANNED_SEARCH
        return resp
    return _side_effect


async def _empty_async_iter(*args, **kwargs):
    """No-op async generator for _runner.run_async."""
    if False:
        yield  # pragma: no cover


def _reset_session_state(session_id: str) -> None:
    """Wipe per-session state for clean test reruns."""
    import shared_state as _ss
    import main as _main
    _ss.PROFILE_BY_SESSION.pop(session_id, None)
    _ss.TOP3_BY_SESSION.pop(session_id, None)
    _ss.CONTACT_BY_SESSION.pop(session_id, None)
    try:
        _ss.LAST_RENDERED_BY_SESSION.pop(session_id, None)
    except Exception:
        pass
    _main._INTAKE_BY_SESSION.pop(session_id, None)


# ---------------------------------------------------------------------------
# The verbatim 11-turn arc (from live_console_logs.txt + spec §G-LIVE-1 table)
# ---------------------------------------------------------------------------

# Welcome string anchor (intake.py:325). Used to detect welcome-replay.
_WELCOME_ANCHOR = "Welcome to InsureVoice"

# Health-status reprompt anchor (intake.py: validate_health_status return path).
_HEALTH_REPROMPT_ANCHOR = "are you in good health, or do you have a condition"

# Cold family_size reprompt anchor (the OLD wording the user got on turn 9).
_FAMILY_COLD_REPROMPT_ANCHOR = "Could you say the family size as a number"

# New B-LIVE-3 T3 friendly reprompt anchor.
_FAMILY_T3_REPROMPT_ANCHOR = "How many people"

# Expected `next_field` per turn — what the server-side state machine SHOULD
# be expecting WHEN THE TURN ARRIVES (i.e. the field the validator runs against).
# Pre-turn-1 the dict is empty so expecting_field is "(none)" but after the
# first response the field for the *next* turn is set.
LIVE_ARC = [
    # (turn_index, user_text, field_being_validated_this_turn,
    #  expected_next_field_after_turn, must_not_match_anchor)
    (1,  "Hi, good afternoon.",         "(none)",        "name",          None),
    (2,  "My name is Abhishek.",        "name",          "age",           None),
    (3,  "I am 25 years old.",          "age",           "smoker",        None),
    (4,  "I don't smoke.",              "smoker",        "income",        None),
    (5,  "25 lakhs",                    "income",        "health_status", None),
    # Turn 6 — was previously rejected. Patched validator must accept.
    (6,  "perfectly in good health",    "health_status", "family_size",   _HEALTH_REPROMPT_ANCHOR),
    # Turn 7 — second healthy phrasing. After the patch, the state machine has
    # ALREADY advanced past health_status on turn 6, so turn 7's text lands on
    # the family_size validator. "in good health" contains "family"? No. It
    # contains no number, no number-word, no family-shape word — so it falls
    # to the cold reprompt. That's a SIDE EFFECT of fixing turn 6 (state
    # advances earlier), not a regression. We assert turn 7 does NOT show
    # the OLD health_status reprompt — that's the real B-LIVE-2 invariant.
    (7,  "in good health",              "family_size",   "family_size",   _HEALTH_REPROMPT_ANCHOR),
    # Turn 8 — "I am healthy." — see the mirror logic above; on the patched
    # path the state machine is on family_size by turn 8, so this also lands
    # on the family validator (no number, no family-shape word → cold reprompt).
    # Assertion is again "no welcome, no health reprompt".
    (8,  "I am healthy.",               "family_size",   "family_size",   _HEALTH_REPROMPT_ANCHOR),
    # Turn 9 — family-shape language, no count. New T3 branch fires.
    (9,  "It's for me and my family.",  "family_size",   "family_size",   _WELCOME_ANCHOR),
    # Turn 10 — "three" — ADVANCES to coverage_goals. MUST NOT replay welcome.
    (10, "three",                       "family_size",   "coverage_goals", _WELCOME_ANCHOR),
    # Turn 11 — give coverage_goal. State should advance to sum_assured.
    (11, "term life for my family",     "coverage_goals", "sum_assured",  _WELCOME_ANCHOR),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RE_INVOKE_IN = re.compile(
    r"INVOKE_IN session=(?P<sid>\S{8}) "
    r"next_field=(?P<field>\S+) "
    r"profile_keys=\[(?P<keys>[^\]]*)\] "
    r"complete=(?P<complete>True|False) "
    r"msg="
)
_RE_INVOKE_OUT_INTAKE = re.compile(
    r"INVOKE_OUT_INTAKE session=(?P<sid>\S{8}) "
    r"field=(?P<field>\S+) "
    r"reply_len=(?P<reply_len>\d+) "
    r"reply_preview="
)


def _extract_invoke_logs(caplog_records):
    """Return parsed (in_lines, out_intake_lines) from caplog records.

    Only INFO-level lines from `main.py` are collected. Each list entry is
    the regex match.groupdict() with `_raw` added for debug.
    """
    ins, outs = [], []
    for rec in caplog_records:
        msg = rec.getMessage()
        m_in = _RE_INVOKE_IN.match(msg)
        if m_in:
            d = m_in.groupdict()
            d["_raw"] = msg
            ins.append(d)
            continue
        m_out = _RE_INVOKE_OUT_INTAKE.match(msg)
        if m_out:
            d = m_out.groupdict()
            d["_raw"] = msg
            outs.append(d)
    return ins, outs


# ---------------------------------------------------------------------------
# THE TEST
# ---------------------------------------------------------------------------

class TestDay8LiveArcIntegration:
    """11-turn replay of the live-test transcript that broke the demo.

    Single test; assertions accumulate across turns. We capture all outputs
    + log records first, THEN run the gates so failures show the full picture.
    """

    def test_live_arc_replay(self):
        from fastapi.testclient import TestClient
        import main as _main

        session_id = "day8-live-arc-int"
        _reset_session_state(session_id)

        # main.py:29 calls logging.basicConfig(..., force=True) at IMPORT time,
        # which removes any pre-existing root handlers (including pytest's
        # caplog handler attached during fixture setup). Result: caplog.records
        # is empty even though `INFO root INVOKE_IN ...` emits to stderr.
        # Workaround: attach our OWN list-collecting handler directly to the
        # root logger after main is imported.
        root_logger = logging.getLogger()
        captured_records = []

        class _ListHandler(logging.Handler):
            def emit(self, record):
                captured_records.append(record)

        list_handler = _ListHandler(level=logging.INFO)
        root_logger.addHandler(list_handler)
        old_level = root_logger.level
        root_logger.setLevel(logging.INFO)

        responses = []
        try:
            with patch.object(_main._runner, "run_async", side_effect=_empty_async_iter):
                with patch("agent_definition.httpx.post", side_effect=_mock_post_factory()):
                    client = TestClient(_main.app)

                    for turn_idx, user_text, _expecting_pre, _expecting_post, _ in LIVE_ARC:
                        r = client.post(
                            "/invoke",
                            json={"message": user_text, "session_id": session_id},
                        )
                        assert r.status_code == 200, (
                            f"Turn {turn_idx} HTTP {r.status_code}: {r.text}"
                        )
                        body = r.json()
                        responses.append((turn_idx, user_text, body))
        finally:
            root_logger.removeHandler(list_handler)
            root_logger.setLevel(old_level)

        ins, outs = _extract_invoke_logs(captured_records)

        # ----------------------------------------------------------------------
        # GATE C — session_id stability. Every response carries the same sid.
        # ----------------------------------------------------------------------
        for turn_idx, _utext, body in responses:
            assert "session_id" in body, f"Turn {turn_idx} response missing session_id"
            assert body["session_id"] == session_id, (
                f"Turn {turn_idx} session_id changed: sent={session_id} "
                f"got={body['session_id']}"
            )
            assert "response" in body and isinstance(body["response"], str)
            assert body["response"].strip(), f"Turn {turn_idx} response empty"

        # ----------------------------------------------------------------------
        # GATE B1 — every turn produced an INVOKE_IN log line.
        # ----------------------------------------------------------------------
        assert len(ins) == len(LIVE_ARC), (
            f"Expected {len(LIVE_ARC)} INVOKE_IN lines, got {len(ins)}.\n"
            f"Captured: {[i['_raw'] for i in ins]}"
        )

        # ----------------------------------------------------------------------
        # GATE B2 — INVOKE_IN.next_field is NEVER the literal '?' string.
        # B-LIVE-6 regression guard.
        # ----------------------------------------------------------------------
        for i, parsed in enumerate(ins, start=1):
            assert parsed["field"] != "?", (
                f"Turn {i} INVOKE_IN next_field='?' — B-LIVE-6 phantom-import "
                f"regression. Raw: {parsed['_raw']}"
            )
            # Session-id prefix must match (8-char prefix of `session_id`).
            assert parsed["sid"] == session_id[:8], (
                f"Turn {i} INVOKE_IN sid prefix mismatch: "
                f"want {session_id[:8]!r}, got {parsed['sid']!r}"
            )

        # ----------------------------------------------------------------------
        # GATE B3 — every still-in-progress intake turn produced an
        # INVOKE_OUT_INTAKE line. Turns 1-10 are intake-in-progress
        # (turn 11 may complete intake, depending on patched flow; we relax
        # the check to "at least one OUT_INTAKE per intake turn we observed").
        # ----------------------------------------------------------------------
        # Build the set of turns where the response was an intake question
        # (i.e. NOT a recommendation). All 11 of our turns should be intake;
        # the recommendation pipeline is mocked to no-op anyway.
        # We require AT LEAST 10 OUT_INTAKE lines (turn 11 may slip through
        # depending on whether `coverage_goals` validates cleanly and intake
        # finishes — in which case the early-return INTAKE log doesn't fire).
        assert len(outs) >= 10, (
            f"Expected >=10 INVOKE_OUT_INTAKE lines (one per intake turn), "
            f"got {len(outs)}. This means B-LIVE-1B early-return logging is "
            f"silent for some turns. Captured: {[o['_raw'] for o in outs]}"
        )
        for parsed in outs:
            assert int(parsed["reply_len"]) > 0, (
                f"INVOKE_OUT_INTAKE has reply_len=0 — empty agent_says. "
                f"Raw: {parsed['_raw']}"
            )
            assert parsed["sid"] == session_id[:8]

        # ----------------------------------------------------------------------
        # GATE A — anchor checks per turn (the bugs the patches claim to fix).
        # ----------------------------------------------------------------------

        responses_by_turn = {t: body for (t, _u, body) in responses}

        # Turn 1: response must contain the welcome string (this IS the welcome).
        r1 = responses_by_turn[1]["response"]
        assert _WELCOME_ANCHOR in r1, (
            f"Turn 1 should be the welcome message; got: {r1!r}"
        )

        # Turn 2: name accepted; agent should ask for age and address user as "Abhishek"
        # (NOT "Abhishek." — B-LIVE-4).
        r2 = responses_by_turn[2]["response"]
        assert "Abhishek" in r2, f"Turn 2 should mention the name 'Abhishek'; got: {r2!r}"
        assert "Abhishek." not in r2, (
            f"Turn 2 still echoing trailing period in name (B-LIVE-4 regression): {r2!r}"
        )
        # And it should be asking the next intake question (age).
        assert "old" in r2.lower() or "age" in r2.lower(), (
            f"Turn 2 should advance to age question; got: {r2!r}"
        )

        # Turn 6: "perfectly in good health" — patched validator must accept.
        # The response must NOT be the OLD health_status reprompt and must NOT
        # be the welcome.
        r6 = responses_by_turn[6]["response"]
        assert _HEALTH_REPROMPT_ANCHOR not in r6.lower(), (
            f"B-LIVE-2 regression: turn 6 'perfectly in good health' rejected "
            f"with old reprompt. Got: {r6!r}"
        )
        assert _WELCOME_ANCHOR not in r6, (
            f"Turn 6 unexpectedly replayed welcome: {r6!r}"
        )
        # And it SHOULD be asking the family question (the next field).
        assert "family" in r6.lower() or "spouse" in r6.lower() or "kids" in r6.lower(), (
            f"Turn 6 should advance to family_size question; got: {r6!r}"
        )

        # Turn 7: "in good health" — by now state already advanced to family_size
        # (turn 6 fix moves the cursor on). Whatever the response, it MUST NOT
        # be the OLD health_status reprompt and MUST NOT be the welcome.
        r7 = responses_by_turn[7]["response"]
        assert _HEALTH_REPROMPT_ANCHOR not in r7.lower(), (
            f"Turn 7 still hit health_status reprompt — implies validator state "
            f"didn't advance on turn 6. Got: {r7!r}"
        )
        assert _WELCOME_ANCHOR not in r7, f"Turn 7 unexpectedly replayed welcome: {r7!r}"

        # Turn 9: "It's for me and my family." — B-LIVE-3 T3 branch.
        # New friendlier "How many people" reprompt expected.
        r9 = responses_by_turn[9]["response"]
        assert _FAMILY_T3_REPROMPT_ANCHOR in r9, (
            f"B-LIVE-3 T3 branch did not fire on 'It's for me and my family.'. "
            f"Expected reply containing 'How many people', got: {r9!r}"
        )
        assert _WELCOME_ANCHOR not in r9, f"Turn 9 replayed welcome: {r9!r}"

        # Turn 10: "three" — CRITICAL. Must NOT replay welcome. Must advance.
        r10 = responses_by_turn[10]["response"]
        assert _WELCOME_ANCHOR not in r10, (
            f"B-LIVE-1 CRITICAL REGRESSION: turn 10 'three' replayed welcome. "
            f"Got: {r10!r}"
        )
        # Should now ask the coverage_goals question.
        # coverage_goals question contains 'protection' and 'mind' (template).
        assert (
            "protection" in r10.lower()
            or "term life" in r10.lower()
            or "savings" in r10.lower()
            or "kind of" in r10.lower()
        ), f"Turn 10 should advance to coverage_goals; got: {r10!r}"

        # Server-side state check: after turn 10, _INTAKE_BY_SESSION must show
        # family_size in profile and expecting_field=coverage_goals.
        intake_state_after_10 = _main._INTAKE_BY_SESSION.get(session_id, {})
        profile_after_10 = intake_state_after_10.get("profile", {})
        assert "family_size" in profile_after_10, (
            f"After turn 10, profile should contain family_size. "
            f"Got profile={profile_after_10}"
        )
        assert profile_after_10.get("family_size") == 3, (
            f"After turn 10, family_size should be 3 (from 'three'). "
            f"Got: {profile_after_10.get('family_size')}"
        )
        # NOTE: `intake_state_after_10` is actually read AFTER the full 11-turn
        # loop finishes (by which time turn 11 has already advanced state to
        # sum_assured). The OUT-log assertion below uses the per-turn captured
        # field which IS the correct point-in-time snapshot for turn 10.

        # Same check via INVOKE_OUT_INTAKE log signature for turn 10.
        # The OUT log captures the field that was JUST validated (the field
        # name in `intake_state.expecting_field` AT THE TIME of the OUT log,
        # which is set to `next_field` already by handle_intake() — see
        # intake.py:427). So the OUT log for turn 10 should show
        # field=coverage_goals.
        # We use the LAST OUT log (turn 10 is the last intake turn that didn't
        # complete intake; turn 11 might complete or might also be intake).
        assert any(o["field"] == "coverage_goals" for o in outs), (
            f"No INVOKE_OUT_INTAKE line shows field=coverage_goals — server-side "
            f"state machine never reached coverage_goals. Captured fields: "
            f"{[o['field'] for o in outs]}"
        )

        # Turn 11: "term life for my family" — coverage_goals validates,
        # state advances to sum_assured. Must NOT be welcome.
        r11 = responses_by_turn[11]["response"]
        assert _WELCOME_ANCHOR not in r11, f"Turn 11 replayed welcome: {r11!r}"

        intake_state_after_11 = _main._INTAKE_BY_SESSION.get(session_id, {})
        profile_after_11 = intake_state_after_11.get("profile", {})
        assert "coverage_goals" in profile_after_11, (
            f"After turn 11, profile should contain coverage_goals. "
            f"Got profile={profile_after_11}"
        )
        # Either still in intake expecting sum_assured, OR intake completed
        # (extremely unlikely without sum_assured turn). Accept both.
        if not intake_state_after_11.get("complete"):
            assert intake_state_after_11.get("expecting_field") == "sum_assured", (
                f"After turn 11, expecting_field should be 'sum_assured'. "
                f"Got: {intake_state_after_11.get('expecting_field')!r}"
            )

    # ----------------------------------------------------------------------
    # Smaller, focused gate tests for D — through-/invoke versions of the
    # validator unit tests. These give clearer failure messages when the
    # patches break specifically at the HTTP layer (vs. at the function
    # layer where test_day8_live_validator_fixes.py already covers them).
    # ----------------------------------------------------------------------

    def test_b_live_4_name_trailing_period_through_invoke(self):
        """B-LIVE-4 over the wire: 'My name is Abhishek.' → 'Abhishek' (no period)."""
        from fastapi.testclient import TestClient
        import main as _main

        session_id = "day8-int-bl4"
        _reset_session_state(session_id)

        with patch.object(_main._runner, "run_async", side_effect=_empty_async_iter):
            with patch("agent_definition.httpx.post", side_effect=_mock_post_factory()):
                client = TestClient(_main.app)

                # Turn 1 — greeting
                client.post("/invoke", json={"message": "hi", "session_id": session_id})
                # Turn 2 — name with trailing period
                r = client.post(
                    "/invoke",
                    json={"message": "My name is Abhishek.", "session_id": session_id},
                )
                body = r.json()

        # Server should have stored "Abhishek" (no trailing period).
        profile = _main._INTAKE_BY_SESSION.get(session_id, {}).get("profile", {})
        assert profile.get("name") == "Abhishek", (
            f"B-LIVE-4: name stored as {profile.get('name')!r}, expected 'Abhishek'"
        )
        # And the agent's response must not echo "Abhishek." with the period.
        assert "Abhishek." not in body["response"], (
            f"B-LIVE-4: agent echoed trailing period in: {body['response']!r}"
        )

    def test_b_live_2_in_good_health_through_invoke(self):
        """B-LIVE-2 over the wire: 'in good health' must advance past health_status."""
        from fastapi.testclient import TestClient
        import main as _main

        session_id = "day8-int-bl2"
        _reset_session_state(session_id)

        with patch.object(_main._runner, "run_async", side_effect=_empty_async_iter):
            with patch("agent_definition.httpx.post", side_effect=_mock_post_factory()):
                client = TestClient(_main.app)

                # Walk to health_status step.
                for msg in ("hi", "Abhishek", "30", "non-smoker", "25 lakhs"):
                    client.post("/invoke", json={"message": msg, "session_id": session_id})

                # Now expecting health_status. Send the previously-rejected phrasing.
                r = client.post(
                    "/invoke",
                    json={"message": "in good health", "session_id": session_id},
                )
                body = r.json()

        # The patched validator must accept this.
        profile = _main._INTAKE_BY_SESSION.get(session_id, {}).get("profile", {})
        assert profile.get("health_status") == "healthy", (
            f"B-LIVE-2: 'in good health' didn't validate. "
            f"Stored profile={profile}"
        )
        # Response should be the family_size question (not the health reprompt).
        assert _HEALTH_REPROMPT_ANCHOR not in body["response"].lower(), (
            f"B-LIVE-2: 'in good health' still hit reprompt: {body['response']!r}"
        )

    def test_b_live_3_family_shape_through_invoke(self):
        """B-LIVE-3 over the wire: 'It's for me and my family.' triggers T3 reprompt."""
        from fastapi.testclient import TestClient
        import main as _main

        session_id = "day8-int-bl3"
        _reset_session_state(session_id)

        with patch.object(_main._runner, "run_async", side_effect=_empty_async_iter):
            with patch("agent_definition.httpx.post", side_effect=_mock_post_factory()):
                client = TestClient(_main.app)

                # Walk to family_size step.
                for msg in ("hi", "Abhishek", "30", "non-smoker", "25 lakhs", "healthy"):
                    client.post("/invoke", json={"message": msg, "session_id": session_id})

                # Now expecting family_size.
                r = client.post(
                    "/invoke",
                    json={"message": "It's for me and my family.", "session_id": session_id},
                )
                body = r.json()

        # Server-side: family_size must NOT be in profile (T3 rejects without count).
        profile = _main._INTAKE_BY_SESSION.get(session_id, {}).get("profile", {})
        assert "family_size" not in profile, (
            f"B-LIVE-3: family_size should NOT be set on shape-only input. "
            f"Got profile={profile}"
        )
        # Response: friendlier T3 reprompt.
        assert _FAMILY_T3_REPROMPT_ANCHOR in body["response"], (
            f"B-LIVE-3: T3 'How many people' anchor missing. Got: {body['response']!r}"
        )
        # And NOT the cold reprompt.
        assert _FAMILY_COLD_REPROMPT_ANCHOR not in body["response"], (
            f"B-LIVE-3: still using cold reprompt. Got: {body['response']!r}"
        )

    def test_b_live_1_no_welcome_replay_after_three(self):
        """B-LIVE-1 critical: 'three' must NOT replay welcome after turn N>=10."""
        from fastapi.testclient import TestClient
        import main as _main

        session_id = "day8-int-bl1"
        _reset_session_state(session_id)

        with patch.object(_main._runner, "run_async", side_effect=_empty_async_iter):
            with patch("agent_definition.httpx.post", side_effect=_mock_post_factory()):
                client = TestClient(_main.app)

                # Walk through all 9 prior turns from the live arc.
                walk = [
                    "Hi, good afternoon.",
                    "My name is Abhishek.",
                    "I am 25 years old.",
                    "I don't smoke.",
                    "25 lakhs",
                    "perfectly in good health",
                    "in good health",
                    "I am healthy.",
                    "It's for me and my family.",
                ]
                for msg in walk:
                    client.post("/invoke", json={"message": msg, "session_id": session_id})

                # THE turn that broke the demo:
                r = client.post(
                    "/invoke",
                    json={"message": "three", "session_id": session_id},
                )
                body = r.json()

        # 1. session_id stable.
        assert body["session_id"] == session_id

        # 2. Response is NOT the welcome.
        assert _WELCOME_ANCHOR not in body["response"], (
            f"B-LIVE-1 CRITICAL: 'three' replayed welcome. Got: {body['response']!r}"
        )

        # 3. Server-side state shows family_size=3 captured AND expecting_field
        #    moved to coverage_goals.
        intake_state = _main._INTAKE_BY_SESSION.get(session_id, {})
        profile = intake_state.get("profile", {})
        assert profile.get("family_size") == 3, (
            f"B-LIVE-1: 'three' didn't land family_size=3. profile={profile}"
        )
        assert intake_state.get("expecting_field") == "coverage_goals", (
            f"B-LIVE-1: expecting_field after 'three' = "
            f"{intake_state.get('expecting_field')!r}, expected 'coverage_goals'"
        )

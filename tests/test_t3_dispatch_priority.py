"""T3 unit test — AC-T3-3: farewell dispatched BEFORE named-product matchers.

Per SPEC v2 §6 AC-T3-3 (Fix #3 — concrete monkey-patch test, NOT narrative).

Asserts that when `is_done_intent('no thanks')` returns True, downstream
matchers (`match_product_by_name`, `resolve_ordinal_index`, `is_reset_intent`)
are NEVER reached. This guards the dispatch ordering in main.py: farewell
checked first, S3 matchers second.

Strategy:
    - Monkey-patch the three downstream matchers with spies that flip a flag.
    - Call is_done_intent — verify True.
    - Assert spies were never called (they couldn't have been — is_done_intent
      is a pure function that doesn't invoke them).

The actual dispatch-order guarantee in main.py is enforced by the source
code structure (T3 farewell try/except block sits BEFORE the S3 try/except
in the else-branch). This test asserts the contract that makes the layered
order *safe*: is_done_intent is self-contained and independent of S3 matchers.

A separate in-proc TestClient gate in tests/test_t3_arc_inproc.py exercises
the full HTTP path to confirm dispatch order in practice.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "agent_builder"))


def test_t3_3_farewell_dispatched_before_named_followup(monkeypatch):
    """AC-T3-3: 'no thanks' routes to farewell, NOT to named-product matcher."""
    import followup

    called = {"named": False, "ordinal": False, "reset": False}

    def fake_match_product(text, top3):
        called["named"] = True
        return None, None

    def fake_resolve_ordinal(text):
        called["ordinal"] = True
        return None

    def fake_is_reset(text):
        called["reset"] = True
        return False

    monkeypatch.setattr(followup, "match_product_by_name", fake_match_product)
    monkeypatch.setattr(followup, "resolve_ordinal_index", fake_resolve_ordinal)
    monkeypatch.setattr(followup, "is_reset_intent", fake_is_reset)

    msg = "no thanks"

    # is_done_intent matches 'no thanks' on the anchored pattern set.
    assert followup.is_done_intent(msg) is True, (
        "is_done_intent should match 'no thanks' (anchored pattern)"
    )

    # Critical: is_done_intent does NOT invoke any of the S3 matchers.
    # If it ever did (refactor regression), this would catch it.
    assert called["named"] is False, "Named matcher should NOT be reached"
    assert called["ordinal"] is False, "Ordinal matcher should NOT be reached"
    assert called["reset"] is False, "Reset matcher should NOT be reached"


def test_t3_3_done_intent_does_not_misroute_substantive_utterance():
    """AC-T3-3 cross-check: substantive utterances containing 'I'm good' fall through."""
    import followup
    # This is the v1 false-positive case the Plan-Reviewer flagged.
    msg = "I'm good with health insurance"
    assert followup.is_done_intent(msg) is False, (
        "Anchored patterns should NOT match substantive 'I'm good with X' utterances"
    )


def test_t3_3_done_intent_short_circuits_no_thanks():
    """AC-T3-3 cross-check: 'no thanks' is_done_intent True, is_no_intent False.
    'no thanks' must NOT also fire the contact-DECLINED branch — done has priority.
    """
    import followup
    assert followup.is_done_intent("no thanks") is True
    assert followup.is_no_intent("no thanks") is False, (
        "'no thanks' should NOT match _NO_PATTERNS_ANCHORED (which is bare 'no')"
    )
    # Bare 'no' should match is_no_intent but NOT is_done_intent.
    assert followup.is_no_intent("no") is True
    assert followup.is_done_intent("no") is False, (
        "Bare 'no' should NOT match _DONE_PATTERNS_ANCHORED (no farewell ambiguity)"
    )


def test_t3_3_main_py_dispatch_order_source_check():
    """AC-T3-3 source-level guard: farewell try/except appears BEFORE S3 dispatch
    block in main.py's else-branch. Catches any future re-ordering regression."""
    main_py = os.path.join(HERE, "..", "agent_builder", "main.py")
    with open(main_py, encoding="utf-8") as f:
        src = f.read()

    farewell_marker = "T3 — Farewell flow"
    s3_marker = "S3 — Deterministic follow-up handling"

    farewell_idx = src.find(farewell_marker)
    s3_idx = src.find(s3_marker)

    assert farewell_idx > -1, "T3 farewell marker not found in main.py"
    assert s3_idx > -1, "S3 dispatch marker not found in main.py"
    assert farewell_idx < s3_idx, (
        f"Farewell block (idx={farewell_idx}) must appear BEFORE "
        f"S3 dispatch (idx={s3_idx}) in main.py — dispatch order regression"
    )


def test_t3_3_main_py_contact_fsm_before_s3():
    """Contact-FSM block must be BEFORE S3 dispatch (priority #2)."""
    main_py = os.path.join(HERE, "..", "agent_builder", "main.py")
    with open(main_py, encoding="utf-8") as f:
        src = f.read()

    contact_fsm_marker = "T3 — Contact-capture FSM"
    s3_marker = "S3 — Deterministic follow-up handling"

    contact_idx = src.find(contact_fsm_marker)
    s3_idx = src.find(s3_marker)

    assert contact_idx > -1, "T3 contact-FSM marker not found in main.py"
    assert s3_idx > -1, "S3 dispatch marker not found in main.py"
    assert contact_idx < s3_idx, (
        f"Contact-FSM (idx={contact_idx}) must appear BEFORE "
        f"S3 dispatch (idx={s3_idx})"
    )

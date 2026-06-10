"""T3 unit tests — done-intent (farewell) detection.

Per SPEC v2 §6 AC-T3-1 + AC-T3-2. Anchored patterns mean substantive
utterances containing 'I'm good with X' fall through (intentional false
negative trade-off per §4.2 / §10 R1).
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "agent_builder"))

from followup import is_done_intent, farewell_voice_text  # noqa: E402


# ---------------------------------------------------------------------------
# AC-T3-1 — 12 positive cases per SPEC v2 §6.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("msg", [
    "no thanks",
    "no thank you",
    "no, thank you",  # comma-stripping per Fix #4
    "not now",
    "no not right now",
    "we're done",
    "I'm done",
    "I'm good",
    "I'm all set",
    "that's all",
    "nothing else",
    "okay bye",
])
def test_t3_1_positive_done_intent(msg):
    """AC-T3-1 positives: each utterance routes to farewell."""
    assert is_done_intent(msg) is True, f"Expected is_done_intent({msg!r}) == True"


# ---------------------------------------------------------------------------
# AC-T3-1 — 10 negative cases per SPEC v2 §6 (includes 2 Plan-Reviewer
# counter-examples that v1's substring match would have wrongly accepted).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("msg", [
    "no I want term life",
    "I'm good with health insurance",
    "that's all I know about diabetes",
    "I want to buy term",
    "can I bye one of these",
    "no I asked for ULIP",
    "I don't know",
    "",
    # Plan-Reviewer counter-examples — anchored regex MUST reject these.
    "Yes I'm fine with the recommendation, I'm good let's move on now please",
    "I'm good with the term life option but show me health",
])
def test_t3_1_negative_done_intent(msg):
    """AC-T3-1 negatives: substantive utterances do NOT match farewell."""
    assert is_done_intent(msg) is False, (
        f"Expected is_done_intent({msg!r}) == False (anchored false-positive)"
    )


# ---------------------------------------------------------------------------
# AC-T3-2 — voice text deterministic.
# ---------------------------------------------------------------------------

def test_t3_2_voice_text_deterministic():
    """100 calls → 100 byte-identical returns."""
    first = farewell_voice_text()
    for _ in range(100):
        assert farewell_voice_text() == first


def test_t3_2_voice_text_canonical_string():
    """The canonical farewell matches the exact SPEC v2 §4.3 text."""
    expected = (
        "Okay, I understand. Thanks for chatting with InsureVoice today. "
        "If you change your mind or want to explore other options later, just say so. "
        "Have a great day!"
    )
    assert farewell_voice_text() == expected


# ---------------------------------------------------------------------------
# Misc edge cases — extra defensive coverage.
# ---------------------------------------------------------------------------

def test_t3_done_handles_none():
    assert is_done_intent(None) is False


def test_t3_done_handles_punctuation():
    """Trailing punctuation gets stripped by the helper."""
    assert is_done_intent("no thanks.") is True
    assert is_done_intent("we're done!") is True
    assert is_done_intent("bye?") is True


def test_t3_done_handles_internal_whitespace():
    """Multiple spaces get collapsed."""
    assert is_done_intent("no   thanks") is True
    assert is_done_intent("  I'm   done  ") is True


def test_t3_done_case_insensitive():
    assert is_done_intent("NO THANKS") is True
    assert is_done_intent("BYE") is True
    assert is_done_intent("That's All") is True

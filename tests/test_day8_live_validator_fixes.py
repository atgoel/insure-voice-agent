"""Day 8 live-test validator fixes — unit tests for B-LIVE-2 / B-LIVE-3 / B-LIVE-4.

Source spec: tasks/2026-06-05_hackathon_day8_tier_b_implementation/reports/Live_Bug_Fix_Spec_v2.md
Live evidence: tasks/2026-06-05_hackathon_day8_tier_b_implementation/data/live_console_logs.txt

Bugs fixed in intake.py:
  - B-LIVE-4 (MED): validate_name strips trailing punctuation/whitespace AFTER
    prefix-stripping ("Abhishek." -> "Abhishek").
  - B-LIVE-2 (HIGH): validate_health_status accepts contextual healthy phrases
    like "in good health", "perfectly fine". Standalone "perfectly" rejected
    on purpose to avoid matching "perfectly comfortable [with my diabetes]".
  - B-LIVE-3 (HIGH): validate_family_size adds a NEW T3 family-shape branch.
    When the user says "for me and my family" without a count, the validator
    acknowledges the family signal and re-prompts specifically for the number.

Tests live in their own file (not extending test_t2_warmth_bugf.py or
test_t4_bug_a_name_prefix.py) so a Day 9 revert is trivial.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent_builder"))
from intake import (  # noqa: E402
    _FAMILY_SHAPE_WORDS,
    validate_family_size,
    validate_health_status,
    validate_name,
)


# ---------------------------------------------------------------------------
# B-LIVE-4 — validate_name strips trailing punctuation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("input_value,expected_name", [
    ("Abhishek", "Abhishek"),                       # regression — bare name unchanged
    ("Abhishek.", "Abhishek"),                      # NEW — trailing period stripped
    ("My name is Abhishek.", "Abhishek"),           # NEW — prefix + trailing combined
    ("Abhishek!", "Abhishek"),                      # NEW — trailing exclam stripped
    ("Abhishek?", "Abhishek"),                      # NEW — trailing question stripped
    ("Abhishek,", "Abhishek"),                      # NEW — trailing comma stripped
    ("Mary J.", "Mary J"),                          # NEW — trailing dot stripped
    ("Mary J. Smith", "Mary J. Smith"),             # regression — internal dot preserved
    ("My name is Abhishek Sharma.", "Abhishek Sharma"),  # multi-token + trailing
])
def test_b_live_4_validate_name_strips_trailing_punct(input_value, expected_name):
    """B-LIVE-4: trailing punctuation is stripped, internal punctuation preserved."""
    ok, value = validate_name(input_value)
    assert ok is True, (
        f"validate_name({input_value!r}) returned ok=False, value={value!r}"
    )
    assert value == expected_name, (
        f"validate_name({input_value!r}) expected {expected_name!r}, got {value!r}"
    )


def test_b_live_4_only_punctuation_rejected():
    """Post-strip empty input should fail validation cleanly (no crash)."""
    ok, value = validate_name(".")
    assert ok is False
    assert isinstance(value, str)


def test_b_live_4_apostrophe_internal_preserved():
    """Internal apostrophes (e.g. O'Brien) must not be stripped."""
    ok, value = validate_name("O'Brien")
    assert ok is True
    assert value == "O'Brien"


# ---------------------------------------------------------------------------
# B-LIVE-2 — validate_health_status accepts contextual healthy phrases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("input_value,expected_label", [
    # NEW Day 8 live-test phrases
    ("perfectly in good health", "healthy"),
    ("in good health", "healthy"),
    ("good health, no problems", "healthy"),
    ("perfectly fine", "healthy"),
    ("completely fine", "healthy"),
    ("no conditions", "healthy"),
    ("no issues", "healthy"),
    ("no problems", "healthy"),
    # Regressions — existing healthy phrases must still pass
    ("I am healthy.", "healthy"),
    ("healthy", "healthy"),
    ("I'm fit", "healthy"),
    ("nothing", "healthy"),
    ("all good", "healthy"),
    ("none", "healthy"),
])
def test_b_live_2_health_status_healthy_branch(input_value, expected_label):
    """B-LIVE-2: contextual healthy phrases route to the healthy branch."""
    ok, value = validate_health_status(input_value)
    assert ok is True, (
        f"validate_health_status({input_value!r}) returned ok=False, value={value!r}"
    )
    assert value == expected_label, (
        f"validate_health_status({input_value!r}) expected label={expected_label!r}, got {value!r}"
    )


@pytest.mark.parametrize("input_value", [
    "I have diabetes",
    "I have blood pressure",
    "I'm fine but have diabetes",                       # pre takes priority over fine
    "I have a condition",
    "diabetic",
    "perfectly comfortable with my diabetes",           # pre wins; "perfectly" alone NOT a healthy match
])
def test_b_live_2_health_status_pre_existing_branch(input_value):
    """B-LIVE-2: pre-existing keywords take priority — even with healthy adjectives present.

    Regression check mandated by audit: "I'm fine but have diabetes" and
    "perfectly comfortable with my diabetes" must route to pre_existing.
    """
    ok, value = validate_health_status(input_value)
    assert ok is True, (
        f"validate_health_status({input_value!r}) returned ok=False, value={value!r}"
    )
    assert value == "pre_existing", (
        f"validate_health_status({input_value!r}) expected 'pre_existing', got {value!r}"
    )


def test_b_live_2_health_status_unparsed_returns_error():
    """Garbage input must still return a clear validator error string."""
    ok, value = validate_health_status("abcdef")
    assert ok is False
    assert isinstance(value, str)
    assert "didn't catch" in value or "Sorry" in value


# ---------------------------------------------------------------------------
# B-LIVE-3 — validate_family_size adds family-shape T3 branch
# ---------------------------------------------------------------------------

def test_b_live_3_family_shape_constant_present():
    """Sanity: _FAMILY_SHAPE_WORDS tuple is exposed and contains expected anchors."""
    assert isinstance(_FAMILY_SHAPE_WORDS, tuple)
    for required in ("family", "spouse", "wife", "husband", "kids", "children",
                     "parents", "me and my"):
        assert required in _FAMILY_SHAPE_WORDS, (
            f"_FAMILY_SHAPE_WORDS missing required anchor {required!r}"
        )


@pytest.mark.parametrize("input_value,expected_count", [
    # T1 — number-word path regressions
    ("three", 3),
    ("four", 4),
    ("two", 2),
    ("just me", 1),
    ("alone", 1),
    ("myself", 1),
    # T2 — digit path regressions
    ("4", 4),
    ("family of 5", 5),
    # Mixed — number-word wins over family-shape (per spec acceptance table)
    ("my wife and two kids", 2),
])
def test_b_live_3_family_size_count_paths(input_value, expected_count):
    """B-LIVE-3: number-word path (T1) and digit path (T2) regression coverage."""
    ok, value = validate_family_size(input_value)
    assert ok is True, (
        f"validate_family_size({input_value!r}) returned ok=False, value={value!r}"
    )
    assert value == expected_count, (
        f"validate_family_size({input_value!r}) expected {expected_count}, got {value!r}"
    )


@pytest.mark.parametrize("input_value", [
    "It's for me and my family.",                       # live-test repro
    "for me and my family",
    "my wife and kids",                                 # family-shape, no count
    "I want to cover my spouse",
    "my parents",
    "wife and children",
])
def test_b_live_3_family_shape_without_count_reprompts(input_value):
    """B-LIVE-3 T3: family-shape language without a count returns a friendly count-asking reprompt."""
    ok, value = validate_family_size(input_value)
    assert ok is False, (
        f"validate_family_size({input_value!r}) expected ok=False, got ok=True value={value!r}"
    )
    assert isinstance(value, str)
    # New reprompt acknowledges the family signal AND asks for a number.
    assert "How many people" in value, (
        f"Reprompt for {input_value!r} missing 'How many people' phrasing: {value!r}"
    )


def test_b_live_3_family_size_range_regression():
    """Range check still rejects sizes outside 1-10."""
    ok, value = validate_family_size("15")
    assert ok is False
    assert "between 1 and 10" in value


def test_b_live_3_family_size_garbage_falls_through_to_default_reprompt():
    """Inputs with no number AND no family-shape word fall back to the cold reprompt."""
    ok, value = validate_family_size("asdf")
    assert ok is False
    assert isinstance(value, str)
    # Should NOT include the new family-shape acknowledgement.
    assert "How many people" not in value
    assert "as a number" in value

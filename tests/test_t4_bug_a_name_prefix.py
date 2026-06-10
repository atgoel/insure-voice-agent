"""T4 unit tests — Bug A: validate_name strips conversational prefixes."""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent_builder"))
from intake import _strip_name_prefix, validate_name


@pytest.mark.parametrize("input,expected_clean", [
    ("Abhishek", "Abhishek"),  # bare name, no-op
    ("Abhishek Sharma", "Abhishek Sharma"),  # bare full name
    ("my name is Abhishek", "Abhishek"),
    ("My Name Is Abhishek", "Abhishek"),
    ("MY NAME IS ABHISHEK", "ABHISHEK"),
    ("i am Abhishek", "Abhishek"),
    ("I am Abhishek Sharma", "Abhishek Sharma"),
    ("I'm Abhishek", "Abhishek"),
    ("this is Abhishek", "Abhishek"),
    ("call me Abhi", "Abhi"),
    ("you can call me Abhi", "Abhi"),
    ("i go by Abhi", "Abhi"),
    ("the name is Abhishek", "Abhishek"),
    ("name is Abhishek", "Abhishek"),
    # Whitespace handling
    ("  my name is Abhishek  ", "Abhishek"),
    # Day 6 echo bug reproducer (the synthetic message had double prefix)
    ("my name is my name is Abhishek", "Abhishek"),  # 2 iterations strips both
    # Edge cases
    ("", ""),  # empty stays empty
    ("x", "x"),  # too-short still returns as-is (length validator catches it later)
])
def test_strip_name_prefix(input, expected_clean):
    """AC-T4-1: stripping is idempotent and case-insensitive."""
    assert _strip_name_prefix(input) == expected_clean


def test_strip_does_not_match_mid_string():
    """AC-T4-2: prefix patterns only match at start, not mid-string."""
    # If user says "Hi my name is Abhishek", the "Hi " prefix isn't stripped
    # because no pattern matches. We don't strip because that risks false
    # positives. Implementer should NOT add greedy whole-word regex.
    assert _strip_name_prefix("Hi Abhishek") == "Hi Abhishek"


def test_strip_handles_three_iterations_max():
    """AC-T4-3: even adversarial nested prefixes converge in 3 iterations."""
    # 4 nested prefixes -> only 3 stripped by design (defensive cap)
    nested = "my name is i am call me i go by Abhishek"
    result = _strip_name_prefix(nested)
    # Should NOT loop forever; result is still a name (whatever 3 iterations produced)
    assert "Abhishek" in result


def test_validate_name_with_prefix_returns_clean_name():
    """AC-T4-4: full validate_name flow strips prefix and returns clean name."""
    ok, value = validate_name("my name is Abhishek")
    assert ok is True, f"validate_name should accept stripped name; got value={value}"
    assert value == "Abhishek", f"Expected 'Abhishek', got {value!r}"


def test_validate_name_with_prefix_too_short_after_strip():
    """AC-T4-5: if stripping produces empty/too-short, length validator fails as before."""
    # "my name is " (trailing space, nothing after) -> after strip becomes ""
    ok, value = validate_name("my name is ")
    # Length validator should reject ""
    assert ok is False
    assert isinstance(value, str)  # error message

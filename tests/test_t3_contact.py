"""T3 unit tests — contact-capture helpers (yes/no/email/domain/voice).

Per SPEC v2 §6 AC-T3-4..AC-T3-8 (helper-level coverage).
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "agent_builder"))

from followup import (  # noqa: E402
    is_yes_intent,
    is_no_intent,
    extract_email,
    _email_domain,
    contact_ask_suffix,
    contact_yes_voice_text,
    contact_invalid_voice_text,
    contact_giveup_voice_text,
    contact_captured_voice_text,
)


# ---------------------------------------------------------------------------
# is_yes_intent — mixes anchored + unanchored patterns by design.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("msg", [
    "yes",
    "Yes please",
    "yeah",
    "yep",
    "sure",
    "ok",
    "okay",
    "kay",
    "please do",
    "send it",
    "send them",
    "send me",
    "go ahead",
    "absolutely",
    "that would be great",
    "that would be nice",
    "that would be helpful",
    # Comma-stripping per Fix #4
    "yes, please",
])
def test_yes_intent_positive(msg):
    assert is_yes_intent(msg) is True, f"Expected is_yes_intent({msg!r}) == True"


@pytest.mark.parametrize("msg", [
    "no",
    "nah",
    "skip it",
    "don't bother",
    "",
    "tell me about HealthFirst",
])
def test_yes_intent_negative(msg):
    assert is_yes_intent(msg) is False, f"Expected is_yes_intent({msg!r}) == False"


# ---------------------------------------------------------------------------
# is_no_intent — anchored, narrow.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("msg", [
    "no",
    "nope",
    "nah",
    "don't bother",
    "dont bother",
    "don't need",
    "skip it",
    "skip the email",
    "not necessary",
    "not needed",
    # Comma-stripping
    "no,",
])
def test_no_intent_positive(msg):
    assert is_no_intent(msg) is True, f"Expected is_no_intent({msg!r}) == True"


@pytest.mark.parametrize("msg", [
    "no thanks",  # done-intent territory, not bare-no
    "no I want term life",  # substantive
    "yes",
    "",
    "skip the recommendation",  # not the email skip pattern
])
def test_no_intent_negative(msg):
    assert is_no_intent(msg) is False, f"Expected is_no_intent({msg!r}) == False"


# ---------------------------------------------------------------------------
# extract_email — liberal regex with trailing-punct stripping.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("a@b.co", "a@b.co"),
    ("My email is abhishek@example.com", "abhishek@example.com"),
    ("abhishek.sharma@inadev.com", "abhishek.sharma@inadev.com"),
    ("send it to user+tag@gmail.com please", "user+tag@gmail.com"),
    ("abhishek@example.com.", "abhishek@example.com"),  # trailing dot stripped
    ("abhishek@example.com,", "abhishek@example.com"),
    ("abhishek@example.com!", "abhishek@example.com"),
])
def test_extract_email_positive(text, expected):
    assert extract_email(text) == expected


@pytest.mark.parametrize("text", [
    "no email here",
    "abhishek",
    "abhishek@",  # missing domain
    "@example.com",  # missing local
    "abhishek@example",  # missing dot in domain
    "",
    None,
])
def test_extract_email_negative(text):
    assert extract_email(text) is None


# ---------------------------------------------------------------------------
# _email_domain — PII-safe extraction.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("email,domain", [
    ("abhishek@example.com", "example.com"),
    ("a.b.c@sub.example.co.uk", "sub.example.co.uk"),
    ("user+tag@gmail.com", "gmail.com"),
])
def test_email_domain_positive(email, domain):
    assert _email_domain(email) == domain


@pytest.mark.parametrize("email", [
    None,
    "",
    "no-at-sign",
])
def test_email_domain_unknown(email):
    assert _email_domain(email) == "unknown"


# ---------------------------------------------------------------------------
# Voice text determinism — each generator returns identical strings.
# ---------------------------------------------------------------------------

def test_contact_voice_texts_deterministic():
    """Each voice-text generator returns byte-identical output across calls."""
    for fn in (
        contact_ask_suffix,
        contact_yes_voice_text,
        contact_invalid_voice_text,
        contact_giveup_voice_text,
    ):
        first = fn()
        for _ in range(50):
            assert fn() == first, f"{fn.__name__} not deterministic"


def test_contact_captured_voice_text_includes_email():
    """The captured voice text MUST echo the email back to the user (PII-by-design)."""
    out = contact_captured_voice_text("abhishek@example.com")
    assert "abhishek@example.com" in out
    assert "Got it" in out


def test_contact_ask_suffix_starts_with_space():
    """Suffix prepends a leading space so it grafts cleanly onto rstripped response."""
    suffix = contact_ask_suffix()
    assert suffix.startswith(" "), f"Suffix must start with a space; got {suffix!r}"
    assert "email" in suffix.lower()


def test_contact_invalid_voice_text_explains_format():
    """Invalid retry prompt should mention example format."""
    out = contact_invalid_voice_text()
    assert "example" in out.lower() or "name at" in out.lower()


def test_contact_giveup_voice_text_offers_help():
    """Giveup text should leave the door open for follow-up help."""
    out = contact_giveup_voice_text()
    assert "anything else" in out.lower() or "help" in out.lower()

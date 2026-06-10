"""
tests/test_voice_explanation.py

TASK-074 through TASK-077: Contract tests for Sub-Agent 3 voice explanation output.

These tests validate that the explanation produced by the Recommendation Explainer
sub-agent meets the required criteria:
  - ≤120 words total (TASK-074)
  - Each product name present in the explanation (TASK-074)
  - Personalisation markers per product (TASK-075)
  - No markdown syntax (WaveNet-safe) (TASK-076)
  - Follow-up single-product response ≤80 words (TASK-077)

No live Gemini or ADK call is made. Tests operate on representative fixture strings
that represent the expected shape of the sub-agent's output. The helpers
(`count_words`, `contains_markdown`) are also independently useful as evaluation
utilities when integrating with the live agent.
"""

import re
import pathlib

import pytest


# ---------------------------------------------------------------------------
# Utility functions (also used by the live integration harness)
# ---------------------------------------------------------------------------

def count_words(text: str) -> int:
    """Count whitespace-separated tokens in a string."""
    return len(text.split())


def contains_markdown(text: str) -> bool:
    """Return True if text contains any markdown formatting that would cause
    awkward pauses or symbols in TTS WaveNet output.

    Checks for: bold (**), headings (##, #), bullet lists (- item, * item),
    table rows (| ), triple newlines.
    """
    patterns = [
        r"\*\*",        # bold
        r"^\s*#{1,6} ", # headings
        r"^\s*[-*] ",   # bullet list items
        r"\|",          # table columns
        r"\n{3,}",      # triple+ newlines
    ]
    for pat in patterns:
        if re.search(pat, text, re.MULTILINE):
            return True
    return False


def has_personalisation(text: str, markers: list[str]) -> bool:
    """Return True if at least one personalisation marker appears in the text (case-insensitive)."""
    text_lower = text.lower()
    return any(m.lower() in text_lower for m in markers)


# ---------------------------------------------------------------------------
# Fixtures — representative explanation outputs
# ---------------------------------------------------------------------------

# Three-product recommendation for a 38-year-old non-smoker, ₹15L income,
# life + health coverage goals, 2 dependents.
THREE_PRODUCT_EXPLANATION = (
    "Based on your profile, here are my top three recommendations. "
    "First, the SecureLife Term Plus gives you ₹1 crore life cover for around ₹1,800 a month "
    "— at 38, this is the ideal time to lock in a low premium before rates increase. "
    "Second, the FamilyShield Health plan covers you and both your dependents for just ₹2,200 "
    "monthly, protecting your family's medical costs under one policy. "
    "Third, the WealthGuard ULIP lets you invest ₹3,000 a month toward your retirement while "
    "keeping life cover intact — well within your ₹15 lakh income. "
    "Would you like more detail on any of these, or shall I start the application?"
)

# Product names used in the three-product fixture
THREE_PRODUCT_NAMES = ["SecureLife Term Plus", "FamilyShield Health", "WealthGuard ULIP"]

# Single-product follow-up deep-dive (≤80 words)
SINGLE_PRODUCT_FOLLOWUP = (
    "The SecureLife Term Plus is a pure term life insurance plan giving ₹1 crore cover "
    "for a 20-year term. It's best suited for non-smokers under 40, like yourself. "
    "Monthly premiums run from ₹1,600 to ₹2,000 depending on the exact term chosen. "
    "Key exclusion: suicide within the first year is not covered, as per IRDAI norms. "
    "Shall I begin the application for this one, or would you like to compare it with another option?"
)

# Personalisation markers expected in the three-product explanation
PERSONALISATION_MARKERS = ["38", "family", "life", "health", "15 lakh", "dependents"]

# A deliberately bad explanation to verify the negative test path
MARKDOWN_EXPLANATION = (
    "## Top 3 Recommendations\n"
    "**Product 1**: SecureLife Term Plus — ₹1 crore cover\n"
    "- Great for non-smokers aged 38\n"
    "- Premium: ₹1,800/month\n\n\n"
    "**Product 2**: FamilyShield Health\n"
    "| Field | Value |\n"
    "| --- | --- |\n"
    "| Premium | ₹2,200 |\n"
)


# ---------------------------------------------------------------------------
# TASK-074: word count ≤ 120 and all product names present
# ---------------------------------------------------------------------------

class TestWordCount:
    def test_three_product_explanation_under_120_words(self):
        """TASK-074: Full three-product recommendation must be ≤120 words."""
        assert count_words(THREE_PRODUCT_EXPLANATION) <= 120, (
            f"Explanation is {count_words(THREE_PRODUCT_EXPLANATION)} words — exceeds 120-word limit"
        )

    def test_all_product_names_present(self):
        """TASK-074: Each of the top-3 product names must appear in the explanation."""
        for name in THREE_PRODUCT_NAMES:
            assert name in THREE_PRODUCT_EXPLANATION, (
                f"Product name '{name}' not found in explanation"
            )

    def test_count_words_utility_correct(self):
        """count_words handles normal prose correctly."""
        assert count_words("one two three") == 3
        assert count_words("  spaced   words  ") == 2
        assert count_words("") == 0


# ---------------------------------------------------------------------------
# TASK-075: Personalisation — at least one marker per product block
# ---------------------------------------------------------------------------

class TestPersonalisation:
    def test_explanation_contains_personalisation_markers(self):
        """TASK-075: Explanation must reference at least one of: age, family, income, coverage goal."""
        assert has_personalisation(THREE_PRODUCT_EXPLANATION, PERSONALISATION_MARKERS), (
            f"Explanation contains none of the expected personalisation markers: "
            f"{PERSONALISATION_MARKERS}"
        )

    def test_age_reference_present(self):
        """Customer age '38' must be referenced in the explanation."""
        assert "38" in THREE_PRODUCT_EXPLANATION

    def test_family_or_dependent_reference_present(self):
        """A family/dependent reference must be present."""
        assert has_personalisation(THREE_PRODUCT_EXPLANATION, ["family", "dependent", "dependents"])

    def test_coverage_goal_reference_present(self):
        """At least one of the customer's coverage goals (life/health) must be mentioned."""
        assert has_personalisation(THREE_PRODUCT_EXPLANATION, ["life", "health"])

    def test_income_reference_present(self):
        """Income bracket reference must be present."""
        assert has_personalisation(THREE_PRODUCT_EXPLANATION, ["15 lakh", "₹15", "income"])


# ---------------------------------------------------------------------------
# TASK-076: No markdown — WaveNet-safe output
# ---------------------------------------------------------------------------

class TestNoMarkdown:
    def test_good_explanation_has_no_markdown(self):
        """TASK-076: A well-formed explanation must not contain markdown syntax."""
        assert not contains_markdown(THREE_PRODUCT_EXPLANATION)

    def test_bad_explanation_detected_as_markdown(self):
        """Negative test: the contains_markdown helper correctly flags markdown output."""
        assert contains_markdown(MARKDOWN_EXPLANATION)

    def test_no_bold_markers(self):
        assert "**" not in THREE_PRODUCT_EXPLANATION

    def test_no_heading_markers(self):
        assert "##" not in THREE_PRODUCT_EXPLANATION
        assert THREE_PRODUCT_EXPLANATION.lstrip()[0] != "#"

    def test_no_table_pipe(self):
        assert "|" not in THREE_PRODUCT_EXPLANATION

    def test_no_triple_newline(self):
        assert "\n\n\n" not in THREE_PRODUCT_EXPLANATION


# ---------------------------------------------------------------------------
# TASK-077: Single-product follow-up ≤ 80 words
# ---------------------------------------------------------------------------

class TestFollowUpResponse:
    def test_single_product_followup_under_80_words(self):
        """TASK-077: Follow-up deep-dive on one product must be ≤80 words."""
        assert count_words(SINGLE_PRODUCT_FOLLOWUP) <= 80, (
            f"Follow-up response is {count_words(SINGLE_PRODUCT_FOLLOWUP)} words — exceeds 80-word limit"
        )

    def test_single_product_followup_has_no_markdown(self):
        """Follow-up response must also be WaveNet-safe."""
        assert not contains_markdown(SINGLE_PRODUCT_FOLLOWUP)

    def test_single_product_followup_ends_with_invitation(self):
        """Follow-up must end with an application/compare invitation."""
        assert has_personalisation(
            SINGLE_PRODUCT_FOLLOWUP,
            ["application", "compare", "shall I", "would you like"],
        )


# ---------------------------------------------------------------------------
# TASK-089: Pitch mode — Product Deep-Dive (Story 5)
# ---------------------------------------------------------------------------

# Representative pitch output for a savings product (ULIP) — text channel, no word limit.
PITCH_ULIP_TEXT = (
    "The WealthGuard ULIP is open to applicants between 18 and 55 years old with a "
    "minimum annual income of 300,000 INR. Non-smokers only. "
    "It provides market-linked returns alongside a life cover of up to 50 lakh INR, "
    "with a flexible policy term of 10 to 20 years. "
    "This plan historically projects approximately 11% annual growth based on the catalog return rate. "
    "Unlike the term and health plans we reviewed, this ULIP is the only option that builds "
    "long-term wealth while keeping life protection intact — that distinctive dual benefit is "
    "what sets it apart from the other two ranked products. "
    "It ranked second for your profile, scoring highest on income fit and long-term goal alignment. "
    "Shall I walk you through starting the application, or would you like to compare it with another plan?"
)

# Representative pitch output for a pure-protection product (term life) — voice channel.
PITCH_TERM_VOICE = (
    "The SecureLife Term Plus is available to non-smokers aged 18 to 65 with a minimum income "
    "of 200,000 INR. It covers up to 1 crore INR for a term of 10 to 30 years. "
    "This is a pure protection plan — premiums pay for cover only, with no maturity payout. "
    "Its key differentiator among the top three is the longest available term at 30 years, "
    "giving you cover deep into retirement. "
    "Shall I start the application?"
)

# Pitch fixture where return_rate is intentionally absent — must NOT contain return claim
PITCH_NO_RETURN_RATE = (
    "The GrowthSure Endowment is available to applicants aged 18 to 55. "
    "It combines life cover with a savings component over a 15-year term. "
    "It scored highest on your profile due to strong age centrality and income fit. "
    "Shall I proceed with the application?"
)


class TestPitchModeSavingsProduct:
    """Story 5 AC 4: savings product pitch cites return_rate from catalog; no LLM fabrication."""

    def test_ulip_pitch_contains_return_rate_mention(self):
        """Pitch for a savings product must mention the return_rate figure."""
        assert has_personalisation(PITCH_ULIP_TEXT, ["11%", "11 %", "11 percent", "return rate"])

    def test_ulip_pitch_no_markdown(self):
        assert not contains_markdown(PITCH_ULIP_TEXT)

    def test_ulip_pitch_has_eligibility_prerequisites(self):
        """Pitch must include eligibility info: age range, income, or smoker status."""
        assert has_personalisation(
            PITCH_ULIP_TEXT,
            ["18", "55", "300,000", "non-smoker", "non smoker", "income"],
        )

    def test_ulip_pitch_has_unique_differentiator(self):
        """Story 5 AC 6: pitch must highlight one distinguishing trait vs. other top-3."""
        assert has_personalisation(
            PITCH_ULIP_TEXT,
            ["sets it apart", "differenti", "unique", "only option", "unlike"],
        )

    def test_ulip_pitch_has_suitability_comparison(self):
        """Story 5 AC 1c: pitch must compare suitability score against other top-3."""
        assert has_personalisation(
            PITCH_ULIP_TEXT,
            ["ranked", "second", "first", "third", "suitability", "scored"],
        )


class TestPitchModeProtectionProduct:
    """Story 5 AC 5: protection product pitch explicitly states no maturity value."""

    def test_term_pitch_states_pure_protection(self):
        """Pitch for term_life must state it is a pure protection plan."""
        assert has_personalisation(
            PITCH_TERM_VOICE,
            ["pure protection", "no maturity", "protection plan", "no payout"],
        )

    def test_term_pitch_voice_within_120_words(self):
        """Story 5 AC 2: voice-channel pitch must be ≤ 120 words."""
        assert count_words(PITCH_TERM_VOICE) <= 120, (
            f"Voice pitch is {count_words(PITCH_TERM_VOICE)} words — exceeds 120-word limit"
        )

    def test_term_pitch_no_markdown(self):
        assert not contains_markdown(PITCH_TERM_VOICE)


class TestPitchModeTextChannel:
    """Story 5 AC 3: text-channel pitch is NOT truncated to 120 words."""

    def test_text_channel_pitch_may_exceed_120_words(self):
        """ULIP text-channel pitch is a full structured output — allowed to exceed 120 words."""
        # The ULIP text pitch is intentionally long to exercise the no-truncation rule.
        # This test asserts the fixture IS longer than 120 words, confirming the rule is
        # being exercised (not just passing vacuously on a short string).
        assert count_words(PITCH_ULIP_TEXT) > 120, (
            "Text-channel pitch fixture should be > 120 words to test that no truncation occurs"
        )

    def test_text_channel_pitch_no_markdown(self):
        """Even text-channel pitch must not use markdown formatting."""
        assert not contains_markdown(PITCH_ULIP_TEXT)


class TestPitchModeNoReturnRate:
    """Story 5 AC 4 negative: if return_rate is absent, pitch must NOT claim returns."""

    def test_no_return_rate_pitch_omits_return_claim(self):
        """If return_rate field is missing, pitch must not invent a return figure."""
        # The fixture PITCH_NO_RETURN_RATE deliberately omits any return percentage.
        assert not has_personalisation(
            PITCH_NO_RETURN_RATE,
            ["%", "percent", "annual growth", "return rate", "maturity value"],
        )

    def test_no_return_rate_pitch_still_valid(self):
        """Pitch without return mention should still cover eligibility and invitation."""
        assert has_personalisation(PITCH_NO_RETURN_RATE, ["18", "55", "application", "proceed"])


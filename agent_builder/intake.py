"""
InsureVoice — Deterministic Conversational Intake State Machine (P.2)
=====================================================================
Replaces the LLM-driven EXTRACT step with deterministic Python validators.

Why this exists
---------------
flash-lite cannot reliably:
  - Hold profile state across multi-turn conversations (forgets prior fields)
  - Validate field values (accepts 'income=health insurance')
  - Construct well-shaped tool args (passes coverage_goals as string vs list)

P.2 owns intake: it asks one question per turn, validates the answer with
plain Python regex/enum/range checks, and keeps the partial profile in
ADK session state. When all required fields are valid, it returns
{"complete": True, "profile": {...}} and main.py builds a single complete
synthetic message for the LLM agent — which then only has to run the
search → compliance → rank → explain pipeline, not extract anything.

Field order is the conversational sequence the agent will follow.
"""

import logging
import re
from typing import Tuple, Any

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Field validators — each returns (ok: bool, value_or_error: Any)
# When ok=True, value_or_error is the cleaned/typed value to store.
# When ok=False, value_or_error is a human-readable error message to read out.
# ---------------------------------------------------------------------------

def _normalise(s: str) -> str:
    return (s or "").strip()


# T4 — Bug A: strip conversational prefixes before name validation.
# Day 6 echo bug: user said "my name is Abhishek" -> agent said
# "Nice to meet you, my name is Abhishek." Validators must see "Abhishek".
# Patterns are anchored to start of (lower-cased) input and applied
# iteratively (max 3 passes) so nested phrasings like "my name is i am Abhi"
# also collapse. Mid-string matches are intentionally NOT stripped to avoid
# false positives on names that happen to contain the words.
_NAME_PREFIX_PATTERNS = [
    re.compile(r"^my name is(\s+|$)", re.IGNORECASE),
    re.compile(r"^my names(\s+|$)", re.IGNORECASE),  # mistranscription variant
    re.compile(r"^i am(\s+|$)", re.IGNORECASE),
    re.compile(r"^i'm(\s+|$)", re.IGNORECASE),
    re.compile(r"^this is(\s+|$)", re.IGNORECASE),
    re.compile(r"^call me(\s+|$)", re.IGNORECASE),
    re.compile(r"^i go by(\s+|$)", re.IGNORECASE),
    re.compile(r"^you can call me(\s+|$)", re.IGNORECASE),
    re.compile(r"^the name is(\s+|$)", re.IGNORECASE),
    re.compile(r"^name is(\s+|$)", re.IGNORECASE),
    re.compile(r"^name's(\s+|$)", re.IGNORECASE),
]


def _strip_name_prefix(text: str) -> str:
    """Strip common conversational prefixes that precede a name.

    "my name is Abhishek" -> "Abhishek"
    "I am Abhishek Sharma" -> "Abhishek Sharma"
    "Abhishek" -> "Abhishek" (no-op)

    Idempotent up to 3 iterations (defensive cap against adversarial
    nested input). Returns the input stripped + with all matching
    leading prefixes removed.
    """
    text = (text or "").strip()
    for _ in range(3):
        before = text
        for pattern in _NAME_PREFIX_PATTERNS:
            text = pattern.sub("", text, count=1).strip()
        if text == before:
            break
    return text


def validate_name(value: str) -> Tuple[bool, Any]:
    v = _normalise(value)
    # T4 — Bug A: strip conversational prefixes BEFORE length/character checks
    v = _strip_name_prefix(v)
    if not v:
        return False, "Sorry, I didn't catch that — what's your name?"
    if len(v) < 2 or len(v) > 50:
        return False, "Could you give me your full name? Anything from 2 to 50 letters works."
    if not re.match(r"^[A-Za-z]", v):
        return False, "Names start with a letter — could you try again?"
    if not re.match(r"^[A-Za-z][A-Za-z\s\.\-']{1,49}$", v):
        return False, "Names should only have letters, spaces, hyphens, or apostrophes — could you try again?"
    return True, v


def validate_age(value: str) -> Tuple[bool, Any]:
    v = _normalise(value)
    m = re.search(r"\d+", v)
    if not m:
        return False, "Sorry, I need your age in years — like '35'."
    age = int(m.group())
    if age < 18 or age > 95:
        return False, "Hmm, I can only quote for ages 18 to 95. Could you double-check?"
    return True, age


# T2 — Bug F: detect ambiguous smoker answers BEFORE the yes/no check.
# Original `no_words` contained "don't" and "dont", which substring-matched
# "I don't know" → recorded smoker=False silently. Mechanical fix per L-001:
# the ambiguous tokens are detected here; handle_intake owns the counter
# state machine + auto-default after 2 ambiguous answers.
_AMBIGUOUS_SMOKER_PHRASES = (
    "don't know", "dont know", "do not know", "not sure", "unsure",
    "no idea", "maybe", "i think", "kind of", "sort of", "sometimes",
    "i guess", "uncertain", "hmm",
)


def _is_ambiguous_smoker(value_lower: str) -> bool:
    """True only when ambiguous AND no clear yes/no smoke signal present.

    The clear-signals guard prevents "I think yes" / "probably yes" from
    being mis-classified as ambiguous (token-level yes/no wins).
    """
    if not any(p in value_lower for p in _AMBIGUOUS_SMOKER_PHRASES):
        return False
    tokens = set(re.findall(r"\b[\w']+\b", value_lower))
    clear_signals = tokens & {"yes", "yeah", "yep", "yup", "no", "nope", "nah", "never",
                              "smoker", "smoking"}
    if clear_signals:
        return False
    if re.search(r"\b(don'?t|do not|never)\s+(smoke|smoking|smoked)\b", value_lower):
        return False
    if re.search(r"\b(i\s+)?smoke(s|d)?\b", value_lower):
        return False
    return True


def validate_smoker(value: str) -> Tuple[bool, Any]:
    """Validate yes/no smoker. Ambiguous handling lives in handle_intake.

    Removes substring `"don't"`/`"dont"`/`"not"` from the no-words set —
    those greedy substrings caused Bug F (matched "I don't know"). Token
    boundaries (`\\b`) replace substring membership for `"no"`/`"yes"` etc.
    """
    v = _normalise(value).lower()
    if not v:
        return False, "Do you smoke? A simple yes or no works."

    # Negation-of-smoke phrases (must match BEFORE token check — "I don't smoke").
    if re.search(r"\b(don'?t|do not|never)\s+(smoke|smoking|smoked)\b", v):
        return True, False
    if re.search(r"\bnon[\s\-]?smoker\b", v):
        return True, False
    # Affirmative-of-smoke patterns ("I smoke", "smokes", "smoked").
    if re.search(r"\b(i\s+)?smoke(s|d)?\b", v) and not re.search(r"\b(don'?t|do not|never)\s+smoke", v):
        return True, True

    # Token-bound yes/no (avoids matching "no" inside "snow" or "y" inside "yesterday").
    tokens = set(re.findall(r"\b[\w']+\b", v))
    if tokens & {"no", "nope", "nah", "never", "n"}:
        return True, False
    if tokens & {"yes", "yeah", "yep", "yup", "y", "smoker", "smoking"}:
        return True, True

    return False, "Sorry, I didn't catch that — do you smoke? Yes or no?"


def validate_income(value: str) -> Tuple[bool, Any]:
    v = _normalise(value).lower()
    if not v:
        return False, "What's your annual income? You can say it in lakhs or crores."
    # Find the first number (with optional decimal)
    m = re.search(r"(\d+(?:\.\d+)?)", v)
    if not m:
        return False, "Could you say your income as a number? Like '12 lakhs' or '1.5 crores'."
    n = float(m.group(1))
    # Detect unit
    if "crore" in v or "cr" in v or "Cr" in v:
        income = int(n * 10_000_000)
    elif "lakh" in v or "lac" in v or "lpa" in v or " l" in (" " + v):
        income = int(n * 100_000)
    elif "thousand" in v or " k" in (" " + v):
        income = int(n * 1_000)
    elif n < 1000:
        # Bare number under 1000 — Indian customers usually mean lakhs
        income = int(n * 100_000)
    else:
        income = int(n)
    if income < 100_000:
        return False, "That's below 1 lakh — could you double-check your annual income?"
    if income > 1_000_000_000:
        return False, "That sounds high — could you double-check your annual income?"
    return True, income


def validate_health_status(value: str) -> Tuple[bool, Any]:
    v = _normalise(value).lower()
    if not v:
        return False, "Are you in good health, or do you have any pre-existing conditions?"
    pre_words = {"pre-existing", "pre existing", "preexisting", "diabetes", "blood pressure",
                 "hypertension", "heart", "cancer", "asthma", "thyroid", "cholesterol",
                 "yes i have", "have a condition", "have conditions", "diabetic"}
    healthy_words = {"healthy", "fit", "fine", "no condition", "no pre", "nothing",
                     "all good", "perfect health", "no issues", "no problems", "none"}
    if any(w in v for w in pre_words):
        return True, "pre_existing"
    if any(w in v for w in healthy_words):
        return True, "healthy"
    return False, "Sorry, I didn't catch that — are you in good health, or do you have a condition like diabetes or blood pressure?"


def validate_family_size(value: str) -> Tuple[bool, Any]:
    v = _normalise(value).lower()
    if not v:
        return False, "How many family members would you like covered, including yourself?"
    word_to_num = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                   "seven": 7, "eight": 8, "nine": 9, "ten": 10, "myself": 1, "just me": 1, "alone": 1}
    for w, n in word_to_num.items():
        if w in v:
            return True, n
    m = re.search(r"\d+", v)
    if not m:
        return False, "Could you say the family size as a number? Like '4'."
    size = int(m.group())
    if size < 1 or size > 10:
        return False, "Family size needs to be between 1 and 10. Could you double-check?"
    return True, size


def validate_coverage_goals(value: str) -> Tuple[bool, Any]:
    v = _normalise(value).lower()
    if not v:
        return False, "What kind of cover are you looking for? Term life, health, critical illness, endowment, ULIP, child plan, or pension?"
    goals = []
    keyword_map = {
        "term_life": ["term life", "term insurance", "life cover", "life insurance", "term plan", "term"],
        "health": ["health insurance", "health cover", "health plan", "medical", "hospital", "health"],
        "critical_illness": ["critical illness", "critical care", "ci ", "cancer cover", "cancer"],
        "endowment": ["endowment", "savings plan", "savings"],
        "ulip": ["ulip", "unit linked", "investment-linked", "wealth"],
        "child_plan": ["child plan", "child education", "kids", "children"],
        "pension": ["pension", "retirement", "retire"],
    }
    for goal, keywords in keyword_map.items():
        for kw in keywords:
            if kw in v:
                if goal not in goals:
                    goals.append(goal)
                break
    if not goals:
        return False, "Sorry, I didn't catch that — could you say 'term life', 'health', 'critical illness', 'endowment', 'ULIP', 'child plan', or 'pension'?"
    return True, goals


def validate_sum_assured(value: str) -> Tuple[bool, Any]:
    v = _normalise(value).lower()
    if not v:
        return False, "What sum assured would you like? You can say it in lakhs or crores."
    m = re.search(r"(\d+(?:\.\d+)?)", v)
    if not m:
        return False, "Could you say the sum assured as a number? Like '50 lakhs' or '1 crore'."
    n = float(m.group(1))
    if "crore" in v or "cr" in v or "Cr" in v:
        amount = int(n * 10_000_000)
    elif "lakh" in v or "lac" in v:
        amount = int(n * 100_000)
    elif "thousand" in v or " k" in (" " + v):
        amount = int(n * 1_000)
    elif n < 100:
        amount = int(n * 10_000_000)  # bare numbers like "1" likely crore for sum assured
    elif n < 100_000:
        amount = int(n * 100_000)
    else:
        amount = int(n)
    if amount < 100_000:
        return False, "That's below 1 lakh — could you double-check the sum assured?"
    if amount > 5_000_000_000:
        return False, "That sounds high — could you double-check the sum assured?"
    return True, amount


# ---------------------------------------------------------------------------
# Question library — one canonical question per field
# {name} placeholder is filled with profile.name once known
# ---------------------------------------------------------------------------

QUESTIONS = {
    "name": "Welcome to InsureVoice! I'm your personal insurance advisor, and I'm absolutely thrilled to help you find the perfect cover — in seconds, not hours. To get started, may I know your good name?",
    "age": "Wonderful to have you, {name}! Now, just so I can handpick the plans that are perfectly tailored for you — how old are you?",
    "smoker": "Brilliant! One small thing — do you smoke at all? No judgement, it just helps me filter the right options for you.",
    "income": "Fantastic! To make sure the premiums I suggest are completely comfortable for you — what's your annual income? You can just say something like '10 lakhs' or '1.5 crore', whatever feels natural.",
    "health_status": "You're doing great, {name}! Are you in good health overall, or do you have any pre-existing conditions — like diabetes or blood pressure — that I should factor into your plan?",
    "family_size": "Perfect! Now the important part — who are we protecting here? Is it just yourself, or do you have a family — a spouse, kids, parents — that you'd love to cover under the same plan?",
    "coverage_goals": "Love it! So what's the kind of protection that matters most to you right now? Are you thinking life cover for your family, health protection, building a savings corpus, securing your child's future, or planning a worry-free retirement? Just tell me what's on your mind!",
    "sum_assured": "Almost there — you're one step away from your perfect plan! How much cover do you have in mind? Something like '50 lakhs' or '1 crore' is perfectly fine — just ballpark it.",
}

VALIDATORS = {
    "name": validate_name,
    "age": validate_age,
    "smoker": validate_smoker,
    "income": validate_income,
    "health_status": validate_health_status,
    "family_size": validate_family_size,
    "coverage_goals": validate_coverage_goals,
    "sum_assured": validate_sum_assured,
}

ORDER = ["name", "age", "smoker", "income", "health_status", "family_size", "coverage_goals", "sum_assured"]


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

def handle_intake(intake_state: dict, message: str) -> dict:
    """Process one turn of conversational intake.

    Args:
        intake_state: mutable dict holding profile + expecting_field. Keyed by
            ADK session state. Caller persists this across turns.
        message: the user's latest utterance.

    Returns:
        {"complete": False, "agent_says": "<question or error>"} when intake
        is still in progress (skip LLM, return this text).

        {"complete": True, "profile": {...validated fields...}} when profile
        is fully filled. Caller forwards to LLM agent for pipeline.
    """
    profile = intake_state.setdefault("profile", {})
    expecting = intake_state.get("expecting_field")

    # T2 — Bug F. Ambiguous-smoker detection runs ONLY when expecting==smoker.
    # Catches "I don't know" / "not sure" BEFORE validate_smoker (where the
    # original substring-match against "don't" silently recorded smoker=False).
    # Counter-driven: 1st ambiguous → re-prompt; 2nd ambiguous → auto-default
    # smoker=True (compliance-safe; documented in T2 SPEC v2 §6.2).
    if expecting == "smoker":
        v_lower = (message or "").strip().lower()
        if _is_ambiguous_smoker(v_lower):
            count = intake_state.get("_smoker_reprompt_count", 0)
            if count >= 1:
                # Second ambiguous answer — auto-default to smoker=True.
                new_count = count + 1
                intake_state["_smoker_reprompt_count"] = new_count
                profile["smoker"] = True
                intake_state["_smoker_resolved"] = "auto_default_true"
                _log.warning(
                    "BUG_F_AUTO_DEFAULT smoker=True reason=ambiguous_twice count=%d value=%r",
                    new_count, message[:40] if message else "",
                )
                # Fall through to "find next missing field" (skip validator —
                # profile["smoker"] is already set above; the guard below
                # prevents re-validation from overwriting it).
            else:
                # First ambiguous answer — increment, re-prompt, do NOT advance.
                intake_state["_smoker_reprompt_count"] = count + 1
                return {
                    "complete": False,
                    "agent_says": "No worries if you're not sure — for the recommendation, do you currently smoke? Yes or no?",
                }

    # If we're expecting a specific field, validate the answer FIRST.
    # Bug F guard: skip validation if smoker was auto-defaulted above
    # (profile already contains the field).
    if expecting and expecting in VALIDATORS and expecting not in profile:
        validator = VALIDATORS[expecting]
        ok, value_or_error = validator(message)
        if not ok:
            # Re-ask with the error appended
            return {
                "complete": False,
                "agent_says": value_or_error,
            }
        profile[expecting] = value_or_error

    # Find next missing field
    next_field = next((f for f in ORDER if f not in profile), None)
    if next_field is None:
        # All fields complete → forward to pipeline
        intake_state["expecting_field"] = None
        intake_state["complete"] = True
        return {
            "complete": True,
            "profile": dict(profile),
        }

    # Ask the next question, set expecting state
    intake_state["expecting_field"] = next_field
    template = QUESTIONS[next_field]
    try:
        question = template.format(name=profile.get("name", ""))
    except (KeyError, ValueError):
        question = template
    return {
        "complete": False,
        "agent_says": question,
    }


def build_synthetic_message(profile: dict) -> str:
    """Build a single complete message representing the validated profile.

    Used to forward to the LlmAgent runner ONCE intake completes — gives
    the LLM all fields in one shot so it doesn't need to extract anything,
    only run the pipeline.
    """
    smoker_str = "smoker" if profile.get("smoker") else "non-smoker"
    health_str = profile.get("health_status", "healthy")
    if health_str == "pre_existing":
        health_str = "with pre-existing conditions"
    coverage = ", ".join(profile.get("coverage_goals", [])) or "term_life"
    return (
        f"My name is {profile.get('name', 'Customer')}. "
        f"I am {profile.get('age', 30)} years old, "
        f"{smoker_str}, "
        f"{health_str}, "
        f"family of {profile.get('family_size', 1)}, "
        f"annual income {profile.get('income', 1000000)} INR. "
        f"I want {coverage} insurance with sum assured {profile.get('sum_assured', 10000000)} INR."
    )

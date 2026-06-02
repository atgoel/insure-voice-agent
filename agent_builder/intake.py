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

import re
from typing import Tuple, Any


# ---------------------------------------------------------------------------
# Field validators — each returns (ok: bool, value_or_error: Any)
# When ok=True, value_or_error is the cleaned/typed value to store.
# When ok=False, value_or_error is a human-readable error message to read out.
# ---------------------------------------------------------------------------

def _normalise(s: str) -> str:
    return (s or "").strip()


def validate_name(value: str) -> Tuple[bool, Any]:
    v = _normalise(value)
    if not v:
        return False, "I didn't catch that — could you tell me your name?"
    if len(v) < 2 or len(v) > 50:
        return False, "Your name should be between 2 and 50 characters."
    if not re.match(r"^[A-Za-z]", v):
        return False, "Your name should start with a letter."
    if not re.match(r"^[A-Za-z][A-Za-z\s\.\-']{1,49}$", v):
        return False, "Please use only letters, spaces, hyphens, or apostrophes in your name."
    return True, v


def validate_age(value: str) -> Tuple[bool, Any]:
    v = _normalise(value)
    m = re.search(r"\d+", v)
    if not m:
        return False, "Could you tell me your age in years? For example, '35'."
    age = int(m.group())
    if age < 18 or age > 95:
        return False, "Age should be between 18 and 95 years."
    return True, age


def validate_smoker(value: str) -> Tuple[bool, Any]:
    v = _normalise(value).lower()
    if not v:
        return False, "Do you smoke? Please answer yes or no."
    no_words = {"no", "non-smoker", "non smoker", "nope", "nah", "never", "n", "not", "dont", "don't"}
    yes_words = {"yes", "smoker", "yeah", "yep", "yup", "y", "i smoke", "smoking"}
    if any(w in v for w in no_words):
        return True, False
    if any(w in v for w in yes_words):
        return True, True
    return False, "I didn't quite catch that — do you smoke? Please say yes or no."


def validate_income(value: str) -> Tuple[bool, Any]:
    v = _normalise(value).lower()
    if not v:
        return False, "What is your annual income? You can say it in lakhs or crores."
    # Find the first number (with optional decimal)
    m = re.search(r"(\d+(?:\.\d+)?)", v)
    if not m:
        return False, "Please give your annual income as a number — for example, '12 lakhs' or '1.5 crores'."
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
        return False, "Annual income should be at least 1 lakh INR. Could you confirm?"
    if income > 1_000_000_000:
        return False, "That seems unusually high — could you confirm your annual income?"
    return True, income


def validate_health_status(value: str) -> Tuple[bool, Any]:
    v = _normalise(value).lower()
    if not v:
        return False, "Are you healthy, or do you have any pre-existing conditions?"
    pre_words = {"pre-existing", "pre existing", "preexisting", "diabetes", "blood pressure",
                 "hypertension", "heart", "cancer", "asthma", "thyroid", "cholesterol",
                 "yes i have", "have a condition", "have conditions", "diabetic"}
    healthy_words = {"healthy", "fit", "fine", "no condition", "no pre", "nothing",
                     "all good", "perfect health", "no issues", "no problems", "none"}
    if any(w in v for w in pre_words):
        return True, "pre_existing"
    if any(w in v for w in healthy_words):
        return True, "healthy"
    return False, "Could you confirm — are you healthy, or do you have a pre-existing condition like diabetes or blood pressure?"


def validate_family_size(value: str) -> Tuple[bool, Any]:
    v = _normalise(value).lower()
    if not v:
        return False, "How many family members will be covered? (Including yourself.)"
    word_to_num = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                   "seven": 7, "eight": 8, "nine": 9, "ten": 10, "myself": 1, "just me": 1, "alone": 1}
    for w, n in word_to_num.items():
        if w in v:
            return True, n
    m = re.search(r"\d+", v)
    if not m:
        return False, "Please tell me the family size as a number, like '4'."
    size = int(m.group())
    if size < 1 or size > 10:
        return False, "Family size should be between 1 and 10."
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
        return False, "I didn't catch what kind of cover you want. Could you say 'term life', 'health', 'critical illness', 'endowment', 'ULIP', 'child plan', or 'pension'?"
    return True, goals


def validate_sum_assured(value: str) -> Tuple[bool, Any]:
    v = _normalise(value).lower()
    if not v:
        return False, "What sum assured would you like? You can say it in lakhs or crores."
    m = re.search(r"(\d+(?:\.\d+)?)", v)
    if not m:
        return False, "Please give the sum assured as a number — for example, '50 lakhs' or '1 crore'."
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
        return False, "Sum assured should be at least 1 lakh INR. Could you confirm?"
    if amount > 5_000_000_000:
        return False, "That seems unusually high — could you confirm the sum assured?"
    return True, amount


# ---------------------------------------------------------------------------
# Question library — one canonical question per field
# {name} placeholder is filled with profile.name once known
# ---------------------------------------------------------------------------

QUESTIONS = {
    "name": "Hi! I'm InsureVoice, here to help you find the right insurance cover. May I have your name please?",
    "age": "Nice to meet you, {name}. How old are you?",
    "smoker": "Got it. Do you smoke?",
    "income": "What is your annual income? You can say it in lakhs or crores.",
    "health_status": "Are you healthy, or do you have any pre-existing conditions like diabetes or blood pressure?",
    "family_size": "How many family members will be covered? You can include yourself.",
    "coverage_goals": "What kind of cover are you looking for? Term life, health, critical illness, endowment, ULIP, child plan, or pension?",
    "sum_assured": "And what sum assured would you like? You can say it in lakhs or crores.",
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

    # If we're expecting a specific field, validate the answer FIRST
    if expecting and expecting in VALIDATORS:
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

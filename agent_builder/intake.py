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
import random
import re
from typing import Tuple, Any, Optional

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
    # Strip conversational fillers from the front ("Yeah, my name is..." → "my name is...")
    # These appear naturally when users start with an affirmation before stating their name.
    v = re.sub(r"^(?:yeah|yes|yep|sure|okay|ok|so|well|right|hi|hey|hello|then|actually|alright|great|perfect|wonderful|absolutely|of course|no problem)[\s,;:.\-!]*",
               "", v, flags=re.IGNORECASE).strip()
    # T4 — Bug A: strip conversational prefixes BEFORE length/character checks
    v = _strip_name_prefix(v)
    # Batch-extract support (2026-06-08): when the user dumps multiple fields in
    # one message ("My name is Atul and I am 45 years of age. I have a family..."),
    # prefix-stripping leaves "Atul and I am 45 years of age..." — which fails on
    # length + digit guards. Truncate at the first clause boundary (sentence-ending
    # period, comma, conjunction, or digit) so only the name portion survives.
    # Safe for: "Abhishek" (no boundary), "Mary Jane" (no digit/conjunction),
    # "Mary J. Smith" (period after single-char initial is NOT a sentence boundary).
    # The regex uses a negative lookbehind to protect single-char initial periods.
    # Sentence-boundary period: protect single-letter initials ("Mary J. Smith")
    # by only matching a period that ISN'T preceded by exactly one uppercase letter.
    # Can't use re.IGNORECASE here (it makes the lookbehind match lowercase too).
    # Find the EARLIEST clause boundary — conjunction/digit usually appears before
    # a deep period, so check both and pick whichever comes first.
    _m1 = re.search(r"[,!?]|\band\b|\bor\b|\bbut\b|\d", v, re.IGNORECASE)
    _m2 = re.search(r"(?<![A-Z])\.", v)
    _clause_end = None
    if _m1 and _m2:
        _clause_end = _m1 if _m1.start() <= _m2.start() else _m2
    elif _m1:
        _clause_end = _m1
    elif _m2:
        _clause_end = _m2
    if _clause_end:
        v = v[:_clause_end.start()]
    # B-LIVE-4 (Day 8 live-test fix): STT v2 emits trailing punctuation on
    # utterance boundary ("My name is Abhishek." -> "Abhishek."). Strip after
    # prefix-stripping so internal periods (e.g. "Mary J. Smith") survive,
    # but a single trailing period/comma/exclam/question/whitespace is removed.
    v = re.sub(r"[\.,!?\s]+$", "", v)
    if not v:
        return False, "Sorry, I didn't catch that — what's your name?"
    if len(v) < 2 or len(v) > 50:
        return False, "Could you give me your full name? Anything from 2 to 50 letters works."
    if not re.match(r"^[A-Za-z]", v):
        return False, "Names start with a letter — could you try again?"
    if not re.match(r"^[A-Za-z][A-Za-z\s\.\-']{1,49}$", v):
        return False, "Names should only have letters, spaces, hyphens, or apostrophes — could you try again?"
    return True, v


def _words_to_age(v_lower: str) -> Optional[int]:
    """Parse a spelled-out age (18-95) from natural speech.

    Day-9 live-test fix: users say "thirty-five years old", not "35". The
    digit-only regex rejected every spelled age. Handles tens ("thirty"),
    ones ("five"), and compounds ("thirty-five" / "thirty five"). Returns the
    int age if a plausible 18-95 value is found, else None.
    """
    ones = {
        "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
        "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
        "nineteen": 19,
    }
    tens = {
        "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
        "seventy": 70, "eighty": 80, "ninety": 90,
    }
    # Normalise hyphen + collapse whitespace so "thirty-five" == "thirty five".
    toks = re.split(r"[\s-]+", v_lower)
    total = None
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in tens:
            val = tens[t]
            # optional ones digit immediately after ("thirty" "five")
            if i + 1 < len(toks) and toks[i + 1] in ones and ones[toks[i + 1]] < 10:
                val += ones[toks[i + 1]]
                i += 1
            total = val
            break
        if t in ones:
            total = ones[t]
            break
        i += 1
    return total


def validate_age(value: str) -> Tuple[bool, Any]:
    v = _normalise(value)
    v_lower = v.lower()
    # T-Day9 BUG-B: a digit-bearing NON-age sentence (e.g. "my income is 12
    # lakhs", "50 lakhs of cover") used to have its number greedily grabbed as
    # an age. Guard: if the utterance carries a money/coverage unit, it is NOT
    # an age answer — reject so the field doesn't get poisoned by a stray digit.
    # Substring match (NOT \b) so plurals "lakhs"/"crores" are caught — \b fails
    # between "lakh" and the trailing "s".
    _money_units = ("lakh", "lac", "lpa", "crore", " cr", "thousand")
    if any(u in v_lower for u in _money_units) or "₹" in v or "rupee" in v_lower:
        return False, "Sorry, I need your age in years — like '35'."
    m = re.search(r"\d+", v)
    if m:
        age = int(m.group())
    else:
        # T-Day9 BUG-A: accept spelled-out ages ("thirty-five years old").
        age = _words_to_age(v_lower)
        if age is None:
            return False, "Sorry, I need your age in years — like '35'."
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


# B-LIVE-3 (Day 8 live-test fix): family-relationship phrases that signal a
# family shape but DO NOT carry an explicit count. Used by validate_family_size
# to acknowledge the family signal and re-prompt for the number specifically.
_FAMILY_SHAPE_WORDS = (
    "family", "spouse", "wife", "husband", "kid", "kids", "child", "children",
    "parent", "parents", "mom", "dad", "father", "mother", "sibling",
    "me and my",
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
    # "not a smoker" / "not the smoker" / "I'm not a smoker" / "not smoker" — explicit negation
    if re.search(r"\bnot\s+(?:a\s+|the\s+)?smoker\b", v):
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
    # Batch-extract guard (2026-06-08): if the message contains sum-assured
    # signal words ("cover", "sum assured", "protection", "assured"), the money
    # amount belongs to sum_assured, not income. Reject so the batch scan
    # correctly assigns it to sum_assured later in ORDER. This prevents "50
    # lakhs cover" from being grabbed as income=5M when it means sum=5M.
    _sum_signals = ("cover", "sum assured", "assured", "protection")
    if any(s in v for s in _sum_signals):
        return False, "What's your annual income? You can say it in lakhs or crores."
    # Find the first number (with optional decimal)
    m = re.search(r"(\d+(?:\.\d+)?)", v)
    if not m:
        return False, "Could you say your income as a number? Like '12 lakhs' or '1.5 crores'."
    n = float(m.group(1))
    # Detect unit — LPM (Lakhs Per Month) must be checked BEFORE the generic
    # lakh/lac branch because "lpm" contains the substring "l" which the
    # " l" guard below would also match, giving the wrong 1× multiplier.
    # "25 LPM" → 25 × 100,000 × 12 = ₹3,000,000/year.
    if re.search(r"\blpm\b|l\.p\.m\.|lakhs?\s+per\s+month|lacs?\s+per\s+month", v):
        income = int(n * 100_000 * 12)
    elif "crore" in v or "cr" in v or "Cr" in v:
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


# BUG-D (Day-9 correctness fix): negators that flip a pre-existing condition
# mention into a healthy signal. "no diabetes or BP" must route healthy, not
# pre_existing. Multi-word negators are listed longest-first conceptually but
# matched by a regex window over the ~3 words preceding each condition mention.
_HEALTH_NEGATORS = (
    "no history of", "free from", "free of", "don't have", "dont have",
    "without", "not", "no",
)


def _window_has_negator(window: str) -> bool:
    """True if the ~3-word `window` contains a negator, matched on WORD boundaries.

    Word-boundary matching avoids the substring trap (substring-greedy-match
    defect): "not" must NOT match inside "cannot"/"nothing", and "no" must NOT
    match inside "now"/"none". Single-word negators are checked against the
    window's token set; multi-word negators ("no history of", "don't have")
    are checked as whole-phrase regex with boundaries.
    """
    tokens = window.split()
    token_set = set(tokens)
    for neg in _HEALTH_NEGATORS:
        if " " in neg:
            if re.search(r"\b" + re.escape(neg) + r"\b", window):
                return True
        elif neg in token_set:
            return True
    return False


def _is_negated(text: str, keyword: str) -> bool:
    """True only if EVERY occurrence of `keyword` in `text` is negated.

    A keyword occurrence counts as negated when one of the negator phrases in
    `_HEALTH_NEGATORS` appears (on word boundaries) within the ~3 words
    immediately preceding it. Per-occurrence semantics protect the documented
    case "I'm fine but I have diabetes" → the unnegated "diabetes" mention makes
    this return False, so the caller still routes to pre_existing.

    Returns False if the keyword is absent (caller checks membership first).
    """
    found_any = False
    for m in re.finditer(re.escape(keyword), text):
        found_any = True
        # Look at up to ~3 words immediately before this occurrence.
        prefix = text[:m.start()]
        window = " ".join(prefix.split()[-3:])
        if not _window_has_negator(window):
            return False  # an unnegated occurrence → not (fully) negated
    return found_any


def validate_health_status(value: str) -> Tuple[bool, Any]:
    v = _normalise(value).lower()
    if not v:
        return False, "Are you in good health, or do you have any pre-existing conditions?"
    # Strong pre-existing signals — explicit conditions or affirmations of having one.
    pre_words = {"pre-existing", "pre existing", "preexisting", "diabetes", "blood pressure",
                 "hypertension", "heart", "cancer", "asthma", "thyroid", "cholesterol",
                 "yes i have", "have a condition", "have conditions", "diabetic"}
    # B-LIVE-2 (Day 8 live-test fix): healthy signals — contextual phrases only.
    # NO standalone adjectives like "perfectly" (would match "perfectly comfortable
    # [with my diabetes]"). Day 8 live additions: "good health", "in good health",
    # "perfectly fine", "completely fine", "no conditions", "no issues", "no problems".
    healthy_words = {"healthy", "fit", "fine", "no condition", "no conditions", "no pre",
                     "nothing", "all good", "perfect health", "perfectly fine",
                     "completely fine", "no issues", "no issue", "no problems",
                     "no problem", "none", "good health", "in good health",
                     "no diabetes", "no bp", "no blood pressure"}
    # BUG-D: per-occurrence negation guard runs BEFORE the raw pre_words scan.
    # "no diabetes or BP" must NOT count as a pre_words hit. Only when EVERY
    # condition mention is negated do we treat it as healthy; if ANY condition
    # keyword appears unnegated ("I'm fine but I have diabetes"), route to
    # pre_existing.
    pre_hits = [w for w in pre_words if w in v]
    if pre_hits:
        if all(_is_negated(v, w) for w in pre_hits):
            return True, "healthy"
        return True, "pre_existing"
    if any(w in v for w in healthy_words):
        return True, "healthy"
    return False, "Sorry, I didn't catch that — are you in good health, or do you have a condition like diabetes or blood pressure?"


def validate_family_size(value: str) -> Tuple[bool, Any]:
    v = _normalise(value).lower()
    if not v:
        return False, "How many family members would you like covered, including yourself?"
    # T1 — number-word path (preferred when an exact count is signalled).
    word_to_num = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                   "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                   "myself": 1, "just me": 1, "alone": 1}
    for w, n in word_to_num.items():
        if w in v:
            return True, n
    # T2 — numeric digit path.
    m = re.search(r"\d+", v)
    if m:
        size = int(m.group())
        if size < 1 or size > 10:
            return False, "Family size needs to be between 1 and 10. Could you double-check?"
        return True, size
    # T3 — B-LIVE-3 (Day 8 live-test fix): family-relationship language without a
    # count. Acknowledge the family signal and ask explicitly for the number,
    # rather than the cold "Could you say the family size as a number?" reprompt.
    if any(w in v for w in _FAMILY_SHAPE_WORDS):
        return False, ("Got it, you'd like to cover your family. "
                       "How many people in total — including yourself? "
                       "You can just say a number like 'three' or '4'.")
    # BUG-C (Day-9 fix): graceful fallback when the answer carries neither a
    # count nor a family-shape word (e.g. an out-of-step reply). Acknowledge the
    # miss, gently restate the question, and give an example — warm tone to match
    # the T3 branch above, voice-friendly (no bullets or special characters).
    return False, ("Sorry, I didn't quite catch a number there. "
                   "How many people would you like covered in total, including yourself? "
                   "You can just say a number like 'three' or '4'.")


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
    # Batch-extract guard (2026-06-08): reject if the message carries age signals
    # ("years old", "age", "years of age") — prevents "45 years of age" from
    # being grabbed as sum_assured=45cr via the bare-number heuristic. Also
    # reject if income-specific keywords are present (same guard as income has
    # for sum signals — mutual exclusion).
    _age_signals = ("years old", "years of age", "year old", "yrs old", "aged ")
    _income_signals = ("income", "salary", "earn", "lpa", "per annum")
    if any(s in v for s in _age_signals):
        return False, "What sum assured would you like? You can say it in lakhs or crores."
    if any(s in v for s in _income_signals):
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
    # FIXED greeting — same every time (user directive: consistent brand experience).
    # Only subsequent questions vary. This is the voice the demo opens with.
    "name": "Welcome to InsureVoice! I'm your personal insurance advisor, and I'm absolutely thrilled to help you find the perfect cover — in seconds, not hours. To get started, may I know your good name?",
    "age": [
        "Wonderful to have you, {name}! Now, just so I can handpick the plans that are perfectly tailored for you — how old are you?",
        "Great to meet you, {name}! To find plans that fit you perfectly, I just need to know — how old are you?",
        "Thanks, {name}! Age matters quite a bit for plan options — would you mind sharing how old you are?",
        "Lovely, {name}! Now, so I can narrow down the best-fit plans for your stage of life — what's your age?",
        "Awesome, {name}! Quick one — how old are you? It helps me shortlist the most relevant options for you.",
        "Perfect, {name}! To make sure I show you age-appropriate plans — could you tell me your age?",
        "Nice to meet you, {name}! What's your current age? That helps me filter down to plans meant for you.",
    ],
    "smoker": [
        "Brilliant! One small thing — do you smoke at all? No judgement, it just helps me filter the right options for you.",
        "Got it! Quick question — are you a smoker? Totally no judgement, I just need it for accurate pricing.",
        "Great! Now, do you happen to smoke? It affects premiums a bit, so I want to get you honest numbers.",
        "Awesome! One more quick one — do you smoke or use tobacco at all? Just need it for the right plan match.",
        "Perfect! This one's simple — are you a smoker or non-smoker? It helps me show you the correct rates.",
        "Nice! Do you smoke at all? No judgement here — I just want to make sure the pricing I show you is spot on.",
    ],
    "income": [
        "Fantastic! To make sure the premiums I suggest are completely comfortable for you — what's your annual income? You can just say something like '10 lakhs' or '1.5 crore', whatever feels natural.",
        "Great! Now, roughly what's your annual income? Something like '12 lakhs' or '80 thousand a month' — just so I suggest plans that fit your budget comfortably.",
        "Awesome! What do you earn in a year, approximately? You can say it however feels natural — like '8 lakhs' or '1 crore'. I want premiums that won't pinch.",
        "Perfect! To recommend premiums that feel comfortable — could you share your annual income? Ballpark is fine, like '15 lakhs' or '2 crore'.",
        "Brilliant! What's your yearly income, roughly? Say it however you like — '10 lpa' or '1.2 crore' — I just want to keep things within your comfort zone.",
        "Love it! Now for budget-matching — what's your approximate annual income? Something like '20 lakhs' or '90 thousand monthly' works perfectly.",
    ],
    "health_status": [
        "You're doing great, {name}! Are you in good health overall, or do you have any pre-existing conditions — like diabetes or blood pressure — that I should factor into your plan?",
        "Wonderful, {name}! Quick health check — are you generally in good health, or is there anything like diabetes or BP that I should keep in mind?",
        "Almost there, {name}! Health-wise, are you all good — or do you have any conditions like diabetes, blood pressure, or thyroid I should factor in?",
        "Great progress, {name}! On the health front — are you fit and healthy overall, or any pre-existing conditions I should know about for accurate recommendations?",
        "Perfect, {name}! One important one — would you say you're in good health, or do you have any ongoing conditions like diabetes or hypertension?",
        "Doing brilliantly, {name}! Are you in good health overall? Or any pre-existing conditions — say diabetes or blood pressure — that I should account for?",
        "Nice one, {name}! Health-wise, anything I should know? Like diabetes, blood pressure, or any ongoing condition — or are you all clear?",
    ],
    "family_size": [
        "Perfect! Now the important part — who are we protecting here? Is it just yourself, or do you have a family — a spouse, kids, parents — that you'd love to cover under the same plan?",
        "Great! Who all are we covering today? Just you, or is there family — spouse, children, parents — you'd like protected too?",
        "Awesome! Tell me about your family — is this cover for you alone, or do you have dependants like a spouse, kids, or parents to include?",
        "Brilliant! Now, who's in the picture? Just yourself, or do you have a family — partner, kids, parents — that you want covered as well?",
        "Love it! Let's talk family — is it just you we're protecting, or do you have loved ones like a spouse, children, or parents to factor in?",
        "Wonderful! How many people are we covering? Just yourself, or a family — spouse, kids, parents — that needs protection too?",
    ],
    "coverage_goals": [
        "Love it! So what's the kind of protection that matters most to you right now? Are you thinking life cover for your family, health protection, building a savings corpus, securing your child's future, or planning a worry-free retirement? Just tell me what's on your mind!",
        "Great! What kind of insurance are you looking for? Life cover, health protection, child's education fund, retirement planning, or maybe a combination? Tell me what matters most.",
        "Awesome! What's your main goal here — protecting your family with life cover, getting health insurance, building savings, planning for retirement, or securing your child's future?",
        "Perfect! Let's talk goals — are you after life protection, health cover, a savings plan, retirement security, or your child's education fund? You can pick more than one!",
        "Brilliant! What type of cover speaks to you? Family life protection, health insurance, wealth building, child's future, retirement — or a mix of a few?",
        "Wonderful! What's on your mind protection-wise? Could be life cover, health, savings, retirement, child education — just tell me what feels important right now.",
        "Nice! So what are we solving for — life cover, health protection, child plan, retirement corpus, or savings? Feel free to mention multiple if you like.",
    ],
    "sum_assured": [
        "Almost there — you're one step away from your perfect plan! How much cover do you have in mind? Something like '50 lakhs' or '1 crore' is perfectly fine — just ballpark it.",
        "Last question! How much coverage are you thinking? A rough number works — like '50 lakhs' or '1 crore' — and I'll find plans in that range.",
        "We're at the finish line! What's your ideal cover amount? Just say something like '75 lakhs' or '2 crore' — ballpark is absolutely fine.",
        "One final thing — how much cover would make you feel secure? Could be '50 lakhs' or '1 crore' — whatever feels right for your situation.",
        "Almost done! What sum assured are you looking at? Something like '1 crore' or '50 lakhs' — just give me a rough figure and I'll match it.",
        "Last one, I promise! How much cover do you want? Ballpark it — '30 lakhs', '1 crore', whatever feels comfortable for your needs.",
    ],
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
# LLM Normalizer — preprocessor that interprets ambiguous user phrasing
# into a form the deterministic validators can parse. The LLM never touches
# the profile directly; it only rewrites the user's text. If it fails or
# times out, we fall through to the original message (graceful degradation).
# ---------------------------------------------------------------------------

_NORMALIZER_PROMPT = """You are a text normalizer for an insurance intake form. Your ONLY job is to rewrite the user's spoken response into a short, direct answer that a simple regex parser can understand.

RULES:
- Output ONLY the normalized answer. No explanations, no prefixes, no "Here is...".
- Keep the user's meaning exactly. Do NOT invent information they didn't provide.
- If they mention multiple fields (name, age, family, etc.), keep them as NATURAL SENTENCES separated by periods. Example: "My name is Atul. I am 45 years old. I have a family of 3. I am looking for term life and health."
- Do NOT use shorthand or compress into "name.age.income" format — keep full phrases so a regex parser can find keywords like "years old", "lpa", "family of".
- Normalize numbers: "mid-thirties" → "35 years old", "around thirty-ish" → "30 years old", "twenty-five LPA" → "25 lpa"
- Normalize negations: "I'm not really a smoker per se" → "non-smoker", "not a smoker" → "non-smoker"
- Normalize family: "it's me, my wife and one kid" → "family of 3", "just myself" → "1"
- Normalize health: "I'm doing pretty well, no issues" → "healthy", "got some BP" → "blood pressure"
- Normalize coverage: "something for life and maybe health too" → "term life and health"
- Keep names as-is (don't normalize names).
- If the answer is already clear and direct (like "35" or "no" or "12 lakhs"), output it UNCHANGED.

CONTEXT (what we already know about this customer):
{profile_context}

FIELD BEING ASKED: {expecting_field}

USER'S RESPONSE: {user_message}

NORMALIZED OUTPUT:"""

_normalizer_client = None


def _get_normalizer_client():
    global _normalizer_client
    if _normalizer_client is None:
        try:
            import os
            from google import genai
            _normalizer_client = genai.Client(
                vertexai=True,
                project=os.environ.get("GOOGLE_CLOUD_PROJECT", "voice-sales-agent"),
                location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
            )
        except Exception as e:
            _log.warning("LLM_NORMALIZER_INIT_FAILED: %s — falling through to raw message", e)
    return _normalizer_client


def _llm_normalize(message: str, expecting_field: str, profile: dict) -> str:
    """Normalize the user's message using the LLM for context understanding.

    Returns the normalized text, or the original message if the LLM fails.
    The FSM validators then parse the normalized output — never the raw LLM
    response directly into the profile.
    """
    if not message or not message.strip():
        return message

    client = _get_normalizer_client()
    if client is None:
        return message

    profile_lines = []
    for k, v in (profile or {}).items():
        if k.startswith("_"):
            continue
        profile_lines.append(f"  {k}: {v}")
    profile_context = "\n".join(profile_lines) if profile_lines else "  (nothing collected yet)"

    prompt = _NORMALIZER_PROMPT.format(
        profile_context=profile_context,
        expecting_field=expecting_field or "(first turn — asking name)",
        user_message=message.strip(),
    )

    try:
        from google.genai import types as _genai_types
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=prompt,
            config=_genai_types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=150,
            ),
        )
        normalized = (response.text or "").strip()
        if normalized and len(normalized) < 300:
            _log.info(
                "LLM_NORMALIZE expecting=%s raw=%r -> normalized=%r",
                expecting_field, message[:60], normalized[:60],
            )
            return normalized
        return message
    except Exception as e:
        _log.warning("LLM_NORMALIZE_FAILED expecting=%s error=%s — using raw message", expecting_field, e)
        return message


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

    # LLM Normalizer: interpret the user's message through the LLM for context
    # understanding BEFORE the FSM processes it. This handles ambiguous phrasing
    # ("mid-thirties", "not really a smoker per se", "me and my wife and kid")
    # that regex validators can't parse. The LLM rewrites into a direct form;
    # validators still own the actual extraction and validation.
    # Gate: only fire when USE_LLM_NORMALIZER env var is set (default in run_local_v5.sh
    # and production). Unit tests don't set this env var, so they test the FSM behavior
    # in isolation and expect specific validator error paths to fire. Also requires
    # _session_id (set by main.py) to confirm we're in a real session flow.
    import os as _os
    if _os.environ.get("USE_LLM_NORMALIZER") and intake_state.get("_session_id"):
        message = _llm_normalize(message, expecting, profile)

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

    # =========================================================================
    # BATCH EXTRACT (2026-06-08, Atul feedback): when a user dumps multiple
    # fields in one message ("I'm Atul, 45, family of 3, health+life"),
    # extract relevant CLAUSES per field using signal patterns, then validate
    # each clause independently. This avoids the full-message-to-validator
    # pitfall (cross-field digits, money-unit guards rejecting the whole msg).
    # =========================================================================
    _BATCH_SIGNALS = {
        "age": [
            re.compile(r"(?:i(?:'m| am)\s+)?(\d{2})\s*(?:years?\s*(?:old|of age)?|yrs?)", re.I),
            re.compile(r"age\s*(?:is\s*)?(\d{2})", re.I),
            re.compile(r"(?:i(?:'m| am)\s+)(\d{2})(?:\s|,|\.|\band\b|$)", re.I),
        ],
        "smoker": [
            # Must capture negation context so validate_smoker sees "not a smoker"
            # not just "smoker". Order matters: negation patterns first.
            re.compile(r"(not\s+a\s+smoker|non[\s-]?smoker|(?:i(?:'m|\s+am)?\s+)?(?:don'?t|do not|never)\s+smoke[sd]?|(?:i(?:'m|\s+am)?\s+)?not\s+(?:a\s+)?smoker)", re.I),
            re.compile(r"((?:i\s+)?smoke[rsd]?|(?:i\s+)?do\s+smoke)", re.I),
        ],
        "income": [
            re.compile(r"(?:income|salary|earn(?:ing)?s?|make|making)\s*(?:is\s*|of\s*)?(\d+(?:\.\d+)?\s*(?:lakh|lac|lpa|crore|cr|thousand|k)\w*)", re.I),
            re.compile(r"(\d+(?:\.\d+)?\s*lpa)", re.I),
            re.compile(r"(\d+(?:\.\d+)?\s*(?:lakh|lac|crore|cr|thousand)s?)\s*(?:income|salary|per\s*(?:annum|year))", re.I),
        ],
        "health_status": [
            re.compile(r"(good health|healthy|perfectly (?:fine|healthy)|no (?:conditions?|issues?|problems?)|in good health|fit)", re.I),
            re.compile(r"(diabetes|blood\s*pressure|hypertension|heart|cancer|asthma|thyroid|cholesterol|pre[\s-]?existing|diabetic)", re.I),
        ],
        "family_size": [
            # Specific patterns first (size is/of/with N) before the greedy generic
            re.compile(r"family\s*(?:member)?\s*size\s*(?:is|of|with)?\s*(\w+)", re.I),
            re.compile(r"family\s*(?:of|with)\s*(\w+)\s*(?:members?|people|persons?)?", re.I),
            re.compile(r"(\w+)\s*(?:family\s*)?members", re.I),
        ],
        "coverage_goals": [
            re.compile(r"((?:term\s*(?:life|insurance|plan)|life\s*(?:cover|insurance)|health(?:\s*(?:cover|insurance|plan))?|critical\s*illness|endowment|ulip|child\s*(?:plan|education)|pension|retirement|medical)(?:\s*(?:and|,|&)\s*(?:term\s*(?:life|insurance|plan)|life\s*(?:cover|insurance)|health(?:\s*(?:cover|insurance|plan))?|critical\s*illness|endowment|ulip|child\s*(?:plan|education)|pension|retirement|medical))*)", re.I),
        ],
        "sum_assured": [
            re.compile(r"(?:cover(?:age)?|sum\s*assured?|insured?\s*for|protection)\s*(?:of\s*)?(\d+(?:\.\d+)?\s*(?:lakh|lac|crore|cr|thousand)s?)", re.I),
            re.compile(r"(\d+(?:\.\d+)?\s*(?:lakh|lac|crore|cr|thousand)s?)\s*(?:cover(?:age)?|sum|protection)", re.I),
        ],
    }

    _msg_for_batch = message or ""
    _batch_trigger = (
        len(_msg_for_batch.strip()) > 35
        or sum(1 for sig in ("lakh", "crore", "health", "life", "family", "smoker",
                             "non-smoker", "cover", "income", "years old", "age",
                             "members", "salary", "earning")
               if sig in _msg_for_batch.lower()) >= 2
    )
    if _batch_trigger:
        _batch_found = []
        for _bf in ORDER:
            if _bf in profile:
                continue
            if _bf == expecting:
                continue
            _signals = _BATCH_SIGNALS.get(_bf)
            if not _signals:
                continue
            _clause = None
            for _pat in _signals:
                _m = _pat.search(_msg_for_batch)
                if _m:
                    _clause = _m.group(0).strip()
                    break
            if not _clause:
                continue
            _bv = VALIDATORS.get(_bf)
            if _bv is None:
                continue
            try:
                _bok, _bval = _bv(_clause)
                if _bok:
                    profile[_bf] = _bval
                    _batch_found.append(_bf)
            except Exception:
                pass
        if _batch_found:
            _log.info(
                "BATCH_EXTRACT session=%s fields_found=%s from_message=%r",
                intake_state.get("_session_id", "?")[:8] if intake_state.get("_session_id") else "?",
                _batch_found,
                _msg_for_batch[:80],
            )

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
    _variants = QUESTIONS[next_field]
    template = random.choice(_variants) if isinstance(_variants, list) else _variants
    try:
        # Use first name only for conversational warmth — "Abhishek" not
        # "Abhishek Sharma". Full name stays in profile for cards/records.
        _full = profile.get("name", "")
        _first = _full.split()[0] if _full else ""
        question = template.format(name=_first)
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

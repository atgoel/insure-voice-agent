"""
S3 — Follow-up state machine.

Handles two classes of post-recommendation turns deterministically (no LLM):
  1. "tell me about <product>" / "the second one" — resolve to a single product
     from TOP3_BY_SESSION, emit a templated voice summary.
  2. "start over" / "reset" / "different details" — clear all per-session state.

When neither applies, return None and let main.py fall through to the LLM.

Mojibake handling: applies _fix_mojibake() to all string fields read from
TOP3_BY_SESSION (defense-in-depth — main.py already sanitizes before write,
but L-004 says fix at egress AND we may have mixed sources later).

Logging: every detected intent emits ONE structured log line so the deployed-
regression test (E.1) can verify the fast-path fired.
"""

import re
import logging
from difflib import SequenceMatcher
from typing import Optional, Tuple

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Intent regex — case-insensitive on application
# ---------------------------------------------------------------------------

# Follow-up patterns. Must match a SUBSTRING of the user's message after
# .strip().lower(). Each pattern is tried in order.
_FOLLOWUP_PATTERNS = [
    re.compile(r"\btell me (more |a bit |a little )?about\b"),
    re.compile(r"\bwhat (about|is|are) (the|a)?\s*\w"),
    re.compile(r"\bdescribe\b"),
    re.compile(r"\bmore (info|information|details) (on|about)\b"),
    re.compile(r"\b(premium|cost|price|charge) for\b"),
    re.compile(r"\b(eligibility|cover|coverage) (for|of)\b"),
]

# Ordinal references — "first one", "the second", "third option", "1st product"
_ORDINAL_PATTERNS = [
    (re.compile(r"\b(the\s+)?(1st|first)\s+(one|option|recommendation|card|plan|product|choice)\b|\bthe\s+(1st|first)\s*$"), 0),
    (re.compile(r"\b(the\s+)?(2nd|second)\s+(one|option|recommendation|card|plan|product|choice)\b|\bthe\s+(2nd|second)\s*$"), 1),
    (re.compile(r"\b(the\s+)?(3rd|third|last)\s+(one|option|recommendation|card|plan|product|choice)\b|\bthe\s+(3rd|third|last)\s*$"), 2),
]

# Reset patterns — must work mid-intake too, so check these very early.
# v2 NOTE: tightened per Fix 4 — looser "let me try with different/new/other"
# pattern was REMOVED to avoid false positives on intake clarifications like
# "let me start with different income".
_RESET_PATTERNS = [
    re.compile(r"\b(start over|reset|begin again|restart|start (from )?(scratch|over))\b"),
]

# Compare patterns — detected so we can park gracefully (not handled deterministically).
_COMPARE_PATTERNS = [
    re.compile(r"\bcompare\b"),
    re.compile(r"\b(vs\.?|versus)\b"),
    re.compile(r"\bdifference between\b"),
    re.compile(r"\bwhich is (better|best)\b"),
]

# Fuzzy match threshold (SequenceMatcher.ratio()). 0.6 chosen empirically:
#   "lifeguard" vs "LifeGuard Plus"            -> 0.65 (PASS)
#   "life guard"  vs "LifeGuard Plus"          -> 0.70 (PASS)
#   "futuresecure" vs "Future Secure"          -> 0.96 (PASS)
#   "term life"  vs "FamilyProtect 3 Crore"    -> 0.32 (REJECT, correct)
#   "savings"    vs "SavingsPlus"              -> 0.80 (PASS)
#   "ulip"       vs "WealthShield ULIP"        -> 0.42 (REJECT — substring match catches this)
# Substring match runs BEFORE fuzzy to handle the ULIP case and similar.
_FUZZY_THRESHOLD = 0.60


_MOJIBAKE_MARKERS = ("â", "Ã", "Â", "â€", "â‚")


def _fix_mojibake(s) -> str:
    if not isinstance(s, str) or not s:
        return s
    if not any(m in s for m in _MOJIBAKE_MARKERS):
        return s
    try:
        return s.encode("cp1252", errors="ignore").decode("utf-8", errors="ignore")
    except Exception:
        pass
    return s



# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_reset_intent(message: str) -> bool:
    """Detect 'start over' / 'reset' / 'restart'. Runs pre-intake."""
    if not message:
        return False
    m = message.strip().lower()
    return any(p.search(m) for p in _RESET_PATTERNS)


def detect_followup_intent(message: str) -> Optional[str]:
    """Classify a post-recommendation message.

    Returns one of:
        "ordinal"  — message contains an ordinal reference (first/second/third)
        "named"    — message contains a follow-up trigger phrase ("tell me about ...")
        "compare"  — comparison intent detected (parked — caller falls back to LLM)
        None       — no follow-up intent detected
    """
    if not message:
        return None
    m = message.strip().lower()

    # Ordinal beats named — "tell me about the second one" should resolve via index.
    for pat, _idx in _ORDINAL_PATTERNS:
        if pat.search(m):
            return "ordinal"

    if any(p.search(m) for p in _COMPARE_PATTERNS):
        return "compare"

    if any(p.search(m) for p in _FOLLOWUP_PATTERNS):
        return "named"

    return None


def resolve_ordinal_index(message: str) -> Optional[int]:
    """Return 0/1/2 if message contains a recognizable ordinal, else None."""
    if not message:
        return None
    m = message.strip().lower()
    for pat, idx in _ORDINAL_PATTERNS:
        if pat.search(m):
            return idx
    return None


def _shortest_token_match(name_lower: str, message_lower: str) -> bool:
    """Return True if any 4+ char token of name appears in message."""
    tokens = [t for t in re.findall(r"\b\w+\b", name_lower) if len(t) >= 4]
    return any(t in message_lower for t in tokens)


def match_product_by_name(message: str, top3: list) -> Tuple[Optional[dict], Optional[str]]:
    """Find which top3 product the user is naming.

    Strategy (in order):
      1. Substring match (case-insensitive) — name appears in message OR vice-versa.
         Handles 'ULIP' inside 'WealthShield ULIP' and 'LifeGuard' inside the full name.
      2. Fuzzy match via SequenceMatcher.ratio() against each product name.
         Threshold 0.60. Best-scoring product wins, tiebreak = lower index (rank).

    Returns:
        (matched_product_dict, "substring" | "fuzzy") — or (None, None) if no match.
    """
    if not message or not top3:
        return None, None
    m_lower = message.strip().lower()

    # Step 1 — substring match (faster, more precise for short user utterances).
    substring_hits = []
    for prod in top3:
        if not isinstance(prod, dict):
            continue
        name = (prod.get("name") or "").strip()
        if not name:
            continue
        name_lower = name.lower()
        # Either direction — "lifeguard" in "Tell me about LifeGuard Plus" OR
        # name fully in message — both are valid hits.
        if name_lower in m_lower or _shortest_token_match(name_lower, m_lower):
            substring_hits.append(prod)
    if len(substring_hits) == 1:
        return substring_hits[0], "substring"
    if len(substring_hits) > 1:
        # Multiple substring hits — pick the one with the longest name (most specific).
        best = max(substring_hits, key=lambda p: len(p.get("name") or ""))
        return best, "substring"

    # Step 2 — fuzzy match.
    best_prod = None
    best_score = 0.0
    for prod in top3:
        if not isinstance(prod, dict):
            continue
        name = (prod.get("name") or "").strip().lower()
        if not name:
            continue
        score = SequenceMatcher(None, m_lower, name).ratio()
        # Also try ratio against just the message's product-like tokens (>=4 chars).
        for token in re.findall(r"\b\w{4,}\b", m_lower):
            score = max(score, SequenceMatcher(None, token, name).ratio())
        if score > best_score:
            best_score = score
            best_prod = prod
    if best_prod is not None and best_score >= _FUZZY_THRESHOLD:
        return best_prod, "fuzzy"
    return None, None


def no_match_voice_text() -> str:
    """Used when follow-up intent is detected but no product can be resolved."""
    return ("I want to make sure I'm telling you about the right one — "
            "which option would you like, the first, second, or third?")


def _no_match_voice_text() -> str:
    """Internal alias used by build_voice_text fallback path."""
    return no_match_voice_text()


def reset_voice_text() -> str:
    """Canned response after a reset. Keeps the agent friendly + restarts intake."""
    return ("No problem, let's start fresh. What's your name?")


def build_voice_text(product) -> str:
    """Build a deterministic single-product voice summary.

    Style mirrors main.py:403-448 (C.5b deterministic template). Target <100
    words for TTS quality. Null-safe: every field access is via .get() with
    a graceful fallback. Mojibake-fixed at egress per L-004.

    Includes (when available): name, key_feature, premium range, age range,
    smoker eligibility, sum assured range. Skips fields cleanly when missing.

    v2 NOTE (Fix 2): the guard explicitly checks for missing/empty `name`
    so that build_voice_text({}) and build_voice_text({'description':'foo'})
    both fall back cleanly per AC8.
    """
    if not isinstance(product, dict) or not product.get("name"):
        return _no_match_voice_text()

    name = _fix_mojibake(product.get("name") or "this product")
    key_feature = _fix_mojibake(product.get("key_feature") or "")
    pmin = product.get("premium_min_monthly")
    pmax = product.get("premium_max_monthly")
    min_age = product.get("min_age")
    max_age = product.get("max_age")
    smoker_ok = product.get("smoker_eligible")
    sum_max = product.get("max_sum_assured")
    desc = _fix_mojibake(product.get("description") or "")

    parts = [f"Here's a bit more on {name}."]

    if key_feature:
        parts.append(f"Its key feature is {key_feature}.")
    elif desc:
        # Trim description to ~25 words to keep total under 100.
        words = desc.split()
        parts.append(" ".join(words[:25]) + ("..." if len(words) > 25 else ""))

    # Premium
    if pmin and pmax:
        try:
            parts.append(f"Premium runs from {int(pmin):,} to {int(pmax):,} INR per month.")
        except (TypeError, ValueError):
            pass
    elif pmin:
        try:
            parts.append(f"Premium starts at {int(pmin):,} INR per month.")
        except (TypeError, ValueError):
            pass

    # Eligibility
    if min_age is not None and max_age is not None:
        try:
            parts.append(f"Open to ages {int(min_age)} through {int(max_age)}.")
        except (TypeError, ValueError):
            pass

    # Smoker — explicit interpretation (Bug 14: smoker_eligible:true means BOTH allowed).
    if smoker_ok is True:
        parts.append("Open to both smokers and non-smokers.")
    elif smoker_ok is False:
        parts.append("Open to non-smokers only.")
    # If None/missing, stay silent — don't fabricate.

    # Sum assured
    if sum_max:
        try:
            sum_max_int = int(sum_max)
            if sum_max_int >= 10_000_000:
                parts.append(f"You can go up to {sum_max_int / 10_000_000:.1f} crore INR in sum assured.")
            elif sum_max_int >= 100_000:
                parts.append(f"You can go up to {sum_max_int / 100_000:.0f} lakh INR in sum assured.")
        except (TypeError, ValueError):
            pass

    parts.append("Want to hear about another option, or shall we go with this one?")
    text = " ".join(parts)
    return text


def dispatch_followup(message: str, session_id: str) -> Optional[dict]:
    """Convenience top-level entrypoint for unit testing.

    Reads TOP3_BY_SESSION at call time. Returns None when nothing matches
    (caller should fall through to LLM). Returns a dict with keys:
        {"voice_text": str, "intent": "ordinal"|"named"|"compare"|"no_match",
         "method": "ordinal"|"substring"|"fuzzy"|None,
         "product_name": str|None}

    main.py does NOT use this wrapper — it composes the same primitives
    inline so it can interleave logging in the SPEC-required format. This
    function is here for unit-test convenience.
    """
    try:
        from shared_state import TOP3_BY_SESSION as _TBS
    except Exception:
        _TBS = {}
    top3 = _TBS.get(session_id) or []
    intent = detect_followup_intent(message)
    if intent is None:
        return None
    if intent in ("ordinal", "named") and not top3:
        return None
    if intent == "compare":
        return None  # caller falls through to LLM
    matched = None
    method = None
    if intent == "ordinal":
        idx = resolve_ordinal_index(message)
        if idx is not None and 0 <= idx < len(top3):
            matched = top3[idx]
            method = "ordinal"
    else:  # named
        matched, method = match_product_by_name(message, top3)
    if matched is not None:
        return {
            "voice_text": build_voice_text(matched),
            "intent": intent,
            "method": method,
            "product_name": matched.get("name"),
        }
    return {
        "voice_text": no_match_voice_text(),
        "intent": "no_match",
        "method": None,
        "product_name": None,
    }


# ===========================================================================
# T3 — Farewell flow (Bug D + Bug I deterministic). Per SPEC v2 §4.
# Anchored patterns require FULL utterance match (not substring).
# ===========================================================================

_DONE_PATTERNS_ANCHORED = (
    re.compile(r"^\s*no\s+thanks?\s*$", re.IGNORECASE),
    re.compile(r"^\s*no\s+thank\s+you\s*$", re.IGNORECASE),
    re.compile(r"^\s*no\s+more\s*$", re.IGNORECASE),
    re.compile(r"^\s*no\s+more\s+thanks?\s*$", re.IGNORECASE),
    re.compile(r"^\s*(no\s+)?not?\s+(right\s+)?now\s*$", re.IGNORECASE),
    re.compile(r"^\s*nope\s*$", re.IGNORECASE),
    re.compile(r"^\s*we'?re\s+done\s*$", re.IGNORECASE),
    re.compile(r"^\s*we\s+are\s+done\s*$", re.IGNORECASE),
    re.compile(r"^\s*i'?m\s+done\s*$", re.IGNORECASE),
    re.compile(r"^\s*i\s+am\s+done\s*$", re.IGNORECASE),
    re.compile(r"^\s*i'?m\s+good\s*$", re.IGNORECASE),
    re.compile(r"^\s*i\s+am\s+good\s*$", re.IGNORECASE),
    re.compile(r"^\s*i'?m\s+all\s+set\s*$", re.IGNORECASE),
    re.compile(r"^\s*all\s+set\s*$", re.IGNORECASE),
    re.compile(r"^\s*that'?s\s+all\s*$", re.IGNORECASE),
    re.compile(r"^\s*that\s+is\s+all\s*$", re.IGNORECASE),
    re.compile(r"^\s*nothing\s+(else|more)\s*$", re.IGNORECASE),
    re.compile(r"^\s*okay\s+bye\s*$", re.IGNORECASE),
    re.compile(r"^\s*ok\s+bye\s*$", re.IGNORECASE),
    re.compile(r"^\s*bye\s*$", re.IGNORECASE),
    re.compile(r"^\s*goodbye\s*$", re.IGNORECASE),
)


def is_done_intent(message: str) -> bool:
    """Detect done/farewell intent. Anchored full-utterance match.

    Per T3 SPEC v2 §4.2 (Fix #1 + Fix #4): patterns are anchored ^...$ to
    avoid false positives on substantive utterances containing
    'I'm good with X' or 'that's all I know about Y'. Comma-stripped to
    handle 'no, thank you' as 'no thank you'.
    """
    if not message:
        return False
    cleaned = message.strip().rstrip(".!?")
    cleaned = cleaned.replace(",", " ")
    cleaned = " ".join(cleaned.split())
    return any(p.match(cleaned) for p in _DONE_PATTERNS_ANCHORED)


CANONICAL_FAREWELL_TEXT = (
    "Okay, I understand. Thanks for chatting with InsureVoice today. "
    "If you change your mind or want to explore other options later, just say so. "
    "Have a great day!"
)


def farewell_voice_text() -> str:
    """Canonical farewell — returns frozen module constant. Determinism > variety for tests."""
    return CANONICAL_FAREWELL_TEXT


# ===========================================================================
# T3 — Contact capture (5-state FSM). Per SPEC v2 §5.
# State enum: NONE | ASKED | AWAITING_EMAIL | CAPTURED | DECLINED
# ===========================================================================

_YES_PATTERNS = [
    re.compile(r"^\s*yes\b", re.IGNORECASE),
    re.compile(r"^\s*yeah\b", re.IGNORECASE),
    re.compile(r"^\s*yep\b", re.IGNORECASE),
    re.compile(r"^\s*sure\b", re.IGNORECASE),
    re.compile(r"^\s*(ok|okay|kay)\b", re.IGNORECASE),
    re.compile(r"\bplease\s+do\b", re.IGNORECASE),
    re.compile(r"\bsend\s+(it|them|me)\b", re.IGNORECASE),
    re.compile(r"\bgo\s+ahead\b", re.IGNORECASE),
    re.compile(r"\babsolutely\b", re.IGNORECASE),
    re.compile(r"\bthat\s+would\s+be\s+(great|nice|helpful)\b", re.IGNORECASE),
]

# Anchored — for narrow no-only intent, NOT done-intent (which has its own set).
_NO_PATTERNS_ANCHORED = [
    re.compile(r"^\s*no\s*$", re.IGNORECASE),
    re.compile(r"^\s*nope\s*$", re.IGNORECASE),
    re.compile(r"^\s*nah\s*$", re.IGNORECASE),
    re.compile(r"^\s*don'?t\s+(bother|need)\s*$", re.IGNORECASE),
    re.compile(r"^\s*skip\s+(it|the\s+email)\s*$", re.IGNORECASE),
    re.compile(r"^\s*not\s+(necessary|needed)\s*$", re.IGNORECASE),
]


def is_yes_intent(message: str) -> bool:
    """Detect yes intent for email-capture ASK state. Mixes anchored + unanchored
    patterns by design — uses .search() not .match()."""
    if not message:
        return False
    cleaned = message.strip().rstrip(".!?").replace(",", " ")
    cleaned = " ".join(cleaned.split())
    return any(p.search(cleaned) for p in _YES_PATTERNS)


def is_no_intent(message: str) -> bool:
    """Detect bare-no intent. Note: 'no thanks' is is_done_intent's job."""
    if not message:
        return False
    cleaned = message.strip().rstrip(".!?").replace(",", " ")
    cleaned = " ".join(cleaned.split())
    return any(p.match(cleaned) for p in _NO_PATTERNS_ANCHORED)


EMAIL_REGEX = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def extract_email(text):
    """Return first email-shaped substring in text, or None.

    Liberal regex per SPEC v2 §5.4 — strips common trailing punctuation
    (".,;:!?'\"") and validates that local + domain (with at least one dot)
    are present.
    """
    if not text:
        return None
    m = EMAIL_REGEX.search(text.strip())
    if not m:
        return None
    candidate = m.group(0).rstrip(".,;:!?'\"")
    if "@" not in candidate:
        return None
    local, _, domain = candidate.rpartition("@")
    if not local or "." not in domain:
        return None
    return candidate


def _email_domain(email):
    """Return domain part for PII-safe logging. Returns 'unknown' on parse failure.

    Defense-in-depth: even if Implementer accidentally passes the full email
    to a log line, this helper wraps it. The downstream audit log
    (_write_audit_log) ALREADY redacts PII per Constitution §IV — but
    contact-capture is new code path; explicit domain extraction at this
    layer prevents accidental full-email logging.
    """
    if not email or "@" not in email:
        return "unknown"
    return email.rsplit("@", 1)[1]


def contact_ask_suffix() -> str:
    return " Want me to email these to you?"


def contact_yes_voice_text() -> str:
    return "Sure! What's your email address?"


def contact_invalid_voice_text() -> str:
    return ("Hmm, I didn't catch a valid email — could you say it again, "
            "like 'name at example dot com'?")


def contact_giveup_voice_text() -> str:
    return ("No problem — I'll skip the email for now. Anything else I can help with?")


def contact_captured_voice_text(email: str) -> str:
    # PII NOTE: full email IS echoed in voice/response (user-facing confirmation
    # is required). Logs MUST NEVER contain the full email — only the domain.
    # See _email_domain() and the T3_CONTACT_CAPTURED log line in main.py.
    # Constitution §IV/§V — audit log MUST omit captured email entirely.
    return f"Got it. I've saved {email} — you'll get the details shortly. Anything else?"

"""
B4 — Flash-Lite Intent Classifier (separate sub-agent)
======================================================
Classifies follow-up turns (post-recommendation) into one of four intents:
    NAMED_PRODUCT    — user references a top3 product by name
    ORDINAL          — user references "first / second / third"
    POLICY_QUESTION  — generic concept question (routes to free-form sub-agent per D4)
    AMBIGUOUS        — confidence too low or pushback / clarification needed

ARCHITECTURE (per D3 lock + D10 Runner spike):
    - This is its OWN LlmAgent instance (intent_classifier_agent), NOT a tool on root_agent.
    - It has its OWN before_model_callback (_force_classifier_tool) — root agent's
      _force_tool_call_mid_pipeline (agent_definition.py:428) is UNTOUCHED.
    - Module-level Runner with separate app_name="insure-voice-classifier" — sessions
      are isolated from root by the (app_name, user_id, session_id) tuple, so
      classifier function_response events do NOT leak into root's mid-pipeline
      state machine.

LESSONS APPLIED:
    - L-001 (mechanical control): tool_config.mode="ANY" forces single tool call;
      prompt directives are advisory only.
    - L-002 (structured data threading): top3_ids flow caller → session.state →
      callback rewrite of user message. LLM never authors top3_ids.

PUBLIC API:
    classify_intent_async(session_id, user_message, top3_ids, user_id="voice-user")
        → {"target_product_id": str, "intent": str, "confidence": float,
           "clarification_question": str, "out_of_scope": bool}
"""

from __future__ import annotations

import logging as _log
import os
from typing import Any, Optional

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest
from google.adk.runners import Runner
from google.adk.tools import FunctionTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types as genai_types

# ---------------------------------------------------------------------------
# Constants — confidence thresholds + feature flag (per B4 SPEC v2 §5)
# ---------------------------------------------------------------------------

CONFIDENCE_THRESHOLD: float = 0.7
"""Threshold above which the classifier's chosen target_product_id is acted on
confidently. Below CONFIDENCE_FORCE_CLARIFY_BAND[0] → escalate via NO_MATCH."""

CONFIDENCE_FORCE_CLARIFY_BAND: tuple[float, float] = (0.5, 0.7)
"""[lo, hi) band where we ALWAYS clarify regardless of intent. Mitigates
shipping-without-sweep risk per B4 SPEC v2 §5."""

# Feature flag — wraps classifier dispatch in main.py. Off by default until
# AC-B4.6.5 (C.5 callback isolation) confirmed post-deploy.
USE_LLM_INTENT_CLASSIFIER_FLAG: bool = (
    os.getenv("USE_LLM_INTENT_CLASSIFIER", "false").strip().lower() == "true"
)

# Separate ADK app namespace — keeps classifier session events isolated from
# root_agent's session events so _force_tool_call_mid_pipeline (C.5) cannot
# observe classifier function_response and mis-trigger. See D10 spike result.
CLASSIFIER_APP_NAME: str = "insure-voice-classifier"

# ---------------------------------------------------------------------------
# Tool: classify_followup_intent
# ---------------------------------------------------------------------------
# The function body is a passthrough — the LLM authors all four return fields
# under tool_config.mode=ANY, so the SDK serializes the LLM's structured output
# into the function_response payload directly. This mirrors the v4 pattern of
# "tool exists to constrain decoding, not to compute" (see also AgentTool wrap
# at agent_definition.py:563).


def classify_followup_intent(
    target_product_id: str,
    intent: str,
    confidence: float,
    clarification_question: str = "",
    out_of_scope: bool = False,
    tool_context: Optional[ToolContext] = None,
) -> dict:
    """Classify a customer follow-up message into one of four intents.

    Args:
        target_product_id: Resolved product_id from the top3 candidates list,
            or "NONE" if intent is POLICY_QUESTION / AMBIGUOUS / out_of_scope.
        intent: One of "NAMED_PRODUCT", "ORDINAL", "POLICY_QUESTION", "AMBIGUOUS".
        confidence: Float in [0, 1]. Calibrated at 0.7 confident-route threshold,
            [0.5, 0.7) force-clarify band, < 0.5 escalation ladder.
        clarification_question: Voice-ready clarification text when confidence
            is in the force-clarify band or intent is AMBIGUOUS. Empty string
            when intent is fully resolved.
        out_of_scope: True for utterances unrelated to insurance products
            (e.g. weather, greetings without follow-up content).
        tool_context: ADK injected — unused but signature required.

    Returns:
        Dict with the same five fields. Pass-through — LLM authors values
        under tool_config.mode=ANY constraint.
    """
    return {
        "target_product_id": str(target_product_id or "NONE"),
        "intent": str(intent or "AMBIGUOUS"),
        "confidence": float(confidence) if isinstance(confidence, (int, float)) else 0.0,
        "clarification_question": str(clarification_question or ""),
        "out_of_scope": bool(out_of_scope),
    }


# ---------------------------------------------------------------------------
# Callback: _force_classifier_tool
# ---------------------------------------------------------------------------
# Mirrors agent_definition.py:428-502 (_force_tool_call_mid_pipeline) signature.
# Two responsibilities:
#   1. Constrain decoding to the classify_followup_intent tool via
#      tool_config.mode="ANY" — guarantees structured output.
#   2. Prepend "Available products: <ids>\n" to the LAST user-role text Part.
#      Per B4 SPEC v2 §3.3 (C4 reviewer fix): inject into user message NOT
#      system_instruction (system_instruction may be None → += TypeError;
#      flash-lite has recency bias on user-message-prepended IDs).


def _force_classifier_tool(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> None:
    # LOOP GUARD (2026-06-06): if classify_followup_intent has ALREADY produced a
    # function_response in this request's contents, classification is done —
    # switch to AUTO so the model emits a final text response and the classifier
    # runner terminates. Without this, mode=ANY forces the tool on EVERY model
    # turn → the same unbounded-force loop as the root agent (lesson L-001). This
    # is the classifier-side (hazard #2) counterpart to the root callback latch.
    try:
        for _c in (getattr(llm_request, "contents", None) or []):
            for _p in (getattr(_c, "parts", None) or []):
                _fr = getattr(_p, "function_response", None)
                if _fr is not None and getattr(_fr, "name", None) == "classify_followup_intent":
                    if llm_request.config is None:
                        llm_request.config = genai_types.GenerateContentConfig()
                    llm_request.config.tool_config = genai_types.ToolConfig(
                        function_calling_config=genai_types.FunctionCallingConfig(mode="AUTO")
                    )
                    return
    except Exception:
        pass

    # 1. Force tool_config.mode=ANY on the classify_followup_intent tool.
    #    Use exact pattern from agent_definition.py:487-496.
    try:
        if llm_request.config is None:
            llm_request.config = genai_types.GenerateContentConfig()
        llm_request.config.tool_config = genai_types.ToolConfig(
            function_calling_config=genai_types.FunctionCallingConfig(
                mode="ANY",
                allowed_function_names=["classify_followup_intent"],
            )
        )
    except Exception as _e:
        try:
            _log.getLogger(__name__).error("CLF_FORCE_FAIL %s", _e)
        except Exception:
            pass

    # 2. Prepend top3 IDs to the last user-role text Part (recency-bias safe).
    #    Caller writes top3_ids_for_session into session.state IMMEDIATELY
    #    before Runner.run_async. Defensive: skip + log warning if not found.
    top3_ids: list[str] = []
    try:
        _state = getattr(callback_context, "state", None)
        if _state is not None:
            # ADK State can be dict-like or have .get()
            try:
                top3_ids = list(_state.get("top3_ids_for_session", []) or [])
            except Exception:
                # Fallback for State objects without .get()
                try:
                    top3_ids = list(_state["top3_ids_for_session"] or [])
                except Exception:
                    top3_ids = []
    except Exception:
        top3_ids = []

    if not top3_ids:
        try:
            _log.getLogger(__name__).warning(
                "CLF_NO_TOP3 callback received empty top3_ids_for_session — proceeding without injection"
            )
        except Exception:
            pass
        return

    contents = getattr(llm_request, "contents", None) or []
    last_user_part = None
    for content in reversed(contents):
        if getattr(content, "role", None) == "user":
            parts = getattr(content, "parts", None) or []
            for p in parts:
                if getattr(p, "text", None):
                    last_user_part = p
                    break
            if last_user_part is not None:
                break

    if last_user_part is None:
        try:
            _log.getLogger(__name__).warning(
                "CLF_NO_USER_PART no user-role text part found in llm_request.contents — skipping top3 injection"
            )
        except Exception:
            pass
        return

    try:
        _ids_csv = ", ".join(str(x) for x in top3_ids)
        last_user_part.text = (
            f"Available products: [{_ids_csv}]\n{last_user_part.text}"
        )
    except Exception as _e:
        try:
            _log.getLogger(__name__).error("CLF_REWRITE_FAIL %s", _e)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# System instruction (inline — small enough to keep with module)
# ---------------------------------------------------------------------------

_CLASSIFIER_INSTRUCTION = """You are an insurance follow-up intent classifier.

You receive ONE user message that came AFTER the agent recommended a top-3 list
of insurance products. The user message is prepended with "Available products: [...]"
listing the product_ids of those top-3 candidates.

You MUST call the classify_followup_intent tool exactly ONCE with these fields:

  intent: one of
    - "NAMED_PRODUCT"   — user names a specific product (exact, substring, or typo)
    - "ORDINAL"         — user says "first / second / third / 1st / 2nd / 3rd / last"
    - "POLICY_QUESTION" — generic concept question, comparisons, or unrelated to a
                          single product ("what is critical illness", "compare these")
    - "AMBIGUOUS"       — match unclear, contradictory, or pushback (e.g. "no I asked
                          for ULIP not term"); also used when confidence is low

  target_product_id:
    - For NAMED_PRODUCT and ORDINAL — the product_id from the Available products list
      that the user is referencing. MUST be ∈ Available products.
    - For POLICY_QUESTION, AMBIGUOUS, out_of_scope=true — "NONE".

  confidence: float in [0, 1].
    - Use 0.95+ for exact name match.
    - Use 0.8-0.9 for substring or first-word match where it uniquely identifies.
    - Use 0.7-0.8 for fuzzy / typo match.
    - Use 0.5-0.7 when match is plausible but ambiguous (you SHOULD provide
      clarification_question — caller will force-clarify).
    - Use < 0.5 when no defensible match exists.

  clarification_question: voice-ready text (one sentence, ≤25 words) asking the
    user to disambiguate. Use when intent is AMBIGUOUS or confidence < 0.7.
    Empty string otherwise.

  out_of_scope: true for utterances unrelated to insurance (weather, greetings
    with no follow-up content, off-topic chitchat). target_product_id MUST be
    "NONE" when out_of_scope is true.

RULES:
  - Comparison utterances ("HealthFirst vs SecureLife", "what's the difference",
    "compare these") → intent=POLICY_QUESTION, target_product_id="NONE".
  - Ordinals override names: "the first one" is ORDINAL even if it contains a
    word that could be a product name fragment.
  - When user pushes back ("no I asked for ULIP not term") and the wrong product
    type is in top3 → intent=AMBIGUOUS, target_product_id="NONE", suggest
    clarification.
  - target_product_id MUST be exactly one of the Available products. If you
    cannot pick one, set intent=AMBIGUOUS and target_product_id="NONE".
"""


# ---------------------------------------------------------------------------
# LlmAgent — separate sub-agent, NOT registered as a tool on root_agent
# ---------------------------------------------------------------------------

intent_classifier_agent: LlmAgent = LlmAgent(
    model="gemini-2.5-flash-lite",
    name="intent_classifier",
    description=(
        "Classifies post-recommendation follow-up turns into NAMED_PRODUCT / "
        "ORDINAL / POLICY_QUESTION / AMBIGUOUS. Single tool call under "
        "tool_config.mode=ANY; structured output only."
    ),
    instruction=_CLASSIFIER_INSTRUCTION,
    # B4 SPEC v2 §3.2 — temp 0.1 (lower than root's 0.25) for classification
    # determinism. Top3 IDs ride on the user message via callback rewrite.
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=0.1,
        top_p=0.7,
        max_output_tokens=300,
    ),
    # Mechanical enforcement (L-001) — separate from root_agent's C.5 callback.
    before_model_callback=_force_classifier_tool,
    tools=[FunctionTool(classify_followup_intent)],
)


# ---------------------------------------------------------------------------
# Module-level Runner — separate app_name (D10 lock)
# ---------------------------------------------------------------------------
# Constructed lazily by get_classifier_runner() so test imports don't require
# the InMemorySessionService singleton to exist at import time. Production
# main.py path constructs this once via init_classifier_runner().

_classifier_runner: Optional[Runner] = None
_session_service_ref: Any = None  # set by init_classifier_runner


def init_classifier_runner(session_service: Any) -> Runner:
    """Initialize the module-level classifier Runner.

    Called ONCE from main.py at module import time, sharing main.py's
    _session_service singleton. Sessions are isolated from root_agent's
    by the separate app_name="insure-voice-classifier" — see D10 spike.

    Args:
        session_service: The same InMemorySessionService instance main.py
            uses for the root_agent Runner.

    Returns:
        The configured Runner. Also stored as module-level _classifier_runner.
    """
    global _classifier_runner, _session_service_ref
    _session_service_ref = session_service
    _classifier_runner = Runner(
        agent=intent_classifier_agent,
        app_name=CLASSIFIER_APP_NAME,
        session_service=session_service,
    )
    return _classifier_runner


# ---------------------------------------------------------------------------
# Helper: extract classification from event stream
# ---------------------------------------------------------------------------
# Pattern mirrors main.py:701-714 — walk events, find function_response with
# name="classify_followup_intent", read .response dict.


def _extract_classifier_result(events: list) -> Optional[dict]:
    for ev in events:
        content = getattr(ev, "content", None)
        if not content:
            continue
        parts = getattr(content, "parts", None) or []
        for p in parts:
            fr = getattr(p, "function_response", None)
            if fr is None:
                continue
            if getattr(fr, "name", None) != "classify_followup_intent":
                continue
            resp = getattr(fr, "response", None)
            # ADK may wrap the response under {"result": {...}} for FunctionTool
            # passthroughs. Unwrap if present, else return as-is.
            if isinstance(resp, dict):
                inner = resp.get("result") if isinstance(resp.get("result"), dict) else resp
                if isinstance(inner, dict):
                    return inner
            return resp if isinstance(resp, dict) else None
    return None


# ---------------------------------------------------------------------------
# Public API: classify_intent_async
# ---------------------------------------------------------------------------


async def classify_intent_async(
    session_id: str,
    user_message: str,
    top3_ids: list[str],
    user_id: str = "voice-user",
) -> Optional[dict]:
    """Run the classifier sub-agent and return the structured classification.

    Args:
        session_id: Root session_id from the /invoke request. Classifier runs
            under f"clf-{session_id}" in a SEPARATE app namespace so events
            do not pollute root_agent's session log.
        user_message: The raw user message (single follow-up turn).
        top3_ids: List of product_ids from TOP3_BY_SESSION[session_id]. Written
            into session.state["top3_ids_for_session"] before run_async so the
            callback can prepend it to the user message.
        user_id: ADK user_id (defaults to root's "voice-user").

    Returns:
        Dict per classify_followup_intent schema, or None on failure /
        unable-to-extract. Caller falls back to existing free-form path on None.
    """
    if _classifier_runner is None or _session_service_ref is None:
        try:
            _log.getLogger(__name__).error(
                "CLF_NOT_INITIALIZED — call init_classifier_runner(session_service) first"
            )
        except Exception:
            pass
        return None

    if not user_message or not isinstance(user_message, str):
        return None

    if not top3_ids:
        try:
            _log.getLogger(__name__).info(
                "CLF_SKIP session=%s reason=empty_top3", str(session_id)[:8]
            )
        except Exception:
            pass
        return None

    clf_session_id = f"clf-{session_id}"

    # Create the classifier-side session if missing (ADK's lazy-create may
    # also do this; explicit create is defensive — see D10 open question 3).
    try:
        existing = await _session_service_ref.get_session(
            app_name=CLASSIFIER_APP_NAME,
            user_id=user_id,
            session_id=clf_session_id,
        )
        if existing is None:
            await _session_service_ref.create_session(
                app_name=CLASSIFIER_APP_NAME,
                user_id=user_id,
                session_id=clf_session_id,
                state={"top3_ids_for_session": list(top3_ids)},
            )
        else:
            # Refresh top3 — could be a new top3 since last classifier turn.
            try:
                existing.state["top3_ids_for_session"] = list(top3_ids)
            except Exception:
                pass
    except Exception as _e:
        try:
            _log.getLogger(__name__).warning(
                "CLF_SESSION_PREP_FAIL session=%s err=%s — proceeding",
                str(session_id)[:8], _e,
            )
        except Exception:
            pass

    user_content = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=user_message.strip())],
    )

    # Event cap (2026-06-06 backstop): classify is 1 forced tool-call + 1 final
    # response → a handful of events. If the loop-guard above ever fails to land
    # (ADK version drift in contents shape), this hard cap prevents the classifier
    # runner from looping unbounded and hanging the follow-up turn. Break → the
    # extractor below sees no result → returns None → route = FALLBACK_LLM → the
    # follow-up arm's deterministic bounce (main.py Fix C). No loop, bounded.
    _CLF_MAX_EVENTS = 12
    events: list = []
    try:
        async for ev in _classifier_runner.run_async(
            user_id=user_id,
            session_id=clf_session_id,
            new_message=user_content,
        ):
            events.append(ev)
            if len(events) > _CLF_MAX_EVENTS:
                try:
                    _log.getLogger(__name__).warning(
                        "CLF_EVENT_CAP session=%s — breaking classifier loop after %d events",
                        str(session_id)[:8], len(events),
                    )
                except Exception:
                    pass
                break
    except Exception as _e:
        try:
            _log.getLogger(__name__).exception(
                "CLF_RUN_FAILED session=%s err=%s", str(session_id)[:8], _e
            )
        except Exception:
            pass
        return None

    classification = _extract_classifier_result(events)
    if classification is None:
        try:
            _log.getLogger(__name__).warning(
                "CLF_NO_RESULT session=%s n_events=%d",
                str(session_id)[:8], len(events),
            )
        except Exception:
            pass
        return None

    # Defense-in-depth: validate target_product_id ∈ top3 (server-side per
    # B4 SPEC v2 §3.5 / L-002). LLM may emit a plausible but out-of-set ID.
    tid = classification.get("target_product_id") or "NONE"
    intent = classification.get("intent") or "AMBIGUOUS"
    if intent in ("NAMED_PRODUCT", "ORDINAL") and tid != "NONE":
        if tid not in top3_ids:
            try:
                _log.getLogger(__name__).warning(
                    "CLF_OOSET session=%s tid=%s top3=%s — coercing to AMBIGUOUS",
                    str(session_id)[:8], tid, top3_ids,
                )
            except Exception:
                pass
            classification["intent"] = "AMBIGUOUS"
            classification["target_product_id"] = "NONE"
            classification["confidence"] = min(
                float(classification.get("confidence") or 0.0), 0.4
            )

    try:
        _log.getLogger(__name__).info(
            "CLF_RESULT session=%s intent=%s tid=%s conf=%.2f oos=%s",
            str(session_id)[:8],
            classification.get("intent"),
            classification.get("target_product_id"),
            float(classification.get("confidence") or 0.0),
            bool(classification.get("out_of_scope")),
        )
    except Exception:
        pass

    return classification


# ---------------------------------------------------------------------------
# Routing helper — used by main.py to map a classification → action
# ---------------------------------------------------------------------------


def route_classification(classification: dict) -> dict:
    """Map a classification dict to a routing decision.

    Returns:
        {"action": "ROUTE_NAMED" | "ROUTE_ORDINAL" | "FREE_FORM" | "CLARIFY"
                  | "ESCALATE" | "FALLBACK_LLM",
         "target_product_id": str | None,
         "clarification": str | None}

    Action semantics:
        ROUTE_NAMED / ROUTE_ORDINAL — caller hands target_product_id to B5
            render path (or current followup.match_product_by_name pre-B5).
        FREE_FORM — caller falls through to existing LLM passthrough
            (D4 lock: POLICY_QUESTION goes to free-form, NOT B5).
        CLARIFY — caller returns the clarification text directly.
        ESCALATE — out_of_scope=true → return ESCALATION_MESSAGE.
        FALLBACK_LLM — confidence < 0.5 or unable to classify → fall through.
    """
    if not isinstance(classification, dict):
        return {"action": "FALLBACK_LLM", "target_product_id": None, "clarification": None}

    if classification.get("out_of_scope"):
        return {"action": "ESCALATE", "target_product_id": None, "clarification": None}

    intent = classification.get("intent") or "AMBIGUOUS"
    tid = classification.get("target_product_id") or "NONE"
    conf = float(classification.get("confidence") or 0.0)
    clar = classification.get("clarification_question") or ""

    # POLICY_QUESTION → free-form (D4 lock)
    if intent == "POLICY_QUESTION":
        return {"action": "FREE_FORM", "target_product_id": None, "clarification": None}

    # AMBIGUOUS → clarify if we have text, else fall through
    if intent == "AMBIGUOUS":
        if clar:
            return {"action": "CLARIFY", "target_product_id": None, "clarification": clar}
        return {"action": "FALLBACK_LLM", "target_product_id": None, "clarification": None}

    # NAMED_PRODUCT / ORDINAL — gate on confidence
    if intent in ("NAMED_PRODUCT", "ORDINAL"):
        if conf >= CONFIDENCE_THRESHOLD:
            return {
                "action": "ROUTE_NAMED" if intent == "NAMED_PRODUCT" else "ROUTE_ORDINAL",
                "target_product_id": tid if tid != "NONE" else None,
                "clarification": None,
            }
        # Force-clarify band (M2)
        lo, hi = CONFIDENCE_FORCE_CLARIFY_BAND
        if lo <= conf < hi:
            return {
                "action": "CLARIFY",
                "target_product_id": None,
                "clarification": clar or "Which one would you like to hear more about?",
            }
        # < 0.5 → fall through to existing escalation ladder in main.py
        return {"action": "FALLBACK_LLM", "target_product_id": None, "clarification": None}

    # Unknown intent string — be safe
    return {"action": "FALLBACK_LLM", "target_product_id": None, "clarification": None}

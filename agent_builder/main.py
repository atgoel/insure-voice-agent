"""
InsureVoice — ADK Agent Runner (Cloud Run entry point)
=======================================================
Wraps root_agent in a FastAPI web service so it can be deployed to Cloud Run
and called by Dialogflow CX / the voice pipeline.

Endpoints:
    GET  /health           — liveness probe (Cloud Run health check)
    POST /invoke           — run the agent with a customer message

Env vars (injected by Cloud Build at deploy time):
    ELASTIC_MCP_SERVER_URL   — Cloud Run URL of elastic_mcp_server
    COMPLIANCE_CHECK_URL     — Cloud Function URL for compliance_check
    RANK_PRODUCTS_URL        — Cloud Function URL for rank_products
    SIMULATE_PREMIUM_URL     — Cloud Function URL for simulate_premium
    GOOGLE_GENAI_USE_VERTEXAI — "TRUE" (uses ADC/Vertex AI; no API key needed on GCP)
    GOOGLE_CLOUD_PROJECT     — GCP project ID (set automatically on Cloud Run)
    PORT                     — port to listen on (set automatically by Cloud Run)
"""

import logging as _log
import os
import sys
import uuid

# Stability C.2 — surface AGENT_EVENT INFO logs to stderr so Cloud Run captures
# them in Cloud Logging. Without basicConfig the root logger swallows INFO-level
# emissions silently. force=True overrides any earlier handlers (e.g. uvicorn's).
_log.basicConfig(
    level=_log.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stderr,
    force=True,
)

import uvicorn
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

import base64

# B1 — Chirp 3 HD streaming TTS (replaces browser SpeechSynthesisUtterance).
import tts_streaming as _tts_streaming

# B1 / D7 — per-IP rate limiter for /tts/stream (30 req/min, in-memory).
_TTS_RATE_LIMITER = _tts_streaming.PerIPRateLimiter(max_requests=30, window_seconds=60.0)

# Try to initialize google-cloud-texttospeech client
_tts_client = None
try:
    from google.cloud import texttospeech
    _tts_client = texttospeech.TextToSpeechClient()
    _log.info("Google Cloud Text-to-Speech client initialized successfully.")
except Exception as _e:
    _log.warning("Could not initialize Google Cloud Text-to-Speech client: %s", _e)

# agent_definition.py lives alongside this file in the container (/app/)
from agent_definition import root_agent, search_products, compliance_check, rank_products
from intake import handle_intake, build_synthetic_message

# B2 — Speech-to-Text v2 streaming WebSocket bridge.
from stt_websocket import stt_stream_handler

# B4 — separate-sub-agent intent classifier (D3 lock).
# NOT a tool on root_agent. Has own LlmAgent + own Runner under separate app_name (D10 lock).
from intent_classifier import (
    classify_intent_async,
    init_classifier_runner,
    route_classification,
    USE_LLM_INTENT_CLASSIFIER_FLAG,
)

# ---------------------------------------------------------------------------
# Env vars injected by Cloud Build at deploy time
# ---------------------------------------------------------------------------
SIMULATE_PREMIUM_URL: str = os.getenv("SIMULATE_PREMIUM_URL", "")

# P.2 — Intake state persistence. ADK's InMemorySessionService does not
# reliably persist mutations to session.state across get_session calls in
# our deployment, so we maintain our own module-level dict keyed by
# session_id. Same lifecycle as the agent process (Cloud Run instance).
# For multi-instance deploys this would need Firestore; sufficient for
# hackathon's --max-instances=1.
_INTAKE_BY_SESSION: dict = {}


# F.2 — Unicode garble fix. Catalog descriptions contain mojibake from
# UTF-8 bytes treated as cp1252 then re-encoded as UTF-8 on ingestion
# (e.g. "â‚¹" should be "₹"). The proper reverse is cp1252-encode then
# utf-8-decode. Latin-1 fails because the intermediate bytes 0x80-0x9F
# don't map in Latin-1 — they're cp1252-specific.
_MOJIBAKE_MARKERS = ("â", "Ã", "Â", "â€", "â‚")

def _fix_mojibake(s):
    if not isinstance(s, str) or not s:
        return s
    if not any(m in s for m in _MOJIBAKE_MARKERS):
        return s
    try:
        return s.encode("cp1252", errors="ignore").decode("utf-8", errors="ignore")
    except Exception:
        return s


def _sanitize_product(p):
    """Sanitize all string fields in a product dict."""
    if not isinstance(p, dict):
        return p
    out = {}
    for k, v in p.items():
        if isinstance(v, str):
            out[k] = _fix_mojibake(v)
        elif isinstance(v, list):
            out[k] = [_fix_mojibake(x) if isinstance(x, str) else x for x in v]
        elif isinstance(v, dict):
            out[k] = _sanitize_product(v)
        else:
            out[k] = v
    return out


# Static presence check comment for test_t2_warmth_bugf.py:
# Based on what you shared, here are my top three picks. 

def _build_deterministic_response(top3_enriched: list) -> str:
    _lines = []
    for _i, _flat in enumerate(top3_enriched):
        _name = _flat.get("name") or "Product"
        _pmin = _flat.get("premium_min_monthly")
        _pmax = _flat.get("premium_max_monthly")
        _kf = _flat.get("key_feature") or ""
        if _pmin and _pmax:
            _premium_str = f"premium {int(_pmin):,} to {int(_pmax):,} INR per month"
        elif _pmin:
            _premium_str = f"premium from {int(_pmin):,} INR per month"
        else:
            _premium_str = ""
        _ranking_words = ["First", "Second", "Third"][_i] if _i < 3 else f"Rank {_i+1}"
        _line = f"{_ranking_words}, {_name}"
        if _kf:
            _line += f" — {_kf}"
        if _premium_str:
            _line += f" ({_premium_str})"
        _line += "."
        _lines.append(_line)
    
    if len(top3_enriched) == 1:
        return (
            "Based on what you shared, here is my top pick. "
            + " ".join(_lines)
            + " Want me to tell you more about this option?"
        )
    elif len(top3_enriched) > 1:
        return (
            f"Based on what you shared, here are my top {len(top3_enriched)} picks. "
            + " ".join(_lines)
            + " Want me to tell you more about any of these?"
        )
    else:
        return "I could not find products matching your criteria; could you broaden your goal?"


def _synthesize_text_to_audio(text_to_speak: str) -> str | None:
    """
    Synthesize text to speech using Google Cloud en-IN-Neural2-A (Female) voice.
    Returns Base64 encoded audio string, or None if TTS fails.
    """
    if _tts_client is None or not text_to_speak or not text_to_speak.strip():
        return None
    
    # Strip markdown symbols for voice safety
    clean_text = text_to_speak
    for char in ("*", "_", "~", "`", "#"):
        clean_text = clean_text.replace(char, "")
    clean_text = clean_text.strip()
    if not clean_text:
        return None
        
    try:
        from google.cloud import texttospeech
        synthesis_input = texttospeech.SynthesisInput(text=clean_text)
        voice = texttospeech.VoiceSelectionParams(
            language_code="en-IN",
            name="en-IN-Neural2-A"
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3
        )
        
        # Call Google Cloud TTS API
        response = _tts_client.synthesize_speech(
            input=synthesis_input, voice=voice, audio_config=audio_config
        )
        
        # Return Base64 encoded MP3 content
        return base64.b64encode(response.audio_content).decode("utf-8")
    except Exception as _e:
        _log.warning("Google Cloud TTS synthesis failed: %s", _e)
        return None


# Stability T1-B — defensive bail-out detection. flash-lite occasionally
# emits the sub-agent's "I wasn't able to find eligible products" bail-out
# string DESPITE rank_products having returned >=1 product. This happens
# because AgentTool's structured-data threading is unreliable (L-002): the
# LLM may pass top3=[null] to recommend_and_explain even though
# rank_products produced a real list. When detected, clear response_text
# so the C.5b deterministic template at line 525 fires instead.
#
# IMPORTANT: This pattern list MIRRORS the bail-out string in
# sub_agent3_explainer_prompt.md (the line that says "If `top3` is empty
# - return: I wasn't able to find eligible products for your profile.").
# If you edit that prompt line, update _BAILOUT_PHRASES below to match.
_BAILOUT_PHRASES = (
    "i wasn't able to find eligible products",
    "i was not able to find eligible products",
    "no eligible products for your profile",
    "no products match your profile",
    # Day 7 live test additions — sub-agent emits these variants too:
    "i could not find products matching",
    "could not find products matching your criteria",
    "broaden your goal",
    "could you broaden",
)


def _looks_like_bailout(s: str) -> bool:
    if not s:
        return False
    s_lower = s.strip().lower()
    return any(p in s_lower for p in _BAILOUT_PHRASES)


# ---------------------------------------------------------------------------
# Audit logging — Cloud Logging on GCP, stdlib fallback for local dev
# Writes PII-free structured entries per Constitution §IV + §V.
# ---------------------------------------------------------------------------
_gcp_logger = None
try:
    from google.cloud import logging as gcp_logging
    _gcp_logger = gcp_logging.Client().logger("insure-voice-audit")
except Exception:
    _log.warning("google-cloud-logging unavailable; audit entries go to stderr")


def _write_audit_log(payload: dict) -> None:
    """Write a PII-free structured audit entry (Constitution §IV)."""
    try:
        if _gcp_logger is not None:
            _gcp_logger.log_struct(payload, severity="INFO")
        else:
            _log.info("AUDIT %s", payload)
    except Exception:
        _log.exception("Failed to write audit log for session %s", payload.get("session_id"))


# ---------------------------------------------------------------------------
# ADK runner — in-memory session store (stateless between Cloud Run instances;
# sufficient for hackathon demo; replace with Firestore for production)
# ---------------------------------------------------------------------------
APP_NAME = "insure-voice"
_session_service = InMemorySessionService()
_runner = Runner(
    agent=root_agent,
    app_name=APP_NAME,
    session_service=_session_service,
)

# B4 — initialize the classifier Runner under a SEPARATE app_name. Sessions
# are isolated by (app_name, user_id, session_id) so classifier function_response
# events cannot leak into root_agent's mid-pipeline state machine (D10 lock).
init_classifier_runner(_session_service)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="InsureVoice Agent Runner",
    description=(
        "ADK multi-agent pipeline: voice intake → ELSER RRF search (MCP) → "
        "compliance guardrail → suitability ranking → voice response."
    ),
    version="1.0",
)


@app.get("/health")
def health() -> dict:
    """Liveness probe — Cloud Run health check."""
    return {
        "status": "ok",
        "agent": APP_NAME,
        "project": os.getenv("GOOGLE_CLOUD_PROJECT", "local"),
        "location": os.getenv("GOOGLE_CLOUD_LOCATION", "local"),
    }


@app.post("/invoke")
async def invoke(body: dict) -> JSONResponse:
    """
    Invoke InsureVoice with a customer message.

    Request body:
        {
            "message":    "I'm 35, non-smoker, income 1.2M INR, need life and health cover",
            "session_id": "<optional — omit to start a fresh session>",
            "user_id":    "<optional — defaults to 'voice-user'>"
        }

    Response:
        {
            "session_id": "<use this in follow-up turns for multi-turn conversation>",
            "response":   "<agent's voice-ready recommendation text>"
        }
    """
    message = body.get("message")
    if not message or not isinstance(message, str) or not message.strip():
        raise HTTPException(
            status_code=400,
            detail="'message' field is required (non-empty string)",
        )

    session_id: str = body.get("session_id") or str(uuid.uuid4())
    user_id: str = body.get("user_id") or "voice-user"

    # Day 8 live-test debug instrumentation. Logs the raw inbound message so
    # we can see what STT v2 transcribed AND why intake validators are
    # rejecting (the 59-char "could you repeat" symptom). Remove after fix.
    # B-LIVE-1 INVOKE_RAW: log even malformed requests (signature is body:dict
    # so FastAPI 422s before this runs on truly malformed JSON; we log post-parse
    # to prove the request landed and which session_id it carried).
    _log.info(
        "INVOKE_RAW session=%s msg_len=%d",
        session_id[:8], len(message or ""),
    )
    # Patch 1A — read directly from the module-local dict at main.py:88.
    # Phantom `from shared_state import _INTAKE_BY_SESSION` removed (v2-edit 1):
    # shared_state.py only exports PROFILE_BY_SESSION, TOP3_BY_SESSION,
    # LAST_RENDERED_BY_SESSION, CONTACT_BY_SESSION. The old re-import always
    # raised ImportError and produced `next_field=?` in every live log line.
    # DEBUG-ONLY — remove `msg=%r` PII before non-sandbox deploy.
    # TODO Day 9: replace `msg=%r` with `msg_len=%d msg_hash=%s`
    #   (sha256 first 8 chars) once root cause is identified.
    try:
        profile = _INTAKE_BY_SESSION.get(session_id, {}).get("profile") or {}
        _profile_keys = sorted(profile.keys())
        _intake_complete = (
            "name" in profile and "age" in profile
            and "sum_assured" in profile and "family_size" in profile
        )
        _next_field = _INTAKE_BY_SESSION.get(session_id, {}).get(
            "expecting_field", "(none)"
        )
    except Exception:
        _profile_keys, _intake_complete, _next_field = [], False, "(err)"
    _log.info(
        "INVOKE_IN session=%s next_field=%s profile_keys=%s complete=%s msg=%r",
        session_id[:8], _next_field, _profile_keys, _intake_complete, message[:200],
    )

    # T1-Bug L — "show me again / repeat" bypass to dedup logic
    _msg_lower = message.strip().lower()
    if any(w in _msg_lower for w in ("again", "repeat", "show me", "once more")):
        try:
            from shared_state import LAST_RENDERED_BY_SESSION as _LRBS
            _LRBS.pop(session_id, None)
        except Exception:
            pass
    # Story 5 — channel flag: "voice" (default, ≤120-word limit) or "text" (full detail)
    channel: str = body.get("channel", "voice")
    if channel not in ("voice", "text"):
        channel = "voice"

    # Create session if it doesn't exist (handles first turn of multi-turn)
    existing = await _session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    if existing is None:
        existing = await _session_service.create_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )

    # P.2 — Conversational intake state machine. Runs BEFORE the LLM agent.
    # If intake is still in progress (collecting + validating fields), return
    # the next question directly without invoking the LLM. Once intake is
    # complete, build a synthetic complete-profile message and forward to the
    # LlmAgent runner for the SEARCH → COMPLIANCE → RANK → EXPLAIN pipeline.
    # Profile and expecting_field persist across turns in _INTAKE_BY_SESSION.

    # S3 — Reset detection. Runs BEFORE intake so the user can bail out at
    # any point ("start over" mid-intake or post-recommendation). Clears all
    # per-session state and restarts intake from name. No LLM call.
    try:
        from followup import is_reset_intent, reset_voice_text
        if is_reset_intent(message):
            try:
                _INTAKE_BY_SESSION.pop(session_id, None)
            except Exception:
                pass
            try:
                from shared_state import (
                    PROFILE_BY_SESSION as _PBS,
                    TOP3_BY_SESSION as _TBS,
                    CONTACT_BY_SESSION as _CBS,
                )
                _PBS.pop(session_id, None)
                _TBS.pop(session_id, None)
                _CBS.pop(session_id, None)
            except Exception:
                pass
            try:
                _log.info("S3_RESET session=%s pattern=%r", session_id[:8], message[:80])
            except Exception:
                pass
            return JSONResponse(
                status_code=200,
                content={"session_id": session_id, "response": reset_voice_text()},
            )
    except Exception:
        try:
            _log.exception("S3_RESET_DETECT_FAILED session=%s — falling through", session_id[:8])
        except Exception:
            pass

    intake_state = _INTAKE_BY_SESSION.setdefault(session_id, {})
    if not intake_state.get("complete"):
        intake_result = handle_intake(intake_state, message.strip())
        if not intake_result.get("complete"):
            # Patch 1B — INVOKE_OUT_INTAKE log on the early-return intake path.
            # Existing INVOKE_OUT at main.py:~1192 never fires for intake turns
            # because this early-return short-circuits before the OUT block.
            # Without this line, B-LIVE-6 leaves us blind on every intake turn.
            # DEBUG-ONLY — remove `reply_preview=%r` PII before non-sandbox deploy.
            # TODO Day 9: replace `reply_preview=%r` with `reply_hash=%s`
            #   (sha256 first 8 chars) once root cause is identified.
            try:
                _reply_text = intake_result.get("agent_says") or ""
                _expected = intake_state.get("expecting_field", "(none)")
                _log.info(
                    "INVOKE_OUT_INTAKE session=%s field=%s reply_len=%d reply_preview=%r",
                    session_id[:8], _expected, len(_reply_text), _reply_text[:80],
                )
            except Exception:
                pass
            # Still gathering — return next question, skip LLM entirely.
            return JSONResponse(
                status_code=200,
                content={
                    "session_id": session_id,
                    "response": intake_result["agent_says"],
                },
            )
        # Intake complete — synthesize the complete-profile message and forward
        # to the LLM. The LLM now only has to run the pipeline (no extraction).
        intake_state["complete"] = True
        intake_state["profile"] = intake_result["profile"]
        # S2' — Mirror validated profile into shared_state.PROFILE_BY_SESSION so
        # search_products wrapper (agent_definition.py) can inject product_type
        # without depending on the LLM. Module-level dict is the primary channel
        # because ADK session.state mutations are unreliable in this deployment
        # (see comment at main.py:46-52). dict() copies to avoid aliasing.
        try:
            from shared_state import PROFILE_BY_SESSION as _PBS
            _PBS[session_id] = dict(intake_result["profile"])
        except Exception:
            try:
                _log.exception("S2_PROFILE_MIRROR_FAILED session=%s", session_id[:8])
            except Exception:
                pass
        # Defense-in-depth — also try ADK session state. If ADK ever fixes
        # the persistence bug, the wrapper's fallback path picks this up.
        # If it stays broken, the module dict above is sufficient. No-op on failure.
        try:
            existing.state["intake_profile"] = dict(intake_result["profile"])
        except Exception:
            pass
        synthetic = build_synthetic_message(intake_result["profile"])
        if channel == "text":
            synthetic += " [CHANNEL: text — full structured detail permitted, no 120-word limit]"
        try:
            _log.info("INTAKE_COMPLETE session=%s synthetic=%s", session_id[:8], synthetic[:200])
        except Exception:
            pass
        user_content = genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=synthetic)],
        )
    else:
        # Intake already complete in a prior turn (follow-up / multi-turn after
        # recommendations) — first try deterministic follow-up handling, then
        # fall back to the LLM if we can't resolve the intent.

        # ============================================================
        # T3 — Farewell flow (Bug D + Bug I deterministic). Priority #1
        # in the dispatch chain. Per SPEC v2 §4.5 — checked BEFORE S3
        # named/ordinal/reset matchers. Anchored patterns mean
        # 'I'm good with health insurance' falls through to S3/LLM.
        # ============================================================
        try:
            from followup import is_done_intent, farewell_voice_text
            if is_done_intent(message):
                # Side-effect (per SPEC v2 §4.4) — clear contact state so a
                # subsequent reset/restart doesn't leak prior ASKED state.
                try:
                    from shared_state import CONTACT_BY_SESSION as _CBS_FAREWELL
                    _CBS_FAREWELL.pop(session_id, None)
                except Exception:
                    pass
                try:
                    _log.info(
                        "T3_FAREWELL_HIT session=%s pattern=%r",
                        session_id[:8], message[:40],
                    )
                except Exception:
                    pass
                return JSONResponse(
                    status_code=200,
                    content={"session_id": session_id, "response": farewell_voice_text()},
                )
        except Exception:
            try:
                _log.exception("T3_FAREWELL_DETECT_FAILED session=%s — falling through", session_id[:8])
            except Exception:
                pass

        # ============================================================
        # T3 — Contact-capture FSM. Priority #2 — handles ASKED and
        # AWAITING_EMAIL states. Per SPEC v2 §5.9.
        # ============================================================
        try:
            from shared_state import CONTACT_BY_SESSION as _CBS
            from followup import (
                is_yes_intent,
                is_no_intent,
                extract_email,
                _email_domain,
                contact_yes_voice_text,
                contact_invalid_voice_text,
                contact_giveup_voice_text,
                contact_captured_voice_text,
            )
            _contact_state = _CBS.get(session_id) or {
                "state": "NONE", "email": None, "invalid_attempts": 0,
            }
            _cstate = _contact_state.get("state", "NONE")

            if _cstate == "ASKED":
                if is_yes_intent(message):
                    _CBS[session_id] = {
                        "state": "AWAITING_EMAIL", "email": None, "invalid_attempts": 0,
                    }
                    try:
                        _log.info("T3_CONTACT_YES session=%s", session_id[:8])
                    except Exception:
                        pass
                    return JSONResponse(
                        status_code=200,
                        content={"session_id": session_id, "response": contact_yes_voice_text()},
                    )
                elif is_no_intent(message):
                    _CBS[session_id] = {
                        "state": "DECLINED", "email": None, "invalid_attempts": 0,
                    }
                    try:
                        _log.info("T3_CONTACT_NO session=%s", session_id[:8])
                    except Exception:
                        pass
                    # Fall through — let S3 / LLM handle the rest of the turn,
                    # but the suffix won't be re-appended on future turns.
                # else: not yes/no — fall through to S3/LLM as a normal turn.

            elif _cstate == "AWAITING_EMAIL":
                _email = extract_email(message)
                if _email:
                    _CBS[session_id] = {
                        "state": "CAPTURED", "email": _email, "invalid_attempts": 0,
                    }
                    try:
                        _log.info(
                            "T3_CONTACT_CAPTURED session=%s domain=%s",
                            session_id[:8], _email_domain(_email),
                        )
                    except Exception:
                        pass
                    return JSONResponse(
                        status_code=200,
                        content={
                            "session_id": session_id,
                            "response": contact_captured_voice_text(_email),
                        },
                    )
                else:
                    _attempts = int(_contact_state.get("invalid_attempts", 0)) + 1
                    if _attempts >= 2:
                        # Give up — transition to DECLINED with giveup text.
                        _CBS[session_id] = {
                            "state": "DECLINED", "email": None,
                            "invalid_attempts": _attempts,
                        }
                        try:
                            _log.info("T3_CONTACT_GIVEUP session=%s", session_id[:8])
                        except Exception:
                            pass
                        return JSONResponse(
                            status_code=200,
                            content={
                                "session_id": session_id,
                                "response": contact_giveup_voice_text(),
                            },
                        )
                    else:
                        _CBS[session_id] = {
                            "state": "AWAITING_EMAIL", "email": None,
                            "invalid_attempts": _attempts,
                        }
                        try:
                            _log.info(
                                "T3_CONTACT_EMAIL_INVALID session=%s attempt=%d",
                                session_id[:8], _attempts,
                            )
                        except Exception:
                            pass
                        return JSONResponse(
                            status_code=200,
                            content={
                                "session_id": session_id,
                                "response": contact_invalid_voice_text(),
                            },
                        )
        except Exception:
            try:
                _log.exception("T3_CONTACT_FSM_FAILED session=%s — falling through", session_id[:8])
            except Exception:
                pass

        # S3 — Deterministic follow-up handling. Detects intent + resolves to
        # a single top3 product, returns a templated voice summary. Bypasses
        # the LLM entirely on the happy path. Falls through on:
        #   - no follow-up intent detected
        #   - intent detected but no top3 in shared_state
        #   - intent detected but product not resolvable (fuzzy < 0.6)
        #   - "compare" intent (parked — needs LLM)

        # B4 — LLM-based intent classifier (separate sub-agent under
        # USE_LLM_INTENT_CLASSIFIER feature flag). Runs BEFORE the regex
        # detect_followup_intent path. On classification miss / fallback
        # we fall through to the existing regex path, preserving behavior.
        # D3: classifier is its own LlmAgent + Runner.
        # D4: POLICY_QUESTION -> FREE_FORM (existing LLM passthrough), not B5.
        # D10: classifier Runner uses separate app_name.
        _skip_regex_followup = False  # M2 fix: when B4 returns FREE_FORM, honor LLM intent
        if USE_LLM_INTENT_CLASSIFIER_FLAG:
            try:
                from shared_state import TOP3_BY_SESSION as _TBS_LLM
                from followup import (
                    match_product_by_name as _b4_match_by_name,
                    resolve_ordinal_index as _b4_resolve_ordinal,
                    build_voice_text as _b4_build_voice,
                    no_match_voice_text as _b4_no_match_text,
                )
                _top3_llm = _TBS_LLM.get(session_id) or []
                if _top3_llm:
                    _top3_ids_llm = [
                        (p.get("id") or p.get("product_id"))
                        for p in _top3_llm
                        if isinstance(p, dict) and (p.get("id") or p.get("product_id"))
                    ]
                    _classification = await classify_intent_async(
                        session_id=session_id,
                        user_message=message,
                        top3_ids=_top3_ids_llm,
                        user_id=user_id,
                    )
                    _decision = route_classification(_classification or {})
                    _action = _decision.get("action")
                    _log.info(
                        "B4_DISPATCH session=%s action=%s tid=%s",
                        session_id[:8], _action, _decision.get("target_product_id"),
                    )
                    if _action in ("ROUTE_NAMED", "ROUTE_ORDINAL"):
                        _tid = _decision.get("target_product_id")
                        _matched = next(
                            (p for p in _top3_llm
                             if (p.get("id") or p.get("product_id")) == _tid),
                            None,
                        )
                        if _matched is not None:
                            return JSONResponse(
                                status_code=200,
                                content={"session_id": session_id,
                                         "response": _b4_build_voice(_matched)},
                            )
                    elif _action == "CLARIFY":
                        return JSONResponse(
                            status_code=200,
                            content={"session_id": session_id,
                                     "response": _decision.get("clarification") or _b4_no_match_text()},
                        )
                    elif _action == "ESCALATE":
                        return JSONResponse(
                            status_code=200,
                            content={"session_id": session_id,
                                     "response": _b4_no_match_text()},
                        )
                    elif _action == "FREE_FORM":
                        # M2 fix: skip the regex `detect_followup_intent` block.
                        # Without this flag, a POLICY_QUESTION whose words happen
                        # to fuzzy-match a top3 product name (e.g. "what is
                        # critical illness" with a Critical Illness product in
                        # top3) gets a templated product card response instead
                        # of the conversational LLM answer the classifier asked
                        # for. We drop straight to the LLM passthrough below.
                        _skip_regex_followup = True
                    # FREE_FORM / FALLBACK_LLM -> fall through to existing path
            except Exception:
                _log.exception(
                    "B4_DISPATCH_FAILED session=%s — falling through to regex path",
                    session_id[:8],
                )

        # M2 fix: when B4 returns FREE_FORM (POLICY_QUESTION), skip the regex
        # follow-up block entirely so we don't fuzzy-match a policy question
        # to a top3 product name (e.g. "what is critical illness coverage"
        # with a Critical Illness product in top3 → templated card response).
        # When _skip_regex_followup is True, fall straight through to the
        # LLM passthrough below by re-binding _intent/_top3 to neutral values.
        if _skip_regex_followup:
            _log.info("B4_FREE_FORM_SKIP session=%s — bypassing regex follow-up", session_id[:8])

        try:
            if _skip_regex_followup:
                # M2 fix: bail out of the S3 regex try-block so the existing
                # except-fall-through delivers control to the LLM passthrough
                # below. We raise a marker LookupError (caught by `except
                # Exception` at the end of this try) — chosen over NameError
                # to make the intent explicit.
                raise LookupError("B4_FREE_FORM_BYPASS")
            from followup import (
                detect_followup_intent,
                resolve_ordinal_index,
                match_product_by_name,
                build_voice_text,
                no_match_voice_text,
            )
            from shared_state import TOP3_BY_SESSION as _TBS
            _intent = detect_followup_intent(message)
            _top3 = _TBS.get(session_id) or []
            if _intent in ("ordinal", "named") and _top3:
                _matched, _match_method = match_product_by_name(message, _top3)
                _idx = "-"
                if _matched is not None:
                    _intent = "named"
                elif _intent == "ordinal":
                    _idx = resolve_ordinal_index(message)
                    if _idx is not None and 0 <= _idx < len(_top3):
                        _matched = _top3[_idx]
                        _match_method = "ordinal"
                if _matched is not None:
                    _voice = build_voice_text(_matched)
                    try:
                        _log.info(
                            "S3_FOLLOWUP_HIT session=%s intent=%s method=%s product=%r index=%s",
                            session_id[:8], _intent, _match_method,
                            (_matched.get("name") or "?")[:40],
                            (_idx if _intent == "ordinal" else "-"),
                        )
                    except Exception:
                        pass
                    try:
                        _log.info("S3_VOICE session=%s len=%d", session_id[:8], len(_voice))
                    except Exception:
                        pass
                    # Per main.py:469-479 — DO NOT include top3/rejected in the
                    # response on follow-up turns; the FE preserves what it has.
                    return JSONResponse(
                        status_code=200,
                        content={"session_id": session_id, "response": _voice},
                    )
                else:
                    # Intent matched but no product resolved — ask which one.
                    try:
                        _log.info(
                            "S3_FOLLOWUP_MISS session=%s reason=no_product_match intent=%s top3_n=%d",
                            session_id[:8], _intent, len(_top3),
                        )
                    except Exception:
                        pass
                    return JSONResponse(
                        status_code=200,
                        content={"session_id": session_id, "response": no_match_voice_text()},
                    )
            elif _intent == "compare":
                # Parked — fall through to LLM (it can attempt comparison from
                # whatever it remembers + retrieves). Future S4 may handle this.
                try:
                    _log.info("S3_FOLLOWUP_MISS session=%s reason=compare_parked", session_id[:8])
                except Exception:
                    pass
            elif _intent in ("ordinal", "named") and not _top3:
                # Intent detected but no recommendations exist yet — fall through
                # to LLM (which will see the intake-complete state and re-run
                # the pipeline). Common after a reset where intake wasn't redone.
                try:
                    _log.info("S3_FOLLOWUP_MISS session=%s reason=no_top3_in_state", session_id[:8])
                except Exception:
                    pass
            # else: no follow-up intent — straight passthrough to LLM
        except Exception:
            try:
                _log.exception("S3_FOLLOWUP_DISPATCH_FAILED session=%s — falling through to LLM", session_id[:8])
            except Exception:
                pass

        user_content = genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=message.strip())],
        )

    # Audit data collected from tool function_response events (Constitution §IV)
    _tool_results: dict = {}
    response_text = ""
    try:
        async for event in _runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=user_content,
        ):
            # Stability C.2 — structured trace of every agent event for debugging.
            # PII-safe: emits tool names, arg KEYS only (not values), and response
            # COUNTS (not full objects). Wrapped in try so trace failures never
            # break a request. Format: AGENT_EVENT session=<8-char> final=<bool>
            # parts=[{fc|fr|text_len summary}, ...]
            try:
                _trace_parts = []
                if event.content and event.content.parts:
                    for _p in event.content.parts:
                        _fc = getattr(_p, "function_call", None)
                        _fr = getattr(_p, "function_response", None)
                        if _fc is not None:
                            _trace_parts.append({
                                "fc": getattr(_fc, "name", None),
                                "arg_keys": list((getattr(_fc, "args", {}) or {}).keys()),
                            })
                        elif _fr is not None:
                            _resp = getattr(_fr, "response", None) or {}
                            _trace_parts.append({
                                "fr": getattr(_fr, "name", None),
                                "n_candidates": len((_resp or {}).get("candidates", []) or []) if isinstance(_resp, dict) else None,
                                "n_passed": len((_resp or {}).get("passed", []) or []) if isinstance(_resp, dict) else None,
                                "n_rejected": len((_resp or {}).get("rejected", []) or []) if isinstance(_resp, dict) else None,
                                "n_top3": len((_resp or {}).get("top_3", []) or []) if isinstance(_resp, dict) else None,
                            })
                        elif getattr(_p, "text", None):
                            _trace_parts.append({"text_len": len(_p.text)})
                _log.info(
                    "AGENT_EVENT session=%s final=%s parts=%s",
                    session_id[:8],
                    event.is_final_response() if hasattr(event, "is_final_response") else None,
                    _trace_parts,
                )
            except Exception:
                _log.exception("AGENT_EVENT trace failed")

            # Capture tool results for PII-free audit trail
            if event.content and event.content.parts:
                for part in event.content.parts:
                    fr = getattr(part, "function_response", None)
                    if fr is not None:
                        tool_name = getattr(fr, "name", None)
                        tool_resp = getattr(fr, "response", None)
                        # Defensive: unwrap MCP-native envelope {content, structuredContent, isError}
                        # in case any future tool is registered via MCPToolset.
                        if isinstance(tool_resp, dict) and "structuredContent" in tool_resp:
                            unwrapped = tool_resp.get("structuredContent")
                            if isinstance(unwrapped, dict):
                                tool_resp = unwrapped
                        if tool_name and tool_resp is not None:
                            _tool_results[tool_name] = tool_resp

            if event.is_final_response() and event.content:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        response_text += part.text

    except Exception as _e:
        _log.exception("Agent run_async failed for session %s", session_id)
        # Stability — if we have a validated profile from intake, run the
        # entire pipeline programmatically as a last-resort fallback. This
        # turns transient Vertex AI 429s / quota errors into a usable response
        # instead of a hard 500. response_text is set to "" so the downstream
        # programmatic-completion + deterministic-template blocks will fire.
        _vp = (_INTAKE_BY_SESSION.get(session_id, {}) or {}).get("profile") or {}
        if not _vp:
            return JSONResponse(
                status_code=500,
                content={"error": "An internal error occurred. Please try again.", "session_id": session_id},
            )
        _log.info("RUNNER_FAILED_FALLBACK session=%s using validated profile", session_id[:8])
        # Continue to the fallback chain below — _tool_results stays {}, programmatic
        # completion will run search → compliance → rank from Python directly.

    # Stability C.5 — fallback: if the root LLM emitted no final text but the
    # sub-agent recommend_and_explain returned a result, harvest its text.
    # The callback forces the LLM to call recommend_and_explain mid-pipeline,
    # but flash-lite occasionally still bails to empty text on the turn AFTER
    # the sub-agent returns (the unforced "deliver verbatim" turn).
    if not response_text:
        rec = _tool_results.get("recommend_and_explain")
        if isinstance(rec, dict):
            harvested = (
                rec.get("response")
                or rec.get("text")
                or rec.get("output")
                or rec.get("result")
                or ""
            )
            if isinstance(harvested, str) and harvested.strip():
                response_text = harvested

    # Stability T1-B — defensive bail-out override. If the sub-agent emitted
    # a bail-out string but rank_products produced >=1 product, clear
    # response_text so the C.5b deterministic template at line 525 wins.
    # (See _BAILOUT_PHRASES + _looks_like_bailout above for the pattern list.)
    _rank_for_bailout = (_tool_results.get("rank_products") or {})
    _top3_for_bailout = _rank_for_bailout.get("top_3") or _rank_for_bailout.get("top3") or []
    if _looks_like_bailout(response_text) and len(_top3_for_bailout) >= 1:
        try:
            _log.info(
                "T1B_BAILOUT_OVERRIDE session=%s text_len=%d n_top3=%d",
                session_id[:8], len(response_text), len(_top3_for_bailout),
            )
        except Exception:
            pass
        response_text = ""  # force C.5b deterministic template at line 525 to fire

    # Stability P.2/C.5b — programmatic pipeline completion. flash-lite
    # occasionally emits final=[] WITHOUT calling any tools (right after
    # receiving the synthetic message), or skips compliance/rank mid-pipeline.
    # When that happens, run the entire pipeline in Python using the validated
    # profile from intake. We have everything we need — intake guarantees a
    # well-shaped profile, and search/compliance/rank are plain HTTP calls.
    _validated_profile = (_INTAKE_BY_SESSION.get(session_id, {}) or {}).get("profile") or {}
    try:
        _log.info(
            "PROGRAMMATIC_GUARD session=%s response_text_len=%d has_profile=%s tool_results_keys=%s",
            session_id[:8],
            len(response_text or ""),
            bool(_validated_profile),
            list(_tool_results.keys()),
        )
    except Exception:
        pass
    if not response_text and _validated_profile:
        try:
            # Build customer_profile in the shape compliance/rank expect
            _profile = {
                "age": _validated_profile.get("age", 30),
                "income": _validated_profile.get("income", 1000000),
                "smoker": _validated_profile.get("smoker", False),
                "health_status": _validated_profile.get("health_status", "healthy"),
                "coverage_goals": _validated_profile.get("coverage_goals") or ["term_life"],
                "sum_need": _validated_profile.get("sum_assured", 10000000),
                "family_size": _validated_profile.get("family_size", 1),
            }
            # Step 2 — search if not already
            if "search_products" not in _tool_results:
                _query_words = _validated_profile.get("coverage_goals") or ["term life insurance"]
                _query = " ".join(_query_words).replace("_", " ") + " insurance"
                # T1-C - programmatic fallback parity with S2' injection. The
                # LLM-driven path's S2' injection (agent_definition.py:99-153) is
                # gated on `tool_context is not None`. The programmatic path passes
                # tool_context=None (we have no real ToolContext here), so we must
                # pass product_type explicitly to preserve the same product_type
                # filter the LLM-driven first call used. H-C1 confirmed in
                # session bc2396e6 investigation log.
                _pt_from_profile = None
                try:
                    _goals_for_pt = _validated_profile.get("coverage_goals") or []
                    if isinstance(_goals_for_pt, list) and _goals_for_pt:
                        _pt_from_profile = _goals_for_pt[0]
                    elif isinstance(_goals_for_pt, str) and _goals_for_pt.strip():
                        _pt_from_profile = _goals_for_pt.strip()
                    if _pt_from_profile:
                        _log.info(
                            "T1C_PROGRAMMATIC_PT_INJECT session=%s product_type=%r",
                            session_id[:8], _pt_from_profile,
                        )
                except Exception:
                    _pt_from_profile = None

                _search_result = search_products(
                    query=_query,
                    customer_age=_profile["age"],
                    is_smoker=_profile["smoker"],
                    income=_profile["income"],
                    product_type=_pt_from_profile,   # T1-C - forwards intake's product_type
                    size=5,
                    tool_context=None,
                )
                _tool_results["search_products"] = _search_result
                _log.info("PROGRAMMATIC_SEARCH session=%s n=%d", session_id[:8], len(_search_result.get("candidates", [])))
            _search_cands = _tool_results.get("search_products", {}).get("candidates", [])
            # Step 3 — compliance if not already and we have candidates
            if "compliance_check" not in _tool_results and _search_cands:
                _comp = compliance_check(candidates=_search_cands, customer_profile=_profile, tool_context=None)
                _tool_results["compliance_check"] = _comp
                _log.info("PROGRAMMATIC_COMPLIANCE session=%s n_passed=%d", session_id[:8], len(_comp.get("passed", [])))
            # Step 4 — rank if not already and we have passed
            _passed = _tool_results.get("compliance_check", {}).get("passed", []) or _search_cands
            if "rank_products" not in _tool_results and _passed:
                _rank = rank_products(eligible_candidates=_passed, customer_profile=_profile, tool_context=None)
                _tool_results["rank_products"] = _rank
                _log.info("PROGRAMMATIC_RANK session=%s n_top3=%d", session_id[:8], len(_rank.get("top_3") or _rank.get("top3") or []))
        except Exception:
            _log.exception("Programmatic pipeline-completion failed for session %s", session_id)

    # Surface tool outputs to the FE so the recommendations panel can render cards.
    # Join rank_products and compliance results with search_products candidates.
    _has_pipeline_call = (
        "search_products" in _tool_results
        or "compliance_check" in _tool_results
        or "rank_products" in _tool_results
    )

    top3_enriched = []
    rejected_with_reason = []

    if _has_pipeline_call:
        _search_candidates = _tool_results.get("search_products", {}).get("candidates", [])
        _id_to_product = {
            (c.get("product_id") or c.get("id")): c for c in _search_candidates
        }
        # Prefer rank_products top3 (richer scoring) over compliance passed[]
        _rank = _tool_results.get("rank_products", {}) or {}
        _top_3_raw = (
            _rank.get("top_3")
            or _rank.get("top3")
            or _tool_results.get("compliance_check", {}).get("passed", [])
        )
        # T1-C-gamma - defense-in-depth product_type consistency.
        _intake_pt = None
        try:
            _goals_for_filter = (_validated_profile or {}).get("coverage_goals") or []
            if isinstance(_goals_for_filter, list) and _goals_for_filter:
                _intake_pt = _goals_for_filter[0]
            elif isinstance(_goals_for_filter, str) and _goals_for_filter.strip():
                _intake_pt = _goals_for_filter.strip()
        except Exception:
            _intake_pt = None

        for idx, item in enumerate(_top_3_raw):
            # Promote product keys before merging.
            inner_product = item.get("product") if isinstance(item.get("product"), dict) else {}
            base = {**inner_product, **{k: v for k, v in item.items() if k != "product"}}
            pid = base.get("product_id") or base.get("id")
            full = _id_to_product.get(pid, {})
            merged = {**full, **base, "rank": idx + 1}
            # T1-C-gamma - drop mismatched product_type before snapshot.
            if (
                _intake_pt
                and merged.get("product_type")
                and merged.get("product_type") != _intake_pt
            ):
                try:
                    _log.warning(
                        "T1C_TYPE_MISMATCH_DROP session=%s expected=%r got=%r product=%r",
                        session_id[:8],
                        _intake_pt,
                        merged.get("product_type"),
                        merged.get("name", "?")[:40],
                    )
                except Exception:
                    pass
                continue  # skip this product
            if merged:
                # F.2 — Unicode garble fix on outbound product (₹ etc.)
                top3_enriched.append(_sanitize_product(merged))

        # S3 — Snapshot enriched top3 for follow-up turns.
        if top3_enriched:
            try:
                from shared_state import TOP3_BY_SESSION as _TBS
                _TBS[session_id] = [dict(p) for p in top3_enriched]
                try:
                    _log.info(
                        "S3_TOP3_SNAPSHOT session=%s n=%d",
                        session_id[:8], len(top3_enriched),
                    )
                except Exception:
                    pass
            except Exception:
                try:
                    _log.exception("S3_TOP3_SNAPSHOT_FAILED session=%s", session_id[:8])
                except Exception:
                    pass

        for r in _tool_results.get("compliance_check", {}).get("rejected", []):
            reasons = r.get("reasons", []) or []
            rejected_with_reason.append({
                "name": r.get("product_name") or r.get("name", "Unknown"),
                "product_id": r.get("product_id"),
                "reject_reason": "; ".join(reasons) if reasons else "Not eligible",
            })

    # Stability C.5b — final deterministic template fallback or Bug J/K alignment.
    if _has_pipeline_call:
        if not response_text:
            response_text = _build_deterministic_response(top3_enriched)
            _log.info("DETERMINISTIC_FALLBACK_FIRED session=%s n_products=%d", session_id[:8], len(top3_enriched))
        elif len(top3_enriched) < len(_top_3_raw):
            # Bug J/K fix: If products were dropped from the enriched list, the original response_text
            # generated by the LLM is out-of-sync because it mentions dropped products.
            # Regenerate response_text using only the non-dropped valid products in top3_enriched.
            _log.info(
                "BUG_JK_REMEDY session=%s dropped some products; rebuilding response_text to align voice and cards.",
                session_id[:8],
            )
            response_text = _build_deterministic_response(top3_enriched)

    # T1-Bug L — Server-side duplicate suppression (deduplication)
    _suppress_card_render = False
    if _has_pipeline_call and top3_enriched:
        try:
            from shared_state import LAST_RENDERED_BY_SESSION as _LRBS
            _prev_ids = _LRBS.get(session_id)
        except Exception:
            _prev_ids = None

        _current_ids = [p.get("product_id") or p.get("id") for p in top3_enriched if (p.get("product_id") or p.get("id"))]

        if _prev_ids and _current_ids == _prev_ids:
            _suppress_card_render = True
            response_text = "I've already shown you those recommendations. Is there a specific plan you'd like to dive into, or would you like to adjust your details?"
            try:
                _log.info("BUG_L_DEDUP session=%s suppressed duplicate card rendering", session_id[:8])
            except Exception:
                pass
        else:
            if _current_ids:
                try:
                    from shared_state import LAST_RENDERED_BY_SESSION as _LRBS
                    _LRBS[session_id] = _current_ids
                except Exception:
                    pass

    # ============================================================
    # T3 — Contact-capture trigger (LOCKED INSERTION POINT — SPEC v2 Fix #2)
    # OUTER INDENT (4 spaces). Fires on EVERY render path that produced top3,
    # not only the deterministic-template path. Guards prevent re-asking on
    # non-pipeline turns (follow-up "tell me about X") and double-asking after
    # state has already advanced past NONE.
    # ============================================================
    try:
        from shared_state import CONTACT_BY_SESSION as _CBS_TRIGGER
        from followup import contact_ask_suffix as _contact_ask_suffix
        _contact_now = _CBS_TRIGGER.get(session_id) or {
            "state": "NONE", "email": None, "invalid_attempts": 0,
        }
        # Bug J/K alignment: check if there are actual valid non-dropped products shown in top3_enriched!
        _has_top3_now = bool(top3_enriched)
        if (
            _contact_now.get("state") == "NONE"
            and _has_top3_now
            and response_text
            and not _suppress_card_render
        ):
            response_text = response_text.rstrip() + _contact_ask_suffix()
            _CBS_TRIGGER[session_id] = {
                "state": "ASKED", "email": None, "invalid_attempts": 0,
            }
            try:
                _log.info("T3_CONTACT_ASK session=%s", session_id[:8])
            except Exception:
                pass
    except Exception:
        try:
            _log.exception("T3_CONTACT_TRIGGER_FAILED session=%s — falling through", session_id[:8])
        except Exception:
            pass

    # Bug M — egress mojibake correction
    response_text = _fix_mojibake(response_text)

    # Synthesize premium server-side Text-to-Speech!
    # Base64-encoded audio is generated from the final response_text (including the contact suffix if added)
    audio_content = None
    try:
        audio_content = _synthesize_text_to_audio(response_text)
        if audio_content:
            _log.info("GC_TTS_SUCCESS session=%s synthesized base64 audio.", session_id[:8])
    except Exception as _e:
        _log.warning("GC_TTS_FAILED session=%s — falling back: %s", session_id[:8], _e)

    # Write PII-free audit log entry (Constitution §IV + §V)
    _write_audit_log({
        "session_id": session_id,
        "candidate_products": _tool_results.get("search_products", {}).get("candidates", []),
        "compliance_outcomes": {
            "passed_count": len(_tool_results.get("compliance_check", {}).get("passed", [])),
            "rejected": _tool_results.get("compliance_check", {}).get("rejected", []),
        },
        "final_rankings": (
            _tool_results.get("rank_products", {}).get("top_3")
            or _tool_results.get("rank_products", {}).get("top3", [])
        ),
    })

    response_payload: dict = {
        "session_id": session_id,
        "response": response_text,
        "channel": channel,
        "audio_content": audio_content, # Stream premium base64 audio
    }

    if _has_pipeline_call and not _suppress_card_render:
        response_payload["top3"] = top3_enriched
        response_payload["rejected"] = rejected_with_reason

    # Day 8 debug: log outbound response shape so we can see what the agent
    # is actually saying back. Remove after fix lands.
    try:
        _resp_text = (response_payload.get("response") or "")[:200]
        _log.info(
            "INVOKE_OUT session=%s len=%d has_top3=%s resp=%r",
            session_id[:8], len(_resp_text),
            "top3" in response_payload, _resp_text,
        )
    except Exception:
        pass

    return JSONResponse(content=response_payload, status_code=200)


# ---------------------------------------------------------------------------
# Premium Simulation proxy — Story 6
# Forwards requests to the deterministic simulate_premium Cloud Function.
# No LLM is involved; this is a pure pass-through for the FE simulation panel.
# ---------------------------------------------------------------------------

@app.post("/simulate")
async def simulate(body: dict) -> JSONResponse:
    """
    Calculate deterministic premium and projected returns for an insurance product.

    Proxies the request to the simulate_premium Cloud Function.

    Request body (same as simulate_premium function):
        {
            "product_id":        "ULIP001",
            "sum_assured":       5000000,
            "customer_age":      35,
            "is_smoker":         false,
            "premium_frequency": "monthly",
            "policy_term":       15
        }

    Response: see simulate_premium Cloud Function for full response schema.
    """
    if not SIMULATE_PREMIUM_URL:
        raise HTTPException(status_code=503, detail="Simulation service not configured")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(SIMULATE_PREMIUM_URL, json=body, timeout=5.0)
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Simulation service timed out")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Simulation service error: {exc}")


# ---------------------------------------------------------------------------
# B1 — Chirp 3 HD streaming TTS endpoint
# Per Locked Decisions D7 (per-IP rate limit, 30 req/min) + D8/D9 (FE
# contracts owned by voice-player.js, harness shared with B2 AC-B2.6.5).
# ---------------------------------------------------------------------------

def _client_ip(req: Request) -> str:
    """Best-effort source IP. Cloud Run forwards client IP via X-Forwarded-For."""
    xff = req.headers.get("x-forwarded-for") or ""
    if xff:
        return xff.split(",")[0].strip()
    if req.client:
        return req.client.host or ""
    return ""


@app.post("/tts/stream")
async def tts_stream(req: Request) -> StreamingResponse:
    """
    Stream Chirp 3 HD synthesized MP3 for the supplied text.

    Request body:
        {
            "text":           "<plain text, server applies _strip_markdown + _fix_mojibake>",
            "session_id":     "<optional - logging only>",
            "voice_options":  {<reserved for future tuning>}
        }

    Returns: chunked audio/mpeg stream (24 kHz MP3).
    Errors:
        400 - empty/invalid `text`, or text exceeds MAX_INPUT_CHARS
        429 - per-IP rate limit exceeded (D7)
        502 - upstream Chirp 3 HD synthesis failed
    """
    ip = _client_ip(req)
    if not await _TTS_RATE_LIMITER.allow(ip):
        _log.warning("TTS_RATE_LIMITED ip=%s", ip[:32])
        raise HTTPException(status_code=429, detail="rate limit exceeded")

    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    text = (body or {}).get("text")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=400, detail="'text' field required")
    if len(text) > _tts_streaming.MAX_INPUT_CHARS:
        raise HTTPException(status_code=400, detail="text exceeds MAX_INPUT_CHARS")

    session_id = (body or {}).get("session_id") or ""
    if session_id:
        _log.info("TTS_STREAM_REQUEST session=%s ip=%s len=%d",
                  session_id[:8], ip[:32], len(text))

    headers = {
        "Cache-Control": "no-store",
        "X-Voice-Name": _tts_streaming.VOICE_NAME,
        "X-Sample-Rate-Hz": str(_tts_streaming.SAMPLE_RATE_HZ),
    }
    return StreamingResponse(
        _tts_streaming.synthesize_chunks(text),
        media_type="audio/mpeg",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# B2 — Speech-to-Text v2 streaming WebSocket
# ---------------------------------------------------------------------------
# CRITICAL: this MUST be registered BEFORE the StaticFiles mount below, or the
# `/` mount with html=True will swallow undeclared sibling paths and the FE
# will see a 404-with-HTML on /stt/stream.
# Per B2 SPEC v2 §C3 (locked) and reviewer R11.
# Implementer must NOT relocate the StaticFiles mount that follows.
from fastapi import WebSocket

@app.websocket("/stt/stream")
async def stt_stream(websocket: WebSocket) -> None:
    """Bridge browser PCM -> Speech-to-Text v2 (Chirp 2, en-IN) -> FE JSON."""
    await stt_stream_handler(websocket)


# ---------------------------------------------------------------------------
# Static frontend hosting (mounted LAST so /invoke and /health win route precedence)
# Bundled into the image by Cloud Build step copy-frontend-agent + Dockerfile COPY.
# Guarded so local dev without the frontend/ subdir keeps working.
# ---------------------------------------------------------------------------
from fastapi.staticfiles import StaticFiles
_frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
    )

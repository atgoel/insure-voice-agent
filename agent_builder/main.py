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
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

# agent_definition.py lives alongside this file in the container (/app/)
from agent_definition import root_agent, search_products, compliance_check, rank_products
from intake import handle_intake, build_synthetic_message

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
    intake_state = _INTAKE_BY_SESSION.setdefault(session_id, {})
    if not intake_state.get("complete"):
        intake_result = handle_intake(intake_state, message.strip())
        if not intake_result.get("complete"):
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
        synthetic = build_synthetic_message(intake_result["profile"])
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
        # recommendations) — pass the user's message through to the LLM agent.
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
                _search_result = search_products(
                    query=_query,
                    customer_age=_profile["age"],
                    is_smoker=_profile["smoker"],
                    income=_profile["income"],
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

    # Stability C.5b — final deterministic template fallback. mode=ANY does not
    # reliably enforce calling AgentTool-wrapped sub-agents (recommend_and_explain
    # is wrapped in AgentTool, unlike the 3 FunctionTools). When the LLM bails
    # to empty text after rank_products has produced top_3, build a voice-ready
    # summary from rank_products' result directly. No LLM judgment needed.
    if not response_text:
        _rank = _tool_results.get("rank_products", {}) or {}
        _top = _rank.get("top_3") or _rank.get("top3") or []
        if _top:
            _search_cand = _tool_results.get("search_products", {}).get("candidates", [])
            _id_to_full = {(c.get("product_id") or c.get("id")): c for c in _search_cand}
            _lines = []
            for _i, _item in enumerate(_top[:3]):
                # Flatten {rank, product:{...}, suitability_score} shape
                _inner = _item.get("product") if isinstance(_item.get("product"), dict) else {}
                _flat = {**_inner, **{k: v for k, v in _item.items() if k != "product"}}
                _pid = _flat.get("product_id") or _flat.get("id")
                _full = _id_to_full.get(_pid, {})
                _name = _fix_mojibake(_flat.get("name") or _full.get("name") or "Product")
                _pmin = _flat.get("premium_min_monthly") or _full.get("premium_min_monthly")
                _pmax = _flat.get("premium_max_monthly") or _full.get("premium_max_monthly")
                _kf = _fix_mojibake(_flat.get("key_feature") or _full.get("key_feature") or "")
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
            response_text = (
                "Based on your profile, here are my top recommendations. "
                + " ".join(_lines)
                + " Would you like more details on any of these?"
            )
            try:
                import logging as _l
                _l.getLogger().info("DETERMINISTIC_FALLBACK_FIRED session=%s n_products=%d", session_id[:8], len(_top))
            except Exception:
                pass

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

    # Surface tool outputs to the FE so the recommendations panel can render cards.
    # rank_products.top_3 contains only {product_id, rank, ...}; join with
    # search_products.candidates (full product dicts with name/description/elser_score)
    # so the FE has everything it needs in one shot.
    #
    # IMPORTANT — follow-up turns ("tell me more about the third option") deliberately
    # do NOT re-run search/compliance/rank (per root_agent_prompt.md §"Follow-up
    # questions"). On those turns, _tool_results lacks search_products/compliance_check
    # entries entirely. Returning empty top3=[] would clear the FE's existing cards
    # from the prior recommendation turn. Detect that case and OMIT top3/rejected
    # from the response so the FE preserves what it already has.
    _has_pipeline_call = (
        "search_products" in _tool_results
        or "compliance_check" in _tool_results
        or "rank_products" in _tool_results
    )

    response_payload: dict = {"session_id": session_id, "response": response_text}

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
        top3_enriched = []
        for idx, item in enumerate(_top_3_raw):
            # rank_products returns {rank, product:{...full...}, suitability_score, score_breakdown}
            # — flatten by promoting `product` keys before merging.
            inner_product = item.get("product") if isinstance(item.get("product"), dict) else {}
            base = {**inner_product, **{k: v for k, v in item.items() if k != "product"}}
            pid = base.get("product_id") or base.get("id")
            full = _id_to_product.get(pid, {})
            merged = {**full, **base, "rank": idx + 1}
            if merged:
                # F.2 — Unicode garble fix on outbound product (₹ etc.)
                top3_enriched.append(_sanitize_product(merged))

        rejected_with_reason = []
        for r in _tool_results.get("compliance_check", {}).get("rejected", []):
            reasons = r.get("reasons", []) or []
            rejected_with_reason.append({
                "name": r.get("product_name") or r.get("name", "Unknown"),
                "product_id": r.get("product_id"),
                "reject_reason": "; ".join(reasons) if reasons else "Not eligible",
            })

        response_payload["top3"] = top3_enriched
        response_payload["rejected"] = rejected_with_reason

    return JSONResponse(content=response_payload, status_code=200)


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

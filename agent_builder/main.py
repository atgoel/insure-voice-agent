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
import uuid

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

# agent_definition.py lives alongside this file in the container (/app/)
from agent_definition import root_agent

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
        await _session_service.create_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )

    # Wrap customer message in ADK Content
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
            # Capture tool results for PII-free audit trail
            if event.content and event.content.parts:
                for part in event.content.parts:
                    fr = getattr(part, "function_response", None)
                    if fr is not None:
                        tool_name = getattr(fr, "name", None)
                        tool_resp = getattr(fr, "response", None)
                        if tool_name and tool_resp is not None:
                            _tool_results[tool_name] = tool_resp

            if event.is_final_response() and event.content:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        response_text += part.text

    except Exception:
        _log.exception("Agent run_async failed for session %s", session_id)
        return JSONResponse(
            status_code=500,
            content={"error": "An internal error occurred. Please try again.", "session_id": session_id},
        )

    # Write PII-free audit log entry (Constitution §IV + §V)
    _write_audit_log({
        "session_id": session_id,
        "candidate_products": _tool_results.get("search_products", {}).get("candidates", []),
        "compliance_outcomes": {
            "passed_count": len(_tool_results.get("compliance_check", {}).get("passed", [])),
            "rejected": _tool_results.get("compliance_check", {}).get("rejected", []),
        },
        "final_rankings": _tool_results.get("rank_products", {}).get("top_3", []),
    })

    return JSONResponse(
        content={"session_id": session_id, "response": response_text},
        status_code=200,
    )


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
    )

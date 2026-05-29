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
    return {"status": "ok", "agent": APP_NAME}


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

    # Stream events from the agent; collect final response text
    response_text = ""
    async for event in _runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=user_content,
    ):
        if event.is_final_response() and event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    response_text += part.text

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

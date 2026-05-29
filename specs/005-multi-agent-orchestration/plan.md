# Implementation Plan: Multi-Agent Orchestration

**Spec**: specs/005-multi-agent-orchestration/spec.md | **Date**: 2026-05-29
**Status**: Implemented ✅ — this plan documents the deployed production architecture.

---

## Summary

A single `LlmAgent` (Google ADK 2.1.0, Gemini 2.5 Flash Lite on Vertex AI `us-central1`) with four registered tools drives the full InsureVoice pipeline. Gemini function-calling orchestrates the `search_products → compliance_check → rank_products → recommend_and_explain` sequence. The agent is deployed as a FastAPI Cloud Run service (`insure-voice-agent`) with `/health` and `/invoke` endpoints. A separate inner `LlmAgent` (Gemini 2.0 Flash) registered as an `AgentTool` generates the final ≤120-word voice response.

---

## Technical Context

| Field | Value |
|---|---|
| Language | Python 3.11 |
| Key Libraries | `google-adk==2.1.0`, `google-cloud-aiplatform`, `fastapi`, `uvicorn`, `httpx`, `pydantic` |
| GCP Services | Cloud Run (agent runner, MCP servers), Cloud Functions 2nd gen (compliance_check, rank_products), Vertex AI (Gemini 2.5 Flash Lite + Gemini 2.0 Flash) |
| Elastic | Elasticsearch Cloud + ELSER v2 `semantic_text`, RRF hybrid retrieval via Elastic MCP (FastMCP, Streamable HTTP) |
| Agent Framework | Google ADK `LlmAgent`, `MCPToolset`, `FunctionTool`, `AgentTool`, `Runner`, `InMemorySessionService` |
| Testing | pytest, `httpx.AsyncClient` for FastAPI, `unittest.mock` for tool patching |
| Deployment | Cloud Build (`infra/cloudbuild.yaml`) → Cloud Run (`insure-voice-agent`) |

---

## Architecture

```
POST /invoke {"message": "I'm 35, non-smoker, ₹8L income, need term life for family"}
        │
        ▼
[FastAPI — main.py — Cloud Run: insure-voice-agent]
  Runner(root_agent, InMemorySessionService)
  session_id propagated for multi-turn
        │
        ▼
[root_agent — LlmAgent — Gemini 2.5 Flash Lite, Vertex AI us-central1]
  Reads root_agent_prompt.md system instruction
  Extracts customer profile from natural language message
        │
        ▼ Tool 1: MCPToolset (Streamable HTTP)
[elastic-mcp-server-native — Cloud Run]
  MCP JSON-RPC:  initialize → tools/list → tools/call search_products(...)
  FastMCP mounted at Starlette root → endpoint at /mcp
  Runs ELSER v2 RRF hybrid query (sparse + BM25 + eligibility filters)
  Returns: {"candidates": [...up to 10 products...]}
        │
        ▼ Tool 2: FunctionTool(compliance_check)
[compliance_check — Cloud Function 2nd gen]
  POST $COMPLIANCE_CHECK_URL
  Payload: {"candidate_products": [...], "customer_profile": {...}}
  Deterministic Python rules only — no LLM (Constitution §II)
  Returns: {"passed": [...], "rejected": [{product_id, product_name, reasons}, ...]}
        │ passed empty?
        ├─ Yes → root_agent composes "all-rejected" voice script (from prompt guardrails)
        ▼ No
[Tool 3: FunctionTool(rank_products)] → Cloud Function 2nd gen
  POST $RANK_PRODUCTS_URL
  Payload: {"passed_products": [...], "customer_profile": {...}}
  Returns: {"top_3": [{rank, product_id, suitability_score, score_breakdown, explanation},...]}
        │
        ▼ Tool 4: AgentTool(recommend_and_explain)
[recommend_and_explain — inner LlmAgent — Gemini 2.0 Flash]
  Reads sub_agent3_explainer_prompt.md
  Receives top_3 + customer profile summary
  Returns: voice-ready prose ≤120 words, INR, WaveNet-safe, no markdown
        │
        ▼
Response: {"session_id": "...", "response": "Based on your profile, here are my top 3..."}
```

### Field-name contract (critical — mismatches cause 400 errors)

| Tool call | Agent passes | Backend expects |
|---|---|---|
| `compliance_check` | `candidates`, `customer_profile` | `candidate_products`, `customer_profile` |
| `rank_products` | `eligible_candidates`, `customer_profile` | `passed_products`, `customer_profile` |

The `compliance_check()` and `rank_products()` wrapper functions in `agent_definition.py` translate agent-side names to API-side names before calling the Cloud Functions.

---

## File Structure

```text
agent_builder/
├── agent_definition.py          # LlmAgent + 4 tools (MCPToolset, 2×FunctionTool, AgentTool)
├── main.py                      # FastAPI Cloud Run entry point (/health, /invoke)
├── root_agent_prompt.md         # Root agent system instruction (5-step process + guardrails)
├── sub_agent1_search_prompt.md  # (legacy — kept for reference; search now via MCPToolset)
├── sub_agent3_explainer_prompt.md  # Inner LlmAgent instruction for voice explanation
├── tools.yaml                   # Tool definitions reference doc
├── Dockerfile                   # Builds agent runner image
└── requirements.txt             # google-adk, fastapi, uvicorn, httpx, pydantic

functions/
├── elastic_mcp_server/          # REST wrapper MCP server (legacy fallback)
│   ├── main.py
│   ├── Dockerfile
│   └── requirements.txt
├── elastic_mcp_server_native/   # FastMCP server — MCPToolset primary target
│   ├── main.py                  # FastMCP + Starlette; /mcp = MCP JSON-RPC endpoint
│   ├── Dockerfile
│   └── requirements.txt
├── compliance_check/
│   └── main.py                  # @functions_framework.http — deterministic rule engine
└── rank_products/
    └── main.py                  # @functions_framework.http — suitability scorer

infra/
└── cloudbuild.yaml              # Build + deploy all 5 services in correct dependency order

tests/
├── test_compliance_check.py     # Unit tests — compliance rules
├── test_rank_products.py        # Unit tests — ranking logic
├── test_product_search.py       # Unit tests — search wrapper
├── test_voice_explanation.py    # Unit tests — explainer sub-agent
└── smoke_test_live.py           # End-to-end smoke test against deployed URLs
```

---

## Deployment Order (Cloud Build)

1. Build + push `elastic-mcp-server` image → deploy to Cloud Run
2. Build + push `elastic-mcp-server-native` image → deploy to Cloud Run → capture URL (`mcp_native_url.txt`)
3. Copy `shared/` → deploy `compliance_check` Cloud Function
4. Copy `shared/` → deploy `rank_products` Cloud Function
5. Build + push agent runner image → deploy to Cloud Run
   - Injects `ELASTIC_MCP_SERVER_URL`, `ELASTIC_MCP_SERVER_NATIVE_URL` from captured URLs
   - Injects `COMPLIANCE_CHECK_URL`, `RANK_PRODUCTS_URL` from `$PROJECT_ID` + region template
   - `GOOGLE_GENAI_USE_VERTEXAI=TRUE` — ADC on Cloud Run; no API key required

---

## Deployed Services

| Service | URL | Purpose |
|---|---|---|
| `insure-voice-agent` | `https://insure-voice-agent-1055350728739.us-central1.run.app` | LlmAgent entry point |
| `elastic-mcp-server-native` | `https://elastic-mcp-server-native-1055350728739.us-central1.run.app` | MCPToolset target (`/mcp`) |
| `elastic-mcp-server` | `https://elastic-mcp-server-mhojvvbq4a-uc.a.run.app` | REST fallback (legacy) |
| `compliance_check` | `https://us-central1-voice-sales-agent.cloudfunctions.net/compliance_check` | Deterministic guardrail |
| `rank_products` | `https://us-central1-voice-sales-agent.cloudfunctions.net/rank_products` | Suitability ranking |

---

## Constitution Check

- [x] **Compliance guardrail respected** — `compliance_check` FunctionTool runs before `rank_products`; root agent prompt explicitly prohibits passing rejected products to `rank_products` or to the customer response; all-rejected path returns constraint explanation, not a hallucinated recommendation.
- [x] **Latency target honoured** — MCPToolset target budgeted < 2s; Cloud Functions < 0.5–1s each; explainer AgentTool < 1s; total < 8s. `httpx` timeouts enforce per-tool budgets (8s search, 5s compliance, 5s ranking).
- [x] **No hallucination risk in deterministic logic** — `compliance_check` Cloud Function uses pure Python predicates with no LLM calls (Constitution §II). Only the root agent and explainer sub-agent use Gemini.
- [x] **Audit trail considered** — root agent prompt (Step 2) instructs logging candidate product IDs + ELSER scores to Cloud Logging before compliance check; `rank_products` returns `score_breakdown` per product; `session_id` propagated for correlation. PII excluded from logs per Constitution §V.

---

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Single LlmAgent vs multi-agent hierarchy | Single `LlmAgent` with 4 tools | ADK `LlmAgent` + function-calling is more stable in ADK 2.1.0, lower latency (no inter-agent message overhead), single deployment unit |
| MCPToolset for search_products | `MCPToolset(StreamableHTTPConnectionParams(url=.../mcp))` | Demonstrates genuine MCP JSON-RPC protocol (`initialize → tools/list → tools/call`) — required for Elastic Partner Track judging |
| Do NOT also register `FunctionTool(search_products)` | MCPToolset only | Gemini returns `400 INVALID_ARGUMENT: Duplicate function declaration found: search_products` if both are registered |
| Explainer as `AgentTool` (inner LlmAgent) | `AgentTool(LlmAgent(gemini-2.0-flash, ...))` | Separates explanation generation from orchestration logic; explainer prompt can be tuned independently; Gemini 2.0 Flash is sufficient and cheaper for prose generation |
| Model for root agent | `gemini-2.5-flash-lite` | Best latency/quality balance for tool-calling orchestration on Vertex AI `us-central1` |
| Session service | `InMemorySessionService` | Sufficient for hackathon demo; stateless between Cloud Run instances is acceptable for single-session demos. Firestore-backed session service is Phase 2 |
| Field-name translation in wrappers | `compliance_check()` maps `candidates → candidate_products`; `rank_products()` maps `eligible_candidates → passed_products` | Gemini generates natural argument names from docstrings; backend APIs use their own validated field names. Translation layer avoids changing deployed Cloud Functions |
| Two MCP server variants | `elastic_mcp_server` (REST + FastAPI) and `elastic_mcp_server_native` (FastMCP + Starlette) | Native variant is the MCPToolset target (MCP protocol). REST variant is retained as a debugging/fallback endpoint |
| FastAPI runner on Cloud Run | `main.py` with `/health` + `/invoke` | Cloud Run requires an HTTP server; FastAPI provides liveness probe for health checks and a clean REST contract for Dialogflow CX integration |

---

## Latency Budget (per-tool)

| Tool / Step | Timeout in Code | Target |
|---|---|---|
| Root agent profile extraction | — (Gemini streaming) | < 1s |
| MCPToolset → elastic-mcp-server-native → ELSER search | 8.0s (`httpx`) | < 2s |
| FunctionTool(compliance_check) | 5.0s (`httpx`) | < 0.5s |
| FunctionTool(rank_products) | 5.0s (`httpx`) | < 1s |
| AgentTool(recommend_and_explain) | — (Gemini streaming) | < 1s |
| **Total** | | **< 8s** |

---

## Multi-Turn Conversation

`session_id` is returned on every `/invoke` response. The caller (Dialogflow CX / voice pipeline) passes it back on subsequent turns. `InMemorySessionService` maintains the full tool-call history and LlmAgent state within the session, enabling follow-up questions ("tell me more about the second one") without re-running the pipeline.

---

## Open Questions

1. **Firestore session persistence**: `InMemorySessionService` loses state if Cloud Run scales to multiple instances or restarts. For production, replace with ADK's Firestore-backed session service and enable session affinity.
2. **Cloud Logging audit writes**: The root agent prompt instructs Gemini to log audit data — but Gemini cannot directly write to Cloud Logging. A thin middleware in `main.py`'s `/invoke` handler should capture the `Runner` event stream and write structured audit log entries (product IDs, ELSER scores, compliance outcomes) explicitly.
3. **Dialogflow CX webhook**: The `/invoke` endpoint is callable from Dialogflow CX as a webhook. The request/response schema may need adaptation to the Dialogflow webhook envelope format for live voice integration.
4. **Cold-start latency**: Cloud Run cold starts (especially MCPToolset's MCP initialize handshake) can add 2–4s on first request. `min-instances: 1` in Cloud Run config eliminates this for production demos.

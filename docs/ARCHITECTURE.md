# InsureVoice — Architecture Deep Dive
## Google Cloud Agent Builder + Elastic ELSER + Dialogflow CX

> **Last updated**: 2026-05-29  
> **Status**: Production — all Cloud Run services and Cloud Functions deployed, end-to-end agent invoke confirmed working.

---

## Deployed Services (Production)

| Service | Type | URL | Transport |
|---|---|---|---|
| `elastic-mcp-server` | Cloud Run | `https://elastic-mcp-server-mhojvvbq4a-uc.a.run.app` | REST `/search_products` |
| `elastic-mcp-server-native` | Cloud Run | `https://elastic-mcp-server-native-1055350728739.us-central1.run.app` | MCP `/mcp` (Streamable HTTP) |
| `insure-voice-agent` | Cloud Run | `https://insure-voice-agent-1055350728739.us-central1.run.app` | FastAPI `/invoke`, `/health` |
| `compliance_check` | Cloud Function (2nd gen) | `https://us-central1-voice-sales-agent.cloudfunctions.net/compliance_check` | HTTP POST |
| `rank_products` | Cloud Function (2nd gen) | `https://us-central1-voice-sales-agent.cloudfunctions.net/rank_products` | HTTP POST |

**LLM**: Gemini 2.5 Flash Lite (`gemini-2.5-flash-lite`) on Vertex AI `us-central1`  
**GCP Project**: `voice-sales-agent` (project number `1055350728739`)  
**Elasticsearch**: `https://my-elasticsearch-project-c2e88f.es.us-central1.gcp.elastic.cloud:443`  
**Secret Manager**: `ES_API_KEY` secret (version 7, CRLF-free)

---

## System Overview

InsureVoice is a multi-agent AI system with four distinct functional layers. Each layer is independently deployable and testable.

```
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 1 — VOICE INTERFACE                                      │
│  Dialogflow CX · Cloud STT (streaming) · Cloud TTS WaveNet     │
└───────────────────────────┬─────────────────────────────────────┘
                            │ structured conversation turns
┌───────────────────────────▼─────────────────────────────────────┐
│  LAYER 2 — AGENT ORCHESTRATION (ADK + google-adk 2.1.0)        │
│  insure-voice-agent (Cloud Run)                                  │
│  LlmAgent · Gemini 2.5 Flash Lite · InMemorySessionService     │
│  Tool 1: MCPToolset → elastic-mcp-server-native /mcp            │
│  Tool 2: FunctionTool(compliance_check) → Cloud Function        │
│  Tool 3: FunctionTool(rank_products) → Cloud Function           │
└───────┬───────────────────┬──────────────────────┬─────────────┘
        │                   │                      │
┌───────▼───────┐  ┌────────▼────────┐  ┌──────────▼──────────┐
│ MCPToolset    │  │ FunctionTool    │  │ FunctionTool        │
│ search_       │  │ compliance_     │  │ rank_products       │
│ products      │  │ check           │  │                     │
│               │  │                 │  │                     │
│ elastic-mcp-  │  │ Cloud Function  │  │ Cloud Function      │
│ server-native │  │ compliance_     │  │ rank_products       │
│ (FastMCP +    │  │ check           │  │                     │
│ Starlette)    │  │                 │  │                     │
└───────┬───────┘  └─────────────────┘  └─────────────────────┘
        │
┌───────▼─────────────────────────────────────────────────────────┐
│  LAYER 3 — SEARCH INTELLIGENCE (Elastic Cloud Serverless)       │
│  ELSER v2 · semantic_text fields · RRF hybrid retriever         │
│  Alias: insurance_products_current (28 products)                │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layer 1: Voice Interface

### Components

| Component | Technology | Role |
|---|---|---|
| Speech-to-Text | Google Cloud STT (streaming) | Transcribes customer voice in real time |
| Conversation Manager | Dialogflow CX | Manages intents, session state, routing to Agent Builder |
| Text-to-Speech | Google Cloud TTS WaveNet (`en-IN-Wavenet-D`) | Synthesizes agent response as voice |

### Dialogflow CX Intent Design

```
├── Default Welcome Intent        → greet + prompt for customer profile
├── provide_profile               → captures: age, income, health, smoker, goals
│   └── triggers: recommend flow → Root Agent
├── ask_about_product             → ad-hoc product Q&A → Vertex AI data store
├── accept_recommendation         → triggers Phase 2 workflow stub
└── reject_recommendation         → ask for different preferences → loop back
```

### Voice Latency Targets

| Step | Target Latency |
|---|---|
| STT transcription | < 1.5s (streaming, end-of-utterance detection) |
| Agent Builder + sub-agents | < 5s |
| TTS synthesis | < 0.5s |
| **Total end-to-end** | **< 8s** |

---

## Layer 2: Agent Orchestration

### ADK Implementation (`agent_builder/`)

The agent is a single `LlmAgent` (not a sub-agent hierarchy) running on `google-adk 2.1.0` with three registered tools. It is served by a FastAPI Cloud Run service (`agent_builder/main.py`).

```python
# agent_builder/agent_definition.py (authoritative)
root_agent = LlmAgent(
    model="gemini-2.5-flash-lite",    # Vertex AI us-central1
    name="InsureVoice",
    tools=[
        MCPToolset(                    # Tool 1 — MCP-native search
            connection_params=StreamableHTTPConnectionParams(
                url=f"{ELASTIC_MCP_SERVER_NATIVE_URL}/mcp"
            )
        ),
        FunctionTool(compliance_check),  # Tool 2 — deterministic guardrail
        FunctionTool(rank_products),     # Tool 3 — suitability scoring
    ]
)
```

**Environment variables** required by `insure-voice-agent` Cloud Run:

| Variable | Value |
|---|---|
| `ELASTIC_MCP_SERVER_URL` | `https://elastic-mcp-server-mhojvvbq4a-uc.a.run.app` (REST, legacy) |
| `ELASTIC_MCP_SERVER_NATIVE_URL` | `https://elastic-mcp-server-native-1055350728739.us-central1.run.app` |
| `COMPLIANCE_CHECK_URL` | `https://us-central1-voice-sales-agent.cloudfunctions.net/compliance_check` |
| `RANK_PRODUCTS_URL` | `https://us-central1-voice-sales-agent.cloudfunctions.net/rank_products` |
| `GOOGLE_GENAI_USE_VERTEXAI` | `TRUE` |
| `GOOGLE_CLOUD_PROJECT` | `voice-sales-agent` |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` |

### MCPToolset — Architecture Decision

**Decision**: Use `MCPToolset` (MCP Streamable HTTP) for `search_products`, not `FunctionTool`.

**Why**: The hackathon requires demonstrating Elastic MCP server integration. `MCPToolset` uses the real MCP JSON-RPC protocol (`initialize` → `tools/list` → `tools/call`), proving the integration is genuine.

**Critical fix — FastMCP double-nesting problem**:

```python
# BROKEN (elastic-mcp-server, FastAPI outer app):
app = FastAPI()
app.mount("/mcp", mcp.http_app())
# FastMCP internal route /mcp served at /mcp/mcp
# MCPToolset gets 404 on /mcp → silently fails → tool not registered

# FIXED (elastic-mcp-server-native, Starlette outer app):
app = mcp.http_app(stateless_http=True)          # FastMCP IS the root ASGI app
app.add_middleware(_HealthMiddleware)              # /health intercepted by middleware
# FastMCP internal /mcp served at /mcp ✓
# MCPToolset connects to .../mcp → works
```

**FastMCP lifespan requirement**: The `StreamableHTTPSessionManager` task group must be initialized at startup. Using `mcp.http_app()` directly as the ASGI app (rather than mounting inside a parent app) satisfies this automatically. The error `StreamableHTTPSessionManager task group was not initialized` is caused by wrapping the app in Starlette/FastAPI without forwarding the lifespan context.

**`stateless_http=True`**: Required for Cloud Run (no persistent SSE connections between requests). Moved from `FastMCP()` constructor (removed in fastmcp 3.x) to `mcp.http_app(stateless_http=True)`.

### Tool API Contracts

**Tool 1 — MCPToolset `search_products`** (auto-discovered from `/mcp`):
```
Input:  query, customer_age, is_smoker, income, product_type?, size?, relax_age_filter?
Output: {"candidates": [...], "total_hits": int, "fallback_triggered": bool}
```

**Tool 2 — `compliance_check`** (`POST /compliance_check` Cloud Function):
```
Input:  {"candidate_products": [...], "customer_profile": {age, income, smoker, health_status, coverage_goals}}
Output: {"passed": [...full product dicts...], "rejected": [{"product_id", "product_name", "reasons"}]}
```
⚠️ Field name: `candidate_products` (not `candidates`)

**Tool 3 — `rank_products`** (`POST /rank_products` Cloud Function):
```
Input:  {"passed_products": [...], "customer_profile": {...}}
Output: {"top_3": [{"rank", "product_id", "suitability_score", "score_breakdown", "explanation"}]}
```
⚠️ Field name: `passed_products` (not `eligible_candidates`)

```
Root Agent receives: raw voice transcript
Root Agent does:
  1. Extract structured customer profile using Gemini function-calling
     Profile: {age, income, smoker, health_status, sum_need, coverage_goals[]}
  2. Call Sub-Agent 1 with structured query → receives candidate products
  3. Call Sub-Agent 2 with candidates + profile → receives filtered products
  4. Call Sub-Agent 3 with filtered products → receives ranked recommendations
  5. Return explanation to Dialogflow CX for TTS delivery
```

### Root Agent System Prompt Design (`agent_builder/root_agent_prompt.md`)

```
You are InsureVoice, an AI-powered insurance sales advisor.

ROLE: You help insurance sales agents match customers to the right insurance products.

PROCESS — always follow these steps in order:
1. EXTRACT: Parse the customer's voice input to extract:
   - Age (years), Monthly/Annual income (INR), Smoker (yes/no)
   - Health status (healthy / pre-existing conditions), Family size
   - Coverage goals (life, health, investment, critical illness, accident)
   - Desired sum assured (if stated)

2. SEARCH: Use search_products (MCPToolset → elastic-mcp-server-native) to find candidates.

3. VALIDATE: Use compliance_check to filter ineligible products.
   CRITICAL: Never recommend a product that compliance_check returned as rejected.

4. RANK: Use rank_products to score and rank the passed products.

5. RESPOND: Deliver the top-3 in voice-friendly tone, ≤ 120 words.

GUARDRAILS:
- Never recommend a product if compliance_check returned it as rejected
- If ALL products are rejected, explain why and ask for updated profile
- Never make medical or legal claims
- Always clarify final underwriting is subject to insurer terms
```

### Tool 1: `search_products` via MCPToolset

**Service**: `elastic-mcp-server-native` (Cloud Run, FastMCP 3.3.1 on Starlette)  
**MCP endpoint**: `POST /mcp` (Streamable HTTP JSON-RPC)

**RRF Hybrid Query** (two ELSER semantic legs + one BM25 leg):
```
Leg A: semantic on description + semantic on key_feature
Leg B: multi_match on name^2, tags, sales_pitch
Hard filters: is_active, min_income ≤ income, age bounds, smoker_eligible
rank_window_size=20, rank_constant=60
```

**Why ELSER**: "comprehensive illness protection for my family" does NOT keyword-match "Critical Illness Rider". ELSER sparse vectors encode the semantic association. BM25 returns zero; ELSER returns the correct product.

### Tool 2: `compliance_check` (Cloud Function)

**POST** `https://us-central1-voice-sales-agent.cloudfunctions.net/compliance_check`

**Request body** (exact field names — Pydantic-validated):
```json
{
  "candidate_products": [...],
  "customer_profile": {
    "age": 35, "income": 800000, "smoker": false,
    "health_status": "healthy", "coverage_goals": ["life"]
  }
}
```

**Compliance rules** (`functions/compliance_check/main.py`):

| Rule ID | Predicate |
|---|---|
| `AGE_MIN` | `customer.age >= product.min_age` |
| `AGE_MAX` | `customer.age <= product.max_age` |
| `SMOKER_EXCLUSION` | `not (smoker and not smoker_eligible)` |
| `INCOME_SUM_CAP` | `sum_need ≤ income × 10` |
| `MEDICAL_EXAM_REQUIRED` | `not (sum_need > medical_required_above and health_status != "healthy")` |

**Why Cloud Function (not LLM)**: Constitution §II — Zero hallucination on eligibility. Rules are pure Python predicates; 100% deterministic, auditable, testable.

**Response asymmetry (G8)**: `passed[]` = full product dicts; `rejected[]` = `{product_id, product_name, reasons}` only.

### Tool 3: `rank_products` (Cloud Function)

**POST** `https://us-central1-voice-sales-agent.cloudfunctions.net/rank_products`

**Request body** (exact field names — Pydantic-validated):
```json
{
  "passed_products": [...],
  "customer_profile": { "age": 35, "income": 800000, ... }
}
```

**Scoring Formula**:
```
suitability_score = (elser_score × 0.4) + (age_centrality × 0.3) + (income_fit × 0.3)

age_centrality = 1 - |age - product_midpoint_age| / (max_age - min_age)
income_fit     = min(income / (sum_need / 10), 1.0)
```

---

## Layer 3: Search Intelligence

### Elasticsearch Index Schema (authoritative — `ingest/create_index.py`)

**Infrastructure**: Elastic Cloud Serverless — built-in EIS. No manual inference endpoint needed.  
**Index**: `insurance_products_v1` → **Alias**: `insurance_products_current`

```json
{
  "mappings": {
    "properties": {
      "id":                    { "type": "keyword" },
      "name":                  { "type": "text", "fields": { "keyword": { "type": "keyword" } } },
      "product_type":          { "type": "keyword" },
      "description":           { "type": "semantic_text" },
      "key_feature":           { "type": "semantic_text" },
      "min_age":               { "type": "integer" },
      "max_age":               { "type": "integer" },
      "smoker_eligible":       { "type": "boolean" },
      "is_active":             { "type": "boolean" },
      "min_income":            { "type": "long" },
      "max_sum_assured":       { "type": "long" },
      "medical_required_above":{ "type": "long" },
      "tags":                  { "type": "keyword" },
      "sales_pitch":           { "type": "text" },
      "premium_min_monthly":   { "type": "integer" },
      "premium_max_monthly":   { "type": "integer" }
    }
  }
}
```

### ELSER Inference Endpoint

```json
PUT _inference/sparse_embedding/elser-v2-endpoint
{
  "service": "elasticsearch",
  "service_settings": {
    "adaptive_allocations": {
      "enabled": true,
      "min_number_of_allocations": 1,
      "max_number_of_allocations": 4
    },
    "num_threads": 1,
    "model_id": ".elser_model_2"
  }
}
```

### Elastic MCP Server — Two Deployments

**elastic-mcp-server** (original, REST + broken MCP):
- `POST /search_products` — REST endpoint, fully working
- `POST /mcp` — MCP endpoint, broken due to double-nesting (`/mcp/mcp`)
- Not used by MCPToolset; kept for Agent Builder `tools.yaml` demo and REST fallback

**elastic-mcp-server-native** (new, MCP-native):
- `GET /health` — liveness probe (intercepted by `_HealthMiddleware`)
- `POST /mcp` — MCP Streamable HTTP endpoint at correct path (FastMCP as root ASGI)
- Used by `MCPToolset` in `agent_definition.py`
- Tool auto-discovered: `search_products` with full ELSER v2 RRF hybrid search

```python
# elastic_mcp_server_native/main.py — key pattern
mcp = FastMCP("insure-voice-elastic-native")

@mcp.tool()
def search_products(query, customer_age, is_smoker, income, ...):
    ...

app = mcp.http_app(stateless_http=True)      # FastMCP IS the ASGI root
app.add_middleware(_HealthMiddleware)          # /health handled before FastMCP
# uvicorn runs `app` on port 8080
```

---

## Data Flow Sequence (Production, 2026-05-29)

```
Customer: "I'm 35, non-smoker, ₹8L income, need term life for my family"
    │
    ▼ [POST /invoke on insure-voice-agent Cloud Run]
    │
    ▼ LlmAgent (Gemini 2.5 Flash Lite, Vertex AI us-central1)
      Extracts profile from message
    │
    ▼ MCPToolset → elastic-mcp-server-native /mcp
      MCP initialize → tools/list → tools/call search_products
      ELSER v2 RRF hybrid query against insurance_products_current
Candidates: [FutureSec Term, LifeGuard Plus, FamilyProtect 3Cr, ...] (10 results)
    │
    ▼ FunctionTool(compliance_check) → Cloud Function
      POST {"candidate_products": [...], "customer_profile": {age, income, smoker, ...}}
Passed:   [FutureSec Term, LifeGuard Plus, FamilyProtect 3Cr, ...]
Rejected: [<products failing age/income/smoker rules>]
    │
    ▼ FunctionTool(rank_products) → Cloud Function
      POST {"passed_products": [...], "customer_profile": {...}}
Top 3: ranked by suitability_score
    │
    ▼ LlmAgent composes voice response (≤ 120 words)
Response: "Considering your age and income, here are the top 3 term life insurance plans..."
    │
    ▼ [JSON {"session_id": ..., "response": "..."} returned to caller]
```

**Confirmed end-to-end latency**: ~7s (within 8s target)
    ▼ Root Agent — compose voice response
"Based on your profile, here are my top 3 recommendations..."
    │
    ▼ Cloud TTS WaveNet (~0.4s)
Voice output → customer hears recommendation
```

**Total latency**: ~7.2 seconds (within 8s target)

---

## Phase 2: Workflow Integration (Post-Hackathon)

When customer accepts a recommendation:

```
accept_recommendation intent
    │
    ▼ Pub/Sub: topic = insurance.recommendation.accepted
    Message: {
      session_id, customer_profile, recommended_product_id,
      timestamp, agent_id, recommendation_score
    }
    │
    ▼ Workflow Engine (Camunda BPMN / Cloud Workflows)
    Process: Begin Insurance Application Workflow
      ├── KYC verification
      ├── Document collection
      ├── Underwriting trigger
      └── Proposal generation
```

---

## Security Considerations

| Concern | Mitigation |
|---|---|
| API keys in environment | GCP Secret Manager for Elastic API key + all credentials |
| Cloud Function auth | Require IAM authentication for compliance_check and rank_products in production |
| Customer voice data | Dialogflow CX: disable data logging for PII; STT audio not persisted |
| Elasticsearch access | API key scoped to `insurance_products` index only (read-only) |
| Open-source IP exposure | Synthetic data only; no real product pricing; no client data |

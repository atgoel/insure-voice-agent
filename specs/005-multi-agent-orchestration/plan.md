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
2. ~~**Cloud Logging audit writes**: The root agent prompt instructs Gemini to log audit data — but Gemini cannot directly write to Cloud Logging. A thin middleware in `main.py`'s `/invoke` handler should capture the `Runner` event stream and write structured audit log entries (product IDs, ELSER scores, compliance outcomes) explicitly.~~ **RESOLVED** — `_write_audit_log()` function is fully implemented in `main.py`. It captures the ADK `Runner` event stream via `_tool_results` dict populated from `function_response` parts, then writes a PII-free structured entry `{session_id, candidate_products, compliance_outcomes, final_rankings}` to Cloud Logging (with stderr fallback for local dev). No LLM involvement in logging.
3. **Dialogflow CX webhook**: The `/invoke` endpoint is callable from Dialogflow CX as a webhook. The request/response schema may need adaptation to the Dialogflow webhook envelope format for live voice integration.
4. **Cold-start latency**: Cloud Run cold starts (especially MCPToolset's MCP initialize handshake) can add 2–4s on first request. `min-instances: 1` in Cloud Run config eliminates this for production demos.

---

---

# Phase 2 Implementation Plan: Product Deep-Dive Pitch & Premium Simulation

**Stories**: 5 (Product Deep-Dive Pitch) + 6 (Premium Simulation) | **Added**: 2026-06-04
**Status**: Partially implemented — Cloud Function + data layer done; agent wiring + frontend remaining.

---

## Summary

Story 5 adds a pitch mode to the `recommend_and_explain` agent: when a customer asks "tell me more about that one", the explainer delivers eligibility prerequisites, key product features (catalog data only — no fabrication), and a suitability-score comparison against the other top-3. Story 6 adds a deterministic `simulate_premium` Cloud Function and a `POST /simulate` proxy endpoint so the frontend can re-calculate premium + projected returns on-the-fly when the customer changes coverage sliders — without re-triggering the full agent pipeline.

---

## Technical Context

| Field | Value |
|---|---|
| Language | Python 3.11 |
| New Cloud Function | `functions/simulate_premium/main.py` — `@functions_framework.http`, no LLM |
| New Agent Tools | `FunctionTool(simulate_premium)` registered in `agent_definition.py` |
| New API Endpoint | `POST /simulate` in `agent_builder/main.py` — pass-through proxy to `SIMULATE_PREMIUM_URL` |
| Channel flag | `channel: "voice" | "text"` on `/invoke` body — controls 120-word limit |
| Frontend additions | Simulation panel in `frontend/simulation.js` + `frontend/index.html` — calls `/simulate` directly |
| Deployment | New Cloud Build step for `simulate_premium` in `infra/cloudbuild.yaml` |
| Testing | `tests/test_simulate_premium.py` (35 tests, no network/LLM) ✅ done |

---

## Architecture — New Data Flows

### Story 5 — Pitch Mode (voice/text channel-aware)

```
POST /invoke {"message": "Tell me more about the first plan", "session_id": "...", "channel": "text"}
        │
        ▼
[FastAPI — main.py]
  channel = body.get("channel", "voice")  → "text"
  Appends "[CHANNEL: text — full structured detail permitted, no 120-word limit]"
  to synthetic message fed to LlmAgent Runner
        │
        ▼
[root_agent — LlmAgent]
  Detects pitch-intent utterance (e.g., "tell me more", "explain", "details")
  Identifies referenced product from session history (rank-1/2/3 or named product)
  Routes to AgentTool(recommend_and_explain) with pitch_mode=True context
        │
        ▼
[recommend_and_explain — inner LlmAgent — Gemini 2.0 Flash]
  sub_agent3_explainer_prompt.md (pitch mode section):
    — Eligibility prerequisites: sourced from compliance_check rules
    — Key features: product catalog fields (benefits[], key_feature, tags) — no fabrication
    — Suitability delta: compare suitability_score vs. other top-3 products
    — Returns (if savings product): return_rate field from catalog only (§II)
    — Voice channel → ≤ 120 words; Text channel → full structured output
        │
        ▼
Response: {"session_id": "...", "response": "...", "channel": "text"}
```

### Story 6 — Premium Simulation (bypasses full pipeline)

```
POST /simulate {"product_id":"ULIP001","sum_assured":1000000,"customer_age":35,
                "is_smoker":false,"premium_frequency":"monthly","policy_term":15}
        │
        ▼
[FastAPI /simulate — main.py proxy handler]
  Validates body has required keys (fast 400 if missing product_id)
  POST $SIMULATE_PREMIUM_URL with raw body
        │
        ▼
[simulate_premium — Cloud Function 2nd gen]
  _validate(): product_id exists, SA ≥ 1L ≤ max, age in range, smoker eligible,
               frequency in enum, policy_term in available_terms
  _simulate(): base_rate_per_lakh → age_bands loading → smoker_loading →
               frequency_multiplier → period_premium
               For savings types: FV annuity = PMT×((1+r_period)^n−1)/r_period,
               clamped to max(fv_annuity, sum_assured)
               For protection types: maturity = null, net_gain = null
  Returns 200 with: period_premium, annual_premium, total_premium_outflow,
                    projected_maturity_value, net_gain, formula_breakdown
        │
        ▼
[Frontend simulation panel — simulation.js]
  Renders period_premium, total_premium_outflow, projected_maturity_value
  On slider/dropdown change → re-calls POST /simulate immediately
  Does NOT call POST /invoke (no pipeline re-trigger)
```

### Agent-side simulation (Story 6 AC 8)

```
POST /invoke {"message": "What would the premium be if I take ₹50 lakh cover monthly?"}
        │
        ▼
[root_agent] detects explicit simulation intent → calls FunctionTool(simulate_premium)
  with values extracted from utterance + session profile
        │
        ▼ FunctionTool(simulate_premium) → POST $SIMULATE_PREMIUM_URL
        ▼
[root_agent] reads period_premium + projected_maturity_value from tool response
  Narrates result in voice-safe prose — never infers numbers from Gemini (§II)
```

---

## File Structure — Changes from Phase 1

```text
# NEW files (already created ✅)
functions/simulate_premium/
├── main.py                   # ✅ Deterministic Cloud Function — premium + FV formula
└── requirements.txt          # ✅ functions-framework==3.*

tests/
└── test_simulate_premium.py  # ✅ 35 unit tests — no network/LLM

# MODIFIED files (pending ❌)
agent_builder/
├── agent_definition.py       # ❌ Add simulate_premium() FunctionTool wrapper
├── main.py                   # ❌ Add POST /simulate proxy endpoint
│                             #    (channel flag already added ✅)
├── root_agent_prompt.md      # ❌ Add pitch-intent routing + simulate_premium tool use
└── sub_agent3_explainer_prompt.md  # ❌ Add pitch mode section (channel-aware)

infra/
└── cloudbuild.yaml           # ❌ Add simulate_premium deploy step + SIMULATE_PREMIUM_URL env var

frontend/
├── index.html                # ❌ Add simulation panel HTML (sliders, output card)
└── simulation.js             # ❌ Add fetchSimulation() calling POST /simulate
```

---

## Constitution Check

- [x] **Compliance guardrail respected** — `simulate_premium` and pitch mode are post-recommendation features; compliance already ran before recommendations were delivered. Simulation does not bypass compliance gate.
- [x] **Latency target honoured** — `/simulate` is a direct Cloud Function call with no LLM involved; < 0.5s. Pitch mode adds one extra AgentTool call to the already-established session; total still < 8s.
- [x] **No hallucination risk in deterministic logic** — `simulate_premium` is pure Python (no Gemini); catalog `return_rate` is the sole source for projected returns; `base_rate_per_lakh`, `age_bands`, `smoker_loading_pct`, `frequency_multipliers` are all catalog fields. Constitution §II satisfied.
- [x] **Audit trail considered** — `/simulate` calls are logged by `_write_audit_log()` in `main.py` when initiated via agent (Story 6 AC 8). Direct frontend calls to `/simulate` bypass the agent audit — acceptable because no recommendation is being made; simulation is exploratory.

---

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| `simulate_premium` as Cloud Function (not Cloud Run) | Cloud Function 2nd gen | Stateless, event-driven, zero cold-start for infrequent calls; no persistent container overhead |
| `/simulate` as proxy endpoint on agent Cloud Run | Pass-through `httpx.post` in `main.py` | Frontend only knows one backend URL; avoids CORS issues; keeps `SIMULATE_PREMIUM_URL` as a server-side secret |
| FunctionTool(simulate_premium) in agent | Register alongside compliance_check / rank_products | Allows voice turns like "what would ₹50L monthly cost?" to be answered deterministically via tool call, not Gemini inference |
| Pitch mode via `channel` flag + prompt section | Append channel hint to synthetic message; extend `sub_agent3_explainer_prompt.md` | Avoids a separate endpoint; reuses existing session history for product context |
| `return_rate` as catalog field (not live NAV) | Fixed field in `data/insurance_products.json` | Hackathon constraint: no live fund feed integration. Constitution §II prohibits LLM estimation. Catalog value is clearly labelled as illustrative |
| Frontend simulation panel calls `/simulate` directly | `POST /simulate` from `simulation.js`; no `/invoke` | Full pipeline re-trigger (STT → agent → search → comply → rank → TTS) would be 8s per slider move — unacceptable for interactive UI. Direct call is < 500ms |
| FV annuity formula, clamped to max(FV, SA) | Standard compound accumulation: `PMT×((1+r_period)^n−1)/r_period` | Industry-standard illustration formula for savings products; SA floor prevents showing maturity < cover amount |
| `available_terms` per product in catalog | Listed explicitly (e.g., `[10,15,20,25,30]`) | Validation rejects any term not in the list; prevents nonsensical inputs (e.g., 7-year term on a product only offered at 10/15/20) |

---

## Latency Budget — New Components

| Component | Timeout | Target |
|---|---|---|
| `POST /simulate` proxy (agent Cloud Run → Cloud Function) | 5.0s (`httpx`) | < 0.5s |
| `FunctionTool(simulate_premium)` via agent | (Gemini function-call flow) | < 1s incl. round-trip |
| Pitch-mode AgentTool call (extra turn in session) | — (Gemini streaming) | < 2s (text channel); < 1.5s (voice channel) |
| Frontend slider → `/simulate` round-trip | — (browser `fetch`) | < 500ms |

---

## Open Questions

1. **`SIMULATE_PREMIUM_URL` secret management**: The Cloud Function URL will be known only after the first `simulate_premium` deploy. The agent Cloud Run needs this injected as an env var. Cloud Build Step ordering must capture the URL (like `mcp_native_url.txt`) and pass it to the agent deploy step.
2. **Frontend `/simulate` base URL**: `frontend/index.html` uses `<meta name="invoke-url">` for the agent URL. A second meta tag (`<meta name="simulate-url">`) or a query-param convention should carry the `/simulate` endpoint for local-dev vs. production switching.
3. **Pitch mode product identification**: When the customer says "tell me more about the second one", the agent must identify which product is rank-2 from `InMemorySessionService` history. If the session has expired or recommendations haven't been delivered yet, the agent must re-prompt for profile rather than hallucinating a product.

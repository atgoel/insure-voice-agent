# InsureVoice — Architecture Deep Dive
## Google Cloud Agent Builder + Elastic ELSER + Vertex AI Gemini

> **Last updated**: 2026-06-03 (Day 6 EOD, post-bundle deploy)
> **Status**: Production — Day 5 stability sprint + Day 6 Atul-domain follow-up bundle deployed; full demo arc validated end-to-end against live infrastructure.
> **Active branch**: `abhishek-stable-branch` (parent: `abhishek-day5-stability` @ commit `6370905`)

---

## Deployed Services (Production)

| Service | Type | URL | Transport |
|---|---|---|---|
| `elastic-mcp-server` | Cloud Run | `https://elastic-mcp-server-mhojvvbq4a-uc.a.run.app` | REST `POST /search_products` |
| `elastic-mcp-server-native` | Cloud Run | `https://elastic-mcp-server-native-1055350728739.us-central1.run.app` | MCP `/mcp` (Streamable HTTP) — **kept as audit/demo asset; not on hot path** |
| `insure-voice-agent` | Cloud Run | `https://insure-voice-agent-mhojvvbq4a-uc.a.run.app` | FastAPI `/invoke`, `/health` |
| `compliance_check` | Cloud Function (2nd gen) | `https://us-central1-voice-sales-agent.cloudfunctions.net/compliance_check` | HTTP POST |
| `rank_products` | Cloud Function (2nd gen) | `https://us-central1-voice-sales-agent.cloudfunctions.net/rank_products` | HTTP POST |

**LLM**: Gemini 2.5 Flash Lite (`gemini-2.5-flash-lite`) on Vertex AI `us-central1`
**Sub-agent LLM**: Gemini 2.5 Flash (`gemini-2.5-flash`) — `recommend_and_explain` only
**GCP Project**: `voice-sales-agent` (project number `1055350728739`)
**Elasticsearch**: `https://my-elasticsearch-project-c2e88f.es.us-central1.gcp.elastic.cloud:443`
**Secret Manager**: `ES_API_KEY` secret (version 7, CRLF-free)

---

## What Changed Day 5 → Day 6 (Stability + Demo Hardening)

The architecture below reflects significant changes from the original Day 1-3 build. This section is the **landmark map** so future readers don't get lost.

### Day 5 — Stability Sprint
- **C.1 + C.3** — Explicit temperature on root agent (0.25) + sub-agent (0.3) + max_output_tokens=400. Reduced LLM variance.
- **C.4** — Attempted root model upgrade to `gemini-2.5-flash`. Rolled back (no improvement).
- **C.5** — ADK `before_model_callback` with `tool_config={mode:ANY, allowed_function_names:[...]}`. **Mechanically enforces tool-call sequence** instead of trusting prompt rules. AC-3 went 0/15 → 5/5 PASS.
- **C.5b** — Session-state argument substitution + programmatic completion + deterministic template fallback. **search_products stashes candidates in session state**; **compliance_check ignores LLM-passed `candidates` arg** and reads from session state instead (flash-lite reliably passes `[null, null, null, null]`). AC-3 went 5/5 → 10/10 PASS.
- **P.1** — Attempted root prompt full rewrite. Failed. Kept old prompt.
- **P.2** — Conversational intake state machine in `intake.py` (deterministic Python validators for 8 fields). Module-level `_INTAKE_BY_SESSION` dict because **ADK `InMemorySessionService` does NOT reliably persist mutations to `session.state` across `get_session` calls** in this deployment.
- **F.1** — FE empty-response fallback (Day 4 already shipped).
- **F.2** — Mojibake fix at egress (`cp1252 → utf-8` reversal). Fixed `â‚¹` → `₹` in voice text and product cards.
- **MCPToolset → REST FunctionTool switch** — search_products migrated from MCP-native (Streamable HTTP JSON-RPC) to plain REST FunctionTool (`POST $ELASTIC_MCP_SERVER_URL/search_products`). Same backend, simpler integration. The MCP-native server (`elastic-mcp-server-native`) is preserved as an audit/demo artifact for the hackathon's Elastic Partner Track requirement, but the hot path is REST.

### Day 6 — Atul-Domain Follow-up + Demo Hardening
- **S2'** — `product_type` argument injector. Mechanically injects `product_type` into `search_products` HTTP payload from validated intake `coverage_goals[0]`. Overrides whatever the LLM passed (or didn't). Bug 6 (mixed product types) eliminated.
- **S3** — Follow-up state machine. Detects "tell me about X" / "second one" / "start over" intents BEFORE LLM dispatch. Routes to deterministic single-product voice text generator. NO LLM call on follow-up turns. Bugs 9, 10 fixed.
- **S4** — Defense-in-depth prompt rules in `sub_agent3_explainer_prompt.md`. Catalog facts + smoker logic + premium-source clarification. Per L-001, prompt-only rules are unreliable on small models — these are belt-and-suspenders for cases where LLM-prose path is exercised.
- **S5 timeout fix** — `httpx.post(timeout=2.5)` → `timeout=8.0`. CF cold-start + ELSER inference + RRF query routinely takes 3-5s. The 2.5s timeout was a silent demo blocker masquerading as ELSER zero-result.

For the per-sub-task evidence trail, see `STABILITY_CHANGELOG.md` (the canonical record).

---

## System Overview

InsureVoice is a multi-layer AI system. Each layer is independently deployable and testable.

```
┌──────────────────────────────────────────────────────────────────────┐
│  LAYER 1 — VOICE INTERFACE (browser-native, no Dialogflow)           │
│  Web Speech API STT · TTS WaveNet · WebRTC mic input                 │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ user message / session_id
┌──────────────────────────▼───────────────────────────────────────────┐
│  LAYER 2 — AGENT ORCHESTRATION                                       │
│  insure-voice-agent (Cloud Run) — FastAPI /invoke                    │
│                                                                      │
│  ┌─ PRE-LLM (deterministic Python, NO LLM call) ────────────────┐  │
│  │  S3 reset detection ("start over" / "reset" / "begin again")  │  │
│  │  P.2 intake state machine (8 fields, regex validators)        │  │
│  │  S3 follow-up dispatch (named/ordinal "tell me about X")      │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              │                                       │
│  ┌─ LLM PIPELINE (only after intake complete + no follow-up) ───┐  │
│  │  LlmAgent · Gemini 2.5 Flash Lite · ADK 2.1.0                  │  │
│  │  ADK before_model_callback (C.5: mode=ANY tool routing)        │  │
│  │  Tool 1: FunctionTool(search_products) → REST CF               │  │
│  │     ├─ S2' product_type injection (mechanical override)        │  │
│  │     └─ session-state stash for C.5b candidate substitution     │  │
│  │  Tool 2: FunctionTool(compliance_check) → CF (C.5b sub)        │  │
│  │  Tool 3: FunctionTool(rank_products) → CF (C.5b sub)           │  │
│  │  Sub-Agent: AgentTool(recommend_and_explain) → Gemini 2.5 Flash│  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              │                                       │
│  ┌─ POST-LLM (deterministic completion + snapshot) ─────────────┐  │
│  │  C.5b deterministic template (renders top3 server-side if    │  │
│  │      LLM bails or 429s)                                       │  │
│  │  Programmatic-completion fallback (re-runs pipeline w/ profile│  │
│  │      from intake if LLM didn't fire tools)                    │  │
│  │  S3 top3 snapshot → shared_state.TOP3_BY_SESSION              │  │
│  │  F.2 mojibake sanitization on outbound product fields         │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────┬──────────────────────┬──────────────────┬────────────────┘
          │                      │                  │
┌─────────▼──────┐  ┌────────────▼────────┐  ┌──────▼──────────────┐
│ search_        │  │ compliance_check    │  │ rank_products       │
│ products       │  │ (Cloud Function)    │  │ (Cloud Function)    │
│ (httpx REST,   │  │ Pure Python rules,  │  │ Suitability scoring │
│  timeout=8.0s) │  │ 100% deterministic  │  │                     │
│                │  │                     │  │                     │
│ elastic-mcp-   │  │                     │  │                     │
│ server REST    │  │                     │  │                     │
│ /search_       │  │                     │  │                     │
│ products       │  │                     │  │                     │
└─────────┬──────┘  └─────────────────────┘  └─────────────────────┘
          │
┌─────────▼─────────────────────────────────────────────────────────────┐
│  LAYER 3 — SEARCH INTELLIGENCE (Elastic Cloud Serverless)             │
│  ELSER v2 · semantic_text fields · RRF hybrid retriever               │
│  Alias: insurance_products_current (28 products, 7 product_types)     │
└───────────────────────────────────────────────────────────────────────┘
```

---

## Layer 1: Voice Interface

The current InsureVoice deployment uses a **browser-native voice frontend** (Web Speech API), not Dialogflow CX. The original plan included Dialogflow CX; the implemented architecture uses an in-browser STT/TTS pipeline served by the same Cloud Run service via static frontend.

### Components

| Component | Technology | Role |
|---|---|---|
| Speech-to-Text | Web Speech API (`webkitSpeechRecognition`) | Browser-native streaming transcription |
| Conversation rendering | Vanilla JS + ELSER ranking badges | Live transcript panel + product cards |
| Text-to-Speech | Cloud TTS WaveNet (`en-IN-Wavenet-D`) | Indian English voice synthesis |
| Frontend bundling | Same-origin from `agent_builder/frontend/` | Cloud Run serves both API + static assets |

### Voice Latency Targets

| Step | Target Latency | Actual (verified) |
|---|---|---|
| STT transcription | < 1.5s (streaming) | ~0.5s (browser-native) |
| Agent /invoke (intake turn) | < 200ms | ~50ms (deterministic Python) |
| Agent /invoke (follow-up turn) | < 200ms | ~10ms (S3 deterministic, no LLM) |
| Agent /invoke (pipeline turn 9) | < 8s | ~7-10s (search → compliance → rank → recommend) |
| TTS synthesis | < 0.5s | ~0.4s |

**Latency note:** intake turns + follow-up turns are sub-100ms because they bypass the LLM entirely. Only the pipeline-firing turn (after intake completion) hits the full 7-10s budget. This is by design — most user turns are fast.

---

## Layer 2: Agent Orchestration — Three-Phase Pipeline

The `/invoke` handler in `agent_builder/main.py` runs **three phases per turn**: pre-LLM (always), LLM pipeline (only after intake completion + no follow-up intent), post-LLM (only on pipeline turns).

### Phase 1: Pre-LLM (Deterministic Python)

```
def /invoke(message, session_id):
    # Phase 1a — S3 reset detection (BEFORE intake)
    if is_reset_intent(message):
        clear _INTAKE_BY_SESSION[session_id]
        clear shared_state.PROFILE_BY_SESSION[session_id]
        clear shared_state.TOP3_BY_SESSION[session_id]
        return canonical_greeting   # NO LLM call

    # Phase 1b — P.2 intake state machine
    intake_state = _INTAKE_BY_SESSION.setdefault(session_id, {})
    if not intake_state["complete"]:
        intake_result = handle_intake(intake_state, message)
        if intake_result["needs_more_data"]:
            return next_canonical_question   # NO LLM call
        # Intake just completed — mirror profile to shared_state
        shared_state.PROFILE_BY_SESSION[session_id] = profile
        # Build synthetic complete-profile message; fall through to Phase 2

    else:
        # Phase 1c — S3 follow-up dispatch (intake already complete)
        intent = detect_followup_intent(message)
        top3 = shared_state.TOP3_BY_SESSION.get(session_id) or []
        if intent in ("named", "ordinal") and top3:
            matched, method = resolve_product(message, top3, intent)
            if matched:
                return build_voice_text(matched)   # NO LLM call
            return no_match_voice_text()           # NO LLM call
        # else: fall through to Phase 2 (LLM pipeline)
```

**Why deterministic:** Per lesson L-001, prompt rules don't reliably enforce tool-call sequences on flash-lite. Python state machines do. The intake + follow-up dispatch handle the high-frequency turns; the LLM only sees the low-frequency (1 per session) pipeline turn.

### Phase 2: LLM Pipeline (only on pipeline turns)

```python
# agent_builder/agent_definition.py (current — Day 6)
root_agent = LlmAgent(
    model="gemini-2.5-flash-lite",
    name="insure_voice",
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=0.25, top_p=0.7, max_output_tokens=800,
    ),
    before_model_callback=_route_next_tool_callback,    # C.5 — mechanical tool routing
    tools=[
        FunctionTool(search_products),   # NOTE: REST, not MCPToolset (Day 5 switch)
        FunctionTool(compliance_check),
        FunctionTool(rank_products),
        AgentTool(recommend_and_explain_agent),  # sub-agent for voice text
    ],
)
```

**Tools are now FunctionTools, not MCPToolset.** The Day 5 switch traded MCP-native protocol fidelity for simpler integration. The MCP-native server is still deployed as `elastic-mcp-server-native` for hackathon track compliance, but the agent talks to `elastic-mcp-server` REST endpoint.

**Environment variables** required by `insure-voice-agent` Cloud Run:

| Variable | Value |
|---|---|
| `ELASTIC_MCP_SERVER_URL` | `https://elastic-mcp-server-mhojvvbq4a-uc.a.run.app` (REST, hot path) |
| `ELASTIC_MCP_SERVER_NATIVE_URL` | `https://elastic-mcp-server-native-1055350728739.us-central1.run.app` (audit) |
| `COMPLIANCE_CHECK_URL` | `https://us-central1-voice-sales-agent.cloudfunctions.net/compliance_check` |
| `RANK_PRODUCTS_URL` | `https://us-central1-voice-sales-agent.cloudfunctions.net/rank_products` |
| `GOOGLE_GENAI_USE_VERTEXAI` | `TRUE` |
| `GOOGLE_CLOUD_PROJECT` | `voice-sales-agent` |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` |

### Phase 3: Post-LLM (Deterministic Completion + Snapshot)

```
After LlmAgent runner completes:
    # C.5b deterministic template — if LLM bailed without text, render server-side
    if response_text == "" and rank_products.top_3 not empty:
        response_text = build_deterministic_template(rank_products.top_3, profile)

    # Programmatic-completion fallback — if LLM didn't fire tools, run pipeline directly
    if not _has_pipeline_call and intake_complete:
        run_pipeline_programmatically(profile)

    # F.2 mojibake sanitization on outbound product fields
    top3_enriched = [_sanitize_product(p) for p in top3_raw]

    # S3 top3 snapshot for next-turn follow-up dispatch
    if top3_enriched:
        shared_state.TOP3_BY_SESSION[session_id] = [dict(p) for p in top3_enriched]

    return JSONResponse({session_id, response, top3, rejected})
```

---

## State Persistence — Module-Level Dicts (Critical Architecture Decision)

ADK's `InMemorySessionService` does NOT reliably persist mutations to `session.state` across `get_session` calls in this deployment. Documented in `main.py:46-52`. To work around this, the architecture uses **module-level dicts** keyed by `session_id` for cross-turn state:

| Dict | Owner | Lifecycle | Cleared on |
|---|---|---|---|
| `_INTAKE_BY_SESSION` (in `main.py`) | P.2 intake state machine | Until intake complete OR reset | "start over" reset |
| `shared_state.PROFILE_BY_SESSION` | S2' arg injector reads this | Until reset | "start over" reset |
| `shared_state.TOP3_BY_SESSION` | S3 follow-up dispatch reads this | Until reset OR new pipeline run | "start over" reset OR overwrite on new top3 |
| `tool_context.state["last_search_candidates"]` | C.5b candidate substitution | Within a single LLM run | Auto-cleared per-invocation |
| `tool_context.state["last_compliance_passed"]` | C.5b candidate substitution | Within a single LLM run | Auto-cleared per-invocation |

**Why module-level dicts work:** `agent_builder/` is a single Python process per Cloud Run instance. With `--max-instances=1` (current hackathon config), there is exactly one process holding all state. Module-level dicts survive across `/invoke` calls because the process is long-lived.

**Production consideration (post-hackathon):** Module-level dicts won't survive horizontal scaling. For multi-instance deploys, migrate to Firestore-backed session storage. Not in scope for hackathon.

---

## Tool API Contracts

### Tool 1 — `search_products` (REST FunctionTool)

**Wrapper:** `agent_builder/agent_definition.py:55-200` (Python function registered as `FunctionTool`)
**Backend:** `POST $ELASTIC_MCP_SERVER_URL/search_products` on Cloud Run
**Timeout:** 8.0s (Day 6 fix; was 2.5s and timing out)

**Request signature:**
```python
def search_products(
    query: str, customer_age: int, is_smoker: bool, income: int,
    product_type: str = None, size: int = 5, relax_age_filter: bool = False,
    tool_context: ToolContext = None,  # ADK-injected
) -> dict
```

**S2' product_type injection (Day 6):**
Inside the wrapper, BEFORE the HTTP call:
1. Read `session_id = tool_context._invocation_context.session.id`
2. PRIMARY: read `profile = shared_state.PROFILE_BY_SESSION[session_id]`
3. FALLBACK: read `profile = tool_context.state["intake_profile"]` (defense-in-depth)
4. If `profile["coverage_goals"]` is non-empty, override `product_type = coverage_goals[0]`
5. Log `S2_INJECT session=<id> llm_passed=<repr> intake_goal=<repr> -> product_type=<repr>`

**C.5b candidate stash:** After successful response, stash `result["candidates"]` in `tool_context.state["last_search_candidates"]` for compliance_check to read.

**Response shape:**
```json
{
  "candidates": [{"product_id", "name", "product_type", "elser_score",
                  "description", "key_feature", "min_age", "max_age",
                  "smoker_eligible", "min_income", "premium_min_monthly"}],
  "total_hits": int,
  "fallback_triggered": bool
}
```

### Tool 2 — `compliance_check` (REST FunctionTool with C.5b substitution)

**Wrapper:** `agent_builder/agent_definition.py:193-260`
**Backend:** `POST $COMPLIANCE_CHECK_URL` on Cloud Function

**C.5b critical pattern (Day 5):**
```python
def compliance_check(candidates, customer_profile, tool_context):
    # IGNORE the LLM-passed `candidates` arg. flash-lite reliably passes
    # [null, null, null, null] (counts items but loses content).
    real_candidates = tool_context.state.get("last_search_candidates") or []
    payload = {"candidate_products": real_candidates,
               "customer_profile": customer_profile}
    # ... HTTP POST with real_candidates, not candidates ...
```

**Backend rules** (`functions/compliance_check/main.py`):

| Rule ID | Predicate |
|---|---|
| `AGE_MIN` | `customer.age >= product.min_age` |
| `AGE_MAX` | `customer.age <= product.max_age` |
| `SMOKER_EXCLUSION` | `not (smoker and not smoker_eligible)` |
| `INCOME_SUM_CAP` | `sum_need ≤ income × 10` |
| `MEDICAL_EXAM_REQUIRED` | `not (sum_need > medical_required_above and health_status != "healthy")` |

**Response asymmetry:** `passed[]` = full product dicts; `rejected[]` = `{product_id, product_name, reasons}` only.

### Tool 3 — `rank_products` (REST FunctionTool with C.5b substitution)

Same pattern as compliance_check. Reads `tool_context.state["last_compliance_passed"]` instead of LLM-passed `passed_products`.

**Scoring formula:**
```
suitability_score = (elser_score × 0.4) + (age_centrality × 0.3) + (income_fit × 0.3)
age_centrality = 1 - |age - product_midpoint_age| / (max_age - min_age)
income_fit     = min(income / (sum_need / 10), 1.0)
```

### Sub-Agent — `recommend_and_explain` (AgentTool, Gemini 2.5 Flash)

**Purpose:** Generate voice-friendly recommendation text (≤120 words) from top3 + customer profile.
**Prompt:** `agent_builder/sub_agent3_explainer_prompt.md` (105 lines, includes Day 6 S4 catalog facts + smoker logic guardrails)
**Configuration:** `temperature=0.3, max_output_tokens=400` (C.3 stability fix)
**Bypassed by:** C.5b deterministic template when sub-agent fails or returns empty

---

## C.5 Tool Routing Callback (Mechanical Tool-Call Enforcement)

`agent_builder/agent_definition.py:300-440` registers `_route_next_tool_callback` on the root LlmAgent. Before each LLM call, this callback inspects the recent tool-call history and **forces the next tool selection** via `tool_config={mode:ANY, allowed_function_names:[...]}`.

```python
def _route_next_tool_callback(callback_context, llm_request):
    last_fr = get_last_function_response(callback_context)

    # Pipeline state machine
    if last_fr is None:
        # No prior tool call — allow search_products only
        forced = ["search_products"]
    elif last_fr.name == "search_products":
        # search done — force compliance_check next
        forced = ["compliance_check"]
    elif last_fr.name == "compliance_check":
        # compliance done — force rank_products next
        forced = ["rank_products"]
    elif last_fr.name == "rank_products":
        # rank done — force recommend_and_explain next (or final response)
        forced = ["recommend_and_explain"]
    # ... etc.

    llm_request.tool_config = ToolConfig(
        function_calling_config=FunctionCallingConfig(mode=ANY, allowed_function_names=forced)
    )
```

**Why mechanical:** flash-lite ignored prompt rules ("MUST call X next") at ~94% rate. Mechanical routing via `tool_config.mode=ANY` is 100% reliable. AC-3 went from 0/15 PASS to 10/10 PASS once this landed.

---

## Layer 3: Search Intelligence

### Elasticsearch Index Schema (`ingest/create_index.py`)

**Infrastructure:** Elastic Cloud Serverless — built-in EIS. No manual inference endpoint needed.
**Index:** `insurance_products_v1` → **Alias:** `insurance_products_current`
**Catalog size:** 28 products across 7 `product_type` values (`term_life`, `health`, `critical_illness`, `endowment`, `ulip`, `child_plan`, `pension`).

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

### RRF Hybrid Query

Two retrieval legs, fused via Reciprocal Rank Fusion:

```
Leg A (semantic):   semantic on description + semantic on key_feature  (ELSER v2)
Leg B (BM25):       multi_match on name^2, tags, sales_pitch
Hard filters:       is_active, min_income ≤ income, age bounds, smoker_eligible, product_type (when passed)
RRF params:         rank_window_size=20, rank_constant=60
```

**Why ELSER:** "comprehensive illness protection for my family" does NOT keyword-match "Critical Illness Rider". ELSER sparse vectors encode the semantic association. BM25 returns zero; ELSER returns the correct product. **The hard filter on `product_type` is what S2' protects** — server-side filtering only fires if the wrapper passes `product_type` in the request. S2' guarantees that.

### ELSER Inference Endpoint

```json
PUT _inference/sparse_embedding/elser-v2-endpoint
{
  "service": "elasticsearch",
  "service_settings": {
    "adaptive_allocations": { "enabled": true,
                              "min_number_of_allocations": 1,
                              "max_number_of_allocations": 4 },
    "num_threads": 1,
    "model_id": ".elser_model_2"
  }
}
```

### Two MCP Server Deployments — Why Both Exist

| Service | Status | Why kept |
|---|---|---|
| `elastic-mcp-server` (REST + broken MCP) | **Hot path** | Stable REST endpoint that the agent calls via `httpx.post`. Used by `FunctionTool(search_products)` since the Day 5 switch. |
| `elastic-mcp-server-native` (FastMCP/Starlette) | Audit/demo | Deployed for hackathon Elastic Partner Track requirement (proves real MCP integration). Not on the hot path. |

**The original FastMCP double-nesting bug** (route `/mcp/mcp` instead of `/mcp` when mounting FastMCP under FastAPI) was solved by `elastic-mcp-server-native` using FastMCP as the root ASGI app. That fix is preserved in the codebase and the service is still deployed.

---

## Data Flow — Production Demo Arc (verified 2026-06-03)

The canonical demo arc is 12 turns. Each turn's behavior is now deterministic except turn 9 (the pipeline turn) and any compare-intent follow-up.

```
Turn 1-8 (intake collection, deterministic Python):
    User → /invoke {"message": "<turn>", "session_id": <persisted>}
    └─→ S3 reset detection: no match → continue
    └─→ P.2 intake state machine: validates field, saves to _INTAKE_BY_SESSION
    └─→ Returns next canonical question (NO LLM call, ~10ms)

Turn 9 (pipeline turn — intake just completed, "1 crore"):
    └─→ P.2 intake completes → mirror profile to PROFILE_BY_SESSION
    └─→ Build synthetic complete-profile message
    └─→ LLM dispatch (Vertex AI Gemini 2.5 Flash Lite):
        ├─→ tool_call: search_products(query, age, smoker, income, [LLM may omit product_type])
        │   ├─→ S2' wrapper: inject product_type from PROFILE_BY_SESSION
        │   ├─→ httpx.post timeout=8.0s → elastic-mcp-server /search_products
        │   ├─→ ELSER RRF returns N candidates with hard product_type filter
        │   └─→ stash candidates in tool_context.state["last_search_candidates"]
        ├─→ tool_call: compliance_check(candidates=[null,...], profile)
        │   ├─→ C.5b wrapper: ignore LLM args, read real candidates from session state
        │   ├─→ POST to compliance_check CF
        │   └─→ stash passed in tool_context.state["last_compliance_passed"]
        ├─→ tool_call: rank_products(...)
        │   └─→ Same C.5b pattern, returns top3
        └─→ tool_call: recommend_and_explain(top3, profile)
            └─→ Sub-agent generates voice text (Gemini 2.5 Flash, temp=0.3)
    └─→ Post-LLM:
        ├─→ C.5b deterministic template (only fires if LLM bailed)
        ├─→ F.2 mojibake sanitization on top3 product fields
        └─→ S3 snapshot: TOP3_BY_SESSION[session_id] = [dict(p) for p in top3_enriched]
    └─→ Returns JSON {session_id, response, top3, rejected}
    Total wall-clock: ~7-10s

Turn 10 (named follow-up "tell me about LifeGuard Plus"):
    └─→ S3 follow-up dispatch: detect_followup_intent → "named"
    └─→ match_product_by_name(message, TOP3_BY_SESSION[session_id]) → product
    └─→ build_voice_text(product) → "Here's more on LifeGuard Plus Term..."
    └─→ Returns JSON {session_id, response} (NO LLM call, ~10ms)

Turn 11 (ordinal follow-up "second one"):
    └─→ S3 follow-up dispatch: detect_followup_intent → "ordinal"
    └─→ resolve_ordinal_index(message) → 1 → top3[1]
    └─→ build_voice_text → "Here's more on <top3[1].name>..."
    └─→ Returns JSON (NO LLM call, ~10ms)

Turn 12 (reset "start over"):
    └─→ S3 reset detection (Phase 1a): is_reset_intent → True
    └─→ Clear _INTAKE_BY_SESSION, PROFILE_BY_SESSION, TOP3_BY_SESSION
    └─→ Return reset_voice_text() → "No problem — let's start fresh. May I have your name please?"
    └─→ Returns JSON (NO LLM call, ~10ms)
```

**Verified end-to-end (S5 v2 live test, 2026-06-03 17:40 IST):** 12-turn arc against real Vertex AI Gemini + real Cloud Functions + real ELSER. All log signals fire correctly. Turn 9 returns 3 products. Turns 10, 11, 12 are sub-100ms with zero LLM calls.

---

## Logging Surface (Cloud Run grep targets)

All log lines are INFO/WARNING/ERROR on Python's root logger, captured by Cloud Logging.

| Log key | Meaning | Where emitted |
|---|---|---|
| `INTAKE_COMPLETE session=<id> synthetic=<msg>` | P.2 intake just completed | `main.py` after `intake_state["complete"] = True` |
| `S2_INJECT session=<id> llm_passed=<repr> intake_goal=<repr> -> product_type=<repr>` | S2' arg injection fired | `agent_definition.py` `search_products` wrapper |
| `S2_INJECT_MULTIGOAL session=<id> goals=<list> picked=<value>` | User had multiple coverage_goals; S2' picked first | Same wrapper |
| `S2_INJECT_SESSION_ID_MISS` | Couldn't get session_id from tool_context | Same wrapper |
| `S2_PROFILE_MIRROR_FAILED session=<id>` | Mirror to PROFILE_BY_SESSION raised | `main.py` intake-completion block |
| `SEARCH_PAYLOAD query=<...> product_type=<value>` | What was actually sent to ES | `agent_definition.py` search_products wrapper |
| `S3_RESET session=<id> pattern=<msg>` | Reset detected; all state cleared | `main.py` Insertion A |
| `S3_FOLLOWUP_HIT session=<id> intent=<named\|ordinal> method=<substring\|fuzzy\|ordinal> product=<name> index=<i>` | Follow-up dispatched deterministically | `main.py` Insertion B |
| `S3_VOICE session=<id> len=<N>` | Deterministic voice text emitted | Same |
| `S3_FOLLOWUP_MISS session=<id> reason=<no_product_match\|compare_parked\|no_top3_in_state>` | Graceful fall-through | Same |
| `S3_TOP3_SNAPSHOT session=<id> n=<count>` | Top3 captured for next turn | `main.py` Insertion C (post-pipeline) |
| `AGENT_EVENT session=<id> final=<bool> parts=<list>` | C.2 LLM event tracing | LlmAgent run loop |
| `CALLBACK_DEBUG last_fr=<tool> n_events=<N> forced=<tool>` | C.5 mechanical routing decision | `_route_next_tool_callback` |

**Demo monitoring during live test:** grep for `S3_FOLLOWUP_HIT`, `S3_VOICE`, `S2_INJECT`, `INTAKE_COMPLETE`, `S3_RESET`. ZERO `AGENT_EVENT` after a follow-up turn proves the LLM bypass is working.

---

## File Layout (`agent_builder/`)

```
agent_builder/
├── main.py                          # FastAPI /invoke + 3-phase pipeline orchestrator
├── agent_definition.py              # LlmAgent + tools + C.5 callback + S2' inject
├── intake.py                        # P.2 8-field state machine + validators (Day 5)
├── shared_state.py                  # NEW Day 6 — PROFILE_BY_SESSION, TOP3_BY_SESSION
├── followup.py                      # NEW Day 6 — S3 intent detector + voice generator
├── root_agent_prompt.md             # Root LlmAgent system prompt
├── sub_agent3_explainer_prompt.md   # recommend_and_explain prompt + S4 guardrails
├── sub_agent1_search_prompt.md      # legacy (retained for reference)
├── tools.yaml                       # Agent Builder tool registration (legacy artifact)
├── requirements.txt                 # ADK 2.1.0, fastapi, httpx, etc.
├── Dockerfile                       # Cloud Run container build
└── frontend/                        # Static voice UI (HTML/JS/CSS, served by Cloud Run)
```

---

## Phase 2: Workflow Integration (Post-Hackathon)

When a customer accepts a recommendation:

```
accept_recommendation event
    │
    ▼ Pub/Sub: topic = insurance.recommendation.accepted
    Message: { session_id, customer_profile, recommended_product_id,
               timestamp, agent_id, recommendation_score }
    │
    ▼ Workflow Engine (Camunda BPMN / Cloud Workflows)
    Process: Begin Insurance Application Workflow
      ├── KYC verification
      ├── Document collection
      ├── Underwriting trigger
      └── Proposal generation
```

---

## Production Hardening Roadmap (Post-Hackathon)

| Concern | Current state | Production-grade fix |
|---|---|---|
| Module-level dict state | Works at `--max-instances=1` | Migrate to Firestore-backed sessions for horizontal scaling |
| Cloud Function auth | Public (allow-unauthenticated) | Require IAM auth on `compliance_check` and `rank_products` |
| Voice data privacy | STT in browser; nothing persisted | Confirm TTS isn't logging audio; add explicit GDPR opt-in |
| Elasticsearch access | API key (read+write) | Scope a read-only API key for the agent; rotate quarterly |
| Open-source IP exposure | Synthetic catalog only | Verify no real product pricing or customer data before any release |
| Catalog size | 28 products, 7 types | Day 7 backlog: expand to ~48 products with disease-specific descriptions |
| ELSER cost | Auto-scaling EIS | Monitor cost per query; add cache layer for hot queries |
| Demo flow robustness | Validated 12-turn arc | Add multi-product compare-intent (S3 currently parks compare to LLM) |

---

## References

- **Stability changelog (canonical record):** `STABILITY_CHANGELOG.md` (root of stable_v2)
- **Day 6 task folder:** `tasks/2026-06-03_hackathon_day6_atul_followup/`
  - SPEC files in `reports/` (S2_ArgInjector, S3_C2_FollowUp, S4_PromptEdits)
  - Validation gates in `scripts/` (s2/s3/s5 validation arcs)
  - Verbatim transcripts and logs in `data/`
- **Demo script:** `docs/DEMO-SCRIPT.md`
- **Hackathon plan (historical):** `docs/HACKATHON-PLAN.md`
- **CEO pitch (historical):** `docs/CEO-PITCH-AND-BUDGET.md`

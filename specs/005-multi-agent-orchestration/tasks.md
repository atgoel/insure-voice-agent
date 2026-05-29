# Tasks: Multi-Agent Orchestration

**Spec**: specs/005-multi-agent-orchestration/spec.md | **Plan**: specs/005-multi-agent-orchestration/plan.md
**Feature status**: Core pipeline implemented ✅ — remaining tasks are P2 stories, audit hardening, and production readiness.

---

## Phase 1 — Infrastructure & Agent Scaffold

**Goal**: ADK LlmAgent deployable to Cloud Run with all tool wiring in place.

- [x] TASK-001 · [infra] · Create `agent_builder/Dockerfile` — Python 3.11 slim, copies `agent_definition.py` + `main.py` + prompts — `agent_builder/Dockerfile`
- [x] TASK-002 · [infra] · Create `agent_builder/requirements.txt` — `google-adk>=0.4.0`, `mcp>=1.0.0`, `httpx>=0.27.0`, `fastapi>=0.115.0`, `uvicorn>=0.30.0` — `agent_builder/requirements.txt`
- [x] TASK-003 · [setup] · Add Cloud Run deploy step for `insure-voice-agent` to `infra/cloudbuild.yaml`, injecting `ELASTIC_MCP_SERVER_URL`, `ELASTIC_MCP_SERVER_NATIVE_URL`, `COMPLIANCE_CHECK_URL`, `RANK_PRODUCTS_URL`, `GOOGLE_GENAI_USE_VERTEXAI=TRUE` — `infra/cloudbuild.yaml`
- [x] TASK-004 · [setup] · Add build + push + deploy steps for `elastic-mcp-server-native` (FastMCP, Starlette) to `infra/cloudbuild.yaml`; capture URL into `/workspace/mcp_native_url.txt` — `infra/cloudbuild.yaml`
- [x] TASK-005 · [setup] · Verify Cloud Build SA IAM: `roles/run.admin`, `roles/cloudfunctions.developer`, `roles/iam.serviceAccountUser`, `roles/secretmanager.secretAccessor`, `roles/aiplatform.user`
- [x] TASK-006 · [infra] · Create `functions/elastic_mcp_server_native/` with `main.py` (FastMCP + Starlette root mount), `Dockerfile`, `requirements.txt` — `functions/elastic_mcp_server_native/`

---

## Phase 2 — Core Tool Wiring (P1: Sequential Orchestration)

**Goal**: `search_products → compliance_check → rank_products → recommend_and_explain` execute in strict order via Gemini function-calling.
**Independent Test**: `POST /invoke` with a complete customer profile returns a ≤120-word recommendation containing product names.

- [x] TASK-010 · [feat] · Define `root_agent` as `LlmAgent(model="gemini-2.5-flash-lite")` with four tools registered: `MCPToolset`, `FunctionTool(compliance_check)`, `FunctionTool(rank_products)`, `AgentTool(recommend_and_explain)` — `agent_builder/agent_definition.py`
- [x] TASK-011 · [feat] · Implement `MCPToolset(StreamableHTTPConnectionParams(url=f"{ELASTIC_MCP_SERVER_NATIVE_URL}/mcp"))` as Tool 1; do NOT also register `FunctionTool(search_products)` (duplicate name → Gemini 400) — `agent_builder/agent_definition.py`
- [x] TASK-012 · [feat] · Implement `compliance_check()` wrapper function; translate `candidates → candidate_products`, `customer_profile` pass-through; `httpx.post(timeout=5.0)` — `agent_builder/agent_definition.py`
- [x] TASK-013 · [feat] · Implement `rank_products()` wrapper function; translate `eligible_candidates → passed_products`; `httpx.post(timeout=5.0)` — `agent_builder/agent_definition.py`
- [x] TASK-014 · [feat] · Define inner `LlmAgent(model="gemini-2.0-flash", name="recommend_and_explain")` wrapped in `AgentTool`; reads `sub_agent3_explainer_prompt.md` — `agent_builder/agent_definition.py`
- [x] TASK-015 · [feat] · Write `root_agent_prompt.md`: 5-step process (EXTRACT → SEARCH → VALIDATE → RANK AND EXPLAIN → RESPOND), guardrails, all-rejected voice script, tone guidance — `agent_builder/root_agent_prompt.md`
- [x] TASK-016 · [feat] · Write `sub_agent3_explainer_prompt.md`: voice-ready prose ≤120 words, INR, WaveNet-safe, no markdown — `agent_builder/sub_agent3_explainer_prompt.md`
- [x] TASK-017 · [feat] · Implement FastAPI runner in `main.py`: `GET /health`, `POST /invoke`; `InMemorySessionService`; `Runner`; stream `run_async` events; return `{"session_id": ..., "response": ...}` — `agent_builder/main.py`
- [x] TASK-018 · [feat] · Add `session_id` propagation in `/invoke`: generate `uuid4()` if absent; create session if not found; pass back to caller for multi-turn — `agent_builder/main.py`
- [x] TASK-019 · [test] · Add `tests/test_voice_explanation.py`: word-count ≤120, product names present, no markdown, follow-up ≤80 words — fixture-based, no live Gemini call — `tests/test_voice_explanation.py`
- [x] TASK-020 · [test] · Extend `tests/smoke_test_live.py` to chain compliance_check + rank_products end-to-end; assert `top_products` non-empty, suitability scores > 0 — `tests/smoke_test_live.py`

---

## Phase 3 — Compliance Guardrail Enforcement (P1: Story 2)

**Goal**: Root agent never surfaces a rejected product; all-rejected path produces constraint explanation.
**Independent Test**: Invoke with a profile where all products are age-rejected; response must NOT contain any product name, must contain rejection reason text.

- [x] TASK-030 · [feat] · Root agent prompt guardrail: "NEVER recommend a rejected product under any circumstances"; all-rejected voice script template in prompt — `agent_builder/root_agent_prompt.md`
- [x] TASK-031 · [feat] · Wrapper `compliance_check()` maps `passed[]` back to agent correctly; `rank_products()` receives only `passed[]` products — `agent_builder/agent_definition.py`
- [x] TASK-032 · [test] · Write `tests/test_orchestration_guardrail.py`: mock `compliance_check` to return `passed=[]`; assert `/invoke` response contains no product names and mentions a constraint — `tests/test_orchestration_guardrail.py`
- [x] TASK-033 · [test] · Write `tests/test_orchestration_guardrail.py`: mock `compliance_check` to reject 3/5 products; assert `rank_products` mock receives only 2 products in `passed_products` — `tests/test_orchestration_guardrail.py`
- [x] TASK-034 · [test] · Live smoke scenario: send profile age=70 (exceeds most max_age limits); verify response explains age constraint and does not recommend a product — `tests/smoke_test_live.py`

---

## Phase 4 — Error Handling & Graceful Degradation (P2: Story 3)

**Goal**: Any single tool failure is handled without crashing the pipeline; customer receives a meaningful message.
**Independent Test**: Mock `search_products` to raise `httpx.HTTPStatusError`; assert `/invoke` returns 200 with a helpful retry message (not 500).

- [x] TASK-040 · [feat] · Add `try/except httpx.HTTPStatusError` in `search_products()` wrapper; on error return `{"candidates": [], "error": "<message>"}` and let root agent handle gracefully — `agent_builder/agent_definition.py`
- [x] TASK-041 · [feat] · Add `try/except httpx.HTTPStatusError` in `compliance_check()` wrapper; on 5xx return `{"passed": [], "rejected": [], "error": "<message>"}` — do NOT proceed to ranking — `agent_builder/agent_definition.py`
- [x] TASK-042 · [feat] · Add `try/except httpx.TimeoutException` in `rank_products()` wrapper; on timeout fall back to returning `passed_products` ordered by `elser_score` with a warning flag — `agent_builder/agent_definition.py`
- [x] TASK-043 · [feat] · Add top-level `try/except Exception` in `/invoke` handler; return `JSONResponse(status_code=500, content={"error": "...", "session_id": session_id})` — `agent_builder/main.py`
- [x] TASK-044 · [test] · Write `tests/test_invoke_error_handling.py`: mock `httpx.post` for each wrapper to raise; assert `/invoke` returns 200 (or 500 with message) per scenario — `tests/test_invoke_error_handling.py`

---

## Phase 5 — Multi-Turn Conversation State (P2: Story 4)

**Goal**: `session_id` enables follow-up questions without re-running the pipeline; "different budget" clears prior state.
**Independent Test**: Send two `/invoke` requests with same `session_id`; second message "tell me more about the second one" gets contextual answer without triggering `search_products` again.

- [x] TASK-050 · [feat] · `/invoke` generates `uuid4()` session_id if absent; returns it in every response — `agent_builder/main.py`
- [x] TASK-051 · [feat] · `InMemorySessionService` retains tool-call history and LlmAgent state within a session across `/invoke` calls — `agent_builder/main.py`
- [x] TASK-052 · [feat] · Add follow-up detection to root agent prompt: if message contains "more about" / "tell me more" / ordinal reference ("first one", "second one") without new profile data → retrieve from session context, do not re-run pipeline — `agent_builder/root_agent_prompt.md`
- [x] TASK-053 · [feat] · Add budget-reset detection to root agent prompt: if message contains "different budget" / "adjust my profile" / "start over" → instruct agent to clear prior recommendation state and re-run from SEARCH step — `agent_builder/root_agent_prompt.md`
- [x] TASK-054 · [test] · Write `tests/test_multi_turn.py`: use `httpx.AsyncClient` against live FastAPI app (no Cloud Run); first turn returns recommendations; second turn "tell me more about the first one" does not re-call `search_products` mock — `tests/test_multi_turn.py`
- [x] TASK-055 · [test] · Write `tests/test_multi_turn.py`: send a message outside insurance scope (e.g. "what's the weather today?"); assert response contains a polite redirect phrase and no product recommendation — covers Spec Story 4 AC 3 — `tests/test_multi_turn.py`

---

## Phase 6 — Audit Trail & Observability (Constitution §IV)

**Goal**: Every recommendation is traceable: candidate IDs + ELSER scores, compliance outcomes, final rankings — logged to Cloud Logging with `session_id` correlation.

- [x] TASK-060 · [feat] · Add structured audit log writer in `main.py`'s `/invoke` handler: capture Runner event stream for `function_response` events; extract `search_products` candidates, `compliance_check` passed/rejected, `rank_products` top_3 — `agent_builder/main.py`
- [x] TASK-061 · [feat] · Write audit log entry via `google.cloud.logging` client: `severity=INFO`, structured JSON payload `{session_id, candidate_products, compliance_outcomes, final_rankings}` — NO PII (no name, no contact info per Constitution §V) — `agent_builder/main.py`
- [x] TASK-062 · [infra] · Add `google-cloud-logging` to `agent_builder/requirements.txt` — `agent_builder/requirements.txt`
- [x] TASK-063 · [test] · Write `tests/test_audit_log.py`: mock `google.cloud.logging` client; verify audit entry contains `session_id`, `candidate_products` list, `compliance_outcomes`, `final_rankings`; verify no PII fields present — `tests/test_audit_log.py`

---

## Phase 7 — Production Hardening

**Goal**: Service handles Cloud Run cold starts, concurrent sessions, and deployment stability.

- [x] TASK-070 · [infra] · Set `--min-instances=1` on `insure-voice-agent` Cloud Run deploy step to eliminate cold-start latency for demo — `infra/cloudbuild.yaml`
- [x] TASK-071 · [infra] · Set `--min-instances=1` on `elastic-mcp-server-native` Cloud Run deploy step (MCPToolset initialize handshake adds 2–4s on cold start) — `infra/cloudbuild.yaml`
- [x] TASK-072 · [feat] · Add `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION` env var reads in `agent_definition.py`; surface in `/health` response for deployment verification — `agent_builder/agent_definition.py`
- [x] TASK-073 · [docs] · Update `README.md` `## Usage` section: `POST /invoke` example with `curl`, sample response, `session_id` multi-turn example — `README.md`
- [ ] TASK-074 · [infra] · (Phase 2 future) Replace `InMemorySessionService` with Firestore-backed ADK session service to support multi-instance Cloud Run scaling — `agent_builder/main.py`
- [x] TASK-075 · [docs] · **HACKATHON DELIVERABLE** Create `docs/DEMO-SCRIPT.md`: demo video script covering voice intake → ELSER match (Cloud Logging visible) → guardrail rejection → ranked recommendation → voice response — `docs/DEMO-SCRIPT.md`

---

## Phase 8 — Integration & End-to-End Validation

- [x] TASK-080 · [test] · End-to-end smoke test against all deployed URLs: `POST https://insure-voice-agent-1055350728739.us-central1.run.app/invoke` with complete profile; assert response ≤120 words, contains at least one product name, `session_id` present — `tests/smoke_test_live.py`
- [x] TASK-081 · [test] · Latency assertion in smoke test: measure wall-clock time of `/invoke` response; assert < 8s (Constitution §III) — `tests/smoke_test_live.py`
- [x] TASK-082 · [test] · Compliance filter smoke test: profile with `age=72, smoker=True, income=300000`; assert response does not contain a product recommendation, contains constraint explanation — `tests/smoke_test_live.py`

---

## Dependencies

```
Phase 1  (infrastructure)  → required by all phases
Phase 2  (core wiring)     → required by Phase 3, 4, 5, 6
Phase 3  (guardrail tests) → independent of Phase 4, 5 (run in parallel)
Phase 4  (error handling)  → independent of Phase 5, 3
Phase 5  (multi-turn)      → requires Phase 2 TASK-050, TASK-051
Phase 6  (audit log)       → requires Phase 2 TASK-017; Phase 1 TASK-003
Phase 7  (hardening)       → can run in parallel with Phase 3–6
Phase 8  (E2E validation)  → requires all prior phases complete
```

---

## MVP Scope

**Minimum shippable for hackathon demo**: Phase 1 ✅ + Phase 2 ✅ + Phase 3 (TASK-030, TASK-031 ✅; TASK-032–034 recommended).

**Recommended before submission**: Phase 7 TASK-070 + TASK-071 (cold start fix) and Phase 8 TASK-080 + TASK-081 (latency gate smoke test).

---

## Task Summary

| Phase | Total | Done | Remaining |
|---|---|---|---|
| Phase 1 — Infrastructure | 6 | 6 | 0 |
| Phase 2 — Core Tool Wiring | 11 | 11 | 0 |
| Phase 3 — Guardrail Enforcement | 5 | 5 | 0 |
| Phase 4 — Error Handling | 5 | 5 | 0 |
| Phase 5 — Multi-Turn State | 6 | 6 | 0 |
| Phase 6 — Audit Trail | 4 | 4 | 0 |
| Phase 7 — Production Hardening | 6 | 5 | 1 (TASK-074 future) |
| Phase 8 — E2E Validation | 3 | 3 | 0 |
| **Total** | **46** | **45** | **1** |

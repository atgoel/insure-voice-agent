# Tasks: ELSER Semantic Search Integration

**Spec**: specs/002-elser-semantic-search/spec.md | **Plan**: specs/002-elser-semantic-search/plan.md

> Completion state reflects the 2026-05-28 drift analysis. Tasks already implemented in the
> codebase are marked `[x]` with a ✅. Two tasks remain open (`[ ]`).

---

## Phase 1 — Index Schema & Infrastructure

**Goal**: Elasticsearch index exists with correct schema; ELSER semantic fields mapped; versioned alias in place.

- [x] TASK-001 · [infra] · Create versioned index `insurance_products_v1` with 18-field mapping (semantic_text on `description` + `key_feature`, flat premium fields, long for monetary) — `ingest/create_index.py` ✅
- [x] TASK-002 · [infra] · Create alias `insurance_products_current → insurance_products_v1` for zero-downtime re-index — `ingest/create_index.py` ✅
- [x] TASK-003 · [infra] · Add `wait_for_cluster()` readiness gate (30s timeout, 5s poll) before index creation — `ingest/create_index.py` ✅
- [x] TASK-004 · [infra] · Support `--delete-existing` CLI flag (dev-only index teardown) — `ingest/create_index.py` ✅
- [x] TASK-005 · [test] · Index schema unit tests: semantic fields, `name.keyword`, all 20 required properties — `tests/test_create_index.py` ✅

---

## Phase 2 — Synthetic Product Catalog

**Goal**: 28 fully-formed products (4 × 7 types) ingested into Elasticsearch with all schema fields populated.
**Independent Test**: `pytest tests/test_insurance_products_data.py` — no live ES required.

- [x] TASK-010 · [feat] · Generate 28 synthetic products across 7 product types (term_life, health, ulip, endowment, critical_illness, pension, child_plan) — `data/insurance_products.json` ✅
- [x] TASK-011 · [feat] · Every product includes all 22 required fields: `id`, `product_code`, `name`, `product_type`, `plan_category`, `uin`, `description`, `key_feature`, `sales_pitch`, `tags`, `rider_name`, `rider_type`, `min_age`, `max_age`, `smoker_eligible`, `min_income`, `max_sum_assured`, `medical_required_above`, `exclusions`, `premium_min_monthly`, `premium_max_monthly`, `is_active` — `data/insurance_products.json` ✅
- [x] TASK-012 · [feat] · `description` fields are 2–3 sentence natural language (≥ 50 chars) for ELSER signal richness; `key_feature` is a headline phrase — `data/insurance_products.json` ✅
- [x] TASK-013 · [feat] · Age ranges and income floors are deliberately varied across products to exercise compliance engine edge cases — `data/insurance_products.json` ✅
- [x] TASK-014 · [feat] · Bulk ingest via `insurance_products_current` alias — `ingest/index_products.py` ✅
- [x] TASK-015 · [test] · Catalog unit tests: count == 28, all 7 types present, 4 per type, no duplicate IDs, all required fields typed correctly — `tests/test_insurance_products_data.py` ✅

---

## Phase 3 — Hybrid Search Cloud Function (P1 Story 2)

**Goal**: `POST /product_search` returns ranked candidate products using RRF hybrid search (ELSER + BM25) with hard eligibility pre-filters.
**Independent Test**: `pytest tests/test_product_search.py` — mock ES client, no live cluster.

- [x] TASK-020 · [feat] · Cloud Function entry point `product_search(request)` with `@functions_framework.http` decorator; returns `{candidates, total_hits, fallback_triggered}` — **logic now in `functions/elastic_mcp_server/main.py`** (`_execute_search`) ✅
- [x] TASK-021 · [feat] · Input validation for 4 required fields (`query: str`, `customer_age: int`, `is_smoker: bool`, `income: int`) and range check `customer_age 18–75`, `size 1–20`; returns HTTP 400 on failure — **logic now in `functions/elastic_mcp_server/main.py`** (`_validate`) ✅
- [x] TASK-022 · [feat] · `_build_eligibility_filters()`: `is_active=true`, `min_income ≤ income`, optional age bounds (`min_age ≤ age ≤ max_age`), optional `smoker_eligible=true` — **`functions/elastic_mcp_server/main.py`** ✅
- [x] TASK-023 · [feat] · `_build_query()`: Retrievers API + RRF; Leg 1 = semantic(`description`) + semantic(`key_feature`); Leg 2 = `multi_match(name^2, tags, sales_pitch)`; identical filters on both legs; `rank_window_size: 20`, `rank_constant: 60` — **`functions/elastic_mcp_server/main.py`** ✅
- [x] TASK-024 · [feat] · Optional `product_type` filter appended to both retriever legs when provided — **`functions/elastic_mcp_server/main.py`** ✅
- [x] TASK-025 · [feat] · `_hits_to_candidates()`: maps `hit["_source"]` + injects `elser_score = float(hit["_score"])` — **`functions/elastic_mcp_server/main.py`** ✅
- [x] TASK-026 · [feat] · HTTP 500 response on Elasticsearch exception with `{"error": "search_error", "detail": "..."}` — **`functions/elastic_mcp_server/main.py`** ✅
- [x] TASK-027 · [test] · Write `test_product_search.py` with 11 unit tests (mock ES client — no live cluster) — `tests/test_product_search.py` ✅

  Tests to cover:
  - `test_build_query_has_rrf_retriever` — `retriever.rrf` present with two `standard` legs
  - `test_semantic_legs_cover_description_and_key_feature` — Leg 1 `should` contains `semantic` on both fields
  - `test_bm25_leg_fields` — Leg 2 `multi_match.fields == ["name^2", "tags", "sales_pitch"]`
  - `test_age_filters_present_by_default` — `min_age lte` and `max_age gte` in both legs' filters when `relax_age=False`
  - `test_age_filters_absent_when_relaxed` — neither `min_age` nor `max_age` filter present when `relax_age=True`
  - `test_income_filter_always_present` — `min_income lte income` present regardless of `relax_age`
  - `test_smoker_filter_only_for_smokers` — `smoker_eligible=true` filter present iff `is_smoker=True`
  - `test_product_type_filter_added_when_specified` — `term.product_type` appended when arg set
  - `test_validation_rejects_age_out_of_range` — age 17 → 400; age 76 → 400
  - `test_validation_rejects_missing_required_fields` — each of `query`, `customer_age`, `is_smoker`, `income` missing → 400
  - `test_elser_score_injected_from_hit_score` — `elser_score` in candidate equals `hit["_score"]`

---

## Phase 4 — Zero-Results Fallback (P2 Story 3)

**Goal**: When strict search returns 0 results, Root Agent retries with `relax_age_filter=true`; if still empty, voice response explains the constraint.

- [x] TASK-030 · [feat] · `relax_age_filter` param accepted and wired through `_build_eligibility_filters()` — **`functions/elastic_mcp_server/main.py`** ✅
- [x] TASK-031 · [feat] · `fallback_triggered` field in response body reflects the `relax_age_filter` value actually used — **`functions/elastic_mcp_server/main.py`** ✅
- [x] TASK-032 · [docs] · Root Agent prompt documents fallback flow: if `candidates == []` and `fallback_triggered == false`, retry with `relax_age_filter: true`; if still empty, surface "no products match" message — `agent_builder/root_agent_prompt.md` ✅
  > Note: root_agent_prompt.md covers the top-level flow. The Sub-Agent 1 prompt (TASK-040) must carry the explicit retry instruction.

---

## Phase 5 — Agent Builder Integration

**Goal**: Agent Builder can call `product_search` via the registered `elastic_product_search` OpenAPI tool; Sub-Agent 1 delegation is fully specified.

- [x] TASK-040 · [infra] · Register `elastic_product_search` as OpenAPI 3.0 tool in `agent_builder/tools.yaml`; all 4 required params (`query`, `customer_age`, `is_smoker`, `income`) declared — `agent_builder/tools.yaml` ✅
- [x] TASK-041 · [infra] · Response schema in `tools.yaml` includes `candidates[]` with all product fields + `elser_score`, `total_hits`, `fallback_triggered` — `agent_builder/tools.yaml` ✅
- [x] TASK-042 · [feat] · Create `sub_agent1_search_prompt.md`: Sub-Agent 1 system prompt instructing the agent to (1) build natural-language query from `coverage_goals`; (2) call `elastic_product_search` with `{query, customer_age, is_smoker, income}`; (3) on empty result with `fallback_triggered=false` retry with `relax_age_filter: true`; (4) on persistent empty result return `{"candidates": [], "fallback_triggered": true}`; (5) pass `product_type` only when customer explicitly requests a category — `agent_builder/sub_agent1_search_prompt.md` ✅

---

## Phase 6 — Integration & Polish

**Goal**: End-to-end smoke test passes; README updated; Cloud Build step verified.

- [x] TASK-050 · [test] · Smoke test: start Elastic MCP Server locally with `uvicorn functions.elastic_mcp_server.main:app`; POST `$MCP_URL/search_products` with a test profile (`age: 38, income: 800000, is_smoker: false, query: "term life cover for family"`) against a real ES cluster and assert `candidates` non-empty — `tests/smoke_test_live.py` ✅
- [x] TASK-051 · [infra] · Verify Cloud Build builds Docker image for `functions/elastic_mcp_server` and deploys as Cloud Run service `elastic-mcp-server`; service URL logged for Agent Builder console registration — `infra/cloudbuild.yaml` ✅
- [x] TASK-052 · [docs] · Update `README.md` — architecture diagram shows Agent Builder → Elastic MCP Server → Elasticsearch; repo structure shows `elastic_mcp_server/` service; `product_search` CF removed from deploy instructions — `README.md` ✅
- [x] TASK-053 · [feat] · Create Elastic MCP Server Cloud Run service (`functions/elastic_mcp_server/`): `main.py` (FastMCP + FastAPI, `_validate`, `_build_query`, `_execute_search`, `POST /search_products` REST + `POST /mcp` JSON-RPC), `requirements.txt`, `Dockerfile` — **Constitution §VI primary search integration** ✅
- [x] TASK-054 · [feat] · Create MCP-native Cloud Run service (`functions/elastic_mcp_server_native/`): FastMCP 3.3.1 as ASGI root with `mcp.http_app(stateless_http=True)`; `_HealthMiddleware` for `/health`; true `/mcp` endpoint used by ADK `MCPToolset` — `functions/elastic_mcp_server_native/main.py`, `requirements.txt`, `Dockerfile` ✅
- [x] TASK-055 · [feat] · Create ADK agent definition (`agent_builder/agent_definition.py`): `MCPToolset(StreamableHTTPConnectionParams(url=f"{ELASTIC_MCP_SERVER_NATIVE_URL}/mcp"))` replaces `FunctionTool(search_products)`; eliminates duplicate function declaration 400 error from Gemini — `agent_builder/agent_definition.py` ✅

---

## Dependencies

```
Phase 1 → Phase 2 (index must exist before ingesting)
Phase 2 → Phase 3 (catalog needed for query integration tests)
Phase 3 TASK-020–026 → TASK-027 (implementation before tests)
Phase 3 → Phase 4 (relax_age_filter is an extension of core search)
Phase 3 + Phase 4 → Phase 5 (function complete before agent integration)
Phase 5 TASK-040–041 → TASK-042 (tools.yaml registered before sub-agent prompt)
Phase 5 TASK-042 → Phase 6 TASK-050 (sub-agent prompt needed for E2E smoke)
```

---

## MVP Scope

**Minimum shippable for hackathon demo**: Phases 1–5 complete.

| Phase | Status | Blocking? |
|---|---|---|
| Phase 1 — Index schema | ✅ DONE | — |
| Phase 2 — Product catalog | ✅ DONE | — |
| Phase 3 — Search Cloud Function | ✅ DONE | — |
| Phase 4 — Fallback | ✅ DONE | — |
| Phase 5 — Agent Builder integration | ✅ DONE | — |
| Phase 6 — Polish | ✅ DONE | — |

**Feature 002 is complete.** All 31 tasks delivered.

**Open tasks**: 0

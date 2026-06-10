# Implementation Plan: ELSER Semantic Search Integration

**Spec**: specs/002-elser-semantic-search/spec.md | **Date**: 2026-05-28 (re-planned — drift corrected)

> **Drift note**: This plan was re-analysed against actual code on 2026-05-28. All 13 drift items
> have been resolved. Sections marked **✅ DONE** reflect code that already exists in the repo;
> sections marked **⬜ TODO** are what remains to be built.

---

## Summary

Sub-Agent 1 (Product Search) is implemented as the **Elastic MCP Server** (`functions/elastic_mcp_server/main.py`), a Cloud Run service that is the Constitution §VI primary Elastic search integration. It exposes two transports:
- `POST /search_products` — REST endpoint registered in Agent Builder via `tools.yaml`. Agent Builder calls this directly.
- `POST /mcp` — MCP Streamable HTTP (JSON-RPC) for programmatic ADK `MCPToolset` clients.

Both transports execute the same RRF hybrid query against the `insurance_products_current` Elasticsearch alias, combining **two** ELSER v2 semantic legs (`description` + `key_feature`) with a BM25 leg (`name^2`, `tags`, `sales_pitch`) via the Retrievers API. Hard eligibility pre-filters (age, income, smoker) are applied at query time. The `product_search` Cloud Function has been removed — the MCP server owns validation, query building, and Elasticsearch execution directly.

---

## Technical Context

| Field | Value |
|---|---|
| Language | Python 3.11 |
| Key Libraries | `elasticsearch>=8.15`, `fastmcp>=2.0`, `fastapi>=0.115`, `uvicorn>=0.30` |
| GCP Services | Agent Builder (ADK), Cloud Run (MCP server), Cloud Build |
| Elastic | Elastic Cloud Serverless — built-in EIS (no manual inference endpoint); `semantic_text` on `description` and `key_feature`; alias `insurance_products_current` → `insurance_products_v1` |
| Query pattern | Retrievers API + RRF (`rank_window_size: 20`, `rank_constant: 60`) |
| Testing | pytest; no live ES required for unit tests (mock `_es` client) |
| Deployment | Cloud Build: Docker build/push + `gcloud run deploy`; service URL registered in Agent Builder console |

---

## Architecture

```
CustomerProfile (from Root Agent)
        │  {age, income, smoker, coverage_goals, ...}
        ▼
Root Agent  →  calls elastic_product_search tool (tools.yaml)          [REST path]
        │       POST $ELASTIC_MCP_SERVER_URL/search_products
        │       body: {query, customer_age, is_smoker, income, [product_type], [size]}
        │
agent_builder/agent_definition.py  (ADK agent — ✅ EXISTS)                [MCP path]
        │  MCPToolset(StreamableHTTPConnectionParams(url=".../mcp"))
        │  auto-discovers `search_products` via MCP initialize + tools/list
        ▼
functions/elastic_mcp_server_native/main.py  (Cloud Run — ✅ EXISTS)       [MCP-native]
        │  FastMCP 3.3.1 mounted as ASGI root; /mcp is true MCP endpoint
        │  stateless_http=True — Cloud Run request-scoped sessions
        │  _HealthMiddleware intercepts /health before FastMCP routing
        │
functions/elastic_mcp_server/main.py  (Cloud Run service — ✅ EXISTS)      [REST legacy]
        │  └─ Transport 1: POST /search_products (REST, OpenAPI — used by Agent Builder tools.yaml)
        │  └─ Transport 2: POST /mcp (MCP JSON-RPC — double-nesting bug: actual path /mcp/mcp)
        │
        │  _execute_search()  →  _build_query()  →  Retrievers API + RRF
        │  ┌─ Leg 1: standard retriever
        │  │    semantic(description, query_text)  +  semantic(key_feature, query_text)
        │  │    filter: is_active, min_income≤income, min_age≤age, max_age≥age, smoker
        │  └─ Leg 2: standard retriever
        │       multi_match(name^2, tags, sales_pitch, query_text)
        │       filter: (same filters as Leg 1)
        │  RRF fuses both legs → top-10 candidates
        │
        ▼
Elasticsearch Cloud Serverless
   alias: insurance_products_current → index: insurance_products_v1
   semantic_text fields: description, key_feature  (EIS infers on ingest + query)
        │
        ▼
Response: { "candidates": [CandidateProduct + elser_score], "total_hits": N, "fallback_triggered": bool }
        │
   if 0 results → Root Agent retries with relax_age_filter=true
   if still 0   → Root Agent responds: "No products match your current profile"
        │
        ▼
Sub-Agent 2: Compliance Guard  (spec-003)
```

**Latency budget**: < 2s (Constitution §III). Serverless EIS is always warm — no cold-start risk. RRF `rank_window_size: 20` keeps the candidate pool small.

---

## File Structure

```text
data/
  insurance_products.json          ✅ DONE — 28 products, 7 types × 4 each; full schema

ingest/
  create_index.py                  ✅ DONE — versioned index (v1) + alias pattern; Serverless EIS
                                             (no inference_id needed; wait_for_cluster() guard)
  index_products.py                ✅ DONE — bulk ingest via alias; no changes needed

functions/elastic_mcp_server/      ✅ DONE — Cloud Run service (REST path; backward-compat)
  main.py                          ✅ DONE — _validate, _build_query, _execute_search;
                                             POST /search_products (REST for Agent Builder tools.yaml);
                                             POST /mcp has /mcp/mcp double-nesting bug (known)
  requirements.txt                 ✅ DONE — fastmcp, fastapi, elasticsearch, uvicorn
  Dockerfile                       ✅ DONE — Cloud Run container image

functions/elastic_mcp_server_native/ ✅ DONE — Cloud Run service (MCP-native path; used by MCPToolset)
  main.py                          ✅ DONE — FastMCP 3.3.1 as ASGI root; mcp.http_app(stateless_http=True);
                                             _HealthMiddleware for /health; true /mcp endpoint
  requirements.txt                 ✅ DONE — fastmcp>=3.3.1, elasticsearch, uvicorn
  Dockerfile                       ✅ DONE — Cloud Run container image

functions/product_search/
  main.py                          ❌ DEPRECATED — logic moved into elastic_mcp_server

agent_builder/
  agent_definition.py              ✅ DONE — ADK agent; MCPToolset(StreamableHTTPConnectionParams)
                                             pointing at elastic-mcp-server-native /mcp;
                                             no FunctionTool(search_products) to avoid duplicate decl
  tools.yaml                       ✅ DONE — elastic_product_search OpenAPI spec (all 3 tools)
                                             URL: $ELASTIC_MCP_SERVER_URL/search_products
  root_agent_prompt.md             ✅ DONE — references Product Search Agent step
  sub_agent1_search_prompt.md      ✅ DONE — dedicated Sub-Agent 1 system prompt

tests/
  test_create_index.py             ✅ DONE — index schema assertions (semantic fields, name.keyword, etc.)
  test_insurance_products_data.py  ✅ DONE — 28 products, 7 types, required fields, no duplicate IDs
  test_product_search.py           ✅ DONE — 42 tests for elastic_mcp_server._build_query,
                                             _build_eligibility_filters, _hits_to_candidates,
                                             _execute_search; mock _es client
```

---

## Constitution Check

- [x] **Compliance guardrail respected** — `product_search` returns raw candidates only. No scoring, ranking, or recommendations happen here. Sub-Agent 2 (compliance) receives every candidate with its `elser_score` before Sub-Agent 3 ranks.
- [x] **Latency target honoured** — < 2s budget (§III). Serverless EIS eliminates cold-start latency. Hard eligibility pre-filters at query time reduce the result set ELSER must score. `size: 10` caps downstream processing.
- [x] **No hallucination risk** — Query construction in `_build_query()` is fully deterministic; only ELSER's relevance scoring is probabilistic (appropriate for search, not eligibility). Eligibility rules live in `compliance_check`, not here.
- [x] **Audit trail** — Response includes `elser_score` per candidate and `total_hits`. Root Agent logs the full candidate list (product IDs + scores) before passing to Sub-Agent 2 (§IV).
- [x] **No PII storage** — Query parameters contain only anonymised profile fields (age, income, smoker flag). No customer name, contact, or identifying data is sent to Elasticsearch.

---

## Drift Items Resolved (vs. original plan 2026-05-27)

| # | Original Plan | Actual Code | Resolution |
|---|---|---|---|
| D1 | Create `sub_agent1_search_prompt.md` as primary deliverable | `functions/product_search/main.py` is the implementation | Architecture updated; `agent_builder/sub_agent1_search_prompt.md` created ✅ |
| D2 | `bool.should` hybrid query | Retrievers API + RRF (Decision F2) | Query pattern section fully rewritten |
| D3 | Only `description` is `semantic_text` | Both `description` AND `key_feature` are `semantic_text` | Design decision corrected |
| D4 | `elser-v2-endpoint` managed inference, 60s polling loop | Elastic Cloud Serverless — built-in EIS, `wait_for_cluster()` only | Infrastructure section updated |
| D5 | 8-field index schema; nested `premium_range` | 18-field schema; flat `premium_min_monthly`/`premium_max_monthly` | Full schema in spec §Index matches `create_index.py` |
| D6 | MODIFY `tools.yaml` — add elastic search tool | `tools.yaml` complete with all 3 tools | File marked ✅ DONE |
| D7 | 2 required search params (`query`, `customer_age`) | 4 required: add `is_smoker`, `income` | Request schema updated |
| D8 | Fallback: relax income first, then age | Income always enforced; `relax_age_filter=true` relaxes only age | Fallback flow corrected |
| D9 | Return `_score` | Model field is `elser_score` (Python convention) | Field name corrected everywhere |
| D10 | Create `test_hybrid_search.py` | File doesn't exist; `test_create_index.py` and `test_insurance_products_data.py` do | Created as `test_product_search.py` ✅ |
| D11 | GENERATE 25–30 products | Catalog already complete: 28 products, all 7 types | Marked ✅ DONE |
| D12 | "Elastic MCP server" as external service | Elastic MCP Server (Cloud Run) IS the search layer; Agent Builder calls `POST $MCP_URL/search_products` directly (tool: `elastic_product_search`); MCP JSON-RPC also at `POST $MCP_URL/mcp` | Architecture corrected |
| D13 | Open Q: `_score` pass-through via MCP | `elser_score` is injected by `product_search` function, not from MCP middleware | Open question closed |

---

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Semantic field scope | `description` + `key_feature` both `semantic_text` | `description` is rich NL; `key_feature` is a short headline phrase — both reward semantic matching over keyword |
| Query pattern | Retrievers API + RRF | Recommended approach for ES 9.x hybrid search; fuses semantic and keyword recall cleanly without manual score normalisation |
| BM25 fields | `name^2`, `tags`, `sales_pitch` | `name` boosted for exact product matches; `tags` for synonym recall; `sales_pitch` catches marketing language in customer queries |
| Eligibility filters | Applied at query time to BOTH retriever legs | Guarantees no ineligible product can surface, reducing compliance engine workload and preventing accidental compliance pass-through on low-score products |
| Income filter timing | Applied at search time (`min_income ≤ income`) | Never relaxed — income eligibility is non-negotiable per IRDAI guidelines |
| Fallback | `relax_age_filter=true` skips age bounds only | Age relaxation trades stricter eligibility for non-zero results; compliance engine then re-enforces age rules on the wider candidate set |
| Elastic infrastructure | Serverless EIS | No endpoint provisioning, no cold-start delay; zero ops overhead for hackathon timeline |
| Index pattern | Versioned index (`v1`) + alias (`current`) | Zero-downtime re-indexing: create `v2`, re-index, flip alias — no query changes needed |
| `elser_score` field name | `elser_score` (not `_score`) | `_score` is a Python private-name convention; `elser_score` is unambiguous and serialises cleanly via `dataclasses.asdict()` |
| Result size default | `10` | Enough candidates for compliance to filter to 3; fits comfortably in Agent Builder context window |
| `rank_window_size` | `20` | ES 9.x RRF default; each retriever leg evaluates top-20 before fusion, balancing recall and latency |

---

## Sub-Agent 1 System Prompt (`agent_builder/sub_agent1_search_prompt.md`) — ✅ DONE

This file registers Sub-Agent 1's behaviour when Agent Builder delegates the search step. The Root Agent passes a `CustomerProfile` JSON; this sub-agent calls `elastic_product_search` and returns candidates.

**Required content**:
1. Extract `coverage_goals` as a natural language query string.
2. Extract `age` (int), `smoker` (bool), `income` (int) from profile.
3. Call `elastic_product_search` with `{ query, customer_age, is_smoker, income }`.
4. If `candidates` is empty AND `fallback_triggered` is false: retry with `relax_age_filter: true`.
5. If still empty: return `{ "candidates": [], "fallback_triggered": true }`.
6. Otherwise: return the full candidate list with all product fields and `elser_score`.
7. Optionally pass `product_type` if a customer explicitly requests a specific product type (e.g., "only pension plans").

---

## Elasticsearch Index Schema (authoritative — matches `create_index.py`)

```json
{
  "mappings": {
    "properties": {
      "id":                     { "type": "keyword" },
      "product_code":           { "type": "keyword" },
      "name":                   { "type": "text", "fields": { "keyword": { "type": "keyword" } } },
      "product_type":           { "type": "keyword" },
      "plan_category":          { "type": "keyword" },
      "uin":                    { "type": "keyword" },
      "description":            { "type": "semantic_text" },
      "key_feature":            { "type": "semantic_text" },
      "sales_pitch":            { "type": "text" },
      "tags":                   { "type": "keyword" },
      "rider_name":             { "type": "keyword" },
      "rider_type":             { "type": "keyword" },
      "min_age":                { "type": "integer" },
      "max_age":                { "type": "integer" },
      "smoker_eligible":        { "type": "boolean" },
      "min_income":             { "type": "long" },
      "max_sum_assured":        { "type": "long" },
      "medical_required_above": { "type": "long" },
      "exclusions":             { "type": "keyword" },
      "premium_min_monthly":    { "type": "integer" },
      "premium_max_monthly":    { "type": "integer" },
      "is_active":              { "type": "boolean" }
    }
  }
}
```

> **Note**: `premium_range` nested object from the original spec is GONE. Premiums are flat fields
> `premium_min_monthly` / `premium_max_monthly` to align with `InsuranceProduct` dataclass.

---

## RRF Hybrid Query Pattern (authoritative — matches `functions/elastic_mcp_server/main.py`)

```json
{
  "size": 10,
  "_source": true,
  "retriever": {
    "rrf": {
      "rank_window_size": 20,
      "rank_constant": 60,
      "retrievers": [
        {
          "standard": {
            "query": {
              "bool": {
                "should": [
                  { "semantic": { "field": "description", "query": "<coverage_goals_text>" } },
                  { "semantic": { "field": "key_feature",  "query": "<coverage_goals_text>" } }
                ],
                "minimum_should_match": 1
              }
            },
            "filter": [
              { "term":  { "is_active": true } },
              { "range": { "min_income": { "lte": "<customer_income>" } } },
              { "range": { "min_age":    { "lte": "<customer_age>" } } },
              { "range": { "max_age":    { "gte": "<customer_age>" } } },
              { "term":  { "smoker_eligible": true } }
            ]
          }
        },
        {
          "standard": {
            "query": {
              "multi_match": {
                "query":  "<coverage_goals_text>",
                "fields": ["name^2", "tags", "sales_pitch"],
                "type":   "best_fields"
              }
            },
            "filter": "<same filters as Leg 1>"
          }
        }
      ]
    }
  }
}
```

> Smoker filter line is only added when `is_smoker=true`. Age filter lines are omitted when `relax_age_filter=true`.

---

## `test_product_search.py` Scope — ✅ DONE

| Test | What it asserts |
|---|---|
| `test_build_query_structure` | `retriever.rrf` present; two `standard` retriever legs; both have identical filters |
| `test_semantic_legs_use_description_and_key_feature` | Leg 1 `should` contains `semantic` on both `description` and `key_feature` |
| `test_bm25_leg_fields` | Leg 2 `multi_match.fields` = `["name^2", "tags", "sales_pitch"]` |
| `test_age_filter_present_by_default` | `min_age lte` and `max_age gte` appear in filters when `relax_age=False` |
| `test_age_filter_absent_when_relaxed` | No `min_age`/`max_age` filter when `relax_age=True` |
| `test_smoker_filter_only_for_smokers` | `smoker_eligible=true` filter present iff `is_smoker=True` |
| `test_income_filter_always_present` | `min_income lte income` always in filters regardless of `relax_age` |
| `test_product_type_filter_when_specified` | `term.product_type` added to filters when `product_type` arg is set |
| `test_validation_rejects_age_out_of_range` | age < 18 or > 75 → 400 response |
| `test_validation_rejects_missing_required_fields` | Missing `query`, `customer_age`, `is_smoker`, or `income` → 400 |
| `test_elser_score_injected_from_hit_score` | `elser_score` in each candidate comes from `hit["_score"]` |

---

## Open Questions

1. **`PRODUCT_SEARCH_URL` in Agent Builder** — The `tools.yaml` base URL for `elastic_product_search` must be set to the Cloud Run URL of the deployed `product_search` function before Agent Builder can call it. Confirm env var name used in `infra/cloudbuild.yaml` Agent Builder registration step.
2. **`sub_agent1_search_prompt.md` — Agent Builder role** — Does Agent Builder require a separate `.md` prompt file for Sub-Agent 1, or is the tool description in `tools.yaml` sufficient? If ADK delegates via the tool call directly, the `.md` may only be needed for the root agent's delegation instruction.
3. **RRF on Serverless tier** — Confirm Elastic Cloud Serverless supports the Retrievers API + RRF syntax (ES 9.x feature). If not, fall back to `bool.should` hybrid with manual score combination.

---

**Next step**: Run `/speckit-tasks` to generate `specs/002-elser-semantic-search/tasks.md`.

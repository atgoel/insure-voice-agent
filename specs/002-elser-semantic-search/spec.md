# Feature Specification: ELSER Semantic Search Integration

**Feature Directory**: `specs/002-elser-semantic-search/`
**Created**: 2026-05-26
**Updated**: 2026-05-29 (production deployment — MCPToolset with elastic-mcp-server-native)
**Status**: Implemented ✅

## Overview

Sub-Agent 1 (Product Search) is implemented as **two parallel Cloud Run services**:

1. **`elastic-mcp-server`** — original REST+MCP service. REST `/search_products` works. MCP `/mcp` has a double-nesting bug (`/mcp/mcp`). Used for Agent Builder `tools.yaml` REST demo.

2. **`elastic-mcp-server-native`** — new MCP-native service. FastMCP 3.3.1 mounted as the root ASGI app on Starlette, so `/mcp` is the true MCP endpoint. Used by `MCPToolset` in `agent_definition.py`.

The agent (`insure-voice-agent`) uses `MCPToolset(StreamableHTTPConnectionParams(url=".../mcp"))` pointing at `elastic-mcp-server-native`. MCPToolset auto-discovers the `search_products` tool via MCP `initialize` + `tools/list` handshake.

Both services execute the same ELSER v2 RRF hybrid query against the `insurance_products_current` alias.

---

## User Stories & Acceptance Criteria

### Story 1 — Elasticsearch Index Schema & ELSER Ingestion (Priority: P1)

The `insurance_products_v1` index is created on Elastic Cloud Serverless with the correct schema, accessible via the alias `insurance_products_current`. All 28 synthetic products are ingested; ELSER inference on `description` and `key_feature` is applied automatically by the built-in Elastic Inference Service (EIS) — no manual endpoint configuration required.

**Why P1**: Nothing works without a populated index.

**Independent Test**: After running `ingest/create_index.py` and `ingest/index_products.py`, query `GET insurance_products_current/_search` and verify all 28 documents are present with populated `.inference.chunks` on both `description` and `key_feature` fields.

**Acceptance Scenarios**:

1. **Given** an Elastic Cloud Serverless project is accessible (built-in EIS — no manual inference endpoint required), **When** `ingest/create_index.py` runs, **Then** versioned index `insurance_products_v1` is created with alias `insurance_products_current`; `description` and `key_feature` are `type: semantic_text`; monetary fields (`min_income`, `max_sum_assured`, `medical_required_above`) are `long`; premium fields are flat integers (`premium_min_monthly`, `premium_max_monthly`).
2. **Given** the index exists and `data/insurance_products.json` has 28 products, **When** `ingest/index_products.py` runs via the `insurance_products_current` alias, **Then** all 28 products are indexed without errors and `_count` returns 28.
3. **Given** a product is indexed, **When** a semantic query is run against `description` or `key_feature`, **Then** ELSER sparse vector inference is applied automatically by EIS and `_score` reflects semantic relevance.

---

### Story 2 — Hybrid Search via `elastic_product_search` (Priority: P1)

Sub-Agent 1 calls the `elastic_product_search` tool, which is the **Elastic MCP Server** (Cloud Run) registered directly in Agent Builder via `tools.yaml`. It executes an RRF hybrid query: two ELSER semantic legs (`description`, `key_feature`) + one BM25 leg (`name^2`, `tags`, `sales_pitch`), with hard eligibility pre-filters on both legs. Results are returned as a list of candidate products with `elser_score`.

**Why P1**: The hackathon requirement is Elastic ELSER integration — this is the core demo feature.

**Independent Test**: POST to `$ELASTIC_MCP_SERVER_URL/search_products` with a test profile (`age: 35, income: 1200000, is_smoker: false, query: "term life family protection"`) and verify it returns ≥ 1 candidate with `elser_score > 0`.

**Acceptance Scenarios**:

1. **Given** a `CustomerProfile` with `coverage_goals: ["life", "health"]` and `age: 38`, **When** Sub-Agent 1 calls the `elastic_product_search` tool, **Then** the query uses the Retrievers API with RRF: Leg 1 = `semantic` on `description` + `semantic` on `key_feature`; Leg 2 = `multi_match` on `name^2`, `tags`, `sales_pitch`; both legs share identical `bool.filter` clauses for `is_active`, `min_income ≤ income`, age bounds, and (when smoker) `smoker_eligible = true`.
2. **Given** a customer query of "I need family health protection for my 8-year-old and elderly parents", **When** ELSER processes it, **Then** family health and critical illness products score higher than term life products.
3. **Given** the search returns results, **When** passed to Compliance Sub-Agent, **Then** each product object includes `id`, `name`, `product_type`, `min_age`, `max_age`, `smoker_eligible`, `min_income`, `medical_required_above`, `exclusions`, and `elser_score` (the raw RRF `_score` from Elasticsearch, renamed from `_score` to avoid Python private-name convention).

---

### Story 3 — Zero-Results Fallback (Priority: P2)

When no products match the hybrid query (e.g., very restrictive age + smoker filter), Sub-Agent 1 relaxes the query incrementally rather than returning an empty result set.

**Acceptance Scenarios**:

1. **Given** a query with very strict filters that yield 0 results, **When** Sub-Agent 1 gets an empty response (`fallback_triggered: false`), **Then** it retries with `relax_age_filter: true` (age bounds removed); the income floor (`min_income ≤ customer income`) is **always enforced** and never relaxed.
2. **Given** even the relaxed query returns 0 results, **When** reported to the Root Agent, **Then** the root agent informs the customer that no products currently match their profile.
3. **Given** the fallback relaxation succeeds, **When** results are passed to compliance, **Then** the compliance layer may still reject some products — that is expected and correct.

---

## Architecture Decisions (2026-05-29)

### Decision: Two MCP Service Deployments

**Context**: `MCPToolset` requires FastMCP to be mounted at the ASGI root so its internal `/mcp` route is served at the outer `/mcp` path. The original `elastic-mcp-server` wraps FastMCP inside FastAPI (`app.mount("/mcp", mcp.http_app())`), causing the actual MCP endpoint to be at `/mcp/mcp` — a 404 for `MCPToolset`.

**Decision**: Deploy a parallel service `elastic-mcp-server-native` using FastMCP as the ASGI root with a `BaseHTTPMiddleware` for `/health`. The original service is kept for backward compatibility with `tools.yaml`.

**Implementation**:
```python
# elastic_mcp_server_native/main.py
app = mcp.http_app(stateless_http=True)   # FastMCP IS the root
app.add_middleware(_HealthMiddleware)       # /health intercepted before FastMCP routing
```

### Decision: stateless_http=True

Cloud Run terminates connections between requests — no persistent SSE sessions are possible. `stateless_http=True` enables request-scoped sessions. This was moved from `FastMCP()` constructor (removed in fastmcp 3.x) to `mcp.http_app(stateless_http=True)`.

### Decision: MCPToolset replaces FunctionTool for search

The agent previously used `FunctionTool(search_products)` calling the REST endpoint. This was replaced with `MCPToolset(StreamableHTTPConnectionParams(url=f"{ELASTIC_MCP_SERVER_NATIVE_URL}/mcp"))`. MCPToolset auto-discovers the `search_products` tool and calls it via JSON-RPC — demonstrating genuine Elastic MCP integration per hackathon requirements.

**Key lesson**: Do not register both `MCPToolset` (which auto-discovers `search_products`) and `FunctionTool(search_products)` simultaneously. Gemini rejects `400 INVALID_ARGUMENT: Duplicate function declaration found: search_products`.

---

## Elasticsearch Index Schema (authoritative — matches `ingest/create_index.py`)

> **Infrastructure**: Elastic Cloud Serverless — built-in EIS. No `inference_id` needed; no manual endpoint provisioning.
> **Index**: `insurance_products_v1` (versioned). **Alias**: `insurance_products_current` (use in all queries and ingests).

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

## Hybrid Query Pattern (authoritative — matches `functions/elastic_mcp_server/main.py`)

Uses the **Retrievers API with RRF** (Reciprocal Rank Fusion). Hard eligibility filters are applied identically to both retriever legs so no ineligible product can surface, even at low score.

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
              { "range": { "max_age":    { "gte": "<customer_age>" } } }
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
            "filter": "<same 4 filters as Leg 1>"
          }
        }
      ]
    }
  }
}
```

> `smoker_eligible: true` term filter is appended to both legs only when `is_smoker=true`.
> Age bound filters are omitted from both legs when `relax_age_filter=true` (fallback mode).
> Income filter (`min_income ≤ income`) is **always present** — never relaxed.

---

## Edge Cases

- **Elasticsearch unreachable** → the Elastic MCP Server returns HTTP 500 `{"error": "search_error", "detail": "..."}`. Root Agent receives a tool error and reports degraded state to the customer.
- **Elastic Cloud Serverless EIS cold start** → Not applicable; Serverless EIS is always warm. No manual retry logic required.
- **Product `description` or `key_feature` field missing from JSON** → Elasticsearch will index the document without ELSER inference on the missing field; it will appear with a lower relevance score in semantic queries. Ingest script logs error count on completion.
- **`relax_age_filter=true` still returns 0 results** → the Elastic MCP Server returns `{"candidates": [], "fallback_triggered": true}`. Root Agent surfaces: *"No products currently match your profile — please clarify your requirements."*
- **Customer coverage goals map to an unusual product type not in catalog** → ELSER semantic matching surfaces closest semantic neighbours; compliance engine may still reject them, which is correct behaviour.

---

## Out of Scope

- BM25-only fallback mode (ELSER is mandatory per hackathon rules).
- Real-time product catalog sync.
- Multi-language search queries (Phase 1: English only).

---

## Technical Notes

- **Elastic infrastructure**: Elastic Cloud Serverless — built-in EIS. No manual `PUT _inference` endpoint required; no `.elser_model_2` configuration; no Docker MCP container.
- **Index alias**: `insurance_products_current` → `insurance_products_v1`. All queries and ingests use the alias.
- **REST tool endpoint**: `POST $ELASTIC_MCP_SERVER_URL/search_products` — registered in Agent Builder via `tools.yaml` as `elastic_product_search`.
- **MCP JSON-RPC endpoint**: `POST $ELASTIC_MCP_SERVER_URL/mcp` — for programmatic ADK `MCPToolset` clients (`search_products` tool).
- **Ingestion**: `ingest/create_index.py` (schema + alias) + `ingest/index_products.py` (bulk data load via alias).

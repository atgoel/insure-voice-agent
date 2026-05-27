# Feature Specification: ELSER Semantic Search Integration

**Feature Directory**: `specs/002-elser-semantic-search/`
**Created**: 2026-05-26
**Status**: Draft

## Overview

Sub-Agent 1 (Product Search) uses the Elastic MCP server to run a hybrid query against the Elasticsearch `insurance_products` index. The query combines ELSER v2 sparse vector semantic search on the `description` field with structured BM25 keyword matching and eligibility pre-filters. The result is an ordered list of candidate products passed to the Compliance Sub-Agent. This spec also covers index schema design and product ingestion.

---

## User Stories & Acceptance Criteria

### Story 1 — Elasticsearch Index Schema & ELSER Ingestion (Priority: P1)

The `insurance_products` index is created in Elasticsearch Cloud with the correct schema. All 25–30 synthetic products are ingested with ELSER v2 sparse vectors on the `description` field.

**Why P1**: Nothing works without a populated index.

**Independent Test**: After running `ingest/create_index.py` and `ingest/index_products.py`, query the index with `GET insurance_products/_search` and verify all documents are present with populated `.inference.chunks` on the `description` field.

**Acceptance Scenarios**:

1. **Given** Elasticsearch Cloud cluster is running with ELSER v2 inference endpoint, **When** `ingest/create_index.py` runs, **Then** index `insurance_products` is created with `description` as `type: semantic_text` and all constraint fields as `keyword`/`integer`/`float`.
2. **Given** the index exists and `data/insurance_products.json` has 25–30 products, **When** `ingest/index_products.py` runs, **Then** all products are indexed without errors and `_count` returns the expected count.
3. **Given** a product is indexed, **When** a semantic query is run against `description`, **Then** ELSER sparse vector scores are present in the `_score` field of results.

---

### Story 2 — Hybrid Search via Elastic MCP (Priority: P1)

Sub-Agent 1 calls the Elastic MCP server's search tool with a hybrid query: semantic search on `description` + structured filters based on the customer profile (age, smoker status, income pre-filter). Results are returned as a list of candidate products.

**Why P1**: The hackathon requirement is Elastic MCP integration — this is the core demo feature.

**Independent Test**: Invoke the MCP search tool directly with a test customer profile and verify it returns ≥ 1 product whose attributes are compatible with the profile.

**Acceptance Scenarios**:

1. **Given** a `CustomerProfile` with `coverage_goals: ["life", "health"]` and `age: 38`, **When** Sub-Agent 1 calls the Elastic MCP search tool, **Then** the query uses a `bool.should` combining `semantic` on `description` and `match` on product name, with `bool.filter` for age bounds.
2. **Given** a customer query of "I need family health protection for my 8-year-old and elderly parents", **When** ELSER processes it, **Then** family health and critical illness products score higher than term life products.
3. **Given** the search returns results, **When** passed to Compliance Sub-Agent, **Then** each product object includes `id`, `name`, `product_type`, `min_age`, `max_age`, `smoker_eligible`, `min_income`, `medical_required_above`, `exclusions`, and `_score`.

---

### Story 3 — Zero-Results Fallback (Priority: P2)

When no products match the hybrid query (e.g., very restrictive age + smoker filter), Sub-Agent 1 relaxes the query incrementally rather than returning an empty result set.

**Acceptance Scenarios**:

1. **Given** a query with very strict filters that yield 0 results, **When** Sub-Agent 1 gets an empty response, **Then** it retries with filters relaxed (remove income pre-filter first, then age range filter).
2. **Given** even the relaxed query returns 0 results, **When** reported to the Root Agent, **Then** the root agent informs the customer that no products currently match their profile.
3. **Given** the fallback relaxation succeeds, **When** results are passed to compliance, **Then** the compliance layer may still reject some products — that is expected and correct.

---

## Elasticsearch Index Schema

```json
{
  "mappings": {
    "properties": {
      "id": { "type": "keyword" },
      "name": { "type": "text" },
      "product_type": { "type": "keyword" },
      "description": {
        "type": "semantic_text",
        "inference_id": "elser-v2-endpoint"
      },
      "min_age": { "type": "integer" },
      "max_age": { "type": "integer" },
      "smoker_eligible": { "type": "boolean" },
      "min_income": { "type": "integer" },
      "max_sum_assured": { "type": "integer" },
      "medical_required_above": { "type": "integer" },
      "exclusions": { "type": "keyword" },
      "coverage_type": { "type": "keyword" },
      "premium_range": {
        "properties": {
          "min": { "type": "integer" },
          "max": { "type": "integer" }
        }
      }
    }
  }
}
```

## Hybrid Query Pattern

```json
{
  "query": {
    "bool": {
      "should": [
        { "semantic": { "field": "description", "query": "<coverage_goals natural language>" } },
        { "match": { "name": "<coverage_goals keywords>" } }
      ],
      "filter": [
        { "range": { "min_age": { "lte": "<customer_age>" } } },
        { "range": { "max_age": { "gte": "<customer_age>" } } }
      ],
      "minimum_should_match": 1
    }
  },
  "size": 10
}
```

---

## Edge Cases

- ELSER inference endpoint not ready (cold start) → retry up to 3 times with 500ms backoff.
- Product `description` field missing from JSON → skip product with warning log.
- Elastic MCP server timeout → return empty list with error flag; Root Agent reports degraded state.
- Customer coverage goals map to an unusual product type not in current catalog → semantic search should still surface closest matches.

---

## Out of Scope

- BM25-only fallback mode (ELSER is mandatory per hackathon rules).
- Real-time product catalog sync.
- Multi-language search queries (Phase 1: English only).

---

## Technical Notes

- ELSER inference endpoint: `PUT _inference/sparse_embedding/elser-v2-endpoint` (see `docs/HACKATHON-PLAN.md` for config).
- MCP tool: Elastic MCP server's `search` tool; called by Sub-Agent 1 via Agent Builder tool definition in `agent_builder/tools.yaml`.
- Index name: `insurance_products`.
- Ingestion: `ingest/create_index.py` (schema) + `ingest/index_products.py` (data load).
- ELSER model: `.elser_model_2` with adaptive allocations enabled.

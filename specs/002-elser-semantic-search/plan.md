# Implementation Plan: ELSER Semantic Search Integration

**Spec**: specs/002-elser-semantic-search/spec.md | **Date**: 2026-05-27

---

## Summary

Sub-Agent 1 (Product Search) uses the Elastic MCP server to execute a hybrid query against the `insurance_products` Elasticsearch index. The query combines ELSER v2 sparse vector semantic search on the `description` field with BM25 keyword matching and age-based structured filters. The implementation requires: (1) synthetic product catalog generation, (2) ELSER inference endpoint + index schema (already scaffolded), (3) hybrid query construction in Sub-Agent 1's system prompt, and (4) MCP tool registration in `agent_builder/tools.yaml`.

---

## Technical Context

| Field | Value |
|---|---|
| Language | Python 3.11 |
| Key Libraries | `elasticsearch>=8.13`, `elasticsearch[async]`, `google-generativeai`, `functions-framework` |
| GCP Services | Agent Builder (ADK), Cloud Functions 2nd gen, Cloud Build |
| Elastic | ELSER v2 (`.elser_model_2`), `semantic_text` field type, Elastic MCP server `search` tool |
| Index | `insurance_products` on Elasticsearch Cloud |
| Inference endpoint | `elser-v2-endpoint` (sparse_embedding service) |
| Testing | pytest + direct `elasticsearch-py` client assertions |
| Deployment | `ingest/` scripts run locally / Cloud Build step; Sub-Agent 1 config in `agent_builder/` |

---

## Architecture

```
CustomerProfile (from Root Agent)
        │
        ▼
Sub-Agent 1: Product Search
        │  builds hybrid bool query from profile fields
        │
        ▼
Elastic MCP server  ──────────────────────────────────────────────────────┐
   tool: search                                                            │
        │                                                                  │
        ▼                                                             Elasticsearch Cloud
 insurance_products index                                             ELSER v2 inference
   bool.should:                                                       endpoint (sparse vecs
     - semantic(description, coverage_goals_text)                     on description field)
     - match(name, coverage_goals_keywords)
   bool.filter:
     - range(min_age <= customer_age)
     - range(max_age >= customer_age)
   size: 10
        │
        ▼
 List[ProductDocument]  ← includes id, name, product_type, all constraint
        │                  fields, _score (ELSER relevance)
        │
   if 0 results → relaxation loop (drop income filter → drop age filter)
        │
        ▼
Sub-Agent 2: Compliance Guard   (next feature spec)
```

**Latency budget for this component**: < 2s (Constitution §III).  
ELSER cold-start risk is mitigated by adaptive allocations (`min_number_of_allocations: 1`), ensuring at least one allocation is always warm.

---

## File Structure

```text
data/
  insurance_products.json          ← GENERATE: 25–30 synthetic products (currently empty [])

ingest/
  create_index.py                  ← EXISTS: add retry/wait loop for ELSER endpoint readiness
  index_products.py                ← EXISTS: no changes needed

agent_builder/
  sub_agent1_search_prompt.md      ← CREATE: Sub-Agent 1 system prompt with hybrid query spec
  tools.yaml                       ← MODIFY: add Elastic MCP `search` tool definition

tests/
  test_create_index.py             ← CREATE: index schema assertions
  test_hybrid_search.py            ← CREATE: hybrid query structure + fallback logic tests
  test_insurance_products_data.py  ← CREATE: catalog completeness + schema validation
```

---

## Constitution Check

- [x] **Compliance guardrail respected** — Sub-Agent 1 outputs raw candidates only; no recommendations are made here. The compliance gate (Sub-Agent 2) receives the full candidate list before any ranking.
- [x] **Latency target honoured** — Budget is < 2s for this sub-agent. Adaptive allocations prevent ELSER cold start. Query `size: 10` limits result set processing time.
- [x] **No hallucination risk in deterministic logic** — Hybrid query construction is deterministic (field values from CustomerProfile substituted into fixed query template). Only the ELSER semantic scoring is probabilistic, which is appropriate and expected for search relevance.
- [x] **Audit trail considered** — Sub-Agent 1 must return `_score` alongside each candidate product. The Root Agent logs the full candidate list (product IDs + ELSER scores) to Cloud Logging before passing to Sub-Agent 2.

---

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Semantic field scope | Only `description` uses `semantic_text` | `description` carries the richest natural language content. `name` is searched with BM25 `match` to avoid inference overhead on short text. |
| Query type for semantic leg | `semantic` query (not `knn`) | `semantic` query is the recommended approach for `semantic_text` fields in ES 8.x; handles chunking and scoring automatically. |
| Filter fields in Phase 1 | Only `min_age` / `max_age` in `bool.filter` | Income and smoker pre-filters are too restrictive for sparse catalogs; they are delegated to the compliance layer. This maximises candidate recall. |
| `minimum_should_match` | `1` | Ensures at least the semantic leg or BM25 leg must match, preventing full-scan on filter-only queries. |
| Result size | `10` | Returns enough candidates for the compliance layer to filter down to 3 ranked results without overwhelming the agent context window. |
| Fallback relaxation order | 1. Drop income filter → 2. Drop age filter | Income filter is more restrictive than age for Indian insurance products; relax it first to maximise meaningful results. |
| Synthetic data generation | Python script producing 28 products across 7 types | Ensures even distribution across `product_type` (term_life, health, ulip, endowment, critical_illness, pension, child_plan) for demo variety. |
| ELSER endpoint wait | Polling loop in `create_index.py` (up to 60s, 5s intervals) | ELSER model deployment takes 20–40s on first run; without a readiness check, subsequent index creation silently fails. |
| MCP tool in tools.yaml | OpenAPI-style tool spec pointing to Elastic MCP `search` endpoint | Agent Builder requires OpenAPI 3.0 tool definitions; Elastic MCP exposes a REST interface compatible with this pattern. |

---

## Sub-Agent 1 System Prompt Spec (`sub_agent1_search_prompt.md`)

Sub-Agent 1 receives a `CustomerProfile` JSON object from the Root Agent and must:

1. Extract `coverage_goals` as a natural language string (e.g., `"life protection and critical illness cover for non-smoker age 38"`).
2. Extract `age` as an integer.
3. Construct and call the Elastic MCP `search` tool with the hybrid query template.
4. If results ≥ 1: return the product list with all fields plus `_score`.
5. If results = 0: retry with relaxed query (remove age filter). If still 0, return `{"candidates": [], "fallback_triggered": true}`.

The prompt must include the exact query JSON template with `$COVERAGE_GOALS`, `$CUSTOMER_AGE` substitution placeholders so Agent Builder can fill them deterministically.

---

## Elastic MCP Tool Registration (`tools.yaml` addition)

```yaml
/elastic_search:
  post:
    operationId: elastic_product_search
    summary: Hybrid ELSER semantic + BM25 search over insurance_products index
    description: >
      Calls the Elastic MCP server search tool. Combines ELSER v2 sparse vector
      semantic search on the description field with BM25 keyword match on name,
      filtered by customer age bounds. Returns up to 10 candidate products with
      relevance scores.
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [query, customer_age]
            properties:
              query:
                type: string
                description: "Natural language coverage goals from customer profile"
              customer_age:
                type: integer
                description: "Customer age in years for age-bound filtering"
              relax_age_filter:
                type: boolean
                default: false
                description: "If true, removes age range filters (fallback mode)"
    responses:
      "200":
        description: Search results
        content:
          application/json:
            schema:
              type: object
              properties:
                candidates:
                  type: array
                  items: { type: object }
                fallback_triggered:
                  type: boolean
                total_hits:
                  type: integer
```

---

## Synthetic Product Catalog Design (`data/insurance_products.json`)

28 products across 7 types (4 products per type):

| `product_type` | Examples |
|---|---|
| `term_life` | ShieldMax Term, LifeGuard Plus, FamilyProtect 3 Crore, YoungTerm |
| `health` | MediCare Family, CriticalCare Senior, HealthFirst Individual, ParentsCare |
| `ulip` | WealthShield ULIP, FutureBuild ULIP, MarketLinked Growth, ChildFuture ULIP |
| `endowment` | GrowthSure Endowment, MoneyBack Classic, Jeevan Raksha, SavingsPlus |
| `critical_illness` | CancerCare Rider, HeartShield CI, CI Protect 36, SeriousIllness Cover |
| `pension` | RetireSmart Pension, GoldenYears Annuity, PensionMaxx, SecureRetirement |
| `child_plan` | ChildStar Future, SmartKid Plus, ChildEducation Guard, FutureAce Child |

Each product has: `id`, `name`, `product_type`, `description` (2–3 sentences, rich natural language for ELSER), `min_age`, `max_age`, `smoker_eligible`, `min_income`, `max_sum_assured`, `medical_required_above`, `exclusions[]`, `coverage_type`, `premium_min_monthly`, `premium_max_monthly`.

Age ranges and income floors are deliberately varied to exercise the compliance engine across all test scenarios.

---

## Open Questions

1. **Elastic MCP server URL** — Is the Elastic MCP server running as a sidecar on Cloud Run, or as an external hosted endpoint? The `tools.yaml` base URL must be set before Agent Builder can call it. If self-hosted, need to document Cloud Run service URL or use the Elastic-hosted MCP endpoint.
2. **ELSER endpoint region** — Does the Elasticsearch Cloud cluster region match the GCP region for the Agent Builder to minimise cross-region latency? Should be confirmed before deployment.
3. **`_score` pass-through** — Elastic MCP server's default `search` response includes `_score`; confirm it is not stripped by the MCP layer before it reaches the agent.

---

**Next step**: Run `/speckit-tasks` to break this plan into actionable, dependency-ordered tasks.

# Implementation Plan: API Design & Data Model

**Scope**: Cross-cutting — applies to ALL five feature specs | **Date**: 2026-05-27

> **Why this plan first**: All five feature specs share `CustomerProfile`, `InsuranceProduct`, and the inter-function API contracts. Inconsistencies across existing specs and skeleton code will cause integration failures if not resolved before individual features are built. This plan is the single authoritative source of truth for all data shapes and API contracts.

---

## Summary

This plan defines the canonical data models and API contracts for the full InsureVoice pipeline. It surfaces 8 inconsistencies found between the existing skeleton code and the specs, proposes a `shared/models.py` module holding all typed data structures, and establishes the authoritative schemas that all five feature implementations must use.

---

## Technical Context

| Field | Value |
|---|---|
| Language | Python 3.11 |
| Key Libraries | `typing`, `dataclasses`, `pydantic>=2.0` (for validation at function entry points) |
| GCP Services | Cloud Functions (2nd gen) — entry-point validation layer |
| Elastic | `semantic_text` field, flat `premium_min_monthly` / `premium_max_monthly` fields (see §Gap 3) |
| Testing | pytest with fixture-driven model round-trip tests |
| Deployment | `shared/` module co-deployed with each Cloud Function via `requirements.txt` + local path |

---

## Gaps & Inconsistencies Found

The following must be resolved **before** any feature implementation starts:

| # | Location | Issue | Resolution |
|---|---|---|---|
| G1 | `data/insurance_products.json` | File is `[]` — empty array. Every downstream feature depends on this catalog. | Generate 28 synthetic products (see §Catalog Design). **Blocker for everything.** |
| G2 | `rank_products/main.py` | `_score` from ELSER can be `0.0–22.0+` (raw sparse score); formula assumes `[0,1]`. Produces suitability scores > 1.0. | Normalise `_score` across the candidate batch before scoring: `norm = score / max_score_in_batch`. |
| G3 | `create_index.py` vs `spec 004` | Index schema uses flat `premium_min_monthly` / `premium_max_monthly` fields. `spec 004` API contract shows `"premium_range": {"min": ..., "max": ...}`. | Keep flat fields in the Elasticsearch document. The `rank_products` function reads `premium_min_monthly` / `premium_max_monthly`. Remove the nested `premium_range` shape from spec 004. |
| G4 | `compliance_check/main.py` | No input validation. Missing `candidate_products` or `customer_profile` causes an unhandled `KeyError`, returning HTTP 500 instead of HTTP 400. | Add Pydantic validation at function entry point (see §Validation Layer). |
| G5 | `rank_products/main.py` | No input validation. Same crash risk as G4. Also missing the `audit.all_scored` field required by spec 004 Story 3. | Add Pydantic validation + audit trail output. |
| G6 | `tools.yaml` | Defines `compliance_check` and `rank_products` but has **no Elastic MCP tool definition**. Sub-Agent 1 has no registered tool. | Add `elastic_product_search` tool definition (see §tools.yaml Contract). |
| G7 | `rank_products` response shape | Each top-3 item nests the product under `"product"` key: `{"rank": 1, "product": {...}, "suitability_score": 0.78}`. The root agent prompt and spec 002 expect product fields to be directly accessible. | Keep the nested shape but document it explicitly. The root agent must dereference `.product.*` to get fields. |
| G8 | `compliance_check` response shape | The `rejected` list uses `product_id` / `product_name` keys but does NOT include the full product object. The root agent needs the product name to explain rejections. The passed list includes the full object. | Keep as-is; root agent uses `product_name` from the rejected entry for the voice explanation. Document this asymmetry. |

---

## Canonical Data Models

All models below must be defined in `shared/models.py` and imported by both Cloud Functions and the ingestion scripts.

### 1. `CustomerProfile`

```python
from dataclasses import dataclass, field
from typing import Optional, List
from enum import Enum

class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    PRE_EXISTING = "pre_existing"

class CoverageGoal(str, Enum):
    LIFE = "life"
    HEALTH = "health"
    CRITICAL_ILLNESS = "critical_illness"
    ACCIDENT = "accident"
    INVESTMENT = "investment"
    ENDOWMENT = "endowment"

@dataclass
class CustomerProfile:
    # Required
    age: int                          # 18–75
    income: int                       # INR per annum, min 100_000
    smoker: bool
    health_status: HealthStatus
    coverage_goals: List[CoverageGoal]

    # Optional
    sum_need: Optional[int] = None    # INR; None = "maximum available"
    family_size: Optional[int] = None # 1–10
    dependents: Optional[int] = None  # 0–9
    preferred_term_years: Optional[int] = None
```

**Validation bounds** (enforced at Root Agent extraction and at Cloud Function entry points):

| Field | Min | Max | Notes |
|---|---|---|---|
| `age` | 18 | 75 | Outside range → Root Agent asks for clarification |
| `income` | 100_000 | — | INR; must be an integer (convert ₹15L → 1_500_000) |
| `sum_need` | 0 | `income × 10` | Exceeding this triggers `INCOME_SUM_CAP` rule |
| `coverage_goals` | len ≥ 1 | len ≤ 6 | At least one goal required to proceed |

---

### 2. `InsuranceProduct` (Elasticsearch document + JSON catalog schema)

This is the authoritative schema. All 28 products in `data/insurance_products.json` must conform to it. The Elasticsearch index mapping in `create_index.py` must match field names exactly.

```python
@dataclass
class InsuranceProduct:
    # Identity & Codes  (aligns with tblProducts)
    id: str                           # e.g. "TERM001" — internal key in ES (keyword)
    product_code: str                 # e.g. "FG_TERM_001" — aligns with vcFGProductCode
    name: str                         # Display name — aligns with vcProductTitle
    product_type: str                 # e.g. "term_life" — aligns with vcProductType (enum values below)
    plan_category: str                # e.g. "Protection" — aligns with VcPlanCategory

    # Regulatory
    uin: str                          # IRDAI Unique Identification Number — aligns with vcUIN

    # ELSER semantic field (primary search surface)
    description: str                  # 2–3 sentences; rich natural language; semantic_text in ES

    # Marketing fields  (aligns with tblProducts marketing columns)
    key_feature: str                  # Short headline — aligns with vcKeyFeature
    sales_pitch: str                  # 1–2 sentence sales description — aligns with vcSalesPitch
    tags: List[str]                   # Search/filter keywords — aligns with vcTags

    # Rider (optional — not all products carry riders)
    rider_name: Optional[str]         # e.g. "Accidental Death Benefit" — aligns with vcRiderName
    rider_type: Optional[str]         # e.g. "Accidental" — aligns with vcRiderType

    # Eligibility constraints (used by compliance engine)
    min_age: int                      # integer in ES
    max_age: int                      # integer in ES
    smoker_eligible: bool             # boolean in ES
    min_income: int                   # INR; long in ES
    max_sum_assured: int              # INR; long in ES
    medical_required_above: int       # INR sum_assured threshold; long in ES
    exclusions: List[str]             # list of keyword in ES

    # Premium (flat fields — NOT nested)
    premium_min_monthly: int          # INR; integer in ES
    premium_max_monthly: int          # INR; integer in ES

    # Lifecycle
    is_active: bool = True            # product availability — aligns with btIsActive


class ProductType(str, Enum):
    """Granular product type — aligns with vcProductType."""
    TERM_LIFE        = "term_life"
    HEALTH           = "health"
    ULIP             = "ulip"
    ENDOWMENT        = "endowment"
    CRITICAL_ILLNESS = "critical_illness"
    PENSION          = "pension"
    CHILD_PLAN       = "child_plan"


class PlanCategory(str, Enum):
    """Broad product category — aligns with VcPlanCategory."""
    PROTECTION       = "Protection"
    SAVINGS          = "Savings"
    RETIREMENT       = "Retirement"
    ULIP             = "ULIP"
    INVESTMENT       = "Investment"
    HEALTH_INSURANCE = "Health Insurance"
    CHILD            = "Child"
    CRITICAL_ILLNESS = "Critical Illness"
```

**Field-to-enterprise mapping**:

| Our Field | tblProducts Column | Notes |
|---|---|---|
| `id` | `intProductCode` (as str) | Internal key |
| `product_code` | `vcFGProductCode` | e.g. `FG_TERM_001` |
| `name` | `vcProductTitle` | Display name |
| `product_type` | `vcProductType` | Granular type |
| `plan_category` | `VcPlanCategory` | Broad category |
| `uin` | `vcUIN` (from M_PHUB_UINCHANGEPRODUCTS) | IRDAI regulatory ID |
| `description` | derived from `vcKeyFeature` + `vcSalesPitch` | Rich NL for ELSER |
| `key_feature` | `vcKeyFeature` | Short benefit headline |
| `sales_pitch` | `vcSalesPitch` | Sales message |
| `tags` | `vcTags` (split to list) | Search keywords |
| `rider_name` | `vcRiderName` | Optional rider |
| `rider_type` | `vcRiderType` | Optional rider type |
| `is_active` | `btIsActive` | Lifecycle flag |

---

### 3. `CandidateProduct` (search result — product + ELSER score)

This is what Sub-Agent 1 returns after an Elastic MCP search. It is an `InsuranceProduct` augmented with the ELSER relevance score.

```python
@dataclass
class CandidateProduct(InsuranceProduct):
    elser_score: float = 0.5    # Raw ELSER sparse vector score (can be > 1.0; normalised before use)
```

The `elser_score` field **must always be present** in every item returned by the search before passing to compliance. The compliance function passes it through unchanged. The ranking function normalises it.

---

### 4. `ComplianceRequest` / `ComplianceResponse`

```python
# POST /compliance_check

# Request body
@dataclass
class ComplianceRequest:
    candidate_products: List[dict]   # list of CandidateProduct dicts (includes _score)
    customer_profile: dict           # CustomerProfile dict

# Response body (HTTP 200)
@dataclass
class ComplianceResponse:
    passed: List[dict]               # full CandidateProduct dicts (including _score)
    rejected: List[RejectedProduct]

@dataclass
class RejectedProduct:
    product_id: str
    product_name: str
    reasons: List[str]               # one entry per violated rule

# Error response (HTTP 400)
# {"error": "missing_required_fields", "fields": ["age", "income"]}
```

**Required fields validated at entry** (HTTP 400 if absent or wrong type):

| Field | Type | Rule |
|---|---|---|
| `customer_profile.age` | int | 18–75 |
| `customer_profile.income` | int | > 0 |
| `customer_profile.smoker` | bool | — |
| `customer_profile.health_status` | `"healthy"` \| `"pre_existing"` | — |
| `candidate_products` | list | len ≥ 0 (empty list is valid → immediate `{passed:[], rejected:[]}`) |

---

### 5. `RankRequest` / `RankResponse`

```python
# POST /rank_products

# Request body
@dataclass
class RankRequest:
    passed_products: List[dict]      # ComplianceResponse.passed — includes _score
    customer_profile: dict           # CustomerProfile dict (needs age, income, sum_need)

# Response body (HTTP 200)
@dataclass
class RankResponse:
    top3: List[RankedProduct]
    audit: AuditTrail

@dataclass
class RankedProduct:
    rank: int
    product: dict                    # full CandidateProduct dict
    suitability_score: float         # [0, 1] after normalisation
    score_breakdown: ScoreBreakdown

@dataclass
class ScoreBreakdown:
    elser_relevance: float           # normalised _score ∈ [0,1]
    age_centrality: float            # ∈ [0,1]
    income_fit: float                # ∈ [0,1]

@dataclass
class AuditTrail:
    all_scored: List[dict]           # ALL passed products with scores, not just top-3
    formula_weights: dict            # {"elser": 0.4, "age": 0.3, "income": 0.3}
    customer_profile_hash: str       # SHA-256 of sorted JSON (no PII — just for correlation)
```

**Required fields validated at entry** (HTTP 400 if absent):
`passed_products` (list), `customer_profile.age` (int), `customer_profile.income` (int).

---

## Elasticsearch Index Schema (Authoritative)

This supersedes any definition in `create_index.py` or the individual specs. The `create_index.py` file must be updated to match this exactly.

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
      "description":            { "type": "semantic_text", "inference_id": "elser-v2-endpoint" },
      "key_feature":            { "type": "text" },
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

No `premium_range` nested object. No `coverage_type` (replaced by `plan_category`).

---

## Hybrid Search Query Contract

The query Sub-Agent 1 passes to the Elastic MCP `search` tool. This is the authoritative template.

```json
{
  "index": "insurance_products",
  "query": {
    "bool": {
      "should": [
        {
          "semantic": {
            "field": "description",
            "query": "<coverage_goals as natural language — e.g. 'life protection and critical illness for non-smoker family'>"
          }
        },
        {
          "match": {
            "name": "<coverage_goals as space-separated keywords>"
          }
        }
      ],
      "filter": [
        { "range": { "min_age": { "lte": "<customer_age>" } } },
        { "range": { "max_age": { "gte": "<customer_age>" } } }
      ],
      "minimum_should_match": 1
    }
  },
  "size": 10,
  "_source": true
}
```

**Fallback query** (relax age filter when `size=0` results):

```json
{
  "index": "insurance_products",
  "query": {
    "bool": {
      "should": [
        { "semantic": { "field": "description", "query": "<coverage_goals>" } },
        { "match": { "name": "<keywords>" } }
      ],
      "minimum_should_match": 1
    }
  },
  "size": 10
}
```

---

## `tools.yaml` Contract (Authoritative)

The full `agent_builder/tools.yaml` must define **three tools**. Current file is missing the Elastic MCP tool.

```yaml
# Tool 1: Elastic MCP semantic search (ADD — currently missing)
/elastic_product_search:
  post:
    operationId: elastic_product_search
    summary: Hybrid ELSER v2 semantic + BM25 search over insurance_products index
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
                description: "Customer coverage goals in natural language"
              customer_age:
                type: integer
              relax_age_filter:
                type: boolean
                default: false
    responses:
      "200":
        description: Candidate products with _score
        content:
          application/json:
            schema:
              type: object
              properties:
                candidates:
                  type: array
                  items: { type: object }
                total_hits: { type: integer }
                fallback_triggered: { type: boolean }

# Tool 2: compliance_check (EXISTS — add 400 response schema)
# Tool 3: rank_products   (EXISTS — add audit field to 200 response schema)
```

---

## `_score` Normalisation (Gap G2 Fix)

The `rank_products` function must normalise raw ELSER scores before computing suitability. Replace the current `score_product` approach with a batch-normalisation step:

```python
def normalise_scores(products: list[dict]) -> list[dict]:
    """Normalise raw ELSER elser_score values to [0, 1] across the batch."""
    scores = [p.get("elser_score", 0.5) for p in products]
    max_score = max(scores) if scores else 1.0
    if max_score == 0:
        max_score = 1.0
    for p in products:
        p["elser_score_normalised"] = p.get("elser_score", 0.5) / max_score
    return products
```

The `score_product` function then uses `elser_score_normalised` (not raw `elser_score`) for the `elser_relevance` component.

---

## `shared/models.py` Module

Create `shared/models.py` with all dataclasses above. Both Cloud Functions import from it via a relative path reference in their deployment packages.

```text
shared/
  __init__.py
  models.py      ← CustomerProfile, InsuranceProduct, CandidateProduct,
                    ComplianceRequest, ComplianceResponse, RejectedProduct,
                    RankRequest, RankResponse, RankedProduct, ScoreBreakdown, AuditTrail
  validation.py  ← Pydantic validators for Cloud Function entry points
```

Cloud Function deployment: each function's directory includes `shared/` as a sibling directory. `requirements.txt` references it via a local path or the shared code is copied at build time via `cloudbuild.yaml`.

---

## Product Catalog Design (`data/insurance_products.json`)

28 products across 7 types (4 per type). Required variation for testing:

| Type | IDs | Age Range Variation | Smoker | Income Floor |
|---|---|---|---|---|
| `term_life` | TERM001–TERM004 | 18–65, 25–55, 18–75, 30–60 | Mix | ₹3L–₹5L |
| `health` | HLTH001–HLTH004 | 18–65, 0–65, 45–80, 18–60 | Mix | ₹2L–₹4L |
| `ulip` | ULIP001–ULIP004 | 18–55, 25–60, 18–65, 30–55 | All false | ₹5L–₹10L |
| `endowment` | ENDT001–ENDT004 | 18–55, 25–60, 18–65, 30–55 | Mix | ₹3L–₹6L |
| `critical_illness` | CRIT001–CRIT004 | 18–65, 25–60, 18–70, 30–65 | Mix | ₹3L–₹5L |
| `pension` | PENS001–PENS004 | 30–65, 25–60, 35–65, 40–70 | Mix | ₹5L–₹8L |
| `child_plan` | CHLD001–CHLD004 | 18–45, 20–50, 18–55, 25–50 | All false | ₹4L–₹8L |

Each product must have a `description` field of 2–3 sentences rich in insurance terminology and coverage-goal keywords so ELSER semantic matching works correctly.

---

## File Structure

```text
shared/
  __init__.py                          ← CREATE: empty
  models.py                            ← CREATE: all dataclasses above
  validation.py                        ← CREATE: Pydantic validators

data/
  insurance_products.json              ← GENERATE: 28 synthetic products (currently empty [])
  generate_products.py                 ← CREATE: script to generate + validate catalog

ingest/
  create_index.py                      ← MODIFY: align schema to authoritative mapping above;
                                                  add ELSER endpoint readiness polling loop

functions/
  compliance_check/
    main.py                            ← MODIFY: add Pydantic input validation (Gap G4)
    requirements.txt                   ← MODIFY: add pydantic>=2.0
  rank_products/
    main.py                            ← MODIFY: add elser_score normalisation (Gap G2),
                                                  add audit trail (Gap G5),
                                                  add Pydantic input validation (Gap G5)
    requirements.txt                   ← MODIFY: add pydantic>=2.0

agent_builder/
  tools.yaml                           ← MODIFY: add elastic_product_search tool (Gap G6)

tests/
  __init__.py                          ← CREATE: empty package marker
  conftest.py                          ← CREATE: shared fixtures (sample profile, products)
  test_models.py                       ← CREATE: round-trip serialisation tests for all models
  test_create_index.py                 ← CREATE: index schema field assertions (with ES mock)
  test_compliance_check.py             ← CREATE: 5 rule tests + validation tests
  test_rank_products.py                ← CREATE: scoring formula, normalisation, audit trail
  test_insurance_products_data.py      ← CREATE: catalog completeness + schema conformance

infra/
  cloudbuild.yaml                      ← MODIFY: add shared/ copy steps before each function deploy
```

---

## Constitution Check

- [x] **Compliance guardrail respected** — This plan defines `ComplianceResponse.passed` as the ONLY input to `rank_products`. Rejected products are never in `passed`.
- [x] **Latency target honoured** — Shared models are pure Python dataclasses; zero I/O at import time. Pydantic validation adds < 1ms per call. No latency risk.
- [x] **No hallucination risk in deterministic logic** — All models are typed, validated, and deterministic. No LLM involvement in data model layer.
- [x] **Audit trail considered** — `AuditTrail` model captures all scored products, weights, and anonymised profile hash. Defined as a required output field of `rank_products`.

---

## Implementation Order

These must be built **in this sequence** — each step unblocks the next:

```
1. shared/models.py + shared/validation.py
2. data/insurance_products.json (generate 28 products)
3. ingest/create_index.py (fix schema + readiness polling)
4. Run: python ingest/create_index.py + python ingest/index_products.py
5. functions/compliance_check/main.py (add validation — Gap G4)
6. functions/rank_products/main.py (normalisation + audit — Gap G2, G5)
7. agent_builder/tools.yaml (add Elastic MCP tool — Gap G6)
8. tests/ (conftest + all test files)
```

Only after steps 1–8 are complete should individual feature plans (001–005) be implemented.

---

## Open Questions

1. **Elastic MCP server endpoint** — Is the Elastic MCP server the Elastic-hosted cloud endpoint or a self-hosted Cloud Run service? The base URL in `tools.yaml` cannot be finalised without this.
2. **Pydantic deployment strategy** — Cloud Functions 2nd gen supports pip dependencies via `requirements.txt`. Confirm `pydantic>=2.0` install size doesn't push the function cold-start over the 1.5s budget when combined with `elasticsearch-py`.
3. **`shared/` module packaging** — Confirm the Cloud Build step in `cloudbuild.yaml` copies `shared/` into each function's build context, or that the function's `requirements.txt` references it via `-e ../shared`.

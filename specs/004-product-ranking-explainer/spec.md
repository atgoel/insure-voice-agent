# Feature Specification: Product Ranking & Recommendation Explainer

**Feature Directory**: `specs/004-product-ranking-explainer/`
**Created**: 2026-05-26
**Status**: Draft

## Overview

Sub-Agent 3 calls the `rank_products` Cloud Function with the compliance-passed products and customer profile. The function scores each product against a multi-factor suitability formula, ranks them, and returns the top-3 with suitability scores and score breakdowns. The Root Agent then uses these ranked results to craft a concise, voice-friendly recommendation explanation for TTS delivery.

---

## User Stories & Acceptance Criteria

### Story 1 — Multi-Factor Suitability Scoring (Priority: P1)

The `rank_products` function scores each passed product using three factors: ELSER relevance, age centrality, and income fit. The weights are documented and tunable. Top-3 are returned in ranked order.

**Why P1**: Without ranked recommendations, the demo has no output. Scoring transparency is also a hackathon judging criterion.

**Independent Test**: POST to the function with 5 test products having known attributes and a test customer profile. Verify the ranking is deterministic and the top-ranked product matches the expected winner based on the formula.

**Acceptance Scenarios**:

1. **Given** 5 passed products with varying ELSER scores and age fits, **When** `rank_products` runs, **Then** the product with highest combined suitability score (ELSER×0.4 + age_centrality×0.3 + income_fit×0.3) is ranked #1.
2. **Given** two products with identical ELSER scores but different age centrality, **When** ranked, **Then** the product whose age range better centres on the customer's age is ranked higher.
3. **Given** a list of fewer than 3 passed products, **When** `rank_products` runs, **Then** the response contains only as many items as there are passed products (no padding or error).
4. **Given** `passed_products` is an empty list, **When** called, **Then** the function returns `{"top3": []}` without error.

---

### Story 2 — Voice-Optimised Explanation Generation (Priority: P1)

The Root Agent uses the top-3 ranked products to generate a recommendation response that is:
- Under 120 words total
- Suitable for TTS WaveNet delivery (no markdown, no tables, conversational tone)
- Uses INR (₹) for all monetary values
- Includes per-product: name, key benefit, approximate premium range, personalised reason

**Why P1**: This is the demo's WOW moment — the voice response with intelligent product matching.

**Independent Test**: Generate a recommendation response from a known top-3 list and verify it is under 120 words, mentions each product by name, and includes a personalised rationale for each.

**Acceptance Scenarios**:

1. **Given** a top-3 ranked list, **When** the Root Agent generates the response, **Then** the response is ≤ 120 words when spoken.
2. **Given** a 38-year-old non-smoker with ₹15L income needing life + health cover, **When** recommendations are delivered, **Then** each product explanation references at least one of: the customer's age, their family, their income bracket, or their stated coverage goal.
3. **Given** the voice response is delivered via TTS, **When** the customer asks "tell me more about the first one", **Then** the Root Agent provides additional detail on product #1 within 80 words.

---

### Story 3 — Audit Trail in Ranking Response (Priority: P2)

The `rank_products` response includes a full audit object: input scores, formula weights, and final ranking for every product evaluated (not just top-3).

**Why P2**: Required for constitution compliance. Useful for debugging and Phase 2 IRDAI audit.

**Acceptance Scenarios**:

1. **Given** 7 passed products, **When** `rank_products` runs, **Then** the response includes `audit.all_scored` with all 7 products' scores, not just the top-3.
2. **Given** the audit trail is logged via Cloud Logging, **When** reviewed post-session, **Then** each entry contains: customer profile hash (anonymised), product IDs evaluated, ELSER scores, suitability scores, and final top-3.

---

## Scoring Formula

```
suitability_score = (elser_relevance × 0.4) + (age_centrality × 0.3) + (income_fit × 0.3)
```

Where:
- `elser_relevance`: ELSER `_score` from Elasticsearch, normalised to [0,1]
- `age_centrality`: `1.0 - |customer_age - product_midpoint_age| / product_age_range`
- `income_fit`: `min(customer_income / (sum_need / 10), 1.0)` — how comfortably income covers the implied premium

Weights are constants in `functions/rank_products/main.py`. Future Phase 2 work may make them configurable.

---

## API Contract

**Endpoint**: `POST /rank_products`

**Request**:
```json
{
  "passed_products": [
    {
      "id": "string",
      "name": "string",
      "min_age": 25,
      "max_age": 65,
      "elser_score": 0.87,
      "premium_min_monthly": 1500,
      "premium_max_monthly": 4500
    }
  ],
  "customer_profile": {
    "age": 38,
    "income": 1500000,
    "sum_need": 10000000
  }
}
```

**Response**:
```json
{
  "top3": [
    {
      "rank": 1,
      "product": { "id": "...", "name": "...", "premium_min_monthly": 1500, "premium_max_monthly": 4500 },
      "suitability_score": 0.7842,
      "score_breakdown": {
        "elser_relevance": 0.87,
        "age_centrality": 0.91,
        "income_fit": 0.75
      }
    }
  ],
  "audit": {
    "all_scored": [...],
    "formula_weights": { "elser": 0.4, "age": 0.3, "income": 0.3 },
    "customer_profile_hash": "<sha256>"
  }
}
```

---

## Edge Cases

- `passed_products` empty → return `{"top3": []}`.
- `elser_score` missing from a product → default to `0.5` (normalised to `1.0` if only product in batch).
- `sum_need` is 0 or absent → `income_fit` defaults to `0.5`.
- All products have the same score → return first 3 in original order (stable sort).
- `min_age == max_age` for a product → age_centrality = 1.0 if customer age matches exactly, 0.0 otherwise.

---

## Out of Scope

- ML-based personalised ranking (Phase 2).
- User preference learning across sessions.
- A/B testing of formula weights.
- Explanation generation within the Cloud Function (that's the Root Agent's job).

---

## Technical Notes

- Existing implementation: `functions/rank_products/main.py` — scoring, normalisation, input validation (`RankRequestValidator`), empty guard, audit trail, and HTTP handler are all complete.
- Tests: `tests/test_rank_products.py` using pytest — covers scoring formula, normalisation, audit trail, and HTTP 400/200 responses. Edge-case tests for `sum_need=0`, `min_age==max_age`, and missing `elser_score` are pending (TASK-064–069).
- Sub-Agent 3 (`Recommendation Explainer`) is a separate ADK `LlmAgent` registered as an `AgentTool` on the root agent in `agent_builder/agent_definition.py`. Its system prompt is `agent_builder/sub_agent3_explainer_prompt.md`.
- The Cloud Function's job ends at top-3 + audit. All voice explanation text is generated by Sub-Agent 3 (Gemini 2.0 Flash) from the top-3 output.
- Cloud Logging for audit: `print(json.dumps(audit_record))` in Cloud Functions writes to structured logs automatically.

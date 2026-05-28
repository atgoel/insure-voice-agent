"""
rank_products — Cloud Function
Scores and ranks compliance-passed products by suitability.

Endpoint: POST /rank_products
Input:  { "passed_products": [...], "customer_profile": {...} }
Output:
  {
    "top3": [{ "rank", "product", "suitability_score", "score_breakdown" }],
    "audit": {
      "all_scored": [...all products with scores, sorted by suitability_score desc],
      "formula_weights": {"elser": 0.4, "age": 0.3, "income": 0.3},
      "customer_profile_hash": "<sha256 of anonymised profile>"
    }
  }

Constitution constraints:
  §III Latency: < 1s budget for this function.
  §IV  Audit trail: all_scored + formula_weights + profile_hash in every response.
  §V   No PII: profile hash is a one-way SHA-256; raw profile is never stored.
"""
import hashlib
import json
import sys
from typing import Optional

import functions_framework
from pydantic import ValidationError

# shared/ is co-deployed alongside this function (see infra/cloudbuild.yaml TASK-051)
sys.path.insert(0, ".")
from shared.validation import RankRequestValidator  # noqa: E402

# ---------------------------------------------------------------------------
# Scoring constants (UPPER_SNAKE_CASE)
# ---------------------------------------------------------------------------
WEIGHT_ELSER  = 0.4
WEIGHT_AGE    = 0.3
WEIGHT_INCOME = 0.3

FORMULA_WEIGHTS = {
    "elser":  WEIGHT_ELSER,
    "age":    WEIGHT_AGE,
    "income": WEIGHT_INCOME,
}


# ---------------------------------------------------------------------------
# TASK-034: Score normalisation
# ---------------------------------------------------------------------------

def normalise_scores(products: list[dict]) -> list[dict]:
    """Normalise raw elser_score values across the candidate batch to [0, 1].

    Divides each elser_score by max(elser_score) across the batch and stores
    the result as elser_score_normalised. Handles all-zero and single-product
    edge cases by setting elser_score_normalised=1.0 for all items.

    The raw elser_score field is preserved unchanged for the audit trail.
    """
    max_score = max((p.get("elser_score", 0.0) for p in products), default=0.0)
    for p in products:
        raw = p.get("elser_score", 0.0)
        p["elser_score_normalised"] = round(raw / max_score, 6) if max_score > 0 else 1.0
    return products


# ---------------------------------------------------------------------------
# TASK-035: Scoring formula using normalised score
# ---------------------------------------------------------------------------

def score_product(product: dict, profile: dict) -> dict:
    """Compute suitability_score in [0, 1] and return score_breakdown.

    All three components are clamped to [0, 1] before weighting:
      elser_relevance — normalised ELSER sparse vector relevance (always [0,1])
      age_centrality  — 1.0 at product age midpoint, decays linearly to 0 at bounds
      income_fit      — how comfortably annual income covers annual premium estimate

    Uses elser_score_normalised (set by normalise_scores) not raw elser_score.
    """
    elser_norm = product.get("elser_score_normalised", 1.0)

    # Age centrality: peaks at midpoint of [min_age, max_age]
    min_age  = product.get("min_age", 18)
    max_age  = product.get("max_age", 65)
    mid_age  = (min_age + max_age) / 2
    age_span = max(max_age - min_age, 1)
    age_centrality = max(0.0, 1.0 - abs(profile.get("age", mid_age) - mid_age) / age_span)

    # Income fit: income / (annual premium estimate derived from sum_need)
    income   = profile.get("income") or 0
    sum_need = profile.get("sum_need") or 0
    income_fit = min(income / max(sum_need / 10, 1), 1.0) if sum_need > 0 else 0.5

    suitability = (
        elser_norm    * WEIGHT_ELSER
        + age_centrality * WEIGHT_AGE
        + income_fit     * WEIGHT_INCOME
    )
    # Guard: floating-point arithmetic can nudge above 1.0 — clamp defensively
    suitability = min(round(suitability, 4), 1.0)

    return {
        "suitability_score": suitability,
        "score_breakdown": {
            "elser_relevance": round(elser_norm, 4),
            "age_centrality":  round(age_centrality, 4),
            "income_fit":      round(income_fit, 4),
        },
    }


# ---------------------------------------------------------------------------
# TASK-038: Profile hash (§V — no PII stored)
# ---------------------------------------------------------------------------

def _profile_hash(profile: dict) -> str:
    """Return a one-way SHA-256 of the customer profile for audit correlation.

    json.dumps with sort_keys=True ensures deterministic output regardless of
    dict insertion order. The hash is not reversible — no PII is stored.
    """
    return hashlib.sha256(
        json.dumps(profile, sort_keys=True, default=str).encode()
    ).hexdigest()


# ---------------------------------------------------------------------------
# Cloud Function handler
# ---------------------------------------------------------------------------

@functions_framework.http
def rank_products(request):
    """Rank compliance-passed products by suitability score.

    Returns HTTP 400 on invalid input.
    Returns HTTP 200 with {top3, audit} on success — even when passed_products is empty.
    """
    # --- Input validation (TASK-036) ----------------------------------------
    data = request.get_json(silent=True)
    if data is None:
        return (
            json.dumps({"error": "validation_error", "detail": "Request body must be JSON"}),
            400,
            {"Content-Type": "application/json"},
        )

    try:
        validated = RankRequestValidator.model_validate(data)
    except ValidationError as exc:
        fields = [".".join(str(loc) for loc in err["loc"]) for err in exc.errors()]
        return (
            json.dumps({"error": "validation_error", "fields": fields}),
            400,
            {"Content-Type": "application/json"},
        )

    products: list[dict] = validated.passed_products
    profile: dict        = validated.customer_profile.model_dump()
    profile_hash: str    = _profile_hash(profile)

    # --- Empty guard (TASK-039) ---------------------------------------------
    if not products:
        return (
            json.dumps({
                "top3": [],
                "audit": {
                    "all_scored":            [],
                    "formula_weights":       FORMULA_WEIGHTS,
                    "customer_profile_hash": profile_hash,
                },
            }),
            200,
            {"Content-Type": "application/json"},
        )

    # --- Normalise + score (TASK-034, TASK-035) -----------------------------
    products = normalise_scores(products)

    scored: list[dict] = []
    for product in products:
        scores = score_product(product, profile)
        scored.append({**product, **scores})

    scored.sort(key=lambda x: x["suitability_score"], reverse=True)

    # --- Build top-3 result -------------------------------------------------
    top3 = [
        {
            "rank":             i + 1,
            "product":          p,
            "suitability_score": p["suitability_score"],
            "score_breakdown":  p["score_breakdown"],
        }
        for i, p in enumerate(scored[:3])
    ]

    # --- Audit trail (TASK-037 / Constitution §IV) --------------------------
    audit = {
        "all_scored":            scored,
        "formula_weights":       FORMULA_WEIGHTS,
        "customer_profile_hash": profile_hash,
    }

    return json.dumps({"top3": top3, "audit": audit}), 200, {"Content-Type": "application/json"}

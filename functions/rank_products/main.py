"""
rank_products — Cloud Function
Scores and ranks compliance-passed products by suitability.

Endpoint: POST /rank_products
Input:  { "passed_products": [...], "customer_profile": {...} }
Output: { "top3": [{ "rank", "product", "suitability_score", "score_breakdown" }] }
"""
import json
import functions_framework


def score_product(product: dict, profile: dict) -> dict:
    elser_score = product.get("_score", 0.5)

    # Age centrality: 1.0 when age is at midpoint of product range
    mid_age = (product["min_age"] + product["max_age"]) / 2
    age_range = max(product["max_age"] - product["min_age"], 1)
    age_centrality = 1.0 - abs(profile.get("age", mid_age) - mid_age) / age_range

    # Income fit: how comfortably annual income covers estimated premium
    income = profile.get("income", 0)
    sum_need = profile.get("sum_need", 0)
    income_fit = min(income / max(sum_need / 10, 1), 1.0) if sum_need > 0 else 0.5

    suitability = (elser_score * 0.4) + (age_centrality * 0.3) + (income_fit * 0.3)

    return {
        "suitability_score": round(suitability, 4),
        "score_breakdown": {
            "elser_relevance": round(elser_score, 4),
            "age_centrality": round(age_centrality, 4),
            "income_fit": round(income_fit, 4)
        }
    }


@functions_framework.http
def rank_products(request):
    data = request.get_json()
    products = data.get("passed_products", [])
    profile = data.get("customer_profile", {})

    scored = []
    for product in products:
        scores = score_product(product, profile)
        scored.append({**product, **scores})

    scored.sort(key=lambda x: x["suitability_score"], reverse=True)
    top3 = scored[:3]

    result = [
        {"rank": i + 1, "product": p, "suitability_score": p["suitability_score"],
         "score_breakdown": p["score_breakdown"]}
        for i, p in enumerate(top3)
    ]

    return json.dumps({"top3": result}), 200, {"Content-Type": "application/json"}

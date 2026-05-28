"""
product_search — Cloud Function
Sub-Agent 1: Hybrid ELSER semantic + BM25 search over the insurance product catalog.

Endpoint: POST /product_search
Input (JSON):
    query              str   — Natural-language customer intent
                               e.g. "affordable term plan for a 35-year-old non-smoker"
    customer_age       int   — Customer age (18–75); used for eligibility pre-filter
    is_smoker          bool  — Smoker flag; filters out smoker_eligible=false products
    income             int   — Annual income in INR; filters by min_income
    product_type       str?  — Optional product type filter (e.g. "term_life", "health")
    size               int?  — Max candidates to return (default 10, max 20)
    relax_age_filter   bool? — If true, skip age bounds filter (fallback mode, default false)

Output (JSON):
    {
      "candidates": [
        {
          <all InsuranceProduct fields>,
          "elser_score": float   -- raw RRF score from Elasticsearch (_score)
        },
        ...
      ],
      "total_hits": int,
      "fallback_triggered": bool   -- true when relax_age_filter was applied
    }

Error responses:
    400  {"error": "validation_error", "fields": [...]}  — missing/invalid input
    500  {"error": "search_error", "detail": "..."}       — Elasticsearch failure

Architecture notes:
  - Uses the Retrievers API with RRF (Reciprocal Rank Fusion) combining:
      Leg 1: semantic query on description + key_feature  (semantic_text, Serverless EIS)
      Leg 2: BM25 multi_match on name + tags             (keyword recall boost)
  - Hard eligibility filters are applied to BOTH retriever legs so no ineligible
    product can surface even at low score, before compliance_check is called.
  - elser_score is the raw Elasticsearch _score from RRF (not normalised here);
    rank_products normalises across the batch before scoring.
  - Constitution §III: latency budget for this function is < 2s end-to-end.
"""
import json
import os

import functions_framework
from elasticsearch import Elasticsearch

ES_URL = os.environ["ES_URL"]
ES_API_KEY = os.environ["ES_API_KEY"]
ALIAS_NAME = "insurance_products_current"
DEFAULT_SIZE = 10
MAX_SIZE = 20

# Required fields for input validation
REQUIRED_FIELDS = {"query": str, "customer_age": int, "is_smoker": bool, "income": int}

client = Elasticsearch(ES_URL, api_key=ES_API_KEY)


def _validate(data: dict) -> list[str]:
    """Return a list of missing or incorrectly-typed field names."""
    errors = []
    for field, expected_type in REQUIRED_FIELDS.items():
        if field not in data:
            errors.append(f"{field} (missing)")
        elif not isinstance(data[field], expected_type):
            errors.append(f"{field} (expected {expected_type.__name__})")
    age = data.get("customer_age")
    if isinstance(age, int) and not (18 <= age <= 75):
        errors.append("customer_age (must be 18–75)")
    size = data.get("size", DEFAULT_SIZE)
    if not isinstance(size, int) or size < 1 or size > MAX_SIZE:
        errors.append(f"size (must be 1–{MAX_SIZE})")
    return errors


def _build_eligibility_filters(customer_age: int, is_smoker: bool, income: int, relax_age: bool) -> list[dict]:
    """Build Elasticsearch filter clauses that enforce hard eligibility constraints.

    These are the same rules the compliance engine checks — applying them at search
    time reduces the candidate set and keeps latency under the 2s budget.
    """
    filters: list[dict] = [
        {"term": {"is_active": True}},
        {"range": {"min_income": {"lte": income}}},
    ]
    if not relax_age:
        # Product's min_age <= customer_age <= product's max_age
        filters.append({"range": {"min_age": {"lte": customer_age}}})
        filters.append({"range": {"max_age": {"gte": customer_age}}})
    if is_smoker:
        # Only return products that explicitly allow smokers
        filters.append({"term": {"smoker_eligible": True}})
    return filters


def _build_query(
    query_text: str,
    customer_age: int,
    is_smoker: bool,
    income: int,
    product_type: str | None,
    size: int,
    relax_age: bool,
) -> dict:
    """Build the Retrievers API query with RRF hybrid search and hard eligibility filters.

    Pattern (per elasticsearch-onboarding skill, Decision F2 — Hybrid with RRF):
      rrf
        ├── standard retriever: semantic query on description + key_feature
        └── standard retriever: BM25 multi_match on name + tags

    Filters are applied identically to BOTH legs so the union is always eligible.
    """
    filters = _build_eligibility_filters(customer_age, is_smoker, income, relax_age)
    if product_type:
        filters.append({"term": {"product_type": product_type}})

    return {
        "size": size,
        "_source": True,   # return full _source for all product fields
        "retriever": {
            "rrf": {
                "retrievers": [
                    # Leg 1: semantic search on description + key_feature (ELSER via EIS)
                    {
                        "standard": {
                            "query": {
                                "bool": {
                                    "should": [
                                        {"semantic": {"field": "description", "query": query_text}},
                                        {"semantic": {"field": "key_feature",  "query": query_text}},
                                    ],
                                    "minimum_should_match": 1,
                                }
                            },
                            "filter": filters,
                        }
                    },
                    # Leg 2: BM25 keyword recall on name (boosted) + tags
                    {
                        "standard": {
                            "query": {
                                "multi_match": {
                                    "query":  query_text,
                                    "fields": ["name^2", "tags", "sales_pitch"],
                                    "type":   "best_fields",
                                }
                            },
                            "filter": filters,
                        }
                    },
                ],
                "rank_window_size": 20,    # candidates considered per retriever leg (ES 9.x)
                "rank_constant": 60,    # RRF damping factor (default; good starting point)
            }
        },
    }


def _hits_to_candidates(hits: list[dict]) -> list[dict]:
    """Convert raw Elasticsearch hits to candidate product dicts with elser_score."""
    candidates = []
    for hit in hits:
        product = hit["_source"]
        product["elser_score"] = float(hit.get("_score", 0.0))
        candidates.append(product)
    return candidates


@functions_framework.http
def product_search(request):
    """Cloud Function entry point — POST /product_search."""
    data = request.get_json(silent=True)
    if data is None:
        return (
            json.dumps({"error": "validation_error", "detail": "Request body must be JSON"}),
            400,
            {"Content-Type": "application/json"},
        )

    errors = _validate(data)
    if errors:
        return (
            json.dumps({"error": "validation_error", "fields": errors}),
            400,
            {"Content-Type": "application/json"},
        )

    query_text: str       = data["query"]
    customer_age: int     = data["customer_age"]
    is_smoker: bool       = data["is_smoker"]
    income: int           = data["income"]
    product_type: str | None = data.get("product_type")
    size: int             = int(data.get("size", DEFAULT_SIZE))
    relax_age: bool       = bool(data.get("relax_age_filter", False))

    try:
        es_query = _build_query(
            query_text=query_text,
            customer_age=customer_age,
            is_smoker=is_smoker,
            income=income,
            product_type=product_type,
            size=size,
            relax_age=relax_age,
        )
        response = client.search(index=ALIAS_NAME, body=es_query)
    except Exception as exc:  # noqa: BLE001
        return (
            json.dumps({"error": "search_error", "detail": str(exc)}),
            500,
            {"Content-Type": "application/json"},
        )

    hits = response["hits"]["hits"]
    total = response["hits"]["total"]["value"]
    candidates = _hits_to_candidates(hits)

    result = {
        "candidates":         candidates,
        "total_hits":         total,
        "fallback_triggered": relax_age,
    }
    return json.dumps(result), 200, {"Content-Type": "application/json"}

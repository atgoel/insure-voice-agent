"""
elastic_mcp_server — Cloud Run service
Constitution §VI primary Elastic search integration for InsureVoice.

Exposes the insurance product search via TWO transports on the same service:

  POST /search_products  — Plain HTTP REST (OpenAPI-compatible)
                           Registered in Agent Builder via tools.yaml.
                           Agent Builder calls this URL directly — no CF wrapper.

  POST /mcp              — MCP Streamable HTTP (JSON-RPC, spec 2025-03-26) via FastMCP
                           For programmatic ADK agents using MCPToolset, or future
                           MCP-native clients.

Both transports execute the same logic:
    _validate() → _build_eligibility_filters() → _build_query() → ES RRF → _hits_to_candidates()

Architecture (post-collapse):
    Agent Builder
        │  POST $MCP_SERVER_URL/search_products   (OpenAPI, registered in tools.yaml)
        ▼
    THIS SERVICE  ← Elastic MCP Server (Cloud Run) — Constitution §VI primary integration
        │  elasticsearch-py
        ▼
    Elasticsearch Cloud Serverless
        ELSER v2 RRF hybrid — alias: insurance_products_current
"""
import json
import os
from typing import Optional

from elasticsearch import Elasticsearch
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastmcp import FastMCP

ES_URL = os.environ["ES_URL"]
ES_API_KEY = os.environ["ES_API_KEY"]
ALIAS_NAME = "insurance_products_current"
DEFAULT_SIZE = 10
MAX_SIZE = 20

# Required fields and their expected types for input validation
REQUIRED_FIELDS = {"query": str, "customer_age": int, "is_smoker": bool, "income": int}

_es = Elasticsearch(ES_URL, api_key=ES_API_KEY)


# ---------------------------------------------------------------------------
# Core search helpers (shared by both transports)
# ---------------------------------------------------------------------------

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


def _build_eligibility_filters(
    customer_age: int, is_smoker: bool, income: int, relax_age: bool
) -> list[dict]:
    """Build Elasticsearch filter clauses that enforce hard eligibility constraints.

    These are the same rules the compliance engine checks — applying them at search
    time reduces the candidate set and keeps latency under the 2s budget.
    """
    filters: list[dict] = [
        {"term": {"is_active": True}},
        {"range": {"min_income": {"lte": income}}},
    ]
    if not relax_age:
        filters.append({"range": {"min_age": {"lte": customer_age}}})
        filters.append({"range": {"max_age": {"gte": customer_age}}})
    if is_smoker:
        filters.append({"term": {"smoker_eligible": True}})
    return filters


def _build_query(
    query_text: str,
    customer_age: int,
    is_smoker: bool,
    income: int,
    product_type: Optional[str],
    size: int,
    relax_age: bool,
) -> dict:
    """Build the Retrievers API query with RRF hybrid search and hard eligibility filters.

    Pattern: RRF fusing two standard retriever legs:
      Leg 1 — semantic query on description + key_feature  (ELSER v2 via Serverless EIS)
      Leg 2 — BM25 multi_match on name^2, tags, sales_pitch  (keyword recall boost)

    Filters are applied identically to BOTH legs so no ineligible product can surface.
    """
    filters = _build_eligibility_filters(customer_age, is_smoker, income, relax_age)
    if product_type:
        filters.append({"term": {"product_type": product_type}})

    return {
        "size": size,
        "_source": True,
        "retriever": {
            "rrf": {
                "retrievers": [
                    # Leg 1: semantic search on description + key_feature
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
                    # Leg 2: BM25 keyword recall on name (boosted) + tags + sales_pitch
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
                "rank_window_size": 20,
                "rank_constant": 60,
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


def _execute_search(data: Optional[dict]) -> tuple[dict, int]:
    """Core search pipeline. Returns (response_body_dict, http_status_int).

    Called by both the REST endpoint and the MCP tool so the logic lives in exactly
    one place.
    """
    if data is None:
        return {"error": "validation_error", "detail": "Request body must be JSON"}, 400

    errors = _validate(data)
    if errors:
        return {"error": "validation_error", "fields": errors}, 400

    query_text: str         = data["query"]
    customer_age: int       = data["customer_age"]
    is_smoker: bool         = data["is_smoker"]
    income: int             = data["income"]
    product_type: Optional[str] = data.get("product_type")
    size: int               = int(data.get("size", DEFAULT_SIZE))
    relax_age: bool         = bool(data.get("relax_age_filter", False))

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
        response = _es.search(index=ALIAS_NAME, body=es_query)
    except Exception as exc:  # noqa: BLE001
        return {"error": "search_error", "detail": str(exc)}, 500

    hits = response["hits"]["hits"]
    total = response["hits"]["total"]["value"]
    candidates = _hits_to_candidates(hits)
    return {
        "candidates": candidates,
        "total_hits": total,
        "fallback_triggered": relax_age,
    }, 200


# ---------------------------------------------------------------------------
# Transport 1: MCP Streamable HTTP  (for ADK MCPToolset / MCP-native clients)
# POST /mcp
# ---------------------------------------------------------------------------

# stateless_http=True: each request is independent — no session handshake required.
mcp = FastMCP("insure-voice-elastic", stateless_http=True)


@mcp.tool()
def search_products(
    query: str,
    customer_age: int,
    is_smoker: bool,
    income: int,
    product_type: Optional[str] = None,
    size: int = DEFAULT_SIZE,
    relax_age_filter: bool = False,
) -> dict:
    """Search insurance products using ELSER v2 RRF hybrid search.

    Satisfies Constitution §VI: Elastic MCP server is the primary search integration.

    Args:
        query: Natural-language customer intent, e.g. "term life for family aged 35".
        customer_age: Customer age in years (18–75).
        is_smoker: If True, filters out products where smoker_eligible=False.
        income: Annual income in INR; products with min_income > income are excluded.
        product_type: Optional product type filter (e.g. "term_life", "health").
        size: Max candidates to return (1–20, default 10).
        relax_age_filter: When True, skips age bounds filter (fallback mode).

    Returns:
        {"candidates": [...], "total_hits": int, "fallback_triggered": bool}
    """
    data = {
        "query": query,
        "customer_age": customer_age,
        "is_smoker": is_smoker,
        "income": income,
        "size": size,
        "relax_age_filter": relax_age_filter,
    }
    if product_type is not None:
        data["product_type"] = product_type

    body, status = _execute_search(data)
    if status != 200:
        raise ValueError(f"{body.get('error')}: {body.get('fields') or body.get('detail', '')}")
    return body


# ---------------------------------------------------------------------------
# Transport 2: Plain HTTP REST  (for Agent Builder OpenAPI tool registration)
# POST /search_products  — same contract as tools.yaml elastic_product_search
# ---------------------------------------------------------------------------

app = FastAPI(title="InsureVoice Elastic MCP Server", version="1.0")

# Mount the MCP transport at /mcp (JSON-RPC endpoint for MCPToolset clients)
app.mount("/mcp", mcp.http_app())


@app.post("/search_products")
async def search_products_rest(request: Request) -> JSONResponse:
    """REST adapter for Agent Builder.  Same request/response schema as tools.yaml."""
    data = await request.json()
    body, status = _execute_search(data)
    return JSONResponse(content=body, status_code=status)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))

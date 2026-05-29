"""
elastic_mcp_server_native — Cloud Run service (MCP-native transport)

This is the PARALLEL deployment alongside elastic_mcp_server.
The existing service keeps REST + MCP-via-FastAPI (for Agent Builder tools.yaml).
This service runs FastMCP as the ROOT ASGI app so MCPToolset works correctly.

Root cause of the original mount problem:
    FastAPI app + app.mount("/mcp", mcp.http_app())
    → FastMCP internally routes to /mcp within its sub-app
    → Full path becomes /mcp/mcp  ← MCPToolset gets 404

Fix here:
    Starlette root app + Mount("/", mcp.http_app())
    → FastMCP internal /mcp is served at /mcp on the outer app  ✓
    → MCPToolset(url="https://this-service.../mcp")  works

Endpoints:
    GET  /health  — liveness probe for Cloud Run
    POST /mcp     — MCP Streamable HTTP (JSON-RPC) ← MCPToolset target
    GET  /mcp     — MCP SSE channel (optional, server-to-client)

Same ELSER v2 RRF search logic as elastic_mcp_server.
"""
import os
from typing import Optional

from elasticsearch import Elasticsearch
from fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

ES_URL       = os.environ["ES_URL"]
ES_API_KEY   = os.environ["ES_API_KEY"]
ALIAS_NAME   = "insurance_products_current"
DEFAULT_SIZE = 10
MAX_SIZE     = 20

REQUIRED_FIELDS = {"query": str, "customer_age": int, "is_smoker": bool, "income": int}

_es = Elasticsearch(ES_URL, api_key=ES_API_KEY)

# ---------------------------------------------------------------------------
# Search helpers (identical to elastic_mcp_server/main.py)
# ---------------------------------------------------------------------------

def _validate(data: dict) -> list[str]:
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
    filters = _build_eligibility_filters(customer_age, is_smoker, income, relax_age)
    if product_type:
        filters.append({"term": {"product_type": product_type}})
    return {
        "size": size,
        "_source": True,
        "retriever": {
            "rrf": {
                "retrievers": [
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
    candidates = []
    for hit in hits:
        product = hit["_source"]
        product["elser_score"] = float(hit.get("_score", 0.0))
        candidates.append(product)
    return candidates


def _execute_search(data: Optional[dict]) -> tuple[dict, int]:
    if data is None:
        return {"error": "validation_error", "detail": "Request body must be JSON"}, 400
    errors = _validate(data)
    if errors:
        return {"error": "validation_error", "fields": errors}, 400

    query_text: str             = data["query"]
    customer_age: int           = data["customer_age"]
    is_smoker: bool             = data["is_smoker"]
    income: int                 = data["income"]
    product_type: Optional[str] = data.get("product_type")
    size: int                   = int(data.get("size", DEFAULT_SIZE))
    relax_age: bool             = bool(data.get("relax_age_filter", False))

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
    except Exception as exc:
        return {"error": "search_error", "detail": str(exc)}, 500

    hits = response["hits"]["hits"]
    total = response["hits"]["total"]["value"]
    return {
        "candidates": _hits_to_candidates(hits),
        "total_hits": total,
        "fallback_triggered": relax_age,
    }, 200


# ---------------------------------------------------------------------------
# MCP tool definition
# ---------------------------------------------------------------------------

mcp = FastMCP("insure-voice-elastic-native")


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
        is_smoker: If True, only smoker-eligible products are returned.
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
# ASGI application
#
# Use mcp.http_app() as the root ASGI app — its lifespan initialises the
# StreamableHTTPSessionManager.  A lightweight middleware intercepts GET /health
# before the request reaches FastMCP, so no outer Starlette wrapper is needed.
#
# FastMCP internally registers:
#   POST /mcp  — Streamable HTTP (JSON-RPC) ← MCPToolset target
#   GET  /mcp  — SSE channel (optional)
#
# MCPToolset in agent_definition.py connects to:
#   StreamableHTTPConnectionParams(url="https://<this-service>/mcp")
# ---------------------------------------------------------------------------

class _HealthMiddleware(BaseHTTPMiddleware):
    """Intercept GET /health before it reaches FastMCP."""
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health" and request.method == "GET":
            return JSONResponse({"status": "ok", "service": "elastic-mcp-server-native"})
        return await call_next(request)


app = mcp.http_app(stateless_http=True)
app.add_middleware(_HealthMiddleware)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))

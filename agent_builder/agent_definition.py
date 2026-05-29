"""
InsureVoice — ADK Agent Definition
===================================
Defines the multi-agent pipeline using Google Agent Development Kit (ADK).

Architecture:
    root_agent (LlmAgent — Gemini 2.0 Flash)
        │
        ├── MCPToolset → POST $ELASTIC_MCP_SERVER_URL/mcp  (MCP JSON-RPC)
        │     Tool: search_products  ← elastic_mcp_server/main.py (Cloud Run)
        │           ELSER v2 RRF hybrid query + elser_score injection
        │
        ├── FunctionTool: compliance_check  → POST $COMPLIANCE_CHECK_URL
        │     Deterministic eligibility rule engine (Constitution §II)
        │
        └── FunctionTool: rank_products     → POST $RANK_PRODUCTS_URL
              Suitability scoring + top-3 ranking with audit trail

MCP server is OUR Cloud Run service (functions/elastic_mcp_server/main.py).
It is NOT the generic Elastic MCP container — it wraps the ELSER RRF query logic
specific to InsureVoice (Constitution §VI).

Env vars required at runtime:
    ELASTIC_MCP_SERVER_URL  — Cloud Run service URL (set by cloudbuild.yaml)
    COMPLIANCE_CHECK_URL    — Cloud Function URL
    RANK_PRODUCTS_URL       — Cloud Function URL
"""

import os
import httpx
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, StreamableHTTPConnectionParams

# ---------------------------------------------------------------------------
# Environment — Cloud Run / Cloud Function URLs
# ---------------------------------------------------------------------------
ELASTIC_MCP_SERVER_URL        = os.environ["ELASTIC_MCP_SERVER_URL"]   # REST transport (existing)
ELASTIC_MCP_SERVER_NATIVE_URL = os.environ["ELASTIC_MCP_SERVER_NATIVE_URL"]  # MCP-native transport
COMPLIANCE_CHECK_URL          = os.environ["COMPLIANCE_CHECK_URL"]
RANK_PRODUCTS_URL             = os.environ["RANK_PRODUCTS_URL"]

# ---------------------------------------------------------------------------
# HTTP call helpers for compliance_check, rank_products, and search_products
# (All three are plain REST calls — reliable and latency-predictable)
# The elastic-mcp-server IS an MCP server (for demo); we call its REST endpoint
# here because MCPToolset requires the /mcp path to be the sub-app root.
# ---------------------------------------------------------------------------

def search_products(
    query: str,
    customer_age: int,
    is_smoker: bool,
    income: int,
    product_type: str = None,
    size: int = 5,
    relax_age_filter: bool = False,
) -> dict:
    """Search insurance products using Elastic ELSER v2 RRF hybrid search.

    Calls the elastic-mcp-server /search_products REST endpoint which runs
    a Retrievers API RRF query (sparse ELSER + BM25 + eligibility filters).

    Args:
        query: Natural language description of what the customer needs.
        customer_age: Customer age in years.
        is_smoker: Whether the customer is a smoker.
        income: Annual income in INR.
        product_type: Optional filter — 'term_life', 'health', 'ulip', etc.
        size: Number of results to return (default 5).
        relax_age_filter: If True, relaxes age eligibility filters.

    Returns:
        {"candidates": [{"product_id", "name", "product_type", "elser_score",
                          "description", "key_features", "min_age", "max_age",
                          "smoker_allowed", "min_income", "premium_min"}, ...]}
    """
    payload = {
        "query": query,
        "customer_age": customer_age,
        "is_smoker": is_smoker,
        "income": income,
        "size": size,
        "relax_age_filter": relax_age_filter,
    }
    if product_type is not None:
        payload["product_type"] = product_type
    resp = httpx.post(f"{ELASTIC_MCP_SERVER_URL}/search_products", json=payload, timeout=8.0)
    resp.raise_for_status()
    return resp.json()

def compliance_check(candidates: list, customer_profile: dict) -> dict:
    """Call the compliance_check Cloud Function.

    Validates each candidate product against deterministic eligibility rules
    (Constitution §II — no LLM involvement).

    Args:
        candidates: List of candidate products returned by search_products.
        customer_profile: Customer profile dict. Required fields:
            age (int), income (int), smoker (bool), health_status (str: "healthy"|"pre_existing"),
            coverage_goals (list[str]: e.g. ["life", "health"]).
            Optional: sum_need (int), family_size (int), dependents (int).

    Returns:
        {"passed": [...full product dicts...], "rejected": [{"product_id", "product_name", "reasons"}, ...]}
    """
    # Map agent-side field names → compliance_check API field names
    payload = {"candidate_products": candidates, "customer_profile": customer_profile}
    resp = httpx.post(COMPLIANCE_CHECK_URL, json=payload, timeout=5.0)
    resp.raise_for_status()
    return resp.json()


def rank_products(eligible_candidates: list, customer_profile: dict) -> dict:
    """Call the rank_products Cloud Function.

    Scores and ranks the top-3 eligible products by suitability, returning
    each with a full score breakdown for audit (Constitution §IV).

    Args:
        eligible_candidates: Products that passed the compliance guardrail
            (the "passed" list from compliance_check).
        customer_profile: Customer profile dict (same shape as compliance_check).

    Returns:
        {"top_3": [{"rank": int, "product_id": str, "suitability_score": float,
                    "score_breakdown": dict, "explanation": str}, ...]}
    """
    # Map agent-side field names → rank_products API field names
    payload = {"passed_products": eligible_candidates, "customer_profile": customer_profile}
    resp = httpx.post(RANK_PRODUCTS_URL, json=payload, timeout=5.0)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

root_agent = LlmAgent(
    model="gemini-2.5-flash-lite",
    name="InsureVoice",
    description=(
        "AI-powered insurance sales advisor. Listens to a customer's needs, "
        "searches the product catalog via ELSER semantic search (Elastic MCP), "
        "validates compliance, ranks top-3 products, and delivers a voice-ready response."
    ),
    instruction=open(
        os.path.join(os.path.dirname(__file__), "root_agent_prompt.md")
    ).read(),
    tools=[
        # ---------------------------------------------------------------
        # Tool 1: Elastic MCP Server NATIVE — ELSER RRF search via MCP protocol
        # FastMCP mounted at Starlette root → /mcp is the actual MCP endpoint.
        # MCPToolset auto-discovers 'search_products' via MCP initialize handshake.
        # ---------------------------------------------------------------
        MCPToolset(
            connection_params=StreamableHTTPConnectionParams(
                url=f"{ELASTIC_MCP_SERVER_NATIVE_URL}/mcp",
            )
        ),

        # ---------------------------------------------------------------
        # Tool 2: Compliance check — deterministic rule engine (Cloud Function)
        # ---------------------------------------------------------------
        FunctionTool(compliance_check),

        # ---------------------------------------------------------------
        # Tool 3: Rank products — suitability scoring (Cloud Function)
        # ---------------------------------------------------------------
        FunctionTool(rank_products),
    ],
)

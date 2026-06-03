"""
InsureVoice — ADK Agent Definition
===================================
Defines the multi-agent pipeline using Google Agent Development Kit (ADK).

Architecture:
    root_agent (LlmAgent — gemini-2.5-flash-lite)
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
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest
from google.adk.tools import FunctionTool
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, StreamableHTTPConnectionParams
from google.adk.tools.tool_context import ToolContext
from google.genai import types as genai_types

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
    tool_context: ToolContext = None,
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
    # S2' — Mechanical product_type injection from validated intake profile.
    # flash-lite omits product_type from search_products calls, defeating the
    # MCP server's term-filter (functions/elastic_mcp_server/main.py:108-109).
    # PRIMARY channel: shared_state.PROFILE_BY_SESSION (module-level dict).
    # FALLBACK: tool_context.state (ADK session state) — unreliable but free.
    # coverage_goals enums are 1:1 with product_type catalog values
    # (verified against intake.py:142-148 + insurance_products.json).
    _llm_passed_pt = product_type
    _intake_goal = None
    _profile = None
    _session_id = None
    try:
        if tool_context is not None:
            try:
                _session_id = tool_context._invocation_context.session.id
            except Exception:
                _session_id = None
            # Primary read: module-level dict
            if _session_id:
                try:
                    from shared_state import PROFILE_BY_SESSION as _PBS
                    _profile = _PBS.get(_session_id)
                except Exception:
                    _profile = None
            # Fallback: ADK session state (defense-in-depth)
            if not _profile:
                try:
                    _profile = tool_context.state.get("intake_profile") or None
                except Exception:
                    _profile = None
            if _profile:
                _goals = _profile.get("coverage_goals") or []
                if isinstance(_goals, list) and _goals:
                    # MVP: pick first goal. Multi-goal disambiguation owned
                    # by S3 (C.2 follow-up state machine), not here.
                    _intake_goal = _goals[0]
                    # Multi-goal warning — implementer is on notice that
                    # the user actually requested multiple types and S2' is
                    # only honoring the first.
                    if len(_goals) > 1:
                        try:
                            import logging as _l
                            _l.getLogger().warning(
                                "S2_INJECT_MULTIGOAL session=%s goals=%r picked=%r — multi-goal disambiguation deferred to S3",
                                (_session_id or "?")[:8], _goals, _intake_goal,
                            )
                        except Exception:
                            pass
                elif isinstance(_goals, str) and _goals.strip():
                    _intake_goal = _goals.strip()
    except Exception:
        _intake_goal = None
    if _intake_goal:
        product_type = _intake_goal  # override LLM-passed value (intake wins)
        try:
            import logging as _l
            _l.getLogger().info(
                "S2_INJECT session=%s llm_passed=%r intake_goal=%r -> product_type=%r",
                (_session_id or "?")[:8], _llm_passed_pt, _intake_goal, product_type,
            )
        except Exception:
            pass
    elif tool_context is not None and _session_id is None:
        # Couldn't even get a session_id — degrade silently but log once so
        # the validation gate can detect this regression mode.
        try:
            import logging as _l
            _l.getLogger().warning("S2_INJECT_SESSION_ID_MISS — using LLM-passed product_type=%r", _llm_passed_pt)
        except Exception:
            pass
    if product_type is not None:
        payload["product_type"] = product_type
    # Debug — log what the LLM actually constructed for search args
    try:
        import logging as _l
        _l.getLogger().info(
            "SEARCH_PAYLOAD query=%r age=%s smoker=%s income=%s product_type=%r",
            query, customer_age, is_smoker, income, product_type,
        )
    except Exception:
        pass
    try:
        # Timeout bumped from 2.5s -> 8.0s on Day 6 (S5 finding):
        # CF cold-start + ELSER inference + RRF query routinely takes 3-5s.
        # 2.5s caused n_candidates=0 timeouts on first call, breaking the demo arc.
        resp = httpx.post(f"{ELASTIC_MCP_SERVER_URL}/search_products", json=payload, timeout=8.0)
        resp.raise_for_status()
        result = resp.json()
        # Stability C.5b — stash candidates in session state so compliance_check
        # can substitute them server-side, bypassing the LLM's broken arg
        # threading (flash-lite passes [null, null, null, null] otherwise).
        try:
            if tool_context is not None:
                tool_context.state["last_search_candidates"] = result.get("candidates", [])
        except Exception:
            pass
        return result
    except httpx.TimeoutException as exc:
        return {"candidates": [], "error": f"search_products timed out: {exc}"}
    except httpx.HTTPStatusError as exc:
        return {"candidates": [], "error": f"search_products HTTP {exc.response.status_code}"}
    except Exception as exc:
        return {"candidates": [], "error": f"search_products unavailable: {exc}"}

def compliance_check(
    candidates: list,
    customer_profile: dict,
    tool_context: ToolContext = None,
) -> dict:
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
    # Stability C.5b — IGNORE the LLM's `candidates` arg. flash-lite reliably
    # passes [null, null, null, null] instead of forwarding actual products.
    # Pull the real candidates from session state (stashed by search_products).
    real_candidates = []
    try:
        if tool_context is not None:
            real_candidates = tool_context.state.get("last_search_candidates", []) or []
    except Exception:
        real_candidates = []
    # Fallback: if session has nothing, filter out nulls from LLM's args
    if not real_candidates:
        real_candidates = [c for c in (candidates or []) if isinstance(c, dict)]

    # Auto-fix common LLM profile-shape errors (string vs list, missing fields)
    profile = dict(customer_profile or {})
    if isinstance(profile.get("coverage_goals"), str):
        profile["coverage_goals"] = [profile["coverage_goals"]]
    if "health_status" not in profile:
        profile["health_status"] = "healthy"
    if "smoker" not in profile and "is_smoker" in profile:
        profile["smoker"] = profile["is_smoker"]

    payload = {"candidate_products": real_candidates, "customer_profile": profile}
    try:
        resp = httpx.post(COMPLIANCE_CHECK_URL, json=payload, timeout=5.0)
        resp.raise_for_status()
        result = resp.json()
        # Stash passed[] for rank_products to substitute
        try:
            if tool_context is not None:
                tool_context.state["last_compliance_passed"] = result.get("passed", [])
        except Exception:
            pass
        return result
    except httpx.TimeoutException as exc:
        return {"passed": [], "rejected": [], "error": f"compliance_check timed out: {exc}"}
    except httpx.HTTPStatusError as exc:
        try:
            import logging as _l
            _l.getLogger().error(
                "COMPLIANCE_400_BODY status=%d body=%s",
                exc.response.status_code,
                exc.response.text[:500],
            )
        except Exception:
            pass
        return {"passed": [], "rejected": [], "error": f"compliance_check HTTP {exc.response.status_code}"}
    except Exception as exc:
        return {"passed": [], "rejected": [], "error": f"compliance_check unavailable: {exc}"}


def rank_products(
    eligible_candidates: list,
    customer_profile: dict,
    tool_context: ToolContext = None,
) -> dict:
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
    # Stability C.5b — substitute passed[] from session, ignore LLM args
    real_eligible = []
    try:
        if tool_context is not None:
            real_eligible = tool_context.state.get("last_compliance_passed", []) or []
    except Exception:
        real_eligible = []
    if not real_eligible:
        real_eligible = [c for c in (eligible_candidates or []) if isinstance(c, dict)]

    profile = dict(customer_profile or {})
    if isinstance(profile.get("coverage_goals"), str):
        profile["coverage_goals"] = [profile["coverage_goals"]]
    if "health_status" not in profile:
        profile["health_status"] = "healthy"

    payload = {"passed_products": real_eligible, "customer_profile": profile}
    try:
        resp = httpx.post(RANK_PRODUCTS_URL, json=payload, timeout=5.0)
        resp.raise_for_status()
        result = resp.json()
        # rank_products Cloud Function returns key "top3" (no underscore).
        # Normalize so the rest of the pipeline (main.py, callback, prompt)
        # can rely on either key being present.
        if "top3" in result and "top_3" not in result:
            result["top_3"] = result["top3"]
        elif "top_3" in result and "top3" not in result:
            result["top3"] = result["top_3"]
        try:
            if tool_context is not None:
                tool_context.state["last_rank_top3"] = result.get("top_3") or result.get("top3") or []
        except Exception:
            pass
        return result
    except httpx.TimeoutException:
        # Graceful fallback: return passed products ordered by elser_score (Constitution §IV)
        sorted_fallback = sorted(
            eligible_candidates,
            key=lambda p: p.get("elser_score", 0.0),
            reverse=True,
        )
        return {
            "top_3": [
                {"rank": i + 1, "product_id": p.get("product_id", p.get("id", "")),
                 "suitability_score": p.get("elser_score", 0.0),
                 "score_breakdown": {"elser_relevance": p.get("elser_score", 0.0)},
                 "explanation": p.get("name", "Product")}
                for i, p in enumerate(sorted_fallback[:3])
            ],
            "warning": "rank_products timed out; results ordered by ELSER score only",
        }
    except httpx.HTTPStatusError as exc:
        return {"top_3": [], "error": f"rank_products HTTP {exc.response.status_code}"}
    except Exception as exc:
        return {"top_3": [], "error": f"rank_products unavailable: {exc}"}


# ---------------------------------------------------------------------------
# Stability C.5 — Mid-pipeline tool-call enforcement (mode=ANY) via callback
# ---------------------------------------------------------------------------
# Without this callback, flash-lite probabilistically emits empty final
# responses after search_products returns candidates (AC-3 was 0/15 across
# C.4 model-bump and 0/4 across P.1 prompt-rewrite attempts). The callback
# inspects the most recent function_response in session history and forces
# tool_config.mode=ANY with allowed_function_names=[next_tool] when the
# pipeline is mid-flight. Mode=AUTO is preserved on first turn (so the LLM
# can choose: clarify vs search), after recommend_and_explain (so the LLM
# can emit the final verbatim text), and on out-of-scope/follow-up turns.
def _force_tool_call_mid_pipeline(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> None:
    events = getattr(callback_context, "_invocation_context", None)
    # Try multiple shapes — ADK API surface varies by version
    session_events = []
    try:
        session_events = callback_context._invocation_context.session.events or []
    except Exception:
        try:
            session_events = callback_context.session.events or []
        except Exception:
            session_events = []

    last_fr_name = None
    last_fr_payload = None
    for ev in reversed(session_events):
        content = getattr(ev, "content", None)
        if content and getattr(content, "parts", None):
            found = False
            for p in content.parts:
                fr = getattr(p, "function_response", None)
                if fr is not None:
                    last_fr_name = getattr(fr, "name", None)
                    last_fr_payload = getattr(fr, "response", None) or {}
                    found = True
                    break
            if found:
                break

    forced_tool = None
    if last_fr_name == "search_products":
        n_candidates = len((last_fr_payload or {}).get("candidates", []) or [])
        if n_candidates > 0:
            forced_tool = "compliance_check"
    elif last_fr_name == "compliance_check":
        n_passed = len((last_fr_payload or {}).get("passed", []) or [])
        if n_passed > 0:
            forced_tool = "rank_products"
    elif last_fr_name == "rank_products":
        _resp = last_fr_payload or {}
        n_top3 = len((_resp.get("top_3") or _resp.get("top3") or []))
        if n_top3 > 0:
            forced_tool = "recommend_and_explain"

    # Debug log so we can verify the callback is correctly identifying
    # the next forced tool. Emits before EVERY LLM call.
    try:
        import logging as _l
        _l.getLogger().error(
            "CALLBACK_DEBUG last_fr=%s n_events=%d forced=%s",
            last_fr_name,
            len(session_events or []),
            forced_tool,
        )
    except Exception:
        pass

    if forced_tool:
        try:
            if llm_request.config is None:
                llm_request.config = genai_types.GenerateContentConfig()
            llm_request.config.tool_config = genai_types.ToolConfig(
                function_calling_config=genai_types.FunctionCallingConfig(
                    mode="ANY",
                    allowed_function_names=[forced_tool],
                )
            )
        except Exception as _e:
            try:
                import logging as _l
                _l.getLogger().error("CALLBACK_FORCE_FAIL %s", _e)
            except Exception:
                pass


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
    # Stability C.1 — explicit sampling config to fix the ~40% pipeline-skip rate
    # observed at default temp=1.0 (LLM probabilistically skipped tool calls,
    # surfacing as empty top3 + hallucinated rejection text). temp=0.0 caused
    # silent paralysis when tested; 0.25 is the empirically safe midpoint.
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=0.25,
        top_p=0.7,
        max_output_tokens=800,
    ),
    # Stability C.5 — see _force_tool_call_mid_pipeline above
    before_model_callback=_force_tool_call_mid_pipeline,
    tools=[
        # ---------------------------------------------------------------
        # Tool 1: Search products — REST call to elastic-mcp-server
        # NOTE: Switched from MCPToolset (MCP-native /mcp) to plain REST FunctionTool.
        # MCP-native wraps responses as {content, structuredContent, isError}; the LLM
        # cannot extract `candidates` from that envelope, so compliance was always
        # called with empty list. REST returns {candidates: [...]} directly.
        # ---------------------------------------------------------------
        FunctionTool(search_products),

        # ---------------------------------------------------------------
        # Tool 2: Compliance check — deterministic rule engine (Cloud Function)
        # ---------------------------------------------------------------
        FunctionTool(compliance_check),

        # ---------------------------------------------------------------
        # Tool 3: Rank products — suitability scoring (Cloud Function)
        # ---------------------------------------------------------------
        FunctionTool(rank_products),

        # ---------------------------------------------------------------
        # Tool 4: Recommendation Explainer — Sub-Agent 3 (LlmAgent)
        # Receives top3 + customer profile summary; returns voice-ready
        # ≤120-word explanation in plain prose (no markdown, INR, WaveNet-safe).
        # ---------------------------------------------------------------
        AgentTool(
            agent=LlmAgent(
                model="gemini-2.5-flash",
                name="recommend_and_explain",
                description=(
                    "Generates a concise voice-ready recommendation explanation "
                    "from the top-3 ranked products and customer profile. "
                    "Output is plain prose, ≤120 words, WaveNet-safe, in INR."
                ),
                instruction=open(
                    os.path.join(os.path.dirname(__file__), "sub_agent3_explainer_prompt.md")
                ).read(),
                # Stability C.3 — explicit sampling config to reduce premium
                # hallucination (Bug 11: premiums change between turns) and
                # invented fields (Bug 13: hallucinated "minimum sum assured").
                # Default temp=1.0 caused stochastic voice-text variation; 0.3
                # is the empirical midpoint for natural-but-grounded prose.
                # max_output_tokens=400 ≈ 120 words for voice comfort.
                generate_content_config=genai_types.GenerateContentConfig(
                    temperature=0.3,
                    top_p=0.7,
                    max_output_tokens=400,
                ),
            )
        ),
    ],
)

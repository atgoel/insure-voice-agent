"""
InsureVoice — ADK Agent Definition
===================================
Defines the multi-agent pipeline using Google Agent Development Kit (ADK).

Architecture:
    root_agent (LlmAgent — gemini-2.5-flash-lite)
        │
        ├── FunctionTool: search_products → MCP tools/call @ $ELASTIC_MCP_SERVER_NATIVE_URL/mcp
        │     Invokes the Elastic Partner MCP server over the MCP protocol
        │     (Streamable HTTP JSON-RPC). A thin Python wrapper unwraps the MCP
        │     envelope (structuredContent.candidates) and threads candidates via
        │     session state (C.5b) — the LLM never sees the envelope, so flash-lite's
        │     envelope-extraction failure (the reason MCPToolset was dropped) is moot.
        │     Server: functions/elastic_mcp_server_native/main.py (FastMCP, stateless).
        │           ELSER v2 RRF hybrid query + elser_score injection.
        │
        ├── FunctionTool: compliance_check  → POST $COMPLIANCE_CHECK_URL
        │     Deterministic eligibility rule engine (Constitution §II)
        │
        └── FunctionTool: rank_products     → POST $RANK_PRODUCTS_URL
              Suitability scoring + top-3 ranking with audit trail

The MCP server is OUR Cloud Run service (functions/elastic_mcp_server_native/main.py).
It wraps the ELSER RRF query logic specific to InsureVoice (Constitution §VI) and
is the Partner-MCP integration point for the Devpost Elastic Partner Track —
invoked over the MCP protocol at runtime (verified live 2026-06-06).

Env vars required at runtime:
    ELASTIC_MCP_SERVER_NATIVE_URL — MCP-native Cloud Run URL (search_products via MCP)
    ELASTIC_MCP_SERVER_URL        — REST sidecar URL (service-probe / fallback only)
    COMPLIANCE_CHECK_URL          — Cloud Function URL
    RANK_PRODUCTS_URL             — Cloud Function URL
    SIMULATE_PREMIUM_URL          — Cloud Function URL (Story 6 — may be empty for local dev)
"""

import os
import json
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
SIMULATE_PREMIUM_URL          = os.environ.get("SIMULATE_PREMIUM_URL", "")   # optional — Story 6

# ---------------------------------------------------------------------------
# HTTP call helpers for compliance_check, rank_products, and search_products
#
# search_products now invokes the Elastic Partner MCP server over the MCP
# protocol (Streamable HTTP JSON-RPC `tools/call`) at $ELASTIC_MCP_SERVER_NATIVE_URL.
# This makes "integration using MCP" (Devpost Elastic Partner Track requirement)
# literally true at runtime — the agent calls the MCP server, not a REST sidecar.
#
# Why a Python wrapper instead of ADK MCPToolset: the MCP envelope wraps the
# tool result as {content, structuredContent, isError}; flash-lite cannot extract
# `candidates` from that envelope (it called compliance with an empty list). So
# Python owns the unwrap (structuredContent.candidates) and the session-state
# threading (C.5b), exactly as before — the LLM never sees the envelope. The
# server runs FastMCP stateless_http=True, so a single `tools/call` works with
# NO initialize handshake: one round-trip, latency-neutral with the old REST call
# (verified ~969ms live, 2026-06-06).
#
# compliance_check and rank_products remain plain REST Cloud Function calls.
# ---------------------------------------------------------------------------

_MCP_HEADERS = {"Accept": "application/json, text/event-stream",
                "Content-Type": "application/json"}


def _parse_mcp_response(resp: httpx.Response) -> dict:
    """Parse an MCP Streamable-HTTP reply.

    FastMCP answers `tools/call` as either a single JSON object or an SSE frame
    (`data: {...}`). Return the decoded JSON-RPC object either way.
    """
    text = resp.text
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype or text.lstrip().startswith("event:") or "data:" in text[:80]:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise ValueError("MCP SSE response had no data frame")
    return json.loads(text)


def _mcp_search_call(arguments: dict, timeout: float = 8.0) -> dict:
    """Invoke search_products on the Elastic MCP server via JSON-RPC tools/call.

    Returns the inner tool payload {"candidates", "total_hits", "fallback_triggered"}
    — the SAME shape the old REST endpoint returned — so all downstream handling
    (C.5b candidate stash, .get("candidates", [])) is unchanged. Raises on
    transport/JSON-RPC/parse error so the caller's existing except-blocks apply.
    """
    # stateless server (FastMCP stateless_http=True) → no initialize handshake.
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "search_products", "arguments": arguments}}
    resp = httpx.post(f"{ELASTIC_MCP_SERVER_NATIVE_URL}/mcp",
                      headers=_MCP_HEADERS, json=body, timeout=timeout)
    resp.raise_for_status()
    data = _parse_mcp_response(resp)
    if "error" in data:
        raise RuntimeError(f"MCP error: {data['error'].get('message', data['error'])}")
    result = data.get("result", {}) or {}
    if result.get("isError"):
        raise RuntimeError(f"MCP tool isError: {result.get('content')}")
    sc = result.get("structuredContent")
    if isinstance(sc, dict) and "candidates" in sc:
        return sc
    # Fallback: some FastMCP versions nest the dict under structuredContent.result,
    # or mirror it only in content[0].text — handle both so a server bump can't
    # silently break the demo.
    if isinstance(sc, dict) and isinstance(sc.get("result"), dict) and "candidates" in sc["result"]:
        return sc["result"]
    content = result.get("content")
    if isinstance(content, list) and content:
        inner = json.loads(content[0].get("text", "{}"))
        if "candidates" in inner:
            return inner
    raise RuntimeError(f"MCP response missing candidates; result keys={list(result.keys())}")


# ---------------------------------------------------------------------------
# (legacy note) The elastic-mcp-server also exposes a plain REST /search_products
# endpoint at $ELASTIC_MCP_SERVER_URL — retained for the service-probe layer and
# as a documented fallback, but NOT used by the agent at runtime.
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

    # S2'' — Mechanical family-floater query enrichment (2026-06-06, Issue 2).
    # Bug: "health insurance for my family of 4" returned an INDIVIDUAL plan.
    # Root cause: the `query` text is LLM-constructed and flash-lite drops the
    # "family" token (L-002), so ELSER can't distinguish MediCare Family Floater
    # from HealthFirst Individual and the individual plan wins on score noise.
    # Both are eligible candidates; the catalog HAS the family product — the
    # signal was just missing from the query. Mirror the S2' pattern: read
    # family_size from the validated profile (NOT the LLM) and append a
    # deterministic floater phrase so the ELSER semantic field (which contains
    # "family floater"/"entire family"/"maternity" for MediCare) ranks it first.
    # Guarded to health + family_size>1 so individual/term/ULIP flows are untouched.
    try:
        if _profile and (product_type == "health"):
            _fam = _profile.get("family_size")
            try:
                _fam = int(_fam) if _fam is not None else 0
            except (TypeError, ValueError):
                _fam = 0
            if _fam > 1:
                query = (
                    f"{query} family floater health plan covering the entire "
                    f"family of {_fam} members under a single sum insured"
                )
                payload["query"] = query
                import logging as _l
                _l.getLogger().info(
                    "S2_FAMILY_INJECT session=%s family_size=%d -> floater query enrichment",
                    (_session_id or "?")[:8], _fam,
                )
    except Exception:
        pass

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
        # Invoke the Elastic Partner MCP server over the MCP protocol (JSON-RPC
        # tools/call). Timeout 8.0s (Day 6 S5 finding): CF cold-start + ELSER
        # inference + RRF query routinely takes 3-5s; 2.5s caused n_candidates=0
        # timeouts on first call, breaking the demo arc. The MCP path is a single
        # round-trip (stateless server), latency-neutral with the old REST call.
        result = _mcp_search_call(payload, timeout=8.0)
        try:
            import logging as _l
            _l.getLogger().info(
                "SEARCH_VIA_MCP candidates=%d total_hits=%s",
                len(result.get("candidates", []) or []), result.get("total_hits"),
            )
        except Exception:
            pass

        # Issue 2 (2026-06-06) — HARD-EXCLUDE individual health plans for a
        # family request. The S2_FAMILY_INJECT query enrichment above only
        # *re-ranks* the floater higher; an individual plan could still appear
        # in the candidate set and surface in cards/voice. User decision:
        # exclude, not down-rank. We cut at search egress so the exclusion
        # propagates to compliance, rank, cards AND voice in one place.
        #
        # Discriminator: tags contain "individual" but NOT "floater"/"family".
        # Guarded to health + family_size>1 (term/ulip/individual flows untouched).
        #
        # DEMO-SAFETY NET: never filter to zero. The catalog currently has only
        # ONE family-floater health product, so a young family may legitimately
        # have just 1 eligible plan. If excluding individual would empty the
        # pool, KEEP the original set (showing something beats a blank screen)
        # and log it. This interim sparseness is resolved by the separate
        # catalog-widening task, not by this filter.
        try:
            if _profile and (product_type == "health"):
                _fam_excl = _profile.get("family_size")
                try:
                    _fam_excl = int(_fam_excl) if _fam_excl is not None else 0
                except (TypeError, ValueError):
                    _fam_excl = 0
                _cands = result.get("candidates", []) or []
                if _fam_excl > 1 and _cands:
                    def _is_individual_only(_c):
                        if not isinstance(_c, dict):
                            return False
                        _t = _c.get("tags") or []
                        _tl = " ".join(_t).lower() if isinstance(_t, list) else str(_t).lower()
                        return ("individual" in _tl) and not ("floater" in _tl or "family" in _tl)
                    _kept = [c for c in _cands if not _is_individual_only(c)]
                    import logging as _l
                    if _kept and len(_kept) < len(_cands):
                        result["candidates"] = _kept
                        _l.getLogger().info(
                            "S2_FAMILY_EXCLUDE session=%s family_size=%d dropped=%d kept=%d",
                            (_session_id or "?")[:8], _fam_excl,
                            len(_cands) - len(_kept), len(_kept),
                        )
                    elif _kept != _cands and not _kept:
                        # Excluding would empty the pool — keep original, log the net.
                        _l.getLogger().info(
                            "S2_FAMILY_EXCLUDE_SKIPPED session=%s family_size=%d "
                            "reason=would_empty_pool candidates=%d (catalog gap — widen later)",
                            (_session_id or "?")[:8], _fam_excl, len(_cands),
                        )
        except Exception:
            pass

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
        return {"candidates": [], "error": f"search_products (MCP) timed out: {exc}"}
    except httpx.HTTPStatusError as exc:
        return {"candidates": [], "error": f"search_products (MCP) HTTP {exc.response.status_code}"}
    except Exception as exc:
        return {"candidates": [], "error": f"search_products (MCP) unavailable: {exc}"}

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
# Story 6 — Premium simulation (deterministic, no LLM)
# Proxies to the simulate_premium Cloud Function which calculates premiums
# using the actuarial formula: FV annuity + age/smoker loadings from catalog.
# Constitution §II: agent must NEVER compute or infer premium figures itself.
# ---------------------------------------------------------------------------

def simulate_premium(
    product_id: str,
    sum_assured: int,
    customer_age: int,
    is_smoker: bool,
    premium_frequency: str,
    policy_term: int,
    tool_context: ToolContext = None,
) -> dict:
    """Calculate deterministic premium and projected returns for an insurance product.

    Args:
        product_id: Product catalog ID (e.g. "ULIP001", "TERM002").
        sum_assured: Cover amount in INR (minimum 100000).
        customer_age: Customer's age in years.
        is_smoker: Whether the customer is a smoker.
        premium_frequency: Payment frequency — "monthly", "quarterly",
            "semi_annual", or "annual".
        policy_term: Policy duration in years (must be in product's
            available_terms list in the catalog).

    Returns:
        {
            "product_id": str,
            "product_name": str,
            "product_type": str,
            "period_premium": float,
            "annual_premium": float,
            "total_premium_outflow": float,
            "projected_maturity_value": float | None,  # None for protection products
            "net_gain": float | None,                   # None for protection products
            "simulation_inputs": dict,
            "formula_breakdown": dict
        }
        On error: {"error": "<message>"}
    """
    if not SIMULATE_PREMIUM_URL:
        return {"error": "simulate_premium service not configured (SIMULATE_PREMIUM_URL not set)"}
    payload = {
        "product_id": product_id,
        "sum_assured": sum_assured,
        "customer_age": customer_age,
        "is_smoker": is_smoker,
        "premium_frequency": premium_frequency,
        "policy_term": policy_term,
    }
    try:
        resp = httpx.post(SIMULATE_PREMIUM_URL, json=payload, timeout=5.0)
        resp.raise_for_status()
        return resp.json()
    except httpx.TimeoutException as exc:
        return {"error": f"simulate_premium timed out: {exc}"}
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json()
        except Exception:
            detail = {}
        return {
            "error": f"simulate_premium HTTP {exc.response.status_code}",
            "validation_errors": detail.get("validation_errors", []),
        }
    except Exception as exc:
        return {"error": f"simulate_premium unavailable: {exc}"}


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
    # Try multiple shapes — ADK API surface varies by version
    inv_ctx = getattr(callback_context, "_invocation_context", None)
    session_events = []
    try:
        session_events = inv_ctx.session.events or []
    except Exception:
        try:
            session_events = callback_context.session.events or []
        except Exception:
            session_events = []

    # LOOP FIX (2026-06-06): scope the scan to the CURRENT invocation only.
    # session.events accumulates across ALL turns in this deployment (in-memory
    # sessions, --max-instances=1), so a PRIOR turn's rank_products / search
    # function_response would otherwise re-arm forcing on a later turn and the
    # ADK run_async `while True` (terminates only on a pure-text final response)
    # never breaks. Filter to this invocation_id so stale FRs can't re-trigger.
    cur_inv_id = getattr(inv_ctx, "invocation_id", None)
    if cur_inv_id is not None:
        session_events = [
            ev for ev in session_events
            if getattr(ev, "invocation_id", cur_inv_id) == cur_inv_id
        ]

    # LATCH (2026-06-06): once recommend_and_explain has produced output in THIS
    # invocation, the pipeline is DONE. Never force again — let the model emit
    # the final verbatim text so run_async can terminate. Without this, flash-lite
    # re-emits a pipeline tool call after the recommendation (prompt "STOP" does
    # not bind a small model — lesson L-001), the fresh FR re-matches a forcing
    # branch, and the agent loops forever (the 72.9s recommendation-turn bug).
    pipeline_done = False
    last_fr_name = None
    last_fr_payload = None
    for ev in reversed(session_events):
        content = getattr(ev, "content", None)
        if content and getattr(content, "parts", None):
            for p in content.parts:
                fr = getattr(p, "function_response", None)
                if fr is not None:
                    nm = getattr(fr, "name", None)
                    if nm == "recommend_and_explain":
                        pipeline_done = True
                    if last_fr_name is None:
                        last_fr_name = nm
                        last_fr_payload = getattr(fr, "response", None) or {}
            if pipeline_done:
                break

    forced_tool = None
    if pipeline_done:
        forced_tool = None  # explicit: pipeline complete → release the force
    elif last_fr_name == "search_products":
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
            "CALLBACK_DEBUG last_fr=%s n_events=%d pipeline_done=%s forced=%s",
            last_fr_name,
            len(session_events or []),
            pipeline_done,
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
        # Tool 1: Search products — invokes the Elastic Partner MCP server over
        # the MCP protocol (JSON-RPC tools/call). Wrapped as a FunctionTool (not
        # ADK MCPToolset) so Python unwraps the {content, structuredContent,
        # isError} envelope and threads candidates via session state — the LLM
        # never sees the envelope it cannot parse. See _mcp_search_call above.
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
        # Tool 3b: Premium Simulation — Story 6 (deterministic Cloud Function)
        # Calculates actuarial premiums + projected maturity value from catalog.
        # Constitution §II: agent MUST call this tool; MUST NOT compute figures.
        # ---------------------------------------------------------------
        FunctionTool(simulate_premium),

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

"""
T1 unit tests — Bug L deduplication logic (identical card re-renders).

Validates:
  - First /invoke turn with recommendations returns top3 cards and normal text.
  - Second /invoke turn with identical recommendations suppresses the top3 cards
    and returns the friendly text override.
  - Third /invoke turn with bypass words ("show me again") bypasses deduplication
    and renders the cards freshly.
  - A turn with different recommendations renders freshly.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "agent_builder"))

# Set default env vars for test environment
os.environ.setdefault("ELASTIC_MCP_SERVER_URL", "http://mock-elastic-mcp.test/search")
os.environ.setdefault("ELASTIC_MCP_SERVER_NATIVE_URL", "http://mock-elastic-mcp-native.test/search")
os.environ.setdefault("COMPLIANCE_CHECK_URL", "http://mock-compliance.test/check")
os.environ.setdefault("RANK_PRODUCTS_URL", "http://mock-rank.test/rank")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")
os.environ.setdefault("GOOGLE_API_KEY", "stub-key-for-tests")

import main as _main
import shared_state as _ss


# ---------------------------------------------------------------------------
# Canned mock responses
# ---------------------------------------------------------------------------

PRODS_A = [
    {
        "product_id": "TERM001",
        "id": "TERM001",
        "name": "LifeGuard Plus",
        "product_type": "term_life",
        "min_age": 18,
        "max_age": 65,
        "premium_min_monthly": 800,
        "premium_max_monthly": 5000,
        "elser_score": 12.0,
    },
    {
        "product_id": "TERM002",
        "id": "TERM002",
        "name": "Future Secure Term",
        "product_type": "term_life",
        "min_age": 18,
        "max_age": 65,
        "premium_min_monthly": 600,
        "premium_max_monthly": 4000,
        "elser_score": 9.5,
    }
]

PRODS_B = [
    {
        "product_id": "HEALTH001",
        "id": "HEALTH001",
        "name": "HealthFirst Premium",
        "product_type": "health",
        "min_age": 18,
        "max_age": 65,
        "premium_min_monthly": 1000,
        "premium_max_monthly": 6000,
        "elser_score": 11.0,
    }
]


def _mock_post_factory(products):
    """Return mock httpx post routing factory for search/comply/rank."""
    def _side_effect(url, *args, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        url_lower = (url or "").lower()
        if "compliance" in url_lower:
            resp.json.return_value = {
                "passed": products,
                "rejected": []
            }
        elif "rank" in url_lower:
            resp.json.return_value = {
                "top_3": [
                    {"rank": i + 1, "product_id": p["product_id"], "product": p, "suitability_score": 0.9}
                    for i, p in enumerate(products)
                ]
            }
        else:
            resp.json.return_value = {
                "candidates": products
            }
        return resp
    return _side_effect


async def _empty_async_iter(*args, **kwargs):
    """Bypasses LLM runner by returning an empty async generator."""
    if False:
        yield


def _reset_session_state(session_id):
    """Wipe all per-session state for clean testing."""
    _ss.PROFILE_BY_SESSION.pop(session_id, None)
    _ss.TOP3_BY_SESSION.pop(session_id, None)
    _ss.CONTACT_BY_SESSION.pop(session_id, None)
    _ss.LAST_RENDERED_BY_SESSION.pop(session_id, None)
    _main._INTAKE_BY_SESSION.pop(session_id, None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_dedup_flow_and_bypass():
    """
    Test deduplication flow end-to-end:
      1. First call: returns top3 cards
      2. Second identical call: suppresses top3 cards, returns dedup message
      3. Third call with 'show me again': bypasses dedup, returns top3 cards freshly
      4. Fourth call with different products: returns top3 cards freshly
    """
    client = TestClient(_main.app)
    session_id = "test-dedup-endpoint-session"
    _reset_session_state(session_id)

    # Pre-populate intake state to skip intake turns
    _main._INTAKE_BY_SESSION[session_id] = {
        "complete": True,
        "profile": {
            "name": "Abhi",
            "age": 30,
            "smoker": False,
            "income": 2500000,
            "health_status": "healthy",
            "family_size": 4,
            "coverage_goals": ["term_life"],
            "sum_assured": 10000000,
        }
    }

    # Patch run_async to bypass LLM and route to programmatic fallback
    with patch.object(_main._runner, "run_async", side_effect=_empty_async_iter):

        # --- Turn 1: Initial Render ---
        with patch("agent_definition.httpx.post", side_effect=_mock_post_factory(PRODS_A)):
            r1 = client.post("/invoke", json={"message": "show my recommendations", "session_id": session_id})
            assert r1.status_code == 200
            resp1 = r1.json()
            assert "top3" in resp1
            assert len(resp1["top3"]) == 2
            assert resp1["top3"][0]["product_id"] == "TERM001"
            assert resp1["top3"][1]["product_id"] == "TERM002"
            assert "email these to you" in resp1["response"].lower() # T3 contact prompt appended

        # --- Turn 2: Duplicate Suppression ---
        with patch("agent_definition.httpx.post", side_effect=_mock_post_factory(PRODS_A)):
            # Turn contact FSM state to NONE first so it doesn't try to answer "show recommendations" as a contact input
            _ss.CONTACT_BY_SESSION[session_id] = {"state": "NONE", "email": None, "invalid_attempts": 0}

            r2 = client.post("/invoke", json={"message": "show my recommendations", "session_id": session_id})
            assert r2.status_code == 200
            resp2 = r2.json()
            assert "top3" not in resp2
            assert "rejected" not in resp2
            assert "already shown you" in resp2["response"]

        # --- Turn 3: "Show me again" Bypass ---
        with patch("agent_definition.httpx.post", side_effect=_mock_post_factory(PRODS_A)):
            _ss.CONTACT_BY_SESSION[session_id] = {"state": "NONE", "email": None, "invalid_attempts": 0}

            r3 = client.post("/invoke", json={"message": "please show me again", "session_id": session_id})
            assert r3.status_code == 200
            resp3 = r3.json()
            assert "top3" in resp3
            assert len(resp3["top3"]) == 2
            assert resp3["top3"][0]["product_id"] == "TERM001"

        # --- Turn 4: Different Products Render freshly ---
        _main._INTAKE_BY_SESSION[session_id]["profile"]["coverage_goals"] = ["health"]
        with patch("agent_definition.httpx.post", side_effect=_mock_post_factory(PRODS_B)):
            _ss.CONTACT_BY_SESSION[session_id] = {"state": "NONE", "email": None, "invalid_attempts": 0}

            r4 = client.post("/invoke", json={"message": "show my recommendations", "session_id": session_id})
            assert r4.status_code == 200
            resp4 = r4.json()
            assert "top3" in resp4
            assert len(resp4["top3"]) == 1
            assert resp4["top3"][0]["product_id"] == "HEALTH001"

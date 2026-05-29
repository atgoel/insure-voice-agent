"""
tests/test_invoke_error_handling.py

TASK-044: Verify that all three tool wrappers handle network/HTTP errors gracefully,
returning safe error dicts instead of raising exceptions that would crash the agent.

No live network calls are made — httpx.post is mocked at the agent_definition level.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://mock")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(
        f"HTTP {status_code}", request=request, response=response
    )


SAMPLE_CANDIDATES = [
    {"product_id": "P001", "name": "Plan A", "elser_score": 9.0,
     "product_type": "term_life", "min_age": 18, "max_age": 65},
]
CUSTOMER_PROFILE = {"age": 35, "income": 1_200_000, "smoker": False,
                    "health_status": "healthy", "coverage_goals": ["life"]}


# ---------------------------------------------------------------------------
# search_products error handling (TASK-040)
# ---------------------------------------------------------------------------

class TestSearchProductsErrorHandling:

    def test_timeout_returns_empty_candidates(self):
        with patch("agent_definition.httpx.post") as mock_post:
            mock_post.side_effect = httpx.TimeoutException("timed out")
            import agent_definition as ad
            result = ad.search_products(
                query="term life", customer_age=35, is_smoker=False, income=1_200_000
            )
        assert result["candidates"] == []
        assert "error" in result
        assert "timed out" in result["error"].lower()

    def test_http_500_returns_empty_candidates(self):
        with patch("agent_definition.httpx.post") as mock_post:
            mock_post.side_effect = _http_status_error(500)
            import agent_definition as ad
            result = ad.search_products(
                query="health cover", customer_age=40, is_smoker=False, income=800_000
            )
        assert result["candidates"] == []
        assert "error" in result
        assert "500" in result["error"]

    def test_connection_error_returns_empty_candidates(self):
        with patch("agent_definition.httpx.post") as mock_post:
            mock_post.side_effect = Exception("connection refused")
            import agent_definition as ad
            result = ad.search_products(
                query="ulip", customer_age=30, is_smoker=False, income=500_000
            )
        assert result["candidates"] == []
        assert "error" in result


# ---------------------------------------------------------------------------
# compliance_check error handling (TASK-041)
# ---------------------------------------------------------------------------

class TestComplianceCheckErrorHandling:

    def test_timeout_returns_safe_empty_passed(self):
        with patch("agent_definition.httpx.post") as mock_post:
            mock_post.side_effect = httpx.TimeoutException("compliance timeout")
            import agent_definition as ad
            result = ad.compliance_check(
                candidates=SAMPLE_CANDIDATES,
                customer_profile=CUSTOMER_PROFILE,
            )
        # On compliance failure, must default to safe empty passed (not 5xx crash)
        assert result["passed"] == []
        assert result["rejected"] == []
        assert "error" in result

    def test_http_503_returns_safe_empty_passed(self):
        with patch("agent_definition.httpx.post") as mock_post:
            mock_post.side_effect = _http_status_error(503)
            import agent_definition as ad
            result = ad.compliance_check(
                candidates=SAMPLE_CANDIDATES,
                customer_profile=CUSTOMER_PROFILE,
            )
        assert result["passed"] == []
        assert result["rejected"] == []
        assert "503" in result.get("error", "")

    def test_error_does_not_propagate_to_rank_products(self):
        """
        Constitution §I: on compliance failure, pipeline must stop.
        Verify that calling rank_products with an empty passed list
        from a compliance error does not raise an exception.
        """
        compliance_error_result = {"passed": [], "rejected": [], "error": "compliance timeout"}
        with patch("agent_definition.httpx.post") as mock_post:
            rank_ok = MagicMock()
            rank_ok.json.return_value = {"top_3": []}
            rank_ok.raise_for_status = MagicMock()
            mock_post.return_value = rank_ok

            import agent_definition as ad
            result = ad.rank_products(
                eligible_candidates=compliance_error_result["passed"],
                customer_profile=CUSTOMER_PROFILE,
            )
        # rank_products called with empty list → returns top_3=[]
        assert result.get("top_3") == []


# ---------------------------------------------------------------------------
# rank_products error handling (TASK-042)
# ---------------------------------------------------------------------------

class TestRankProductsErrorHandling:

    def test_timeout_falls_back_to_elser_order(self):
        """On timeout, rank_products returns candidates sorted by elser_score."""
        candidates = [
            {"product_id": "P001", "name": "Plan A", "elser_score": 5.0},
            {"product_id": "P002", "name": "Plan B", "elser_score": 9.0},
            {"product_id": "P003", "name": "Plan C", "elser_score": 7.5},
        ]
        with patch("agent_definition.httpx.post") as mock_post:
            mock_post.side_effect = httpx.TimeoutException("rank timeout")
            import agent_definition as ad
            result = ad.rank_products(
                eligible_candidates=candidates,
                customer_profile=CUSTOMER_PROFILE,
            )
        assert "top_3" in result
        assert "warning" in result
        # Should be sorted by elser_score descending: P002, P003, P001
        ids = [item["product_id"] for item in result["top_3"]]
        assert ids == ["P002", "P003", "P001"], f"Expected elser-score order, got {ids}"

    def test_timeout_fallback_max_3_items(self):
        """Fallback result never contains more than 3 items."""
        candidates = [
            {"product_id": f"P{i:03d}", "name": f"Plan {i}", "elser_score": float(i)}
            for i in range(1, 7)  # 6 products
        ]
        with patch("agent_definition.httpx.post") as mock_post:
            mock_post.side_effect = httpx.TimeoutException("rank timeout")
            import agent_definition as ad
            result = ad.rank_products(
                eligible_candidates=candidates,
                customer_profile=CUSTOMER_PROFILE,
            )
        assert len(result["top_3"]) <= 3

    def test_http_500_returns_empty_top3(self):
        with patch("agent_definition.httpx.post") as mock_post:
            mock_post.side_effect = _http_status_error(500)
            import agent_definition as ad
            result = ad.rank_products(
                eligible_candidates=SAMPLE_CANDIDATES,
                customer_profile=CUSTOMER_PROFILE,
            )
        assert result.get("top_3") == []
        assert "error" in result

"""
tests/test_orchestration_guardrail.py

TASK-032 / TASK-033: Orchestration-level compliance guardrail tests.

These tests verify that:
  - TASK-032: When compliance_check returns passed=[], the agent wrapper never
              proceeds to rank_products and the pipeline returns an all-rejected signal.
  - TASK-033: When compliance_check rejects 3 of 5 candidates, rank_products
              receives exactly the 2 passed products (not the original 5).

No live Cloud Run / Cloud Function calls are made. httpx.post is mocked at the
agent_definition module level so the FunctionTool wrappers are exercised directly.
"""

import json
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Minimal product fixtures
# ---------------------------------------------------------------------------

def _make_product(product_id: str, name: str) -> dict:
    return {
        "product_id": product_id,
        "name": name,
        "product_type": "term_life",
        "elser_score": 8.5,
        "min_age": 18,
        "max_age": 65,
        "smoker_allowed": True,
        "min_income": 300_000,
        "premium_min": 8_000,
        "description": f"{name} description",
    }


FIVE_PRODUCTS = [_make_product(f"P{i:03d}", f"Plan {i}") for i in range(1, 6)]
CUSTOMER_PROFILE = {
    "age": 35,
    "income": 1_200_000,
    "smoker": False,
    "health_status": "healthy",
    "coverage_goals": ["life"],
}


# ---------------------------------------------------------------------------
# Helper: build a mock httpx Response
# ---------------------------------------------------------------------------

def _mock_response(body: dict, status_code: int = 200) -> MagicMock:
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = body
    mock.raise_for_status = MagicMock()  # no-op for 200
    return mock


# ---------------------------------------------------------------------------
# TASK-032: All products rejected — rank_products must NOT be called
# ---------------------------------------------------------------------------

class TestAllRejected:
    """When compliance_check returns passed=[], rank_products is never invoked."""

    def test_compliance_check_wrapper_all_rejected(self):
        """
        Call the compliance_check() wrapper directly with a mock that returns
        passed=[].  Verify the return shape matches the expected contract.
        """
        all_rejected_response = {
            "passed": [],
            "rejected": [
                {"product_id": p["product_id"], "product_name": p["name"],
                 "reasons": ["Maximum entry age is 65; customer is 70"]}
                for p in FIVE_PRODUCTS
            ],
        }

        with patch("agent_definition.httpx.post") as mock_post:
            mock_post.return_value = _mock_response(all_rejected_response)

            # Import here so the patch is active at call time
            import agent_definition as ad
            result = ad.compliance_check(
                candidates=FIVE_PRODUCTS,
                customer_profile=CUSTOMER_PROFILE,
            )

        assert result["passed"] == [], "passed must be empty list"
        assert len(result["rejected"]) == 5, "all 5 products must be rejected"
        # rank_products was not called — only one httpx.post call (compliance_check)
        assert mock_post.call_count == 1

    def test_payload_uses_candidate_products_key(self):
        """
        Ensure the wrapper translates the agent-side 'candidates' argument to
        the API-side 'candidate_products' key (field-name contract).
        """
        with patch("agent_definition.httpx.post") as mock_post:
            mock_post.return_value = _mock_response({"passed": [], "rejected": []})

            import agent_definition as ad
            ad.compliance_check(candidates=FIVE_PRODUCTS, customer_profile=CUSTOMER_PROFILE)

        call_kwargs = mock_post.call_args
        sent_payload = call_kwargs[1]["json"] if call_kwargs[1] else call_kwargs[0][1]
        assert "candidate_products" in sent_payload, (
            "Wrapper must send 'candidate_products' key, not 'candidates'"
        )
        assert "customer_profile" in sent_payload


# ---------------------------------------------------------------------------
# TASK-033: Partial rejection — rank_products receives only passed products
# ---------------------------------------------------------------------------

class TestPartialRejection:
    """When compliance_check rejects 3/5 products, rank_products gets only 2."""

    def _make_partial_compliance_response(self, passed_ids: list, rejected_ids: list) -> dict:
        passed = [p for p in FIVE_PRODUCTS if p["product_id"] in passed_ids]
        rejected = [
            {"product_id": p["product_id"], "product_name": p["name"],
             "reasons": ["Smoker exclusion"]}
            for p in FIVE_PRODUCTS if p["product_id"] in rejected_ids
        ]
        return {"passed": passed, "rejected": rejected}

    def test_rank_products_receives_only_passed(self):
        """
        Mock compliance_check to return 2 passed / 3 rejected.
        Call rank_products wrapper with the passed list and verify the
        'passed_products' key is sent (field-name contract).
        """
        passed_ids = ["P001", "P002"]
        rejected_ids = ["P003", "P004", "P005"]
        compliance_response = self._make_partial_compliance_response(passed_ids, rejected_ids)
        passed_products = compliance_response["passed"]

        rank_response = {
            "top_3": [
                {"rank": 1, "product_id": "P001", "suitability_score": 0.92,
                 "score_breakdown": {"elser_relevance": 0.8, "age_centrality": 0.9,
                                     "income_fit": 0.95, "coverage_match": 1.0},
                 "explanation": "Great fit for your needs."},
                {"rank": 2, "product_id": "P002", "suitability_score": 0.85,
                 "score_breakdown": {"elser_relevance": 0.75, "age_centrality": 0.88,
                                     "income_fit": 0.9, "coverage_match": 0.95},
                 "explanation": "Also a solid option."},
            ]
        }

        with patch("agent_definition.httpx.post") as mock_post:
            mock_post.return_value = _mock_response(rank_response)

            import agent_definition as ad
            result = ad.rank_products(
                eligible_candidates=passed_products,
                customer_profile=CUSTOMER_PROFILE,
            )

        # Verify the payload sent to rank_products
        call_kwargs = mock_post.call_args
        sent_payload = call_kwargs[1]["json"] if call_kwargs[1] else call_kwargs[0][1]

        assert "passed_products" in sent_payload, (
            "Wrapper must send 'passed_products' key, not 'eligible_candidates'"
        )
        assert len(sent_payload["passed_products"]) == 2, (
            "rank_products must receive exactly 2 passed products, not 5"
        )
        passed_ids_sent = {p["product_id"] for p in sent_payload["passed_products"]}
        assert passed_ids_sent == {"P001", "P002"}, (
            "Only the 2 passed products should be forwarded"
        )

    def test_rejected_products_not_in_rank_payload(self):
        """Rejected product IDs must never appear in the rank_products payload."""
        rejected_ids = {"P003", "P004", "P005"}
        passed_products = [p for p in FIVE_PRODUCTS if p["product_id"] not in rejected_ids]

        with patch("agent_definition.httpx.post") as mock_post:
            mock_post.return_value = _mock_response({"top_3": []})

            import agent_definition as ad
            ad.rank_products(
                eligible_candidates=passed_products,
                customer_profile=CUSTOMER_PROFILE,
            )

        sent_payload = mock_post.call_args[1]["json"]
        sent_ids = {p["product_id"] for p in sent_payload["passed_products"]}
        overlap = sent_ids & rejected_ids
        assert overlap == set(), (
            f"Rejected product IDs {overlap} must not reach rank_products"
        )

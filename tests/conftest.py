"""
Shared pytest fixtures for the InsureVoice test suite.

Fixtures:
    sample_customer_profile       — a complete valid customer profile dict
    sample_products               — 3 real products from the catalog (no elser_score)
    sample_candidate_products     — same 3 products with elser_score values 12.0, 7.5, 3.0
    passed_products_fixture       — alias for sample_candidate_products (all passed compliance)
    rejected_products_fixture     — 1-item list with rejection metadata shape
"""

import os
import sys
import pytest

# Define default environment variables for testing so importing agent_definition/main succeeds.
os.environ.setdefault("ELASTIC_MCP_SERVER_URL", "http://mock-elastic-mcp.test/search")
os.environ.setdefault("ELASTIC_MCP_SERVER_NATIVE_URL", "http://mock-elastic-mcp-native.test/search")
os.environ.setdefault("COMPLIANCE_CHECK_URL", "http://mock-compliance.test/check")
os.environ.setdefault("RANK_PRODUCTS_URL", "http://mock-rank.test/rank")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")
os.environ.setdefault("GOOGLE_API_KEY", "stub-key-for-tests")



@pytest.fixture
def sample_customer_profile():
    """A complete, valid customer profile dict with all optional fields set."""
    return {
        "age": 35,
        "income": 1_200_000,  # ₹12 lakh p.a.
        "smoker": False,
        "health_status": "healthy",
        "sum_need": 10_000_000,  # ₹1 crore
    }


# ---------------------------------------------------------------------------
# Base product data (matches real catalog entries for TERM001–TERM003)
# ---------------------------------------------------------------------------

_TERM001 = {
    "id": "TERM001",
    "name": "Future Secure Term Plan",
    "product_type": "term_life",
    "plan_category": "Protection",
    "description": "Pure term protection plan with high sum assured at affordable premiums.",
    "key_feature": "Return of premium option available; critical illness rider add-on",
    "tags": ["term", "protection", "affordable"],
    "sales_pitch": "Protect your family's future from just ₹500 per month.",
    "min_age": 18,
    "max_age": 65,
    "smoker_eligible": False,
    "min_income": 300_000,
    "max_sum_assured": 50_000_000,
    "medical_required_above": 10_000_000,
    "premium_min_monthly": 500,
    "premium_max_monthly": 3_000,
    "is_active": True,
}

_TERM002 = {
    "id": "TERM002",
    "name": "LifeGuard Plus Term",
    "product_type": "term_life",
    "plan_category": "Protection",
    "description": "Flexible term plan with smoker eligibility and enhanced riders.",
    "key_feature": "Accidental death benefit; waiver of premium on disability",
    "tags": ["term", "smoker-friendly", "riders"],
    "sales_pitch": "Comprehensive coverage that never lets you down.",
    "min_age": 25,
    "max_age": 55,
    "smoker_eligible": True,
    "min_income": 300_000,
    "max_sum_assured": 30_000_000,
    "medical_required_above": 7_500_000,
    "premium_min_monthly": 800,
    "premium_max_monthly": 5_000,
    "is_active": True,
}

_TERM003 = {
    "id": "TERM003",
    "name": "FamilyProtect 3 Crore",
    "product_type": "term_life",
    "plan_category": "Protection",
    "description": "High-cover family protection plan for breadwinners.",
    "key_feature": "Covers up to 3 crore; no medical required below 50 lakh sum assured",
    "tags": ["term", "family", "high-cover"],
    "sales_pitch": "Give your family 3 crore of certainty.",
    "min_age": 18,
    "max_age": 75,
    "smoker_eligible": True,
    "min_income": 500_000,
    "max_sum_assured": 30_000_000,
    "medical_required_above": 5_000_000,
    "premium_min_monthly": 1_200,
    "premium_max_monthly": 8_000,
    "is_active": True,
}


@pytest.fixture
def sample_products():
    """3 real catalog products without elser_score (pre-search stage)."""
    return [dict(_TERM001), dict(_TERM002), dict(_TERM003)]


@pytest.fixture
def sample_candidate_products():
    """Same 3 products with elser_score values descending: 12.0, 7.5, 3.0.

    Represents the output of elastic_product_search — ready for compliance_check.
    """
    p1 = dict(_TERM001)
    p1["elser_score"] = 12.0

    p2 = dict(_TERM002)
    p2["elser_score"] = 7.5

    p3 = dict(_TERM003)
    p3["elser_score"] = 3.0

    return [p1, p2, p3]


@pytest.fixture
def passed_products_fixture(sample_candidate_products):
    """All 3 candidate products assumed to have passed compliance.

    Alias for sample_candidate_products — used as input to rank_products.
    """
    return list(sample_candidate_products)


@pytest.fixture
def rejected_products_fixture():
    """1-item list in the shape returned by compliance_check for rejected products.

    Contains only product_id, product_name, reasons — not the full product object.
    """
    return [
        {
            "product_id": "TERM_SMOKER_ONLY",
            "product_name": "SmokerShield Term",
            "reasons": ["AGE_MIN: customer age 17 below minimum 18"],
        }
    ]

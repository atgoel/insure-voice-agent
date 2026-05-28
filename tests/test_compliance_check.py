"""
tests/test_compliance_check.py

TASK-032: Tests for functions/compliance_check/main.py.

Coverage (9 test cases as specified):
  Rule tests (5):
    1. AGE_MIN    — product min_age > customer age → rejected
    2. AGE_MAX    — product max_age < customer age → rejected
    3. SMOKER_EXCLUSION — smoker customer + smoker_eligible=False → rejected
    4. INCOME_SUM_CAP   — sum_need > income×10 → rejected
    5. MEDICAL_EXAM_REQUIRED — large sum_need + pre_existing → rejected

  Validation tests (3):
    6. Missing age in customer_profile → HTTP 400
    7. Missing customer_profile entirely → HTTP 400
    8. Empty candidate_products list → HTTP 200 with passed=[], rejected=[]

  sum_need guard (1) [TASK-030]:
    9. Profile without sum_need key passes both INCOME_SUM_CAP and MEDICAL_EXAM_REQUIRED

All tests use a direct-call approach (call the handler function with a mock request)
to avoid standing up a real HTTP server.
"""
import json
import sys
import types
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub out functions_framework so we can import the Cloud Function without
# the runtime dependency being installed in the test environment.
# ---------------------------------------------------------------------------
_ff = types.ModuleType("functions_framework")
_ff.http = lambda fn: fn   # no-op decorator
sys.modules.setdefault("functions_framework", _ff)

# ---------------------------------------------------------------------------
# Import the Cloud Function under test
# ---------------------------------------------------------------------------
import pathlib
ROOT = pathlib.Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from functions.compliance_check.main import compliance_check  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(body: dict) -> MagicMock:
    req = MagicMock()
    req.get_json.return_value = body
    return req


def _call(body: dict):
    """Call the Cloud Function and return (parsed_body, status_code)."""
    response = compliance_check(_make_request(body))
    body_str, status, _ = response
    return json.loads(body_str), status


def _product(**overrides) -> dict:
    """Return a minimal valid InsuranceProduct dict with optional overrides."""
    base = {
        "id":                    "TERM001",
        "name":                  "SecureLife Term Plan",
        "min_age":               18,
        "max_age":               65,
        "smoker_eligible":       True,
        "min_income":            300_000,
        "max_sum_assured":       10_000_000,
        "medical_required_above": 5_000_000,
    }
    base.update(overrides)
    return base


def _profile(**overrides) -> dict:
    """Return a minimal valid customer profile dict with optional overrides."""
    base = {
        "age":           35,
        "income":        1_200_000,
        "smoker":        False,
        "health_status": "healthy",
        "sum_need":      5_000_000,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Rule tests (5)
# ---------------------------------------------------------------------------

class TestAgeRules:
    def test_age_min_rejected(self):
        """Customer age below product min_age → AGE_MIN violation."""
        body, status = _call({
            "candidate_products": [_product(min_age=40)],
            "customer_profile":   _profile(age=30),
        })
        assert status == 200
        assert body["passed"] == []
        assert len(body["rejected"]) == 1
        assert body["rejected"][0]["product_id"] == "TERM001"
        assert any("AGE_MIN" in r or "Minimum entry age" in r for r in body["rejected"][0]["reasons"])

    def test_age_max_rejected(self):
        """Customer age above product max_age → AGE_MAX violation."""
        body, status = _call({
            "candidate_products": [_product(max_age=55)],
            "customer_profile":   _profile(age=60),
        })
        assert status == 200
        assert body["passed"] == []
        assert any("Maximum entry age" in r for r in body["rejected"][0]["reasons"])

    def test_age_within_bounds_passes(self):
        """Customer age within [min_age, max_age] → passes both age rules."""
        body, status = _call({
            "candidate_products": [_product(min_age=18, max_age=65)],
            "customer_profile":   _profile(age=35),
        })
        assert status == 200
        assert len(body["passed"]) == 1
        assert body["rejected"] == []


class TestSmokerRule:
    def test_smoker_excluded_when_product_not_eligible(self):
        """Smoker customer + smoker_eligible=False → SMOKER_EXCLUSION violation."""
        body, status = _call({
            "candidate_products": [_product(smoker_eligible=False)],
            "customer_profile":   _profile(smoker=True),
        })
        assert status == 200
        assert body["passed"] == []
        assert any("smoker" in r.lower() for r in body["rejected"][0]["reasons"])

    def test_non_smoker_passes_non_eligible_product(self):
        """Non-smoker customer passes a smoker_eligible=False product."""
        body, status = _call({
            "candidate_products": [_product(smoker_eligible=False)],
            "customer_profile":   _profile(smoker=False),
        })
        assert status == 200
        assert len(body["passed"]) == 1


class TestIncomeSumCapRule:
    def test_sum_need_exceeds_10x_income_rejected(self):
        """sum_need > income×10 → INCOME_SUM_CAP violation."""
        body, status = _call({
            "candidate_products": [_product()],
            "customer_profile":   _profile(income=500_000, sum_need=6_000_000),  # 12× income
        })
        assert status == 200
        assert body["passed"] == []
        assert any("income cap" in r.lower() for r in body["rejected"][0]["reasons"])

    def test_sum_need_at_10x_income_passes(self):
        """sum_need == income×10 is exactly on the boundary — should pass."""
        body, status = _call({
            "candidate_products": [_product()],
            "customer_profile":   _profile(income=500_000, sum_need=5_000_000),
        })
        assert status == 200
        assert len(body["passed"]) == 1


class TestMedicalExamRule:
    def test_large_sum_need_with_pre_existing_rejected(self):
        """sum_need > medical_required_above AND pre_existing → MEDICAL_EXAM_REQUIRED."""
        body, status = _call({
            "candidate_products": [_product(medical_required_above=3_000_000)],
            "customer_profile":   _profile(sum_need=4_000_000, health_status="pre_existing"),
        })
        assert status == 200
        assert body["passed"] == []
        assert any("Medical exam" in r for r in body["rejected"][0]["reasons"])

    def test_large_sum_need_with_healthy_passes(self):
        """sum_need > medical_required_above but health_status=healthy → passes."""
        body, status = _call({
            "candidate_products": [_product(medical_required_above=3_000_000)],
            "customer_profile":   _profile(sum_need=4_000_000, health_status="healthy"),
        })
        assert status == 200
        assert len(body["passed"]) == 1


# ---------------------------------------------------------------------------
# Validation tests (3)
# ---------------------------------------------------------------------------

class TestValidation:
    def test_missing_age_returns_400(self):
        """customer_profile without age field → HTTP 400 with fields list."""
        profile = _profile()
        del profile["age"]
        body, status = _call({
            "candidate_products": [_product()],
            "customer_profile":   profile,
        })
        assert status == 400
        assert body["error"] == "validation_error"
        assert any("age" in f for f in body["fields"])

    def test_missing_customer_profile_returns_400(self):
        """Request body without customer_profile key → HTTP 400."""
        body, status = _call({"candidate_products": [_product()]})
        assert status == 400
        assert body["error"] == "validation_error"

    def test_empty_candidates_returns_200_with_empty_lists(self):
        """Empty candidate_products list → HTTP 200, passed=[], rejected=[]."""
        body, status = _call({
            "candidate_products": [],
            "customer_profile":   _profile(),
        })
        assert status == 200
        assert body["passed"] == []
        assert body["rejected"] == []


# ---------------------------------------------------------------------------
# sum_need guard (TASK-030)
# ---------------------------------------------------------------------------

class TestSumNeedDefault:
    def test_absent_sum_need_passes_income_sum_cap(self):
        """Profile with no sum_need key defaults to 0 — always ≤ income×10."""
        profile = _profile()
        del profile["sum_need"]
        body, status = _call({
            "candidate_products": [_product()],
            "customer_profile":   profile,
        })
        assert status == 200
        # INCOME_SUM_CAP must not fire
        if body["rejected"]:
            for r in body["rejected"][0]["reasons"]:
                assert "income cap" not in r.lower(), "INCOME_SUM_CAP should not fire for absent sum_need"

    def test_absent_sum_need_passes_medical_exam_rule(self):
        """Profile with no sum_need key never exceeds medical_required_above."""
        profile = _profile(health_status="pre_existing")
        del profile["sum_need"]
        body, status = _call({
            "candidate_products": [_product(medical_required_above=1_000_000)],
            "customer_profile":   profile,
        })
        assert status == 200
        # MEDICAL_EXAM_REQUIRED must not fire (0 < 1_000_000)
        if body["rejected"]:
            for r in body["rejected"][0]["reasons"]:
                assert "Medical exam" not in r, "MEDICAL_EXAM_REQUIRED should not fire for absent sum_need"

"""
tests/test_simulate_premium.py
================================
Unit tests for functions/simulate_premium/main.py — deterministic premium
simulation engine (Story 6, TASK-096).

All tests run without network or LLM calls.  The Cloud Function is imported
directly and exercised through its internal helpers and through a synthetic
Flask-like request object.
"""

import importlib.util as _importlib_util
import json
import math
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Make simulate_premium importable without polluting sys.modules["main"].
# Using spec_from_file_location registers the module under a unique key
# ("simulate_premium_main") so it does not clobber agent_builder/main.py,
# which test_audit_log.py needs under the bare name "main".
# ---------------------------------------------------------------------------
_FUNC_DIR = Path(__file__).parent.parent / "functions" / "simulate_premium"
_sim_spec = _importlib_util.spec_from_file_location(
    "simulate_premium_main", _FUNC_DIR / "main.py"
)
sim_mod = _importlib_util.module_from_spec(_sim_spec)
sys.modules["simulate_premium_main"] = sim_mod
_sim_spec.loader.exec_module(sim_mod)  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Minimal product fixture covering all product types used in tests
_FIXTURE_PRODUCTS = [
    {
        "id": "TERM001",
        "name": "Future Secure Term",
        "product_type": "term_life",
        "min_age": 18, "max_age": 65,
        "smoker_eligible": False,
        "max_sum_assured": 50_000_000,
        "base_rate_per_lakh": 55,
        "age_bands": [
            {"min_age": 18, "max_age": 30, "loading_pct": 0},
            {"min_age": 31, "max_age": 45, "loading_pct": 15},
            {"min_age": 46, "max_age": 60, "loading_pct": 35},
            {"min_age": 61, "max_age": 95, "loading_pct": 60},
        ],
        "smoker_loading_pct": 0,
        "frequency_multipliers": {
            "monthly": 1.0, "quarterly": 0.99, "semi_annual": 0.975, "annual": 0.95
        },
        "available_terms": [10, 15, 20, 25, 30],
        "return_rate": None,
    },
    {
        "id": "TERM002",
        "name": "LifeGuard Plus",
        "product_type": "term_life",
        "min_age": 25, "max_age": 55,
        "smoker_eligible": True,
        "max_sum_assured": 30_000_000,
        "base_rate_per_lakh": 65,
        "age_bands": [
            {"min_age": 18, "max_age": 30, "loading_pct": 0},
            {"min_age": 31, "max_age": 45, "loading_pct": 15},
            {"min_age": 46, "max_age": 60, "loading_pct": 35},
            {"min_age": 61, "max_age": 95, "loading_pct": 60},
        ],
        "smoker_loading_pct": 30,
        "frequency_multipliers": {
            "monthly": 1.0, "quarterly": 0.99, "semi_annual": 0.975, "annual": 0.95
        },
        "available_terms": [10, 15, 20, 25, 30],
        "return_rate": None,
    },
    {
        "id": "ULIP001",
        "name": "WealthShield ULIP",
        "product_type": "ulip",
        "min_age": 18, "max_age": 55,
        "smoker_eligible": False,
        "max_sum_assured": 20_000_000,
        "base_rate_per_lakh": 900,
        "age_bands": [
            {"min_age": 18, "max_age": 30, "loading_pct": 0},
            {"min_age": 31, "max_age": 45, "loading_pct": 15},
            {"min_age": 46, "max_age": 60, "loading_pct": 35},
        ],
        "smoker_loading_pct": 0,
        "frequency_multipliers": {
            "monthly": 1.0, "quarterly": 0.99, "semi_annual": 0.975, "annual": 0.95
        },
        "available_terms": [10, 15, 20],
        "return_rate": 11.0,
    },
    {
        "id": "ENDT001",
        "name": "GrowthSure Endowment",
        "product_type": "endowment",
        "min_age": 18, "max_age": 55,
        "smoker_eligible": True,
        "max_sum_assured": 5_000_000,
        "base_rate_per_lakh": 700,
        "age_bands": [
            {"min_age": 18, "max_age": 30, "loading_pct": 0},
            {"min_age": 31, "max_age": 45, "loading_pct": 15},
            {"min_age": 46, "max_age": 60, "loading_pct": 35},
        ],
        "smoker_loading_pct": 20,
        "frequency_multipliers": {
            "monthly": 1.0, "quarterly": 0.99, "semi_annual": 0.975, "annual": 0.95
        },
        "available_terms": [10, 15, 20, 25],
        "return_rate": 6.5,
    },
    {
        "id": "PENS001",
        "name": "RetireSmart Pension",
        "product_type": "pension",
        "min_age": 30, "max_age": 65,
        "smoker_eligible": True,
        "max_sum_assured": 50_000_000,
        "base_rate_per_lakh": 600,
        "age_bands": [
            {"min_age": 18, "max_age": 30, "loading_pct": 0},
            {"min_age": 31, "max_age": 45, "loading_pct": 15},
            {"min_age": 46, "max_age": 65, "loading_pct": 35},
        ],
        "smoker_loading_pct": 10,
        "frequency_multipliers": {
            "monthly": 1.0, "quarterly": 0.99, "semi_annual": 0.975, "annual": 0.95
        },
        "available_terms": [10, 15, 20, 30],
        "return_rate": 7.0,
    },
    {
        "id": "HLTH001",
        "name": "MediCare Family Floater",
        "product_type": "health",
        "min_age": 18, "max_age": 65,
        "smoker_eligible": True,
        "max_sum_assured": 5_000_000,
        "base_rate_per_lakh": 380,
        "age_bands": [
            {"min_age": 18, "max_age": 30, "loading_pct": 0},
            {"min_age": 31, "max_age": 45, "loading_pct": 15},
            {"min_age": 46, "max_age": 65, "loading_pct": 35},
        ],
        "smoker_loading_pct": 15,
        "frequency_multipliers": {
            "monthly": 1.0, "quarterly": 0.99, "semi_annual": 0.975, "annual": 0.95
        },
        "available_terms": [1],
        "return_rate": None,
    },
    {
        "id": "CRIT001",
        "name": "CancerCare Shield",
        "product_type": "critical_illness",
        "min_age": 18, "max_age": 65,
        "smoker_eligible": False,
        "max_sum_assured": 5_000_000,
        "base_rate_per_lakh": 180,
        "age_bands": [
            {"min_age": 18, "max_age": 30, "loading_pct": 0},
            {"min_age": 31, "max_age": 45, "loading_pct": 15},
            {"min_age": 46, "max_age": 65, "loading_pct": 35},
        ],
        "smoker_loading_pct": 0,
        "frequency_multipliers": {
            "monthly": 1.0, "quarterly": 0.99, "semi_annual": 0.975, "annual": 0.95
        },
        "available_terms": [5, 10, 15, 20],
        "return_rate": None,
    },
]

_CATALOG = {p["id"]: p for p in _FIXTURE_PRODUCTS}


def _make_request(body: dict) -> MagicMock:
    """Create a minimal Flask-compatible mock request."""
    req = MagicMock()
    req.method = "POST"
    req.get_json = MagicMock(return_value=body)
    return req


def _call(body: dict, catalog: dict = _CATALOG) -> tuple[dict, int]:
    """Invoke simulate_premium with a patched catalog; return (response_dict, status_code)."""
    sim_mod._CATALOG = dict(catalog)
    req = _make_request(body)
    raw, status, _ = sim_mod.simulate_premium(req)
    return json.loads(raw), status


def setup_function():
    """Reset the module-level catalog cache before each test function."""
    sim_mod._CATALOG = {}


# ---------------------------------------------------------------------------
# Frequency multiplier tests
# ---------------------------------------------------------------------------

class TestFrequencyMultipliers:
    """Monthly is the reference rate; annual should be cheaper by 5%."""

    def test_annual_cheaper_than_monthly(self):
        monthly, _ = _call({"product_id": "TERM001", "sum_assured": 1_000_000,
                             "customer_age": 25, "is_smoker": False,
                             "premium_frequency": "monthly", "policy_term": 20})
        annual, _ = _call({"product_id": "TERM001", "sum_assured": 1_000_000,
                            "customer_age": 25, "is_smoker": False,
                            "premium_frequency": "annual", "policy_term": 20})
        # annual discount multiplier = 0.95 → annual discounted premium is 5% cheaper
        assert annual["annual_premium"] < monthly["annual_premium"]
        assert abs(annual["annual_premium"] / monthly["annual_premium"] - 0.95) < 0.001

    def test_quarterly_discount_applied(self):
        monthly, _ = _call({"product_id": "TERM001", "sum_assured": 1_000_000,
                             "customer_age": 25, "is_smoker": False,
                             "premium_frequency": "monthly", "policy_term": 20})
        quarterly, _ = _call({"product_id": "TERM001", "sum_assured": 1_000_000,
                               "customer_age": 25, "is_smoker": False,
                               "premium_frequency": "quarterly", "policy_term": 20})
        assert abs(quarterly["annual_premium"] / monthly["annual_premium"] - 0.99) < 0.001

    def test_period_premium_scales_with_periods(self):
        result, status = _call({"product_id": "TERM001", "sum_assured": 1_000_000,
                                 "customer_age": 25, "is_smoker": False,
                                 "premium_frequency": "annual", "policy_term": 10})
        assert status == 200
        # For annual: period_premium == annual_premium (paid once per year)
        assert abs(result["period_premium"] - result["annual_premium"]) < 0.01

    def test_monthly_period_premium_is_twelfth_of_annual(self):
        result, status = _call({"product_id": "TERM001", "sum_assured": 1_000_000,
                                 "customer_age": 25, "is_smoker": False,
                                 "premium_frequency": "monthly", "policy_term": 10})
        assert status == 200
        # period_premium is rounded to 2 d.p.; up to 12 × 0.005 = 0.06 accumulated error
        assert abs(result["period_premium"] * 12 - result["annual_premium"]) < 0.07


# ---------------------------------------------------------------------------
# Age band loading tests
# ---------------------------------------------------------------------------

class TestAgeBandLoading:

    def test_young_age_no_loading(self):
        result_young, _ = _call({"product_id": "TERM001", "sum_assured": 1_000_000,
                                  "customer_age": 25, "is_smoker": False,
                                  "premium_frequency": "annual", "policy_term": 20})
        assert result_young["formula_breakdown"]["age_loading_pct"] == 0

    def test_middle_age_loading_applied(self):
        result_mid, _ = _call({"product_id": "TERM001", "sum_assured": 1_000_000,
                                "customer_age": 40, "is_smoker": False,
                                "premium_frequency": "annual", "policy_term": 20})
        assert result_mid["formula_breakdown"]["age_loading_pct"] == 15

    def test_older_age_higher_loading(self):
        result_old, _ = _call({"product_id": "TERM001", "sum_assured": 1_000_000,
                                "customer_age": 55, "is_smoker": False,
                                "premium_frequency": "annual", "policy_term": 10})
        assert result_old["formula_breakdown"]["age_loading_pct"] == 35

    def test_older_premium_higher_than_young(self):
        young, _ = _call({"product_id": "TERM001", "sum_assured": 1_000_000,
                           "customer_age": 25, "is_smoker": False,
                           "premium_frequency": "annual", "policy_term": 10})
        old, _ = _call({"product_id": "TERM001", "sum_assured": 1_000_000,
                         "customer_age": 55, "is_smoker": False,
                         "premium_frequency": "annual", "policy_term": 10})
        assert old["annual_premium"] > young["annual_premium"]


# ---------------------------------------------------------------------------
# Smoker loading tests
# ---------------------------------------------------------------------------

class TestSmokerLoading:

    def test_smoker_loading_increases_premium(self):
        non_smoker, _ = _call({"product_id": "TERM002", "sum_assured": 1_000_000,
                                "customer_age": 35, "is_smoker": False,
                                "premium_frequency": "annual", "policy_term": 20})
        smoker, _ = _call({"product_id": "TERM002", "sum_assured": 1_000_000,
                            "customer_age": 35, "is_smoker": True,
                            "premium_frequency": "annual", "policy_term": 20})
        assert smoker["annual_premium"] > non_smoker["annual_premium"]
        # Smoker loading = 30% — annualised premium should be 1.3× the non-smoker premium
        ratio = smoker["annual_premium"] / non_smoker["annual_premium"]
        assert abs(ratio - 1.30) < 0.001

    def test_smoker_loading_zero_for_zero_loading_product(self):
        # TERM001 has smoker_loading_pct=0 and smoker_eligible=False;
        # we bypass eligibility check by calling _simulate directly
        product = _CATALOG["TERM001"]
        r = sim_mod._simulate(product, sa=1_000_000, age=25, is_smoker=True,
                              frequency="annual", term=20)
        assert r["formula_breakdown"]["smoker_loading_pct"] == 0

    def test_smoker_not_eligible_returns_400(self):
        result, status = _call({"product_id": "CRIT001", "sum_assured": 500_000,
                                 "customer_age": 30, "is_smoker": True,
                                 "premium_frequency": "monthly", "policy_term": 10})
        assert status == 400
        assert any("smoker" in e.lower() for e in result["validation_errors"])


# ---------------------------------------------------------------------------
# Return projection tests (savings products)
# ---------------------------------------------------------------------------

class TestReturnProjections:

    def test_ulip_has_projected_maturity(self):
        result, status = _call({"product_id": "ULIP001", "sum_assured": 1_000_000,
                                 "customer_age": 30, "is_smoker": False,
                                 "premium_frequency": "annual", "policy_term": 15})
        assert status == 200
        assert result["projected_maturity_value"] is not None
        assert result["projected_maturity_value"] > 0
        assert result["net_gain"] is not None

    def test_endowment_has_projected_maturity(self):
        result, status = _call({"product_id": "ENDT001", "sum_assured": 500_000,
                                 "customer_age": 35, "is_smoker": False,
                                 "premium_frequency": "annual", "policy_term": 20})
        assert status == 200
        assert result["projected_maturity_value"] is not None
        assert result["projected_maturity_value"] >= result["total_premium_outflow"]

    def test_pension_has_projected_maturity(self):
        result, status = _call({"product_id": "PENS001", "sum_assured": 1_000_000,
                                 "customer_age": 40, "is_smoker": False,
                                 "premium_frequency": "monthly", "policy_term": 20})
        assert status == 200
        assert result["projected_maturity_value"] is not None

    def test_net_gain_equals_maturity_minus_outflow(self):
        result, status = _call({"product_id": "ULIP001", "sum_assured": 1_000_000,
                                 "customer_age": 25, "is_smoker": False,
                                 "premium_frequency": "annual", "policy_term": 10})
        assert status == 200
        expected = result["projected_maturity_value"] - result["total_premium_outflow"]
        assert abs(result["net_gain"] - expected) < 0.01

    def test_term_life_returns_null(self):
        result, status = _call({"product_id": "TERM001", "sum_assured": 1_000_000,
                                 "customer_age": 30, "is_smoker": False,
                                 "premium_frequency": "annual", "policy_term": 20})
        assert status == 200
        assert result["projected_maturity_value"] is None
        assert result["net_gain"] is None

    def test_health_returns_null(self):
        result, status = _call({"product_id": "HLTH001", "sum_assured": 500_000,
                                 "customer_age": 30, "is_smoker": False,
                                 "premium_frequency": "annual", "policy_term": 1})
        assert status == 200
        assert result["projected_maturity_value"] is None

    def test_critical_illness_returns_null(self):
        result, status = _call({"product_id": "CRIT001", "sum_assured": 500_000,
                                 "customer_age": 30, "is_smoker": False,
                                 "premium_frequency": "annual", "policy_term": 10})
        assert status == 200
        assert result["projected_maturity_value"] is None


# ---------------------------------------------------------------------------
# Validation error tests
# ---------------------------------------------------------------------------

class TestValidation:

    def test_invalid_product_id_returns_400(self):
        result, status = _call({"product_id": "NOTEXIST", "sum_assured": 1_000_000,
                                 "customer_age": 30, "is_smoker": False,
                                 "premium_frequency": "monthly", "policy_term": 10})
        assert status == 400
        assert "validation_errors" in result
        assert any("not found" in e for e in result["validation_errors"])

    def test_missing_product_id_returns_400(self):
        result, status = _call({"sum_assured": 1_000_000, "customer_age": 30,
                                 "is_smoker": False, "premium_frequency": "monthly",
                                 "policy_term": 10})
        assert status == 400

    def test_sum_assured_below_1_lakh_returns_400(self):
        result, status = _call({"product_id": "TERM001", "sum_assured": 50_000,
                                 "customer_age": 25, "is_smoker": False,
                                 "premium_frequency": "annual", "policy_term": 20})
        assert status == 400
        assert any("sum_assured" in e.lower() for e in result["validation_errors"])

    def test_sum_assured_above_max_returns_400(self):
        result, status = _call({"product_id": "TERM001", "sum_assured": 999_000_000,
                                 "customer_age": 25, "is_smoker": False,
                                 "premium_frequency": "annual", "policy_term": 20})
        assert status == 400
        assert any("maximum" in e for e in result["validation_errors"])

    def test_age_below_min_returns_400(self):
        result, status = _call({"product_id": "TERM001", "sum_assured": 1_000_000,
                                 "customer_age": 15, "is_smoker": False,
                                 "premium_frequency": "annual", "policy_term": 20})
        assert status == 400
        assert any("min" in e.lower() and "age" in e.lower() for e in result["validation_errors"])

    def test_age_above_max_returns_400(self):
        result, status = _call({"product_id": "TERM001", "sum_assured": 1_000_000,
                                 "customer_age": 70, "is_smoker": False,
                                 "premium_frequency": "annual", "policy_term": 10})
        assert status == 400
        assert any("maximum age" in e.lower() or "max" in e.lower() for e in result["validation_errors"])

    def test_invalid_frequency_returns_400(self):
        result, status = _call({"product_id": "TERM001", "sum_assured": 1_000_000,
                                 "customer_age": 30, "is_smoker": False,
                                 "premium_frequency": "weekly", "policy_term": 20})
        assert status == 400
        assert any("frequency" in e.lower() for e in result["validation_errors"])

    def test_invalid_policy_term_returns_400(self):
        result, status = _call({"product_id": "TERM001", "sum_assured": 1_000_000,
                                 "customer_age": 30, "is_smoker": False,
                                 "premium_frequency": "annual", "policy_term": 7})
        assert status == 400
        assert any("policy_term" in e.lower() or "term" in e.lower()
                   for e in result["validation_errors"])

    def test_missing_sum_assured_returns_400(self):
        result, status = _call({"product_id": "TERM001", "customer_age": 30,
                                 "is_smoker": False, "premium_frequency": "annual",
                                 "policy_term": 20})
        assert status == 400

    def test_missing_is_smoker_returns_400(self):
        result, status = _call({"product_id": "TERM001", "sum_assured": 1_000_000,
                                 "customer_age": 30,
                                 "premium_frequency": "annual", "policy_term": 20})
        assert status == 400


# ---------------------------------------------------------------------------
# Formula correctness tests
# ---------------------------------------------------------------------------

class TestFormulaCorrectness:
    """Verify the arithmetic of the premium formula end-to-end."""

    def test_base_rate_scales_linearly_with_sum_assured(self):
        r1, _ = _call({"product_id": "TERM001", "sum_assured": 1_000_000,
                        "customer_age": 25, "is_smoker": False,
                        "premium_frequency": "annual", "policy_term": 20})
        r2, _ = _call({"product_id": "TERM001", "sum_assured": 2_000_000,
                        "customer_age": 25, "is_smoker": False,
                        "premium_frequency": "annual", "policy_term": 20})
        assert abs(r2["annual_premium"] / r1["annual_premium"] - 2.0) < 0.001

    def test_total_outflow_equals_period_times_periods_times_term(self):
        result, _ = _call({"product_id": "TERM001", "sum_assured": 1_000_000,
                            "customer_age": 25, "is_smoker": False,
                            "premium_frequency": "monthly", "policy_term": 20})
        expected = result["period_premium"] * 12 * 20
        # period_premium is rounded; 240 periods × 0.005 max = 1.2 accumulated error
        assert abs(result["total_premium_outflow"] - expected) < 1.5

    def test_formula_breakdown_echoes_key_steps(self):
        result, _ = _call({"product_id": "ENDT001", "sum_assured": 1_000_000,
                            "customer_age": 40, "is_smoker": True,
                            "premium_frequency": "annual", "policy_term": 15})
        bd = result["formula_breakdown"]
        assert "base_annual_premium" in bd
        assert "age_loading_pct" in bd
        assert "smoker_loading_pct" in bd
        assert "frequency_multiplier" in bd
        assert bd["smoker_loading_pct"] == 20   # ENDT001 smoker_loading_pct

    def test_response_includes_product_metadata(self):
        result, status = _call({"product_id": "ULIP001", "sum_assured": 1_000_000,
                                 "customer_age": 28, "is_smoker": False,
                                 "premium_frequency": "monthly", "policy_term": 15})
        assert status == 200
        assert result["product_id"] == "ULIP001"
        assert result["product_name"] == "WealthShield ULIP"
        assert result["product_type"] == "ulip"

    def test_simulation_inputs_echoed_in_response(self):
        body = {"product_id": "TERM001", "sum_assured": 1_500_000, "customer_age": 32,
                "is_smoker": False, "premium_frequency": "semi_annual", "policy_term": 15}
        result, status = _call(body)
        assert status == 200
        inputs = result["simulation_inputs"]
        assert inputs["sum_assured"] == 1_500_000
        assert inputs["premium_frequency"] == "semi_annual"
        assert inputs["policy_term"] == 15


# ---------------------------------------------------------------------------
# HTTP method tests
# ---------------------------------------------------------------------------

class TestHttpMethods:

    def test_get_method_returns_405(self):
        sim_mod._CATALOG = dict(_CATALOG)
        req = MagicMock()
        req.method = "GET"
        req.get_json = MagicMock(return_value={})
        raw, status, _ = sim_mod.simulate_premium(req)
        assert status == 405

    def test_options_returns_204(self):
        sim_mod._CATALOG = dict(_CATALOG)
        req = MagicMock()
        req.method = "OPTIONS"
        raw, status, _ = sim_mod.simulate_premium(req)
        assert status == 204

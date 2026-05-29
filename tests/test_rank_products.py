"""
tests/test_rank_products.py

TASK-040: Tests for functions/rank_products/main.py.

Coverage (per task spec):
  1. Scoring formula verification — known inputs produce the expected winner
  2. Normalisation — raw elser_score > 1.0 is normalised so suitability_score ≤ 1.0
  3. Audit trail present in every response (all_scored, formula_weights, profile_hash)
  4. Empty passed_products → HTTP 200, top3=[], audit present
  5. Missing age in customer_profile → HTTP 400
  6. No suitability_score > 1.0 produced for any scored product
"""
import hashlib
import json
import sys
import types
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub functions_framework so the Cloud Function can be imported without the
# runtime dependency being installed in the test environment.
# ---------------------------------------------------------------------------
_ff = types.ModuleType("functions_framework")
_ff.http = lambda fn: fn
sys.modules.setdefault("functions_framework", _ff)

# ---------------------------------------------------------------------------
# Import the function under test
# ---------------------------------------------------------------------------
import pathlib
ROOT = pathlib.Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from functions.rank_products.main import (  # noqa: E402
    normalise_scores,
    score_product,
    _profile_hash,
    rank_products,
    FORMULA_WEIGHTS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(body: dict) -> MagicMock:
    req = MagicMock()
    req.get_json.return_value = body
    return req


def _call(body: dict):
    """Call the Cloud Function and return (parsed_body, status_code)."""
    resp = rank_products(_make_request(body))
    body_str, status, _ = resp
    return json.loads(body_str), status


def _product(pid: str = "TERM001", elser_score: float = 7.5, **overrides) -> dict:
    """Minimal valid product dict with an elser_score field."""
    base = {
        "id":            pid,
        "name":          f"Product {pid}",
        "min_age":       18,
        "max_age":       65,
        "smoker_eligible": True,
        "min_income":    300_000,
        "max_sum_assured": 10_000_000,
        "medical_required_above": 5_000_000,
        "elser_score":   elser_score,
    }
    base.update(overrides)
    return base


def _profile(**overrides) -> dict:
    base = {
        "age":    35,
        "income": 1_200_000,
        "sum_need": 5_000_000,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. Scoring formula — known inputs produce the expected winner
# ---------------------------------------------------------------------------

class TestScoringFormula:
    def test_highest_elser_score_wins_when_age_and_income_equal(self):
        """With equal age/income profiles, the highest elser_score should rank first."""
        products = [
            _product("A", elser_score=15.0),
            _product("B", elser_score=7.5),
            _product("C", elser_score=3.0),
        ]
        body, status = _call({
            "passed_products": products,
            "customer_profile": _profile(),
        })
        assert status == 200
        assert body["top3"][0]["product"]["id"] == "A"

    def test_top3_ordering_is_descending(self):
        """top3 must be sorted descending by suitability_score."""
        products = [_product(f"P{i}", elser_score=float(i)) for i in range(1, 6)]
        body, status = _call({
            "passed_products": products,
            "customer_profile": _profile(),
        })
        assert status == 200
        scores = [item["suitability_score"] for item in body["top3"]]
        assert scores == sorted(scores, reverse=True)

    def test_rank_field_is_sequential(self):
        """rank values in top3 are 1, 2, 3."""
        products = [_product(f"P{i}", elser_score=float(i * 2)) for i in range(1, 4)]
        body, _ = _call({"passed_products": products, "customer_profile": _profile()})
        assert [item["rank"] for item in body["top3"]] == [1, 2, 3]

    def test_score_breakdown_components_present(self):
        """Each top3 item must include elser_relevance, age_centrality, income_fit."""
        body, _ = _call({
            "passed_products": [_product()],
            "customer_profile": _profile(),
        })
        breakdown = body["top3"][0]["score_breakdown"]
        assert "elser_relevance" in breakdown
        assert "age_centrality" in breakdown
        assert "income_fit" in breakdown

    def test_age_centrality_peaks_at_midpoint(self):
        """A customer at the midpoint of a product's age range should score higher
        on age_centrality than one near the boundary."""
        product_mid  = _product("MID",  min_age=30, max_age=50, elser_score=5.0)
        product_edge = _product("EDGE", min_age=30, max_age=50, elser_score=5.0)
        # Score with age at midpoint (40) vs near boundary (48)
        products_mid  = normalise_scores([product_mid.copy()])
        products_edge = normalise_scores([product_edge.copy()])
        score_mid  = score_product(products_mid[0],  {"age": 40, "income": 1_000_000, "sum_need": 0})
        score_edge = score_product(products_edge[0], {"age": 48, "income": 1_000_000, "sum_need": 0})
        assert score_mid["score_breakdown"]["age_centrality"] > score_edge["score_breakdown"]["age_centrality"]


# ---------------------------------------------------------------------------
# 2. Normalisation — raw elser_score > 1.0 is normalised, suitability_score ≤ 1.0
# ---------------------------------------------------------------------------

class TestNormalisation:
    def test_normalised_score_of_max_product_is_1(self):
        """The product with the highest raw elser_score gets elser_score_normalised = 1.0."""
        products = normalise_scores([
            _product("A", elser_score=15.0),
            _product("B", elser_score=7.5),
        ])
        max_product = next(p for p in products if p["id"] == "A")
        assert max_product["elser_score_normalised"] == 1.0

    def test_normalised_scores_all_lte_1(self):
        products = normalise_scores([
            _product("A", elser_score=22.5),
            _product("B", elser_score=11.0),
            _product("C", elser_score=3.0),
        ])
        assert all(p["elser_score_normalised"] <= 1.0 for p in products)

    def test_all_zero_scores_get_1_0(self):
        """When all raw scores are 0, normalised scores should all be 1.0 (not NaN/div-zero)."""
        products = normalise_scores([_product("A", elser_score=0.0), _product("B", elser_score=0.0)])
        assert all(p["elser_score_normalised"] == 1.0 for p in products)

    def test_single_product_normalised_to_1(self):
        products = normalise_scores([_product("A", elser_score=9.9)])
        assert products[0]["elser_score_normalised"] == 1.0

    def test_suitability_score_never_exceeds_1(self):
        """End-to-end test: even with extreme raw scores, suitability_score must be ≤ 1.0."""
        products = [_product("A", elser_score=100.0)]
        body, status = _call({
            "passed_products": products,
            "customer_profile": _profile(),
        })
        assert status == 200
        assert body["top3"][0]["suitability_score"] <= 1.0

    def test_all_top3_suitability_scores_lte_1(self):
        products = [_product(f"P{i}", elser_score=float(i * 5)) for i in range(1, 6)]
        body, _ = _call({"passed_products": products, "customer_profile": _profile()})
        for item in body["top3"]:
            assert item["suitability_score"] <= 1.0, (
                f"suitability_score {item['suitability_score']} > 1.0 for {item['product']['id']}"
            )

    def test_raw_elser_score_preserved_in_audit(self):
        """Raw elser_score must be preserved alongside the normalised value in audit."""
        body, _ = _call({
            "passed_products": [_product("A", elser_score=15.0)],
            "customer_profile": _profile(),
        })
        all_scored = body["audit"]["all_scored"]
        assert all_scored[0]["elser_score"] == 15.0


# ---------------------------------------------------------------------------
# 3. Audit trail present in every response
# ---------------------------------------------------------------------------

class TestAuditTrail:
    def test_audit_key_present(self):
        body, status = _call({
            "passed_products": [_product()],
            "customer_profile": _profile(),
        })
        assert status == 200
        assert "audit" in body

    def test_audit_contains_all_scored(self):
        products = [_product("A"), _product("B"), _product("C")]
        body, _ = _call({"passed_products": products, "customer_profile": _profile()})
        assert "all_scored" in body["audit"]
        assert len(body["audit"]["all_scored"]) == 3

    def test_audit_contains_formula_weights(self):
        body, _ = _call({"passed_products": [_product()], "customer_profile": _profile()})
        weights = body["audit"]["formula_weights"]
        assert weights["elser"]  == 0.4
        assert weights["age"]    == 0.3
        assert weights["income"] == 0.3

    def test_audit_contains_profile_hash(self):
        body, _ = _call({"passed_products": [_product()], "customer_profile": _profile()})
        assert "customer_profile_hash" in body["audit"]
        assert len(body["audit"]["customer_profile_hash"]) == 64  # SHA-256 hex digest

    def test_profile_hash_is_deterministic(self):
        """Same profile must always produce the same hash."""
        profile = _profile()
        h1 = _profile_hash(profile)
        h2 = _profile_hash(profile)
        assert h1 == h2

    def test_profile_hash_changes_with_different_profile(self):
        assert _profile_hash(_profile(age=30)) != _profile_hash(_profile(age=40))

    def test_all_scored_sorted_descending(self):
        """audit.all_scored must be sorted by suitability_score descending."""
        products = [_product(f"P{i}", elser_score=float(i)) for i in range(1, 5)]
        body, _ = _call({"passed_products": products, "customer_profile": _profile()})
        scores = [p["suitability_score"] for p in body["audit"]["all_scored"]]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# 4. Empty passed_products → HTTP 200, top3=[], audit present
# ---------------------------------------------------------------------------

class TestEmptyInput:
    def test_empty_products_returns_200(self):
        body, status = _call({"passed_products": [], "customer_profile": _profile()})
        assert status == 200

    def test_empty_products_top3_is_empty(self):
        body, _ = _call({"passed_products": [], "customer_profile": _profile()})
        assert body["top3"] == []

    def test_empty_products_audit_present(self):
        body, _ = _call({"passed_products": [], "customer_profile": _profile()})
        assert "audit" in body
        assert body["audit"]["all_scored"] == []
        assert "formula_weights" in body["audit"]
        assert "customer_profile_hash" in body["audit"]


# ---------------------------------------------------------------------------
# 5. Validation — missing age → HTTP 400
# ---------------------------------------------------------------------------

class TestValidation:
    def test_missing_age_returns_400(self):
        profile = _profile()
        del profile["age"]
        body, status = _call({"passed_products": [_product()], "customer_profile": profile})
        assert status == 400
        assert body["error"] == "validation_error"
        assert any("age" in f for f in body["fields"])

    def test_missing_customer_profile_returns_400(self):
        body, status = _call({"passed_products": [_product()]})
        assert status == 400
        assert body["error"] == "validation_error"

    def test_missing_passed_products_returns_400(self):
        body, status = _call({"customer_profile": _profile()})
        assert status == 400
        assert body["error"] == "validation_error"

    def test_non_json_body_returns_400(self):
        req = MagicMock()
        req.get_json.return_value = None
        resp = rank_products(req)
        body_str, status, _ = resp
        assert status == 400


# ---------------------------------------------------------------------------
# TASK-064 / TASK-065: sum_need edge cases → income_fit defaults to 0.5
# ---------------------------------------------------------------------------

class TestIncomeFitEdgeCases:
    def test_sum_need_zero_income_fit_default(self):
        """TASK-064: sum_need=0 → income_fit == 0.5 (no ZeroDivisionError)."""
        products = normalise_scores([_product().copy()])
        scores = score_product(products[0], {"age": 35, "income": 1_200_000, "sum_need": 0})
        assert scores["score_breakdown"]["income_fit"] == 0.5

    def test_sum_need_absent_income_fit_default(self):
        """TASK-065: profile with no sum_need key → income_fit == 0.5."""
        products = normalise_scores([_product().copy()])
        scores = score_product(products[0], {"age": 35, "income": 1_200_000})
        assert scores["score_breakdown"]["income_fit"] == 0.5

    def test_sum_need_zero_via_http(self):
        """sum_need=0 does not cause a server error — returns HTTP 200."""
        body, status = _call({
            "passed_products": [_product()],
            "customer_profile": {"age": 35, "income": 1_200_000, "sum_need": 0},
        })
        assert status == 200
        assert body["top3"][0]["score_breakdown"]["income_fit"] == 0.5


# ---------------------------------------------------------------------------
# TASK-066 / TASK-067: min_age == max_age (point range) edge cases
# ---------------------------------------------------------------------------

class TestPointAgeRange:
    def test_point_age_range_exact_match(self):
        """TASK-066: product min_age=max_age=40, customer age=40 → age_centrality == 1.0."""
        products = normalise_scores([_product("EXACT", min_age=40, max_age=40).copy()])
        scores = score_product(products[0], {"age": 40, "income": 1_000_000, "sum_need": 0})
        assert scores["score_breakdown"]["age_centrality"] == 1.0

    def test_point_age_range_mismatch(self):
        """TASK-067: product min_age=max_age=40, customer age=41 → age_centrality == 0.0 (not negative)."""
        products = normalise_scores([_product("MISS", min_age=40, max_age=40).copy()])
        scores = score_product(products[0], {"age": 41, "income": 1_000_000, "sum_need": 0})
        assert scores["score_breakdown"]["age_centrality"] == 0.0


# ---------------------------------------------------------------------------
# TASK-068: Fewer than 3 passed products — no padding
# ---------------------------------------------------------------------------

class TestFewProducts:
    def test_two_products_returns_two_in_top3(self):
        """TASK-068: 2 passed_products → len(top3)==2, no padding, no error."""
        body, status = _call({
            "passed_products": [_product("A"), _product("B")],
            "customer_profile": _profile(),
        })
        assert status == 200
        assert len(body["top3"]) == 2

    def test_two_products_ranks_are_sequential(self):
        """Ranks must be 1, 2 — not 1, 3 or 0-indexed."""
        body, _ = _call({
            "passed_products": [_product("A", elser_score=5.0), _product("B", elser_score=2.0)],
            "customer_profile": _profile(),
        })
        assert [item["rank"] for item in body["top3"]] == [1, 2]

    def test_one_product_returns_one_in_top3(self):
        body, status = _call({
            "passed_products": [_product("ONLY")],
            "customer_profile": _profile(),
        })
        assert status == 200
        assert len(body["top3"]) == 1
        assert body["top3"][0]["rank"] == 1


# ---------------------------------------------------------------------------
# TASK-069: Missing elser_score defaults to neutral (not 0.0)
# ---------------------------------------------------------------------------

class TestElserScoreAbsent:
    def test_elser_score_absent_defaults_to_neutral(self):
        """TASK-069: product with no elser_score key → elser_relevance > 0.0 (neutral, not worst-case)."""
        p = {
            "id": "NOELS",
            "name": "No ELSER Product",
            "min_age": 18,
            "max_age": 65,
            # elser_score deliberately omitted
        }
        products = normalise_scores([p.copy()])
        scores = score_product(products[0], {"age": 35, "income": 1_200_000, "sum_need": 0})
        assert scores["score_breakdown"]["elser_relevance"] > 0.0

    def test_elser_score_absent_batch_with_present_score(self):
        """A product missing elser_score should not dominate over one with a real score."""
        p_no_score = {"id": "NOELS", "name": "No Score", "min_age": 18, "max_age": 65}
        p_high_score = _product("HIGH", elser_score=10.0)
        body, status = _call({
            "passed_products": [p_no_score, p_high_score],
            "customer_profile": _profile(),
        })
        assert status == 200
        # The product with an explicit high elser_score should rank first
        assert body["top3"][0]["product"]["id"] == "HIGH"


# ---------------------------------------------------------------------------
# TASK-081: Audit all_scored covers ALL inputs (not just top-3)
# ---------------------------------------------------------------------------

class TestAuditAllScored:
    def test_audit_all_scored_covers_all_inputs(self):
        """TASK-081: 7 passed_products → audit.all_scored has 7 entries, not just 3."""
        products = [_product(f"P{i}", elser_score=float(i)) for i in range(1, 8)]
        body, status = _call({"passed_products": products, "customer_profile": _profile()})
        assert status == 200
        assert len(body["audit"]["all_scored"]) == 7

    def test_audit_all_scored_ids_match_input(self):
        """All input product IDs appear in audit.all_scored."""
        products = [_product(f"X{i}") for i in range(5)]
        body, _ = _call({"passed_products": products, "customer_profile": _profile()})
        audit_ids = {p["id"] for p in body["audit"]["all_scored"]}
        input_ids = {p["id"] for p in products}
        assert audit_ids == input_ids

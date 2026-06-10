"""
tests/test_product_search.py

TASK-027: Unit tests for functions/elastic_mcp_server/main.py.

Coverage (11 tests):
  Query structure (3):
    1. Retriever API with RRF present; two standard retriever legs
    2. Leg 1 contains semantic queries on both description and key_feature
    3. Leg 2 uses multi_match on name^2, tags, sales_pitch

  Filter logic (4):
    4. Age filters (min_age lte, max_age gte) present in both legs by default
    5. Age filters absent when relax_age=True
    6. Income filter (min_income lte income) always present regardless of relax_age
    7. smoker_eligible=true filter present only when is_smoker=True

  Optional params (1):
    8. product_type term filter appended to both legs when product_type is specified

  Input validation (2):
    9. Ages outside 18-75 return HTTP 400
   10. Each missing required field (query, customer_age, is_smoker, income) returns HTTP 400

  Score injection (1):
   11. elser_score in each candidate equals hit["_score"] from Elasticsearch response

All tests use a mock Elasticsearch client -- no live cluster required.
"""
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stubs -- prevent import errors for heavy deps not installed in test env
# ---------------------------------------------------------------------------

# elasticsearch: stub so Elasticsearch() construction at module level doesn't fail
_es_mod = types.ModuleType("elasticsearch")
_es_mod.Elasticsearch = MagicMock
sys.modules.setdefault("elasticsearch", _es_mod)

# fastmcp: stub FastMCP so @mcp.tool() decoration works at import time
class _FakeFastMCP:
    def __init__(self, *a, **kw): pass
    def tool(self): return lambda fn: fn
    def http_app(self, *a, **kw): return MagicMock()

_fastmcp_mod = types.ModuleType("fastmcp")
_fastmcp_mod.FastMCP = _FakeFastMCP
sys.modules.setdefault("fastmcp", _fastmcp_mod)

# fastapi: stub FastAPI + Request so app = FastAPI() and app.mount() work at import time
class _FakeApp:
    def mount(self, *a, **kw): pass
    def post(self, *a, **kw): return lambda fn: fn

_fastapi_mod = types.ModuleType("fastapi")
_fastapi_mod.FastAPI = lambda **kw: _FakeApp()
_fastapi_mod.Request = object
sys.modules.setdefault("fastapi", _fastapi_mod)

_fastapi_resp_mod = types.ModuleType("fastapi.responses")
_fastapi_resp_mod.JSONResponse = MagicMock
sys.modules.setdefault("fastapi.responses", _fastapi_resp_mod)

# ---------------------------------------------------------------------------
# Import helpers from the Elastic MCP server module under test
# ---------------------------------------------------------------------------
import pathlib
import importlib.util

ROOT = pathlib.Path(__file__).parent.parent
_FUNC_PATH = ROOT / "functions" / "elastic_mcp_server" / "main.py"

_env_patch = {"ES_URL": "https://fake.es.io", "ES_API_KEY": "fake-key"}


def _import_mcp_server():
    """Import (or re-import) the module with patched env vars."""
    with patch.dict("os.environ", _env_patch):
        mod_name = "elastic_mcp_server_module"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        spec = importlib.util.spec_from_file_location(mod_name, _FUNC_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


_mod = _import_mcp_server()
_build_eligibility_filters = _mod._build_eligibility_filters
_build_query               = _mod._build_query
_hits_to_candidates        = _mod._hits_to_candidates


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _call(body):
    """Call _execute_search and return (body_dict, status_code)."""
    return _mod._execute_search(body)


def _get_filters_from_leg(retriever_leg: dict) -> list:
    """Extract the filter list from a standard retriever leg dict."""
    return retriever_leg["standard"].get("filter", [])


def _all_filter_keys(filter_list: list) -> set:
    """Return all field names referenced across a list of filter dicts."""
    keys = set()
    for f in filter_list:
        if "term" in f:
            keys.update(f["term"].keys())
        if "range" in f:
            keys.update(f["range"].keys())
    return keys


# ---------------------------------------------------------------------------
# 1 — Query structure: RRF with two retriever legs
# ---------------------------------------------------------------------------

class TestQueryStructure:
    def setup_method(self):
        self.q = _build_query(
            query_text="term life for family",
            customer_age=38,
            is_smoker=False,
            income=800_000,
            product_type=None,
            size=10,
            relax_age=False,
        )

    def test_rrf_retriever_present(self):
        assert "retriever" in self.q
        assert "rrf" in self.q["retriever"]

    def test_two_standard_retriever_legs(self):
        legs = self.q["retriever"]["rrf"]["retrievers"]
        assert len(legs) == 2
        for leg in legs:
            assert "standard" in leg

    def test_rrf_parameters(self):
        rrf = self.q["retriever"]["rrf"]
        assert rrf["rank_window_size"] == 20
        assert rrf["rank_constant"] == 60


# ---------------------------------------------------------------------------
# 2 — Leg 1 contains semantic queries on description and key_feature
# ---------------------------------------------------------------------------

class TestSemanticLeg:
    def setup_method(self):
        self.q = _build_query(
            query_text="pension plan for retirement",
            customer_age=50,
            is_smoker=False,
            income=1_500_000,
            product_type=None,
            size=10,
            relax_age=False,
        )
        self.leg1 = self.q["retriever"]["rrf"]["retrievers"][0]

    def test_leg1_has_bool_should(self):
        query_clause = self.leg1["standard"]["query"]
        assert "bool" in query_clause
        assert "should" in query_clause["bool"]

    def test_semantic_on_description(self):
        should = self.leg1["standard"]["query"]["bool"]["should"]
        semantic_fields = [c["semantic"]["field"] for c in should if "semantic" in c]
        assert "description" in semantic_fields

    def test_semantic_on_key_feature(self):
        should = self.leg1["standard"]["query"]["bool"]["should"]
        semantic_fields = [c["semantic"]["field"] for c in should if "semantic" in c]
        assert "key_feature" in semantic_fields

    def test_minimum_should_match_is_1(self):
        bool_clause = self.leg1["standard"]["query"]["bool"]
        assert bool_clause.get("minimum_should_match") == 1


# ---------------------------------------------------------------------------
# 3 — Leg 2 uses multi_match on name^2, tags, sales_pitch
# ---------------------------------------------------------------------------

class TestBM25Leg:
    def setup_method(self):
        self.q = _build_query(
            query_text="health insurance family",
            customer_age=30,
            is_smoker=False,
            income=600_000,
            product_type=None,
            size=10,
            relax_age=False,
        )
        self.leg2 = self.q["retriever"]["rrf"]["retrievers"][1]

    def test_leg2_uses_multi_match(self):
        query_clause = self.leg2["standard"]["query"]
        assert "multi_match" in query_clause

    def test_multi_match_fields(self):
        fields = self.leg2["standard"]["query"]["multi_match"]["fields"]
        assert "name^2" in fields
        assert "tags" in fields
        assert "sales_pitch" in fields

    def test_multi_match_type_best_fields(self):
        assert self.leg2["standard"]["query"]["multi_match"]["type"] == "best_fields"


# ---------------------------------------------------------------------------
# 4 — Age filters present in both legs by default (relax_age=False)
# ---------------------------------------------------------------------------

class TestAgeFilterPresent:
    def setup_method(self):
        self.q = _build_query(
            query_text="any plan",
            customer_age=40,
            is_smoker=False,
            income=700_000,
            product_type=None,
            size=10,
            relax_age=False,
        )
        self.legs = self.q["retriever"]["rrf"]["retrievers"]

    def _age_filter_keys(self, leg):
        return _all_filter_keys(_get_filters_from_leg(leg))

    def test_min_age_filter_in_leg1(self):
        assert "min_age" in self._age_filter_keys(self.legs[0])

    def test_max_age_filter_in_leg1(self):
        assert "max_age" in self._age_filter_keys(self.legs[0])

    def test_min_age_filter_in_leg2(self):
        assert "min_age" in self._age_filter_keys(self.legs[1])

    def test_max_age_filter_in_leg2(self):
        assert "max_age" in self._age_filter_keys(self.legs[1])

    def test_min_age_lte_customer_age(self):
        filters = _get_filters_from_leg(self.legs[0])
        min_age_filter = next(f for f in filters if "range" in f and "min_age" in f["range"])
        assert min_age_filter["range"]["min_age"]["lte"] == 40

    def test_max_age_gte_customer_age(self):
        filters = _get_filters_from_leg(self.legs[0])
        max_age_filter = next(f for f in filters if "range" in f and "max_age" in f["range"])
        assert max_age_filter["range"]["max_age"]["gte"] == 40


# ---------------------------------------------------------------------------
# 5 — Age filters absent when relax_age=True
# ---------------------------------------------------------------------------

class TestAgeFilterAbsentWhenRelaxed:
    def setup_method(self):
        self.q = _build_query(
            query_text="any plan",
            customer_age=40,
            is_smoker=False,
            income=700_000,
            product_type=None,
            size=10,
            relax_age=True,
        )
        self.legs = self.q["retriever"]["rrf"]["retrievers"]

    def _filter_keys(self, leg):
        return _all_filter_keys(_get_filters_from_leg(leg))

    def test_min_age_absent_in_leg1(self):
        assert "min_age" not in self._filter_keys(self.legs[0])

    def test_max_age_absent_in_leg1(self):
        assert "max_age" not in self._filter_keys(self.legs[0])

    def test_min_age_absent_in_leg2(self):
        assert "min_age" not in self._filter_keys(self.legs[1])

    def test_max_age_absent_in_leg2(self):
        assert "max_age" not in self._filter_keys(self.legs[1])


# ---------------------------------------------------------------------------
# 6 — Income filter always present regardless of relax_age
# ---------------------------------------------------------------------------

class TestIncomeFilterAlwaysPresent:
    def _income_filter_present(self, relax_age: bool) -> bool:
        q = _build_query(
            query_text="any plan",
            customer_age=35,
            is_smoker=False,
            income=500_000,
            product_type=None,
            size=10,
            relax_age=relax_age,
        )
        legs = q["retriever"]["rrf"]["retrievers"]
        for leg in legs:
            keys = _all_filter_keys(_get_filters_from_leg(leg))
            if "min_income" not in keys:
                return False
        return True

    def test_income_filter_present_with_age_strict(self):
        assert self._income_filter_present(relax_age=False) is True

    def test_income_filter_present_with_age_relaxed(self):
        assert self._income_filter_present(relax_age=True) is True

    def test_income_filter_value_correct(self):
        q = _build_query(
            query_text="plan",
            customer_age=30,
            is_smoker=False,
            income=900_000,
            product_type=None,
            size=10,
            relax_age=False,
        )
        filters = _get_filters_from_leg(q["retriever"]["rrf"]["retrievers"][0])
        income_filter = next(f for f in filters if "range" in f and "min_income" in f["range"])
        assert income_filter["range"]["min_income"]["lte"] == 900_000


# ---------------------------------------------------------------------------
# 7 — smoker_eligible filter only when is_smoker=True
# ---------------------------------------------------------------------------

class TestSmokerFilter:
    def _smoker_filter_present(self, is_smoker: bool) -> bool:
        q = _build_query(
            query_text="any plan",
            customer_age=35,
            is_smoker=is_smoker,
            income=600_000,
            product_type=None,
            size=10,
            relax_age=False,
        )
        legs = q["retriever"]["rrf"]["retrievers"]
        for leg in legs:
            keys = _all_filter_keys(_get_filters_from_leg(leg))
            if "smoker_eligible" in keys:
                return True
        return False

    def test_smoker_filter_present_for_smoker(self):
        assert self._smoker_filter_present(is_smoker=True) is True

    def test_smoker_filter_absent_for_non_smoker(self):
        assert self._smoker_filter_present(is_smoker=False) is False

    def test_smoker_filter_value_is_true(self):
        q = _build_query(
            query_text="plan",
            customer_age=35,
            is_smoker=True,
            income=600_000,
            product_type=None,
            size=10,
            relax_age=False,
        )
        filters = _get_filters_from_leg(q["retriever"]["rrf"]["retrievers"][0])
        smoker_filter = next(f for f in filters if "term" in f and "smoker_eligible" in f["term"])
        assert smoker_filter["term"]["smoker_eligible"] is True


# ---------------------------------------------------------------------------
# 8 — product_type term filter appended when product_type is specified
# ---------------------------------------------------------------------------

class TestProductTypeFilter:
    def test_product_type_filter_added(self):
        q = _build_query(
            query_text="pension plan",
            customer_age=50,
            is_smoker=False,
            income=1_000_000,
            product_type="pension",
            size=10,
            relax_age=False,
        )
        for leg in q["retriever"]["rrf"]["retrievers"]:
            keys = _all_filter_keys(_get_filters_from_leg(leg))
            assert "product_type" in keys, "product_type filter missing from retriever leg"

    def test_product_type_filter_value_correct(self):
        q = _build_query(
            query_text="pension plan",
            customer_age=50,
            is_smoker=False,
            income=1_000_000,
            product_type="pension",
            size=10,
            relax_age=False,
        )
        filters = _get_filters_from_leg(q["retriever"]["rrf"]["retrievers"][0])
        pt_filter = next(f for f in filters if "term" in f and "product_type" in f["term"])
        assert pt_filter["term"]["product_type"] == "pension"

    def test_product_type_filter_absent_when_not_specified(self):
        q = _build_query(
            query_text="any plan",
            customer_age=35,
            is_smoker=False,
            income=600_000,
            product_type=None,
            size=10,
            relax_age=False,
        )
        for leg in q["retriever"]["rrf"]["retrievers"]:
            keys = _all_filter_keys(_get_filters_from_leg(leg))
            assert "product_type" not in keys


# ---------------------------------------------------------------------------
# 9 — Input validation: age out of range → HTTP 400
# ---------------------------------------------------------------------------

class TestValidationAgeRange:
    def test_age_below_minimum_returns_400(self):
        body, status = _call({
            "query": "plan", "customer_age": 17,
            "is_smoker": False, "income": 600_000,
        })
        assert status == 400
        assert body["error"] == "validation_error"
        assert any("customer_age" in f for f in body["fields"])

    def test_age_above_maximum_returns_400(self):
        body, status = _call({
            "query": "plan", "customer_age": 76,
            "is_smoker": False, "income": 600_000,
        })
        assert status == 400
        assert body["error"] == "validation_error"
        assert any("customer_age" in f for f in body["fields"])

    def test_age_at_minimum_boundary_accepted(self):
        """age=18 is valid; mock ES so _execute_search can complete."""
        with patch.object(_mod, "_es") as mock_es:
            mock_es.search.return_value = {"hits": {"hits": [], "total": {"value": 0}}}
            body, status = _call({"query": "plan", "customer_age": 18, "is_smoker": False, "income": 600_000})
        assert status == 200

    def test_age_at_maximum_boundary_accepted(self):
        with patch.object(_mod, "_es") as mock_es:
            mock_es.search.return_value = {"hits": {"hits": [], "total": {"value": 0}}}
            body, status = _call({"query": "plan", "customer_age": 75, "is_smoker": False, "income": 600_000})
        assert status == 200


# ---------------------------------------------------------------------------
# 10 — Input validation: missing required fields → HTTP 400
# ---------------------------------------------------------------------------

class TestValidationMissingFields:
    _valid_base = {
        "query": "family life cover",
        "customer_age": 35,
        "is_smoker": False,
        "income": 700_000,
    }

    @pytest.mark.parametrize("missing_field", ["query", "customer_age", "is_smoker", "income"])
    def test_missing_required_field_returns_400(self, missing_field):
        body_data = {k: v for k, v in self._valid_base.items() if k != missing_field}
        body, status = _call(body_data)
        assert status == 400
        assert body["error"] == "validation_error"
        assert any(missing_field in f for f in body["fields"])

    def test_null_body_returns_400(self):
        body, status = _mod._execute_search(None)
        assert status == 400
        assert "validation_error" in body["error"]


# ---------------------------------------------------------------------------
# 11 — elser_score injected from hit["_score"]
# ---------------------------------------------------------------------------

class TestElserScoreInjection:
    def test_elser_score_equals_hit_score(self):
        hits = [
            {"_source": {"id": "TERM001", "name": "SecureLife Term"}, "_score": 14.2},
            {"_source": {"id": "TERM002", "name": "LifeGuard Plus"},  "_score":  7.8},
            {"_source": {"id": "TERM003", "name": "FamilyProtect"},    "_score":  2.1},
        ]
        candidates = _hits_to_candidates(hits)
        assert len(candidates) == 3
        assert candidates[0]["elser_score"] == pytest.approx(14.2)
        assert candidates[1]["elser_score"] == pytest.approx(7.8)
        assert candidates[2]["elser_score"] == pytest.approx(2.1)

    def test_elser_score_is_float(self):
        hits = [{"_source": {"id": "X"}, "_score": 5}]  # integer score
        candidates = _hits_to_candidates(hits)
        assert isinstance(candidates[0]["elser_score"], float)

    def test_missing_score_defaults_to_zero(self):
        hits = [{"_source": {"id": "X"}}]  # no _score key
        candidates = _hits_to_candidates(hits)
        assert candidates[0]["elser_score"] == 0.0

    def test_elser_score_injected_via_cloud_function(self):
        """End-to-end: mock ES client; assert elser_score appears in response."""
        mock_hits = [
            {"_source": {"id": "TERM001", "name": "SecureLife"}, "_score": 9.5},
        ]
        with patch.object(_mod, "_es") as mock_es:
            mock_es.search.return_value = {
                "hits": {"hits": mock_hits, "total": {"value": 1}},
            }
            body, status = _mod._execute_search({
                "query": "term life",
                "customer_age": 35,
                "is_smoker": False,
                "income": 600_000,
            })
        assert status == 200
        assert len(body["candidates"]) == 1
        assert body["candidates"][0]["elser_score"] == pytest.approx(9.5)
        assert body["total_hits"] == 1
        assert body["fallback_triggered"] is False

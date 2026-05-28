"""
tests/test_models.py
Round-trip serialisation tests for all shared dataclasses and validators.
Phase 1 exit criterion: all tests pass.
"""
import dataclasses
import pytest

from shared.models import (
    AuditTrail,
    CandidateProduct,
    ComplianceRequest,
    ComplianceResponse,
    CoverageGoal,
    CustomerProfile,
    HealthStatus,
    InsuranceProduct,
    PlanCategory,
    ProductType,
    RankRequest,
    RankResponse,
    RankedProduct,
    RejectedProduct,
    ScoreBreakdown,
)
from shared.validation import (
    ComplianceRequestValidator,
    CustomerProfileValidator,
    RankRequestValidator,
    format_validation_error,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_profile_dict():
    return {
        "age": 38,
        "income": 1_500_000,
        "smoker": False,
        "health_status": "healthy",
        "coverage_goals": ["life", "health"],
        "sum_need": 10_000_000,
        "family_size": 4,
        "dependents": 2,
        "preferred_term_years": 20,
    }


@pytest.fixture
def sample_product_dict():
    return {
        "id": "TERM001",
        "product_code": "FG_TERM_001",
        "name": "Future Secure Term Plan",
        "product_type": "term_life",
        "plan_category": "Protection",
        "uin": "123N456V01",
        "description": "A comprehensive term plan for life protection.",
        "key_feature": "High life cover at affordable premiums",
        "sales_pitch": "Secure your family with affordable cover.",
        "tags": ["term", "protection"],
        "rider_name": "Accidental Death Benefit Rider",
        "rider_type": "Accidental",
        "min_age": 18,
        "max_age": 65,
        "smoker_eligible": False,
        "min_income": 300_000,
        "max_sum_assured": 50_000_000,
        "medical_required_above": 10_000_000,
        "exclusions": ["suicide_within_1yr"],
        "premium_min_monthly": 500,
        "premium_max_monthly": 3_000,
        "is_active": True,
    }


@pytest.fixture
def sample_candidate_dict(sample_product_dict):
    return {**sample_product_dict, "elser_score": 12.5}


# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------

def test_health_status_values():
    assert HealthStatus.HEALTHY.value == "healthy"
    assert HealthStatus.PRE_EXISTING.value == "pre_existing"


def test_coverage_goal_values():
    expected = {"life", "health", "critical_illness", "accident", "investment", "endowment"}
    assert {g.value for g in CoverageGoal} == expected


def test_product_type_values():
    expected = {"term_life", "health", "ulip", "endowment", "critical_illness", "pension", "child_plan"}
    assert {t.value for t in ProductType} == expected


def test_plan_category_values():
    expected = {"Protection", "Savings", "Retirement", "ULIP", "Investment", "Health Insurance", "Child", "Critical Illness"}
    assert {c.value for c in PlanCategory} == expected


# ---------------------------------------------------------------------------
# CustomerProfile tests
# ---------------------------------------------------------------------------

def test_customer_profile_required_fields(sample_profile_dict):
    profile = CustomerProfile(**sample_profile_dict)
    assert profile.age == 38
    assert profile.income == 1_500_000
    assert profile.smoker is False
    assert profile.health_status == "healthy"
    assert profile.coverage_goals == ["life", "health"]


def test_customer_profile_optional_defaults():
    profile = CustomerProfile(
        age=30,
        income=500_000,
        smoker=False,
        health_status="healthy",
        coverage_goals=["life"],
    )
    assert profile.sum_need is None
    assert profile.family_size is None
    assert profile.dependents is None
    assert profile.preferred_term_years is None


def test_customer_profile_to_dict(sample_profile_dict):
    profile = CustomerProfile(**sample_profile_dict)
    d = profile.to_dict()
    assert isinstance(d, dict)
    assert d["age"] == 38
    assert d["coverage_goals"] == ["life", "health"]


def test_customer_profile_is_dataclass(sample_profile_dict):
    profile = CustomerProfile(**sample_profile_dict)
    assert dataclasses.is_dataclass(profile)


# ---------------------------------------------------------------------------
# InsuranceProduct tests
# ---------------------------------------------------------------------------

def test_insurance_product_fields(sample_product_dict):
    product = InsuranceProduct(**sample_product_dict)
    assert product.id == "TERM001"
    assert product.product_code == "FG_TERM_001"
    assert product.uin == "123N456V01"
    assert product.plan_category == "Protection"
    assert product.rider_name == "Accidental Death Benefit Rider"
    assert product.is_active is True


def test_insurance_product_optional_rider_none():
    product = InsuranceProduct(
        id="PENS001", product_code="FG_PENS_001", name="Test Pension",
        product_type="pension", plan_category="Retirement", uin="678N901V01",
        description="A pension plan.", key_feature="Guaranteed income",
        sales_pitch="Retire comfortably.", tags=["pension"],
        rider_name=None, rider_type=None,
        min_age=30, max_age=65, smoker_eligible=True,
        min_income=400_000, max_sum_assured=50_000_000,
        medical_required_above=0, exclusions=[],
        premium_min_monthly=2_000, premium_max_monthly=50_000,
    )
    assert product.rider_name is None
    assert product.rider_type is None
    assert product.is_active is True  # default


def test_insurance_product_to_dict(sample_product_dict):
    product = InsuranceProduct(**sample_product_dict)
    d = product.to_dict()
    assert isinstance(d, dict)
    assert d["id"] == "TERM001"
    assert isinstance(d["tags"], list)
    assert isinstance(d["exclusions"], list)


# ---------------------------------------------------------------------------
# CandidateProduct tests
# ---------------------------------------------------------------------------

def test_candidate_product_default_elser_score(sample_product_dict):
    candidate = CandidateProduct(**sample_product_dict)
    assert candidate.elser_score == 0.5


def test_candidate_product_custom_elser_score(sample_candidate_dict):
    candidate = CandidateProduct(**sample_candidate_dict)
    assert candidate.elser_score == 12.5


def test_candidate_product_inherits_insurance_product(sample_candidate_dict):
    candidate = CandidateProduct(**sample_candidate_dict)
    assert isinstance(candidate, InsuranceProduct)
    assert candidate.id == "TERM001"


def test_candidate_product_to_dict(sample_candidate_dict):
    candidate = CandidateProduct(**sample_candidate_dict)
    d = candidate.to_dict()
    assert "elser_score" in d
    assert d["elser_score"] == 12.5


# ---------------------------------------------------------------------------
# RejectedProduct tests
# ---------------------------------------------------------------------------

def test_rejected_product_fields():
    rejected = RejectedProduct(
        product_id="TERM001",
        product_name="Future Secure Term Plan",
        reasons=["Maximum entry age is 65; customer is 70"],
    )
    assert rejected.product_id == "TERM001"
    assert len(rejected.reasons) == 1
    d = rejected.to_dict()
    assert d["reasons"] == ["Maximum entry age is 65; customer is 70"]


def test_rejected_product_multiple_reasons():
    rejected = RejectedProduct(
        product_id="ULIP001",
        product_name="WealthShield ULIP",
        reasons=["Product not available for smokers", "Requested sum assured exceeds 10x annual income cap"],
    )
    assert len(rejected.reasons) == 2


# ---------------------------------------------------------------------------
# ComplianceRequest / ComplianceResponse tests
# ---------------------------------------------------------------------------

def test_compliance_request_to_dict(sample_candidate_dict, sample_profile_dict):
    req = ComplianceRequest(
        candidate_products=[sample_candidate_dict],
        customer_profile=sample_profile_dict,
    )
    d = req.to_dict()
    assert isinstance(d["candidate_products"], list)
    assert len(d["candidate_products"]) == 1


def test_compliance_response_to_dict(sample_candidate_dict):
    resp = ComplianceResponse(
        passed=[sample_candidate_dict],
        rejected=[RejectedProduct("X001", "Old Product", ["Age too high"]).to_dict()],
    )
    d = resp.to_dict()
    assert len(d["passed"]) == 1
    assert len(d["rejected"]) == 1


# ---------------------------------------------------------------------------
# ScoreBreakdown / RankedProduct tests
# ---------------------------------------------------------------------------

def test_score_breakdown_fields():
    sb = ScoreBreakdown(elser_relevance=0.85, age_centrality=0.90, income_fit=0.75)
    assert sb.elser_relevance == 0.85
    d = sb.to_dict()
    assert set(d.keys()) == {"elser_relevance", "age_centrality", "income_fit"}


def test_ranked_product_fields(sample_candidate_dict):
    sb = ScoreBreakdown(elser_relevance=0.85, age_centrality=0.90, income_fit=0.75)
    rp = RankedProduct(
        rank=1,
        product=sample_candidate_dict,
        suitability_score=0.8425,
        score_breakdown=sb,
    )
    assert rp.rank == 1
    assert rp.suitability_score == 0.8425
    d = rp.to_dict()
    assert d["rank"] == 1
    assert "score_breakdown" in d


# ---------------------------------------------------------------------------
# AuditTrail tests
# ---------------------------------------------------------------------------

def test_audit_trail_fields(sample_candidate_dict):
    audit = AuditTrail(
        all_scored=[{"id": "TERM001", "suitability_score": 0.84}],
        formula_weights={"elser": 0.4, "age": 0.3, "income": 0.3},
        customer_profile_hash="abc123",
    )
    assert audit.customer_profile_hash == "abc123"
    d = audit.to_dict()
    assert d["formula_weights"]["elser"] == 0.4


# ---------------------------------------------------------------------------
# RankRequest / RankResponse tests
# ---------------------------------------------------------------------------

def test_rank_request_to_dict(sample_candidate_dict, sample_profile_dict):
    req = RankRequest(
        passed_products=[sample_candidate_dict],
        customer_profile=sample_profile_dict,
    )
    d = req.to_dict()
    assert len(d["passed_products"]) == 1


def test_rank_response_to_dict(sample_candidate_dict):
    sb = ScoreBreakdown(elser_relevance=0.85, age_centrality=0.9, income_fit=0.75)
    rp = RankedProduct(rank=1, product=sample_candidate_dict, suitability_score=0.84, score_breakdown=sb)
    audit = AuditTrail(
        all_scored=[{"id": "TERM001", "suitability_score": 0.84}],
        formula_weights={"elser": 0.4, "age": 0.3, "income": 0.3},
        customer_profile_hash="abc123",
    )
    resp = RankResponse(top3=[rp.to_dict()], audit=audit)
    d = resp.to_dict()
    assert len(d["top3"]) == 1
    assert "audit" in d


# ---------------------------------------------------------------------------
# Pydantic validator tests  (TASK-007 / 008 / 009)
# ---------------------------------------------------------------------------

class TestCustomerProfileValidator:
    def test_valid_profile(self, sample_profile_dict):
        v = CustomerProfileValidator(**sample_profile_dict)
        assert v.age == 38

    def test_age_too_low(self, sample_profile_dict):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CustomerProfileValidator(**{**sample_profile_dict, "age": 17})

    def test_age_too_high(self, sample_profile_dict):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CustomerProfileValidator(**{**sample_profile_dict, "age": 76})

    def test_income_too_low(self, sample_profile_dict):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CustomerProfileValidator(**{**sample_profile_dict, "income": 50_000})

    def test_invalid_health_status(self, sample_profile_dict):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CustomerProfileValidator(**{**sample_profile_dict, "health_status": "unknown"})

    def test_empty_coverage_goals(self, sample_profile_dict):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CustomerProfileValidator(**{**sample_profile_dict, "coverage_goals": []})

    def test_invalid_coverage_goal(self, sample_profile_dict):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CustomerProfileValidator(**{**sample_profile_dict, "coverage_goals": ["flying"]})

    def test_sum_need_exceeds_cap(self, sample_profile_dict):
        from pydantic import ValidationError
        # income=1_500_000, cap=15_000_000; sum_need=20_000_000 exceeds cap
        with pytest.raises(ValidationError):
            CustomerProfileValidator(**{**sample_profile_dict, "sum_need": 20_000_000})

    def test_optional_fields_default_none(self):
        v = CustomerProfileValidator(
            age=30, income=500_000, smoker=False,
            health_status="healthy", coverage_goals=["life"],
        )
        assert v.sum_need is None
        assert v.family_size is None


class TestComplianceRequestValidator:
    def test_valid_request(self, sample_candidate_dict, sample_profile_dict):
        v = ComplianceRequestValidator(
            candidate_products=[sample_candidate_dict],
            customer_profile=sample_profile_dict,
        )
        assert len(v.candidate_products) == 1

    def test_empty_candidates_valid(self, sample_profile_dict):
        v = ComplianceRequestValidator(
            candidate_products=[],
            customer_profile=sample_profile_dict,
        )
        assert v.candidate_products == []

    def test_missing_profile_field(self, sample_candidate_dict):
        from pydantic import ValidationError
        bad_profile = {"age": 38, "income": 1_500_000, "smoker": False}  # missing health_status
        with pytest.raises(ValidationError):
            ComplianceRequestValidator(
                candidate_products=[sample_candidate_dict],
                customer_profile=bad_profile,
            )


class TestRankRequestValidator:
    def test_valid_request(self, sample_candidate_dict, sample_profile_dict):
        v = RankRequestValidator(
            passed_products=[sample_candidate_dict],
            customer_profile=sample_profile_dict,
        )
        assert len(v.passed_products) == 1

    def test_empty_passed_products_valid(self, sample_profile_dict):
        v = RankRequestValidator(
            passed_products=[],
            customer_profile=sample_profile_dict,
        )
        assert v.passed_products == []

    def test_missing_age_in_profile(self, sample_candidate_dict):
        from pydantic import ValidationError
        bad_profile = {"income": 1_500_000}  # missing age
        with pytest.raises(ValidationError):
            RankRequestValidator(
                passed_products=[sample_candidate_dict],
                customer_profile=bad_profile,
            )


class TestFormatValidationError:
    def test_returns_structured_dict(self, sample_profile_dict):
        from pydantic import ValidationError
        try:
            CustomerProfileValidator(**{**sample_profile_dict, "age": 5})
        except Exception as exc:
            result = format_validation_error(exc)
            assert result["error"] == "validation_error"
            assert isinstance(result["details"], list)
            assert len(result["details"]) >= 1
            assert "field" in result["details"][0]
            assert "message" in result["details"][0]

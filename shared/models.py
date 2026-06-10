"""
shared/models.py
Canonical data models for the InsureVoice pipeline.

All Cloud Functions, ingestion scripts, and tests import from here.
Aligns with enterprise tblProducts / M_PHUB_UINCHANGEPRODUCTS schema.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class HealthStatus(str, Enum):
    HEALTHY     = "healthy"
    PRE_EXISTING = "pre_existing"


class CoverageGoal(str, Enum):
    LIFE             = "life"
    HEALTH           = "health"
    CRITICAL_ILLNESS = "critical_illness"
    ACCIDENT         = "accident"
    INVESTMENT       = "investment"
    ENDOWMENT        = "endowment"


class ProductType(str, Enum):
    """Granular product type — aligns with vcProductType."""
    TERM_LIFE        = "term_life"
    HEALTH           = "health"
    ULIP             = "ulip"
    ENDOWMENT        = "endowment"
    CRITICAL_ILLNESS = "critical_illness"
    PENSION          = "pension"
    CHILD_PLAN       = "child_plan"


class PlanCategory(str, Enum):
    """Broad product category — aligns with VcPlanCategory."""
    PROTECTION       = "Protection"
    SAVINGS          = "Savings"
    RETIREMENT       = "Retirement"
    ULIP             = "ULIP"
    INVESTMENT       = "Investment"
    HEALTH_INSURANCE = "Health Insurance"
    CHILD            = "Child"
    CRITICAL_ILLNESS = "Critical Illness"


# ---------------------------------------------------------------------------
# CustomerProfile  (TASK-002)
# ---------------------------------------------------------------------------

@dataclass
class CustomerProfile:
    """
    Structured customer profile extracted from voice intake.
    Exists only within the Agent Builder session — never persisted (Constitution §V).
    """
    # Required
    age: int
    income: int                          # INR per annum; min 100_000
    smoker: bool
    health_status: HealthStatus
    coverage_goals: List[str]            # list of CoverageGoal values

    # Optional
    sum_need: Optional[int] = None       # INR; None = customer said "maximum"
    family_size: Optional[int] = None    # 1–10
    dependents: Optional[int] = None     # 0–9
    preferred_term_years: Optional[int] = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# InsuranceProduct  (TASK-003)
# Aligns with enterprise tblProducts + M_PHUB_UINCHANGEPRODUCTS
# ---------------------------------------------------------------------------

@dataclass
class InsuranceProduct:
    """
    Insurance product master record.
    Maps directly to the Elasticsearch insurance_products index document.
    """
    # Identity & codes (tblProducts)
    id: str                              # e.g. "TERM001" — ES keyword
    product_code: str                    # e.g. "FG_TERM_001" — vcFGProductCode
    name: str                            # Display name — vcProductTitle
    product_type: str                    # ProductType enum value — vcProductType
    plan_category: str                   # PlanCategory enum value — VcPlanCategory

    # Regulatory (M_PHUB_UINCHANGEPRODUCTS)
    uin: str                             # IRDAI Unique Identification Number — vcUIN

    # ELSER semantic field (primary search surface)
    description: str                     # 2–3 sentences; rich NL; semantic_text in ES

    # Marketing (tblProducts)
    key_feature: str                     # Short headline — vcKeyFeature
    sales_pitch: str                     # 1–2 sentence sales description — vcSalesPitch
    tags: List[str]                      # Search/filter keywords — vcTags

    # Rider info (optional — not all products have riders)
    rider_name: Optional[str]            # e.g. "Accidental Death Benefit" — vcRiderName
    rider_type: Optional[str]            # e.g. "Accidental" — vcRiderType

    # Eligibility constraints (consumed by compliance engine)
    min_age: int
    max_age: int
    smoker_eligible: bool
    min_income: int                      # INR
    max_sum_assured: int                 # INR
    medical_required_above: int          # INR sum_assured threshold
    exclusions: List[str]               # e.g. ["suicide_within_1yr"]

    # Premium (flat fields — NOT nested; aligns with ES mapping)
    premium_min_monthly: int             # INR
    premium_max_monthly: int             # INR

    # Lifecycle (btIsActive)
    is_active: bool = True

    # Premium simulation fields (Story 6 — simulate_premium Cloud Function)
    # Optional so existing code that constructs InsuranceProduct without them still works.
    base_rate_per_lakh: Optional[int] = None      # Annual base premium per ₹1 lakh sum assured
    age_bands: Optional[List[dict]] = None        # List of {min_age, max_age, loading_pct}
    smoker_loading_pct: Optional[int] = None      # Additional % loading for smokers
    frequency_multipliers: Optional[dict] = None  # {monthly, quarterly, semi_annual, annual}
    available_terms: Optional[List[int]] = None   # Policy terms in years supported by product
    return_rate: Optional[float] = None           # Expected annual return % (savings products only)

    # Additional catalog fields (generated by data/generate_products.py)
    benefits: Optional[List[str]] = None          # List of product benefit bullet points
    eligibility_summary: Optional[str] = None     # Human-readable eligibility summary string

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# CandidateProduct  (TASK-004)
# InsuranceProduct augmented with ELSER relevance score after search
# ---------------------------------------------------------------------------

@dataclass
class CandidateProduct(InsuranceProduct):
    """
    Product returned by the Elastic MCP search.
    Extends InsuranceProduct with the raw ELSER sparse vector score.
    Field named elser_score (not _score) to avoid Python private-name convention.
    """
    elser_score: float = 0.5             # Raw ELSER score; can be > 1.0; normalised before ranking

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# Compliance types  (TASK-005)
# ---------------------------------------------------------------------------

@dataclass
class RejectedProduct:
    """A product that failed one or more compliance rules."""
    product_id: str
    product_name: str
    reasons: List[str]                   # one entry per violated rule

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class ComplianceRequest:
    """Request body for POST /compliance_check."""
    candidate_products: List[dict]       # list of CandidateProduct dicts (include elser_score)
    customer_profile: dict               # CustomerProfile dict

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class ComplianceResponse:
    """
    Response body for POST /compliance_check (HTTP 200).

    Asymmetry note (Gap G8):
    - passed: full CandidateProduct dicts (including elser_score)
    - rejected: only product_id, product_name, reasons (RejectedProduct)
    The root agent uses product_name from RejectedProduct for voice explanations.
    """
    passed: List[dict]                   # full CandidateProduct dicts
    rejected: List[dict]                 # RejectedProduct dicts

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# Ranking types  (TASK-006)
# ---------------------------------------------------------------------------

@dataclass
class ScoreBreakdown:
    """Component scores contributing to the final suitability score."""
    elser_relevance: float               # normalised elser_score ∈ [0, 1]
    age_centrality: float                # ∈ [0, 1]
    income_fit: float                    # ∈ [0, 1]

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class RankedProduct:
    """A single entry in the top-3 ranked recommendation list."""
    rank: int
    product: dict                        # full CandidateProduct dict
    suitability_score: float             # ∈ [0, 1] after normalisation
    score_breakdown: ScoreBreakdown

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["score_breakdown"] = self.score_breakdown.to_dict()
        return d


@dataclass
class AuditTrail:
    """
    Audit record for every rank_products call (Constitution §IV).
    Logged to Cloud Logging; customer_profile_hash anonymises PII.
    """
    all_scored: List[dict]               # ALL passed products with scores (not just top-3)
    formula_weights: dict                # {"elser": 0.4, "age": 0.3, "income": 0.3}
    customer_profile_hash: str           # SHA-256 of sorted profile JSON (no PII)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class RankRequest:
    """Request body for POST /rank_products."""
    passed_products: List[dict]          # ComplianceResponse.passed
    customer_profile: dict               # CustomerProfile dict (needs age, income, sum_need)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class RankResponse:
    """Response body for POST /rank_products (HTTP 200)."""
    top3: List[dict]                     # list of RankedProduct dicts
    audit: AuditTrail

    def to_dict(self) -> dict:
        return {
            "top3": self.top3,
            "audit": self.audit.to_dict(),
        }

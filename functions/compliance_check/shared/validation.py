"""
shared/validation.py
Pydantic validators for Cloud Function entry points.

Used to validate incoming JSON payloads before processing.
Returns structured error details on ValidationError for HTTP 400 responses.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# CustomerProfile validator  (TASK-007)
# ---------------------------------------------------------------------------

VALID_COVERAGE_GOALS = {"life", "health", "critical_illness", "accident", "investment", "endowment"}
VALID_HEALTH_STATUSES = {"healthy", "pre_existing"}


class CustomerProfileValidator(BaseModel):
    """
    Validates a customer profile dict at Cloud Function entry points.
    Raises ValidationError (→ HTTP 400) on invalid input.
    """
    age: int = Field(..., ge=18, le=75, description="Customer age in years (18–75)")
    income: int = Field(..., ge=100_000, description="Annual income in INR (min ₹1L)")
    smoker: bool = Field(..., description="Whether the customer is a smoker")
    health_status: str = Field(..., description="'healthy' or 'pre_existing'")
    coverage_goals: List[str] = Field(..., min_length=1, description="At least one coverage goal required")
    sum_need: Optional[int] = Field(default=None, ge=0, description="Desired sum assured in INR")
    family_size: Optional[int] = Field(default=None, ge=1, le=10)
    dependents: Optional[int] = Field(default=None, ge=0, le=9)
    preferred_term_years: Optional[int] = Field(default=None, ge=1, le=40)

    @field_validator("health_status")
    @classmethod
    def validate_health_status(cls, v: str) -> str:
        if v not in VALID_HEALTH_STATUSES:
            raise ValueError(f"health_status must be one of {sorted(VALID_HEALTH_STATUSES)}, got '{v}'")
        return v

    @field_validator("coverage_goals")
    @classmethod
    def validate_coverage_goals(cls, v: List[str]) -> List[str]:
        invalid = [g for g in v if g not in VALID_COVERAGE_GOALS]
        if invalid:
            raise ValueError(f"Invalid coverage_goals: {invalid}. Must be subset of {sorted(VALID_COVERAGE_GOALS)}")
        if len(v) > 6:
            raise ValueError(f"coverage_goals may contain at most 6 entries, got {len(v)}")
        return v

    @model_validator(mode="after")
    def validate_sum_need_cap(self) -> "CustomerProfileValidator":
        if self.sum_need is not None and self.sum_need > self.income * 10:
            raise ValueError(
                f"sum_need ({self.sum_need:,}) exceeds 10× annual income ({self.income * 10:,}). "
                "This will cause all products to fail the INCOME_SUM_CAP compliance rule."
            )
        return self


# ---------------------------------------------------------------------------
# ComplianceRequest validator  (TASK-008)
# ---------------------------------------------------------------------------

class _ComplianceProfileValidator(BaseModel):
    """Minimal profile fields required by the compliance engine."""
    age: int = Field(..., ge=0, description="Customer age")
    income: int = Field(..., ge=0, description="Annual income in INR")
    smoker: bool = Field(..., description="Smoker status")
    health_status: str = Field(..., description="'healthy' or 'pre_existing'")
    sum_need: Optional[int] = Field(default=None, ge=0)

    @field_validator("health_status")
    @classmethod
    def validate_health_status(cls, v: str) -> str:
        if v not in VALID_HEALTH_STATUSES:
            raise ValueError(f"health_status must be one of {sorted(VALID_HEALTH_STATUSES)}, got '{v}'")
        return v


class ComplianceRequestValidator(BaseModel):
    """
    Validates the full POST /compliance_check request body.
    candidate_products may be an empty list (returns passed=[], rejected=[] immediately).
    """
    candidate_products: List[Dict[str, Any]] = Field(..., description="List of candidate product dicts")
    customer_profile: _ComplianceProfileValidator = Field(..., description="Customer profile")


# ---------------------------------------------------------------------------
# RankRequest validator  (TASK-009)
# ---------------------------------------------------------------------------

class _RankProfileValidator(BaseModel):
    """Minimal profile fields required by the ranking engine."""
    age: int = Field(..., ge=0, description="Customer age")
    income: int = Field(..., ge=0, description="Annual income in INR")
    sum_need: Optional[int] = Field(default=None, ge=0)


class RankRequestValidator(BaseModel):
    """
    Validates the full POST /rank_products request body.
    passed_products may be an empty list (returns top3=[] immediately).
    """
    passed_products: List[Dict[str, Any]] = Field(..., description="Compliance-passed product dicts")
    customer_profile: _RankProfileValidator = Field(..., description="Customer profile")


# ---------------------------------------------------------------------------
# Utility: format ValidationError for HTTP 400 response
# ---------------------------------------------------------------------------

def format_validation_error(exc: Exception) -> dict:
    """
    Convert a Pydantic ValidationError into a structured HTTP 400 response body.
    Returns: {"error": "validation_error", "details": [{"field": ..., "message": ...}]}
    """
    errors = []
    if hasattr(exc, "errors"):
        for e in exc.errors():
            field_path = ".".join(str(loc) for loc in e.get("loc", []))
            errors.append({"field": field_path, "message": e.get("msg", str(e))})
    return {"error": "validation_error", "details": errors}

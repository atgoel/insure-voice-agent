"""
compliance_check — Cloud Function
Guardrail: deterministic eligibility rule engine.

Endpoint: POST /compliance_check
Input:  { "candidate_products": [...], "customer_profile": {...} }
Output: { "passed": [...], "rejected": [{"product_id", "product_name", "reasons": [...]}] }

Response shape (G8 asymmetry — Constitution §IV):
  passed[]   — full product dicts (all InsuranceProduct fields intact for ranking)
  rejected[] — {"product_id", "product_name", "reasons"} only; root agent uses
               product_name for voice explanation of why a product was excluded.

Constitution constraints (non-negotiable):
  §I  Compliance-first: every product_id in candidates is either in passed or rejected.
  §II Zero hallucination: all rules are pure Python predicates — no LLM, no ML.
  §IV Audit trail: a Cloud Logging record is emitted after every evaluation.
"""
import json
import sys

import functions_framework
from pydantic import ValidationError

# shared/ is co-deployed alongside this function (see infra/cloudbuild.yaml TASK-051)
sys.path.insert(0, ".")
from shared.validation import ComplianceRequestValidator  # noqa: E402


# ---------------------------------------------------------------------------
# Deterministic eligibility rules (§II — NO LLM, NO ML, NO external calls)
# ---------------------------------------------------------------------------

COMPLIANCE_RULES = [
    {
        "rule_id": "AGE_MIN",
        "check":  lambda p, c: c["age"] >= p["min_age"],
        "reason": lambda p, c: f"Minimum entry age is {p['min_age']}; customer is {c['age']}",
    },
    {
        "rule_id": "AGE_MAX",
        "check":  lambda p, c: c["age"] <= p["max_age"],
        "reason": lambda p, c: f"Maximum entry age is {p['max_age']}; customer is {c['age']}",
    },
    {
        "rule_id": "SMOKER_EXCLUSION",
        "check":  lambda p, c: not (c.get("smoker", False) and not p.get("smoker_eligible", True)),
        "reason": lambda p, c: "Product not available for smokers",
    },
    {
        "rule_id": "INCOME_SUM_CAP",
        # sum_need may be None (Pydantic serialises absent Optional[int] as None),
        # so use `or 0` to treat both missing and None as 0.
        "check":  lambda p, c: (c.get("sum_need") or 0) <= (c.get("income") or 0) * 10,
        "reason": lambda p, c: "Requested sum assured exceeds 10x annual income cap",
    },
    {
        "rule_id": "MEDICAL_EXAM_REQUIRED",
        # Same None-safety: absent/None sum_need → 0, never exceeds medical_required_above.
        "check":  lambda p, c: not (
            (c.get("sum_need") or 0) > p.get("medical_required_above", float("inf"))
            and c.get("health_status", "healthy") != "healthy"
        ),
        "reason": lambda p, c: "Medical exam required for this sum assured with declared health conditions",
    },
]


# ---------------------------------------------------------------------------
# Cloud Function handler
# ---------------------------------------------------------------------------

@functions_framework.http
def compliance_check(request):
    """Evaluate eligibility rules for each candidate product against a customer profile.

    Returns HTTP 400 on invalid input (missing/wrong-type fields).
    Returns HTTP 200 with {passed, rejected} on success — even when all products fail.

    Response asymmetry (G8): passed[] contains full product dicts; rejected[] contains
    only {product_id, product_name, reasons} so the root agent can name the excluded
    product in the voice response without needing the full object.
    """
    # --- Input validation (TASK-028) ----------------------------------------
    data = request.get_json(silent=True)
    if data is None:
        return (
            json.dumps({"error": "validation_error", "detail": "Request body must be JSON"}),
            400,
            {"Content-Type": "application/json"},
        )

    try:
        validated = ComplianceRequestValidator.model_validate(data)
    except ValidationError as exc:
        fields = [".".join(str(loc) for loc in err["loc"]) for err in exc.errors()]
        return (
            json.dumps({"error": "validation_error", "fields": fields}),
            400,
            {"Content-Type": "application/json"},
        )

    candidates = validated.candidate_products
    profile = validated.customer_profile.model_dump()

    # --- Empty candidates guard (TASK-029) ----------------------------------
    if not candidates:
        return (
            json.dumps({"passed": [], "rejected": []}),
            200,
            {"Content-Type": "application/json"},
        )

    # --- Rule evaluation (§II deterministic) --------------------------------
    passed = []
    rejected = []

    for product in candidates:
        violations = []
        for rule in COMPLIANCE_RULES:
            if not rule["check"](product, profile):
                violations.append(rule["reason"](product, profile))
        if violations:
            rejected.append({
                "product_id":   product["id"],
                "product_name": product["name"],
                "reasons":      violations,
            })
        else:
            passed.append(product)

    # --- Audit trail (TASK-052 / Constitution §IV) --------------------------
    # Cloud Logging captures stdout from Cloud Run; print() → structured log.
    print(json.dumps({
        "event":        "compliance_check",
        "passed_ids":   [p["id"] for p in passed],
        "rejected_ids": [r["product_id"] for r in rejected],
    }))

    return json.dumps({"passed": passed, "rejected": rejected}), 200, {"Content-Type": "application/json"}

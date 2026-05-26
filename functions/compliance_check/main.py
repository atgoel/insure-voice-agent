"""
compliance_check — Cloud Function
Guardrail: deterministic eligibility rule engine.

Endpoint: POST /compliance_check
Input:  { "candidate_products": [...], "customer_profile": {...} }
Output: { "passed": [...], "rejected": [{"product_id", "product_name", "reason"}] }
"""
import json
import functions_framework


COMPLIANCE_RULES = [
    {
        "rule_id": "AGE_MIN",
        "check": lambda p, c: c["age"] >= p["min_age"],
        "reason": lambda p, c: f"Minimum entry age is {p['min_age']}; customer is {c['age']}"
    },
    {
        "rule_id": "AGE_MAX",
        "check": lambda p, c: c["age"] <= p["max_age"],
        "reason": lambda p, c: f"Maximum entry age is {p['max_age']}; customer is {c['age']}"
    },
    {
        "rule_id": "SMOKER_EXCLUSION",
        "check": lambda p, c: not (c.get("smoker", False) and not p.get("smoker_eligible", True)),
        "reason": lambda p, c: "Product not available for smokers"
    },
    {
        "rule_id": "INCOME_SUM_CAP",
        "check": lambda p, c: c.get("sum_need", 0) <= c.get("income", 0) * 10,
        "reason": lambda p, c: f"Requested sum assured exceeds 10x annual income cap"
    },
    {
        "rule_id": "MEDICAL_EXAM_REQUIRED",
        "check": lambda p, c: not (
            c.get("sum_need", 0) > p.get("medical_required_above", float("inf"))
            and c.get("health_status", "healthy") != "healthy"
        ),
        "reason": lambda p, c: "Medical exam required for this sum assured with declared health conditions"
    },
]


@functions_framework.http
def compliance_check(request):
    data = request.get_json()
    candidates = data.get("candidate_products", [])
    profile = data.get("customer_profile", {})

    passed = []
    rejected = []

    for product in candidates:
        violations = []
        for rule in COMPLIANCE_RULES:
            if not rule["check"](product, profile):
                violations.append(rule["reason"](product, profile))
        if violations:
            rejected.append({
                "product_id": product["id"],
                "product_name": product["name"],
                "reasons": violations
            })
        else:
            passed.append(product)

    return json.dumps({"passed": passed, "rejected": rejected}), 200, {"Content-Type": "application/json"}

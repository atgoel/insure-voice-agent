# Feature Specification: Compliance Guardrail Engine

**Feature Directory**: `specs/003-compliance-guardrail-engine/`
**Created**: 2026-05-26
**Status**: Draft

## Overview

The `compliance_check` Cloud Function is the deterministic eligibility rule engine. It receives a list of candidate products from ELSER search and a customer profile, evaluates each product against a set of hardcoded eligibility rules, and returns two lists: products that passed all rules and products that were rejected (with reasons). This is the critical compliance gate — no rejected product may ever appear in the final recommendation. The function uses zero LLM calls.

---

## User Stories & Acceptance Criteria

### Story 1 — Core Eligibility Rule Engine (Priority: P1)

All 5 core compliance rules are implemented and the function correctly classifies products as passed or rejected. Each rejection includes a human-readable reason.

**Why P1**: This is the single most important non-negotiable in the constitution. The hackathon demo must show at least one product being rejected with a visible reason.

**Independent Test**: POST to the function with a test profile (age=70, non-smoker, ₹5L income) and a product with max_age=65 — verify the product appears in `rejected` with reason containing "Maximum entry age is 65; customer is 70".

**Acceptance Scenarios**:

1. **Given** a product with `min_age=25, max_age=55` and a customer aged 60, **When** `compliance_check` runs, **Then** the product is in `rejected` with reason "Maximum entry age is 55; customer is 60".
2. **Given** a product with `smoker_eligible=false` and a customer who is a smoker, **When** `compliance_check` runs, **Then** the product is in `rejected` with reason "Product not available for smokers".
3. **Given** a customer with `sum_need=₹5Cr` and `income=₹3L` (sum_need > 10× income), **When** `compliance_check` runs, **Then** all products are rejected with reason "Requested sum assured exceeds 10x annual income cap".
4. **Given** a product requiring medical exam above ₹50L and a customer with `health_status=pre_existing` and `sum_need=₹1Cr`, **When** `compliance_check` runs, **Then** the product is rejected with reason "Medical exam required for this sum assured with declared health conditions".
5. **Given** a customer profile that satisfies all rules for a product, **When** `compliance_check` runs, **Then** the product is in `passed` with no rejection reason.

---

### Story 2 — All-Products-Rejected Handling (Priority: P1)

When all candidate products are rejected, the function still returns a well-formed response. The Root Agent handles this gracefully in the user-facing response.

**Why P1**: Demo quality requires the system to behave sensibly even in edge cases. Showing a graceful rejection with clear reasoning is a demo strength.

**Acceptance Scenarios**:

1. **Given** all candidate products fail compliance checks, **When** `compliance_check` returns, **Then** the response is `{"passed": [], "rejected": [...all products with reasons...]}` — not an error.
2. **Given** the Root Agent receives `passed: []` from compliance, **When** formulating the response, **Then** it explains to the customer which constraints blocked recommendations and asks if they want to adjust their profile.

---

### Story 3 — Rule Extensibility Without Regression (Priority: P3)

Adding a new rule to `COMPLIANCE_RULES` does not break existing passing products. New rules can be added by appending to the list.

**Acceptance Scenarios**:

1. **Given** 5 existing rules, **When** a 6th rule is added to `COMPLIANCE_RULES`, **Then** all existing pytest test cases still pass.
2. **Given** a product that should fail only the new rule, **When** tested, **Then** it appears in `rejected` with only the new rule's reason.

---

## Compliance Rules (Current Set)

| Rule ID | Logic | Rejection Reason Template |
|---|---|---|
| `AGE_MIN` | `customer.age >= product.min_age` | `"Minimum entry age is {min_age}; customer is {age}"` |
| `AGE_MAX` | `customer.age <= product.max_age` | `"Maximum entry age is {max_age}; customer is {age}"` |
| `SMOKER_EXCLUSION` | `not (customer.smoker and not product.smoker_eligible)` | `"Product not available for smokers"` |
| `INCOME_SUM_CAP` | `customer.sum_need <= customer.income * 10` | `"Requested sum assured exceeds 10x annual income cap"` |
| `MEDICAL_EXAM_REQUIRED` | `not (sum_need > product.medical_required_above and health != 'healthy')` | `"Medical exam required for this sum assured with declared health conditions"` |
| `INCOME_MIN` | `customer.income >= product.min_income` | `"Minimum income requirement is ₹{min_income:,}; customer income is ₹{income:,}"` |

---

## API Contract

**Endpoint**: `POST /compliance_check`

**Request**:
```json
{
  "candidate_products": [
    {
      "id": "string",
      "name": "string",
      "min_age": 18,
      "max_age": 65,
      "smoker_eligible": true,
      "min_income": 300000,
      "medical_required_above": 5000000
    }
  ],
  "customer_profile": {
    "age": 38,
    "income": 1500000,
    "smoker": false,
    "health_status": "healthy",
    "sum_need": 10000000
  }
}
```

**Response**:
```json
{
  "passed": [{ "id": "...", "name": "...", "..." : "..." }],
  "rejected": [
    { "product_id": "...", "product_name": "...", "reasons": ["..."] }
  ]
}
```

---

## Edge Cases

- `candidate_products` is empty → return `{"passed": [], "rejected": []}` immediately.
- `customer_profile` missing required fields (age, income, smoker, health_status) → return HTTP 400 with field-level validation error.
- `sum_need` not provided → treat as 0 (skip INCOME_SUM_CAP and MEDICAL_EXAM_REQUIRED rules).
- Product missing `medical_required_above` → treat as `float('inf')` (rule never triggers).
- Multiple rule violations for one product → include all reasons in `reasons` list.

---

## Out of Scope

- IRDAI regulation cross-check (Phase 2).
- Dynamic rule loading from database or configuration file.
- Any LLM call within this function.
- Fraud detection.

---

## Technical Notes

- Existing implementation: `functions/compliance_check/main.py` — fully implemented. Core rules, input validation, audit trail, and tests are complete.
- Framework: `functions-framework` (`@functions_framework.http` decorator).
- Tests: `tests/test_compliance_check.py` using pytest.
- Input validation is implemented at the function entry point via `ComplianceRequestValidator` (Pydantic); returns HTTP 400 with field-level error detail on invalid input.
- HTTP 400 with descriptive JSON error for invalid input.

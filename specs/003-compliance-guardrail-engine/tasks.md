# Tasks: Compliance Guardrail Engine

**Spec**: specs/003-compliance-guardrail-engine/spec.md | **Plan**: specs/003-compliance-guardrail-engine/plan.md

---

## Phase 1 — Verification & Setup
**Goal**: Confirm the existing implementation is correct and deployment-ready before any new work.

- [x] TASK-001 · [setup] · Verify `COMPLIANCE_RULES` list in `main.py` contains all 5 rules (AGE_MIN, AGE_MAX, SMOKER_EXCLUSION, INCOME_SUM_CAP, MEDICAL_EXAM_REQUIRED) — `functions/compliance_check/main.py`
- [x] TASK-002 · [setup] · Verify `cloudbuild.yaml` Step 3a (`copy-shared-compliance`) copies `shared/` before deploy and that `deploy-compliance-check` has `waitFor: [copy-shared-compliance]` — `infra/cloudbuild.yaml`
- [x] TASK-003 · [setup] · Verify `root_agent_prompt.md` Guardrails section explicitly handles `passed: []` with constraint explanation and profile clarification prompt — `agent_builder/root_agent_prompt.md`
- [x] TASK-004 · [test] · Run full pytest suite locally and confirm all 14 tests in `test_compliance_check.py` pass with zero failures — `tests/test_compliance_check.py`
- [x] TASK-005 · [setup] · Confirm `functions/compliance_check/requirements.txt` pins `functions-framework==3.*` and `pydantic>=2.0` with no missing transitive deps — `functions/compliance_check/requirements.txt`

---

## Phase 2 — Core Eligibility Rule Engine (P1 — Story 1)
**Goal**: All 5 compliance rules fire correctly, each rejection includes the exact human-readable reason from the spec, and every candidate appears in exactly one of `passed` or `rejected`.

**Independent Test**: `POST /compliance_check` with `age=70`, product `max_age=65` → product in `rejected` with `reasons: ["Maximum entry age is 65; customer is 70"]`.

- [x] TASK-010 · [feat] · Implement `AGE_MIN` rule: `customer.age >= product.min_age`, rejection message matches spec template — `functions/compliance_check/main.py`
- [x] TASK-011 · [feat] · Implement `AGE_MAX` rule: `customer.age <= product.max_age`, rejection message matches spec template — `functions/compliance_check/main.py`
- [x] TASK-012 · [feat] · Implement `SMOKER_EXCLUSION` rule: reject when `customer.smoker=True` and `product.smoker_eligible=False` — `functions/compliance_check/main.py`
- [x] TASK-013 · [feat] · Implement `INCOME_SUM_CAP` rule: `sum_need <= income * 10`; treat absent/None `sum_need` as 0 — `functions/compliance_check/main.py`
- [x] TASK-014 · [feat] · Implement `MEDICAL_EXAM_REQUIRED` rule: reject when `sum_need > product.medical_required_above` and `health_status != 'healthy'`; treat absent `medical_required_above` as `float('inf')` — `functions/compliance_check/main.py`
- [x] TASK-015 · [feat] · Implement multi-rule violation accumulation: a single product that fails multiple rules has all reasons in its `reasons` list — `functions/compliance_check/main.py`
- [x] TASK-016 · [feat] · Implement response asymmetry (G8): `passed[]` returns full product dicts; `rejected[]` returns `{product_id, product_name, reasons}` only — `functions/compliance_check/main.py`
- [x] TASK-017 · [feat] · Implement input validation via `ComplianceRequestValidator` (Pydantic); return HTTP 400 with `{"error": "validation_error", "fields": [...]}` on invalid payload — `functions/compliance_check/main.py`
- [x] TASK-018 · [feat] · Implement empty candidates guard: return `{"passed": [], "rejected": []}` immediately when `candidate_products` is empty — `functions/compliance_check/main.py`
- [x] TASK-019 · [feat] · Implement audit trail: emit `print(json.dumps({event, passed_ids, rejected_ids}))` after every evaluation — `functions/compliance_check/main.py`
- [x] TASK-020 · [test] · Confirm pytest test class `TestComplianceRules` covers all 5 rule scenarios and all 3 validation scenarios (missing age → 400, missing profile → 400, empty candidates → 200) — `tests/test_compliance_check.py`
- [x] TASK-021 · [test] · Run `pytest tests/test_compliance_check.py -v` and attach output confirming 14/14 pass — `tests/test_compliance_check.py`

---

## Phase 3 — All-Products-Rejected Handling (P1 — Story 2)
**Goal**: When `passed: []`, the Root Agent explains which constraints blocked recommendations and prompts the customer to clarify their profile — not an error state.

- [x] TASK-030 · [feat] · Confirm `root_agent_prompt.md` Guardrails section includes: "If ALL products are rejected, explain what constraints blocked them and ask the customer to clarify their profile" — `agent_builder/root_agent_prompt.md`
- [x] TASK-031 · [feat] · Add an explicit **voice script example** to `root_agent_prompt.md` showing the all-rejected response pattern: name the blocking constraints, do not suggest non-eligible products, invite profile adjustment — `agent_builder/root_agent_prompt.md`
- [x] TASK-032 · [test] · Add pytest test `test_all_products_rejected_response_shape`: send 3 products all failing AGE_MAX, assert `passed=[]` and `rejected` has 3 entries each with non-empty `reasons` — `tests/test_compliance_check.py`
- [x] TASK-033 · [test] · Add pytest test `test_mixed_pass_and_reject`: 1 product passes, 2 fail different rules — assert `passed` has 1 entry (full dict) and `rejected` has 2 with correct reasons — `tests/test_compliance_check.py`
- [x] TASK-034 · [test] · Add pytest test `test_multi_violation_accumulation`: send 1 product failing both `AGE_MAX` and `SMOKER_EXCLUSION` — assert `len(reasons) == 2` and both reason strings present in `rejected[0]["reasons"]` — `tests/test_compliance_check.py`

---

## Phase 4 — Rule Extensibility Without Regression (P3 — Story 3)
**Goal**: A 6th rule can be appended to `COMPLIANCE_RULES` without modifying existing rule logic or breaking any existing test.

- [x] TASK-040 · [feat] · Add a `MIN_INCOME` rule (rule_id `INCOME_MIN`): `customer.income >= product.min_income`; rejection reason `"Minimum income requirement is ₹{min_income:,}; customer income is ₹{income:,}"` — `functions/compliance_check/main.py`
- [x] TASK-041 · [test] · Write pytest test `test_min_income_rule_rejected`: profile with `income=200_000`, product `min_income=300_000` → product in `rejected` with correct reason string AND `len(reasons) == 1` (confirming no other rules fired) — `tests/test_compliance_check.py`
- [x] TASK-042 · [test] · Write pytest test `test_new_rule_no_regression`: re-run all Phase 2 test scenarios after adding `INCOME_MIN` and confirm all still pass (no previously-passing product is newly rejected) — `tests/test_compliance_check.py`

---

## Phase 5 — Deployment Verification & Smoke Test
**Goal**: The function is live on Cloud Run and correctly rejects an age-ineligible product via a real HTTP call.

- [x] TASK-050 · [infra] · Trigger `gcloud builds submit` with `cloudbuild.yaml`; verify Step 3a (`copy-shared-compliance`) and Step 3b (`deploy-compliance-check`) complete with exit 0 — `infra/cloudbuild.yaml`
- [x] TASK-051 · [infra] · Capture the deployed `compliance_check` Cloud Function URL from build output and store in `.env.local` as `COMPLIANCE_CHECK_URL` — `infra/cloudbuild.yaml`
- [x] TASK-052 · [test] · Run smoke test: `curl -X POST $COMPLIANCE_CHECK_URL` with `age=70`, `max_age=65` product — assert HTTP 200, product in `rejected`, reason contains "Maximum entry age is 65; customer is 70" — `tests/smoke_test_live.py`
- [x] TASK-053 · [test] · Run smoke test: `curl -X POST $COMPLIANCE_CHECK_URL` with `smoker=true`, `smoker_eligible=false` product — assert product in `rejected`, reason is "Product not available for smokers" — `tests/smoke_test_live.py`
- [x] TASK-054 · [test] · Run smoke test: all-rejected scenario (`age=70`, all products `max_age=50`) — assert `passed=[]` and HTTP 200 (not 4xx/5xx) — `tests/smoke_test_live.py`
- [x] TASK-055 · [infra] · Verify Cloud Logging shows `compliance_check` audit events (`passed_ids`, `rejected_ids`) for the smoke test calls — GCP Console Log Explorer

---

## Dependencies

- Phase 2 tasks are all marked done — no blockers for Phase 3
- Phase 3 requires Phase 2 complete (TASK-010 through TASK-021)
- Phase 4 (Story 3 / P3) requires Phase 2 complete; independent of Phase 3
- Phase 5 requires Phase 3 complete AND `cloudbuild.yaml` build succeeds (TASK-050)
- TASK-052 through TASK-055 require TASK-051 (live URL available)

---

## MVP Scope

**Minimum shippable for hackathon demo**: Phase 1 + Phase 2 + Phase 3 (TASK-001–TASK-033) + Phase 5 smoke tests (TASK-052–TASK-054).

Phase 4 (`INCOME_MIN` rule extensibility) is P3 — add only after demo is validated end-to-end.

---

## Task Summary

| Phase | Tasks | Status |
|---|---|---|
| Phase 1 — Verification & Setup | TASK-001–005 | 5 done |
| Phase 2 — Core Rule Engine (P1) | TASK-010–021 | 12 done |
| Phase 3 — All-Rejected Handling (P1) | TASK-030–034 | 5 done |
| Phase 4 — Extensibility (P3) | TASK-040–042 | 3 done |
| Phase 5 — Deployment & Smoke Tests | TASK-050–055 | 6 done |
| **Total** | **22 tasks** | **22/22 done ✅** |

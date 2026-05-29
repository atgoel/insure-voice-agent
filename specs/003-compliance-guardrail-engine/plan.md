# Implementation Plan: Compliance Guardrail Engine

**Spec**: specs/003-compliance-guardrail-engine/spec.md | **Date**: 2026-05-29

## Summary

The `compliance_check` Cloud Function is a deterministic rule engine that gates every product recommendation — no LLM, no ML, no external calls. The core implementation in `functions/compliance_check/main.py` is **substantially complete**: all 5 rules are coded, input validation via Pydantic is wired, the audit trail emits to Cloud Logging, and 14 pytest cases cover every acceptance scenario. The plan focuses on verifying the deployment integration, confirming the `shared/` co-deployment path, and closing the one missing story (root-agent graceful handling of `passed: []`).

---

## Technical Context

| Field | Value |
|---|---|
| Language | Python 3.11 |
| Key Libraries | `functions-framework==3.*`, `pydantic>=2.0` |
| GCP Services | Cloud Functions 2nd gen (HTTP trigger), Cloud Logging |
| Elastic | None — compliance is pure Python, no search calls |
| Testing | pytest, direct function call (mock `flask.Request`) |
| Deployment | Cloud Build → Cloud Run (same `cloudbuild.yaml` as other functions) |

---

## Architecture

```
Root Agent (ADK)
    │
    │  POST /compliance_check
    │  { candidate_products: [...], customer_profile: {...} }
    ▼
compliance_check Cloud Function
    │
    ├── ComplianceRequestValidator (Pydantic)  ←── HTTP 400 on bad input
    │
    ├── Empty candidates guard  ──────────────  HTTP 200 { passed:[], rejected:[] }
    │
    ├── COMPLIANCE_RULES loop (pure Python λ)
    │     AGE_MIN / AGE_MAX / SMOKER_EXCLUSION / INCOME_SUM_CAP / MEDICAL_EXAM_REQUIRED
    │
    ├── Cloud Logging audit record (stdout → structured log)
    │
    └── HTTP 200 { passed: [full dicts], rejected: [{product_id, product_name, reasons}] }
                                │
                       Root Agent: passed → rank_products
                                   passed=[] → voice explanation to customer
```

**Data flow position**: Sub-Agent 1 (ELSER search) → **Sub-Agent 2 (this function)** → Sub-Agent 3 (rank_products).

**Latency budget**: < 0.5 s (Constitution §III). The function is pure in-process Python iteration over ≤ 10 products with 5 predicates — no I/O, easily within budget.

---

## File Structure

```text
functions/
  compliance_check/
    main.py                     ← EXISTS — core implementation complete
    requirements.txt            ← EXISTS — functions-framework==3.*, pydantic>=2.0
    shared/
      __init__.py               ← EXISTS
      models.py                 ← EXISTS — CustomerProfile, InsuranceProduct dataclasses
      validation.py             ← EXISTS — ComplianceRequestValidator, _ComplianceProfileValidator

tests/
  test_compliance_check.py      ← EXISTS — 14 test cases covering all acceptance scenarios

agent_builder/
  root_agent_prompt.md          ← MODIFY — add graceful handling instructions for passed=[]

infra/
  cloudbuild.yaml               ← VERIFY — shared/ copy step must precede compliance_check deploy
```

---

## Constitution Check

- [x] **Compliance guardrail respected** — `COMPLIANCE_RULES` loop runs over every candidate; a product ID must appear in exactly one of `passed` or `rejected`. No shortcut paths exist.
- [x] **Latency target honoured** — Pure in-memory Python loop, no I/O. Budget: < 0.5 s. No risk.
- [x] **No hallucination risk** — Zero LLM calls. All rules are `lambda p, c: <boolean expression>`. Constitution §II satisfied.
- [x] **Audit trail** — `print(json.dumps({...}))` on every evaluation. Cloud Logging captures stdout from Cloud Run. Product IDs of passed and rejected sets are recorded.
- [ ] **All-products-rejected voice response** — Root Agent handling for `passed: []` must be explicit in `root_agent_prompt.md` (Story 2, currently unverified).

---

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Rule representation | List of dicts with `check` λ and `reason` λ | Append-only extensibility (Story 3). New rules never touch existing rule logic. |
| Response asymmetry (G8) | `passed[]` = full product dicts; `rejected[]` = `{product_id, product_name, reasons}` | `rank_products` needs full dicts; root agent needs product names for voice explanation. |
| Pydantic model for profile validation | `_ComplianceProfileValidator` (no `coverage_goals`) vs `CustomerProfileValidator` (full) | Compliance only needs age/income/smoker/health_status/sum_need. Avoids requiring voice intake fields at compliance boundary. |
| `sum_need` default | `None` treated as `0` via `c.get("sum_need") or 0` | Absent sum_need should never trigger income/medical rules — safe default. |
| `medical_required_above` default | `float("inf")` when absent from product | Rule never fires if product omits the field — safe and permissive. |
| Determinism enforcement | No imports of `google.cloud.aiplatform`, `vertexai`, `elasticsearch` | Enforced at code level; CI will fail if these imports appear in `compliance_check/main.py`. |
| Audit via stdout | `print(json.dumps(...))` | Cloud Run forwards stdout to Cloud Logging automatically. Zero extra library dependency. |

---

## Implementation Status

| Task | Status | Notes |
|---|---|---|
| 5 compliance rules coded | **Done** | `COMPLIANCE_RULES` list in `main.py` |
| Input validation (HTTP 400) | **Done** | `ComplianceRequestValidator` via Pydantic |
| Empty candidates guard | **Done** | Early return before rule loop |
| Audit trail (Cloud Logging) | **Done** | `print(json.dumps(...))` stdout |
| Pydantic models (`shared/`) | **Done** | `models.py`, `validation.py` |
| Unit tests (14 cases) | **Done** | `tests/test_compliance_check.py` |
| Root Agent: `passed=[]` handling | **Pending** | `root_agent_prompt.md` needs explicit instructions |
| `cloudbuild.yaml` `shared/` copy step | **Verify** | Step must copy `shared/` into `functions/compliance_check/` before deploy |
| Smoke test (live endpoint) | **Pending** | Age=70 / max_age=65 rejection scenario against deployed URL |

---

## Open Questions

1. **`shared/` co-deployment** ✅ RESOLVED: `cloudbuild.yaml` Step 3a (`copy-shared-compliance`, `id: copy-shared-compliance`) runs `cp -r shared/ functions/compliance_check/shared/` and Step 3b has `waitFor: [copy-shared-compliance]`. The path is correct.

2. **`coverage_goals` in test profile** ✅ RESOLVED: `_ComplianceProfileValidator` (used at the compliance function entry point) does not declare `coverage_goals`. `CustomerProfileValidator` (used at voice intake) does. Test profiles in `test_compliance_check.py` use `_profile()` which omits `coverage_goals` — they correctly route through `_ComplianceProfileValidator` with no spurious 400s.

3. **Root Agent `passed=[]` prompt** ✅ RESOLVED: `root_agent_prompt.md` Guardrails section already contains: *"If ALL products are rejected, explain what constraints blocked them and ask the customer to clarify their profile."* Voice script example added via TASK-031.

4. **Dual-validator `sum_need` behaviour** (design note): `CustomerProfileValidator` (voice intake) raises HTTP 400 at validation time when `sum_need > income × 10` — it rejects the request before it reaches the compliance engine. `_ComplianceProfileValidator` (compliance function entry) intentionally omits this check — it accepts the profile and relies on the `INCOME_SUM_CAP` rule to reject the products. This split is by design: the compliance function must remain callable independently (e.g. in tests, direct API calls) with any valid-shaped profile, and the INCOME_SUM_CAP rule provides the same safety net. The two validators must never be swapped.

# Tasks: API Design & Data Model

**Spec**: specs/000-api-design-data-model/ | **Plan**: specs/000-api-design-data-model/plan.md

> These tasks are the **foundation layer**. All five feature specs (001–005) depend on this work. Do not begin any feature implementation until Phase 1–5 are complete.

---

## Phase 1 — Shared Module Scaffold

**Goal**: Create the `shared/` Python package that holds all canonical data models and Pydantic validators. Both Cloud Functions and test fixtures import from here.

- [x] TASK-001 · [setup] · Create `shared/__init__.py` (empty package marker) — `shared/__init__.py`
- [x] TASK-002 · [feat] · Implement `CustomerProfile`, `HealthStatus`, `CoverageGoal` dataclasses — `shared/models.py`
- [x] TASK-003 · [feat] · Implement `ProductType` enum and `InsuranceProduct` dataclass — `shared/models.py`
- [x] TASK-004 · [feat] · Implement `CandidateProduct` (extends `InsuranceProduct` with `elser_score: float = 0.5` field for raw ELSER sparse vector score; renamed from `_score` to avoid Python private-naming confusion) — `shared/models.py`
- [x] TASK-005 · [feat] · Implement `ComplianceRequest`, `ComplianceResponse`, `RejectedProduct` dataclasses — `shared/models.py`
- [x] TASK-006 · [feat] · Implement `RankRequest`, `RankResponse`, `RankedProduct`, `ScoreBreakdown`, `AuditTrail` dataclasses — `shared/models.py`
- [x] TASK-007 · [feat] · Implement `CustomerProfileValidator` Pydantic model (age 18–75, income ≥ 100_000, coverage_goals len ≥ 1) — `shared/validation.py`
- [x] TASK-008 · [feat] · Implement `ComplianceRequestValidator` Pydantic model (validates profile fields + candidate_products is list) — `shared/validation.py`
- [x] TASK-009 · [feat] · Implement `RankRequestValidator` Pydantic model (validates passed_products is list, profile.age and profile.income present) — `shared/validation.py`
- [x] TASK-010 · [test] · Write `tests/test_models.py` — round-trip serialisation for all dataclasses; verify field types and defaults — `tests/test_models.py`

**Phase 1 exit criterion**: `pytest tests/test_models.py` passes with all models correctly typed and serialisable.

---

## Phase 2 — Product Catalog Generation (Gap G1 — Blocker)

**Goal**: Populate `data/insurance_products.json` with 28 synthetic products conforming exactly to `InsuranceProduct` schema. This is the single biggest blocker — every downstream feature is broken without it.

**Independent Test**: Run `python data/generate_products.py --validate` and confirm 28 products, 7 types × 4 products each, all required fields present, no missing `description`.

- [x] TASK-011 · [setup] · Create `data/generate_products.py` script scaffold — `data/generate_products.py`
- [x] TASK-012 · [feat] · Generate 4 `term_life` products (IDs: TERM001–TERM004) with varied age ranges (18–65, 25–55, 18–75, 30–60), mixed smoker eligibility, rich ELSER-optimised descriptions — `data/generate_products.py`
- [x] TASK-013 · [feat] · Generate 4 `health` products (IDs: HLTH001–HLTH004) including family health, senior health, individual, and parents plan variants — `data/generate_products.py`
- [x] TASK-014 · [feat] · Generate 4 `ulip` products (IDs: ULIP001–ULIP004) with investment + protection descriptions, smoker_eligible=false, higher income floors (₹5L–₹10L) — `data/generate_products.py`
- [x] TASK-015 · [feat] · Generate 4 `endowment` products (IDs: ENDT001–ENDT004) with savings + maturity benefit descriptions — `data/generate_products.py`
- [x] TASK-016 · [feat] · Generate 4 `critical_illness` products (IDs: CRIT001–CRIT004) with CI-specific conditions listed in description (cancer, heart, stroke) — `data/generate_products.py`
- [x] TASK-017 · [feat] · Generate 4 `pension` products (IDs: PENS001–PENS004) with retirement/annuity descriptions, entry age 30+ — `data/generate_products.py`
- [x] TASK-018 · [feat] · Generate 4 `child_plan` products (IDs: CHLD001–CHLD004) with education/future fund descriptions, max parent entry age ≤ 55, smoker_eligible=false — `data/generate_products.py`
- [x] TASK-019 · [feat] · Add `--validate` flag to `generate_products.py` that loads each product through `InsuranceProduct` dataclass and asserts all 14 fields are present and correctly typed — `data/generate_products.py`
- [x] TASK-020 · [infra] · Run `generate_products.py` and write output to `data/insurance_products.json` — `data/insurance_products.json`
- [x] TASK-021 · [test] · Write `tests/test_insurance_products_data.py` — assert file loads as JSON list, len=28, all 7 product types present, every product has non-empty `description` ≥ 50 chars, no duplicate IDs — `tests/test_insurance_products_data.py`

**Phase 2 exit criterion**: `pytest tests/test_insurance_products_data.py` passes; `data/insurance_products.json` contains 28 products with all required fields.

---

## Phase 3 — Elasticsearch Index Schema Fix (Gap G3)

**Goal**: Align `create_index.py` with the authoritative index schema from the plan. Add ELSER endpoint readiness polling so the script doesn't silently fail on cold start.

**Independent Test**: Run `python ingest/create_index.py` against a live Elasticsearch Cloud cluster and verify index created with correct mappings via `GET insurance_products/_mapping`.

- [x] TASK-022 · [feat] · Update `create_index.py` — add `coverage_type` field (`keyword`) to index mapping (currently missing) — `ingest/create_index.py`
- [x] TASK-023 · [feat] · Update `create_index.py` — confirm `name` field uses `text` with `.keyword` sub-field (for BM25 `match` queries) — `ingest/create_index.py`
- [x] TASK-024 · [feat] · Add cluster readiness polling loop to `create_index.py`: poll `client.info()` every 5s for up to 30s before creating the index (adapted for Serverless — no inference endpoint setup required) — `ingest/create_index.py`
- [x] TASK-025 · [feat] · Add `--delete-existing` CLI flag to `create_index.py` to allow clean re-creation during development without editing the file — `ingest/create_index.py`
- [x] TASK-026 · [test] · Write `tests/test_create_index.py` — mock the `elasticsearch` client and assert: (a) cluster health checked before index creation; (b) index body contains all 21 expected fields; (c) `description` and `key_feature` have `type: semantic_text`; (d) `coverage_type` present — `tests/test_create_index.py`

**Phase 3 exit criterion**: `pytest tests/test_create_index.py` passes; mapping verified against authoritative schema.

---

## Phase 4 — Fix `compliance_check` Cloud Function (Gaps G4, G8)

**Goal**: Add Pydantic input validation so invalid requests return HTTP 400 instead of crashing. Document the response shape asymmetry (G8).

**Independent Test**: `POST /compliance_check` with missing `customer_profile.age` field → assert HTTP 400 with `{"error": "missing_required_fields", "fields": ["age"]}`.

- [x] TASK-027 · [feat] · Add `pydantic>=2.0` to `functions/compliance_check/requirements.txt` — `functions/compliance_check/requirements.txt`
- [x] TASK-028 · [feat] · Import and apply `ComplianceRequestValidator` at the top of `compliance_check()` handler; return HTTP 400 JSON on `ValidationError` with field names listed — `functions/compliance_check/main.py`
- [x] TASK-029 · [feat] · Add guard: if `candidate_products` is an empty list, return `{"passed": [], "rejected": []}` immediately without iterating — `functions/compliance_check/main.py`
- [x] TASK-030 · [test] · Verify existing `c.get("sum_need", 0)` default already handles absent `sum_need` correctly: add pytest case asserting a profile with no `sum_need` key passes both `INCOME_SUM_CAP` and `MEDICAL_EXAM_REQUIRED` rules — no code change needed — `tests/test_compliance_check.py`
- [x] TASK-031 · [feat] · Add a docstring comment to `compliance_check()` documenting the response asymmetry (G8): `passed` items include full product dict; `rejected` items include only `product_id`, `product_name`, `reasons` — `functions/compliance_check/main.py`
- [x] TASK-052 · [feat] · Add Cloud Logging audit record to `compliance_check()`: emit `print(json.dumps({"event": "compliance_check", "passed_ids": [...], "rejected_ids": [...]}))` after rule evaluation — satisfies Constitution §IV — `functions/compliance_check/main.py`
- [x] TASK-032 · [test] · Write `tests/test_compliance_check.py` — 5 rule tests (AGE_MIN, AGE_MAX, SMOKER_EXCLUSION, INCOME_SUM_CAP, MEDICAL_EXAM_REQUIRED) + 3 validation tests (missing age → 400, missing profile → 400, empty candidates → 200 with empty lists) — `tests/test_compliance_check.py`

**Phase 4 exit criterion**: `pytest tests/test_compliance_check.py` — all 9 test cases pass (5 rule + 3 validation + 1 sum_need guard).

---

## Phase 5 — Fix `rank_products` Cloud Function (Gaps G2, G5)

**Goal**: Fix `_score` normalisation so suitability scores are always in `[0,1]`. Add Pydantic validation. Add the `audit` field to the response.

**Independent Test**: `POST /rank_products` with 3 products where one has `_score=15.0` and two have `_score=7.5` — verify top-ranked product has `score_breakdown.elser_relevance=1.0` (normalised) and `suitability_score ≤ 1.0`.

- [x] TASK-033 · [feat] · Add `pydantic>=2.0` to `functions/rank_products/requirements.txt` — `functions/rank_products/requirements.txt`
- [x] TASK-034 · [feat] · Add `normalise_scores(products)` function: divides each `elser_score` by `max(elser_score)` across the batch; stores result in `elser_score_normalised`; handles all-zero and single-product edge cases — `functions/rank_products/main.py`
- [x] TASK-035 · [feat] · Update `score_product()` to use `elser_score_normalised` (not raw `elser_score`) for the `elser_relevance` component of the suitability formula — `functions/rank_products/main.py`
- [x] TASK-036 · [feat] · Import and apply `RankRequestValidator` at the top of `rank_products()` handler; return HTTP 400 on `ValidationError` — `functions/rank_products/main.py`
- [x] TASK-037 · [feat] · Add `audit` field to the response: `{"all_scored": [...all products with scores...], "formula_weights": {"elser": 0.4, "age": 0.3, "income": 0.3}, "customer_profile_hash": "<sha256>"}` — `functions/rank_products/main.py`
- [x] TASK-038 · [feat] · Implement `_profile_hash(profile: dict) -> str` using `hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()` — `functions/rank_products/main.py`
- [x] TASK-039 · [feat] · Add guard: if `passed_products` is empty list, return `{"top3": [], "audit": {"all_scored": [], ...}}` immediately — `functions/rank_products/main.py`
- [x] TASK-040 · [test] · Write `tests/test_rank_products.py` — scoring formula verification (known inputs → expected winner), normalisation test (raw score > 1.0 → normalised ≤ 1.0), audit trail present, empty input → 200 with empty top3, missing `age` → 400 — `tests/test_rank_products.py`

**Phase 5 exit criterion**: `pytest tests/test_rank_products.py` — all test cases pass; no `suitability_score > 1.0` produced.

---

## Phase 6 — Fix `tools.yaml` (Gap G6)

**Goal**: Add the Elastic MCP `elastic_product_search` tool to `agent_builder/tools.yaml` so Sub-Agent 1 has a registered tool to call.

- [x] TASK-041 · [feat] · Add `elastic_product_search` POST path to `agent_builder/tools.yaml` — operationId, request schema (query: string, customer_age: integer, relax_age_filter: boolean default false), response schema (candidates array, total_hits integer, fallback_triggered boolean) — `agent_builder/tools.yaml`
- [x] TASK-042 · [feat] · Add `400` and `500` response schemas to the existing `compliance_check` and `rank_products` paths in `tools.yaml` — `agent_builder/tools.yaml`
- [x] TASK-043 · [feat] · Add `audit` field to the `rank_products` 200 response schema in `tools.yaml` to match the updated function response — `agent_builder/tools.yaml`
- [x] TASK-044 · [docs] · Add a comment block at the top of `tools.yaml` documenting the three tools, their ownership (Elastic MCP vs Cloud Function), and the environment variables used for Cloud Function base URLs — `agent_builder/tools.yaml`
- [x] TASK-049 · [docs] · Update `specs/004-product-ranking-explainer/spec.md` — replace nested `premium_range: {min, max}` in the API Contract section with flat `premium_min_monthly` / `premium_max_monthly` fields to align with authoritative index schema (Gap G3 resolution) — `specs/004-product-ranking-explainer/spec.md`
- [x] TASK-050 · [test] · Validate `agent_builder/tools.yaml` passes OpenAPI 3.0 lint: run `openapi-spec-validator agent_builder/tools.yaml` and assert zero errors — `agent_builder/tools.yaml`

**Phase 6 exit criterion**: TASK-050 reports zero lint errors; all three tools defined with complete request/response schemas; `specs/004-product-ranking-explainer/spec.md` API contract uses flat premium fields.

---

## Phase 7 — Test Infrastructure

**Goal**: Create shared test fixtures and confirm the full test suite passes end-to-end before any feature implementation begins.

- [x] TASK-045 · [setup] · Create `tests/__init__.py` — `tests/__init__.py`
- [x] TASK-046 · [setup] · Create `tests/conftest.py` with pytest fixtures: `sample_customer_profile` (complete valid dict), `sample_products` (3 products from catalog), `sample_candidate_products` (same 3 with `elser_score` values 12.0, 7.5, 3.0), `passed_products_fixture`, `rejected_products_fixture` — `tests/conftest.py`
- [x] TASK-047 · [test] · Verify full test suite: `pytest tests/` — all phases 1–6 tests pass together with no import errors or fixture conflicts — `tests/`
- [x] TASK-048 · [docs] · Update `README.md` — add **Development Setup** section: environment variables (`ES_URL`, `ES_API_KEY`, `COMPLIANCE_CHECK_URL`, `RANK_PRODUCTS_URL`), `pip install -r requirements.txt`, `pytest tests/` command — `README.md`

**Phase 7 exit criterion**: `pytest tests/ -v` — green across all test files with no warnings about missing fixtures.

---

## Phase 8 — Infrastructure Fix (Cloud Build `shared/` Packaging)

**Goal**: Ensure the `shared/` module is available inside each Cloud Function's deployment package. Without this step both functions fail at runtime with `ModuleNotFoundError: No module named 'shared'` on first deploy.

- [x] TASK-051 · [infra] · Update `infra/cloudbuild.yaml` — add `cp -r shared/ functions/compliance_check/shared/` step before the compliance_check deploy step, and `cp -r shared/ functions/rank_products/shared/` step before the rank_products deploy step — `infra/cloudbuild.yaml`
- [ ] TASK-053 · [test] · Smoke test after deploy: send a valid `POST /compliance_check` payload to the deployed Cloud Run URL and assert HTTP 200 (not HTTP 500 from missing import) — `infra/cloudbuild.yaml`

**Phase 8 exit criterion**: Both Cloud Functions respond HTTP 200 to valid payloads after deployment via the updated `cloudbuild.yaml`.

---

## Dependencies

```
Phase 1 (shared models) ──────────────────────────────────────────────────────┐
    │                                                                          │
    ├──► Phase 2 (catalog) ──► Phase 3 (index schema)                        │
    │                                                                          │
    ├──► Phase 4 (compliance fix) ◄── requires shared/validation.py           │
    │                                                                          │
    ├──► Phase 5 (ranking fix) ◄───── requires shared/validation.py           │
    │                                                                          │
    └──► Phase 6 (tools.yaml) ◄────── no code dependency; can run in parallel │
                                                                               │
Phase 7 (test infra) ◄─────────────────────────────────────────── requires all┘
```

Specific task dependencies:
- TASK-019 (catalog validation) requires TASK-002, TASK-003 (InsuranceProduct dataclass)
- TASK-028 (compliance validation) requires TASK-008 (ComplianceRequestValidator)
- TASK-036 (rank validation) requires TASK-009 (RankRequestValidator)
- TASK-046 (conftest fixtures) requires TASK-020 (populated catalog file)
- TASK-047 (full suite) requires all previous tasks complete
- TASK-050 (OpenAPI lint) requires TASK-041 (elastic_product_search tool added to tools.yaml)
- TASK-051 (cloudbuild.yaml) requires Phase 1 complete (shared/ module exists to copy)
- TASK-053 (smoke test) requires TASK-051 and both Cloud Functions successfully deployed

---

## MVP Scope

**Minimum shippable** (unblocks all 5 feature spec implementations):

| Phase | Tasks | Required for MVP? |
|---|---|---|
| Phase 1 — Shared Models | TASK-001–010 | **Yes** |
| Phase 2 — Product Catalog | TASK-011–021 | **Yes — blocker** |
| Phase 3 — Index Schema | TASK-022–026 | **Yes** |
| Phase 4 — Compliance Fix | TASK-027–032, TASK-052 | **Yes** |
| Phase 5 — Ranking Fix | TASK-033–040 | **Yes** |
| Phase 6 — tools.yaml + Spec Fix | TASK-041–044, TASK-049, TASK-050 | Yes (Sub-Agent 1 broken without TASK-041; spec 004 alignment required) |
| Phase 7 — Test Infra | TASK-045–048 | TASK-045, TASK-046, TASK-047 required; TASK-048 optional |
| Phase 8 — Cloud Build | TASK-051, TASK-053 | **Yes** (deployment broken without TASK-051) |

**Total tasks: 53** | Phase 1: 10 | Phase 2: 11 | Phase 3: 5 | Phase 4: 7 | Phase 5: 8 | Phase 6: 6 | Phase 7: 4 | Phase 8: 2

All 53 tasks are prerequisite work. There is no subset that can be skipped safely — G1 (empty catalog) alone breaks every feature spec, and Phase 8 (TASK-051) must complete before any hackathon deployment attempt.

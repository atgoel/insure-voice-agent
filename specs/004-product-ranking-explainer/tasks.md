# Tasks: Product Ranking & Recommendation Explainer

**Spec**: specs/004-product-ranking-explainer/spec.md | **Plan**: specs/004-product-ranking-explainer/plan.md

> Task IDs start at TASK-060 to avoid collision with TASK-034–TASK-040 already embedded as
> inline comments in `functions/rank_products/main.py`, and TASK-001–TASK-055 used by
> features 002 and 003.

---

## Phase 1 — Verification & Setup

**Goal**: Confirm the core Cloud Function, validators, and test suite are wired correctly before adding new work.

- [x] TASK-060 · [setup] · Read `functions/rank_products/main.py` end-to-end; confirm `normalise_scores`, `score_product`, `_profile_hash`, HTTP handler, empty guard, and top-3 builder are all present — `functions/rank_products/main.py`
- [x] TASK-061 · [setup] · Confirm `shared/validation.py` exports `RankRequestValidator` with `passed_products` (list) and `_RankProfileValidator` fields `age`, `income`, `sum_need` — `shared/validation.py`
- [x] TASK-062 · [setup] · Confirm `agent_builder/tools.yaml` `/rank_products` path has required fields `passed_products` and `customer_profile` and a 200 response schema — `agent_builder/tools.yaml`
- [x] TASK-063 · [test] · Run `pytest tests/test_rank_products.py -v` locally; confirm all existing tests pass with zero failures — `tests/test_rank_products.py`

---

## Phase 2 — Multi-Factor Suitability Scoring (P1 — Story 1)

**Goal**: Scoring formula is deterministic, all edge cases handled without errors, and full pytest coverage for the Cloud Function.

**Independent Test**: `POST /rank_products` with 5 products of known attributes and a profile with `age=38`, `income=1_500_000`, `sum_need=10_000_000`; verify ranked #1 matches manual formula calculation.

> Core scoring (TASK-034 through TASK-040) is already implemented. This phase adds the missing edge-case tests.

- [x] TASK-034 · [feat] · `normalise_scores()`: divides by batch max, falls back to `1.0` when all-zero or single product — `functions/rank_products/main.py`
- [x] TASK-035 · [feat] · `score_product()`: `suitability = elser_norm×0.4 + age_centrality×0.3 + income_fit×0.3`; clamped to `[0, 1]`; returns `score_breakdown` dict — `functions/rank_products/main.py`
- [x] TASK-036 · [feat] · HTTP input validation via `RankRequestValidator`; returns HTTP 400 with `{"error": "validation_error", "fields": [...]}` on invalid payload — `functions/rank_products/main.py`
- [x] TASK-037 · [feat] · Audit trail in every 200 response: `audit.all_scored` (all products, desc), `audit.formula_weights`, `audit.customer_profile_hash` — `functions/rank_products/main.py`
- [x] TASK-038 · [feat] · `_profile_hash()`: SHA-256 of `json.dumps(profile, sort_keys=True)` — deterministic, one-way, no PII (Constitution §V) — `functions/rank_products/main.py`
- [x] TASK-039 · [feat] · Empty guard: `passed_products=[]` returns `{"top3": [], "audit": {...}}` with HTTP 200 — `functions/rank_products/main.py`
- [x] TASK-040 · [test] · Core test suite: scoring formula, normalisation, audit trail, HTTP 400/200 status codes — `tests/test_rank_products.py`
- [x] TASK-064 · [test] · Add `test_sum_need_zero_income_fit_default`: profile `sum_need=0` → `score_breakdown.income_fit == 0.5` (no ZeroDivisionError) — `tests/test_rank_products.py`
- [x] TASK-065 · [test] · Add `test_sum_need_absent_income_fit_default`: profile with no `sum_need` key → `income_fit == 0.5` — `tests/test_rank_products.py`
- [x] TASK-066 · [test] · Add `test_point_age_range_exact_match`: product `min_age=40`, `max_age=40`, customer `age=40` → `age_centrality == 1.0` — `tests/test_rank_products.py`
- [x] TASK-067 · [test] · Add `test_point_age_range_mismatch`: product `min_age=40`, `max_age=40`, customer `age=41` → `age_centrality == 0.0` (not negative, not error) — `tests/test_rank_products.py`
- [x] TASK-068 · [test] · Add `test_fewer_than_three_products_no_padding`: 2 `passed_products` → `len(top3) == 2`, no HTTP error, ranks are `[1, 2]` — `tests/test_rank_products.py`
- [x] TASK-069 · [test] · Add `test_elser_score_absent_defaults_to_neutral`: product with no `elser_score` key → `score_breakdown.elser_relevance` is `> 0.0` (neutral, not worst-case zero) — `tests/test_rank_products.py`

---

## Phase 3 — Voice-Optimised Explanation Generation (P1 — Story 2)

**Goal**: Sub-Agent 3 system prompt exists, enforces ≤120 word voice output in WaveNet-safe prose with personalised per-product rationale.

**Independent Test**: Apply the sub-agent3 prompt template to a known `top3` fixture offline (no live ADK call) and verify word count ≤ 120, all 3 product names present, and at least one personalisation marker per product.

- [x] TASK-070 · [feat] · Create `agent_builder/sub_agent3_explainer_prompt.md`: defines input context (top3 JSON, customer profile summary), output format (plain prose, ≤120 words, ₹ for all monetary values, no markdown), and personalisation rule (each product must reference age, family, income bracket, or coverage goal) — `agent_builder/sub_agent3_explainer_prompt.md`
- [x] TASK-070a · [infra] · Register Sub-Agent 3 in `agent_builder/agent_definition.py`: create a new `LlmAgent` (Gemini 2.0 Flash) with `sub_agent3_explainer_prompt.md` as its instruction; wrap it as an `AgentTool` and add to `root_agent.tools`; name it `"recommend_and_explain"` — `agent_builder/agent_definition.py`
- [x] TASK-071 · [feat] · Add follow-up handling section to `sub_agent3_explainer_prompt.md`: when customer says "tell me more about [number/name]", provide ≤80 word deep-dive on that product only — `agent_builder/sub_agent3_explainer_prompt.md`
- [x] TASK-072 · [feat] · Add guardrail reminders to `sub_agent3_explainer_prompt.md`: never reference rejected products; never make medical claims or guarantee underwriting approval; always note premiums are indicative — `agent_builder/sub_agent3_explainer_prompt.md`
- [x] TASK-073 · [feat] · Verify `agent_builder/root_agent_prompt.md` Step 4 ("EXPLAIN") correctly delegates to the Recommendation Explainer Agent and that Step 5 ("RESPOND") uses its output verbatim for TTS — `agent_builder/root_agent_prompt.md`
- [x] TASK-074 · [test] · Write `tests/test_voice_explanation.py`: load a known `top3` fixture (3 products, known names + premiums), apply explanation prompt template offline, assert `word_count <= 120` and each product name appears at least once — `tests/test_voice_explanation.py`
- [x] TASK-075 · [test] · Add `test_explanation_personalisation`: fixture profile `age=38`, `coverage_goals=["life","health"]`; assert explanation contains at least one of `["38", "family", "life", "health"]` per product block — `tests/test_voice_explanation.py`
- [x] TASK-076 · [test] · Add `test_explanation_no_markdown`: assert explanation text contains none of `["**", "##", "- ", "| ", "\n\n\n"]` (WaveNet-safe) — `tests/test_voice_explanation.py`
- [x] TASK-077 · [test] · Add `test_single_product_explanation_under_80_words`: fixture with `top3` length 1 (follow-up detail scenario); verify ≤80 word output — `tests/test_voice_explanation.py`

---

## Phase 4 — Audit Trail Logging (P2 — Story 3)

**Goal**: Every `rank_products` call emits a structured log entry to Cloud Logging with all 7 audit fields (Constitution §IV), and `audit.all_scored` covers all evaluated products — not just the top-3.

- [x] TASK-080 · [feat] · Verify `functions/rank_products/main.py` emits `print(json.dumps({"event": "rank_products_audit", **audit}))` after building the audit dict; add it if absent — `functions/rank_products/main.py`
- [x] TASK-081 · [test] · Add `test_audit_all_scored_covers_all_inputs`: 7 `passed_products` → `len(audit.all_scored) == 7` (not just 3) — `tests/test_rank_products.py`
- [x] TASK-082 · [test] · Add `test_audit_all_scored_sorted_descending`: `all_scored` list is in descending `suitability_score` order — `tests/test_rank_products.py`
- [x] TASK-083 · [test] · Add `test_audit_present_on_empty_input`: `passed_products=[]` → response still contains `audit` key with `all_scored=[]`, `formula_weights`, `customer_profile_hash` — `tests/test_rank_products.py`

---

## Phase 5 — Deployment Verification & Smoke Test

**Goal**: `rank_products` Cloud Function is live, the deployed URL is captured, and a real HTTP call returns a correct ranked response.

- [x] TASK-090 · [infra] · Verify `infra/cloudbuild.yaml` contains a step that copies `shared/` into the `functions/rank_products/` directory before deploying (`copy-shared-rank` equivalent) — `infra/cloudbuild.yaml`
- [x] TASK-091 · [infra] · Verify `infra/cloudbuild.yaml` contains a `deploy-rank-products` step with `waitFor: [copy-shared-rank]` — `infra/cloudbuild.yaml`
- [x] TASK-092 · [infra] · Run `gcloud builds submit` and confirm both `copy-shared-rank` and `deploy-rank-products` steps exit 0 — `infra/cloudbuild.yaml`
- [x] TASK-093 · [infra] · Capture deployed `rank_products` URL from build output; store as `RANK_PRODUCTS_URL` in `.env.local` — local config
- [x] TASK-094 · [test] · Smoke test: `POST $RANK_PRODUCTS_URL` with 5 products and a valid profile — assert HTTP 200, `top3` length ≤ 3, `top3[0].suitability_score >= top3[1].suitability_score` — `tests/smoke_test_live.py`
- [x] TASK-095 · [test] · Smoke test: `POST $RANK_PRODUCTS_URL` with `passed_products=[]` — assert HTTP 200, `top3=[]`, `audit` present — `tests/smoke_test_live.py`
- [ ] TASK-096 · [infra] · Verify Cloud Logging shows `rank_products_audit` events for the smoke test calls (GCP Console Log Explorer, filter `jsonPayload.event="rank_products_audit"`)

---

## Dependencies

- Phase 1 is a read-only verification gate; all later phases depend on Phase 1 passing
- Phase 2 edge-case tests (TASK-064–TASK-068) require Phase 1 verification (TASK-060–TASK-063)
- Phase 3 (TASK-070–TASK-077) is independent of Phase 2; can be run in parallel
- TASK-070a requires TASK-070 (prompt must exist before registration)
- Phase 4 (TASK-080–TASK-083) requires Phase 1 (TASK-060) for the handler verification
- Phase 5 (TASK-090–TASK-096) requires Phase 2 complete AND a successful Cloud Build

---

## MVP Scope

**Minimum shippable**: Phase 1 + Phase 2 + Phase 3 (TASK-060 through TASK-077).

This delivers the full end-to-end demo flow: compliance-passed products → deterministic ranked top-3 → voice-optimised recommendation response. Phase 4 (audit logging) and Phase 5 (live deployment smoke tests) are required for judging but not blocking on the local demo.

# InsureVoice Constitution

## I. Compliance-First (NON-NEGOTIABLE)

Every product recommendation **must** pass the compliance guardrail before being presented to any customer. The `compliance_check` Cloud Function is the single mandatory gate between search results and ranked recommendations. A product that fails any eligibility rule must never appear in the final response — not even as a fallback or informational item.

If all candidate products are rejected, the system must explain the constraint to the customer and prompt them to clarify their profile. It must never fabricate an alternative recommendation.

## II. Zero Hallucination on Eligibility

The `compliance_check` function is deterministic rule logic. It must **never** delegate eligibility decisions to an LLM, ML model, or probabilistic system. Every rule must be expressed as a pure Python predicate that produces the same result for the same inputs every time.

Permissible in compliance logic: age bounds, income caps, smoker flags, medical exam thresholds, sum assured limits.
Not permissible: Gemini calls, Elasticsearch queries, external API calls, non-deterministic logic.

## III. Latency Gate

End-to-end latency from voice utterance to TTS response **must** be under 8 seconds. Individual budget:

| Component | Budget |
|---|---|
| STT transcription (Dialogflow CX) | < 1.5s |
| Root Agent profile extraction | < 1s |
| Sub-Agent 1: ELSER semantic search | < 2s |
| Sub-Agent 2: Compliance check | < 0.5s |
| Sub-Agent 3: Ranking + explanation | < 1s |
| TTS synthesis | < 0.5s |
| **Total** | **< 8s** |

Any design decision that risks breaching these budgets must be called out explicitly in the plan and requires explicit approval before implementation.

## IV. Audit Trail

Every set of recommendations delivered to a customer must be traceable. The system must record:
- Input customer profile (anonymised — no name, no contact info)
- Candidate products from search (with ELSER relevance scores)
- Compliance outcomes per product (passed / rejected + rejection reason)
- Final top-3 rankings (product ID, suitability score, score breakdown)

Audit records may live in Cloud Logging / Pub/Sub in Phase 1. IRDAI-aligned persistent audit is a Phase 2 concern.

## V. No PII Storage

Customer profiles exist only within the Agent Builder session context. They must not be written to any database, log stream, or file system. Anonymous session tokens are acceptable for audit correlation.

## VI. Open Source & Hackathon Eligibility

- Apache 2.0 LICENSE at repository root (required for judging)
- Public GitHub repository
- Elastic MCP server must be the primary search integration (not a secondary fallback)
- Google Cloud Agent Builder (ADK) must orchestrate the agents

## VII. Simplicity Under Deadline

This is a hackathon. Prefer working implementations over architectural elegance. When in doubt: deploy a Cloud Function, not a microservice. Use Gemini directly, not a RAG pipeline. The demo must run end-to-end by submission day.

YAGNI applies: do not build Phase 2 features during Phase 1.

## Governance

This constitution supersedes all other practices described in feature specs, plans, and tasks. If a task contradicts the constitution, the task must be revised — the constitution does not bend.

Constitution amendments require: documented rationale, impact analysis on existing specs, and explicit team approval.

All feature specs must include a **Constitution Check** section in their plan.

**Version**: 1.0.0 | **Ratified**: 2026-05-26 | **Last Amended**: 2026-05-26

---
name: speckit-plan
description: "Generate a detailed implementation plan for a feature spec. Produces specs/NNN-<feature>/plan.md with tech stack, architecture, file layout, and research. Run after /speckit-specify."
mode: speckit.plan
argument-hint: "Optional: additional context about tech choices or constraints"
---

# Spec-Kit: Plan

Generate a detailed implementation plan from the current feature spec.

## User Input

```text
$ARGUMENTS
```

## Project Context

**InsureVoice** — AI-powered multi-agent insurance sales recommendation agent.

- **Language**: Python 3.11
- **GCP Services**: Cloud Functions (2nd gen), Agent Builder (ADK), Dialogflow CX, Cloud STT/TTS, Cloud Build, Vertex AI
- **Search**: Elasticsearch Cloud + ELSER v2 (`semantic_text` field) + Elastic MCP server
- **LLM**: Gemini 2.0 Flash
- **Testing**: pytest, functions-framework local emulation
- **Deployment**: Cloud Build (`infra/cloudbuild.yaml`), Cloud Run / Cloud Functions
- **Key source paths**:
  - `agent_builder/` — root agent config + sub-agent prompts
  - `functions/compliance_check/` — guardrail Cloud Function
  - `functions/rank_products/` — ranking Cloud Function
  - `ingest/` — Elasticsearch index creation and product ingestion
  - `data/insurance_products.json` — product catalog

Constitution: `specs/constitution.md`

## Procedure

1. **Load context**:
   - Read `.specify/feature.json` to get `feature_directory`
   - Read `specs/<feature>/spec.md` for user stories and requirements
   - Read `specs/constitution.md` for governance constraints

2. **Research** (if needed): Note any open technical questions about GCP, Elastic, or ADK that affect the design. Record as `research.md` in the feature directory.

3. **Write `specs/<feature>/plan.md`** using this structure:

```markdown
# Implementation Plan: [FEATURE NAME]

**Spec**: specs/NNN-feature/spec.md | **Date**: [DATE]

## Summary
[Primary requirement + approach in 2–3 sentences]

## Technical Context

| Field | Value |
|---|---|
| Language | Python 3.11 |
| Key Libraries | [e.g., functions-framework, elasticsearch, google-cloud-aiplatform] |
| GCP Services | [e.g., Cloud Functions, Agent Builder ADK] |
| Elastic | [e.g., ELSER v2 semantic_text, Elastic MCP] |
| Testing | pytest + functions-framework local emulator |
| Deployment | Cloud Build / Cloud Run |

## Architecture

[Describe data flow through InsureVoice layers relevant to this feature]

## File Structure

```text
[Show exact files to create or modify — no placeholders]
```

## Constitution Check

- [ ] Compliance guardrail respected (no rejected product can be recommended)
- [ ] Latency target honoured (< 8s end-to-end)
- [ ] No hallucination risk in deterministic logic
- [ ] Audit trail considered

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|

## Open Questions
[Any remaining unknowns]
```

4. **Save** the plan to `specs/<feature>/plan.md`.

5. **Report**: Path written, key design decisions made, any open questions flagged.

**Next step**: Run `/speckit-tasks` to break the plan into actionable tasks.

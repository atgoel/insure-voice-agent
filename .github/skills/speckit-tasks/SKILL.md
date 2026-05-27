---
name: speckit-tasks
description: "Generate an actionable, dependency-ordered tasks.md for a feature. Run after /speckit-plan. Produces specs/NNN-<feature>/tasks.md with phased implementation tasks."
mode: speckit.tasks
argument-hint: "Optional: scope or constraint notes (e.g., 'MVP only', 'skip voice layer')"
---

# Spec-Kit: Tasks

Generate an actionable task breakdown for the current feature.

## User Input

```text
$ARGUMENTS
```

## Project Context

**InsureVoice** — Python 3.11 · Google Cloud Agent Builder (ADK) · Elastic ELSER v2 · Dialogflow CX · Gemini 2.0 Flash

Key implementation paths:
- `functions/compliance_check/main.py` — deterministic eligibility rule engine (Cloud Function)
- `functions/rank_products/main.py` — suitability scoring (Cloud Function)
- `agent_builder/root_agent_prompt.md` — Root Supervisor Agent prompt
- `agent_builder/tools.yaml` — tool definitions for sub-agents
- `ingest/create_index.py` — Elasticsearch index schema
- `ingest/index_products.py` — ELSER product ingestion
- `data/insurance_products.json` — 25–30 synthetic products

## Procedure

1. **Load context**:
   - Read `.specify/feature.json` → `feature_directory`
   - Read `specs/<feature>/spec.md` for user stories (P1, P2, P3)
   - Read `specs/<feature>/plan.md` for tech stack and file structure

2. **Write `specs/<feature>/tasks.md`** using phased structure:

```markdown
# Tasks: [FEATURE NAME]

**Spec**: specs/NNN-feature/spec.md | **Plan**: specs/NNN-feature/plan.md

## Phase 1 — Setup & Infrastructure
- [ ] TASK-001 · [setup] · Create/verify [file path]
- [ ] TASK-002 · [infra] · [infrastructure task]

## Phase 2 — Core Implementation (P1 Story)
**Goal**: [P1 story goal]
**Independent Test**: [how to test P1 alone]

- [ ] TASK-010 · [feat] · [description] — `path/to/file.py`
- [ ] TASK-011 · [feat] · [description] — `path/to/file.py`
- [ ] TASK-012 · [test] · Write pytest for [component]

## Phase 3 — [P2 Story Name]
**Goal**: [P2 story goal]
...

## Phase N — Integration & Polish
- [ ] TASK-NNN · [test] · End-to-end test: voice → recommendation
- [ ] TASK-NNN · [docs] · Update README with usage

## Dependencies
- Phase 2 requires Phase 1 complete
- Phase 3 requires Phase 2 TASK-010, TASK-011

## MVP Scope
Minimum shippable: Phase 1 + Phase 2 only.
```

3. **Task rules**:
   - Every task has: checkbox, ID (`TASK-NNN`), label `[feat|test|infra|docs|setup]`, description, file path
   - Each user story phase ends with at least one `[test]` task
   - Total tasks: aim for 5–10 per story phase, 3–5 for setup
   - All file paths must match actual project structure

4. **Save** to `specs/<feature>/tasks.md`.

5. **Report**: Total task count, per-phase count, suggested MVP scope.

**Next steps**: Run `/speckit-analyze` for consistency check, then `/speckit-implement` to start coding.

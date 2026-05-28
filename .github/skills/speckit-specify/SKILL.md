---
name: speckit-specify
description: "Create or update a feature specification from a natural language description. USE FOR: defining new features, user stories, acceptance criteria, and edge cases. Writes specs/NNN-<feature-name>/spec.md. Run this first before plan or tasks."
argument-hint: "Natural language description of the feature to specify"
---

# Spec-Kit: Specify

Create or update a feature specification from a natural language description.

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input above before proceeding.

## Project Context

This is the **InsureVoice** project — an AI-powered multi-agent insurance sales recommendation system built for the Elastic Partner Track hackathon.

Key components:
- **Root Agent** (Google Cloud Agent Builder / ADK) — orchestrates 3 sub-agents
- **Sub-Agent 1: Product Search** — ELSER v2 hybrid semantic search via Elastic MCP
- **Sub-Agent 2: Compliance Guard** — `functions/compliance_check/` Cloud Function
- **Sub-Agent 3: Recommendation Explainer** — `functions/rank_products/` Cloud Function
- **Voice Interface** — Dialogflow CX + Cloud STT + Cloud TTS WaveNet
- **Data** — `data/insurance_products.json`, indexed into Elasticsearch with `ingest/`

Active specs are in `specs/`. Constitution is at `specs/constitution.md`.

## Procedure

1. **Scan existing specs** — Read `specs/` directory. Find the next available 3-digit number (e.g., if `001-`, `002-` exist, next is `003-`).

2. **Generate a short feature name** — 2–4 words in kebab-case from the feature description (e.g., `voice-intake`, `elser-search`, `compliance-engine`).

3. **Create the spec directory and file**:
   - Create `specs/NNN-<feature-name>/spec.md`
   - Save resolved path to `.specify/feature.json`:
     ```json
     { "feature_directory": "specs/NNN-<feature-name>" }
     ```

4. **Write the spec** using this structure:

```markdown
# Feature Specification: [FEATURE NAME]

**Feature Directory**: `specs/NNN-feature-name/`
**Created**: [TODAY's DATE]
**Status**: Draft

## Overview
[2–3 sentence summary of what this feature does and why it matters for InsureVoice]

## User Stories & Acceptance Criteria

### Story 1 — [Title] (Priority: P1)
[Description]
**Acceptance Scenarios**:
1. **Given** [...], **When** [...], **Then** [...]

### Story 2 — [Title] (Priority: P2)
...

## Edge Cases
- [What happens when...]

## Out of Scope
- [What this spec does NOT cover]

## Technical Notes
[Relevant implementation hints: which files/agents/services are involved]
```

5. **Apply InsureVoice context** — for every user story, consider:
   - Compliance constraints (age, income caps, smoker rules, sum assured limits)
   - Voice delivery format (< 120 words, INR currency, WaveNet TTS)
   - ELSER semantic search (sparse vectors, not keyword search)
   - Multi-agent orchestration sequence: Extract → Search → Validate → Rank → Respond
   - Latency target: < 8s end-to-end

6. **Report**: State the path of the created spec and a 1-sentence summary of each user story.

**Next step**: Run `/speckit-plan` to create an implementation plan.

# Feature Specification: Voice Intake & Profile Extraction

**Feature Directory**: `specs/001-voice-intake-profile-extraction/`
**Created**: 2026-05-26
**Status**: Draft

## Overview

When a customer speaks their insurance needs in natural language ("I'm 38, non-smoker, ₹15L income, need life and health cover for my family"), the Root Agent must parse that transcript and extract a structured profile that all downstream sub-agents can consume. This spec covers: the Dialogflow CX conversation design, the Root Agent's profile extraction logic, the structured profile schema, and the multi-turn handling when required fields are missing.

---

## User Stories & Acceptance Criteria

### Story 1 — Single-Turn Profile Extraction (Priority: P1)

A customer provides all required fields in a single voice utterance. The Root Agent extracts a complete, validated `CustomerProfile` object without asking any follow-up questions.

**Why P1**: Without a complete profile, no downstream sub-agent can run. This is the critical path.

**Independent Test**: Provide a transcript like "I'm 35, married, non-smoker, ₹20L annual income, need ₹1Cr life cover for 20 years" — the extracted profile must contain all required fields with correct types.

**Acceptance Scenarios**:

1. **Given** a transcript with age, income, smoker status, and at least one coverage goal, **When** the Root Agent processes it, **Then** it returns a `CustomerProfile` with all required fields correctly typed and no follow-up question is asked.
2. **Given** a transcript with monetary values in lakhs (₹15L, ₹20L), **When** extracted, **Then** income is stored as an integer in INR (e.g., `1500000`).
3. **Given** a transcript that mentions "family of 4", **When** extracted, **Then** `family_size = 4` and `dependents = 3`.

---

### Story 2 — Multi-Turn Clarification for Missing Fields (Priority: P2)

When a customer's utterance is missing one or more required fields, the Root Agent asks a single targeted clarifying question. It stores partial profile state across turns and completes extraction on the follow-up.

**Why P2**: Most real customers won't give a complete profile in one sentence. Multi-turn handling is key for demo realism.

**Independent Test**: Provide a transcript with no income mentioned — the agent must ask exactly one question about income, accept the follow-up answer, and produce a complete profile.

**Acceptance Scenarios**:

1. **Given** a transcript with missing income, **When** processed, **Then** the Root Agent asks exactly one clarifying question about annual income and does not proceed to search.
2. **Given** the Root Agent has asked for income and receives "₹12 lakhs per year" as a follow-up, **When** processed, **Then** the incomplete profile is completed with `income = 1200000` and search proceeds.
3. **Given** a transcript that is entirely off-topic (not insurance-related), **When** processed, **Then** the Root Agent politely redirects and does not attempt profile extraction.

---

### Story 3 — Profile Validation & Bounds Checking (Priority: P2)

The extracted `CustomerProfile` is validated before being passed downstream. Out-of-range or logically inconsistent values trigger a clarifying question, not a silent failure.

**Acceptance Scenarios**:

1. **Given** an extracted age of 0 or > 100, **When** validated, **Then** the Root Agent asks for clarification rather than passing the invalid profile.
2. **Given** a `sum_need` > 10× `income`, **When** validated, **Then** the Root Agent warns the customer that the requested cover may exceed eligibility limits and asks to confirm.
3. **Given** a complete and valid profile, **When** validated, **Then** it is passed to the Product Search Sub-Agent without modification.

---

## CustomerProfile Schema

```json
{
  "age": "integer (18–75)",
  "income": "integer (INR per annum, min 100000)",
  "smoker": "boolean",
  "health_status": "enum: healthy | pre_existing (specify condition if mentioned)",
  "family_size": "integer (1–10)",
  "dependents": "integer (0–9)",
  "coverage_goals": "array of enum: life | health | critical_illness | accident | investment | endowment",
  "sum_need": "integer (INR, optional — inferred from 'need ₹Xcr/₹XL cover')",
  "preferred_term_years": "integer (optional)"
}
```

**Required fields**: `age`, `income`, `smoker`, `health_status`, `coverage_goals`  
**Optional**: `sum_need`, `family_size`, `dependents`, `preferred_term_years`

---

## Edge Cases

- Customer says "I don't smoke" vs. "I'm a non-smoker" vs. "occasional smoker" → map to `smoker: false` or `true` with consistent logic.
- Coverage goals stated as "family protection" — map to `life` + `health`.
- Age given as "in my thirties" → ask for exact age.
- Sum need stated as "maximum possible" → set `sum_need: null` and let the search/compliance layer handle it.
- Customer switches to Hindi mid-sentence (phase 1: flag and ask to repeat in English; phase 2: full Hindi support).

---

## Out of Scope

- Phase 2 Hindi/multilingual support.
- Identity verification or KYC.
- Saving customer profiles to any database.
- Handling more than one customer per session.

---

## Technical Notes

- Profile extraction: Root Agent prompt (`agent_builder/root_agent_prompt.md`) uses Gemini function-calling with structured output schema.
- Conversation flow: Dialogflow CX `provide_profile` intent → triggers Agent Builder handoff.
- Partial profile state: maintained in Agent Builder session context (not persisted).
- Downstream: validated `CustomerProfile` dict is passed as the `customer_profile` field to all sub-agents.

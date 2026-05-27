---
name: speckit-checklist
description: "Generate a domain-specific requirements quality checklist for the current spec. Validates completeness, clarity, and consistency of the spec — not the implementation. Run before /speckit-plan."
mode: speckit.checklist
argument-hint: "Domain focus for the checklist (e.g., 'compliance', 'voice UX', 'search accuracy', 'agent orchestration')"
---

# Spec-Kit: Checklist

Generate a requirements quality checklist for the current spec.

## Concept: "Unit Tests for Spec Writing"

Checklists validate the **quality of requirements**, not the implementation:
- ✅ "Is the compliance rejection response format defined?" (completeness)
- ✅ "Is 'under 8 seconds' broken down per agent step?" (clarity)  
- ❌ NOT "Test that the compliance function returns 200" (that's a code test)

## User Input

```text
$ARGUMENTS
```

## Project Context

**InsureVoice** — Common checklist domains:

| Domain | Focus |
|---|---|
| `compliance` | All 5 rule types defined, rejection message format, edge cases (all rejected, partial match) |
| `voice UX` | Word count limits, language/locale, multi-turn context, TTS format requirements |
| `search accuracy` | Hybrid query weights, zero-result fallback, semantic vs. keyword trade-off documented |
| `agent orchestration` | Sub-agent call sequence defined, timeout handling, error propagation, retry policy |
| `data model` | All product fields defined, required vs. optional, validation rules, example values |
| `security` | Input validation, no PII storage, secret management, injection prevention |

## Procedure

1. **Load spec**:
   - Read `.specify/feature.json` → `feature_directory`
   - Read `specs/<feature>/spec.md`

2. **Determine domain** from `$ARGUMENTS` or from the spec content.

3. **Generate checklist** in `specs/<feature>/checklists/<domain>.md`:

```markdown
# [Domain] Checklist: [Feature Name]

## Completeness
- [ ] [Is X defined for all scenarios?]
- [ ] [Are all error states specified?]

## Clarity
- [ ] [Is Y quantified with specific values?]
- [ ] [Are ambiguous terms defined?]

## Consistency
- [ ] [Does this align with the constitution?]
- [ ] [Are there contradictions with other specs?]

## Edge Cases
- [ ] [Is the zero-results case handled?]
- [ ] [Is the all-rejected case handled?]
```

4. Generate 10–20 items, weighted toward the specific domain.

5. **Report**: Path to checklist, item count per category, any immediate spec gaps found.

**After completing the checklist**: Run `/speckit-implement` — it will verify checklist completion before coding.

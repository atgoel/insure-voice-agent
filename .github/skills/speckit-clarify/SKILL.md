---
name: speckit-clarify
description: "Ask up to 5 targeted clarification questions about the current spec and encode answers back into spec.md. Run before /speckit-plan to reduce ambiguity."
argument-hint: "Optional: specific area to clarify (e.g., 'compliance rules', 'voice format')"
---

# Spec-Kit: Clarify

Identify and resolve ambiguities in the current feature spec.

## User Input

```text
$ARGUMENTS
```

## Project Context

**InsureVoice** — Focus clarification questions on these common ambiguity zones:

| Zone | Common gaps |
|---|---|
| Compliance rules | Which rules are hardcoded vs. configurable? What happens when ALL products are rejected? |
| Voice format | Max word count? Language (en-IN only)? Multi-turn context retention? |
| ELSER search | Hybrid weights (BM25 vs. sparse vector)? Filter strictness? Fallback when 0 results? |
| Agent orchestration | Sequential or parallel sub-agent calls? Timeout / retry policy? |
| Scoring | Weights for ELSER relevance vs. age centrality vs. income fit? |
| Data | Are products synthetic only, or real IRDAI data? Privacy constraints? |

## Procedure

1. **Load current spec**:
   - Read `.specify/feature.json` → `feature_directory`
   - Read `specs/<feature>/spec.md`

2. **Scan for ambiguity** across these categories (mark each: Clear / Partial / Missing):
   - Functional scope & success criteria
   - Actor / system roles
   - Data model & constraints
   - Error / edge case handling
   - Performance targets (latency, accuracy)
   - Security / compliance requirements
   - Out-of-scope boundaries

3. **Formulate up to 5 questions**: Prioritise by impact. Ask only about gaps that would block correct implementation.

4. **Present questions** clearly numbered. Wait for user answers.

5. **Update spec.md**: For each answer, find the relevant section and add the clarification as a bullet or update the acceptance criteria. Add a `## Clarification Log` section at the end:
   ```markdown
   ## Clarification Log
   - [DATE] Q: [question] → A: [answer]
   ```

6. **Report**: Number of clarifications resolved, any remaining open questions.

**Next step**: Run `/speckit-plan` once all critical questions are answered.

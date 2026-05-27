---
name: speckit-analyze
description: "Non-destructive consistency check across spec.md, plan.md, and tasks.md. Identifies misalignments, gaps, and constitution violations. Run after /speckit-tasks before implementing."
mode: speckit.analyze
argument-hint: "Optional: focus area (e.g., 'compliance', 'latency', 'test coverage')"
---

# Spec-Kit: Analyze

Perform a read-only consistency check across spec, plan, and tasks.

## User Input

```text
$ARGUMENTS
```

**STRICTLY READ-ONLY** — this skill does not modify any files. It only reports findings.

## Project Context

**InsureVoice** constitution principles to check against:
1. **Compliance-first** — guardrail filter is mandatory; no rejected product reaches the response
2. **Zero hallucination on eligibility** — compliance logic must be deterministic (no LLM)
3. **Latency gate** — end-to-end < 8s; each agent/function < 2s
4. **Audit trail** — every recommendation must be traceable (product ID, scores, rules applied)
5. **Hackathon constraints** — Apache 2.0 LICENSE, public repo, hosted demo, Elastic MCP used

## Procedure

1. **Load artifacts**:
   - Read `.specify/feature.json` → `feature_directory`
   - Read `specs/<feature>/spec.md` (user stories, acceptance criteria)
   - Read `specs/<feature>/plan.md` (tech stack, architecture, file structure)
   - Read `specs/<feature>/tasks.md` (all tasks)
   - Read `specs/constitution.md` (governance constraints)

2. **Run consistency checks**:

   **Spec ↔ Plan alignment**:
   - Every P1 user story has a corresponding plan section
   - Tech stack in plan matches InsureVoice stack
   - File paths in plan match actual project structure

   **Plan ↔ Tasks alignment**:
   - Every file listed in plan has at least one CREATE or MODIFY task
   - Each story has at least one `[test]` task
   - No orphaned tasks referencing non-existent plan sections

   **Constitution compliance**:
   - Compliance guardrail check present in relevant specs
   - Latency target stated in plan
   - No LLM used in compliance/eligibility logic
   - Audit logging considered

   **Completeness**:
   - Edge cases addressed in spec
   - Error handling planned
   - Out-of-scope section present

3. **Output structured report**:

```markdown
## Consistency Analysis Report

### Spec ↔ Plan
| Check | Status | Finding |
|---|---|---|
| P1 story covered in plan | ✅ / ⚠️ / ❌ | [details] |

### Plan ↔ Tasks
| Check | Status | Finding |

### Constitution Compliance
| Principle | Status | Finding |

### Summary
- Critical issues (❌): N
- Warnings (⚠️): N
- All clear (✅): N

### Recommended actions (if any)
1. [action]
```

4. Do **not** modify any files. Offer remediation suggestions as text only.

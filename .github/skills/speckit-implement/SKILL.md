---
name: speckit-implement
description: "Execute the implementation plan by working through tasks.md phase by phase. Writes actual code. Run after /speckit-tasks. Stops at incomplete checklists."
argument-hint: "Optional: phase to start from (e.g., 'Phase 2'), or 'all' for everything"
---

# Spec-Kit: Implement

Execute the implementation plan by working through tasks.md.

## User Input

```text
$ARGUMENTS
```

## Project Context

**InsureVoice** — Python 3.11 · functions-framework · elasticsearch · google-cloud-aiplatform

**Security rules** (OWASP / hackathon compliance):
- Never hardcode API keys — use environment variables / Secret Manager
- Validate all Cloud Function inputs (type, range, required fields)
- Compliance guardrail is deterministic — no LLM involved in eligibility decisions
- No customer PII stored beyond the session

**Style conventions**:
- Google Cloud Functions: `@functions_framework.http` decorator
- Return `json.dumps(result), 200, {"Content-Type": "application/json"}`
- Pytest files: `tests/test_<module>.py`
- Constants: UPPER_SNAKE_CASE at module level

## Procedure

1. **Load context**:
   - Read `.specify/feature.json` → `feature_directory`
   - Read `specs/<feature>/tasks.md` — task list and phases
   - Read `specs/<feature>/plan.md` — tech stack and architecture
   - Read `specs/<feature>/spec.md` — acceptance criteria
   - Read `specs/constitution.md` — governance constraints

2. **Check phase scope**: If `$ARGUMENTS` specifies a phase, start there. Otherwise start at Phase 1.

3. **For each task in the current phase**:
   - Read the task description and target file path
   - Implement the code following project conventions
   - Mark the task complete: `- [x] TASK-NNN`
   - Move to next task

4. **After each phase**:
   - Run a mental "constitution check" — does the implementation honour all constraints?
   - Report what was built and what phase is next

5. **Project setup** (Phase 1 only):
   - Verify `.gitignore` includes: `__pycache__/`, `*.pyc`, `.env`, `.venv/`, `venv/`, `*.egg-info/`
   - Create `tests/__init__.py` if it doesn't exist
   - Verify `requirements.txt` exists in each `functions/` subdirectory

6. **Report**: Files created/modified, tasks completed, any blockers.

**Note**: If any checklist in `specs/<feature>/checklists/` has incomplete items, stop and report them before proceeding.

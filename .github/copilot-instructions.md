# InsureVoice — GitHub Copilot Context

<!-- Managed by Spec-Kit — Spec-Driven Development -->

## Project Overview

**InsureVoice** is an AI-powered, voice-driven insurance sales recommendation system built for the **Elastic Partner Track** of the "Building Agents for Real-World Challenges" hackathon (Google Cloud + Gemini 2.0 Flash).

It reduces insurance sales consultation time from 60–90 minutes to under 8 seconds by orchestrating a multi-agent pipeline: voice intake → semantic search → compliance validation → ranked recommendations → voice response.

---

## Architecture

```
Customer Voice
    │
Dialogflow CX (Cloud STT streaming)
    │
Root Agent — Insurance Sales Supervisor  [Google Cloud Agent Builder / ADK]
    │         Gemini 2.0 Flash · Multi-turn state
    ├── Sub-Agent 1: Product Search    → Elastic MCP (ELSER v2 hybrid search)
    ├── Sub-Agent 2: Compliance Guard  → compliance_check Cloud Function
    └── Sub-Agent 3: Explainer         → rank_products Cloud Function
                                              │
                                     Cloud TTS WaveNet → Voice Response
```

### Latency budget
| Step | Target |
|---|---|
| STT transcription | < 1.5s |
| Agent Builder + sub-agents | < 5s |
| TTS synthesis | < 0.5s |
| **Total end-to-end** | **< 8s** |

---

## Repository Structure

```
SalesRecommendandWorkflow/
├── agent_builder/
│   ├── root_agent_prompt.md     # Root Supervisor Agent system prompt
│   └── tools.yaml               # Tool definitions for sub-agents
├── data/
│   └── insurance_products.json  # Synthetic product catalog (25–30 products)
├── docs/
│   ├── ARCHITECTURE.md          # Full architecture deep-dive
│   ├── HACKATHON-PLAN.md        # Day-by-day build plan
│   └── CEO-PITCH-AND-BUDGET.md  # Business pitch
├── functions/
│   ├── compliance_check/
│   │   └── main.py              # Deterministic eligibility rule engine
│   └── rank_products/
│       └── main.py              # Suitability scoring & ranking
├── ingest/
│   ├── create_index.py          # Elasticsearch index schema (ELSER v2 semantic_text)
│   └── index_products.py        # Product ingestion into Elastic
├── infra/
│   └── cloudbuild.yaml          # Cloud Build deployment config
├── specs/                       # ← Spec-Driven Development artifacts
│   ├── constitution.md          # Project governance principles
│   ├── 001-voice-intake-profile-extraction/
│   ├── 002-elser-semantic-search/
│   ├── 003-compliance-guardrail-engine/
│   ├── 004-product-ranking-explainer/
│   └── 005-multi-agent-orchestration/
└── .github/
    ├── copilot-instructions.md  # This file
    └── skills/                  # Spec-Kit skills (slash commands)
        ├── speckit-specify/     # /speckit-specify — create feature spec
        ├── speckit-plan/        # /speckit-plan — implementation plan
        ├── speckit-tasks/       # /speckit-tasks — task breakdown
        ├── speckit-implement/   # /speckit-implement — write code
        ├── speckit-clarify/     # /speckit-clarify — resolve spec ambiguity
        ├── speckit-analyze/     # /speckit-analyze — consistency check
        └── speckit-checklist/   # /speckit-checklist — requirements quality
```

---

## Spec-Driven Development Workflow

This project uses **Spec-Driven Development** via Spec-Kit. The workflow:

```
/speckit-specify  →  /speckit-clarify  →  /speckit-plan  →  /speckit-checklist
      →  /speckit-tasks  →  /speckit-analyze  →  /speckit-implement
```

All specs live in `specs/`. The active feature is tracked in `.specify/feature.json`.

---

## Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| LLM | Gemini 2.0 Flash |
| Agent Orchestration | Google Cloud Agent Builder (ADK) |
| Voice STT | Google Cloud STT (streaming) |
| Voice TTS | Google Cloud TTS WaveNet (`en-IN-Wavenet-D`) |
| Conversation | Dialogflow CX |
| Semantic Search | Elasticsearch Cloud + ELSER v2 sparse vectors |
| Agent Search Tool | Elastic MCP server |
| Cloud Functions | Python functions-framework (2nd gen) |
| Deployment | Cloud Build + Cloud Run |

---

## Coding Conventions

- **Cloud Functions**: Use `@functions_framework.http` decorator; return `json.dumps(result), 200, {"Content-Type": "application/json"}`
- **Input validation**: Always validate required fields and types at Cloud Function entry points
- **No hardcoded secrets**: Use environment variables / GCP Secret Manager
- **No PII storage**: Customer profiles exist only within the session; never persisted
- **Compliance engine is DETERMINISTIC**: No LLM in `compliance_check` — rule violations must never rely on model judgement
- **Currency**: Always use INR (₹) for all monetary values
- **Voice responses**: Keep under 120 words for TTS comfort
- **Tests**: `tests/test_<module>.py` using pytest

---

## Constitution Principles (summary)

Full details in `specs/constitution.md`. Key non-negotiables:

1. **Compliance-first**: Guardrail runs before every recommendation; rejected products never appear in final output
2. **Zero hallucination on eligibility**: `compliance_check` uses deterministic rule logic only
3. **Latency gate**: End-to-end < 8s; individual functions/agents have per-step budgets
4. **Audit trail**: Every recommendation includes product ID, suitability score, rule outcomes
5. **Open source**: Apache 2.0 LICENSE at repo root; public repository

---

## Hackathon Submission Requirements

- [ ] Hosted project URL
- [ ] Public GitHub repo with Apache 2.0 LICENSE
- [ ] ~3 minute demo video: voice intake → ELSER match → guardrail rejection → ranked recommendation → voice response
- [ ] Elastic MCP integration demonstrated (ELSER v2 semantic search)
- [ ] Built with Google Cloud Agent Builder

<!-- End Spec-Kit managed section -->

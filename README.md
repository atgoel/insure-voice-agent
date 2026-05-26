# InsureVoice — AI-Powered Insurance Sales Recommendation Agent

![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)
![Track](https://img.shields.io/badge/hackathon-Elastic%20Partner%20Track-brightgreen)
![Platform](https://img.shields.io/badge/platform-Google%20Cloud%20Agent%20Builder-orange)
![Search](https://img.shields.io/badge/search-Elasticsearch%20ELSER%20v2-005571)

> **Hackathon**: Building Agents for Real-World Challenges — Elastic Partner Track (Financial Services)
> **Built with**: Google Cloud Agent Builder · Gemini 2.0 Flash · Elastic MCP · ELSER v2 · Dialogflow CX

---

## The Problem

Insurance sales agents spend 60–90 minutes per customer manually matching complex customer profiles to the right insurance products. The process is error-prone, compliance-risky, and frustrating for customers. A 35-year-old married professional with a specific health history, income level, and family protection goal should not have to wait 90 minutes for a recommendation that takes 30 seconds to reason through.

---

## What InsureVoice Does

A **voice-driven, multi-agent AI system** that:

1. **Listens** — accepts a customer profile over natural voice ("I'm 38, non-smoker, ₹15L income, need life + health cover for my family")
2. **Understands semantically** — uses Elastic ELSER sparse vector search to match complex natural language needs to insurance products without keyword dependency
3. **Validates compliance** — automatically filters out products the customer is ineligible for (age bounds, income caps, medical requirements, smoker exclusions) before any recommendation is made
4. **Recommends with explanation** — returns top-3 products ranked by suitability with a voice-delivered "why this product" explanation per recommendation
5. **Initiates action** (Phase 2) — triggers the downstream sales workflow to begin the application process

---

## Target Goals

### Hackathon Goals
- [ ] Submit to **Elastic Partner Track** with a functional, hosted agent
- [ ] Demonstrate **Elastic MCP + ELSER** as the agent's core search superpower
- [ ] Score top 3 in the Financial Services domain by demonstrating multi-step agent reasoning with real compliance guardrails
- [ ] 3-minute demo video showing: voice intake → ELSER semantic match → guardrail rejection → ranked recommendation → voice response

### Product Goals (Phase 1)
- [ ] Voice-to-recommendation latency under 8 seconds end-to-end
- [ ] Guardrail compliance filter with zero false-pass rate on hardcoded eligibility rules
- [ ] ELSER hybrid search outperforming pure BM25 on semantic customer queries (measured by top-3 recall on 20 test cases)
- [ ] Multi-turn conversation — follow-up questions maintain context across turns

### Product Goals (Phase 2 — Post-Hackathon)
- [ ] Trigger downstream application workflow (Pub/Sub → workflow engine) on recommendation acceptance
- [ ] Expand product catalog to real insurance product data
- [ ] Add IRDAI-aligned audit trail for every AI recommendation made
- [ ] Multi-language support (English + Hindi) for regional insurance agents

### Business Goals
- [ ] Produce a client-demoable prototype of the Conversational AI module (aligned with Infinity platform Year 3 roadmap)
- [ ] Validate Google Cloud Agent Builder multi-agent architecture pattern for production adoption

---

## Architecture

```
Customer Voice
      │
Dialogflow CX (Cloud STT streaming)
      │
┌─────────────────────────────────────────┐
│  ROOT AGENT — Insurance Sales Supervisor│  ← Google Cloud Agent Builder
│  Gemini 2.0 Flash · Multi-turn state    │    Vertex AI data store grounding
│  System prompt: supervisor logic        │
└──────────┬──────────────────────────────┘
           │  orchestrates 3 sub-agents
    ┌──────┼──────────────────┐
    ▼      ▼                  ▼
[Sub-Agent 1]    [Sub-Agent 2]       [Sub-Agent 3]
Product Search   Compliance Guard    Recommendation Explainer
    │                 │                     │
[Elastic MCP]   [compliance_check     [rank_products
 search tool     Cloud Function]       Cloud Function]
 ELSER hybrid
    │
[Elasticsearch Cloud — ELSER v2 semantic_text — insurance_products index]
    │
Cloud TTS WaveNet → Voice Response
```

### Key Technical Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Primary orchestrator | Google Cloud Agent Builder (multi-agent ADK) | Hackathon requirement; native supervisor pattern |
| Search AI | Elasticsearch + ELSER v2 via Elastic MCP | Sparse vector semantic search; Elastic partner requirement |
| Guardrail implementation | Cloud Function (deterministic rule engine) | Fast, testable, zero hallucination risk for compliance logic |
| Voice | Dialogflow CX + Cloud STT + Cloud TTS WaveNet | Native GCP integration; low-latency streaming |
| LLM | Gemini 2.0 Flash | Speed + cost; sufficient for intake parsing + explanation |

---

## Repository Structure

```
insure-voice-agent/
├── docs/                          # All specs, plans, architecture docs
│   ├── HACKATHON-PLAN.md          # Full technical build plan (phased)
│   ├── CEO-PITCH-AND-BUDGET.md    # Business case + budget estimate
│   └── ARCHITECTURE.md            # Deep-dive architecture + decisions
├── Requirements/                  # Original challenge brief + roadmap context
├── data/                          # Synthetic insurance product catalog
│   └── insurance_products.json    # 25–30 products for Elasticsearch index
├── ingest/                        # Elasticsearch setup + ingestion scripts
│   ├── create_index.py            # Index schema + ELSER inference endpoint
│   └── index_products.py          # Bulk ingest script
├── functions/                     # Google Cloud Functions
│   ├── compliance_check/          # Guardrail: eligibility rule engine
│   │   └── main.py
│   └── rank_products/             # Top-3 scorer + ranking
│       └── main.py
├── agent_builder/                 # Agent Builder configurations
│   ├── root_agent_prompt.md       # Supervisor system prompt
│   └── tools.yaml                 # OpenAPI specs for Cloud Function tools
├── dialogflow/                    # Dialogflow CX agent export
├── infra/                         # Infrastructure as code
│   └── cloudbuild.yaml            # CI/CD pipeline
├── tests/                         # Unit + integration tests
├── README.md                      # This file
└── LICENSE                        # Apache 2.0
```

---

## Demo Scenarios

Four scenarios are scripted and pre-tested for the demo video:

| # | Input | Expected Outcome |
|---|---|---|
| A | "I'm 35, married, ₹12L income, need life cover for my family" | Top-3 term life products, voice explanation |
| B | "I'm 42, smoker, want a ULIP investment plan" | Guardrail blocks ULIP (smoker exclusion) + offers eligible alternatives |
| C | "I need comprehensive illness protection" | ELSER matches Critical Illness Rider via semantic understanding — no keyword overlap |
| D | Accept recommendation → "proceed with application" | Phase 2 workflow trigger fires (Pub/Sub event shown) |

---

## Setup

### Prerequisites

- Google Cloud project with billing enabled
- Elastic Cloud account (14-day free trial sufficient)
- Python 3.11+
- Docker (for Elastic MCP server)

### Google Cloud APIs to Enable

```bash
gcloud services enable \
  aiplatform.googleapis.com \
  dialogflow.googleapis.com \
  speech.googleapis.com \
  texttospeech.googleapis.com \
  run.googleapis.com \
  cloudfunctions.googleapis.com \
  cloudbuild.googleapis.com
```

### Elastic Cloud Setup

1. Create a deployment on [Elastic Cloud](https://cloud.elastic.co) with ML tier enabled
2. Create ELSER inference endpoint:
```bash
PUT _inference/sparse_embedding/elser-v2-endpoint
{
  "service": "elasticsearch",
  "service_settings": {
    "adaptive_allocations": { "enabled": true, "min_number_of_allocations": 1 },
    "num_threads": 1,
    "model_id": ".elser_model_2"
  }
}
```
3. Run Elastic MCP server:
```bash
docker run \
  -e ES_URL=https://your-deployment.es.io:443 \
  -e ES_API_KEY=your_api_key \
  -p 3000:3000 \
  docker.elastic.co/mcp/elasticsearch
```

### Ingest Insurance Product Catalog

```bash
cd ingest
pip install elasticsearch
python create_index.py
python index_products.py
```

### Deploy Cloud Functions

```bash
cd functions/compliance_check
gcloud functions deploy compliance_check --runtime python311 --trigger-http --allow-unauthenticated

cd ../rank_products
gcloud functions deploy rank_products --runtime python311 --trigger-http --allow-unauthenticated
```

---

## Budget

| Item | Cost |
|---|---|
| Google Cloud (Vertex AI, Agent Builder, STT/TTS, Functions) | ~₹9,000–15,000 ($110–185) |
| Elastic Cloud | ₹0 (14-day free trial) |
| GitHub | ₹0 |
| **Total direct cash** | **₹9,000–15,000** |

Full budget breakdown and CEO business case: [docs/CEO-PITCH-AND-BUDGET.md](docs/CEO-PITCH-AND-BUDGET.md)

---

## Team

| Role | Responsibility |
|---|---|
| Engineer 1 | Agent Builder multi-agent setup + Cloud Functions |
| Engineer 2 | Dialogflow CX + voice integration (STT/TTS) |
| Engineer 3 | Elasticsearch + ELSER data pipeline + DevOps |

---

## License

This project is licensed under the **Apache License 2.0** — see the [LICENSE](LICENSE) file for details.

---

## Acknowledgements

- [Google Cloud Agent Builder](https://cloud.google.com/products/agent-builder)
- [Elastic MCP Server](https://www.elastic.co/docs/solutions/search/mcp)
- [ELSER v2 — Elastic Learned Sparse EncodeR](https://www.elastic.co/guide/en/machine-learning/current/ml-nlp-elser.html)
- Hackathon: *Building Agents for Real-World Challenges* — Elastic Partner Track

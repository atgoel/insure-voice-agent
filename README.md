# InsureVoice — AI-Powered Insurance Sales Recommendation Agent

![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)
![Track](https://img.shields.io/badge/hackathon-Elastic%20Partner%20Track-brightgreen)
![Platform](https://img.shields.io/badge/platform-Google%20Cloud%20Agent%20Builder-orange)
![Search](https://img.shields.io/badge/search-Elasticsearch%20ELSER%20v2-005571)

> **Hackathon**: Building Agents for Real-World Challenges — Elastic Partner Track (Financial Services)
> **Built with**: Google Cloud Agent Builder · Gemini 2.5 Flash Lite + Flash · Elastic MCP · ELSER v2 · Chirp 3 HD TTS · Speech-to-Text v2

---

## Tier B Voice-Stack (Day 8 — 2026-06-05)

The original browser-native voice stack (Web Speech API STT + `SpeechSynthesisUtterance` TTS) has been replaced with a Google Cloud voice stack and an LLM intent classifier. Tier B is **in-tree on `stable_v4` but NOT yet deployed** — live revision `00030-jc7` still serves the Day 7 baseline. Smoke + traffic promotion deferred to Day 9.

| Sub-task | What it adds | Status |
|---|---|---|
| **B1 — Chirp 3 HD streaming TTS** | `agent_builder/tts_streaming.py` (396 lines). Voice `en-IN-Chirp3-HD-Aoede`, 24kHz MP3. Public API `synthesize_bytes` / `synthesize_chunks`. PoC measured 1.57s cold start. New endpoint `POST /tts/stream` with in-memory per-IP rate limit (30 req/min). | DELIVERED |
| **B2 — Speech-to-Text v2 streaming** | `agent_builder/stt_websocket.py` (549 lines) + `frontend/voice/stt-client.js` + `frontend/voice/audio-worklet-processor.js`. Speech-to-Text v2 + Chirp 2 model + native VAD tuned to 800ms. New endpoint `WebSocket /stt/stream`. en-IN. AudioWorklet 16kHz PCM mic capture. | DELIVERED |
| **B4 — Flash-Lite intent classifier** | `agent_builder/intent_classifier.py` (565 lines). Separate `LlmAgent` sub-agent with its own ADK Runner under `app_name="insure-voice-classifier"` (D10 fix — root agent runs as `app_name="insure-voice"`). 4 categories: `NAMED_PRODUCT` / `ORDINAL` / `POLICY_QUESTION` / `AMBIGUOUS`. Confidence threshold 0.7; force-clarify band (0.5, 0.7). Public API `classify_intent_async` / `classify_followup_intent` / `init_classifier_runner`. Own `before_model_callback=_force_classifier_tool`. Feature-flagged via `USE_LLM_INTENT_CLASSIFIER` env var (default off). | DELIVERED |
| **FE wiring** | `frontend/voice-player.js` (428 lines) + the two `voice/` modules above. D8 contract: B2 publishes `window.__voiceAudioCtx` + `window.__voiceMicSuspended`; B1 reads only and toggles them around `<audio>` playback. 200ms echo-tail delay on `<audio>.onended` before `.resume()`. | DELIVERED |
| **B3 — Silero VAD** | Open-source ONNX neural VAD in the browser. | **DROPPED (D1)** — hackathon rule "all other AI tools not permitted". |
| **B5 / B6 / B7** | Tool-result-only render, backchannel injection, ADK eval smoke harness. | **DEFERRED — Day 9+** |

**Endpoint registration order rule:** in `agent_builder/main.py`, the WebSocket route `/stt/stream` MUST be registered BEFORE the StaticFiles mount on `/`. StaticFiles wildcards `/`-prefixed paths and will swallow the WebSocket upgrade if registered first.

**Test suite:** **567 passed / 29 skipped / 0 failed** (28.86s on `stable_v4`). Up from the 551-pass baseline by 16 tests: +12 from `tests/test_intent_classifier.py` + 4 from `tests/test_b2_resume_tail.py`. New B4 golden fixture at `tests/fixtures/bug_j_golden.json` (15 hand-authored cases against the 28-product catalog).

**Live deploy status:** rev `00030-jc7` (Day 7 baseline). Tier B not yet deployed; Day 9 plan is `--no-traffic` deploy + browser smoke + AC-B4.11 latency probe before promoting traffic.

For the implementation reports (B1, B2, B4, FE_Merge, Reviewer_Pass, G1/G3/G4 pre-flight gates) see `tasks/2026-06-05_hackathon_day8_tier_b_implementation/reports/` in the workspace. The plain-English walkthrough is `Tier_B_Plain_English_Walkthrough.md`.

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
Customer Voice (mic)
      │
AudioWorklet 16kHz PCM Int16 LE → WebSocket /stt/stream    ← B2 (stable_v4)
      │
Speech-to-Text v2 + Chirp 2 model + native VAD (800ms, en-IN)
      │  text
┌─────────────────────────────────────────┐
│  ROOT AGENT — Insurance Sales Supervisor│  ← Google Cloud Agent Builder
│  Gemini 2.5 Flash-Lite · Multi-turn FSM │    app_name="insure-voice"
│  before_model_callback C.5 (untouched)  │    + intake.py state machine
└──────────┬──────────────────────────────┘
           │  on follow-up turns (USE_LLM_INTENT_CLASSIFIER=true):
           ▼
┌─────────────────────────────────────────┐
│  B4 INTENT CLASSIFIER (sub-agent)       │  ← Day 8 (2026-06-05)
│  Gemini 2.5 Flash-Lite · separate Runner│    app_name="insure-voice-classifier"
│  NAMED_PRODUCT/ORDINAL/POLICY_QUESTION/ │    own _force_classifier_tool callback
│  AMBIGUOUS · confidence ≥ 0.7 routes;   │
│  (0.5, 0.7) force-clarify; <0.5 fallback│
└─────────────────────────────────────────┘
           │  orchestrates 3 sub-agents
    ┌──────┼──────────────────┐
    ▼      ▼                  ▼
[Sub-Agent 1]    [Sub-Agent 2]       [Sub-Agent 3]
Product Search   Compliance Guard    Recommendation Explainer
    │                 │                     │
[product_search  [compliance_check    [rank_products
 Cloud Function]  Cloud Function]      Cloud Function]
    │  POST /mcp
[Elastic MCP Server]  ← Constitution §VI primary search integration
 Cloud Run · FastMCP · search_products tool
    │  elasticsearch-py
[Elasticsearch Cloud Serverless — ELSER v2 semantic_text — insurance_products_current]
    │
Cloud TTS Chirp 3 HD (`en-IN-Chirp3-HD-Aoede`, 24kHz MP3) → Voice Response
   ▲
   │ via POST /tts/stream  (Day 8 Tier B; STT path is WebSocket /stt/stream + Chirp 2)
```

### Key Technical Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Primary orchestrator | Google Cloud Agent Builder (multi-agent ADK) | Hackathon requirement; native supervisor pattern |
| Search AI | Elasticsearch + ELSER v2 via Elastic MCP Server | Sparse vector semantic search; Elastic partner requirement §VI |
| Guardrail implementation | Cloud Function (deterministic rule engine) | Fast, testable, zero hallucination risk for compliance logic |
| Voice TTS | Google Cloud Text-to-Speech — Chirp 3 HD (`en-IN-Chirp3-HD-Aoede`, 24kHz MP3) via `POST /tts/stream` | Day 8 Tier B swap; replaces browser `SpeechSynthesisUtterance`. 1.57s cold-start PoC. |
| Voice STT | Google Cloud Speech-to-Text v2 + Chirp 2 model with native VAD (800ms) via `WebSocket /stt/stream` | Day 8 Tier B swap; replaces browser `webkitSpeechRecognition`. AudioWorklet 16kHz PCM mic capture. |
| Intent classification (follow-up turns) | Gemini 2.5 Flash-Lite separate sub-agent (Day 8 B4) under `app_name="insure-voice-classifier"`, feature-flagged via `USE_LLM_INTENT_CLASSIFIER` | Replaces brittle regex in `followup.py`. Confidence < 0.7 falls back to legacy regex. Default OFF. |
| LLM | Gemini 2.5 Flash-Lite (root, temperature 0.25) + Gemini 2.5 Flash (recommend_and_explain sub-agent, temperature 0.3) | Speed + cost; deterministic Python state machines handle high-frequency turns (intake/follow-up) so LLM is only invoked on the pipeline turn. |

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
├── functions/                     # Google Cloud services
│   ├── elastic_mcp_server/        # Elastic MCP Server (Cloud Run) — §VI primary search integration
│   │   ├── main.py                # FastMCP + FastAPI: search_products REST + MCP tool → Elasticsearch
│   │   ├── requirements.txt       # fastmcp, fastapi, elasticsearch, uvicorn
│   │   └── Dockerfile             # Cloud Run container image
│   ├── compliance_check/          # Cloud Function: deterministic eligibility rule engine
│   │   └── main.py
│   └── rank_products/             # Cloud Function: top-3 scorer + ranking
│       └── main.py
├── agent_builder/                 # Agent Builder configurations
│   ├── root_agent_prompt.md       # Supervisor system prompt
│   ├── sub_agent1_search_prompt.md # Sub-Agent 1 (Product Search) delegation prompt
│   ├── tools.yaml                 # OpenAPI specs for Cloud Function tools
│   ├── tts_streaming.py           # B1 (Day 8) — Chirp 3 HD streaming TTS
│   ├── stt_websocket.py           # B2 (Day 8) — Speech-to-Text v2 WebSocket bridge
│   ├── intent_classifier.py       # B4 (Day 8) — Flash-Lite intent classifier sub-agent
│   ├── followup.py                # CANONICAL_FAREWELL_TEXT + regex follow-up dispatch
│   ├── intake.py                  # 8-field deterministic intake state machine
│   ├── shared_state.py            # in-memory session state + LAST_RENDERED_BY_SESSION dedup
│   └── frontend/                  # static FE assets (mounted at / by FastAPI)
│       ├── voice-player.js        # B1 FE — MediaSource MP3 player, D8 read-only globals
│       ├── voice/stt-client.js    # B2 FE — WebSocket STT client + AudioWorklet pump
│       ├── voice/audio-worklet-processor.js # B2 FE — 16kHz PCM Int16 LE encoder
│       ├── simulation.js          # voice UI engine (intake + recommendation)
│       ├── index.html             # main page
│       └── app.js                 # session bootstrap + transcript renderer
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
  speech.googleapis.com \
  texttospeech.googleapis.com \
  run.googleapis.com \
  cloudfunctions.googleapis.com \
  cloudbuild.googleapis.com
# Note (Day 8 — 2026-06-05): dialogflow.googleapis.com no longer required.
# Dialogflow CX has been removed end-to-end; the agent uses a FastAPI /invoke
# handler with deterministic Python state machines (intake.py + followup.py).
```

### Elastic Cloud Setup

1. Create an **Elastic Cloud Serverless** project at [cloud.elastic.co](https://cloud.elastic.co) (Elasticsearch Serverless).
   - Serverless uses the built-in Elastic Inference Service (EIS) for `semantic_text` fields — no manual ELSER endpoint creation required.
2. Copy your **Cloud ID / endpoint URL** and an **API Key** with `indices:data/write` and `indices:admin/create` permissions into your `.env`.
3. Run the ingest scripts (see below) — ELSER inference is applied automatically on ingestion.

### Ingest Insurance Product Catalog

```bash
cd ingest
pip install elasticsearch
python create_index.py
python index_products.py
```

### Deploy Cloud Functions

Use Cloud Build for a full pipeline deploy (copies shared/ module, sets secrets, deploys all three functions):

```bash
gcloud builds submit \
  --substitutions=_ES_URL=https://<your-deployment>.es.io:443,_ES_API_KEY_SECRET=ES_API_KEY,_REGION=us-central1
```

Or deploy individually:

```bash
# compliance_check
cp -r shared/ functions/compliance_check/shared/
gcloud functions deploy compliance_check --runtime python311 --trigger-http --allow-unauthenticated --source=functions/compliance_check

# rank_products
cp -r shared/ functions/rank_products/shared/
gcloud functions deploy rank_products --runtime python311 --trigger-http --allow-unauthenticated --source=functions/rank_products

# product_search (no shared/ dependency)
gcloud functions deploy product_search --runtime python311 --trigger-http --allow-unauthenticated \
  --source=functions/product_search \
  --set-env-vars=ES_URL=https://<your-deployment>.es.io:443 \
  --set-secrets=ES_API_KEY=ES_API_KEY:latest
```

---

## Development Setup

### Environment Variables

Create a `.env` file (never commit it — it is in `.gitignore`):

```bash
# Elasticsearch Cloud connection
ES_URL=https://<your-deployment>.es.io:443
ES_API_KEY=<your-api-key>

# Deployed Cloud Function URLs (pre-linked to voice-sales-agent)
COMPLIANCE_CHECK_URL=https://us-central1-voice-sales-agent.cloudfunctions.net/compliance_check
RANK_PRODUCTS_URL=https://us-central1-voice-sales-agent.cloudfunctions.net/rank_products
PRODUCT_SEARCH_URL=https://us-central1-voice-sales-agent.cloudfunctions.net/product_search
```

Alternatively, you can copy `.env.example` to `.env` which already has these pre-configured for the `voice-sales-agent` GCP project.

Load the variables in your shell before running scripts:

```bash
# Linux / macOS
export $(grep -v '^#' .env | xargs)

# Windows PowerShell
Get-Content .env | ForEach-Object { if ($_ -notmatch '^#') { $v=$_.Split('=',2); [System.Environment]::SetEnvironmentVariable($v[0],$v[1]) } }
```

### Install Dependencies

```bash
# Cloud Function dependencies (install per function)
pip install -r functions/compliance_check/requirements.txt
pip install -r functions/rank_products/requirements.txt
pip install -r functions/product_search/requirements.txt

# Dev / test dependencies
pip install pytest openapi-spec-validator elasticsearch
```

### Tier B Voice-Stack runtime dependencies (Day 8 — 2026-06-05)

`agent_builder/requirements.txt` adds three packages required by the Tier B modules. Cloud Build picks them up on the next deploy; for local runs install them into the agent's venv:

```bash
pip install google-cloud-texttospeech>=2.14.1   # B1 — Chirp 3 HD TTS (POST /tts/stream)
pip install google-cloud-speech>=2.27.0          # B2 — Speech-to-Text v2 (WebSocket /stt/stream)
pip install google-genai==1.75.0                 # B4 — pinned for the classifier Runner (D11)
```

`google-cloud-speech` is **required** for Tier B STT. If the package is missing at import time, `WebSocket /stt/stream` returns `SDK_UNAVAILABLE` gracefully — the rest of the app keeps working, but the voice-input path is dead.

### Feature flags

| Env var | Default | Effect |
|---|---|---|
| `USE_LLM_INTENT_CLASSIFIER` | `false` (off) | When `true`, follow-up turns invoke the Gemini Flash-Lite intent classifier (B4) before falling back to the existing regex path in `followup.py`. Confidence < 0.7 still falls back to regex. |

### Run Tests

```bash
# Full test suite
pytest tests/ -v

# Single file
pytest tests/test_compliance_check.py -v

# With short tracebacks (CI-friendly)
pytest tests/ --tb=short
```

Expected output (Day 8 — 2026-06-05): **567 passed / 29 skipped / 0 failed** (~28.86s). Up from the 551-pass Phase-1-only baseline by +12 from `tests/test_intent_classifier.py` (B4) + 4 from `tests/test_b2_resume_tail.py` (B1↔B2 echo-tail contract).

### Validate OpenAPI Spec

```bash
python -m openapi_spec_validator agent_builder/tools.yaml
# Expected: agent_builder/tools.yaml: OK
```

### Run Ingest (requires ES_URL + ES_API_KEY)

```bash
# Create the versioned index + alias
python ingest/create_index.py

# Force-recreate if the index already exists
python ingest/create_index.py --delete-existing

# Bulk-ingest the 28-product catalog
python ingest/index_products.py
```

---

## Usage

### Invoke the InsureVoice Agent

The agent exposes the following endpoints on Cloud Run:

| Method | Path | Purpose | Notes |
|---|---|---|---|
| `GET` | `/health` | Liveness probe | Returns `{status, agent, project, location}`. |
| `POST` | `/invoke` | Main turn handler | Single + multi-turn JSON over HTTP. |
| `POST` | `/tts/stream` | Tier B B1 — Chirp 3 HD streaming TTS | Per-IP rate limit **30 req/min** (in-memory `collections.deque`, returns 429 on breach). MP3 chunks. **Day 8 — not yet on live revision `00030-jc7`.** |
| `WebSocket` | `/stt/stream` | Tier B B2 — Speech-to-Text v2 streaming | Browser AudioWorklet → 16kHz PCM frames → STT v2 + Chirp 2. Server-side VAD 800ms. **Must be registered BEFORE the StaticFiles mount in `main.py`** or StaticFiles will swallow the WebSocket upgrade. **Day 8 — not yet on live revision `00030-jc7`.** |

**Live demo URL:** `https://insure-voice-agent-mhojvvbq4a-uc.a.run.app/` (Day 7 baseline rev `00030-jc7`).

**Health check**

```bash
curl https://insure-voice-agent-1055350728739.us-central1.run.app/health
# {"status":"ok","agent":"insure-voice","project":"voice-sales-agent","location":"us-central1"}
```

**Single-turn recommendation**

```bash
curl -X POST https://insure-voice-agent-1055350728739.us-central1.run.app/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "message": "I am 35 years old, non-smoker, annual income 1.2 million INR. I need term life cover for my family of four."
  }'
```

Response:

```json
{
  "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "response": "Based on your profile, here are my top three recommendations. First, SecureLife Term Plan — ₹1 crore cover at roughly ₹8,000 per year, ideal for a 35-year-old with your income. Second, FamilyShield Plus offers broader riders at ₹9,500 per year. Third, PureProtect Term covers you up to age 75 with a waiver of premium benefit. Would you like to know more about any of these?"
}
```

**Multi-turn follow-up** (pass `session_id` from the first response)

```bash
# Follow-up: ask for more detail on the first recommendation
curl -X POST https://insure-voice-agent-1055350728739.us-central1.run.app/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Tell me more about the first one.",
    "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
  }'

# Profile reset: start over with a different budget
curl -X POST https://insure-voice-agent-1055350728739.us-central1.run.app/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Let me try with a different budget — income is now 2 million INR.",
    "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
  }'
```

The agent maintains full session context for follow-up questions. Passing a new `session_id` (or omitting it) starts a fresh conversation.

---



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
| Engineer 2 | Voice integration (STT v2 + Chirp 3 HD TTS via FastAPI; D8 echo-cancellation contract) |
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

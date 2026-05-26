# InsureVoice — Architecture Deep Dive
## Google Cloud Agent Builder + Elastic ELSER + Dialogflow CX

---

## System Overview

InsureVoice is a multi-agent AI system with four distinct functional layers. Each layer is independently deployable and testable.

```
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 1 — VOICE INTERFACE                                      │
│  Dialogflow CX · Cloud STT (streaming) · Cloud TTS WaveNet     │
└───────────────────────────┬─────────────────────────────────────┘
                            │ structured conversation turns
┌───────────────────────────▼─────────────────────────────────────┐
│  LAYER 2 — AGENT ORCHESTRATION (Agent Builder ADK)             │
│  Root Agent (Supervisor) · 3 Sub-Agents · Vertex AI data store │
└───────┬───────────────────┬──────────────────────┬─────────────┘
        │                   │                      │
┌───────▼───────┐  ┌────────▼────────┐  ┌──────────▼──────────┐
│ Sub-Agent 1   │  │  Sub-Agent 2    │  │  Sub-Agent 3        │
│ Product Search│  │  Compliance     │  │  Explainer          │
│               │  │  Guardrail      │  │                     │
│ Elastic MCP   │  │ Cloud Function  │  │ Cloud Function      │
│ ELSER hybrid  │  │ compliance_check│  │ rank_products       │
└───────┬───────┘  └─────────────────┘  └─────────────────────┘
        │
┌───────▼─────────────────────────────────────────────────────────┐
│  LAYER 3 — SEARCH INTELLIGENCE (Elastic)                        │
│  Elasticsearch Cloud · ELSER v2 · semantic_text fields          │
│  Hybrid search: BM25 + sparse vectors + structured filters      │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layer 1: Voice Interface

### Components

| Component | Technology | Role |
|---|---|---|
| Speech-to-Text | Google Cloud STT (streaming) | Transcribes customer voice in real time |
| Conversation Manager | Dialogflow CX | Manages intents, session state, routing to Agent Builder |
| Text-to-Speech | Google Cloud TTS WaveNet (`en-IN-Wavenet-D`) | Synthesizes agent response as voice |

### Dialogflow CX Intent Design

```
├── Default Welcome Intent        → greet + prompt for customer profile
├── provide_profile               → captures: age, income, health, smoker, goals
│   └── triggers: recommend flow → Root Agent
├── ask_about_product             → ad-hoc product Q&A → Vertex AI data store
├── accept_recommendation         → triggers Phase 2 workflow stub
└── reject_recommendation         → ask for different preferences → loop back
```

### Voice Latency Targets

| Step | Target Latency |
|---|---|
| STT transcription | < 1.5s (streaming, end-of-utterance detection) |
| Agent Builder + sub-agents | < 5s |
| TTS synthesis | < 0.5s |
| **Total end-to-end** | **< 8s** |

---

## Layer 2: Agent Orchestration

### Agent Builder Multi-Agent (ADK) Architecture

Agent Builder's ADK supports hierarchical multi-agent orchestration. One Root Agent (the "supervisor") delegates to specialized Sub-Agents in sequence.

```
Root Agent receives: raw voice transcript
Root Agent does:
  1. Extract structured customer profile using Gemini function-calling
     Profile: {age, income, smoker, health_status, sum_need, coverage_goals[]}
  2. Call Sub-Agent 1 with structured query → receives candidate products
  3. Call Sub-Agent 2 with candidates + profile → receives filtered products
  4. Call Sub-Agent 3 with filtered products → receives ranked recommendations
  5. Return explanation to Dialogflow CX for TTS delivery
```

### Root Agent System Prompt Design (`agent_builder/root_agent_prompt.md`)

```
You are InsureVoice, an AI-powered insurance sales advisor.

ROLE: You help insurance sales agents match customers to the right insurance products.

PROCESS — always follow these steps in order:
1. EXTRACT: Parse the customer's voice input to extract:
   - Age (years), Monthly/Annual income (INR), Smoker (yes/no)
   - Health status (healthy / pre-existing conditions), Family size
   - Coverage goals (life, health, investment, critical illness, accident)
   - Desired sum assured (if stated)

2. SEARCH: Use the Product Search Agent to find candidate products matching the profile.

3. VALIDATE: Use the Compliance Guardrail Agent to filter out ineligible products.
   CRITICAL: Never recommend a product that the Compliance Agent has rejected.

4. EXPLAIN: Use the Recommendation Explainer Agent to rank and explain top-3 products.

5. RESPOND: Deliver the top-3 recommendations in a warm, conversational voice tone.
   Format: "Based on your profile, here are my top 3 recommendations for you..."

GUARDRAILS:
- Never recommend a product if the Compliance Agent returns it as rejected
- If ALL products are rejected, explain why and ask for updated profile information
- Never make medical or legal claims about insurance products
- Always clarify that final underwriting is subject to insurer terms
```

### Sub-Agent 1: Product Search Agent

**Tool**: Elastic MCP `search`

**Input** (from Root Agent):
```json
{
  "query": "term life insurance family protection",
  "filters": {
    "age_range": [35, 35],
    "income_min": 1200000,
    "smoker": false
  }
}
```

**Elasticsearch Query** (generated from above):
```json
{
  "query": {
    "bool": {
      "should": [
        {
          "semantic": {
            "field": "description",
            "query": "term life insurance family protection"
          }
        },
        {
          "match": {
            "name": { "query": "term life family", "boost": 1.5 }
          }
        }
      ],
      "filter": [
        { "range": { "min_age": { "lte": 35 } } },
        { "range": { "max_age": { "gte": 35 } } },
        { "term": { "smoker_eligible": true } }
      ],
      "minimum_should_match": 1
    }
  },
  "size": 10
}
```

**Why ELSER over pure BM25**: A customer saying "comprehensive illness protection for my family" will NOT match "Critical Illness Rider" by keywords. ELSER's sparse vectors encode semantic association — "illness protection" → "critical illness", "family" → "family floater". BM25 would return zero results; ELSER returns the correct product.

### Sub-Agent 2: Compliance Guardrail Agent

**Tool**: `compliance_check` Cloud Function

**Rule Set** (`functions/compliance_check/main.py`):

```python
COMPLIANCE_RULES = [
    {
        "rule_id": "AGE_MIN",
        "check": lambda p, c: c["age"] >= p["min_age"],
        "rejection_reason": lambda p, c: f"Minimum age for this product is {p['min_age']}; customer is {c['age']}"
    },
    {
        "rule_id": "AGE_MAX",
        "check": lambda p, c: c["age"] <= p["max_age"],
        "rejection_reason": lambda p, c: f"Maximum entry age for this product is {p['max_age']}; customer is {c['age']}"
    },
    {
        "rule_id": "SMOKER_EXCLUSION",
        "check": lambda p, c: not (c["smoker"] and not p["smoker_eligible"]),
        "rejection_reason": lambda p, c: "This product is not available for smokers"
    },
    {
        "rule_id": "INCOME_SUM_CAP",
        "check": lambda p, c: c.get("sum_need", 0) <= c["income"] * 10,
        "rejection_reason": lambda p, c: f"Requested sum assured exceeds 10x annual income cap"
    },
    {
        "rule_id": "MEDICAL_EXAM_REQUIRED",
        "check": lambda p, c: not (c.get("sum_need", 0) > p.get("medical_required_above", float('inf')) and c.get("health_status") != "healthy"),
        "rejection_reason": lambda p, c: "Medical examination required for this sum assured with declared health conditions"
    }
]
```

**Why Cloud Function (not LLM)**: Compliance rules must be 100% deterministic. An LLM can hallucinate or misapply rules. Using a Cloud Function means the guardrail is auditable, testable, and never wrong.

### Sub-Agent 3: Recommendation Explainer Agent

**Tool**: `rank_products` Cloud Function

**Scoring Formula**:
```
suitability_score = (elser_score × 0.4) + (age_centrality × 0.3) + (income_fit × 0.3)

where:
  age_centrality  = 1 - |age - product_midpoint_age| / (max_age - min_age)
  income_fit      = min(income / (sum_need / 10), 1.0)  # how comfortably income covers premium
```

**Output** (top-3 per product):
```json
{
  "rank": 1,
  "product_name": "SecureLife Term Plan Plus",
  "product_type": "Term Life",
  "suitability_score": 0.87,
  "voice_explanation": "My top recommendation is SecureLife Term Plan Plus. At your age of 35, this gives you ₹1 crore life cover at just ₹800 per month. It's designed for married professionals with dependents — exactly your situation. Critical illness is covered as a rider at no extra cost for the first year."
}
```

---

## Layer 3: Search Intelligence

### Elasticsearch Index Schema

```json
PUT /insurance_products
{
  "mappings": {
    "properties": {
      "id":                    { "type": "keyword" },
      "name":                  { "type": "text", "fields": { "keyword": { "type": "keyword" } } },
      "product_type":          { "type": "keyword" },
      "description":           { "type": "semantic_text", "inference_id": "elser-v2-endpoint" },
      "min_age":               { "type": "integer" },
      "max_age":               { "type": "integer" },
      "smoker_eligible":       { "type": "boolean" },
      "min_income":            { "type": "long" },
      "max_sum_assured":       { "type": "long" },
      "medical_required_above":{ "type": "long" },
      "exclusions":            { "type": "keyword" },
      "coverage_type":         { "type": "keyword" },
      "premium_min_monthly":   { "type": "integer" },
      "premium_max_monthly":   { "type": "integer" }
    }
  }
}
```

### ELSER Inference Endpoint

```json
PUT _inference/sparse_embedding/elser-v2-endpoint
{
  "service": "elasticsearch",
  "service_settings": {
    "adaptive_allocations": {
      "enabled": true,
      "min_number_of_allocations": 1,
      "max_number_of_allocations": 4
    },
    "num_threads": 1,
    "model_id": ".elser_model_2"
  }
}
```

### Elastic MCP Server

```bash
docker run \
  --name elastic-mcp \
  -e ES_URL=https://your-deployment.es.io:443 \
  -e ES_API_KEY=your_base64_api_key \
  -p 3000:3000 \
  docker.elastic.co/mcp/elasticsearch
```

**MCP Tools exposed**:
- `search` — execute Elasticsearch Query DSL (used by Sub-Agent 1)
- `list_indices` — list available indices
- `get_mappings` — inspect field structure
- `esql` — ES|QL query interface (available for advanced queries)

Agent Builder connects to the MCP server endpoint (`http://elastic-mcp:3000/mcp`) and uses the `search` tool as a registered tool in Sub-Agent 1.

---

## Data Flow Sequence

```
Customer: "I'm 35, married, non-smoker, ₹12L income, need life cover for family"
    │
    ▼ Cloud STT (streaming, ~1.2s)
Transcript: "I'm 35 married non-smoker 12 lakh income need life cover for family"
    │
    ▼ Dialogflow CX (provide_profile intent matched)
    │
    ▼ Root Agent — Gemini function-call: extract_profile()
Profile: {age: 35, smoker: false, income: 1200000, coverage_goals: ["life", "family"], sum_need: 10000000}
    │
    ▼ Sub-Agent 1 — Elastic MCP search
Candidates: [SecureLife Term Plus, IndiaProtect 2 Crore, FamilyShield Pro, LifeMax ULIP, ...] (10 results)
    │
    ▼ Sub-Agent 2 — compliance_check Cloud Function
Passed:   [SecureLife Term Plus, IndiaProtect 2 Crore, FamilyShield Pro]
Rejected: [LifeMax ULIP] → reason: "ULIP products require minimum 3-year income continuity declaration"
    │
    ▼ Sub-Agent 3 — rank_products Cloud Function + Gemini explanation
Top 3: SecureLife (0.87), IndiaProtect (0.79), FamilyShield (0.71)
    │
    ▼ Root Agent — compose voice response
"Based on your profile, here are my top 3 recommendations..."
    │
    ▼ Cloud TTS WaveNet (~0.4s)
Voice output → customer hears recommendation
```

**Total latency**: ~7.2 seconds (within 8s target)

---

## Phase 2: Workflow Integration (Post-Hackathon)

When customer accepts a recommendation:

```
accept_recommendation intent
    │
    ▼ Pub/Sub: topic = insurance.recommendation.accepted
    Message: {
      session_id, customer_profile, recommended_product_id,
      timestamp, agent_id, recommendation_score
    }
    │
    ▼ Workflow Engine (Camunda BPMN / Cloud Workflows)
    Process: Begin Insurance Application Workflow
      ├── KYC verification
      ├── Document collection
      ├── Underwriting trigger
      └── Proposal generation
```

---

## Security Considerations

| Concern | Mitigation |
|---|---|
| API keys in environment | GCP Secret Manager for Elastic API key + all credentials |
| Cloud Function auth | Require IAM authentication for compliance_check and rank_products in production |
| Customer voice data | Dialogflow CX: disable data logging for PII; STT audio not persisted |
| Elasticsearch access | API key scoped to `insurance_products` index only (read-only) |
| Open-source IP exposure | Synthetic data only; no real product pricing; no client data |

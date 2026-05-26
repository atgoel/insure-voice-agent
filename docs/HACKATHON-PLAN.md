# InsureVoice — Hackathon Technical Build Plan
## Elastic Partner Track | Google Cloud Agent Builder + ELSER + Dialogflow CX

**Date**: 2026-05-26
**Timeline**: 1–2 weeks | Team: 2–3 people
**Hackathon**: Building Agents for Real-World Challenges — Elastic Partner Track (Financial Services)

---

## Hackathon Fit Assessment

**Score: 9/10 for Elastic track** — strongest possible domain alignment.

| Criterion | Assessment |
|---|---|
| Elastic MCP integration | ELSER sparse vector search — deepest possible Elastic use; most teams use basic BM25 |
| Partner track alignment | Financial Services (one of 3 highlighted themes in challenge brief) |
| Multi-step agent mission | Intake → Decompose → Search → Guardrail → Rank → Explain |
| Beyond chat | Recommends, validates compliance, initiates workflow |
| Demo WOW factor | Voice + live guardrail rejection — most submissions will be text-only |

### Risks & Mitigations

| Risk | Mitigation |
|---|---|
| ELSER requires ML node cost | Elastic Cloud 14-day free trial; use EIS (Elastic Inference Service) |
| Live voice demo failure | Record backup demo video on Day 13 |
| Agent Builder scoping | 3 focused sub-agents with single tools each; no over-engineering |

---

## Architecture

```
Customer Voice
      │
Dialogflow CX (Cloud STT streaming)
      │
┌─────────────────────────────────────────┐
│  ROOT AGENT — Insurance Sales Supervisor│  ← Google Cloud Agent Builder (ADK)
│  Gemini 2.0 Flash · Multi-turn state    │    Vertex AI data store grounding
└──────────┬──────────────────────────────┘
           │  orchestrates 3 sub-agents in sequence
    ┌──────┼──────────────────┐
    ▼      ▼                  ▼
[Sub-Agent 1]    [Sub-Agent 2]       [Sub-Agent 3]
Product Search   Compliance Guard    Recommendation Explainer
    │                 │                     │
[Elastic MCP]   [compliance_check     [rank_products
 ELSER hybrid    Cloud Function]       Cloud Function]
    │
[Elasticsearch Cloud — ELSER v2 semantic_text]
    │
Cloud TTS WaveNet → Voice Response
```

### How Agent Builder Implements the Supervisor/Guardrail Patterns

| Pattern | Implementation |
|---|---|
| **Supervisor node** | Root Agent system prompt with routing logic; ADK multi-agent orchestration |
| **Guardrail filter** | Sub-Agent 2 "Compliance Guardrail Agent" + `compliance_check` Cloud Function |
| **Query decomposition** | Root Agent uses Gemini function-calling to extract structured profile from transcript |
| **Worker nodes** | 3 dedicated Sub-Agents (Search, Compliance, Explainer) each with one focused tool |

---

## Phase 0: Foundation Setup (Day 1–2) — All Parallel

1. **GCP project**: Enable APIs — Vertex AI, Agent Builder/ADK, Dialogflow CX, Cloud Run, Cloud Build, Cloud Functions, Speech-to-Text, TTS
2. **Elastic Cloud**: Create 14-day trial deployment; enable ML tier OR use Elastic Inference Service (EIS) for ELSER — avoids ML node ops overhead
3. **GitHub**: Create public repo with Apache 2.0 LICENSE at root *(required for judging eligibility)*
4. **Dev environment**: Python 3.11, `elasticsearch`, `google-cloud-aiplatform`, `fastapi`, Docker

---

## Phase 1: Data + Elastic Search Engine (Day 2–4) — Person 3

5. **Synthetic insurance catalog** (`data/insurance_products.json`) — 25–30 products:
   - Types: Term Life, Endowment, ULIP, Health (individual + family), Critical Illness Rider, Accidental Benefit
   - Each product fields: `id`, `name`, `product_type`, `description` (rich text for ELSER), `min_age`, `max_age`, `smoker_eligible`, `min_income`, `max_sum_assured`, `medical_required_above`, `exclusions[]`, `coverage_type[]`, `premium_range`

6. **Elasticsearch index schema** (`ingest/create_index.py`):
   - `description` field: `type: semantic_text`, `inference_id: elser-v2-endpoint`
   - All constraint fields: `type: keyword/integer/float` for filter clauses
   - Hybrid query pattern: `bool.should[semantic(description), match(title)]` + `filter` clauses

7. **ELSER inference endpoint**:
   ```json
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

8. **Ingest script** (`ingest/index_products.py`): reads JSON, routes through ELSER inference pipeline, bulk indexes

9. **Elastic MCP server test**:
   ```bash
   docker run -e ES_URL=https://your-deployment.es.io:443 \
              -e ES_API_KEY=your_key \
              -p 3000:3000 \
              docker.elastic.co/mcp/elasticsearch
   ```
   Verify `search` tool returns ELSER-scored results with token weights.

---

## Phase 2: Agent Builder Multi-Agent Setup (Day 4–8) — Person 1 + 2

**Agent Builder IS the supervisor. Three sub-agents, one root.**

10. **Root Agent — "Insurance Sales Supervisor"** (`agent_builder/root_agent_prompt.md`):
    - System prompt encodes full supervisor logic:
      > You are an insurance sales advisor. When a customer describes their needs, you must: (1) extract their profile, (2) search for matching products via the Product Search Agent, (3) validate eligibility via the Compliance Agent, (4) generate a ranked recommendation via the Explainer Agent, (5) deliver the response by voice. Maintain context across multiple turns.
    - Gemini 2.0 Flash; multi-turn conversation state
    - Connected to Vertex AI data store for product Q&A grounding

11. **Sub-Agent 1 — "Product Search Agent"**:
    - Single tool: Elastic MCP `search`
    - Receives structured query from Root Agent: `{age, income, smoker, coverage_goals[], sum_need}`
    - Executes hybrid BM25 + ELSER query with `filter` clauses for age/income bounds
    - Returns candidate products list with ELSER relevance scores

12. **Sub-Agent 2 — "Compliance Guardrail Agent"**:
    - Tool: `compliance_check` Cloud Function (`functions/compliance_check/main.py`)
    - Input: `{candidate_products[], customer_profile}`
    - Output: `{passed: [], rejected: [{product_id, product_name, reason}]}`
    - Compliance rules (hardcoded, deterministic — no LLM involved):
      - Age eligibility: `profile.age >= product.min_age AND profile.age <= product.max_age`
      - Smoker exclusion: `NOT (profile.smoker AND NOT product.smoker_eligible)`
      - Income sum cap: `profile.sum_need <= profile.income * 10`
      - Medical exam: `IF profile.sum_need > product.medical_required_above THEN profile.health_status == 'healthy'`

13. **Sub-Agent 3 — "Recommendation Explainer Agent"**:
    - Tool: `rank_products` Cloud Function (`functions/rank_products/main.py`)
    - Scoring: `elser_score × 0.4 + age_suitability × 0.3 + income_fit × 0.3`
    - Gemini generates voice-friendly explanation: "Based on your profile, here are my top 3 recommendations…"
    - Per-product: product name, coverage summary, why it fits, monthly premium estimate

14. **Vertex AI data store**: Upload `data/insurance_products.json` as grounding — Root Agent uses this for direct Q&A ("what is a ULIP?") without needing the full search pipeline

15. **OpenAPI tool spec** (`agent_builder/tools.yaml`): defines `compliance_check` and `rank_products` Cloud Function endpoints for Agent Builder to call

---

## Phase 3: Voice Integration (Day 7–10) — Person 2

*(Runs in parallel with Phase 2 tail-end)*

16. **Dialogflow CX agent**: linked to Agent Builder Root Agent
    - Intents: `start_recommendation`, `provide_profile`, `ask_about_product`, `accept_recommendation`, `reject_recommendation`
    - Audio input mode enabled

17. **Cloud Speech-to-Text**: streaming STT in Dialogflow CX
    - Model: `latest_long` with `financial` phrase hints
    - Language: `en-IN` for Indian English insurance terminology

18. **Cloud Text-to-Speech WaveNet**: configure in Dialogflow CX response
    - Voice: `en-IN-Wavenet-D` (natural, clear)
    - SSML: add pause after product names, emphasis on "top recommendation"

19. **End-to-end flow test**: speak profile → Root Agent routes → Product Search → Elastic MCP → ELSER results → Compliance Agent → filtered → Explainer Agent → TTS voice response

---

## Phase 4: Demo Polish + Submission (Day 11–14)

20. **Phase 2 workflow stub**: `accept_recommendation` intent → Pub/Sub message `insurance.recommendation.accepted` published — shown in demo as "Application workflow initiated"

21. **4 pre-scripted demo scenarios** (all must pass clean before video recording):

    | Scenario | Voice Input | Expected |
    |---|---|---|
    | A — Normal | "35, married, ₹12L income, need life cover" | Top-3 term life products |
    | B — Guardrail | "42, smoker, want a ULIP investment plan" | Blocked + non-smoker alternatives shown |
    | C — ELSER Semantic | "comprehensive illness protection for my family" | Matches Critical Illness Rider — no keyword overlap |
    | D — Phase 2 | Accepts recommendation | Workflow trigger event shown |

22. **3-minute demo video structure**:
    - 0:00–0:30 — Problem statement + architecture overview
    - 0:30–1:45 — Live demo: Scenarios A + B (guardrail rejection)
    - 1:45–2:30 — ELSER semantic search advantage (Scenario C)
    - 2:30–3:00 — Phase 2 workflow teaser + closing

23. **Devpost submission checklist**:
    - [ ] Hosted project URL (Dialogflow CX web demo interface)
    - [ ] Public GitHub repo URL with Apache 2.0 license visible
    - [ ] 3-minute demo video URL
    - [ ] Elastic partner track selected

---

## Files to Create

| File | Purpose |
|---|---|
| `data/insurance_products.json` | Synthetic 25–30 product catalog |
| `ingest/create_index.py` | Elasticsearch index schema + ELSER inference endpoint creation |
| `ingest/index_products.py` | Bulk ingest script |
| `functions/compliance_check/main.py` | Guardrail compliance rule engine (Cloud Function) |
| `functions/compliance_check/requirements.txt` | Python dependencies |
| `functions/rank_products/main.py` | Product scoring + ranking (Cloud Function) |
| `functions/rank_products/requirements.txt` | Python dependencies |
| `agent_builder/root_agent_prompt.md` | Root Agent supervisor system prompt |
| `agent_builder/tools.yaml` | OpenAPI specs for Cloud Function tools |
| `dialogflow/` | Dialogflow CX agent export (zip) |
| `infra/cloudbuild.yaml` | CI/CD pipeline |
| `tests/test_compliance.py` | Guardrail unit tests |
| `tests/test_search.py` | ELSER search integration tests |

---

## Verification Checklist

- [ ] `POST compliance_check` with `{age: 17, product: term_life}` → rejected (age < 18)
- [ ] `POST compliance_check` with `{smoker: true, product: ulip_growth}` → rejected (smoker exclusion)
- [ ] Elastic search: query "comprehensive illness protection" → Critical Illness Rider in top-3 (ELSER semantic match)
- [ ] Full voice loop: speak → STT → Agent Builder → sub-agents → TTS → voice response under 10 seconds
- [ ] Multi-turn: "tell me more about option 2" → context maintained, correct product returned
- [ ] Elastic MCP connectivity: `search` tool returns ELSER token weights in response

---

## Team Split

| Person | Area | Days |
|---|---|---|
| Engineer 1 | Agent Builder multi-agent setup (Root + Sub-Agents) + Cloud Functions | 2–10 |
| Engineer 2 | Dialogflow CX + voice integration (STT/TTS) + end-to-end testing | 5–12 |
| Engineer 3 | Synthetic data + Elasticsearch + ELSER pipeline + DevOps + demo support | 1–14 |

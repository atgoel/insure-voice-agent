# InsureVoice — Demo Video Script

**Hackathon**: Building Agents for Real-World Challenges — Elastic Partner Track
**Target runtime**: ~3 minutes
**Format**: Screen recording + voiceover

---

## Pre-Demo Checklist

Before recording, verify all services are live:

```bash
# Health check
curl https://insure-voice-agent-1055350728739.us-central1.run.app/health
# Expected: {"status":"ok","agent":"insure-voice","project":"voice-sales-agent","location":"us-central1"}

# MCP server
curl https://elastic-mcp-server-native-1055350728739.us-central1.run.app/health
```

Open two browser tabs:
1. **Google Cloud Logging** — filter: `resource.type="cloud_run_revision" logName:"insure-voice-audit"`
2. **Elastic Cloud Discover** — index `insurance_products_current`, fields: `name`, `product_type`, `_score`

---

## Scene 1 — Problem Setup (0:00–0:20)

**Voiceover**:
> "Insurance advisors spend 60 to 90 minutes per customer manually matching products to profiles. InsureVoice reduces that to under 8 seconds using AI-powered semantic search, deterministic compliance guardrails, and Google Cloud Agent Builder."

**Screen**: Show the architecture diagram from `docs/ARCHITECTURE.md` or `README.md`.

---

## Scene 2 — Happy Path: Complete Profile → Recommendation (0:20–1:30)

**Voiceover**:
> "Let's start with a typical customer. Priya is 35, non-smoker, earns ₹12 lakh a year, and needs term life cover for her family of four."

**Action**: Open a terminal or Postman. Run:

```bash
curl -X POST https://insure-voice-agent-1055350728739.us-central1.run.app/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "message": "I am 35 years old, non-smoker, annual income 1.2 million INR. I need term life cover for my family of four."
  }'
```

**Screen**: Show the response JSON. Highlight:
- `session_id` (for multi-turn)
- `response` text — ≤120 words, mentions product names and INR amounts

**Voiceover** (while response appears):
> "The agent extracts the profile, runs ELSER semantic search via the Elastic MCP server, validates eligibility with deterministic rules, scores and ranks the top 3 products — all in under 8 seconds."

**Action**: Switch to Google Cloud Logging tab. Show the structured audit log entry:

```json
{
  "session_id": "...",
  "candidate_products": [ ... ],
  "compliance_outcomes": { "passed_count": 4, "rejected": [] },
  "final_rankings": [ { "rank": 1, "product_id": "TERM001", "suitability_score": 0.91 }, ... ]
}
```

**Voiceover**:
> "Every recommendation is logged to Cloud Logging with the ELSER scores, compliance outcomes, and final rankings — a complete audit trail."

---

## Scene 3 — Compliance Guardrail: All-Rejected Path (1:30–2:10)

**Voiceover**:
> "Now let's see the compliance guardrail in action. Ravi is 72, a smoker, with a modest income. Most products won't be eligible."

**Action**: Run:

```bash
curl -X POST https://insure-voice-agent-1055350728739.us-central1.run.app/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "message": "I am 72 years old, smoker, income 300000 INR. I need any insurance cover."
  }'
```

**Screen**: Show the response. It should explain the constraints, not recommend a product.

**Voiceover**:
> "The compliance guardrail is deterministic Python logic — no LLM is involved in eligibility decisions. Rejected products never reach the customer response. The agent explains the constraints and asks Ravi to adjust his profile."

**Action**: Show the Cloud Logging audit entry for this call. Point out `compliance_outcomes.rejected` list with `reasons` for each product.

---

## Scene 4 — Elastic MCP Integration (2:10–2:40)

**Voiceover**:
> "The search superpower is Elastic ELSER — sparse vector semantic search via the Elastic MCP server. Here's how it works under the hood."

**Action**: Switch to the Cloud Run logs for `elastic-mcp-server-native`. Show the MCP JSON-RPC trace:
- `initialize` handshake
- `tools/list` response (showing `search_products` tool)
- `tools/call` with customer profile fields

**Voiceover**:
> "The ADK agent uses MCPToolset to connect to our FastMCP server over Streamable HTTP. The MCP server runs an ELSER v2 RRF hybrid query — combining sparse vectors and BM25 — on the Elastic Cloud index. This is genuine MCP protocol integration, not a REST wrapper."

---

## Scene 5 — Multi-Turn Conversation (2:40–3:00)

**Voiceover**:
> "Finally, multi-turn context. Using the session ID from the first response, Priya asks a follow-up question."

**Action**: Run (replace `SESSION_ID` with actual value from Scene 2):

```bash
curl -X POST https://insure-voice-agent-1055350728739.us-central1.run.app/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Tell me more about the first one.",
    "session_id": "SESSION_ID"
  }'
```

**Screen**: Show that the agent answers about rank-1 product without re-running the full pipeline.

**Voiceover**:
> "The agent maintains session context. Follow-up questions are answered instantly from the previous recommendation — no new search or compliance check required."

---

## Scene 6 — Wrap Up (3:00–3:05)

**Voiceover**:
> "InsureVoice: 60-minute insurance consultations reduced to 8 seconds. Built with Google Cloud Agent Builder, Elastic ELSER, and deterministic compliance guardrails. Apache 2.0 — fully open source."

**Screen**: Show the GitHub repository URL and the InsureVoice architecture diagram side-by-side.

---

## Key Metrics to Highlight During Recording

| Metric | Target | Actual (measure live) |
|---|---|---|
| End-to-end latency | < 8s | Run TASK-081 smoke test |
| Response word count | ≤ 120 words | Count from response JSON |
| Products in catalog | 28 | `GET /health` or Elastic Discover |
| Compliance guardrail | Deterministic, 0 LLM calls | Show `compliance_check/main.py` |

---

## Backup Commands (if live demo fails)

Record these as separate take-overs:

```bash
# Cached happy-path response (pre-recorded)
cat docs/demo-cache/happy-path-response.json

# Show unit test run (offline proof)
pytest tests/test_compliance_check.py tests/test_rank_products.py -v --tb=short
```

# Feature Specification: Multi-Agent Orchestration

**Feature Directory**: `specs/005-multi-agent-orchestration/`
**Created**: 2026-05-26
**Updated**: 2026-05-29 (production deployment — single LlmAgent with 3 tools)
**Status**: Implemented ✅

## Overview

The orchestration layer is a **single `LlmAgent`** (Google ADK 2.1.0, Gemini 2.5 Flash Lite on Vertex AI `us-central1`) with three tools registered directly — NOT a multi-agent hierarchy. This was chosen for simplicity, lower latency, and better reliability in the hackathon timeframe.

**Architecture decision**: "Sub-Agent" language from the original spec was aspirational. The actual ADK implementation uses one `LlmAgent` that calls tools sequentially via Gemini function-calling.

```python
# agent_builder/agent_definition.py
agent = LlmAgent(
    model="gemini-2.5-flash-lite",
    name="insure_voice_agent",
    tools=[
        MCPToolset(connection_params=StreamableHTTPConnectionParams(
            url=f"{ELASTIC_MCP_SERVER_NATIVE_URL}/mcp"
        )),               # Tool 1: search_products (auto-discovered via MCP)
        FunctionTool(compliance_check),   # Tool 2
        FunctionTool(rank_products),      # Tool 3
    ]
)
```

---

## Architecture Decisions (2026-05-29)

### Decision: Single LlmAgent vs Multi-Agent Hierarchy

**Context**: The spec originally described a Root Agent + 3 Sub-Agents using Agent Builder's hierarchical agent feature. In practice, Google ADK 2.1.0's `LlmAgent` with tools is more stable, faster to deploy, and achieves the same orchestration outcome via Gemini function-calling.

**Decision**: Use a single `LlmAgent` with three tools. The Root Agent system prompt drives the search → comply → rank sequence.

**Trade-offs**:
- ✅ Lower latency (no inter-agent message passing)
- ✅ Single deployment unit, simpler debugging
- ✅ MCPToolset integration works cleanly
- ❌ Less modular than true sub-agents (acceptable for hackathon scope)

### Decision: MCPToolset for search_products (Tool 1)

`MCPToolset(StreamableHTTPConnectionParams(url=f"{ELASTIC_MCP_SERVER_NATIVE_URL}/mcp"))` is used instead of `FunctionTool(search_products)`.

**Why**: MCPToolset uses the real MCP JSON-RPC protocol (`initialize` → `tools/list` → `tools/call`) over Streamable HTTP, demonstrating genuine Elastic MCP integration — a hackathon requirement.

**Critical rule**: Do NOT also register `FunctionTool(search_products)`. Gemini returns `400 INVALID_ARGUMENT: Duplicate function declaration found: search_products` when a tool name appears twice in the tool list.

### Decision: FunctionTool for compliance_check and rank_products (Tools 2–3)

These call Cloud Functions directly. Field names are Pydantic-validated — the agent wrapper must use exact field names:
- `compliance_check`: `{"candidate_products": [...], "customer_profile": {...}}`
- `rank_products`: `{"passed_products": [...], "customer_profile": {...}}`

Passing `"candidates"` or `"eligible_candidates"` causes a `400 Bad Request`.

### Decision: compliance_check is DETERMINISTIC (no LLM)

Per Constitution §II, the compliance guardrail uses pure Python predicate rules only. This is implemented in `functions/compliance_check/main.py` with zero LLM involvement.

---

## User Stories & Acceptance Criteria

### Story 1 — Sequential Tool Orchestration (Priority: P1)

Given a complete customer profile, the LlmAgent calls tools in strict order: `search_products` → `compliance_check` → `rank_products`.

**Acceptance Scenarios**:

1. **Given** a complete customer profile message, **When** the agent is invoked, **Then** `search_products` is called first via MCPToolset with the profile fields.
2. **Given** `search_products` returns ≥ 1 candidate, **When** the agent continues, **Then** `compliance_check` is called with `candidate_products` = all returned products.
3. **Given** `compliance_check` returns ≥ 1 passed product, **When** the agent continues, **Then** `rank_products` is called with `passed_products` = only the passed products.
4. **Given** all 3 tools complete, **When** the agent generates a response, **Then** it produces a voice-ready recommendation ≤ 120 words.
5. **Given** all tools complete within budget, **When** end-to-end time is measured, **Then** total < 8s.

---

### Story 2 — Compliance Guardrail Enforcement (Priority: P1)

The Root Agent never passes a rejected product to `rank_products` or to the customer response.

**Acceptance Scenarios**:

1. **Given** `compliance_check` rejects 3 out of 5 candidates, **When** `rank_products` is called, **Then** it receives only the 2 passed products.
2. **Given** `compliance_check` rejects all candidates, **When** the agent generates a response, **Then** it explains which constraints blocked all recommendations and asks if the customer wants to adjust their profile.

---

### Story 3 — Error Handling & Graceful Degradation (Priority: P2)

If any tool call fails, the agent handles it gracefully.

**Acceptance Scenarios**:

1. **Given** `search_products` returns an error, **When** detected, **Then** the agent responds with a helpful message and suggests retrying.
2. **Given** `compliance_check` returns HTTP 500, **When** detected, **Then** the agent does not proceed to ranking.
3. **Given** `rank_products` times out, **When** detected, **Then** the agent falls back to presenting compliance-passed products in search-score order.

---

### Story 4 — Multi-Turn Conversation State (Priority: P2)

After delivering recommendations, the agent maintains context for follow-up questions.

**Acceptance Scenarios**:

1. **Given** recommendations have been delivered, **When** the customer asks "tell me more about the second one", **Then** the agent provides detail on rank-2 product without re-running the pipeline.
2. **Given** the customer says "let me try with a different budget", **When** processed, **Then** the agent clears previous recommendation state and initiates a new search.

---

## Orchestration Sequence Diagram (actual implementation)

```
POST /invoke {"message": "I'm 35, non-smoker, ₹8L income, need term life for family"}
        │
        ▼
[LlmAgent — Gemini 2.5 Flash Lite, Vertex AI us-central1]
  Extracts customer profile from message
        │
        ▼
[MCPToolset → elastic-mcp-server-native /mcp]
  MCP initialize → tools/list → tools/call search_products(
      query="term life family",
      customer_age=35, is_smoker=False, income=800000
  )
  ELSER v2 RRF hybrid query → insurance_products_current
  Returns: candidate_products[] (up to 10)
        │
        ▼
[FunctionTool(compliance_check)]
  POST https://us-central1-voice-sales-agent.cloudfunctions.net/compliance_check
  {
    "candidate_products": [...],
    "customer_profile": {age:35, income:800000, smoker:false, health_status:"healthy", coverage_goals:["life"]}
  }
  Returns: {passed:[], rejected:[]}
        │ passed empty?
        ├─ Yes → explain constraints, ask to adjust profile
        ▼ No
[FunctionTool(rank_products)]
  POST https://us-central1-voice-sales-agent.cloudfunctions.net/rank_products
  {
    "passed_products": [...],
    "customer_profile": {...}
  }
  Returns: {top_products: [{rank, product_name, suitability_score, ...}, ...]}
        │
        ▼
[LlmAgent — compose voice response]
  ≤ 120 words, INR, top-3 ranked products
        │
        ▼
Response: {"session_id": "...", "response": "Based on your profile, here are my top 3..."}
```

---

## Deployed Services (2026-05-29)

| Service | URL | Purpose |
|---|---|---|
| `insure-voice-agent` | `https://insure-voice-agent-1055350728739.us-central1.run.app` | LlmAgent entry point |
| `elastic-mcp-server-native` | `https://elastic-mcp-server-native-1055350728739.us-central1.run.app` | MCPToolset target |
| `elastic-mcp-server` | `https://elastic-mcp-server-mhojvvbq4a-uc.a.run.app` | REST fallback (legacy) |
| `compliance_check` | `https://us-central1-voice-sales-agent.cloudfunctions.net/compliance_check` | Cloud Function |
| `rank_products` | `https://us-central1-voice-sales-agent.cloudfunctions.net/rank_products` | Cloud Function |

---

## User Stories & Acceptance Criteria

### Story 1 — Sequential Tool Orchestration (Priority: P1)

Given a complete customer profile, the LlmAgent calls tools in strict order: `search_products` → `compliance_check` → `rank_products`.

**Acceptance Scenarios**:

1. **Given** a complete customer profile message, **When** the agent is invoked, **Then** `search_products` is called first via MCPToolset.
2. **Given** `search_products` returns ≥ 1 candidate, **When** the agent continues, **Then** `compliance_check` is called with `candidate_products` = all returned products.
3. **Given** `compliance_check` returns ≥ 1 passed product, **When** the agent continues, **Then** `rank_products` is called with `passed_products` = only the passed products.
4. **Given** all 3 tools complete, **When** the agent generates a response, **Then** it produces a voice-ready recommendation ≤ 120 words.
5. **Given** all tools complete within budget, **When** end-to-end time is measured, **Then** total < 8s.

---

### Story 2 — Compliance Guardrail Enforcement (Priority: P1)

The agent never passes a rejected product to `rank_products` or to the customer response.

**Acceptance Scenarios**:

1. **Given** `compliance_check` rejects 3 out of 5 candidates, **When** `rank_products` is called, **Then** it receives only the 2 passed products.
2. **Given** the Root Agent's response mentions a rejected product, **When** reviewed, **Then** it clearly states the product was considered but is not eligible, with the rejection reason.
3. **Given** `compliance_check` rejects all candidates, **When** the agent generates a response, **Then** it explains which constraints blocked all recommendations and asks if the customer wants to adjust their profile.

---

### Story 3 — Error Handling & Graceful Degradation (Priority: P2)

If any tool call fails, the agent handles it gracefully without crashing the session.

**Acceptance Scenarios**:

1. **Given** `search_products` returns an error, **When** detected, **Then** the agent responds with a helpful message and suggests retrying.
2. **Given** `compliance_check` returns HTTP 500, **When** detected, **Then** the agent does not proceed to ranking.
3. **Given** `rank_products` times out, **When** detected, **Then** the agent falls back to presenting compliance-passed products in search-score order.

---

### Story 4 — Multi-Turn Conversation State (Priority: P2)

After delivering recommendations, the agent maintains context for follow-up questions.

**Acceptance Scenarios**:

1. **Given** recommendations have been delivered, **When** the customer says "tell me more about the second one", **Then** the agent provides detail on rank-2 product without re-running the pipeline.
2. **Given** the customer says "let me try with a different budget", **When** processed, **Then** the agent clears previous recommendation state and initiates a new search.
3. **Given** the customer asks something outside insurance, **When** processed, **Then** the agent politely redirects to insurance topics.

---

## Edge Cases

- All products rejected → handled per Story 2 above.
- Total latency exceeds 8s → log a warning; no user-facing impact (response still delivered).
- Session timeout mid-conversation → new session starts fresh.

---

## Out of Scope

- Phase 2: Trigger downstream application workflow (Pub/Sub → Camunda).
- Parallel tool execution (all calls are sequential).
- Multi-customer session handling.

---

## Technical Notes

- Root Agent: `agent_builder/root_agent_prompt.md` — system prompt drives all orchestration logic.
- Tool definitions (legacy REST demo): `agent_builder/tools.yaml`.
- ADK `LlmAgent` is configured in `agent_builder/agent_definition.py`.
- Service URLs injected via environment variables (`ELASTIC_MCP_SERVER_NATIVE_URL`, `COMPLIANCE_CHECK_URL`, `RANK_PRODUCTS_URL`).
- Cloud Logging captures all tool call inputs/outputs for the audit trail.

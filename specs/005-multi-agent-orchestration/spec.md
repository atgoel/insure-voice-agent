# Feature Specification: Multi-Agent Orchestration

**Feature Directory**: `specs/005-multi-agent-orchestration/`
**Created**: 2026-05-26
**Status**: Draft

## Overview

The Root Agent (Insurance Sales Supervisor) orchestrates 3 Sub-Agents in a fixed sequence using Google Cloud Agent Builder (ADK). This spec covers: the Root Agent prompt design, the sub-agent tool definitions in `agent_builder/tools.yaml`, the sequential call pattern, error propagation, multi-turn state management, and the complete end-to-end data flow from customer voice transcript to TTS-ready response. This is the integration layer that binds all other specs together.

---

## User Stories & Acceptance Criteria

### Story 1 — Sequential Sub-Agent Orchestration (Priority: P1)

Given a complete `CustomerProfile`, the Root Agent calls Sub-Agents 1, 2, and 3 in strict order: Search → Compliance → Rank. Results from each step are passed as inputs to the next.

**Why P1**: This is the system's core orchestration loop — the hackathon's core demo is this sequence working end-to-end.

**Independent Test**: Provide a complete customer profile to the Root Agent and verify via structured logging that all three sub-agents are called in sequence within 5 seconds.

**Acceptance Scenarios**:

1. **Given** a complete `CustomerProfile`, **When** the Root Agent begins orchestration, **Then** Sub-Agent 1 (Search) is called first, with `coverage_goals` as semantic query and profile fields as filters.
2. **Given** Sub-Agent 1 returns ≥ 1 candidate product, **When** the Root Agent continues, **Then** Sub-Agent 2 (Compliance) is called with all candidates and the full profile.
3. **Given** Sub-Agent 2 returns ≥ 1 passed product, **When** the Root Agent continues, **Then** Sub-Agent 3 (Ranking) is called with only the passed products.
4. **Given** Sub-Agent 3 returns a `top3` list, **When** the Root Agent generates a response, **Then** it produces a voice-ready recommendation string ≤ 120 words.
5. **Given** all 3 sub-agents complete within their budgets, **When** the total elapsed time is measured, **Then** it is < 5s (agent layer budget).

---

### Story 2 — Compliance Guardrail Enforcement in Orchestration (Priority: P1)

The Root Agent never passes a rejected product to Sub-Agent 3 or to the customer response. Rejected products may be mentioned with their rejection reason, but never recommended.

**Why P1**: Constitution principle I — Compliance-First. This must be demonstrated in the hackathon demo.

**Acceptance Scenarios**:

1. **Given** Sub-Agent 2 rejects 3 out of 5 candidates, **When** Sub-Agent 3 is called, **Then** it receives only the 2 passed products — not all 5.
2. **Given** the Root Agent's response mentions a rejected product, **When** reviewed, **Then** it clearly states the product was considered but is not eligible, with the rejection reason.
3. **Given** Sub-Agent 2 rejects all candidates, **When** the Root Agent generates a response, **Then** it explains which constraints blocked all recommendations and asks if the customer wants to adjust their profile.

---

### Story 3 — Error Handling & Graceful Degradation (Priority: P2)

If any sub-agent call fails (timeout, error response), the Root Agent handles it gracefully without crashing the session. The customer receives a helpful message.

**Acceptance Scenarios**:

1. **Given** the Elastic MCP search tool returns an error, **When** the Root Agent detects it, **Then** it responds "I'm having trouble searching our product catalog right now. Let me try again." and retries once.
2. **Given** the `compliance_check` function returns HTTP 500, **When** detected, **Then** the Root Agent does not proceed to ranking and responds "Our compliance system is temporarily unavailable. Please try again in a moment."
3. **Given** `rank_products` times out, **When** detected, **Then** the Root Agent falls back to returning the compliance-passed products in search-score order (best effort, no scoring).

---

### Story 4 — Multi-Turn Conversation State (Priority: P2)

After delivering recommendations, the Root Agent maintains context for follow-up questions. The customer can ask for more detail on a specific recommendation or restart with a new profile.

**Acceptance Scenarios**:

1. **Given** recommendations have been delivered, **When** the customer says "tell me more about the second one", **Then** the Root Agent provides additional detail on rank-2 product without re-running the pipeline.
2. **Given** the customer says "let me try with a different budget", **When** processed, **Then** the Root Agent clears the previous recommendation state and initiates a new profile extraction.
3. **Given** the customer asks about something outside insurance (e.g., "what's the weather today"), **When** processed, **Then** the Root Agent politely redirects to insurance topics without crashing.

---

## Orchestration Sequence Diagram

```
Customer Voice Transcript
        │
        ▼
[Root Agent: Extract CustomerProfile]
        │ profile complete?
        ├─ No → ask clarifying question → wait for next turn
        ▼ Yes
[Sub-Agent 1: Product Search]
  → Elastic MCP search tool
  → Returns: candidate_products[]
        │
        ▼
[Sub-Agent 2: Compliance Guard]
  → POST /compliance_check
  → Returns: passed[], rejected[]
        │ passed empty?
        ├─ Yes → Root Agent: explain constraints to customer
        ▼ No
[Sub-Agent 3: Recommendation Explainer]
  → POST /rank_products
  → Returns: top3[]
        │
        ▼
[Root Agent: Generate voice response]
  → ≤ 120 words, INR, personalised
        │
        ▼
Cloud TTS WaveNet → Voice Response
```

---

## Agent Builder Tool Definitions (tools.yaml schema)

```yaml
tools:
  - name: elastic_product_search
    type: mcp
    mcp_server: elastic-mcp
    description: "Search insurance products using ELSER semantic hybrid search"
    parameters:
      - name: query
        type: string
        description: "Customer coverage goals in natural language"
      - name: customer_age
        type: integer
      - name: smoker
        type: boolean

  - name: compliance_check
    type: http_function
    url: "${COMPLIANCE_CHECK_URL}"
    method: POST
    description: "Deterministic eligibility rule engine for insurance products"

  - name: rank_products
    type: http_function
    url: "${RANK_PRODUCTS_URL}"
    method: POST
    description: "Score and rank compliance-passed products by suitability"
```

---

## Edge Cases

- Profile extraction fails after 2 clarifying questions → Root Agent apologises and ends the session.
- ELSER returns 0 results after retry → Root Agent informs customer and exits gracefully.
- All products rejected → handled per Story 2 above.
- Total latency exceeds 8s → log a warning; no user-facing impact (response still delivered).
- Session timeout mid-conversation → Dialogflow CX handles; new session starts fresh.

---

## Out of Scope

- Phase 2: Trigger downstream application workflow (Pub/Sub → Camunda).
- A/B testing of different orchestration sequences.
- Parallel sub-agent execution (all calls are sequential in Phase 1).
- Multi-customer session handling.

---

## Technical Notes

- Root Agent: `agent_builder/root_agent_prompt.md` — the system prompt drives all orchestration logic.
- Tool definitions: `agent_builder/tools.yaml`.
- ADK multi-agent configuration is managed in the Google Cloud Console / Agent Builder UI.
- Sub-agent Cloud Function URLs are injected via environment variables (`COMPLIANCE_CHECK_URL`, `RANK_PRODUCTS_URL`).
- Cloud Logging captures all sub-agent call inputs/outputs for the audit trail.
- Dialogflow CX manages the voice session lifecycle; Agent Builder handles the reasoning loop.

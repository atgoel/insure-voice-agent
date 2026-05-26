# Infinity — Agentic AI Year 2–3 Roadmap
## Intelligent Sales Journey, Automated Underwriting, Conversational Proposal

**Prerequisite:** Phases 1–6 of `INFINITY-MODERN-LOW-LEVEL-ARCHITECTURE.md` must be complete.  
Event-driven architecture, BFF layer, Rule Engine, Camunda orchestration, and Central Master Data Platform are the foundation that makes this plan possible.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Why Now? The Foundation Dependency](#2-the-foundation-dependency)
3. [Phase A — Smart Form Filling (Year 2, Q1–Q2)](#3-phase-a-smart-form-filling)
4. [Phase B — AI Underwriting Assistance (Year 2, Q3–Q4)](#4-phase-b-ai-underwriting-assistance)
5. [Phase C — Conversational AI & Document Intelligence (Year 3)](#5-phase-c-conversational-ai)
6. [AI Architecture: Services & Integration Points](#6-ai-architecture)
7. [Camunda + AI: Orchestration Patterns](#7-camunda-ai-orchestration)
8. [ROI & Business Case](#8-roi-and-business-case)
9. [Data Privacy, IRDAI Compliance & Audit Trail](#9-compliance-and-audit)
10. [Technology Stack Summary](#10-technology-stack)
11. [Year-by-Year Roadmap Timeline](#11-roadmap-timeline)

---

## 1. Executive Summary

The Phases 1–6 modern architecture transforms Infinity from a monolithic, queue-based system to an event-driven, API-first platform. It reduces the end-to-end sales journey from ~90 minutes to ~20 minutes.

The Year 2–3 Agentic AI layer adds intelligence on top of that foundation. AI agents are orchestrated by Camunda — they are **Camunda service task workers** that call Azure AI services and return results back into the workflow. Human underwriters remain in the loop for all edge cases via Camunda Tasklist human tasks.

**Year 2–3 targets:**

| Metric | Current | Year 2 Target | Year 3 Target |
|--------|---------|--------------|--------------|
| Agent form filling time | 16–23 min | 5–8 min | 2–4 min |
| Form completion rate | ~40% | ~75% | ~85–90% |
| Internal processing time | 45–60 min | 15–20 min | < 1 min (90% cases) |
| Agent cost per application | ₹800–1,200 (time cost) | ₹350–500 | ₹100–200 |
| Accuracy of data capture | ~85% (manual entry errors) | ~97% (AI-assisted) | ~99.5% (AI + OCR) |
| Cases auto-approved without UW review | ~30% | ~60% | ~85–90% |

**Core principle: AI assists, Camunda orchestrates, humans validate edge cases.  
IRDAI compliance requires a full audit trail — every AI decision is logged.**

---

## 2. The Foundation Dependency

Agentic AI cannot be bolted onto the legacy architecture. The following Phase 1–6 outputs are prerequisites:

| Foundation Element | Why AI Needs It |
|-------------------|-----------------|
| **Event-driven architecture (Phase 2–5)** | AI workers publish events; downstream processing continues asynchronously without awaiting AI completion |
| **Camunda orchestration (Phase 4)** | AI service tasks are Camunda job workers; BPMN boundary error events handle AI failures gracefully; human task fallback is a first-class BPMN construct |
| **Rule Engine Service (Phase 2–3)** | AI uses the Rule Engine to validate its own suggestions (e.g., "Would this client be eligible for Product X?"); keeps AI in sync with current business rules |
| **Central Master Data Platform (Phase 3–5)** | AI pre-fill uses product definitions, pincode data, and form structure from the platform service |
| **BFF Layer (Phase 3–4)** | The smart form init BFF call (`/bff/form-init`) becomes the trigger for AI pre-fill; BFF aggregates AI pre-fill results with form structure in a single response |
| **Azure Key Vault (Phase 0)** | Azure OpenAI API keys, Document Intelligence keys, and Computer Vision keys are stored securely in Key Vault — never in code |
| **Database-per-service (Phase 3)** | AI audit logs stored in a dedicated `AI_Audit_DB` without touching proposal or processing databases |

---

## 3. Phase A — Smart Form Filling
**(Year 2, Q1–Q2 — approximately Week 53–78)**

### 3.1 Objective

Reduce agent form-filling time from 16–23 minutes to 5–8 minutes for standard cases.  
Approach: AI pre-populates 60–70% of form fields from existing data sources before the agent opens the form.

### 3.2 CKYC Pre-Fill via Azure AI

**What:** Central KYC (CKYC) records contain name, DOB, address, PAN, and Aadhaar hash for all insured persons. Azure OpenAI's function-calling capability queries the CKYC API and maps the response to the Infinity proposal form field schema.

**How it integrates:**  
1. Agent selects a product and enters the client's PAN or Aadhaar (2 fields)
2. BFF `/bff/form-init` call includes a CKYC pre-fill flag
3. Agent Web BFF calls `FGLI-MS-ProposalForm` (for any existing draft) AND a new `FGLI-AI-PreFillService` in parallel
4. `FGLI-AI-PreFillService` calls CKYC API → maps to form schema using field-mapping rules from Central Master Data → returns pre-filled field values
5. BFF merges pre-fill data with form structure → single response to MFE
6. Fields with high-confidence pre-fill are shown as read-only with a "Confirm" button; low-confidence fields are shown pre-filled but editable

**Fields typically pre-filled from CKYC:** First name, last name, DOB, gender, address line 1–3, pincode, state, city, PAN number, Aadhaar hash (for verification, not display).

**Saved time:** 7–8 minutes of manual data entry per proposal.

### 3.3 Conditional Questions via Rule Engine

**What:** Currently the proposal form shows all 108 fields regardless of the product. Most fields are not applicable. With the Rule Engine, the form dynamically determines which 30–40 fields are relevant for a given product + customer combination.

**How it integrates:**
1. BFF `/bff/form-init` passes `{ productCode, customerAge, sumAssured, occupation }` to Rule Engine (`ReflexQuestionTriggers` rule set)
2. Rule Engine returns the list of active field groups for this case
3. BFF returns a trimmed form definition — agent sees only the fields relevant to this case
4. Agents never see irrelevant fields; hidden fields are pre-filled with default values from the product definition

**Fields typically eliminated:** Medical questions for low-risk, low-sum-assured cases; nominee relationship fields if nominee rule doesn't require guardian for this age band; bank details section if renewal premium waiver is not selected.

**Saved time:** 6–8 minutes by eliminating irrelevant navigation and scroll. Completion rate impact: +25 percentage points.

### 3.4 AI-Powered Real-Time Validation

**What:** As the agent fills the form, fields are validated in real time — not at submit time. Mis-spellings, cross-field inconsistencies (e.g., DOB vs age entered), and format errors are caught immediately with intelligent suggestions.

**Technology:** Azure OpenAI GPT-4 function calling for semantic validation (e.g., "This occupation has elevated medical risk for this sum assured — consider flagging for UW"). Standard field validation remains in the Rule Engine.

**Camunda Integration:** Not required in Phase A — validation is synchronous within the BFF/form layer, not a workflow step.

### 3.5 Document OCR Pre-Fill via Azure Document Intelligence

**What:** Agent uploads an Aadhaar card, PAN card, or salary slip image. Azure Document Intelligence extracts structured data (name, DOB, address, income) and pre-fills the corresponding form fields.

**How it integrates:**
1. Agent uploads document → `FGLI-MS-DocumentService` stores in blob, publishes `DocumentUploaded` event
2. `FGLI-AI-PreFillService` subscribes to `DocumentUploaded`, calls Azure Document Intelligence, extracts fields
3. Extracted fields are POSTed back to the form via a `DocumentPreFillCompleted` event
4. MFE shows a non-blocking toast: "We filled 8 fields from your Aadhaar upload. Please review."

**Fields typically extracted from Aadhaar:** Name, DOB, gender, full address, pincode.  
**Fields typically extracted from PAN:** PAN number, name (cross-check with Aadhaar).  
**Fields typically extracted from salary slip:** Monthly income, employer name.

**Saved time:** 3–5 minutes for the standard case where 2–3 documents are uploaded.

### 3.6 New Services in Phase A

| Service | Technology | Purpose |
|---------|-----------|---------|
| `FGLI-AI-PreFillService` | .NET 8, Azure OpenAI SDK, Azure Document Intelligence SDK | CKYC pre-fill, field mapping, OCR extraction |
| `FGLI-AI-ValidationService` | .NET 8, Azure OpenAI function calling | Semantic field validation, cross-field checks |

Both services are **event-driven** (subscribe to `DocumentUploaded`, `ProposalDraftCreated`) and also callable synchronously from the BFF layer.

---

## 4. Phase B — AI Underwriting Assistance
**(Year 2, Q3–Q4 — approximately Week 79–104)**

### 4.1 Objective

Change: 85–90% of standard cases are auto-processed by AI. Human underwriters focus only on genuinely complex cases. Cases that currently take 45–60 minutes to process take < 1 minute.

### 4.2 AI Risk Scoring Worker

**What:** A new Camunda service task worker that calls Azure OpenAI GPT-4 with a structured risk assessment prompt. The model evaluates: applicant age, occupation, declared medical history, sum assured, and reflex question answers. It returns a risk score (0–100), a recommendation (Auto-Approve / Standard UW / Senior UW / Decline), and a reasoning paragraph.

**How it integrates into the Camunda BPMN:**

```
ApplicationSubmitted
    │
    ▼
[Service Task: DeDupeWorker]
    │
    ▼
[Service Task: ImageQCWorker]
    │
    ▼
[Service Task: PrintQCWorker]
    │
    ▼
[Service Task: AIRiskScoringWorker]  ← NEW in Phase B
    │
    ├── Score ≥ 90 (Standard Risk) ──────────────────────────────► [Auto-Approve]
    │
    ├── Score 70–89 (Moderate Risk) ──► [Service Task: StandardUWReviewTask]
    │                                        (Camunda Human Task — UW queue)
    │
    ├── Score < 70 (Complex Risk) ──────► [Service Task: SeniorUWReviewTask]
    │                                        (Camunda Human Task — Senior UW queue)
    │
    └── AI Error / Timeout ────────────► [Boundary Error Event]
                                              → Manual UW Review fallback
```

**Confidence threshold design:**
- Score ≥ 90: Auto-approve directly (no human review)
- Score 70–89: AI recommendation shown to standard UW reviewer in Camunda Tasklist; UW accepts or overrides with one click
- Score < 70: Routed to senior UW with full AI reasoning summary as context
- AI timeout (> 30s) or error: Boundary error event fires → routes to manual UW review (same fallback as current system)

**IRDAI compliance:** Every AI score, recommendation, reasoning paragraph, and the final human decision (accept/override) is written to `AI_Audit_DB` with a tamper-proof timestamp. Cases cannot be issued without this audit record.

### 4.3 AI Underwriting Worker Implementation

```csharp
public class AIRiskScoringWorker : IJobHandler
{
    private readonly OpenAIClient _openAI;
    private readonly IAuditRepository _audit;

    public async Task HandleAsync(IJobClient client, IJob job)
    {
        var variables = job.Variables;
        var prompt = BuildRiskPrompt(variables);

        var response = await _openAI.GetChatCompletionsAsync(
            "gpt-4",
            new ChatCompletionsOptions
            {
                Messages =
                {
                    new ChatMessage(ChatRole.System, UNDERWRITING_SYSTEM_PROMPT),
                    new ChatMessage(ChatRole.User, prompt)
                },
                Functions = { RISK_SCORING_FUNCTION_DEFINITION }
            });

        var result = ParseRiskResponse(response);

        // Audit every AI decision regardless of outcome
        await _audit.LogAsync(new AIDecisionAuditRecord
        {
            ApplicationNo = variables.AppNo,
            Model = "gpt-4",
            Prompt = prompt,
            RawResponse = response.Choices[0].Message.Content,
            Score = result.Score,
            Recommendation = result.Recommendation,
            Reasoning = result.Reasoning,
            ProcessedAt = DateTimeOffset.UtcNow
        });

        await client.NewCompleteJobCommand(job.Key)
            .Variables(new
            {
                aiRiskScore = result.Score,
                aiRecommendation = result.Recommendation,
                aiReasoning = result.Reasoning,
                aiProcessedAt = DateTimeOffset.UtcNow
            })
            .SendAsync();
    }
}
```

### 4.4 Parallel AI Processing via Camunda

For efficiency, multiple AI evaluations run in parallel using Camunda's parallel gateway:

```
[PrintQCComplete]
    │
    ▼
[Parallel Gateway] ──────────────────┬─────────────────────────────┐
    │                                 │                             │
[AIRiskScoringWorker]     [AIMedicalHistoryWorker]      [AIDocumentVerifierWorker]
(Risk classification)     (Medical declarations         (Document authenticity +
                           cross-referenced with         completeness check)
                           declared conditions)
    │                                 │                             │
    └─────────────────────────────────┴─────────────────────────────┘
                                      │
                              [Join Gateway]
                                      │
                              [Decision Gateway]
                             /        |          \
                     Auto-Approve  Send to UW   Decline
```

Running three AI workers in parallel shrinks total AI processing from ~90 seconds sequential to ~30 seconds (dominated by the slowest call).

### 4.5 Human-in-the-Loop: Camunda Tasklist for UW

When the AI routes to human review, the case appears in **Camunda Tasklist** for underwriters. The Tasklist shows:
- AI risk score and confidence
- AI reasoning paragraph (plain language, not raw JSON)
- All application details
- Flagged fields (what the AI identified as risk factors)
- Two action buttons: **Approve as recommended** / **Override** (requires a typed reason)

Overrides are stored in `AI_Audit_DB` with the underwriter's ID and reason. Override patterns are fed back quarterly to the AI model fine-tuning pipeline.

---

## 5. Phase C — Conversational AI & Document Intelligence
**(Year 3 — approximately Week 105–156)**

### 5.1 WhatsApp Chatbot for Proposal Form Filling

**What:** A customer receives a WhatsApp message with a proposal form link. Instead of opening a web form, the customer can reply to the WhatsApp bot. The bot asks questions conversationally, validates answers, and fills the Camunda-managed proposal on the backend.

**How it integrates:**
1. `FGLI-BFF-Customer` exposes a WhatsApp webhook endpoint
2. Azure Bot Service (or direct Twilio/WhatsApp Business API) routes messages to a new `FGLI-AI-ConversationalService`
3. `FGLI-AI-ConversationalService` maintains session state in Redis and calls Azure OpenAI with the conversation history
4. Each validated answer triggers a PATCH to `FGLI-MS-ProposalForm` service
5. When the full form is complete, `FGLI-AI-ConversationalService` signals the Camunda process

**Target:** Customer declaration form (CDF) completed in < 5 WhatsApp messages for standard cases.

### 5.2 Azure Computer Vision for Document Authenticity

**What:** When a document is uploaded (Aadhaar, PAN, Passport), Azure Computer Vision checks for signs of tampering — irregular fonts, modified fields, inconsistent metadata.

**How it integrates:**
- `FGLI-MS-DocumentService` publishes `DocumentUploaded` event
- `FGLI-AI-DocumentVerifierWorker` (Camunda service task) calls Azure Computer Vision
- Returns: `{ authentic: true, confidence: 0.97, flags: [] }` or `{ authentic: false, flags: ["PAN_NUMBER_EDITED"] }`
- Low-confidence documents are routed to a human document verification queue in Camunda Tasklist

### 5.3 Intelligent Agent Assistance (Co-Pilot Mode)

**What:** An AI co-pilot panel embedded in the Agent MFE shell. As the agent fills the form, the co-pilot:
- Suggests the most appropriate product based on client profile (age, income, dependents, risk appetite)
- Flags potential underwriting concerns before submission (e.g., "Sum assured > 10x annual income — will require financial proof")
- Predicts completion time and warns about fields that typically cause errors

**Technology:** Azure OpenAI GPT-4 with a custom system prompt trained on Infinity product catalogue and underwriting guidelines (updated from the Rule Engine and Central Master Data Platform).

### 5.4 Feedback Loop: Model Improvement

| Source | Data Collected | Purpose |
|--------|---------------|---------|
| UW override records in `AI_Audit_DB` | Cases where human UW overrode AI recommendation | Quarterly fine-tuning of risk scoring model |
| Document verification outcomes | Documents flagged by CV that were later accepted by human | Improve Computer Vision confidence thresholds |
| Form completion analytics | Which fields agents modify after AI pre-fill | Improve pre-fill mapping accuracy |
| Rule Engine updates | New/changed business rules | Keep AI system prompt current |

---

## 6. AI Architecture — Services & Integration Points

```
┌───────────────────────────────────────────────────────────────────────────────────────────────┐
│  AI LAYER (Year 2-3 additions, all Camunda job workers or event-driven)                      │
│                                                                                               │
│  ┌──────────────────────────┐  ┌──────────────────────────┐  ┌──────────────────────────┐   │
│  │  FGLI-AI-PreFillService  │  │ FGLI-AI-ValidationSvc    │  │ FGLI-AI-ConversationalSvc │   │
│  │  CKYC pre-fill           │  │ Semantic field validation │  │ WhatsApp chatbot (Year 3) │   │
│  │  DocIntelligence OCR     │  │ Azure OpenAI GPT-4        │  │ Azure Bot Service         │   │
│  └──────────────────────────┘  └──────────────────────────┘  └──────────────────────────┘   │
│                                                                                               │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  Camunda AI Workers (Job Workers running as AKS pods)                                │    │
│  │                                                                                       │    │
│  │  AIRiskScoringWorker     AIMedicalHistoryWorker     AIDocumentVerifierWorker          │    │
│  │  (Azure OpenAI GPT-4)    (Azure OpenAI GPT-4)       (Azure Computer Vision)           │    │
│  │                                                                                       │    │
│  │  All workers: read job variables → call Azure AI → write result variables + audit     │    │
│  └──────────────────────────────────────────────────────────────────────────────────────┘    │
│                                                                                               │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  AI_Audit_DB (Azure SQL)                                                              │    │
│  │  Tables: AIDecisionLog, AIOverrideLog, AIDocumentVerificationLog, ModelVersionLog     │    │
│  │  Retention: 10 years (IRDAI regulatory requirement)                                   │    │
│  └──────────────────────────────────────────────────────────────────────────────────────┘    │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
        │ reads                          │ subscribes to events           │ Camunda workers
        ▼                                ▼                                ▼
┌──────────────────┐   ┌────────────────────────────────┐   ┌────────────────────────────────┐
│  Central Master  │   │  Azure Service Bus             │   │  Camunda Platform 8 (AKS)      │
│  Data Platform   │   │  DocumentUploaded              │   │  BPMN: AI service tasks in     │
│  Products, forms │   │  ProposalSubmitted             │   │  parallel gateway pattern      │
│  field schemas   │   │  AIPreFillCompleted            │   │  Boundary events for AI errors │
└──────────────────┘   └────────────────────────────────┘   └────────────────────────────────┘
```

---

## 7. Camunda + AI: Orchestration Patterns

### 7.1 Pattern: AI Service Task with Boundary Error Event

Every AI call in the BPMN has an **attached boundary error event**. If Azure OpenAI returns an error, times out (> 30 seconds), or returns a response below confidence threshold, the boundary error fires and routes to a human fallback task.

```xml
<!-- BPMN snippet: AI Risk Scoring with fallback -->
<serviceTask id="AIRiskScoring" name="AI Risk Scoring"
    zeebe:jobType="ai-risk-scoring" />

<boundaryEvent id="AITimeoutError" attachedToRef="AIRiskScoring">
    <errorEventDefinition errorRef="AITimeoutError" />
</boundaryEvent>

<sequenceFlow sourceRef="AITimeoutError" targetRef="ManualUWReview" />
```

This ensures **zero cases are stuck** if Azure OpenAI is unavailable. The system degrades gracefully to the same manual processing path that exists today.

### 7.2 Pattern: Parallel AI Gateway

```xml
<!-- BPMN snippet: Parallel AI calls -->
<parallelGateway id="AIParallelSplit" />
<serviceTask id="RiskScoring" zeebe:jobType="ai-risk-scoring" />
<serviceTask id="MedicalHistory" zeebe:jobType="ai-medical-review" />
<serviceTask id="DocumentVerify" zeebe:jobType="ai-document-verify" />
<parallelGateway id="AIParallelJoin" />
<!-- All three complete before joining -->
```

### 7.3 Pattern: Human-in-the-Loop via Camunda User Task

```xml
<userTask id="UWHumanReview" name="Underwriter Review"
    zeebe:assignee="=uwQueue"
    zeebe:candidateGroups="underwriters">
    
    <!-- Variables visible in Tasklist -->
    <!-- aiRiskScore, aiRecommendation, aiReasoning are shown in the task form -->
</userTask>
```

Underwriters see the AI reasoning in Camunda Tasklist. They can approve, override with reason, or escalate. All actions are recorded in `AI_Audit_DB`.

---

## 8. ROI and Business Case

### 8.1 Cost Per Application

| Component | Current (manual) | Year 2 (AI-assisted) | Year 3 (AI-automated) |
|-----------|-----------------|---------------------|----------------------|
| Agent form-filling time cost | ₹600–900 | ₹200–350 | ₹80–150 |
| Processing / UW labour cost | ₹400–600 | ₹150–250 (60% auto-approved) | ₹50–100 (90% auto-approved) |
| Error re-work cost | ₹200–400 | ₹50–100 (AI validation) | ₹10–30 (AI catches most) |
| **Total cost per application** | **₹1,200–1,900** | **₹400–700** | **₹140–280** |
| **Saving vs current** | — | ~65% reduction | ~85% reduction |

### 8.2 Processing Time

| Stage | Current | Year 2 | Year 3 |
|-------|---------|--------|--------|
| Form filling (agent) | 16–23 min | 5–8 min | 2–4 min |
| Internal processing | 45–60 min | 10–15 min (60% auto) | < 1 min (90% auto) |
| **End-to-end journey** | **~90 min** | **~20 min** | **< 10 min** |

### 8.3 Volume-Based Impact (Example: 10,000 applications/month)

| Metric | Current | Year 3 AI |
|--------|---------|----------|
| Agent hours per month (form filling) | 3,000–3,800 hr | 330–660 hr |
| Cases requiring human UW review | 10,000 (100%) | 1,000–1,500 (10–15%) |
| UW team hours per month | 2,000–2,500 hr | 200–375 hr |
| Estimated labour saving per month | — | ₹35–50 lakh |
| Form completion rate | ~40% → 4,000 completed | ~85% → 8,500 completed (= +4,500 additional policies per month) |

The new business generated from completion rate improvement (4,500 additional policies × average premium) typically exceeds the AI infrastructure cost by 10–20×.

### 8.4 AI Infrastructure Cost

| Service | Estimated Monthly Cost | Notes |
|---------|----------------------|-------|
| Azure OpenAI GPT-4 (10K apps × 3 AI calls × ~2,000 tokens avg) | ~$800–1,200 | Scales linearly with volume |
| Azure Document Intelligence | ~$300–500 | Per document analysed |
| Azure Computer Vision | ~$100–200 | Per document image |
| Additional AKS pods for AI workers (3 workers × 2 replicas) | ~$150–250 | Co-located on existing cluster |
| `AI_Audit_DB` storage | ~$50–100 | SQL Basic/Standard tier |
| **Total AI infrastructure** | **~$1,400–2,250/month** | **~₹1.2–1.9 lakh/month** |

Compared to ₹35–50 lakh/month labour saving, the AI infrastructure cost is < 5% of the saving.

---

## 9. Data Privacy, IRDAI Compliance & Audit Trail

### 9.1 IRDAI Requirements

| Requirement | How Addressed |
|------------|---------------|
| All underwriting decisions must be explainable | AI reasoning paragraph stored in `AI_Audit_DB` with every decision; accessible to IRDAI on request |
| Human oversight for non-standard risks | Camunda routes any case below 90% confidence to human UW; no fully automated decline without human confirmation |
| Data retention for 10 years | `AI_Audit_DB` configured with 10-year archival policy |
| No discriminatory decision-making | Rule Engine rules (approved by actuarial + compliance) govern eligibility; AI cannot override Rule Engine outcomes |
| Customer consent for AI processing | Proposal form consent checkbox updated to include AI-based processing disclosure |

### 9.2 Data Minimisation

AI models receive only the fields needed for their specific task:
- Risk Scoring Worker: receives only age, occupation, sum assured, medical flags, reflex answers — not PAN, Aadhaar, or name
- Document Verifier: receives only document image hash and metadata — not the customer's application data
- Pre-Fill Service: receives CKYC data only after agent confirms client identity (2FA verification)

All Azure AI calls use managed identities (Azure Workload Identity in AKS) — no AI API keys exist in application code.

### 9.3 Audit Log Schema

```sql
-- AI_Audit_DB.AIDecisionLog
CREATE TABLE AIDecisionLog (
    Id              BIGINT IDENTITY PRIMARY KEY,
    ApplicationNo   VARCHAR(20) NOT NULL,
    WorkerType      VARCHAR(50) NOT NULL,   -- 'RiskScoring' | 'MedicalHistory' | 'DocumentVerify'
    ModelName       VARCHAR(50) NOT NULL,   -- 'gpt-4' | 'document-intelligence-v3' etc.
    InputHash       VARCHAR(64) NOT NULL,   -- SHA-256 of input (for tamper detection)
    Score           DECIMAL(5,2),
    Recommendation  VARCHAR(30),            -- 'AutoApprove' | 'StandardUW' | 'SeniorUW' | 'Decline'
    Reasoning       NVARCHAR(MAX),          -- AI's plain-language reasoning
    ProcessedAt     DATETIMEOFFSET NOT NULL,
    OverriddenBy    VARCHAR(50),            -- NULL if AI decision was accepted
    OverrideReason  NVARCHAR(500),
    OverriddenAt    DATETIMEOFFSET
);
```

---

## 10. Technology Stack Summary

| Component | Technology | Rationale |
|-----------|-----------|----------|
| **Large Language Model** | Azure OpenAI GPT-4 (via Azure-hosted endpoint) | Data residency in Azure India region; enterprise SLA; no data used for model training |
| **Document OCR** | Azure Document Intelligence (Form Recognizer v3) | Pre-built Aadhaar, PAN, and payslip models; high accuracy on Indian documents |
| **Image Analysis** | Azure Computer Vision | Document tampering detection; pre-built fraud detection models |
| **Chatbot** | Azure Bot Service + WhatsApp Business API (Twilio) | Existing BFSI use cases; scalable webhook integration |
| **Workflow Orchestration** | Camunda Platform 8 (Zeebe) | Already in use from Phase 4; AI workers are just another job type |
| **Rules Validation** | Microsoft.RulesEngine | Already in use from Phase 2; AI suggestions validated against business rules |
| **AI Worker Runtime** | .NET 8 AKS pods (Zeebe .NET Client) | Consistent with existing service stack; AKS from Phase 6 |
| **Secrets Management** | Azure Key Vault + Workload Identity | All AI API keys in Key Vault; pods use managed identity |
| **Audit Storage** | Azure SQL `AI_Audit_DB` | Structured audit queries; 10-year retention; familiar ops tooling |
| **Session State (chatbot)** | Azure Redis Cache | Existing Redis cluster; chatbot session TTL = 30 minutes |

---

## 11. Year-by-Year Roadmap Timeline

```
YEAR 1 (Phases 1–6):  Modern event-driven architecture, Rule Engine, BFF, Central Master Data, AKS
    └── Foundation for AI (event bus, Camunda, Key Vault, secure services)

YEAR 2, Q1–Q2 (Phase A):  Smart Form Filling
    ├── Week 01–04:  FGLI-AI-PreFillService (CKYC integration, field mapping against Central Master Data)
    ├── Week 05–08:  Azure Document Intelligence OCR integration (Aadhaar, PAN, payslip)
    ├── Week 09–12:  Conditional question filtering via Rule Engine (ReflexQuestionTriggers)
    └── Week 13–26:  FGLI-AI-ValidationService (real-time semantic validation); MFE updates for AI pre-fill UX

YEAR 2, Q3–Q4 (Phase B):  AI Underwriting Assistance
    ├── Week 27–34:  AIRiskScoringWorker (Camunda service task, GPT-4 risk scoring function)
    ├── Week 35–38:  AIMedicalHistoryWorker + AIDocumentVerifierWorker (parallel gateway)
    ├── Week 39–44:  Camunda Tasklist UW review UI (AI reasoning panel, accept/override workflow)
    └── Week 45–52:  AI_Audit_DB setup; IRDAI compliance review; production rollout with parallel shadow mode

YEAR 3, Q1–Q2 (Phase C):  Conversational AI
    ├── Week 01–12:  WhatsApp chatbot for CDF (Azure Bot Service + FGLI-AI-ConversationalService)
    └── Week 13–24:  Agent co-pilot panel in React MFE (product suggestion, pre-submission risk flag)

YEAR 3, Q3–Q4:  Model Improvement & Optimisation
    ├── Week 25–36:  Analyse UW override patterns; fine-tune risk scoring model on Infinity-specific data
    └── Week 37–52:  Feedback loop automation; expand auto-approval threshold (target: 90%+ auto-approved)
```

### Key Milestone: Shadow Mode

Every AI worker is deployed in **shadow mode** first:
- The AI worker runs and logs its result, but the process continues on the existing path
- After 4 weeks of shadow mode data, accuracy is reviewed
- Only after accuracy ≥ agreed threshold does the BPMN route switch to use the AI output

This ensures zero business disruption from AI errors. If an AI model degrades (e.g., after an OpenAI model update), shadow mode metrics detect it before it affects decisions.

---

*Document maintained by: Architecture Team*  
*Status: Year 2–3 Planning (Year 1 foundation in progress)*

*Related documents:*
- `INFINITY-MODERN-LOW-LEVEL-ARCHITECTURE.md` — Phases 1–6 modern architecture (prerequisite)
- `FORM-OPTIMIZATION-ANALYSIS.md` — Form fill time reduction analysis (source data)
- `AGENTIC-AI-DEMO.md` — AI demo architecture used for pre-sales POC
- `WORKFLOW-ENGINE-STRATEGIC-ASSESSMENT.md` — Camunda rationale

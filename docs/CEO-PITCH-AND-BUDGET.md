# CEO Approval Request — InsureVoice Hackathon Participation
## Building Agents for Real-World Challenges | Elastic Partner Track

**To:** CEO
**From:** Engineering Team
**Date:** 2026-05-26
**Re:** Approval to participate in Google × Elastic AI Hackathon 2026
**Ask:** 2–3 engineers for 12–14 days + ₹15,000 cloud budget

---

## The Opportunity in One Paragraph

Google and Elastic are running a global AI agent hackathon with a $5,000 first-place prize per partner track. We have a genuine competitive advantage: our Infinity platform roadmap already calls for an AI-powered conversational sales assistant in Year 3 (Phase C). We can build a working prototype of that exact feature — a voice-enabled insurance recommendation agent with compliance guardrails — in 12–14 days for under ₹15,000 in cloud costs. Whether we win or not, we walk away with a **live, demoable prototype** we can show to our insurance client in the next conversation, as a preview of the intelligent sales journey we're building into Infinity.

---

## What We Build

A voice-driven AI agent for insurance sales recommendations:

- Listens to a customer's needs over natural voice ("I'm 38, non-smoker, ₹15L income, need life + health cover")
- Semantically matches needs to insurance products using Elastic's ELSER AI model
- Automatically blocks non-compliant recommendations (age, income caps, smoker exclusions) before anything is shown
- Returns top-3 products with a voice-delivered, natural language explanation per recommendation

This is **Phase C of our own Year 3 roadmap** — built as a functional prototype, using production-grade technology (Google Cloud Agent Builder + Elastic ELSER), for ₹15,000 instead of ₹80–120 lakh as a formal development project.

---

## Budget Estimate

### Direct Cloud Costs (Actual Cash Outlay)

| Item | Provider | Cost (INR) | Cost (USD) | Notes |
|---|---|---|---|---|
| Vertex AI + Agent Builder (Gemini API calls, data store) | GCP | ₹5,000–8,000 | ~$60–100 | Development + 4-week demo period |
| Cloud STT / TTS (voice I/O) | GCP | ₹1,500–3,000 | ~$18–36 | Testing + demo sessions |
| Cloud Functions + Cloud Run | GCP | ₹500–1,000 | ~$6–12 | Minimal at hackathon scale |
| Dialogflow CX | GCP | ₹1,000–2,000 | ~$12–25 | Audio sessions during dev |
| Elasticsearch Cloud | Elastic | ₹0 | $0 | **14-day free trial — covers full build** |
| GitHub | GitHub | ₹0 | $0 | Free public repository |
| **Total Direct Cash** | | **₹8,000–14,000** | **~$100–170** | |
| Contingency (10%) | | ₹1,000 | ~$12 | |
| **Grand Total Cash** | | **~₹9,000–15,000** | **~$110–185** | |

### Team Time Investment (Opportunity Cost — Not New Spend)

Engineers are on existing payroll. This is the opportunity cost of their time.

| Role | Duration | Hours | Rate | Total (INR) |
|---|---|---|---|---|
| Senior/Intermediate Engineer × 2 | 12 days each | 96 hrs | ₹1,200/hr | ₹1,15,200 |
| Mid-level Engineer × 1 | 10 days | 80 hrs | ₹900/hr | ₹72,000 |
| Buffer (20% for testing + polish) | | | | ₹37,440 |
| **Total Opportunity Cost** | | | | **~₹2,25,000** |

### All-In Investment

| Category | INR | USD |
|---|---|---|
| Direct cloud cash | ₹9,000–15,000 | ~$110–185 |
| Team opportunity cost | ~₹2,25,000 | ~$2,700 |
| **Total all-in** | **~₹2,35,000–2,40,000** | **~$2,850** |

---

## Return on Investment

### Prize Upside

| Placement | Prize | INR Equivalent | vs. Total Investment |
|---|---|---|---|
| 🥇 1st Place | $5,000 | ~₹4,15,000 | **+₹1,75,000 profit** |
| 🥈 2nd Place | $3,000 | ~₹2,49,000 | **Break-even + ₹9,000** |
| 🥉 3rd Place | $2,000 | ~₹1,66,000 | Recovers cloud cost + 70% of team time |

> **Even a 3rd-place finish recovers the cloud cost entirely and 70% of team opportunity cost.**

### Demo Asset Value (Independent of Prize)

| Asset | Business Value |
|---|---|
| Working voice + AI recommendation demo | Directly demoable to our insurance client in next engagement discussion as a preview of Infinity Phase C |
| Proof-of-concept for Year 3 Roadmap | Moves the roadmap conversation from "planned" to "here's a prototype" — accelerates client buy-in for AI investment |
| Architecture validated | Google Cloud Agent Builder multi-agent pattern proven before committing to ₹1 crore formal build |
| Team hands-on with GenAI stack | 2–3 engineers trained on Agent Builder, ELSER, Voice AI — directly applicable to Phase C delivery |
| Brand credibility | Google + Elastic ecosystem recognition; potential for blog/case study exposure |

### Conservative ROI Scenario (No Prize)

- Cloud spend: ₹15,000 (worst case)
- Demo asset value: One client meeting where we show this prototype instead of slides converts the conversation on the AI roadmap investment
- One project extension or new module from that conversation = ₹25–50 lakh revenue
- **ROI on ₹15,000 cash spend: 1,600x–3,300x on realistic client conversion**

---

## Why We Can Win the Elastic Track

| Differentiator | Other Teams Likely | Our Entry |
|---|---|---|
| Elastic integration depth | Basic Elasticsearch keyword search | ELSER v2 — Elastic's proprietary sparse vector AI model |
| Domain | Generic productivity tools | Insurance sales — one of the 3 highlighted challenge domains |
| Interface | Text chat | Voice-enabled — significantly more impressive in a 3-minute video |
| Compliance intelligence | None | Guardrail agent that blocks ineligible products and explains why |
| Multi-step reasoning | Single tool call | Full pipeline: intake → search → validate → rank → explain |

Elastic's judges will recognize ELSER usage as the deepest possible integration of their technology. This is what they want to highlight in their hackathon showcase.

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| We don't place in top 3 | Medium | Cloud cost only (₹15,000) | Demo asset remains regardless |
| Integration is harder than estimated | Low | Scope reduction to simpler voice demo | Pre-agreed MVP scope; fallback = text-only |
| Current project impact | Low | Minor sprint delay | Engineers selected from available capacity |
| Cloud costs exceed estimate | Very Low | Max ₹5,000 overage | GCP billing alerts set at ₹15,000 cap |

**Worst case**: ₹15,000 cloud spend + 12–14 days team time, and we have a working AI insurance demo prototype. There is no scenario where this is a loss.

---

## IP & Open Source Considerations

The hackathon requires a public open-source repository (Apache 2.0). The following is clarified:

- **Open-sourced**: The code, configuration, and architecture of the prototype
- **Our IP remains ours**: The insurance domain knowledge embedded in the system prompt, the guardrail rule design, the product catalog architecture, and the integration pattern are documented internally and inform our production roadmap
- **No proprietary client data**: The demo uses entirely synthetic insurance product data — no real client data is used or exposed
- **No competitor advantage**: The codebase demonstrates our technical capability — which is a marketing asset, not a liability

---

## Post-Hackathon Demo Maintenance (Optional)

If we want to keep the demo live for ongoing client pitches after the hackathon:

| Item | Monthly Cost (INR) |
|---|---|
| Elastic Cloud (with ELSER ML tier) | ₹8,000–16,000 |
| GCP (demo traffic) | ₹2,000–4,000 |
| **Total to maintain live** | **~₹10,000–20,000/month** |

*Recommendation: Keep live for 3 months post-hackathon (₹30,000–60,000) to support client demo cycles.*

---

## Timeline

| Week | What Gets Built | Who |
|---|---|---|
| Week 1, Days 1–4 | Elastic Cloud setup, ELSER index, synthetic data ingestion, Elastic MCP verified | Engineer 3 |
| Week 1, Days 4–7 | Agent Builder Root Agent + 3 Sub-Agents, Cloud Functions (compliance + ranking) | Engineer 1 |
| Week 2, Days 7–10 | Dialogflow CX voice flow, STT/TTS integration, end-to-end testing | Engineer 2 |
| Week 2, Days 10–12 | Demo scenarios tested, demo video recorded, README finalized | All |
| Week 2, Days 12–14 | Devpost submission, client demo prep | All |

---

## The Ask

**Approve the following:**

1. **Team time**: 3 engineers (to be named) for 12–14 days
2. **Cloud budget**: ₹15,000 maximum for GCP usage
3. **GitHub repository**: Public open-source repository under Apache 2.0 license

**Decision required by**: [Date — 3 days before hackathon registration deadline]

---

## Summary

| | |
|---|---|
| **Cash spend** | ₹9,000–15,000 |
| **Prize potential** | ₹1,65,000–4,15,000 |
| **Demo asset** | Live prototype of Infinity Year 3 Phase C |
| **Break-even placement** | 3rd place ($2,000) |
| **Worst case** | ₹15,000 spent + working AI demo in hand |
| **Best case** | ₹4,15,000 prize + client demo momentum + team capability built |

This is one of the lowest-cost, highest-visibility investments we can make this quarter. We are not building something speculative — we are building Phase C of our own roadmap, for ₹15,000, with a prize upside.

---

*Technical architecture details: [HACKATHON-PLAN.md](HACKATHON-PLAN.md)*
*Project structure and setup: [../README.md](../README.md)*

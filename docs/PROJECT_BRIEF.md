# InsureVoice — Project Brief
**Hackathon:** Google Cloud Rapid Agent — Elastic Partner Track
**Submission deadline:** 2026-06-11 14:00 PT
**Last updated:** 2026-06-10 (v7 — cover page + light theme UI)

**Recent updates:**
- **Day 10 (2026-06-10) — v7:** Two-page frontend launched. Cover/landing page (`index.html`) at `/` with marketing content + product selector. Light theme voice UI (`agent.html`) at `/agent` — white/blue palette, `clearRect` canvas fix (no black box), mic popup fixed (`onend` no-auto-restart + `_restartPending` debounce). Original dark UI preserved at `/app_dark`. Branch: `abhishek-final-branch`.
- **Day 8 (2026-06-05):** Tier B voice-stack swap — Chirp 3 HD streaming TTS (B1) + Speech-to-Text v2 streaming (B2) + Gemini Flash-Lite intent classifier (B4). All in-tree on `stable_v4`; **not yet deployed** — live revision `00030-jc7` still serves the Day 7 baseline. Day 9 plan: `--no-traffic` deploy + browser smoke + AC-B4.11 latency probe before traffic promotion. B3 (Silero VAD) DROPPED per D1 (hackathon-rule risk). B5/B6/B7 DEFERRED to Day 9+. Test suite on `stable_v4`: **567 passed / 29 skipped / 0 failed**.
- **Day 7 (2026-06-04):** T1-T4 polish + Atul Story 5/6 merge + LIVE deploy on rev `00030-jc7`. 6/6 live arc battery PASS.

---

## What we're building

InsureVoice is a voice-driven AI insurance advisor. The user speaks via the browser, an AI agent collects 8 fields conversationally (name, age, smoker, income, health, family size, coverage goals, sum assured), then runs a deterministic pipeline that:

1. Searches the product catalog using **Elastic ELSER v2 hybrid retriever** (semantic + BM25)
2. Filters by hard eligibility rules (age, income, smoker status) via Cloud Function
3. Ranks the survivors by suitability score
4. Generates a voice-friendly recommendation (≤120 words)

The user can then say "tell me about LifeGuard Plus" or "the second one" to get a deterministic single-product detail. Or "start over" to reset.

**Live demo:** https://insure-voice-agent-mhojvvbq4a-uc.a.run.app/
**User flow (v7):** Cover page → "Talk to AI Advisor" → light theme voice agent (`/agent`)

---

## Tech stack

| Layer | Technology (live `00030-jc7` — Day 7 baseline) | Technology (`stable_v4` Day 8 Tier B, pre-deploy) |
|---|---|---|
| Voice STT | Web Speech API (`webkitSpeechRecognition`), 1.2s silence-debounce | Google Cloud Speech-to-Text v2 + Chirp 2, native VAD 800ms, `WebSocket /stt/stream`, AudioWorklet 16kHz PCM mic |
| Voice TTS | Cloud TTS WaveNet (`en-IN-Wavenet-D`) | Google Cloud Chirp 3 HD (`en-IN-Chirp3-HD-Aoede`), 24kHz MP3, `POST /tts/stream` (per-IP rate limit 30 req/min) |
| Intent classification (follow-up turns) | Regex in `followup.py` | (Day 7 regex retained as fallback) + Gemini Flash-Lite separate sub-agent (`app_name="insure-voice-classifier"`), feature-flagged via `USE_LLM_INTENT_CLASSIFIER` (default off) |
| Agent orchestration | Google ADK 2.1.0, Vertex AI Gemini 2.5 Flash Lite (root) + Flash (sub-agent) | Same |
| Search | Elastic Cloud Serverless, ELSER v2 sparse vectors, RRF hybrid | Same |
| Backend | FastAPI on Cloud Run, 3 Cloud Functions (search, compliance, rank) | Same + 3 new backend modules (`tts_streaming.py`, `stt_websocket.py`, `intent_classifier.py`) |
| Hosting | Google Cloud Platform — project `voice-sales-agent` | Same |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 1 — Voice (browser)                                          │
│  Web Speech API STT → /invoke → Cloud TTS WaveNet                   │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ user message + session_id
┌──────────────────────────▼──────────────────────────────────────────┐
│  Layer 2 — Agent Orchestration (insure-voice-agent Cloud Run)       │
│                                                                     │
│  Phase 1 — PRE-LLM (deterministic Python, no LLM call):             │
│    • Reset detection ("start over" / "reset")                       │
│    • Intake state machine (8 fields, regex validators)              │
│    • Follow-up dispatch (named/ordinal "tell me about X")           │
│                                                                     │
│  Phase 2 — LLM PIPELINE (only after intake complete + no follow-up):│
│    • LlmAgent (Gemini 2.5 Flash Lite) + ADK before_model_callback   │
│    • Mechanical tool routing via tool_config.mode=ANY               │
│    • Tools: search → compliance → rank → recommend_and_explain      │
│    • product_type arg injection inside search wrapper               │
│    • Session-state arg substitution for downstream tools            │
│                                                                     │
│  Phase 3 — POST-LLM:                                                │
│    • Deterministic template fallback if LLM bails                   │
│    • Mojibake sanitization (cp1252→utf-8)                           │
│    • Top3 snapshot for next-turn follow-up                          │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│  Layer 3 — Elastic Cloud Serverless                                 │
│  ELSER v2 + RRF hybrid + hard filters (age/income/smoker/type)      │
│  Index: insurance_products_current (28 products, 7 types)           │
└─────────────────────────────────────────────────────────────────────┘
```

Full architecture deep-dive: `docs/ARCHITECTURE.md` (~700 lines).

---

## Build progression

| Day | Owner | Work |
|---|---|---|
| 1-3 | Atul | GCP infra, Cloud Functions, ELSER index, basic agent wiring |
| 4 | Abhishek | Frontend bundling, same-origin Cloud Run deploy, Markdown rendering, ELSER ranking badges |
| 5 | Abhishek | Stability sprint — temperature config, ADK callback enforcement (mode=ANY tool routing), session-state argument substitution, deterministic intake state machine, deterministic template fallback. AC-3 (root agent invokes search) went 0/15 → 10/10 PASS. |
| 6 | Abhishek | Bug 6 fix (product_type argument injector), Bug 9/10 fix (follow-up state machine), Bug 11/13/14 mitigation (defense-in-depth prompt rules), critical timeout fix (2.5s → 8.0s caught by live E2E test) |
| 7 | Abhishek | T1-T4 polish + Atul Story 5/6 merge + LIVE deploy on rev `00030-jc7`. 6/6 live arc battery PASS. Phase 1 correctness fixes (Bugs J/K/L/M — Named-Product-Wins, session-dedup, mojibake fix) shipped in `stable_v4`. |
| 8 | Abhishek | Tier B voice-stack swap — B1 Chirp 3 HD TTS + B2 Speech-to-Text v2 streaming + B4 Flash-Lite intent classifier (separate sub-agent under `app_name="insure-voice-classifier"`, feature-flagged via `USE_LLM_INTENT_CLASSIFIER`). 3 NEW backend modules + 3 NEW frontend modules. D1 dropped Silero VAD (hackathon rule risk). D13 `CANONICAL_FAREWELL_TEXT` constant. Reviewer M1+M2 fixes applied. **Test suite 551 → 567 PASS.** Tier B in-tree on `stable_v4`; **NOT yet deployed**. |

Today's `stable_v4` working copy is to be pushed onto branch `abhishek-stable-branch` (parent at push: `2cb367e` — Day 7 baseline). Live revision `00030-jc7` is unchanged. Day 9 plan: `--no-traffic` deploy + browser smoke + AC-B4.11 latency probe before traffic promotion.

---

## Key design decisions

### 1. Module-level dicts instead of ADK session state
ADK's `InMemorySessionService` doesn't reliably persist mutations to `session.state` across `get_session` calls in this deployment. We work around this with module-level Python dicts (`_INTAKE_BY_SESSION`, `PROFILE_BY_SESSION`, `TOP3_BY_SESSION`) keyed by `session_id`. Works at `--max-instances=1`. Won't survive horizontal scaling — Firestore migration is the post-hackathon path.

### 2. Mechanical tool-call routing via `before_model_callback`
flash-lite ignores prompt rules ("MUST call X next") at ~94% rate. We use ADK's `before_model_callback` to set `tool_config={mode:ANY, allowed_function_names:[...]}` before each LLM turn, which **forces** the next tool selection. AC-3 went from 0/15 PASS to 10/10 PASS.

### 3. Session-state argument substitution
When `search_products` returns candidates, we stash them in `tool_context.state["last_search_candidates"]`. The next tool wrapper (`compliance_check`) **ignores** the LLM-passed `candidates` arg (which is reliably `[null, null, null, null]` on flash-lite) and reads from session state instead. Python owns structured-data threading; the LLM only owns intent.

### 4. Deterministic fallbacks at every layer
- If LLM bails without text → deterministic template renders top3 server-side.
- If LLM doesn't fire tools → programmatic completion runs the pipeline directly using validated profile.
- If user says "tell me about X" → deterministic generator returns single-product detail without LLM call.
- If sub-agent 429s → fall back to programmatic completion.

The pattern: **every LLM dependency has a Python fallback.**

### 5. Hybrid voice/text turn classification
Most user turns (intake questions, follow-ups, reset) are sub-100ms because they bypass the LLM entirely. Only the pipeline-firing turn (1 per session, after intake completion) hits the full 7-10s budget. The LLM is used surgically, not pervasively.

---

## What's been validated

**Test suite (Day 8, `stable_v4`):** 567 passed / 29 skipped / 0 failed (~28.86s). Up from 551 by +12 (`tests/test_intent_classifier.py`) + 4 (`tests/test_b2_resume_tail.py`).

**End-to-end demo arc (12 turns, against live infrastructure)** — verified 2026-06-03 against live revision `00030-jc7`:
- 8 intake turns → all canonical questions correct
- Turn 9 (pipeline) → 3 products returned, voice text generated
- Turn 10 ("tell me about LifeGuard Plus") → deterministic detail, NO LLM call
- Turn 11 ("second one") → deterministic detail of `top3[1]`, NO LLM call
- Turn 12 ("start over") → all state cleared, fresh greeting

Verbatim log signals confirmed for every key path: argument injection, search payload, top3 snapshot, follow-up dispatch, voice generation, reset. Zero LLM dispatches after pipeline completion (proves bypass works).

---

## What's outstanding

| Item | Status | Why deferred |
|---|---|---|
| Tier B Cloud Run deploy + browser smoke + AC-B4.11 latency probe | Day 9 | `--no-traffic` deploy first, smoke + latency PASS before promoting traffic. Live rev `00030-jc7` stays available for instant rollback. |
| B5 — Tool-result-only render | Day 9+ | Predecessor on G5 + B4 + v4 baseline capture. |
| B6 — Backchannel injection | Day 9+ | Predecessor on B1 voice lock. AC-B6.0 measurement gate before threshold lock. |
| B7 — ADK eval smoke harness | Day 9+ | D13 (`CANONICAL_FAREWELL_TEXT` constant) predecessor landed today. 5-case smoke only per D2. Local-run-only per D14. |
| Catalog expansion (~20 new products incl. disease-specific health) | Day 7 backlog | Need ELSER index decision (shared vs isolated v2) |
| Compare-products feature ("compare X and Y") | Parked | Currently falls through to LLM; deterministic implementation deferred |
| Demo deck + Devpost video | Day 10-12 | Polish + storytelling work |
| Devpost final submission package | Day 11-14 | Code link + video + write-up |
| 5 root-prompt bugs (4, 11, 13, 14, 15) | Mitigated | Architecture fixes (S2'/S3 deterministic) bypass LLM-prose path; ideal fix is in root prompt design |

---

## Repository & deployment

| Item | Value |
|---|---|
| GitHub repo | `atul-goel_incrp/insure-voice-agent` (private) |
| Active branch | `abhishek-stable-branch` |
| Parent at next push | `2cb367e` (Day 7 baseline) |
| Live demo URL | https://insure-voice-agent-mhojvvbq4a-uc.a.run.app/ (rev `00030-jc7`, Day 7 baseline — Tier B not yet deployed) |
| GCP project | `voice-sales-agent` (project number `1055350728739`) |
| Region | `us-central1` |
| Hackathon URL | rapid-agent.devpost.com |
| Track | Elastic Partner Track ($5k / $3k / $2k prizes) |

---

## Where to look

**For technical context:**
- `docs/ARCHITECTURE.md` — full architecture deep-dive (~700 lines, diagrams + tables)
- `STABILITY_CHANGELOG.md` — per-decision evidence trail (chronological)
- `docs/DEMO-SCRIPT.md` — pre-demo checklist + scene-by-scene script

**For the Day 6 deploy bundle:**
- `tasks/2026-06-03_hackathon_day6_atul_followup/reports/Day6_Diff_Review.md` — what's in the latest commit

**For the code itself (in priority order):**
- `agent_builder/main.py` (lines 140-510) — `/invoke` 3-phase pipeline
- `agent_builder/agent_definition.py` (lines 55-330) — tool wrappers + arg injection + callback
- `agent_builder/intake.py` — 8-field state machine
- `agent_builder/followup.py` — follow-up dispatch state machine
- `agent_builder/shared_state.py` — module-level session-state dicts

---

## Production-readiness notes (post-hackathon)

| Concern | Current state | Production-grade fix |
|---|---|---|
| Module-level dict state | Works at `--max-instances=1` | Migrate to Firestore-backed sessions |
| Cloud Function auth | Public (allow-unauthenticated) | Require IAM auth on `compliance_check` and `rank_products` |
| Voice data privacy | STT in browser; nothing persisted | Confirm TTS isn't logging audio; add explicit GDPR opt-in |
| Elasticsearch access | API key (read+write) | Scope read-only API key for the agent; rotate quarterly |
| Catalog size | 28 products, 7 types | Expand to ~48 products with disease-specific descriptions |
| Compare-products feature | Falls through to LLM | Deterministic implementation |

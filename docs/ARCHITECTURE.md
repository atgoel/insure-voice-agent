# InsureVoice — Architecture Deep Dive
## Google Cloud Agent Builder + Elastic ELSER + Vertex AI Gemini

> **Last updated**: 2026-06-05 (Day 8 EOD, Tier B implemented in `stable_v4` — pre-push, pre-deploy)
> **Status**: Live revision `00030-jc7` (Day 7 baseline) serves 100% traffic. Tier B voice-stack swap (B1 Chirp 3 HD TTS + B2 Speech-to-Text v2 + B4 Flash-Lite intent classifier) is in-tree on `stable_v4` and will deploy to a new revision with `--no-traffic` on Day 9 before traffic promotion.
> **Active branch**: `abhishek-stable-branch` (parent at push: `2cb367e` — Day 7 baseline)
> **Test suite (Day 8, `stable_v4`)**: **567 passed / 29 skipped / 0 failed** (~28.86s). Up from 551 by +12 (`test_intent_classifier.py`) + 4 (`test_b2_resume_tail.py`).

---

## Deployed Services (Production)

| Service | Type | URL | Transport |
|---|---|---|---|
| `elastic-mcp-server` | Cloud Run | `https://elastic-mcp-server-mhojvvbq4a-uc.a.run.app` | REST `POST /search_products` |
| `elastic-mcp-server-native` | Cloud Run | `https://elastic-mcp-server-native-1055350728739.us-central1.run.app` | MCP `/mcp` (Streamable HTTP) — **kept as audit/demo asset; not on hot path** |
| `insure-voice-agent` | Cloud Run | `https://insure-voice-agent-mhojvvbq4a-uc.a.run.app` | FastAPI `/invoke`, `/health` |
| `compliance_check` | Cloud Function (2nd gen) | `https://us-central1-voice-sales-agent.cloudfunctions.net/compliance_check` | HTTP POST |
| `rank_products` | Cloud Function (2nd gen) | `https://us-central1-voice-sales-agent.cloudfunctions.net/rank_products` | HTTP POST |

**LLM**: Gemini 2.5 Flash Lite (`gemini-2.5-flash-lite`) on Vertex AI `us-central1`
**Sub-agent LLM**: Gemini 2.5 Flash (`gemini-2.5-flash`) — `recommend_and_explain` only
**GCP Project**: `voice-sales-agent` (project number `1055350728739`)
**Elasticsearch**: `https://my-elasticsearch-project-c2e88f.es.us-central1.gcp.elastic.cloud:443`
**Secret Manager**: `ES_API_KEY` secret (version 7, CRLF-free)

---

## What Changed Day 5 → Day 6 (Stability + Demo Hardening)

The architecture below reflects significant changes from the original Day 1-3 build. This section is the **landmark map** so future readers don't get lost.

### Day 5 — Stability Sprint
- **C.1 + C.3** — Explicit temperature on root agent (0.25) + sub-agent (0.3) + max_output_tokens=400. Reduced LLM variance.
- **C.4** — Attempted root model upgrade to `gemini-2.5-flash`. Rolled back (no improvement).
- **C.5** — ADK `before_model_callback` with `tool_config={mode:ANY, allowed_function_names:[...]}`. **Mechanically enforces tool-call sequence** instead of trusting prompt rules. AC-3 went 0/15 → 5/5 PASS.
- **C.5b** — Session-state argument substitution + programmatic completion + deterministic template fallback. **search_products stashes candidates in session state**; **compliance_check ignores LLM-passed `candidates` arg** and reads from session state instead (flash-lite reliably passes `[null, null, null, null]`). AC-3 went 5/5 → 10/10 PASS.
- **P.1** — Attempted root prompt full rewrite. Failed. Kept old prompt.
- **P.2** — Conversational intake state machine in `intake.py` (deterministic Python validators for 8 fields). Module-level `_INTAKE_BY_SESSION` dict because **ADK `InMemorySessionService` does NOT reliably persist mutations to `session.state` across `get_session` calls** in this deployment.
- **F.1** — FE empty-response fallback (Day 4 already shipped).
- **F.2** — Mojibake fix at egress (`cp1252 → utf-8` reversal). Fixed `â‚¹` → `₹` in voice text and product cards.
- **MCPToolset → REST FunctionTool switch** — search_products migrated from MCP-native (Streamable HTTP JSON-RPC) to plain REST FunctionTool (`POST $ELASTIC_MCP_SERVER_URL/search_products`). Same backend, simpler integration. The MCP-native server (`elastic-mcp-server-native`) is preserved as an audit/demo artifact for the hackathon's Elastic Partner Track requirement, but the hot path is REST.

### Day 6 — Atul-Domain Follow-up + Demo Hardening
- **S2'** — `product_type` argument injector. Mechanically injects `product_type` into `search_products` HTTP payload from validated intake `coverage_goals[0]`. Overrides whatever the LLM passed (or didn't). Bug 6 (mixed product types) eliminated.
- **S3** — Follow-up state machine. Detects "tell me about X" / "second one" / "start over" intents BEFORE LLM dispatch. Routes to deterministic single-product voice text generator. NO LLM call on follow-up turns. Bugs 9, 10 fixed.
- **S4** — Defense-in-depth prompt rules in `sub_agent3_explainer_prompt.md`. Catalog facts + smoker logic + premium-source clarification. Per L-001, prompt-only rules are unreliable on small models — these are belt-and-suspenders for cases where LLM-prose path is exercised.
- **S5 timeout fix** — `httpx.post(timeout=2.5)` → `timeout=8.0`. CF cold-start + ELSER inference + RRF query routinely takes 3-5s. The 2.5s timeout was a silent demo blocker masquerading as ELSER zero-result.

For the per-sub-task evidence trail, see `STABILITY_CHANGELOG.md` (the canonical record).

---

## What Changed Day 7 → Day 8 (Tier B Voice-Stack Swap, 2026-06-05)

Tier B replaces the browser-native voice stack (Web Speech API STT + `SpeechSynthesisUtterance` TTS) with a Google Cloud voice stack and adds an LLM-based intent classifier on follow-up turns. **All Tier B work is in-tree on `stable_v4` but NOT yet deployed** — live revision `00030-jc7` is still the Day 7 baseline.

### New backend modules

| Module | Purpose | Public API |
|---|---|---|
| `agent_builder/tts_streaming.py` (B1, 396 lines) | Streaming TTS over Google Cloud Text-to-Speech, voice `en-IN-Chirp3-HD-Aoede`, 24kHz MP3. Per-IP `collections.deque` rate limiter (30 req/min, 429 on breach). | `synthesize_bytes(text)`, `synthesize_chunks(text)` async generator. |
| `agent_builder/stt_websocket.py` (B2, 549 lines) | Speech-to-Text v2 + Chirp 2 model + native VAD tuned to 800ms. WebSocket transport. en-IN. Graceful `SDK_UNAVAILABLE` degradation if `google-cloud-speech` isn't importable. | `stt_stream_handler(websocket)` FastAPI route handler. |
| `agent_builder/intent_classifier.py` (B4, 565 lines) | Gemini 2.5 Flash-Lite intent classifier as a separate `LlmAgent` sub-agent. Own ADK `Runner` and own `before_model_callback=_force_classifier_tool`. Returns one of `NAMED_PRODUCT` / `ORDINAL` / `POLICY_QUESTION` / `AMBIGUOUS` with confidence. | `classify_intent_async`, `classify_followup_intent`, `init_classifier_runner`. |

### New frontend modules

| Module | Purpose |
|---|---|
| `agent_builder/frontend/voice-player.js` (428 lines) | `<audio>` MediaSource MP3 player + D8 lock/resume hooks around `window.__voiceAudioCtx`. |
| `agent_builder/frontend/voice/stt-client.js` | WebSocket STT client. Publishes `window.__voiceAudioCtx` + `window.__voiceMicSuspended` on init. |
| `agent_builder/frontend/voice/audio-worklet-processor.js` | AudioWorkletProcessor for low-latency 16kHz PCM mic capture. |

### New endpoints in `main.py`

| Method | Path | Notes |
|---|---|---|
| `POST` | `/tts/stream` | B1. In-memory per-IP rate limit 30 req/min (deque). 429 on breach. |
| `WebSocket` | `/stt/stream` | B2. **Must be registered BEFORE the StaticFiles mount on `/`** — the static mount uses `html=True` and would otherwise swallow undeclared sibling paths (including the WebSocket upgrade). |

### B4 dispatch in `/invoke` (Phase 1c — follow-up turns only)

When intake is complete AND `top3` is present in `shared_state.TOP3_BY_SESSION` AND the env var `USE_LLM_INTENT_CLASSIFIER` is truthy (default **off**), `/invoke` dispatches the classifier BEFORE the existing regex `detect_followup_intent` path:

```
1. Read top3_ids from shared_state.TOP3_BY_SESSION[session_id]
2. classification = await classify_intent_async(session_id, user_message, top3_ids, user_id)
3. decision     = route_classification(classification)
4. Branch on decision["action"]:
     ROUTE_NAMED   → resolve top3 product by target_product_id → return build_voice_text(matched)   (NO LLM)
     ROUTE_ORDINAL → resolve top3[ordinal_index]               → return build_voice_text(matched)   (NO LLM)
     CLARIFY       → return clarification text                                                       (NO LLM)
     ESCALATE      → return no_match_voice_text()                                                    (NO LLM)
     FREE_FORM     → set _skip_regex_followup = True; raise LookupError("B4_FREE_FORM_BYPASS")
                     to land at the LLM passthrough below (M2 fix — POLICY_QUESTION → free-form, NOT B5)
     FALLBACK_LLM  → fall through to existing regex detect_followup_intent path (low-confidence safe default)
5. Any exception in classifier → log B4_DISPATCH_FAILED, fall through to regex path (defense-in-depth)
```

**Confidence thresholds:**

| Confidence | Action |
|---|---|
| ≥ 0.7 | Honor classification (`ROUTE_NAMED` / `ROUTE_ORDINAL` / `FREE_FORM` per intent) |
| 0.5 ≤ c < 0.7 | Force-clarify band → `CLARIFY` regardless of intent |
| < 0.5 | `FALLBACK_LLM` — defer to regex path |

**Feature flag:** `USE_LLM_INTENT_CLASSIFIER` env var. Default **off** for the Day 9 first deploy — flip on after `--no-traffic` smoke test passes against rev `00031` (or whatever the next promoted revision is).

### End-to-end voice flow (Tier B)

```
USER SPEAKS
  ↓ (mic 16kHz PCM via AudioWorklet)
WebSocket /stt/stream
  ↓ (gRPC to Speech-to-Text v2 / Chirp 2, server-side VAD 800ms)
text → /invoke
  ↓ (root agent + intake + recommendation)
[optional B4 classifier sub-agent for follow-up turns]
  ↓ (Runner with app_name="insure-voice-classifier")
text response → /tts/stream POST
  ↓ (Chirp 3 HD synthesize, 24kHz MP3)
MediaSource <audio> → user hears
```

The pre-LLM / LLM-pipeline / post-LLM 3-phase architecture from Day 5/6 is unchanged. B4 hooks in at the start of Phase 1c (follow-up dispatch) and only invokes the Flash-Lite classifier sub-agent when `USE_LLM_INTENT_CLASSIFIER=true`. Below the 0.7 confidence threshold the agent falls back to the existing regex path in `followup.py`. Above 0.7 it routes to the corresponding deterministic branch (`NAMED_PRODUCT` / `ORDINAL`) or — for `POLICY_QUESTION` — falls through to the LLM passthrough via the M2 `_skip_regex_followup` flag.

### D8 contract — FE↔BE coordination via two browser globals

To prevent the Chirp 3 playback from being captured by the open mic and re-transcribed (the "agent transcribes its own voice" echo bug), B1 and B2 coordinate via two `window` globals:

```
window.__voiceAudioCtx       // The mic-capture AudioContext. B2 publishes on STT init; B1 reads only.
window.__voiceMicSuspended   // Boolean flag. B2 initializes; B1 toggles around <audio> playback.
```

Sequence on TTS playback:

1. `<audio>.onplay` → B1 calls `window.__voiceAudioCtx.suspend()` → mic goes silent.
2. TTS finishes → `<audio>.onended` → **B1 waits 200ms (echo decay)** → calls `.resume()` → mic listens again.

The 200ms echo-tail is load-bearing — without it, the mic captures the speaker echo's tail and the agent transcribes its own goodbye. The Reviewer M1 fix removed an earlier mistake in `voice-player.js` where the player was creating + publishing the `window.__voiceAudioCtx` global itself (inverting the contract). It now uses a null-safe `_readAudioCtx` helper and reads only.

### D10 contract — separate Runner / separate `app_name` for the classifier

The B4 classifier is a separate `LlmAgent` with its own ADK `Runner`, registered under `app_name="insure-voice-classifier"` rather than the root agent's `app_name="insure-voice"`. Why: ADK supports exactly one `before_model_callback` per agent. The root `LlmAgent` already has `_force_tool_call_mid_pipeline` (the C.5 enforcement callback from Day 5). Running the classifier on the same Runner would either fight that callback or leak the classifier's `function_response` events into the root agent's session event log and break the C.5 mid-pipeline state machine. Separate `app_name` isolates the two sessions cleanly. The classifier callback is `_force_classifier_tool`.

### D13 — `CANONICAL_FAREWELL_TEXT` constant

`followup.py:393` was previously a `farewell_voice_text()` function. It is now a module-level constant `CANONICAL_FAREWELL_TEXT = "..."` with a back-compat lambda `farewell_voice_text = lambda: CANONICAL_FAREWELL_TEXT` for callers that still invoke it as a function. Required predecessor for the deferred B7 eval harness (byte-identical assertions on done_001 / done_002 cases).

### D1 — B3 (Silero VAD) DROPPED

The original Tier B plan included Silero VAD as a fourth sub-task (browser-side neural VAD via ONNX, ~150-300ms barge-in latency win). It was dropped on 2026-06-05 because Silero is a neural net at inference time — plausibly violates the Devpost rule "all other AI tools not permitted". Server-side VAD from Speech-to-Text v2 (D2's `voice_activity_events` at 800ms) is the only VAD layer.

### Hackathon-rule audit (Tier B)

| Sub-task | Uses | Rule check |
|---|---|---|
| B1 — Chirp 3 HD TTS | Google Cloud Text-to-Speech API | ✅ Google Cloud, not "AI tool". |
| B2 — STT v2 streaming | Google Cloud Speech-to-Text v2 + Chirp 2 | ✅ Same vendor, same tier. |
| B4 — Flash-Lite classifier | Gemini 2.5 Flash-Lite via ADK | ✅ Gemini is mandated. |
| ~~B3 — Silero VAD~~ | Open-source ONNX neural net | ❌ DROPPED (D1). |

**Verdict:** Tier B is rule-compliant. Everything on Google Cloud's stack.

### Reviewer pass — M1 + M2

* **M1** — `voice-player.js` `_ensureAudioCtx` removed → `_readAudioCtx` null-safe read (D8 contract restored — B1 reads `window.__voiceAudioCtx`, never publishes it; B2 owns the publish).
* **M2** — `main.py` adds `_skip_regex_followup` flag for the FREE_FORM intent path; raises `LookupError("B4_FREE_FORM_BYPASS")` (caught at `main.py:680`-ish) to land at the LLM passthrough below.

### Deploy status (Day 8 EOD)

| Item | Status |
|---|---|
| Live serving revision | `00030-jc7` (Day 7 baseline) — 100% traffic. Tier B is **NOT** on this revision. |
| `stable_v4` test suite | **567 PASS / 29 SKIP / 0 FAIL** (~28.86s) — up from 551 baseline (+12 `test_intent_classifier.py` + 4 `test_b2_resume_tail.py`). |
| Day 9 plan | Cloud Build deploy to a new revision with `--no-traffic`. Browser smoke test (mic + speaker echo). Then promote traffic. |
| Feature flag default at first deploy | `USE_LLM_INTENT_CLASSIFIER=false` — classifier code lives in the image but the dispatch is a no-op until the flag is flipped. Roll-back is one env-var change. |
| Deferred (post-Day 9) | AC-B4.11 40-call live latency probe; B5/B6/B7 implementer fan-out; classifier session GC after `clf-{session_id}` extraction. |

---

## System Overview

InsureVoice is a multi-layer AI system. Each layer is independently deployable and testable.

> **Note (2026-06-05):** Layer 1 below shows the **Day 7 baseline (live `00030-jc7`)**. The Tier B Day 8 swap (Chirp 3 HD TTS + STT v2 WebSocket + Flash-Lite classifier) is in `stable_v4` but not yet on a serving revision. See "What Changed Day 7 → Day 8" above for the Day 8 voice-stack diagram.

```
┌──────────────────────────────────────────────────────────────────────┐
│  LAYER 1 — VOICE INTERFACE (browser-native — Day 7 baseline)         │
│  Web Speech API STT · TTS WaveNet · WebRTC mic input                 │
│  Day 8 (stable_v4, pre-deploy):                                      │
│    STT  → WebSocket /stt/stream → Speech-to-Text v2 + Chirp 2        │
│    TTS  → POST /tts/stream      → Chirp 3 HD (en-IN-Chirp3-HD-Aoede) │
│    B4   → Flash-Lite classifier (separate Runner, follow-up turns)   │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ user message / session_id
┌──────────────────────────▼───────────────────────────────────────────┐
│  LAYER 2 — AGENT ORCHESTRATION                                       │
│  insure-voice-agent (Cloud Run) — FastAPI /invoke                    │
│                                                                      │
│  ┌─ PRE-LLM (deterministic Python, NO LLM call) ────────────────┐  │
│  │  S3 reset detection ("start over" / "reset" / "begin again")  │  │
│  │  P.2 intake state machine (8 fields, regex validators)        │  │
│  │  S3 follow-up dispatch (named/ordinal "tell me about X")      │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              │                                       │
│  ┌─ LLM PIPELINE (only after intake complete + no follow-up) ───┐  │
│  │  LlmAgent · Gemini 2.5 Flash Lite · ADK 2.1.0                  │  │
│  │  ADK before_model_callback (C.5: mode=ANY tool routing)        │  │
│  │  Tool 1: FunctionTool(search_products) → REST CF               │  │
│  │     ├─ S2' product_type injection (mechanical override)        │  │
│  │     └─ session-state stash for C.5b candidate substitution     │  │
│  │  Tool 2: FunctionTool(compliance_check) → CF (C.5b sub)        │  │
│  │  Tool 3: FunctionTool(rank_products) → CF (C.5b sub)           │  │
│  │  Sub-Agent: AgentTool(recommend_and_explain) → Gemini 2.5 Flash│  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              │                                       │
│  ┌─ POST-LLM (deterministic completion + snapshot) ─────────────┐  │
│  │  C.5b deterministic template (renders top3 server-side if    │  │
│  │      LLM bails or 429s)                                       │  │
│  │  Programmatic-completion fallback (re-runs pipeline w/ profile│  │
│  │      from intake if LLM didn't fire tools)                    │  │
│  │  S3 top3 snapshot → shared_state.TOP3_BY_SESSION              │  │
│  │  F.2 mojibake sanitization on outbound product fields         │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────┬──────────────────────┬──────────────────┬────────────────┘
          │                      │                  │
┌─────────▼──────┐  ┌────────────▼────────┐  ┌──────▼──────────────┐
│ search_        │  │ compliance_check    │  │ rank_products       │
│ products       │  │ (Cloud Function)    │  │ (Cloud Function)    │
│ (httpx REST,   │  │ Pure Python rules,  │  │ Suitability scoring │
│  timeout=8.0s) │  │ 100% deterministic  │  │                     │
│                │  │                     │  │                     │
│ elastic-mcp-   │  │                     │  │                     │
│ server REST    │  │                     │  │                     │
│ /search_       │  │                     │  │                     │
│ products       │  │                     │  │                     │
└─────────┬──────┘  └─────────────────────┘  └─────────────────────┘
          │
┌─────────▼─────────────────────────────────────────────────────────────┐
│  LAYER 3 — SEARCH INTELLIGENCE (Elastic Cloud Serverless)             │
│  ELSER v2 · semantic_text fields · RRF hybrid retriever               │
│  Alias: insurance_products_current (28 products, 7 product_types)     │
└───────────────────────────────────────────────────────────────────────┘
```

---

## Layer 1: Voice Interface

**Day 7 baseline (live revision `00030-jc7`):** browser-native voice frontend — Web Speech API STT (`webkitSpeechRecognition`) + Cloud TTS WaveNet (`en-IN-Wavenet-D`). The original plan included Dialogflow CX; the implemented architecture uses an in-browser STT/TTS pipeline served by the same Cloud Run service via static frontend.

**Day 8 (`stable_v4` working copy, NOT yet deployed):** swapped to a Google Cloud voice stack — see "What Changed Day 7 → Day 8" above for the full Tier B context.

### Components — Day 8 Tier B (`stable_v4`)

| Component | Technology | Role |
|---|---|---|
| Speech-to-Text | Google Cloud Speech-to-Text v2 + Chirp 2 model + native VAD 800ms via `WebSocket /stt/stream` (`stt_websocket.py` + `voice/stt-client.js` + `voice/audio-worklet-processor.js`) | en-IN. AudioWorklet 16kHz PCM mic capture. Pause-tolerant. ~92% baseline accuracy on Indian English vs ~75-80% browser. |
| Conversation rendering | Vanilla JS + ELSER ranking badges | Live transcript panel + product cards |
| Text-to-Speech | Google Cloud Text-to-Speech — Chirp 3 HD (`en-IN-Chirp3-HD-Aoede`, 24kHz MP3) via `POST /tts/stream` (`tts_streaming.py` + `voice-player.js`) | Natural human-sounding Indian English voice. PoC measured 1.57s cold start. Per-IP rate limit 30 req/min. |
| Frontend bundling | Same-origin from `agent_builder/frontend/` | Cloud Run serves both API + static assets |

### Components — Day 7 baseline (live `00030-jc7`)

| Component | Technology | Role |
|---|---|---|
| Speech-to-Text | Web Speech API (`webkitSpeechRecognition`) | Browser-native streaming transcription with 1.2s silence-debounce |
| Text-to-Speech | Cloud TTS WaveNet (`en-IN-Wavenet-D`) | Indian English voice synthesis |

### Voice Latency Targets

| Step | Target Latency | Actual (verified) | Day 8 Tier B (PoC, not yet live) |
|---|---|---|---|
| STT transcription | < 1.5s (streaming) | ~0.5s (browser-native) | Speech-to-Text v2 + Chirp 2: end-of-utterance ~0.6-1.0s expected; AC-B4.11 40-call probe deferred to Day 9 |
| Agent /invoke (intake turn) | < 200ms | ~50ms (deterministic Python) | unchanged |
| Agent /invoke (follow-up turn) | < 200ms | ~10ms (S3 deterministic, no LLM) | with B4 ON: +1 Flash-Lite call (~600-800ms p50). With flag OFF (Day 9 first deploy): unchanged. |
| Agent /invoke (pipeline turn 9) | < 8s | ~7-10s (search → compliance → rank → recommend) | unchanged |
| TTS synthesis | < 0.5s | ~0.4s (browser `SpeechSynthesisUtterance` / WaveNet) | Chirp 3 HD PoC: 1.57s cold, well under 2s target |

**Latency note:** intake turns + follow-up turns are sub-100ms because they bypass the LLM entirely. Only the pipeline-firing turn (after intake completion) hits the full 7-10s budget. This is by design — most user turns are fast. Tier B's B4 classifier (when enabled) adds one extra Flash-Lite call (~600-800ms p50) on follow-up turns; AC-B6.0 measurement gate from Locked_Decisions.md decides whether to keep B6 backchannel based on live recommendation-turn p50 against a 1500ms threshold.

---

## Layer 2: Agent Orchestration — Three-Phase Pipeline

The `/invoke` handler in `agent_builder/main.py` runs **three phases per turn**: pre-LLM (always), LLM pipeline (only after intake completion + no follow-up intent), post-LLM (only on pipeline turns).

### Phase 1: Pre-LLM (Deterministic Python)

```
def /invoke(message, session_id):
    # Phase 1a — S3 reset detection (BEFORE intake)
    if is_reset_intent(message):
        clear _INTAKE_BY_SESSION[session_id]
        clear shared_state.PROFILE_BY_SESSION[session_id]
        clear shared_state.TOP3_BY_SESSION[session_id]
        return canonical_greeting   # NO LLM call

    # Phase 1b — P.2 intake state machine
    intake_state = _INTAKE_BY_SESSION.setdefault(session_id, {})
    if not intake_state["complete"]:
        intake_result = handle_intake(intake_state, message)
        if intake_result["needs_more_data"]:
            return next_canonical_question   # NO LLM call
        # Intake just completed — mirror profile to shared_state
        shared_state.PROFILE_BY_SESSION[session_id] = profile
        # Build synthetic complete-profile message; fall through to Phase 2

    else:
        # Phase 1c — S3 follow-up dispatch (intake already complete)
        # NEW Day 8 (B4): if USE_LLM_INTENT_CLASSIFIER and top3 present, run the
        # Flash-Lite classifier sub-agent FIRST (separate Runner / app_name=
        # "insure-voice-classifier"). Branch on route_classification(...):
        #   ROUTE_NAMED / ROUTE_ORDINAL → return deterministic voice text (NO LLM)
        #   CLARIFY                     → return clarification text       (NO LLM)
        #   ESCALATE                    → return no_match_voice_text()    (NO LLM)
        #   FREE_FORM                   → _skip_regex_followup=True;
        #                                 raise LookupError("B4_FREE_FORM_BYPASS")
        #                                 → land at LLM passthrough (M2 fix)
        #   FALLBACK_LLM / exception    → fall through to regex path below
        intent = detect_followup_intent(message)
        top3 = shared_state.TOP3_BY_SESSION.get(session_id) or []
        if intent in ("named", "ordinal") and top3:
            matched, method = resolve_product(message, top3, intent)
            if matched:
                return build_voice_text(matched)   # NO LLM call
            return no_match_voice_text()           # NO LLM call
        # else: fall through to Phase 2 (LLM pipeline)
```

**Why deterministic:** Per lesson L-001, prompt rules don't reliably enforce tool-call sequences on flash-lite. Python state machines do. The intake + follow-up dispatch handle the high-frequency turns; the LLM only sees the low-frequency (1 per session) pipeline turn.

### Phase 2: LLM Pipeline (only on pipeline turns)

```python
# agent_builder/agent_definition.py (current — Day 6)
root_agent = LlmAgent(
    model="gemini-2.5-flash-lite",
    name="insure_voice",
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=0.25, top_p=0.7, max_output_tokens=800,
    ),
    before_model_callback=_route_next_tool_callback,    # C.5 — mechanical tool routing
    tools=[
        FunctionTool(search_products),   # NOTE: REST, not MCPToolset (Day 5 switch)
        FunctionTool(compliance_check),
        FunctionTool(rank_products),
        AgentTool(recommend_and_explain_agent),  # sub-agent for voice text
    ],
)
```

**Tools are now FunctionTools, not MCPToolset.** The Day 5 switch traded MCP-native protocol fidelity for simpler integration. The MCP-native server is still deployed as `elastic-mcp-server-native` for hackathon track compliance, but the agent talks to `elastic-mcp-server` REST endpoint.

**Environment variables** required by `insure-voice-agent` Cloud Run:

| Variable | Value |
|---|---|
| `ELASTIC_MCP_SERVER_URL` | `https://elastic-mcp-server-mhojvvbq4a-uc.a.run.app` (REST, hot path) |
| `ELASTIC_MCP_SERVER_NATIVE_URL` | `https://elastic-mcp-server-native-1055350728739.us-central1.run.app` (audit) |
| `COMPLIANCE_CHECK_URL` | `https://us-central1-voice-sales-agent.cloudfunctions.net/compliance_check` |
| `RANK_PRODUCTS_URL` | `https://us-central1-voice-sales-agent.cloudfunctions.net/rank_products` |
| `GOOGLE_GENAI_USE_VERTEXAI` | `TRUE` |
| `GOOGLE_CLOUD_PROJECT` | `voice-sales-agent` |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` |
| `USE_LLM_INTENT_CLASSIFIER` | (Day 8 Tier B) `false` by default. Set truthy to enable B4 classifier dispatch in `/invoke` Phase 1c. |

### Phase 3: Post-LLM (Deterministic Completion + Snapshot)

```
After LlmAgent runner completes:
    # C.5b deterministic template — if LLM bailed without text, render server-side
    if response_text == "" and rank_products.top_3 not empty:
        response_text = build_deterministic_template(rank_products.top_3, profile)

    # Programmatic-completion fallback — if LLM didn't fire tools, run pipeline directly
    if not _has_pipeline_call and intake_complete:
        run_pipeline_programmatically(profile)

    # F.2 mojibake sanitization on outbound product fields
    top3_enriched = [_sanitize_product(p) for p in top3_raw]

    # S3 top3 snapshot for next-turn follow-up dispatch
    if top3_enriched:
        shared_state.TOP3_BY_SESSION[session_id] = [dict(p) for p in top3_enriched]

    return JSONResponse({session_id, response, top3, rejected})
```

---

## State Persistence — Module-Level Dicts (Critical Architecture Decision)

ADK's `InMemorySessionService` does NOT reliably persist mutations to `session.state` across `get_session` calls in this deployment. Documented in `main.py:46-52`. To work around this, the architecture uses **module-level dicts** keyed by `session_id` for cross-turn state:

| Dict | Owner | Lifecycle | Cleared on |
|---|---|---|---|
| `_INTAKE_BY_SESSION` (in `main.py`) | P.2 intake state machine | Until intake complete OR reset | "start over" reset |
| `shared_state.PROFILE_BY_SESSION` | S2' arg injector reads this | Until reset | "start over" reset |
| `shared_state.TOP3_BY_SESSION` | S3 follow-up dispatch reads this | Until reset OR new pipeline run | "start over" reset OR overwrite on new top3 |
| `tool_context.state["last_search_candidates"]` | C.5b candidate substitution | Within a single LLM run | Auto-cleared per-invocation |
| `tool_context.state["last_compliance_passed"]` | C.5b candidate substitution | Within a single LLM run | Auto-cleared per-invocation |

**Why module-level dicts work:** `agent_builder/` is a single Python process per Cloud Run instance. With `--max-instances=1` (current hackathon config), there is exactly one process holding all state. Module-level dicts survive across `/invoke` calls because the process is long-lived.

**Production consideration (post-hackathon):** Module-level dicts won't survive horizontal scaling. For multi-instance deploys, migrate to Firestore-backed session storage. Not in scope for hackathon.

---

## Tool API Contracts

### Tool 1 — `search_products` (REST FunctionTool)

**Wrapper:** `agent_builder/agent_definition.py:55-200` (Python function registered as `FunctionTool`)
**Backend:** `POST $ELASTIC_MCP_SERVER_URL/search_products` on Cloud Run
**Timeout:** 8.0s (Day 6 fix; was 2.5s and timing out)

**Request signature:**
```python
def search_products(
    query: str, customer_age: int, is_smoker: bool, income: int,
    product_type: str = None, size: int = 5, relax_age_filter: bool = False,
    tool_context: ToolContext = None,  # ADK-injected
) -> dict
```

**S2' product_type injection (Day 6):**
Inside the wrapper, BEFORE the HTTP call:
1. Read `session_id = tool_context._invocation_context.session.id`
2. PRIMARY: read `profile = shared_state.PROFILE_BY_SESSION[session_id]`
3. FALLBACK: read `profile = tool_context.state["intake_profile"]` (defense-in-depth)
4. If `profile["coverage_goals"]` is non-empty, override `product_type = coverage_goals[0]`
5. Log `S2_INJECT session=<id> llm_passed=<repr> intake_goal=<repr> -> product_type=<repr>`

**C.5b candidate stash:** After successful response, stash `result["candidates"]` in `tool_context.state["last_search_candidates"]` for compliance_check to read.

**Response shape:**
```json
{
  "candidates": [{"product_id", "name", "product_type", "elser_score",
                  "description", "key_feature", "min_age", "max_age",
                  "smoker_eligible", "min_income", "premium_min_monthly"}],
  "total_hits": int,
  "fallback_triggered": bool
}
```

### Tool 2 — `compliance_check` (REST FunctionTool with C.5b substitution)

**Wrapper:** `agent_builder/agent_definition.py:193-260`
**Backend:** `POST $COMPLIANCE_CHECK_URL` on Cloud Function

**C.5b critical pattern (Day 5):**
```python
def compliance_check(candidates, customer_profile, tool_context):
    # IGNORE the LLM-passed `candidates` arg. flash-lite reliably passes
    # [null, null, null, null] (counts items but loses content).
    real_candidates = tool_context.state.get("last_search_candidates") or []
    payload = {"candidate_products": real_candidates,
               "customer_profile": customer_profile}
    # ... HTTP POST with real_candidates, not candidates ...
```

**Backend rules** (`functions/compliance_check/main.py`):

| Rule ID | Predicate |
|---|---|
| `AGE_MIN` | `customer.age >= product.min_age` |
| `AGE_MAX` | `customer.age <= product.max_age` |
| `SMOKER_EXCLUSION` | `not (smoker and not smoker_eligible)` |
| `INCOME_SUM_CAP` | `sum_need ≤ income × 10` |
| `MEDICAL_EXAM_REQUIRED` | `not (sum_need > medical_required_above and health_status != "healthy")` |

**Response asymmetry:** `passed[]` = full product dicts; `rejected[]` = `{product_id, product_name, reasons}` only.

### Tool 3 — `rank_products` (REST FunctionTool with C.5b substitution)

Same pattern as compliance_check. Reads `tool_context.state["last_compliance_passed"]` instead of LLM-passed `passed_products`.

**Scoring formula:**
```
suitability_score = (elser_score × 0.4) + (age_centrality × 0.3) + (income_fit × 0.3)
age_centrality = 1 - |age - product_midpoint_age| / (max_age - min_age)
income_fit     = min(income / (sum_need / 10), 1.0)
```

### Sub-Agent — `recommend_and_explain` (AgentTool, Gemini 2.5 Flash)

**Purpose:** Generate voice-friendly recommendation text (≤120 words) from top3 + customer profile.
**Prompt:** `agent_builder/sub_agent3_explainer_prompt.md` (105 lines, includes Day 6 S4 catalog facts + smoker logic guardrails)
**Configuration:** `temperature=0.3, max_output_tokens=400` (C.3 stability fix)
**Bypassed by:** C.5b deterministic template when sub-agent fails or returns empty

---

## C.5 Tool Routing Callback (Mechanical Tool-Call Enforcement)

`agent_builder/agent_definition.py:300-440` registers `_route_next_tool_callback` on the root LlmAgent. Before each LLM call, this callback inspects the recent tool-call history and **forces the next tool selection** via `tool_config={mode:ANY, allowed_function_names:[...]}`.

```python
def _route_next_tool_callback(callback_context, llm_request):
    last_fr = get_last_function_response(callback_context)

    # Pipeline state machine
    if last_fr is None:
        # No prior tool call — allow search_products only
        forced = ["search_products"]
    elif last_fr.name == "search_products":
        # search done — force compliance_check next
        forced = ["compliance_check"]
    elif last_fr.name == "compliance_check":
        # compliance done — force rank_products next
        forced = ["rank_products"]
    elif last_fr.name == "rank_products":
        # rank done — force recommend_and_explain next (or final response)
        forced = ["recommend_and_explain"]
    # ... etc.

    llm_request.tool_config = ToolConfig(
        function_calling_config=FunctionCallingConfig(mode=ANY, allowed_function_names=forced)
    )
```

**Why mechanical:** flash-lite ignored prompt rules ("MUST call X next") at ~94% rate. Mechanical routing via `tool_config.mode=ANY` is 100% reliable. AC-3 went from 0/15 PASS to 10/10 PASS once this landed.

---

## Layer 3: Search Intelligence

### Elasticsearch Index Schema (`ingest/create_index.py`)

**Infrastructure:** Elastic Cloud Serverless — built-in EIS. No manual inference endpoint needed.
**Index:** `insurance_products_v1` → **Alias:** `insurance_products_current`
**Catalog size:** 28 products across 7 `product_type` values (`term_life`, `health`, `critical_illness`, `endowment`, `ulip`, `child_plan`, `pension`).

```json
{
  "mappings": {
    "properties": {
      "id":                    { "type": "keyword" },
      "name":                  { "type": "text", "fields": { "keyword": { "type": "keyword" } } },
      "product_type":          { "type": "keyword" },
      "description":           { "type": "semantic_text" },
      "key_feature":           { "type": "semantic_text" },
      "min_age":               { "type": "integer" },
      "max_age":               { "type": "integer" },
      "smoker_eligible":       { "type": "boolean" },
      "is_active":             { "type": "boolean" },
      "min_income":            { "type": "long" },
      "max_sum_assured":       { "type": "long" },
      "medical_required_above":{ "type": "long" },
      "tags":                  { "type": "keyword" },
      "sales_pitch":           { "type": "text" },
      "premium_min_monthly":   { "type": "integer" },
      "premium_max_monthly":   { "type": "integer" }
    }
  }
}
```

### RRF Hybrid Query

Two retrieval legs, fused via Reciprocal Rank Fusion:

```
Leg A (semantic):   semantic on description + semantic on key_feature  (ELSER v2)
Leg B (BM25):       multi_match on name^2, tags, sales_pitch
Hard filters:       is_active, min_income ≤ income, age bounds, smoker_eligible, product_type (when passed)
RRF params:         rank_window_size=20, rank_constant=60
```

**Why ELSER:** "comprehensive illness protection for my family" does NOT keyword-match "Critical Illness Rider". ELSER sparse vectors encode the semantic association. BM25 returns zero; ELSER returns the correct product. **The hard filter on `product_type` is what S2' protects** — server-side filtering only fires if the wrapper passes `product_type` in the request. S2' guarantees that.

### ELSER Inference Endpoint

```json
PUT _inference/sparse_embedding/elser-v2-endpoint
{
  "service": "elasticsearch",
  "service_settings": {
    "adaptive_allocations": { "enabled": true,
                              "min_number_of_allocations": 1,
                              "max_number_of_allocations": 4 },
    "num_threads": 1,
    "model_id": ".elser_model_2"
  }
}
```

### Two MCP Server Deployments — Why Both Exist

| Service | Status | Why kept |
|---|---|---|
| `elastic-mcp-server` (REST + broken MCP) | **Hot path** | Stable REST endpoint that the agent calls via `httpx.post`. Used by `FunctionTool(search_products)` since the Day 5 switch. |
| `elastic-mcp-server-native` (FastMCP/Starlette) | Audit/demo | Deployed for hackathon Elastic Partner Track requirement (proves real MCP integration). Not on the hot path. |

**The original FastMCP double-nesting bug** (route `/mcp/mcp` instead of `/mcp` when mounting FastMCP under FastAPI) was solved by `elastic-mcp-server-native` using FastMCP as the root ASGI app. That fix is preserved in the codebase and the service is still deployed.

---

## Data Flow — Production Demo Arc (verified 2026-06-03)

The canonical demo arc is 12 turns. Each turn's behavior is now deterministic except turn 9 (the pipeline turn) and any compare-intent follow-up.

```
Turn 1-8 (intake collection, deterministic Python):
    User → /invoke {"message": "<turn>", "session_id": <persisted>}
    └─→ S3 reset detection: no match → continue
    └─→ P.2 intake state machine: validates field, saves to _INTAKE_BY_SESSION
    └─→ Returns next canonical question (NO LLM call, ~10ms)

Turn 9 (pipeline turn — intake just completed, "1 crore"):
    └─→ P.2 intake completes → mirror profile to PROFILE_BY_SESSION
    └─→ Build synthetic complete-profile message
    └─→ LLM dispatch (Vertex AI Gemini 2.5 Flash Lite):
        ├─→ tool_call: search_products(query, age, smoker, income, [LLM may omit product_type])
        │   ├─→ S2' wrapper: inject product_type from PROFILE_BY_SESSION
        │   ├─→ httpx.post timeout=8.0s → elastic-mcp-server /search_products
        │   ├─→ ELSER RRF returns N candidates with hard product_type filter
        │   └─→ stash candidates in tool_context.state["last_search_candidates"]
        ├─→ tool_call: compliance_check(candidates=[null,...], profile)
        │   ├─→ C.5b wrapper: ignore LLM args, read real candidates from session state
        │   ├─→ POST to compliance_check CF
        │   └─→ stash passed in tool_context.state["last_compliance_passed"]
        ├─→ tool_call: rank_products(...)
        │   └─→ Same C.5b pattern, returns top3
        └─→ tool_call: recommend_and_explain(top3, profile)
            └─→ Sub-agent generates voice text (Gemini 2.5 Flash, temp=0.3)
    └─→ Post-LLM:
        ├─→ C.5b deterministic template (only fires if LLM bailed)
        ├─→ F.2 mojibake sanitization on top3 product fields
        └─→ S3 snapshot: TOP3_BY_SESSION[session_id] = [dict(p) for p in top3_enriched]
    └─→ Returns JSON {session_id, response, top3, rejected}
    Total wall-clock: ~7-10s

Turn 10 (named follow-up "tell me about LifeGuard Plus"):
    └─→ S3 follow-up dispatch: detect_followup_intent → "named"
    └─→ match_product_by_name(message, TOP3_BY_SESSION[session_id]) → product
    └─→ build_voice_text(product) → "Here's more on LifeGuard Plus Term..."
    └─→ Returns JSON {session_id, response} (NO LLM call, ~10ms)

Turn 11 (ordinal follow-up "second one"):
    └─→ S3 follow-up dispatch: detect_followup_intent → "ordinal"
    └─→ resolve_ordinal_index(message) → 1 → top3[1]
    └─→ build_voice_text → "Here's more on <top3[1].name>..."
    └─→ Returns JSON (NO LLM call, ~10ms)

Turn 12 (reset "start over"):
    └─→ S3 reset detection (Phase 1a): is_reset_intent → True
    └─→ Clear _INTAKE_BY_SESSION, PROFILE_BY_SESSION, TOP3_BY_SESSION
    └─→ Return reset_voice_text() → "No problem — let's start fresh. May I have your name please?"
    └─→ Returns JSON (NO LLM call, ~10ms)
```

**Verified end-to-end (S5 v2 live test, 2026-06-03 17:40 IST):** 12-turn arc against real Vertex AI Gemini + real Cloud Functions + real ELSER. All log signals fire correctly. Turn 9 returns 3 products. Turns 10, 11, 12 are sub-100ms with zero LLM calls.

---

## Logging Surface (Cloud Run grep targets)

All log lines are INFO/WARNING/ERROR on Python's root logger, captured by Cloud Logging.

| Log key | Meaning | Where emitted |
|---|---|---|
| `INTAKE_COMPLETE session=<id> synthetic=<msg>` | P.2 intake just completed | `main.py` after `intake_state["complete"] = True` |
| `S2_INJECT session=<id> llm_passed=<repr> intake_goal=<repr> -> product_type=<repr>` | S2' arg injection fired | `agent_definition.py` `search_products` wrapper |
| `S2_INJECT_MULTIGOAL session=<id> goals=<list> picked=<value>` | User had multiple coverage_goals; S2' picked first | Same wrapper |
| `S2_INJECT_SESSION_ID_MISS` | Couldn't get session_id from tool_context | Same wrapper |
| `S2_PROFILE_MIRROR_FAILED session=<id>` | Mirror to PROFILE_BY_SESSION raised | `main.py` intake-completion block |
| `SEARCH_PAYLOAD query=<...> product_type=<value>` | What was actually sent to ES | `agent_definition.py` search_products wrapper |
| `S3_RESET session=<id> pattern=<msg>` | Reset detected; all state cleared | `main.py` Insertion A |
| `S3_FOLLOWUP_HIT session=<id> intent=<named\|ordinal> method=<substring\|fuzzy\|ordinal> product=<name> index=<i>` | Follow-up dispatched deterministically | `main.py` Insertion B |
| `S3_VOICE session=<id> len=<N>` | Deterministic voice text emitted | Same |
| `S3_FOLLOWUP_MISS session=<id> reason=<no_product_match\|compare_parked\|no_top3_in_state>` | Graceful fall-through | Same |
| `S3_TOP3_SNAPSHOT session=<id> n=<count>` | Top3 captured for next turn | `main.py` Insertion C (post-pipeline) |
| `AGENT_EVENT session=<id> final=<bool> parts=<list>` | C.2 LLM event tracing | LlmAgent run loop |
| `CALLBACK_DEBUG last_fr=<tool> n_events=<N> forced=<tool>` | C.5 mechanical routing decision | `_route_next_tool_callback` (alias of `_force_tool_call_mid_pipeline`, `agent_definition.py:428`) |
| `B4_DISPATCH session=<id> action=<ROUTE_NAMED\|ROUTE_ORDINAL\|CLARIFY\|ESCALATE\|FREE_FORM\|FALLBACK_LLM> tid=<product_id>` | NEW Day 8 — B4 classifier branch decision | `main.py` Phase 1c (after `await classify_intent_async`) |
| `B4_DISPATCH_FAILED session=<id>` | NEW Day 8 — classifier raised; falling through to regex path | Same |

**Demo monitoring during live test:** grep for `S3_FOLLOWUP_HIT`, `S3_VOICE`, `S2_INJECT`, `INTAKE_COMPLETE`, `S3_RESET`. ZERO `AGENT_EVENT` after a follow-up turn proves the LLM bypass is working.

---

## File Layout (`agent_builder/`)

```
agent_builder/
├── main.py                          # FastAPI /invoke + /tts/stream + WebSocket /stt/stream + 3-phase pipeline orchestrator
├── agent_definition.py              # LlmAgent + tools + C.5 callback + S2' inject
├── intake.py                        # P.2 8-field state machine + validators (Day 5)
├── shared_state.py                  # Day 6 — PROFILE_BY_SESSION, TOP3_BY_SESSION; Day 8 — LAST_RENDERED_BY_SESSION (Bug L)
├── followup.py                      # Day 6 — S3 intent detector + voice generator. Day 8 — D13 CANONICAL_FAREWELL_TEXT constant.
├── tts_streaming.py                 # NEW Day 8 (B1) — Chirp 3 HD streaming TTS. POST /tts/stream.
├── stt_websocket.py                 # NEW Day 8 (B2) — Speech-to-Text v2 WebSocket handler. WebSocket /stt/stream.
├── intent_classifier.py             # NEW Day 8 (B4) — Flash-Lite classifier sub-agent (separate Runner, app_name="insure-voice-classifier").
├── root_agent_prompt.md             # Root LlmAgent system prompt
├── sub_agent3_explainer_prompt.md   # recommend_and_explain prompt + S4 guardrails
├── sub_agent1_search_prompt.md      # legacy (retained for reference)
├── tools.yaml                       # Agent Builder tool registration (legacy artifact)
├── requirements.txt                 # ADK 2.1.0, fastapi, httpx; Day 8 adds google-cloud-texttospeech, google-cloud-speech, google-genai==1.75.0
├── Dockerfile                       # Cloud Run container build
└── frontend/                        # Static voice UI (HTML/JS/CSS, served by Cloud Run)
    ├── index.html                   # Day 8 — adds <script> tags for voice-player.js + voice/stt-client.js
    ├── simulation.js                # Day 8 — UI hooks for voice-player + stt-client
    ├── voice-player.js              # NEW Day 8 (B1 FE) — <audio> playback + D8 lock/resume hooks (M1 fix: read-only on window.__voiceAudioCtx)
    └── voice/
        ├── stt-client.js            # NEW Day 8 (B2 FE) — WebSocket STT client. Publishes window.__voiceAudioCtx + window.__voiceMicSuspended on init.
        └── audio-worklet-processor.js  # NEW Day 8 (B2 FE) — 16kHz PCM mic capture worklet.
```

---

## Phase 2: Workflow Integration (Post-Hackathon)

When a customer accepts a recommendation:

```
accept_recommendation event
    │
    ▼ Pub/Sub: topic = insurance.recommendation.accepted
    Message: { session_id, customer_profile, recommended_product_id,
               timestamp, agent_id, recommendation_score }
    │
    ▼ Workflow Engine (Camunda BPMN / Cloud Workflows)
    Process: Begin Insurance Application Workflow
      ├── KYC verification
      ├── Document collection
      ├── Underwriting trigger
      └── Proposal generation
```

---

## Production Hardening Roadmap (Post-Hackathon)

| Concern | Current state | Production-grade fix |
|---|---|---|
| Module-level dict state | Works at `--max-instances=1` | Migrate to Firestore-backed sessions for horizontal scaling |
| Cloud Function auth | Public (allow-unauthenticated) | Require IAM auth on `compliance_check` and `rank_products` |
| Voice data privacy | Day 7: STT in browser, nothing persisted. Day 8 (`stable_v4`): STT audio streamed to Google Cloud Speech-to-Text v2; TTS text sent to Cloud Text-to-Speech. Cloud audit logging applies. | Confirm Speech/TTS APIs aren't retaining payloads (set `enableSpokenPunctuation=False`, opt out of data logging at project level); add explicit GDPR opt-in; review Cloud audit log retention. |
| Elasticsearch access | API key (read+write) | Scope a read-only API key for the agent; rotate quarterly |
| Open-source IP exposure | Synthetic catalog only | Verify no real product pricing or customer data before any release |
| Catalog size | 28 products, 7 types | Day 7 backlog: expand to ~48 products with disease-specific descriptions |
| ELSER cost | Auto-scaling EIS | Monitor cost per query; add cache layer for hot queries |
| Demo flow robustness | Validated 12-turn arc | Add multi-product compare-intent (S3 currently parks compare to LLM) |

---

## References

- **Stability changelog (canonical record):** `STABILITY_CHANGELOG.md` (root of stable_v2)
- **Day 6 task folder:** `tasks/2026-06-03_hackathon_day6_atul_followup/`
  - SPEC files in `reports/` (S2_ArgInjector, S3_C2_FollowUp, S4_PromptEdits)
  - Validation gates in `scripts/` (s2/s3/s5 validation arcs)
  - Verbatim transcripts and logs in `data/`
- **Day 7 task folder:** `tasks/2026-06-04_hackathon_day7_polish_bugs/`
  - `p3_tier_b_voice_stack/reports/` — Tier B Implementation Plan + 6 SPEC v2 docs (B1/B2/B4/B5/B6/B7) + 6 Reviewer Verdicts + Locked_Decisions.md (D1-D14)
- **Day 8 task folder (Tier B implementation, 2026-06-05):** `tasks/2026-06-05_hackathon_day8_tier_b_implementation/`
  - `reports/Tier_B_Plain_English_Walkthrough.md` — master plain-English summary of the Day 8 voice-stack swap
  - `reports/D10_Runner_Spike_Result.md` — separate-Runner / separate-`app_name` architecture verification
  - `reports/B1_Implementation_Report.md` · `reports/B2_Implementation_Report.md` · `reports/B4_Implementation_Report.md`
  - `reports/Reviewer_Pass_Report.md` — M1 + M2 fixes
  - `reports/Push_Plan.md` — Day 9 deploy preconditions
- **Demo script:** `docs/DEMO-SCRIPT.md`
- **Hackathon plan (historical):** `docs/HACKATHON-PLAN.md`
- **CEO pitch (historical):** `docs/CEO-PITCH-AND-BUDGET.md`

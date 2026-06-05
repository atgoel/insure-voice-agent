# InsureVoice — Technical Analysis & Strategic Improvement Plan
## Making InsureVoice "Hackathon-Ready" with Natural Conversational UX and Ultra-Low Latency

> **Document Status**: Strategic Proposal & Architectural Blueprint
> **Prepared for**: Financial Services Track — Elastic Partner Track

---

## Status — DELIVERED (2026-06-05 Day 8)

This plan was the input to the Day 7 → Day 8 Tier B Spec-Kit cycle. Path B was chosen and executed. The key sub-tasks, their final status, and links to the implementation reports are below. The body of the plan (sections 1-6) is preserved below as historical record — read it as the *intent*, not the *current state*.

| Sub-task | Status | Implementation report (`tasks/2026-06-05_hackathon_day8_tier_b_implementation/reports/`) |
|---|---|---|
| **B1 — Chirp 3 HD streaming TTS** (Path B §4.1 — server-side TTS upgrade) | **DELIVERED** — `agent_builder/tts_streaming.py` (396 lines), voice `en-IN-Chirp3-HD-Aoede`, 24kHz MP3, `POST /tts/stream` with per-IP rate limit (30 req/min). PoC 1.57s cold start. | `B1_*.md` |
| **B2 — Speech-to-Text v2 streaming** (Path B §4.2 *replaces* Silero VAD) | **DELIVERED** — `agent_builder/stt_websocket.py` (549 lines) + `voice/stt-client.js` + `voice/audio-worklet-processor.js`. Server-side native VAD 800ms (replaces the planned client-side Silero VAD). `WebSocket /stt/stream`. AudioWorklet 16kHz PCM mic capture. Pause-tolerant. | `B2_*.md` |
| **B3 — Silero VAD** (Path B §4.2 as originally written) | **DROPPED — D1 lock 2026-06-05.** Hackathon rule "all other AI tools not permitted" — Silero is a neural net at inference time, plausibly disqualifying. The 150-300ms barge-in win was not worth the disqualification risk. Server-side VAD from STT v2 (B2's native `voice_activity_events` at 800ms) is the only VAD layer. |
| **B4 — Flash-Lite intent classifier** (NEW — not in original plan; added during Day 7 spec cycle) | **DELIVERED** — `agent_builder/intent_classifier.py` (565 lines). Separate `LlmAgent` sub-agent under `app_name="insure-voice-classifier"` (D10) with own `before_model_callback=_force_classifier_tool`. Categories: NAMED_PRODUCT / ORDINAL / POLICY_QUESTION / AMBIGUOUS. Confidence threshold 0.7, force-clarify band (0.5, 0.7). Feature-flagged via `USE_LLM_INTENT_CLASSIFIER`. | `B4_*.md` |
| FE wiring (Path B §4.3 — echo-cancelled barge-in / D8 contract) | **DELIVERED** — `voice-player.js` (428 lines). D8 contract: B2 publishes `window.__voiceAudioCtx` + `window.__voiceMicSuspended`; B1 reads only and toggles `.suspend()` / `.resume()` around `<audio>` playback. 200ms echo-tail before resume. | `FE_Merge_*.md` |
| Path B §4.4 — Acoustic fillers + cold-start tuning (B6 backchannel) | **DEFERRED — Day 9+.** AC-B6.0 measurement gate: live `/invoke` p50 measured per turn type before threshold lock. Drop B6 if backchannel would fire on >50% of turns. |
| B5 — Tool-result-only render (refactor) | **DEFERRED — Day 9+.** Predecessor on G5 + B4 + v4 baseline capture. |
| B7 — ADK eval smoke harness | **DEFERRED — Day 9+.** Predecessor (D13 — `CANONICAL_FAREWELL_TEXT` constant) **landed today** in `followup.py:393`; back-compat function shim retained. 5-case smoke only per D2. Local-run-only per D14 (CI half deferred). |
| Path A — Gemini Multimodal Live API | **NOT TAKEN.** Path B chosen for execution risk; Path A would have required WebRTC proxy + `@google/genai` migration in 6 days. |

### Reviewer fixes applied today (2026-06-05)

* **M1** — `voice-player.js` `_ensureAudioCtx` (which was inverting the D8 contract by creating + publishing `window.__voiceAudioCtx` from the player side) was removed and replaced with a null-safe `_readAudioCtx`. B2 now is the sole publisher; B1 is read-only.
* **M2** — `agent_builder/main.py` adds a `_skip_regex_followup` flag for the FREE_FORM intent path so it raises `LookupError("B4_FREE_FORM_BYPASS")` and lands at the LLM passthrough instead of being short-circuited by the regex follow-up dispatch.

### Test suite (Day 8 baseline)

**567 passed / 29 skipped / 0 failed** on `stable_v4` (~28.86s). Up from the 551-pass Phase-1-only baseline by +12 (`tests/test_intent_classifier.py`) + 4 (`tests/test_b2_resume_tail.py`). Golden fixture: `tests/fixtures/bug_j_golden.json` — 15 hand-authored cases against the 28-product catalog.

### Live deploy status

Live revision `00030-jc7` (Day 7 baseline) is unchanged. Tier B is in-tree on `stable_v4` but **not yet deployed**. Day 9 plan: Cloud Build to a new Cloud Run revision with `--no-traffic`, browser smoke test (uvicorn + manual mic), AC-B4.11 40-call live latency probe, then promote 0% → 100% only after smoke + latency PASS.

For the full decision contract see `tasks/2026-06-04_hackathon_day7_polish_bugs/p3_tier_b_voice_stack/reports/Locked_Decisions.md` (D1-D14).

---

## 1. Executive Summary: Why InsureVoice is Not Yet "Hackathon Ready"

While the underlying architecture of InsureVoice (Google Cloud ADK, Elasticsearch ELSER v2, and deterministic compliance Cloud Functions) is technically sound and robust, the **User Experience (UX) fails to deliver the "Wow" factor** required to win a highly competitive hackathon. 

Currently, the voice agent feels like a **glorified, rigid IVR (Interactive Voice Response) system** rather than a human-like conversational advisor. The four critical failure modes impacting the UX are:
1. **Robotic Voice Quality**: Relying on browser-native SpeechSynthesis (or traditional static TTS) yields a monotone, artificial voice lacking the natural prosody, emphasis, and rhythm of a professional sales advisor.
2. **Active Listening Feedback & Stutters**: The system actively listens using a naive, hardcoded 1.2s silence timer. If a user pauses mid-thought to think, the agent prematurely cuts them off, posts a half-sentence to the LLM, and interrupts the user with an irrelevant or broken response.
3. **No Barge-In (Interruption)**: While the agent is speaking, the microphone is shut off. If the user wants to correct a mistake, change a parameter, or stop the agent mid-sentence, they are forced to wait silently until the agent finishes its entire script.
4. **The Turn-9 Latency Gap (7–10s Silence)**: When the intake is complete and the full search-compliance-ranking-explain pipeline fires, there is a massive 7–10 second delay. To the user, the app feels dead or crashed.

To capture the top prize, we must transition the voice agent to feel **fluid, active, and alive**. Below are the two strategic paths proposed to upgrade InsureVoice.

---

## 2. Root-Cause Performance Analysis

```
Current User Voice Turn-Loop:
User Speaks ──► [1.2s Silence Timer] ──► Browser STT ──► Server /invoke (7-10s Sequential Cloud Runs) ──► Robotic TTS Synthesis ──► Speaker
   ▲                                                                                                                                │
   └────────────────────────────────────────── Muted During Playback (No Interruption) ─────────────────────────────────────────────┘
```

### 2.1. The Voice Quality Bottleneck
In `agent_builder/frontend/simulation.js`, the voice playback is tied to:
```javascript
const PreferredVoice = voices.find(v => v.lang.toLowerCase() === 'en-in' && v.name.toLowerCase().includes('female'));
```
This binds synthesis to the browser's **local, client-side SpeechSynthesis engine**. Depending on the user's OS and browser (Chrome, Edge, Safari), this voice ranges from barely acceptable to jarringly robotic. It lacks natural breathing, micro-pauses, and correct stress patterns on Indian insurance terms (e.g., "Crore", "Lakhs", "IRDAI", "ULIP").

### 2.2. The Active Listening / "Stutter" Problem
The turn-end detection in `simulation.js` uses a static `setTimeout` debounce of `1200ms` on the `onresult` handler:
```javascript
this.silenceTimer = setTimeout(() => {
    const speechOutput = (this.accumulatedTranscript + interimTranscript).trim();
    if (speechOutput.length >= 1) {
        this.stopListening();
        this.processInputText(speechOutput);
    }
}, 1200);
```
This is a fragile, heuristic-based Voice Activity Detection (VAD). It fails because:
* **Mid-sentence Hesitations**: When humans state complex numbers (e.g., "I earn... *[1.5s pause]*... about twelve lakhs per year"), the system cuts them off at "I earn", queries the server, and says "I didn't catch that."
* **No Acoustic Echo Cancellation (AEC)**: If the microphone is left open during speech, the agent's own voice leaks into the microphone, gets transcribed by browser STT, and is posted as a new query—triggering endless feedback stutters.

### 2.3. The 8-Second Turn-9 Latency Trap
When the user finishes the intake, the agent runs a sequential, blocking pipeline:
1. `search_products` (HTTP call to Cloud Run -> Elasticsearch ELSER search) -> **3–5 seconds**
2. `compliance_check` (HTTP call to Cloud Function, Python environment boot/cold-start) -> **1.5–2 seconds**
3. `rank_products` (HTTP call to Cloud Function) -> **1 second**
4. `recommend_and_explain` (Vertex AI LLM prompt generation) -> **2–3 seconds**

This sequential design means the user experiences **8 seconds of dead, visual-only silence** before any audio feedback is played.

---

## 3. PATH A: The Quantum Leap — Gemini Multimodal Live API (Recommended)

The most advanced, modern, and "Hackathon-Winning" solution is to bypass the traditional sequential pipeline entirely and implement the **Gemini Multimodal Live API** (powered by `gemini-2.0-flash` or `gemini-2.5-flash` Live endpoints). 

Instead of treating voice as text-to-text with speech wrappers, the Live API operates over a **real-time, bidirectional WebSocket connection (WSS) using raw audio streams (audio-to-audio native modality)**.

```
Gemini Live API Architecture:
                              ┌──────────────────────────────────────────────┐
                              │            Secure Node/Python Proxy          │
User Mic (16kHz PCM) ───────► │  Establishes session, manages Vertex auth,   │ ──────► WSS Connection
                              │  forwards raw binary audio chunks            │ ◄─────► Vertex AI Gemini Live API
Speaker (16kHz Audio) ◄────── │                                              │ ◄─────  (Aoede / Puck Live Voices)
                              └──────────────────────────────────────────────┘
                                                                 ▲
                                                                 │ Function Tool-Call Event (JSON)
                                                       [Elasticsearch & GCP Cloud Functions]
```

### 3.1. How Path A Transforms the User Experience
1. **Studio-Quality, Ultra-Realistic Voices**: Native access to Gemini's expressiveness (voices like **Aoede**, **Puck**, **Charon**, **Kore**, **Fenrir**). These models synthesize voice with organic breath sounds, natural pauses, polite verbal fillers ("uh-huh", "sure"), and responsive emotional tone.
2. **Sub-1 Second Latency**: Since the audio is processed directly by the neural network without intermediate transcribing (STT) and synthesizing (TTS) steps, the response time drops to **500–800ms**.
3. **Model-Level Barge-In (Native Interruption)**: Gemini handles interruptions natively. The user can talk over the agent. When the Gemini Live server detects incoming audio frames while it is streaming output, it **instantly stops streaming audio**, adapts its context, and says "Oh, sorry! Let me change that for you."
4. **Dynamic Tool Calling**: 
   When Gemini completes the conversational intake, it triggers a native `ToolCall` event over the WebSocket:
   * The client or server proxy interceptor captures the event.
   * It executes the Elastic ELSER search, compliance checks, and ranking in the background.
   * It sends back the structured product data as a `ToolResponse`.
   * Gemini digests the JSON and explains the top plans immediately in natural speech.

### 3.2. Implementation Architecture for Path A
To deploy this securely and quickly:
* **The Client**: Use `@google/genai` or standard WebSockets in the browser. Use the Web Audio API (`AudioWorklet`) to capture microphone inputs as 16kHz PCM chunks and play back incoming audio packets.
* **The Proxy (Security)**: Set up a lightweight WebSocket relay on Cloud Run. This proxy handles Google Cloud service account authentication and obtains ephemeral access tokens so API keys are never exposed in the browser frontend.
* **Partners Integration Option**: To bypass writing custom AudioWorklets, leverage **LiveKit** or **Daily.co** integrations. Both provide ready-to-use WebRTC wrappers specifically tuned for the Gemini Live API.

---

## 4. PATH B: The Hardened Sequential Evolution (Optimized Upgrade)

If shifting to WebSockets/WebRTC is considered too high-risk for the immediate submission schedule, we can execute a **highly targeted, surgical upgrade of the existing sequential architecture** (FastAPI `/invoke` + React/Vanilla JS). 

This path keeps the stable, deterministic Python state machines (`intake.py` and `followup.py`) but completely overhauls the audio loop, VAD, and perceived latency.

### 4.1. Step 1: Upgrade to Google Cloud TTS Chirp HD (formerly Journey) / Neural2

**STATUS:** **DELIVERED 2026-06-05 (B1).** Server-side TTS landed in `agent_builder/tts_streaming.py` (396 lines) — voice `en-IN-Chirp3-HD-Aoede`, 24kHz MP3, `synthesize_bytes` (one-shot) + `synthesize_chunks` (streaming generator) helpers, `POST /tts/stream` endpoint with per-IP rate limit (30 req/min). PoC 1.57s cold start. Report: `tasks/2026-06-05_hackathon_day8_tier_b_implementation/reports/B1_Implementation_Report.md`.

To eliminate the robotic browser voice, move the synthesis logic to the server side using premium generative models.

```
Server-Side TTS Integration:
FastAPI `/invoke` ──► Run Multi-Agent ──► Get Explanation Text ──► Call Google Cloud TTS API (en-IN-Neural2-A) ──► Return Audio Base64 ──► FE plays Audio
```

* **Action**: Configure the backend to call the Google Cloud Text-to-Speech API using the `en-IN-Neural2-A` (Female) or `en-IN-Wavenet-D` voice, or utilize the newly released global **Chirp HD** (formerly Journey) models.
* **Latency Optimization**: Instead of waiting for the full synthesis to complete before sending the response, the backend should chunk the text by sentences and stream the generated audio bytes back to the frontend. The browser can start playing Sentence 1 while Sentence 2 is still being synthesized.

### 4.2. Step 2: Implement Browser-Native Neural VAD (Silero VAD)

**STATUS:** **REPLACED — Silero VAD DROPPED 2026-06-05 (B3, D1 hackathon rule). Saved ~3h.** Hackathon rule "all other AI tools not permitted" — Silero is a neural net at inference time, plausibly disqualifying. The 150-300ms barge-in win was not worth the disqualification risk.

**Replacement DELIVERED — STT v2 server-side VAD (B2).** `agent_builder/stt_websocket.py` (549 lines, `stt_stream_handler`) + `voice/stt-client.js` + `voice/audio-worklet-processor.js`. Server-side native `voice_activity_events` at 800ms (replaces planned client-side Silero). `WebSocket /stt/stream` route. AudioWorklet 16kHz PCM mic capture. Pause-tolerant. Report: `tasks/2026-06-05_hackathon_day8_tier_b_implementation/reports/B2_Implementation_Report.md`.

Replace the fragile `1200ms` silence timer with **Silero VAD** running in the browser via **ONNX Runtime Web**.

* **Why it works**: Silero VAD is a state-of-the-art, ultra-lightweight neural network that runs locally in browser-side JavaScript using WASM. It distinguishes human speech from background noise, keyboard clicks, breathing, and momentary hesitations.
* **Action**: Integrate `@ricky0123/vad-web` via CDN:
  ```javascript
  const myvad = await vad.MicVAD.new({
    onSpeechStart: () => {
      // User started talking! If agent is speaking, stop playback instantly.
      if (window.isPlayingVoice) {
        window.voiceEngine.interruptAgent();
      }
    },
    onSpeechEnd: (audio) => {
      // Precise turn-end detected. Send to backend immediately!
      const speechText = getAccumulatedTranscript();
      window.voiceEngine.processInputText(speechText);
    }
  });
  ```

### 4.3. Step 3: Implement Echo-Cancelled Client Barge-In & Server `/interrupt`

**STATUS:** **DELIVERED 2026-06-05 (FE wiring).** `voice-player.js` (428 lines). D8 single-publisher contract: B2 publishes `window.__voiceAudioCtx` + `window.__voiceMicSuspended`; B1 reads only and toggles `.suspend()` / `.resume()` around `<audio>` playback. 200ms echo-tail before resume. `/interrupt` endpoint not implemented this round — current barge-in handled at the audio-context level. Report: `tasks/2026-06-05_hackathon_day8_tier_b_implementation/reports/FE_Merge_Report.md`.

To enable true natural conversational interruptions:
1. **Keep Mic Active**: Ensure the microphone stream remains open during the agent's playback state (`window.voiceState === 'SPEAKING'`).
2. **Acoustic Echo Cancellation**: Instantiate the microphone stream with explicit echo-cancellation flags:
   ```javascript
   navigator.mediaDevices.getUserMedia({ 
     audio: { echoCancellation: true, noiseSuppression: true } 
   });
   ```
3. **Interrupt Event**: If the VAD fires `onSpeechStart` while the agent is speaking:
   * Call `window.speechSynthesis.cancel()` (or pause the active HTML5 `<audio>` element playing the server TTS).
   * Transition the UI orb immediately to Purple `LISTENING`.
   * Send a lightweight `POST /interrupt` to Cloud Run. The server immediately aborts the active LLM execution to save GCP quota and compute.

### 4.4. Step 4: Mask Turn-9 Latency with "Acoustic Fillers" & Cold Start Tuning

**STATUS:** **DEFERRED to Day 9+ (B6 backchannel).** Predecessors landed today (B1 voice lock); B6 spec is ready. Scope intentionally narrowed to intake + followup turns ONLY per G4 baseline measurement gate. AC-B6.0: live `/invoke` p50 measured per turn type before threshold lock. Drop B6 if backchannel would fire on >50% of turns.

An 8-second wait can be made to feel instant through conversational design:
* **Generative Acoustic Fillers**: When the frontend detects the intake is complete and POSTs to `/invoke` for Turn 9, the client should **instantly** play a pre-cached, highly natural audio buffer.
  * *Option 1*: A verbal filler from the agent: *"Excellent, Rahul. Let me search our Elastic catalog and run those profiles against our compliance rules. Just a second..."*
  * *Option 2*: A soft, natural keyboard typing sound or a gentle hum.
  * This masks the 8-second processing delay. Instead of "dead silence", the user feels the agent is actively working on their behalf.
* **Eliminate Cloud Function Cold Starts**: The Cloud Functions (`compliance_check`, `rank_products`) are taking up to 2 seconds due to cold starts.
  * *Fix*: Configure a minimum instance count of `min-instances = 1` on Google Cloud Functions in your GCP configuration. This ensures warm, containerized execution, reducing latency by **~3.5 seconds**.

---

## 5. Summary Evaluation Matrix

| Feature / Criteria | Current Architecture | PATH A: Gemini Multimodal Live | PATH B: Hardened Sequential |
|---|---|---|---|
| **Voice Quality** | ❌ **Robotic (1/10)**<br>Local browser SpeechSynthesis | 🌟 **Premium (10/10)**<br>Expressive, breathing, natural intonation |  **Excellent (8/10)**<br>Cloud-synthesized Neural2/Chirp |
| **Response Latency** | ❌ **High (2/10)**<br>7-10 seconds on pipeline turns | 🌟 **Real-time (9.5/10)**<br>Sub-1 second end-to-end |  **Good (7.5/10)**<br>3-4 seconds (with fillers/streaming) |
| **Interruption (Barge-in)** | ❌ **No (0/10)**<br>Mic closed during agent playback | 🌟 **Native (10/10)**<br>Model-level adaptive stopping |  **Solid (8/10)**<br>VAD-triggered client cancellation |
| **VAD Accuracy** | ❌ **Low (3/10)**<br>Hardcoded 1.2s timeout stutters | 🌟 **Native (9/10)**<br>Native multi-modal turn detection |  **Very Good (8.5/10)**<br>Silero VAD ONNX Web |
| **Dev Effort** | — |  **Medium-High**<br>Requires WebSockets + AudioWorklets | 🚀 **Low-Medium**<br>Incremental edits to `main.py`/`simulation.js` |
| **Implementation Risk** | — | **Medium**<br>WebRTC networking complexity | 🚀 **Ultra-Low**<br>Guaranteed fallback paths survive |
| **Hackathon Grand-Prize Wow**| ❌ **Low (4/10)** | 🌟 **Maximum (10/10)** |  **Very Strong (8/10)** |

---

## 6. Recommended Action Plan

To balance **impact** and **execution risk**, we recommend a structured execution model divided into three progressive phases:

### Phase 1: High-Impact Infrastructure Tuning
1. **Set Cloud Functions `min-instances = 1`** in GCP Console. This immediately removes cold-start latency from the Turn-9 pipeline.
2. **Deploy the Acoustic Filler buffers** on the frontend. When Turn 9 triggers, play a natural agent voice filler ("Let me search our product catalog for you...") immediately, masking the backend delay.

### Phase 2: Core UX & Client-Side Upgrades (Path B Evolution)
1. **Integrate `@ricky0123/vad-web`** into `agent_builder/frontend/index.html`. Replace the old `setTimeout` debounce in `simulation.js` with neural speech-end signals.
2. **Implement client-side cancellation**: keep the mic active during `SPEAKING`, and call `window.speechSynthesis.cancel()` immediately when VAD fires `onSpeechStart`.
3. **Upgrade voice selection**: if staying turn-based, modify `/invoke` to call GCP Text-to-Speech API directly (`en-IN-Neural2-A`) and return the base64 audio stream.

### Phase 3: High-Value Exploration (Path A Spike)
1. Create an isolated git branch (`gemini-live-spike`) and attempt to deploy a simple WebSockets proxy connecting the frontend to `gemini-2.0-flash-exp` or `gemini-2.5-flash` Live.
2. Validate WebSockets connection, audio frame capture, and native function calling.
3. If successful, merge the live spike into master; otherwise, ship the highly stable and polished Path B upgrades.

---

## Reviewer fixes applied 2026-06-05

These two reviewer-flagged issues were caught during the Day 8 reviewer pass and fixed before the docs push. Both surfaced *after* B1/B2/B4 were "implemented" but *before* deploy — exactly the value of a separate reviewer round.

**M1 — D8 contract inversion (`voice-player.js`):** B1 was creating + publishing `window.__voiceAudioCtx` from the player side instead of reading B2's. Failure path: welcome message played on session-open → orphan playback-only AudioContext, B2's mic-suspend hooks subsequently no-op outside the narrow STT-active window (echo-cancellation broken on every welcome). Fix: deleted `_ensureAudioCtx()`; replaced with null-safe `_readAudioCtx()`. Spec D8 single-publisher contract restored — B2 is the sole publisher of `window.__voiceAudioCtx` + `window.__voiceMicSuspended`; B1 is read-only.

**M2 — POLICY_QUESTION fall-through (`agent_builder/main.py`):** When the B4 classifier returned `FREE_FORM` (i.e. POLICY_QUESTION intent), the dispatch was falling through to the legacy regex `detect_followup_intent` block, which would fuzzy-match the policy question to a product name ("what is critical illness" + Critical Illness in `top3` → templated card returned instead of LLM passthrough answer). Fix: introduced `_skip_regex_followup` flag on the FREE_FORM branch; when set, `detect_followup_intent` raises `LookupError("B4_FREE_FORM_BYPASS")` and the existing fall-through chain lands at the LLM passthrough as intended. No new code path — reused the existing exception-driven fall-through.

---

## Cross-references

- Push plan: `tasks/2026-06-05_hackathon_day8_tier_b_implementation/reports/Push_Plan.md`
- Plain-English walkthrough: `tasks/2026-06-05_hackathon_day8_tier_b_implementation/reports/Tier_B_Plain_English_Walkthrough.md`
- Locked decisions D1-D14: `tasks/2026-06-04_hackathon_day7_polish_bugs/p3_tier_b_voice_stack/reports/Locked_Decisions.md`
- Implementation reports: `tasks/2026-06-05_hackathon_day8_tier_b_implementation/reports/{B1,B2,B4}_Implementation_Report.md`
- Reviewer pass: `tasks/2026-06-05_hackathon_day8_tier_b_implementation/reports/Reviewer_Pass_Report.md`
- D10 Runner spike: `tasks/2026-06-05_hackathon_day8_tier_b_implementation/reports/D10_Runner_Spike_Report.md`

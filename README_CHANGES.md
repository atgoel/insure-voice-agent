# Phase 1 Correctness Improvements (v4 changes)

This document outlines the correctness bug fixes (Bugs J, K, L, and M) introduced in **insure-voice-agent-stable_v4** to elevate conversational flow, remove visual redundancies, fix currency encoding, and ensure product-routing precision.

---

## 1. Bugs J & K: Named-Product-Wins Rule
*   **The Issue**: During multi-turn conversations, when the user explicitly asked about a specific insurance product by name (e.g., *"No, I just want to know more about the Medicare plan..."*), the agent would sometimes incorrectly route them to a different plan because a vague ordinal keyword matching heuristic (e.g., *"first"*, *"second"*) got falsely triggered by ordinal-routing logic.
*   **The Fix**: Implemented a strict **"Named-Product-Wins"** precedence rule in `agent_builder/main.py`.
    *   The agent first extracts any explicitly named products from the user's message using `match_product_by_name()`.
    *   If a product is explicitly named, that matching product **takes absolute precedence** and overrides any ordinal interpretation (even if the user's sentence contained ordinal-related words like *"first"*).
    *   This eliminates demo-killing false routes when a user inquires about a named plan.
*   **Files Modified**:
    *   `agent_builder/main.py` (Routing logic updated to enforce named precedence).

---

## 2. Bug L: Session-Specific Duplicate Suppression
*   **The Issue**: If a user repeated their question or followed up on a recommendation without triggering a change in the top-3 products, the agent would visually re-render the exact same product list card. This cluttered the UI and made the agent feel slow and robotic.
*   **The Fix**: Created a server-side session deduplication system.
    *   Introduced `LAST_RENDERED_BY_SESSION` in `agent_builder/shared_state.py` to cache the list of product IDs shown to the user on their last turn.
    *   If the exact same list of products is returned consecutively, the agent **suppresses the UI card render** (removes the `top3` payload).
    *   Instead of repeating itself, the agent delivers a clean, human-like conversational fallback message:
        > *"I've already shown you those recommendations. Is there a specific plan you'd like to dive into, or would you like to adjust your details?"*
    *   **Bypass**: Implemented a voice bypass. If the user explicitly asks to *"show me again"*, *"repeat"*, *"once more"*, or *"show me"*, the state is cleared and the agent renders the card fresh.
*   **Files Modified / Added**:
    *   `agent_builder/shared_state.py` (Added `LAST_RENDERED_BY_SESSION` tracker).
    *   `agent_builder/main.py` (Incorporated duplication check, bypass detector, and payload suppression).
    *   `tests/test_t1_bug_l_dedup.py` **[NEW]** (Comprehensive test suite covering consecutive rendering suppression and repetition bypass).

---

## 3. Bug M: Egress Mojibake Correction
*   **The Issue**: Text displays on screen showed visual encoding corruption for the Indian Rupee symbol, rendering as `â‚¹` instead of `₹`.
*   **The Fix**: Added a lightweight, robust correction function `_fix_mojibake()` at output egress.
    *   Before sending any `response_text` to the client, the text is scanned and any instances of `â‚¹` are cleanly resolved to `₹`.
*   **Files Modified**:
    *   `agent_builder/main.py` (Added `_fix_mojibake` utility and integrated it into response construction).

---

## 4. Verification and Testing
All correctness fixes are thoroughly verified and covered by the automated test suite.

### Running Tests
To run the full test suite (including the new `test_t1_bug_l_dedup.py` test suite):
```bash
pytest
```
*   **v4 Expected Output (Phase 1 only, pre-Tier B baseline)**: **551 passed**, 29 skipped (All tests green).
*   **v3 Expected Output**: **550 passed**, 29 skipped (All tests green, with Phase 1 fixes cleanly reverted to Day 7 baseline).
*   **v4 Expected Output (Day 8, Phase 1 + Tier B)**: **567 passed**, 29 skipped (see Day 8 section below).

---

## Day 8 — Tier B Voice-Stack Implementation (2026-06-05)

The Tier B voice-stack swap landed in `stable_v4` on the same working copy as the Phase 1 correctness fixes above and is part of the same push to `abhishek-stable-branch`. Tier B is **not yet deployed** — live revision `00030-jc7` still serves the Day 7 baseline; the Day 9 plan is `--no-traffic` deploy + browser smoke + AC-B4.11 latency probe before promoting traffic.

### B1 — Chirp 3 HD streaming TTS
*   **What:** New module `agent_builder/tts_streaming.py` (396 lines). Voice `en-IN-Chirp3-HD-Aoede`, 24kHz MP3. Public API `synthesize_bytes(text)` and `synthesize_chunks(text)` (async generator). New endpoint `POST /tts/stream` with in-memory per-IP rate limit (30 req/min, 429 on breach). PoC measured 1.57s cold start (under the 2s target).
*   **Replaces:** browser-native `SpeechSynthesisUtterance`.
*   **Report:** `tasks/2026-06-05_hackathon_day8_tier_b_implementation/reports/B1_*.md`.

### B2 — Speech-to-Text v2 streaming
*   **What:** New module `agent_builder/stt_websocket.py` (549 lines). Public API `stt_stream_handler` (FastAPI WebSocket route). Google Cloud Speech-to-Text v2 + Chirp 2 model + native VAD tuned to 800ms. New endpoint `WebSocket /stt/stream`. AudioWorklet-based 16kHz PCM mic capture from the browser via `frontend/voice/audio-worklet-processor.js` + `frontend/voice/stt-client.js`. Graceful `SDK_UNAVAILABLE` degradation when `google-cloud-speech` is not importable.
*   **Replaces:** browser-native `webkitSpeechRecognition` + 1.2s silence-debounce.
*   **Endpoint registration rule:** the WebSocket route MUST be registered BEFORE the StaticFiles mount in `main.py`, otherwise StaticFiles swallows the WebSocket upgrade.
*   **Report:** `tasks/2026-06-05_hackathon_day8_tier_b_implementation/reports/B2_*.md`.

### B4 — Flash-Lite intent classifier
*   **What:** New module `agent_builder/intent_classifier.py` (565 lines). Separate `LlmAgent` sub-agent with its OWN ADK Runner under `app_name="insure-voice-classifier"` (D10 fix — root agent runs as `app_name="insure-voice"` and `function_response` events from the classifier Runner would otherwise leak into root's session event log and break the C.5 mid-pipeline state machine). Public API `classify_intent_async`, `classify_followup_intent`, `init_classifier_runner`. Returns one of `NAMED_PRODUCT` / `ORDINAL` / `POLICY_QUESTION` / `AMBIGUOUS` with a confidence score. Confidence threshold 0.7; force-clarify band (0.5, 0.7); below 0.5 falls back to the legacy regex path in `followup.py`. Own `before_model_callback=_force_classifier_tool` (separate from the root agent's existing `_force_tool_call_mid_pipeline`).
*   **Feature-flagged:** env var `USE_LLM_INTENT_CLASSIFIER` (default off — opt-in).
*   **Golden fixture:** `tests/fixtures/bug_j_golden.json` — 15 hand-authored cases against the 28-product catalog (D12).
*   **D13 refactor:** `farewell_voice_text()` in `followup.py:393` replaced by module-level constant `CANONICAL_FAREWELL_TEXT` with a back-compat function shim.
*   **Report:** `tasks/2026-06-05_hackathon_day8_tier_b_implementation/reports/B4_*.md`.

### Reviewer pass — M1 + M2 fixes
*   **M1 — D8 inversion fix** in `frontend/voice-player.js`: removed the buggy `_ensureAudioCtx` (which was creating + publishing the `window.__voiceAudioCtx` global from the player side, inverting the D8 contract) and replaced it with a null-safe `_readAudioCtx`. B2 publishes `window.__voiceAudioCtx` and `window.__voiceMicSuspended` on STT init; B1 only reads them and toggles `.suspend()` / `.resume()` around `<audio>` playback (with a 200ms echo-tail delay on `.onended` before resume).
*   **M2 — FREE_FORM fall-through** in `agent_builder/main.py`: added a `_skip_regex_followup` flag for the FREE_FORM intent path so it raises `LookupError("B4_FREE_FORM_BYPASS")` and lands at the LLM passthrough instead of being short-circuited by the regex follow-up dispatch.

### Hackathon-rule audit (Tier B)
The Devpost rules require Gemini + Google Cloud Agent Builder + Partner MCP server, and forbid "all other AI tools". B1 (Cloud TTS), B2 (Cloud STT v2 + Chirp 2), and B4 (Gemini Flash-Lite via ADK) are all on the Google Cloud stack — compliant. B3 (open-source Silero VAD ONNX neural net at inference time) was **DROPPED (D1)** specifically because it plausibly violates "all other AI tools not permitted"; the 150-300ms barge-in latency win was not worth the disqualification risk.

### B3 / B5 / B6 / B7 status
| Sub-task | Status | Reason |
|---|---|---|
| B3 — Silero VAD | DROPPED (D1) | Hackathon rule risk. ~3h reclaimed. |
| B5 — Tool-result-only render | DEFERRED — Day 9+ | Predecessor on G5 + B4 + v4 baseline capture. |
| B6 — Backchannel injection | DEFERRED — Day 9+ | Predecessor on B1 voice lock. AC-B6.0 measurement gate before threshold lock. |
| B7 — ADK eval smoke harness | DEFERRED — Day 9+ | Predecessor on D13 canonical-farewell refactor (now landed). 5-case smoke only per D2. |

### Combined test suite (Phase 1 + Tier B)
Running `pytest tests/` from `stable_v4`: **567 passed / 29 skipped / 0 failed** (~28.86s). The +16 net delta over the 551-pass Phase-1-only baseline is +12 from `tests/test_intent_classifier.py` (NEW, 11+ B4 cases) + 4 from `tests/test_b2_resume_tail.py` (NEW, B1↔B2 echo-tail contract).

### Files (Day 8 only — see `tasks/.../Push_Plan.md` for the complete list)
*   **NEW backend:** `agent_builder/tts_streaming.py`, `agent_builder/stt_websocket.py`, `agent_builder/intent_classifier.py`.
*   **NEW frontend:** `agent_builder/frontend/voice-player.js`, `agent_builder/frontend/voice/stt-client.js`, `agent_builder/frontend/voice/audio-worklet-processor.js`.
*   **NEW tests/fixtures:** `tests/test_intent_classifier.py`, `tests/test_b2_resume_tail.py`, `tests/fixtures/bug_j_golden.json`.
*   **MODIFIED:** `agent_builder/main.py` (Tier B routing + M2 fix), `agent_builder/followup.py` (D13 canonical-farewell constant), `agent_builder/requirements.txt` (`google-cloud-texttospeech>=2.14.1`, `google-cloud-speech>=2.27.0`, `google-genai==1.75.0`), `agent_builder/frontend/simulation.js` + `frontend/index.html` (UI hooks).

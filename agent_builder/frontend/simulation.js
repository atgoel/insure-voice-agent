/**
 * InsureVoice — Voice Console Engine (Cloud-only)
 * --------------------------------------------------
 * Captures the user's microphone via the Web Speech API, POSTs each utterance
 * to the same-origin /invoke endpoint (Atul's deployed multi-agent runner),
 * and speaks back the response via SpeechSynthesisUtterance.
 *
 * Local Sim mode and the synthetic INSURANCE_PRODUCTS catalog were removed
 * for the deployed build (see tasks/2026-06-01_hackathon_day4/data/simulation_pre_strip.js
 * for the pre-strip snapshot if Local Sim is needed for offline development).
 */

// Early debug log buffer to capture pre-load warnings before app.js wires logDebug
window.logDebugBuffer = [];
window.logDebug = function (message, level = "info") {
    window.logDebugBuffer.push({ message, level, time: new Date().toLocaleTimeString() });
    console.log(`[EarlyDebug][${level}] ${message}`);
};

class VoiceSimulationEngine {
    constructor() {
        // Empty meta content = relative URL → calls hit same origin (production same-origin deploy).
        // Set meta content to absolute URL for local-dev testing against a remote /invoke.
        const meta = document.querySelector('meta[name="invoke-url"]');
        this.invokeUrl = (meta && meta.content) ? meta.content : '';
        this.invokeSessionId = null;       // null until first /invoke response
        this.hasUserSpokenOnce = false;
        this.sessionEnded = false;

        this.sttClient = null;             // B2 — SttClient.create(...) controller
        this.sttActive = false;
        this.shouldBeListening = false;
        this.isPlayingVoice = false;
        this.currentUtterance = null;
        this.accumulatedTranscript = '';
        this.postRecSilenceTimerId = null; // 15s post-turn silence prompt
        this.silenceStrikeCount = 0;

        try {
            this.synth = window.speechSynthesis;
        } catch (e) {
            console.warn("speechSynthesis access is blocked/restricted:", e);
            this.synth = null;
        }

        this.setupSpeechToText();
    }

    setupSpeechToText() {
        if (!window.SttClient || typeof window.SttClient.create !== 'function') {
            console.warn("[B2] SttClient not loaded — voice/stt-client.js missing?");
            window.logDebug("[STT Error] SttClient not loaded — voice/stt-client.js missing?", "warning");
            return;
        }
        this.sttClient = window.SttClient.create({
            onActivity: (event) => {
                window.logDebug(`[STT VAD] ${event}`, "muted");
                if (event === 'SPEECH_ACTIVITY_BEGIN') {
                    this.hasUserSpokenOnce = true;
                    this.clearSilenceTimers();
                    this.silenceStrikeCount = 0;
                }
            },
            onInterim: (text, _stability) => {
                this.accumulatedTranscript = text;  // live render buffer
                if (text.trim()) window.logDebug("[STT Interim] " + text, "muted");
            },
            onFinal: (text, _conf) => {
                const speechOutput = (text || '').trim();
                if (!speechOutput) return;
                this.accumulatedTranscript = '';
                // Strategy 2: do NOT tear down the WS/mic/gRPC. Soft-pause (mute)
                // so any trailing user speech or the upcoming TTS can't re-trigger
                // STT; the stream stays OPEN for the whole conversation. speak() ->
                // voice-player onplay suspends the ctx; onended resumes + the
                // processInputText .then re-arm re-enables the silence timer.
                // (The old stopListening()/rebuild-per-turn caused the turn-3 dead
                // mic — there is no second `new WebSocket()` race anymore.)
                this.pauseListening();
                window.addTranscriptBubble('USER', speechOutput);
                this.processInputText(speechOutput);
            },
            onError: (code, detail) => {
                window.logDebug(`[STT Error] ${code}: ${detail}`, "warning");
                if (code === 'MIC_DENIED') {
                    window.updateVoiceState('BLOCKED');
                    return;
                }
                // STT_RPC_ERROR after silent reconnect failed — surface banner.
                window.updateVoiceState('IDLE');
            },
            onClosed: (reason) => {
                this.sttActive = false;
                window.logDebug(`[STT Closed] ${reason}`, "info");
            }
        });
    }

    startSilenceTimer() {
        this.clearSilenceTimers();

        // Don't arm before user has spoken once (kills welcome-time fires)
        if (!this.hasUserSpokenOnce) {
            console.log("[SilenceTimer] Skipped — user has not spoken yet.");
            return;
        }
        // Don't arm after session ends (goodbye spoken, etc.)
        if (this.sessionEnded) {
            console.log("[SilenceTimer] Skipped — session has ended.");
            return;
        }
        // Don't arm while TTS is mid-sentence
        if (this.isPlayingVoice) {
            console.log("[SilenceTimer] Skipped — TTS still in progress.");
            return;
        }

        // 15s post-turn silence prompt. Slot-filling lives server-side now (Atul's agent),
        // so the client only needs one timer: nudge once, then wrap up.
        this.postRecSilenceTimerId = setTimeout(() => {
            this.silenceStrikeCount++;

            let prompt = "";
            if (this.silenceStrikeCount === 1) {
                // Strike 1: soft pause (mute + clear timers), stream stays OPEN;
                // the nudge TTS's onended -> startListening() re-arms a fresh timer.
                this.pauseListening();
                prompt = "Anything else I can help with? Otherwise I'll wrap up here.";
            } else {
                // Strike 2: true session end. sessionEnded set BEFORE the prompt so
                // startSilenceTimer's guard won't re-arm; hard teardown happens in
                // the TTS callback below.
                this.sessionEnded = true;
                prompt = "Thank you so much for exploring options with InsureVoice. Goodbye!";
            }

            window.addTranscriptBubble('AGENT', prompt);
            this.speak(prompt, () => {
                if (this.silenceStrikeCount === 1) {
                    this.startListening();          // idempotent re-arm (stream still OPEN)
                } else {
                    this.stopListening();           // HARD teardown at true session end
                    window.updateVoiceState('IDLE');
                    const core = document.querySelector('.voice-orb-core');
                    if (core) {
                        core.style.boxShadow = "none";
                        core.style.background = "radial-gradient(circle, rgba(100,116,139,0.4) 0%, rgba(15,23,42,0.8) 100%)";
                    }
                }
            }, false);
        }, 15000);
    }

    clearSilenceTimers() {
        if (this.postRecSilenceTimerId) {
            clearTimeout(this.postRecSilenceTimerId);
            this.postRecSilenceTimerId = null;
        }
    }

    async startListening() {
        if (!this.sttClient) return;
        if (this.sessionEnded) return;

        // UNMUTE FIRST (2026-06-06 dead-mic fix). The worklet drops EVERY audio
        // frame while muteSTTOutput is true (stt-client.js:339), so the mic is
        // deaf until this clears. Previously this lived AFTER the `isPlayingVoice`
        // early-return below — so if a strike-1 nudge's speak() had re-set
        // isPlayingVoice=true, startListening() returned before unmuting and the
        // mic stayed dead for the rest of the turn (observed: "not a smoker" never
        // reached the server). Unmuting is always safe; only TIMER-arming must
        // wait for TTS to finish. This also covers the _speakBrowser fallback
        // path where voice-player's onended never runs.
        try { this.sttClient.setMuted(false); } catch (_) {}

        if (this.isPlayingVoice) return;   // never ARM THE SILENCE TIMER mid-TTS

        this.shouldBeListening = true;
        window.updateVoiceState('LISTENING');
        this.accumulatedTranscript = '';

        // Stream already live (turns 2+): just re-arm the silence timer, do NOT
        // rebuild the WS/mic/gRPC. sttActive alone is the "is the stream live?"
        // signal — dropped the old `shouldBeListening && sttActive` combined guard.
        if (this.sttActive) {
            this.startSilenceTimer();
            return;
        }

        // FIRST turn only: open mic + WS + gRPC exactly once.
        try {
            this.sttClient.setSessionId(this.invokeSessionId);
            await this.sttClient.start({ session_id: this.invokeSessionId });
            this.sttActive = true;
            this.startSilenceTimer();
        } catch (e) {
            console.warn("[STT] Start exception:", e);
            this.sttActive = false;   // reset so a later turn can retry the open
            this.startSilenceTimer();
        }
    }

    // Soft pause: mute + clear timers, leave WS+mic+gRPC ALIVE. Used between
    // turns (onFinal), by silence-timer strike 1, and by the mute button (app.js).
    // Re-armable via the now-idempotent startListening().
    pauseListening() {
        this.shouldBeListening = false;
        this.clearSilenceTimers();
        if (this.sttClient && this.sttActive) {
            try { this.sttClient.setMuted(true); } catch (e) { console.warn("[STT] Mute exception:", e); }
        }
    }

    // Hard stop: full teardown of WS+mic+gRPC. Used ONLY at true session end
    // (silence strike 2 / sessionEnded) and page unload. NOT between turns.
    async stopListening() {
        this.shouldBeListening = false;
        this.clearSilenceTimers();
        if (this.sttClient && this.sttActive) {
            try { await this.sttClient.stop(); } catch (e) { console.warn("[STT] Stop exception:", e); }
            this.sttActive = false;
        }
    }

    speak(text, callback, force = true) {
        if (!this.synth) {
            if (callback) callback();
            return;
        }

        console.log(`[TTS-DIAG] speak() called. Pending queue length=${this.synth.pending}, speaking=${this.synth.speaking}, text="${text.substring(0, 60)}..."`);

        // B1 — Prefer server-side Chirp 3 HD via /tts/stream when available.
        // Falls back to SpeechSynthesisUtterance (_speakBrowser) if the player
        // module failed to load or the server returns non-2xx.
        if (window.voicePlayer && typeof window.voicePlayer.playTTS === 'function') {
            if (force && this.isPlayingVoice) {
                try { window.voicePlayer.stop(); } catch (e) { /* ignore */ }
                this.isPlayingVoice = false;
            } else if (this.isPlayingVoice && !force) {
                console.log("[TTS] Skipping /tts/stream — playback in progress, force=false");
                return;
            }

            this.isPlayingVoice = true;
            window.updateVoiceState('SPEAKING');
            window.voicePlayer.playTTS(text, {
                session_id: this.invokeSessionId || undefined,
                force: !!force,
            }).then((result) => {
                this.isPlayingVoice = false;
                if (result && result.ok) {
                    if (callback) setTimeout(callback, 50);
                } else {
                    console.warn("[TTS] /tts/stream failed; falling back to browser TTS:", result && result.error);
                    this._speakBrowser(text, callback, force);
                }
            }).catch((err) => {
                this.isPlayingVoice = false;
                console.warn("[TTS] /tts/stream threw; falling back to browser TTS:", err);
                this._speakBrowser(text, callback, force);
            });
            return;
        }
        return this._speakBrowser(text, callback, force);
    }

    _speakBrowser(text, callback, force = true) {
        // Original browser-side SpeechSynthesisUtterance path, kept as fallback.
        if (!this.synth) {
            if (callback) callback();
            return;
        }

        if (force) {
            if (this.synth.speaking || this.synth.pending) {
                console.log("[TTS-DIAG] Canceling existing utterance before new speak()");
                this.synth.cancel();
                this.isPlayingVoice = false;
            }
        } else if (this.isPlayingVoice || this.synth.speaking) {
            console.log("[TTS] Skipping cancel — TTS in progress and force=false");
            return;
        }

        const cleanText = text
            .replace(/<[^>]*>/g, '')
            .replace(/\*\*([^*]+)\*\*/g, '$1')
            .replace(/__([^_]+)__/g, '$1')
            .replace(/\*([^*]+)\*/g, '$1')
            .replace(/_([^_]+)_/g, '$1')
            .replace(/`([^`]+)`/g, '$1')
            .replace(/^#+\s+/gm, '')
            .replace(/[*_~`]/g, '')
            .trim();

        if (!cleanText) {
            if (callback) callback();
            return;
        }

        const utterance = new SpeechSynthesisUtterance(cleanText);
        utterance.lang = 'en-IN';
        this.currentUtterance = utterance;

        const voices = this.synth.getVoices();
        const preferredVoice = voices.find(v => v.lang.toLowerCase() === 'en-in' && v.name.toLowerCase().includes('female'));
        if (preferredVoice) {
            utterance.voice = preferredVoice;
        }
        utterance.rate = 1.0;
        utterance.pitch = 1.05;

        let speechStarted = false;
        let speechCompleted = false;

        const startTimeout = setTimeout(() => {
            if (!speechStarted && !speechCompleted) {
                console.warn("[TTS Failsafe] Speech failed to start within 1.5s. Forcing callback.");
                handleSpeechEnd();
            }
        }, 1500);

        const wordCount = cleanText.split(/\s+/).length;
        const estimatedDurationMs = (wordCount / 2.5) * 1000 + 4000;
        const endTimeout = setTimeout(() => {
            if (!speechCompleted) {
                console.warn(`[TTS Failsafe] Speech exceeded estimated duration (${estimatedDurationMs}ms). Forcing callback.`);
                handleSpeechEnd();
            }
        }, estimatedDurationMs);

        const handleSpeechEnd = () => {
            if (speechCompleted) return;
            speechCompleted = true;
            clearTimeout(startTimeout);
            clearTimeout(endTimeout);
            this.isPlayingVoice = false;
            window.updateVoiceState('IDLE');
            this.currentUtterance = null;
            if (callback) {
                setTimeout(callback, 50);
            }
        };

        utterance.onstart = () => {
            speechStarted = true;
            this.isPlayingVoice = true;
            window.updateVoiceState('SPEAKING');
        };
        utterance.onend = () => handleSpeechEnd();
        utterance.onerror = (e) => {
            console.error("Speech playback error:", e);
            handleSpeechEnd();
        };

        try {
            if (this.synth.paused) {
                console.log("[TTS] Synth is paused, calling resume() before speak.");
                this.synth.resume();
            }
            this.synth.speak(utterance);
        } catch (err) {
            console.error("[TTS speak error]", err);
            handleSpeechEnd();
        }
    }

    async playWelcomeGreeting() {
        window.updateVoiceState('PROCESSING');

        // Clear existing static bubbles in transcript
        const scroller = document.getElementById('transcript-scroller');
        if (scroller) scroller.innerHTML = '';

        // Silent start: no welcome bubble, no welcome speech.
        // The user's first utterance is the seed for /invoke.
        window.logDebug("[Cloud Mode] Silent start — waiting for user to speak first.", "info");

        if (this.sttClient) {
            window.updateVoiceState('LISTENING');
            this.startListening();
            return;
        }

        window.updateVoiceState('BLOCKED');
        const warnMsg = `
            <div class="voice-blocked-warning" style="padding: 12px 16px; margin: 10px 0; border-radius: 8px; background: rgba(220, 38, 38, 0.15); border: 1px solid rgba(220, 38, 38, 0.3); color: #fca5a5; font-size: 14px; line-height: 1.5;">
                <strong style="display: block; margin-bottom: 4px; color: #f87171;"><i class="fa-solid fa-microphone-slash"></i> Microphone / Speech Recognition Blocked</strong>
                The Web Speech API is not active or blocked in this browser/frame context.
                <ul style="margin: 8px 0 0 16px; padding: 0;">
                    <li>Check if mic permission is granted in your browser address bar.</li>
                    <li>Ensure you are using <strong>Google Chrome</strong> or <strong>Edge</strong>.</li>
                    <li>If running inside an iframe, check that the frame has <code>allow="microphone"</code>.</li>
                </ul>
            </div>
        `;
        window.addTranscriptBubble('AGENT', warnMsg);
        window.logDebug("[System Error] Speech Recognition is null/disabled. Interactive speech features will not function.", "error");
    }

    processInputText(text) {
        window.updateVoiceState('PROCESSING');
        window.logDebug(`[STT Input] "${text}"`, "info");

        // Mark that user has spoken — unlocks silence timer for future turns
        this.hasUserSpokenOnce = true;

        window.logDebug(`[/invoke] POST → message="${text.substring(0, 60)}…" session=${this.invokeSessionId || '(new)'}`, "info");

        // [B-LIVE-1] Patch 1C — DIAGNOSTIC INSTRUMENTATION ONLY (no behaviour change).
        // Detect FE session-id loss between turns to disambiguate H1 (FE state loss)
        // vs H2 (server intake-state loss). Remove after Day 9 root-cause confirmed.
        const sentId = this.invokeSessionId;
        if (this.hasUserSpokenOnce && (sentId === null || sentId === undefined || sentId === '')) {
            console.error("[B-LIVE-1] invokeSessionId is empty before /invoke POST — session-id was LOST. This will cause welcome-replay. Stack:", new Error().stack);
        }
        console.log("[B-LIVE-1] POST /invoke session=" + sentId + " text='" + text + "' /* TODO Day 9: PII removal */");

        const requestBody = { message: text };
        if (this.invokeSessionId) {
            requestBody.session_id = this.invokeSessionId;
        }

        fetch(`${this.invokeUrl}/invoke`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody)
        })
            .then(res => {
                if (!res.ok) throw new Error(`HTTP error ${res.status}`);
                return res.json();
            })
            .then(data => {
                // [B-LIVE-1] Patch 1C — flag server returning a different session_id than sent.
                const gotId = data.session_id;
                if (sentId && gotId && sentId !== gotId) {
                    console.warn("[B-LIVE-1] server returned different session_id than sent — sent=" + sentId + " got=" + gotId);
                }
                this.invokeSessionId = data.session_id;
                // Fallback when bot text is empty: if pipeline returned products, point at the cards.
                // Otherwise the original "didn't catch that" prompt for the user to retry.
                const hasProducts = Array.isArray(data.top3) && data.top3.length > 0;
                const explanation = data.response
                    || (hasProducts
                        ? "Here are your top matches — please see the recommended products on the right."
                        : "I'm sorry, I didn't catch that. Could you say it again?");

                // Render product cards ONLY when the response actually carries new
                // recommendation data. Follow-up turns ("tell me more about the third
                // option") deliberately omit top3/rejected — preserve the existing
                // cards instead of clearing them. Use property presence, NOT truthiness:
                // an empty array is a real "no matches found" signal worth honoring;
                // a missing key means "no new pipeline run, keep current cards."
                if ('top3' in data || 'rejected' in data) {
                    const newTop3 = Array.isArray(data.top3) ? data.top3 : [];
                    const newRejected = Array.isArray(data.rejected) ? data.rejected : [];
                    window.displayRecommendedProducts(newTop3, newRejected);
                }

                window.addTranscriptBubble('AGENT', explanation);
                this.speak(explanation, () => {
                    this.startListening();
                });
            })
            .catch(err => {
                console.error("Cloud Run /invoke failed:", err);
                window.logDebug(`[/invoke ERROR] ${err.message}`, "warning");
                const errMsg = "I'm having trouble reaching the recommendation service. Please try again in a moment.";
                window.addTranscriptBubble('AGENT', errMsg);
                this.speak(errMsg, () => { this.startListening(); });
            });
    }
}

// Global initialization
try {
    window.voiceEngine = new VoiceSimulationEngine();
} catch (err) {
    console.error("Critical error constructing VoiceSimulationEngine:", err);
    window.logDebug("[Critical Error] Failed to start voice engine: " + err.message, "error");
}

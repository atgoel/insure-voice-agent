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

        // Simulation endpoint — reads simulate-url meta tag; falls back to same-origin /simulate.
        const simMeta = document.querySelector('meta[name="simulate-url"]');
        this.simulateUrl = (simMeta && simMeta.content) ? simMeta.content : '/simulate';
        this.invokeSessionId = null;       // null until first /invoke response
        this.hasUserSpokenOnce = false;
        this.sessionEnded = false;

        this.recognition = null;
        this.recognitionActive = false;
        this.shouldBeListening = false;
        this.isPlayingVoice = false;
        this.currentUtterance = null;
        this.accumulatedTranscript = '';
        this.silenceTimer = null;          // 1.2s end-of-speech debounce inside onresult
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
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) {
            console.warn("Web Speech API is not supported in this browser.");
            window.logDebug("[STT Error] Web Speech API is not supported in this browser.", "warning");
            return;
        }

        try {
            this.recognition = new SpeechRecognition();
        } catch (err) {
            console.error("Failed to construct SpeechRecognition:", err);
            window.logDebug("[STT Error] Failed to initialize SpeechRecognition: " + err.message, "warning");
            this.recognition = null;
            return;
        }

        this.recognition.continuous = true;
        this.recognition.interimResults = true;
        this.recognition.lang = 'en-IN';

        this.recognition.onstart = () => {
            this.recognitionActive = true;
            window.updateVoiceState('LISTENING');
            this.accumulatedTranscript = '';
            window.logDebug("[STT] Speech recognition session started. Speak now!", "success");
        };

        this.recognition.onerror = (event) => {
            this.recognitionActive = false;
            console.error("STT Error:", event.error);
            window.logDebug("[STT Error] " + event.error, "warning");
            if (event.error !== 'no-speech') {
                window.updateVoiceState('IDLE');
            }
        };

        this.recognition.onend = () => {
            this.recognitionActive = false;
            window.logDebug("[STT] Speech recognition session ended.", "info");
            const muted = (typeof window.isMuted !== 'undefined') ? window.isMuted : false;
            if (this.shouldBeListening && !this.isPlayingVoice && !muted) {
                console.log("[STT onend] Auto-restarting recognition to fulfill target state...");
                try {
                    this.recognition.start();
                } catch (e) {
                    console.error("[STT onend] Failed to restart recognition:", e);
                }
            } else if (window.voiceState === 'LISTENING') {
                window.updateVoiceState('IDLE');
            }
        };

        this.recognition.onresult = (event) => {
            this.hasUserSpokenOnce = true;
            this.clearSilenceTimers();
            this.silenceStrikeCount = 0;

            if (this.silenceTimer) {
                clearTimeout(this.silenceTimer);
            }

            let interimTranscript = '';
            let finalTranscript = '';

            for (let i = event.resultIndex; i < event.results.length; ++i) {
                if (event.results[i].isFinal) {
                    finalTranscript += event.results[i][0].transcript + ' ';
                } else {
                    interimTranscript += event.results[i][0].transcript;
                }
            }

            if (finalTranscript) {
                this.accumulatedTranscript += finalTranscript;
            }

            const currentLiveSpeech = this.accumulatedTranscript + interimTranscript;
            console.log("Live Speech Transcript:", currentLiveSpeech);
            if (currentLiveSpeech.trim()) {
                window.logDebug("[STT Interim] " + currentLiveSpeech, "muted");
            }

            // 1.2s end-of-speech debounce — wait for user to actually finish before sending
            this.silenceTimer = setTimeout(() => {
                const speechOutput = (this.accumulatedTranscript + interimTranscript).trim();
                if (speechOutput.length >= 1) {
                    this.stopListening();
                    window.addTranscriptBubble('USER', speechOutput);
                    this.processInputText(speechOutput);
                }
            }, 1200);
        };
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
            this.stopListening();

            let prompt = "";
            if (this.silenceStrikeCount === 1) {
                prompt = "Anything else I can help with? Otherwise I'll wrap up here.";
            } else {
                prompt = "Thank you so much for exploring options with InsureVoice. Goodbye!";
                this.sessionEnded = true;
            }

            window.addTranscriptBubble('AGENT', prompt);
            this.speak(prompt, () => {
                if (this.silenceStrikeCount === 1) {
                    this.startListening();
                } else {
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

    startListening() {
        if (!this.recognition) return;
        if (this.isPlayingVoice) return;
        if (this.sessionEnded) return;

        this.shouldBeListening = true;
        window.updateVoiceState('LISTENING');
        this.accumulatedTranscript = '';

        if (this.recognitionActive) {
            console.log("[STT] Recognition is already active. Ensuring silence timer is started.");
            this.startSilenceTimer();
            return;
        }

        try {
            this.recognition.start();
            this.startSilenceTimer();
        } catch (e) {
            console.warn("[STT] Start exception, will auto-start on next end if shouldBeListening is true:", e);
            this.startSilenceTimer();
        }
    }

    stopListening() {
        this.shouldBeListening = false;
        this.clearSilenceTimers();
        if (this.recognition && this.recognitionActive) {
            try {
                this.recognition.stop();
            } catch (e) {
                console.warn("[STT] Stop exception:", e);
            }
        }
    }

    speak(text, callback, force = true) {
        if (!this.synth) {
            if (callback) callback();
            return;
        }

        console.log(`[TTS-DIAG] speak() called. Pending queue length=${this.synth.pending}, speaking=${this.synth.speaking}, text="${text.substring(0, 60)}..."`);

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
        // Priority: en-IN female > en-IN any > en-GB female > en-US female
        const preferredVoice =
            voices.find(v => v.lang.toLowerCase() === 'en-in' && v.name.toLowerCase().includes('female')) ||
            voices.find(v => v.lang.toLowerCase() === 'en-in') ||
            voices.find(v => v.lang.toLowerCase().startsWith('en-gb') && v.name.toLowerCase().includes('female')) ||
            voices.find(v => v.lang.toLowerCase().startsWith('en-us') && v.name.toLowerCase().includes('female'));
        if (preferredVoice) {
            utterance.voice = preferredVoice;
            console.log(`[TTS] Selected voice: ${preferredVoice.name} (${preferredVoice.lang})`);
        }
        utterance.rate = 1.15;   // 1.0 was too slow/slurred; 1.15 is crisp and natural
        utterance.pitch = 1.08;

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

        if (this.recognition) {
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
                this.invokeSessionId = data.session_id;
                const explanation = data.response || "I'm sorry, I didn't catch that. Could you say it again?";

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

// ==========================================================================
// STORY 6 — Premium Simulation Panel (deterministic, no LLM)
// ==========================================================================

(function initSimulationPanel() {
    // DOM references — all guarded so absence of elements never throws.
    const screen       = document.getElementById('simulation-screen');
    const btnOpen      = document.getElementById('btn-open-sim');
    const btnClose     = document.getElementById('btn-close-sim');
    const productSel   = document.getElementById('sim-product-select');
    const sumSlider    = document.getElementById('sim-sum-assured');
    const sumLabel     = document.getElementById('sim-sum-label');
    const freqSel      = document.getElementById('sim-frequency');
    const termSel      = document.getElementById('sim-policy-term');
    const resultsCard  = document.getElementById('sim-results-card');
    const errMsg       = document.getElementById('sim-error-msg');
    const loadingEl    = document.getElementById('sim-loading');

    const resPeriod    = document.getElementById('sim-res-period');
    const resAnnual    = document.getElementById('sim-res-annual');
    const resTotal     = document.getElementById('sim-res-total');
    const resMaturity  = document.getElementById('sim-res-maturity');
    const resGain      = document.getElementById('sim-res-gain');
    const resMatRow    = document.getElementById('sim-res-maturity-row');
    const resGainRow   = document.getElementById('sim-res-gain-row');

    if (!screen || !btnOpen) return; // Panel not present in DOM — skip

    // Retrieve SIMULATE_URL from the engine instance (set during constructor from meta tag).
    function getSimulateUrl() {
        try {
            return (window.voiceEngine && window.voiceEngine.simulateUrl) ? window.voiceEngine.simulateUrl : '/simulate';
        } catch (_) { return '/simulate'; }
    }

    // Expose a method so app.js can populate the product selector after a recommendation turn.
    window.populateSimProductSelector = function(top3Products) {
        if (!productSel || !Array.isArray(top3Products)) return;
        // Clear existing dynamic options (keep the placeholder)
        while (productSel.options.length > 1) productSel.remove(1);
        top3Products.forEach((p, i) => {
            const name = p.name || p.product_name || `Plan ${i + 1}`;
            const pid  = p.product_id || p.id || '';
            const opt  = new Option(`${i + 1}. ${name}`, pid);
            opt.dataset.productType = p.product_type || '';
            productSel.appendChild(opt);
        });
        // Show the open button now that we have products
        if (btnOpen) btnOpen.style.display = 'flex';
    };

    // Format INR amounts compactly (₹5.0L, ₹1.2Cr, etc.)
    function fmtINR(val) {
        if (val == null || isNaN(val)) return '—';
        const n = Number(val);
        if (n >= 10000000) return `₹${(n / 10000000).toFixed(2)}Cr`;
        if (n >= 100000)   return `₹${(n / 100000).toFixed(2)}L`;
        return `₹${n.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',')}`;
    }

    // Update sum assured label as slider moves
    function updateSumLabel() {
        if (sumLabel && sumSlider) sumLabel.textContent = fmtINR(Number(sumSlider.value));
    }

    // Show/hide loading and result panels
    function setLoading(visible) {
        if (loadingEl)  loadingEl.style.display  = visible ? 'flex'  : 'none';
        if (resultsCard) resultsCard.style.display = visible ? 'none'  : 'none'; // keep hidden until results
        if (errMsg)      errMsg.style.display      = 'none';
    }

    function showError(msg) {
        if (errMsg) { errMsg.textContent = msg; errMsg.style.display = 'block'; }
        if (resultsCard) resultsCard.style.display = 'none';
        if (loadingEl)   loadingEl.style.display   = 'none';
    }

    function showResults(data) {
        if (!resultsCard) return;
        if (resPeriod)  resPeriod.textContent  = fmtINR(data.period_premium);
        if (resAnnual)  resAnnual.textContent   = fmtINR(data.annual_premium);
        if (resTotal)   resTotal.textContent    = fmtINR(data.total_premium_outflow);

        const hasMat = data.projected_maturity_value != null;
        if (resMatRow) resMatRow.style.display = hasMat ? '' : 'none';
        if (resGainRow) resGainRow.style.display = hasMat ? '' : 'none';
        if (hasMat) {
            if (resMaturity) resMaturity.textContent = fmtINR(data.projected_maturity_value);
            if (resGain)     resGain.textContent     = fmtINR(data.net_gain);
        }

        resultsCard.style.display = 'flex';
        if (errMsg)    errMsg.style.display    = 'none';
        if (loadingEl) loadingEl.style.display = 'none';
    }

    // Core simulation call
    async function fetchSimulation() {
        const productId = productSel && productSel.value;
        if (!productId) return; // No product selected yet — don't call

        const payload = {
            product_id:        productId,
            sum_assured:       parseInt(sumSlider ? sumSlider.value : 5000000, 10),
            customer_age:      30,   // Default — overridden by profile if available
            is_smoker:         false, // Default — overridden by profile if available
            premium_frequency: freqSel ? freqSel.value : 'monthly',
            policy_term:       parseInt(termSel ? termSel.value : 15, 10),
        };

        // Inject actual customer profile fields if intake data is available on the engine
        try {
            const profile = window.voiceEngine && window.voiceEngine.latestCustomerProfile;
            if (profile) {
                if (profile.age)     payload.customer_age = profile.age;
                if (profile.smoker !== undefined) payload.is_smoker = profile.smoker;
            }
        } catch (_) {}

        setLoading(true);
        try {
            const resp = await fetch(getSimulateUrl(), {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify(payload),
            });
            if (!resp.ok) {
                const detail = await resp.json().catch(() => ({}));
                showError(`Simulation error: ${detail.detail || resp.statusText}`);
                return;
            }
            const data = await resp.json();
            if (data.error) { showError(data.error); return; }
            showResults(data);
        } catch (err) {
            showError(`Network error: ${err.message}`);
        }
    }

    // Wire events
    if (btnOpen)  btnOpen.addEventListener('click', () => { screen.style.display = 'flex'; });
    if (btnClose) btnClose.addEventListener('click', () => { screen.style.display = 'none'; });
    if (sumSlider) {
        sumSlider.addEventListener('input',  () => { updateSumLabel(); fetchSimulation(); });
        updateSumLabel();
    }
    if (freqSel)     freqSel.addEventListener('change',   fetchSimulation);
    if (termSel)     termSel.addEventListener('change',   fetchSimulation);
    if (productSel)  productSel.addEventListener('change', fetchSimulation);

})();

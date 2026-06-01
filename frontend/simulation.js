/**
 * InsureVoice — Premium Voice Console Simulation Engine
 * Handles client-side Web Speech APIs, local ELSER-like keyword matching,
 * and deterministic compliance filters out-of-the-box.
 * Includes explicit bridge hookups for connecting to their Dialogflow CX agent.
 */

// Early debug log buffer to capture pre-load warnings before app.js is fully loaded
window.logDebugBuffer = [];
window.logDebug = function(message, level = "info") {
    window.logDebugBuffer.push({ message, level, time: new Date().toLocaleTimeString() });
    console.log(`[EarlyDebug][${level}] ${message}`);
};

// ----------------------------------------------------
// 1. Core Insurance Products Database (Synthetic Catalog)
// ----------------------------------------------------
// NOTE: This INSURANCE_PRODUCTS array is used ONLY in Local Sim mode.
// In Cloud Run AI mode, products come from Atul's product-search Cloud Function
// via /api/chat. Real catalog (28 products) lives in data/insurance_products.json.
const INSURANCE_PRODUCTS = [
    {
        id: "term_shield_pro",
        name: "Term Shield Pro",
        type: "Term Life",
        description: "Pure term protection plan providing massive coverage for family security. Ideal for young married couples, working professionals, and primary breadwinners needing high sum assured options at low premiums.",
        min_age: 18,
        max_age: 65,
        min_income: 300000,
        smoker_eligible: true,
        premium: "₹1,200/mo",
        sum_assured: "₹1 Crore",
        highlights: ["High Sum Assured", "Terminal Illness Rider", "Tax Savings Section 80C"]
    },
    {
        id: "smart_protect_life",
        name: "Smart Protect Life",
        type: "Term Life",
        description: "Comprehensive life protection with return of premium benefits. Best for safety-conscious investors seeking both security and guaranteed returns of their premiums upon survival.",
        min_age: 21,
        max_age: 60,
        min_income: 500000,
        smoker_eligible: true,
        premium: "₹1,950/mo",
        sum_assured: "₹75 Lakhs",
        highlights: ["Return of Premium", "Accidental Death Benefit", "Flexible Policy Terms"]
    },
    {
        id: "ulip_growth_builder",
        name: "ULIP Wealth Growth Builder",
        type: "ULIP Investment",
        description: "High-yield investment-linked life protection plan. Directs premiums into stock market equity funds for capital appreciation. Strictly restricted to non-smokers and healthy individuals under 45 due to aggressive equity structuring.",
        min_age: 18,
        max_age: 45,
        min_income: 800000,
        smoker_eligible: false, // Guardrail rule target
        premium: "₹4,500/mo",
        sum_assured: "₹50 Lakhs",
        highlights: ["Market Linked Returns", "5 Year Lock-In", "Switch Equity Funds Free"]
    },
    {
        id: "sec_child_future",
        name: "Secure Child Future Plan",
        type: "Endowment",
        description: "Traditional savings and life assurance policy specifically designed to fund children's higher education, marriage milestones, and secure their future even in the parent's absence.",
        min_age: 18,
        max_age: 50,
        min_income: 400000,
        smoker_eligible: true,
        premium: "₹2,800/mo",
        sum_assured: "₹30 Lakhs",
        highlights: ["Guaranteed Education Payout", "Life Cover on Parent", "Premium Waiver Rider"]
    },
    {
        id: "comprehensive_health_shield",
        name: "Comprehensive Health Shield",
        type: "Health",
        description: "Full-scale medical and health protection plan. Covers in-patient hospitalization, ICU expenses, pre/post-operative treatments, daycare operations, and annual health checkups for the entire family.",
        min_age: 18,
        max_age: 75,
        min_income: 250000,
        smoker_eligible: true,
        premium: "₹950/mo",
        sum_assured: "₹10 Lakhs Coverage",
        highlights: ["Cashless Hospitalization", "No Co-payment", "Free Annual Health Checkups"]
    },
    {
        id: "critical_illness_rider",
        name: "Critical Illness Guard",
        type: "Health",
        description: "Specialized rider/policy paying a lump sum upon diagnostic detection of 36 major critical illnesses including cancer, stroke, kidney failure, bypass surgery, and organ failures. Essential for comprehensive family health safety.",
        min_age: 18,
        max_age: 65,
        min_income: 400000,
        smoker_eligible: true,
        premium: "₹750/mo",
        sum_assured: "₹25 Lakhs Lump Sum",
        highlights: ["36 Critical Illnesses Covered", "Instant Lump Sum Payout", "Premium Waiver on Diagnosis"]
    }
];

// ----------------------------------------------------
// 2. Local Simulation Logic (Speech to Text & Text to Speech)
// ----------------------------------------------------

class VoiceSimulationEngine {
    constructor() {
        // Empty meta content = relative URL → calls hit same origin (production same-origin deploy).
        // Set meta content to absolute URL for local-dev testing against a remote /invoke.
        const meta = document.querySelector('meta[name="invoke-url"]');
        this.invokeUrl = (meta && meta.content) ? meta.content : '';
        this.invokeSessionId = null;       // null until first /invoke response
        this.hasUserSpokenOnce = false;    // for U2 fix

        this.recognition = null;
        this.isPlayingVoice = false;
        try {
            this.synth = window.speechSynthesis;
        } catch (e) {
            console.warn("speechSynthesis access is blocked/restricted:", e);
            this.synth = null;
        }
        this.recognitionActive = false;
        this.shouldBeListening = false;
        this.setupSpeechToText();
        
        // Persistent slot-filling profile state
        this.sessionProfile = {
            age: null,
            smoker: null,
            income: null,
            userName: null
        };
        this.userName = null;
        this.validationStrikes = { age: 0, smoker: 0, income: 0, off_topic: 0 };
        this.silenceStrikeCount = 0;
        this.silenceTimerId = null;
        this.postRecSilenceTimerId = null;

        this.lastQuestionAsked = null;
        this.sessionId = "session-" + Math.floor(Math.random() * 1000000);
        
        // Setup mode switch toggle wiring
        this.setupModeToggle();
    }

    setupModeToggle() {
        const checkbox = document.getElementById('mode-checkbox');
        const labelLocal = document.getElementById('label-local');
        const labelCloud = document.getElementById('label-cloud');

        if (!checkbox) return;

        checkbox.addEventListener('change', () => {
            const isCloud = checkbox.checked;
            if (isCloud) {
                labelLocal.classList.remove('active');
                labelCloud.classList.add('active');
                window.logDebug("[System] Switched to Cloud Run AI Mode. Hits Flask API with Vertex AI & Elastic Cloud.", "primary");
            } else {
                labelLocal.classList.add('active');
                labelCloud.classList.remove('active');
                window.logDebug("[System] Switched to Local Simulation Mode. Heuristics & local guardrails active.", "info");
            }
            // Reset slots on mode switch so they don't have mixed states
            this.sessionProfile = { age: null, smoker: null, income: null, userName: null };
            this.userName = null;
            this.validationStrikes = { age: 0, smoker: 0, income: 0, off_topic: 0 };
            this.silenceStrikeCount = 0;
            this.clearSilenceTimers();

            this.lastQuestionAsked = null;
            this.sessionId = "session-" + Math.floor(Math.random() * 1000000);
            window.displayRecommendedProducts([], []);

            // If the session is active, stop speaking/listening and restart the greeting!
            const activeScreen = document.getElementById('active-screen');
            if (activeScreen && activeScreen.classList.contains('active')) {
                if (this.synth) this.synth.cancel();
                this.stopListening();
                this.playWelcomeGreeting();
            }
        });
    }

    // 2.1 Web Speech API Speech-to-Text Setup
    setupSpeechToText() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) {
            console.warn("Web Speech API is not supported in this browser.");
            if (window.logDebug) window.logDebug("[STT Error] Web Speech API is not supported in this browser.", "warning");
            return;
        }

        try {
            this.recognition = new SpeechRecognition();
        } catch (err) {
            console.error("Failed to construct SpeechRecognition:", err);
            if (window.logDebug) window.logDebug("[STT Error] Failed to initialize SpeechRecognition: " + err.message, "warning");
            this.recognition = null;
            return;
        }

        this.recognition.continuous = true;
        this.recognition.interimResults = true;
        this.recognition.lang = 'en-IN'; // Configured to match the user's Dialogflow CX agent language!

        this.silenceTimer = null;
        this.accumulatedTranscript = '';

        this.recognition.onstart = () => {
            this.recognitionActive = true;
            window.updateVoiceState('LISTENING');
            this.accumulatedTranscript = '';
            if (window.logDebug) window.logDebug("[STT] Speech recognition session started. Speak now!", "success");
        };

        this.recognition.onerror = (event) => {
            this.recognitionActive = false;
            console.error("STT Error:", event.error);
            if (window.logDebug) window.logDebug("[STT Error] " + event.error, "warning");
            if (event.error !== 'no-speech') {
                window.updateVoiceState('IDLE');
            }
        };

        this.recognition.onend = () => {
            this.recognitionActive = false;
            if (window.logDebug) window.logDebug("[STT] Speech recognition session ended.", "info");
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
            // Clear any active silence timers immediately
            this.clearSilenceTimers();
            this.silenceStrikeCount = 0; // Reset silence strike count on successful user response

            // Clear any pending end-of-speech silence timer
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
            if (window.logDebug && currentLiveSpeech.trim()) {
                window.logDebug("[STT Interim] " + currentLiveSpeech, "muted");
            }

            // Set a natural 1.2s silence timeout before processing the speech query to prevent premature cutoffs
            this.silenceTimer = setTimeout(() => {
                const speechOutput = (this.accumulatedTranscript + interimTranscript).trim();
                if (speechOutput.length >= 1) {
                    // Stop recognition before starting to speak
                    this.stopListening();
                    
                    window.addTranscriptBubble('USER', speechOutput);
                    this.processInputText(speechOutput);
                }
            }, 1200);
        };
    }

    startSilenceTimer() {
        this.clearSilenceTimers();

        // Guard 1: never arm before user has spoken at least once (kills welcome-time fires)
        if (!this.hasUserSpokenOnce) {
            console.log("[SilenceTimer] Skipped — user has not spoken yet.");
            return;
        }

        // Guard 2: Don't arm if session has ended (end_conversation, off-topic strike-2, etc.)
        if (this.sessionProfile.state === 'SESSION_ENDED' ||
            this.sessionProfile.state === 'COMPLETED_SUCCESS' ||
            this.lastQuestionAsked === 'COMPLETE') {
            console.log("[SilenceTimer] Skipped arming — session has ended.");
            return;
        }

        // Guard 3: Don't arm if currently speaking (existing P4 check)
        if (this.isPlayingVoice) {
            console.log("[SilenceTimer] Skipped arming — TTS still in progress.");
            return;
        }

        const isProfileComplete = (
            this.sessionProfile.age !== null &&
            this.sessionProfile.smoker !== null &&
            this.sessionProfile.income !== null
        );

        if (!isProfileComplete) {
            // Slot-filling phase: 8-second silence timer
            let targetField = "age";
            if (this.sessionProfile.age === null) {
                targetField = "age";
            } else if (this.sessionProfile.smoker === null) {
                targetField = "whether you smoke";
            } else if (this.sessionProfile.income === null) {
                targetField = "annual income";
            }

            this.silenceTimerId = setTimeout(() => {
                this.silenceStrikeCount++;
                this.stopListening();
                
                let prompt = "";
                if (this.silenceStrikeCount === 1) {
                    prompt = `Are you still there? I asked about your ${targetField}.`;
                } else {
                    prompt = "I'm having trouble hearing you. Should we try again from the start, or call back later?";
                    // End session
                    this.sessionProfile.state = "SESSION_ENDED";
                    this.lastQuestionAsked = "COMPLETE";
                }

                window.addTranscriptBubble('AGENT', prompt);
                this.speak(prompt, () => {
                    if (this.silenceStrikeCount === 1) {
                        this.startListening();
                    } else {
                        // Stop the orb
                        window.updateVoiceState('IDLE');
                        const core = document.querySelector('.voice-orb-core');
                        if (core) {
                            core.style.boxShadow = "none";
                            core.style.background = "radial-gradient(circle, rgba(100,116,139,0.4) 0%, rgba(15,23,42,0.8) 100%)";
                        }
                    }
                }, false);
            }, 8000);
        } else {
            // Post-recommendation phase: 15-second silence timer
            this.postRecSilenceTimerId = setTimeout(() => {
                this.silenceStrikeCount++;
                this.stopListening();

                let prompt = "";
                if (this.silenceStrikeCount === 1) {
                    prompt = "Anything else I can help with? Otherwise I'll wrap up here.";
                } else {
                    prompt = "Thank you so much for exploring options with InsureVoice. Goodbye!";
                    this.sessionProfile.state = "SESSION_ENDED";
                    this.lastQuestionAsked = "COMPLETE";
                }

                window.addTranscriptBubble('AGENT', prompt);
                this.speak(prompt, () => {
                    if (this.silenceStrikeCount === 1) {
                        this.startListening();
                    } else {
                        // Stop the orb and make it inactive
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
    }

    clearSilenceTimers() {
        if (this.silenceTimerId) {
            clearTimeout(this.silenceTimerId);
            this.silenceTimerId = null;
        }
        if (this.postRecSilenceTimerId) {
            clearTimeout(this.postRecSilenceTimerId);
            this.postRecSilenceTimerId = null;
        }
    }

    startListening() {
        if (!this.recognition) return;
        if (this.isPlayingVoice) return;

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

    // 2.2 Local Speech Synthesis
    speak(text, callback, force = true) {
        if (!this.synth) {
            if (callback) callback();
            return;
        }

        console.log(`[TTS-DIAG] speak() called. Pending queue length=${this.synth.pending}, speaking=${this.synth.speaking}, text="${text.substring(0,60)}..."`);

        // Only cancel in-progress speech if the caller forces it (new turn, new user input)
        // Silence-timer prompts should NOT interrupt a bot mid-sentence
        if (force) {
            if (this.synth.speaking || this.synth.pending) {
                console.log("[TTS-DIAG] Canceling existing utterance before new speak()");
                this.synth.cancel();
                this.isPlayingVoice = false;
            }
        } else if (this.isPlayingVoice || this.synth.speaking) {
            console.log("[TTS] Skipping cancel — TTS in progress and force=false");
            return;  // Don't even queue this utterance; it's a follow-up that's no longer needed
        }

        // Strip HTML tags and markdown formatting from speech synthesis text so it speaks naturally
        const cleanText = text
            .replace(/<[^>]*>/g, '')              // strip HTML tags
            .replace(/\*\*([^*]+)\*\*/g, '$1')    // strip **bold** (keep inner text)
            .replace(/__([^_]+)__/g, '$1')        // strip __bold__ alt syntax
            .replace(/\*([^*]+)\*/g, '$1')        // strip *italic*
            .replace(/_([^_]+)_/g, '$1')          // strip _italic_ alt syntax
            .replace(/`([^`]+)`/g, '$1')          // strip `code`
            .replace(/^#+\s+/gm, '')              // strip # heading markers
            .replace(/[*_~`]/g, '')               // strip any leftover markdown chars
            .trim();

        if (!cleanText) {
            if (callback) callback();
            return;
        }

        const utterance = new SpeechSynthesisUtterance(cleanText);
        utterance.lang = 'en-IN'; // Matches en-IN voice settings

        // Keep a reference to prevent garbage collection
        this.currentUtterance = utterance;

        // Try to match Dialogflow CX's en-IN-Wavenet-D Female voice profile
        const voices = this.synth.getVoices();
        const preferredVoice = voices.find(v => v.lang.toLowerCase() === 'en-in' && v.name.toLowerCase().includes('female'));
        if (preferredVoice) {
            utterance.voice = preferredVoice;
        }

        utterance.rate = 1.0;
        utterance.pitch = 1.05; // Slightly higher pitch like their configured pitch: 2 (mapped safely to standard Synthesis scale)

        let speechStarted = false;
        let speechCompleted = false;

        // Fail-safe timeouts
        // 1. If speech doesn't start in 1.5 seconds, force start/callback
        const startTimeout = setTimeout(() => {
            if (!speechStarted && !speechCompleted) {
                console.warn("[TTS Failsafe] Speech failed to start within 1.5s. Forcing callback.");
                handleSpeechEnd();
            }
        }, 1500);

        // 2. Maximum duration fallback based on word count (approx 150 words per minute -> 2.5 words per second)
        const wordCount = cleanText.split(/\s+/).length;
        const estimatedDurationMs = (wordCount / 2.5) * 1000 + 4000; // word count duration + 4s margin
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
            
            // Nullify the current utterance reference
            this.currentUtterance = null;
            
            if (callback) {
                // Ensure callback is called asynchronously to prevent callstack overflow
                setTimeout(callback, 50);
            }
        };

        utterance.onstart = () => {
            speechStarted = true;
            this.isPlayingVoice = true;
            window.updateVoiceState('SPEAKING');
        };

        utterance.onend = () => {
            handleSpeechEnd();
        };

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
        const checkbox = document.getElementById('mode-checkbox');
        const isCloudMode = checkbox && checkbox.checked;

        // Clear existing static bubbles in transcript
        const scroller = document.getElementById('transcript-scroller');
        if (scroller) scroller.innerHTML = '';

        if (isCloudMode) {
            // Silent start: no welcome bubble, no welcome speech.
            // The user's first utterance is the seed for /invoke.
            window.logDebug("[Cloud Mode] Silent start — waiting for user to speak first.", "info");

            if (this.recognition) {
                window.updateVoiceState('LISTENING');
                this.startListening();
            } else {
                window.updateVoiceState('BLOCKED');
                // Keep the existing mic-blocked warning bubble — that's a real error state, not a welcome
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
            return;
        }

        // --- Local Sim Mode (existing behavior, unchanged) ---
        let explanation = "Hello! I am your InsureVoice Sales Advisor. Speak into your microphone and state your insurance requirements. For example, tell me your age, tobacco status, income level, and what coverage you need!";
        this.lastQuestionAsked = 'AGE';

        window.addTranscriptBubble('AGENT', explanation);

        if (this.recognition) {
            this.speak(explanation, () => {
                window.updateVoiceState('LISTENING');
                this.startListening();
            });
        } else {
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
    }

    // ----------------------------------------------------
    // 3. Client-Side ELSER + Compliance Processing
    // ----------------------------------------------------
    processInputText(text) {
        window.updateVoiceState('PROCESSING');
        window.logDebug(`[STT Input] "${text}"`, "info");
        
        const checkbox = document.getElementById('mode-checkbox');
        const isCloudMode = checkbox && checkbox.checked;

        if (isCloudMode) {
            // Mark that user has spoken — unlocks silence timer for future turns
            this.hasUserSpokenOnce = true;

            window.logDebug(`[/invoke] POST → message="${text.substring(0,60)}…" session=${this.invokeSessionId || '(new)'}`, "info");

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
                // Atul's /invoke returns { session_id, response }. Cards data NOT returned in Phase 1.
                this.invokeSessionId = data.session_id;
                const explanation = data.response || "I'm sorry, I didn't catch that. Could you say it again?";

                // Phase 2 hook (currently inactive): if Atul enriches with top3+rejected, render cards
                if (Array.isArray(data.top3) && Array.isArray(data.rejected)) {
                    window.displayRecommendedProducts(data.top3, data.rejected);
                } else {
                    window.displayRecommendedProducts([], []);
                }

                window.addTranscriptBubble('AGENT', explanation);
                this.speak(explanation, () => {
                    this.startListening();
                });
            })
            .catch(err => {
                console.error("Cloud Run /invoke failed:", err);
                window.logDebug(`[/invoke ERROR] ${err.message}. Falling back to Local Sim for this turn only.`, "warning");

                // Fallback to Local Sim for this single turn (preserves demo continuity)
                if (checkbox) checkbox.checked = false;
                const labelLocal = document.getElementById('label-local');
                const labelCloud = document.getElementById('label-cloud');
                if (labelLocal) labelLocal.classList.add('active');
                if (labelCloud) labelCloud.classList.remove('active');

                window.logDebug("[System] Fallback to Local Sim for this turn.", "info");
                this.processInputText(text);
            });
            return;
        }

        // --- Local Simulation Mode State Machine ---

        // Check if slots are already fully filled
        const isProfileComplete = (
            this.sessionProfile.age !== null &&
            this.sessionProfile.smoker !== null &&
            this.sessionProfile.income !== null
        );

        setTimeout(() => {
            if (isProfileComplete) {
                // We are in the post-recommendation flow. Use the local intent classifier!
                const intent = this.classifyLocalIntent(text);
                console.log(`[Local Post-Rec Intent Classify] Input: '${text}' -> Classified Intent: '${intent}'`);

                // Always run search, compliance, and suitability ranking to keep cards active on the frontend
                const candidates = this.simulateELSERMatches(text);
                const complianceReport = this.runComplianceCheck(candidates, this.sessionProfile);
                const rankedPassed = this.runSuitabilityRanking(complianceReport.passed, this.sessionProfile);

                let voiceExplanation = "";

                if (intent === "start_application") {
                    this.sessionProfile.state = "SESSION_ENDED";
                    this.lastQuestionAsked = "COMPLETE";
                    this.clearSilenceTimers();   // NEW — kill any pending silence timer
                    voiceExplanation = "Fantastic! You have made an incredibly smart decision to secure your family's financial future today. I am sending a secure, 1-click checkout link to your registered mobile number, and our senior representative will call you in 5 minutes to complete the quick documentation. Thank you for choosing InsureVoice!";
                    
                    window.addTranscriptBubble('AGENT', voiceExplanation);
                    this.speak(voiceExplanation, () => {
                        window.updateVoiceState('IDLE');
                    });
                    return;
                }
                
                else if (intent === "compare_products") {
                    voiceExplanation = this.generateLocalComparisonResponse(rankedPassed, text);
                    window.addTranscriptBubble('AGENT', voiceExplanation);
                    this.speak(voiceExplanation, () => {
                        this.startListening();
                    });
                    return;
                }

                else if (intent === "policy_qna") {
                    const qnaResult = this.getLocalPolicyQnaAnswer(text, rankedPassed);
                    voiceExplanation = qnaResult.answer;
                    window.addTranscriptBubble('AGENT', voiceExplanation);
                    
                    this.speak(voiceExplanation, () => {
                        // Do NOT end session even when escalated; user can keep asking
                        this.startListening();
                    });
                    return;
                }

                else if (intent === "more_options") {
                    let voiceExplanation = "";
                    if (rankedPassed.length >= 2) {
                        const topName = rankedPassed[0].name || "your top match";
                        const otherNames = rankedPassed.slice(1, 3).map(p => p.name).filter(Boolean).join(", ");
                        if (otherNames) {
                            voiceExplanation = `Besides ${topName}, you also qualify for ${otherNames}. All options are visible in the cards on screen. Would you like more detail on any of these, or shall we proceed with the application?`;
                        } else {
                            voiceExplanation = `You've already seen our top recommendation, ${topName}. I can relax some filters to find more options if you'd like — just say 'show me more'.`;
                        }
                    } else if (rankedPassed.length === 1) {
                        voiceExplanation = `You've seen our best match, ${rankedPassed[0].name}. I can relax some filters to look for more variety if you'd like.`;
                    } else {
                        voiceExplanation = "I'm not finding additional matches in your eligibility band right now. Want me to broaden the search criteria?";
                    }
                    window.addTranscriptBubble('AGENT', voiceExplanation);
                    this.speak(voiceExplanation, () => {
                        this.startListening();
                    });
                    return;
                }

                else if (intent === "end_conversation") {
                    this.sessionProfile.state = "SESSION_ENDED";
                    this.lastQuestionAsked = "COMPLETE";
                    this.clearSilenceTimers();   // NEW — kill any pending silence timer
                    voiceExplanation = "Thank you so much for exploring insurance options with InsureVoice. Have a wonderful day ahead, and stay safe. Goodbye!";
                    window.addTranscriptBubble('AGENT', voiceExplanation);
                    this.speak(voiceExplanation, () => {
                        window.updateVoiceState('IDLE');
                        // Do NOT call startListening() here — session is over
                    });
                    return;
                }

                else if (intent === "off_topic") {
                    if (!this.validationStrikes.off_topic) {
                        this.validationStrikes.off_topic = 0;
                    }
                    this.validationStrikes.off_topic += 1;
                    const strike = this.validationStrikes.off_topic;
                    if (strike === 1) {
                        voiceExplanation = "I can certainly help you with general questions, but today I am here to assist you with your insurance matches. Would you like to proceed with the application or ask any questions about these plans?";
                    } else {
                        voiceExplanation = "Since we are off topic, I will end our session now. Please feel free to call us back when you want to explore our insurance policies. Goodbye!";
                        this.sessionProfile.state = "SESSION_ENDED";
                        this.lastQuestionAsked = "COMPLETE";
                        this.clearSilenceTimers();   // NEW — kill any pending silence timer
                    }

                    window.addTranscriptBubble('AGENT', voiceExplanation);
                    this.speak(voiceExplanation, () => {
                        if (strike < 2) {
                            this.startListening();
                        } else {
                            window.updateVoiceState('IDLE');
                        }
                    });
                    return;
                }

                else {  // silence / fallback
                    voiceExplanation = "I am still here to help you. Do you have any questions about the recommended plans, or would you like to compare them?";
                    window.addTranscriptBubble('AGENT', voiceExplanation);
                    this.speak(voiceExplanation, () => {
                        this.startListening();
                    });
                    return;
                }
            } else {
                // PROGRESSIVE SLOT-FILLING & ROBUST VALIDATION PHASE
                
                // Determine the first un-filled slot (progressive order: Age -> Smoker -> Income)
                let targetSlot = null;
                if (this.sessionProfile.age === null) {
                    targetSlot = "age";
                } else if (this.sessionProfile.smoker === null) {
                    targetSlot = "smoker";
                } else if (this.sessionProfile.income === null) {
                    targetSlot = "income";
                }

                // Standard profile slot extraction (returns { age, smoker, income })
                const extracted = this.extractProfile(text, targetSlot.toUpperCase());
                console.log("Extracted on this turn:", extracted);

                // Check for name introduction anywhere in text
                const namePattern = /\b(?:my name is|i am|i'm|this is|call me)\s+([A-Za-z]+)\b/i;
                const nameMatch = text.match(namePattern);
                if (nameMatch && !this.userName) {
                    const rawName = nameMatch[1];
                    this.userName = rawName.charAt(0).toUpperCase() + rawName.slice(1).toLowerCase();
                    this.sessionProfile.userName = this.userName;
                    window.logDebug(`[Name Extracted] User name saved: ${this.userName}`, "success");
                }

                // 1. Conversational Greeting or Name Introduction Bypass (do not strike!)
                const lowerInput = text.toLowerCase().trim();
                const standardGreetings = ["hello", "hi", "hey", "greetings", "good morning", "good afternoon", "good evening", "how are you", "how's it going", "yo"];
                const isGreeting = standardGreetings.some(g => lowerInput === g || lowerInput.startsWith(g + " ") || lowerInput.startsWith(g + ","));
                const isIntroOnly = nameMatch && (lowerInput.replace(namePattern, "").trim().length < 5 || standardGreetings.some(g => lowerInput.includes(g)));

                if ((isGreeting || isIntroOnly) && extracted.age === null && extracted.smoker === null && extracted.income === null) {
                    let greetingPrompt = "";
                    if (this.userName) {
                        greetingPrompt = `Hello ${this.userName}! It's a pleasure to speak with you. To find the absolute best insurance recommendations for you, could you please tell me how old you are?`;
                    } else {
                        greetingPrompt = "Hello! It's a pleasure to speak with you. To find the absolute best insurance recommendations for you, could you please tell me how old you are?";
                    }
                    this.lastQuestionAsked = 'AGE';
                    window.addTranscriptBubble('AGENT', greetingPrompt);
                    this.speak(greetingPrompt, () => {
                        this.startListening();
                    });
                    return;
                }

                // 2. Conversational "not sure" Age check
                if (targetSlot === "age") {
                    const notSureWords = ["not sure", "don't know", "dont know", "no idea", "not certain", "cannot say", "can't say", "maybe"];
                    if (notSureWords.some(word => lowerInput.includes(word))) {
                        const responsePrompt = "That is completely fine! Roughly how old are you? A close estimate is perfectly fine to find the best matched plans for you.";
                        window.addTranscriptBubble('AGENT', responsePrompt);
                        this.speak(responsePrompt, () => {
                            this.startListening();
                        });
                        return;
                    }
                }

                // Concurrently apply any valid extracted slots to profile (cross-slot concurrent extraction)
                let hadAge = this.sessionProfile.age !== null;
                let hadSmoker = this.sessionProfile.smoker !== null;
                let hadIncome = this.sessionProfile.income !== null;

                if (extracted.age !== null && typeof extracted.age === 'number' && extracted.age >= 18 && extracted.age <= 95) {
                    this.sessionProfile.age = parseInt(extracted.age);
                    this.validationStrikes.age = 0; // reset strikes
                }
                if (extracted.smoker !== null) {
                    this.sessionProfile.smoker = extracted.smoker;
                    this.validationStrikes.smoker = 0; // reset strikes
                }
                if (extracted.income !== null && typeof extracted.income === 'number' && extracted.income > 0) {
                    this.sessionProfile.income = parseFloat(extracted.income);
                    this.validationStrikes.income = 0; // reset strikes
                }

                // Check if any new slot got filled on this turn
                let anyUpdated = (
                    (!hadAge && this.sessionProfile.age !== null) ||
                    (!hadSmoker && this.sessionProfile.smoker !== null) ||
                    (!hadIncome && this.sessionProfile.income !== null)
                );

                let targetStillNull = this.sessionProfile[targetSlot] === null;

                // 3. Strict Slot validation and Multi-strike handling if target slot was not filled
                if (targetStillNull) {
                    if (anyUpdated) {
                        // They provided some other slot information out of order. Do not strike! Acknowledge and prompt for remaining.
                        let responsePrompt = "";
                        if (targetSlot === "age") {
                            responsePrompt = this.userName 
                                ? `Got it, I've noted that in your profile, ${this.userName}. To proceed with finding the best insurance plans, could you please tell me how old you are?`
                                : "Got it, I've noted that in your profile. To proceed with finding the best insurance plans, could you please tell me how old you are?";
                        } else if (targetSlot === "smoker") {
                            responsePrompt = this.userName
                                ? `Got it, thank you, ${this.userName}. Do you smoke, consume nicotine, or use any tobacco products?`
                                : "Got it, thank you. Do you smoke, consume nicotine, or use any tobacco products?";
                        } else if (targetSlot === "income") {
                            responsePrompt = this.userName
                                ? `Perfect, thank you, ${this.userName}. Lastly, what is your approximate annual income or salary?`
                                : "Perfect, thank you. Lastly, what is your approximate annual income or salary?";
                        }
                        window.addTranscriptBubble('AGENT', responsePrompt);
                        this.speak(responsePrompt, () => {
                            this.startListening();
                        });
                        return;
                    } else {
                        // No new valid slot filled. Increment validation strike for the target slot.
                        if (targetSlot === "age") {
                            this.validationStrikes.age += 1;
                            const strike = this.validationStrikes.age;
                            let responsePrompt = "";
                            if (strike === 1) {
                                responsePrompt = "I'm sorry, we can only provide recommendations for ages between 18 and 95. Could you please tell me your age again?";
                            } else if (strike === 2) {
                                responsePrompt = "To proceed, we need a valid age between 18 and 95. Let me redirect you to a human specialist, or would you like to try entering your age one more time?";
                            } else {
                                responsePrompt = "I'm sorry, since we haven't been able to verify a valid age, I will have to end our call for now. Please feel free to call us back when you are ready. Goodbye!";
                                this.sessionProfile.state = "SESSION_ENDED";
                                this.lastQuestionAsked = "COMPLETE";
                                this.clearSilenceTimers();   // NEW — kill any pending silence timer
                            }
                            
                            window.addTranscriptBubble('AGENT', responsePrompt);
                            this.speak(responsePrompt, () => {
                                if (strike < 3) {
                                    this.startListening();
                                } else {
                                    window.updateVoiceState('IDLE');
                                }
                            });
                            return;
                        }

                        else if (targetSlot === "smoker") {
                            this.validationStrikes.smoker += 1;
                            const strike = this.validationStrikes.smoker;
                            let responsePrompt = "";
                            if (strike === 1) {
                                responsePrompt = "I'm sorry, I didn't understand if you use tobacco or not. Do you smoke or use any nicotine products? A simple yes or no is fine.";
                            } else if (strike === 2) {
                                responsePrompt = "I still need to confirm your tobacco status. Let me connect you with an advisor, or could you please clarify if you smoke?";
                            } else {
                                responsePrompt = "Since we cannot confirm your tobacco status, I will end this session. Goodbye!";
                                this.sessionProfile.state = "SESSION_ENDED";
                                this.lastQuestionAsked = "COMPLETE";
                                this.clearSilenceTimers();   // NEW — kill any pending silence timer
                            }
                            
                            window.addTranscriptBubble('AGENT', responsePrompt);
                            this.speak(responsePrompt, () => {
                                if (strike < 3) {
                                    this.startListening();
                                } else {
                                    window.updateVoiceState('IDLE');
                                }
                            });
                            return;
                        }

                        else if (targetSlot === "income") {
                            this.validationStrikes.income += 1;
                            const strike = this.validationStrikes.income;
                            let responsePrompt = "";
                            if (strike === 1) {
                                responsePrompt = "I didn't quite catch that. Your annual income must be a positive number. Could you please state your approximate annual income again?";
                            } else if (strike === 2) {
                                responsePrompt = "We require a valid income value to check compliance. Let me connect you with a specialist, or would you like to state it one more time?";
                            } else {
                                responsePrompt = "I'm sorry, as we cannot verify your income, I will have to end this call. Have a nice day! Goodbye!";
                                this.sessionProfile.state = "SESSION_ENDED";
                                this.lastQuestionAsked = "COMPLETE";
                                this.clearSilenceTimers();   // NEW — kill any pending silence timer
                            }
                            
                            window.addTranscriptBubble('AGENT', responsePrompt);
                            this.speak(responsePrompt, () => {
                                if (strike < 3) {
                                    this.startListening();
                                } else {
                                    window.updateVoiceState('IDLE');
                                }
                            });
                            return;
                        }
                    }
                }

                // Re-check if all slots are complete now after this extraction turn
                const isProfileCompleteNow = (
                    this.sessionProfile.age !== null &&
                    this.sessionProfile.smoker !== null &&
                    this.sessionProfile.income !== null
                );

                window.logDebug(`[Profile Slots] Age: ${this.sessionProfile.age !== null ? this.sessionProfile.age : 'null'}, Smoker: ${this.sessionProfile.smoker !== null ? (this.sessionProfile.smoker ? 'Yes' : 'No') : 'null'}, Income: ${this.sessionProfile.income !== null ? '₹' + (this.sessionProfile.income/100000).toFixed(1) + 'L' : 'null'}`, "info");

                if (isProfileCompleteNow) {
                    this.sessionProfile.state = "OFFER_PRESENTED";
                    this.lastQuestionAsked = "OFFER_PRESENTED";
                    
                    // All slots are successfully filled! Run ELSER + Compliance Report
                    const matchedCandidates = this.simulateELSERMatches(text);
                    const complianceReport = this.runComplianceCheck(matchedCandidates, this.sessionProfile);
                    
                    // Run custom suitability ranking
                    const rankedPassed = this.runSuitabilityRanking(complianceReport.passed, this.sessionProfile);
                    
                    // Explains compliance matches & rejections
                    const voiceExplanation = this.generateVoiceExplanation({ passed: rankedPassed, rejected: complianceReport.rejected }, this.sessionProfile);

                    // Update cards slider live on UI
                    window.displayRecommendedProducts(rankedPassed, complianceReport.rejected);

                    window.addTranscriptBubble('AGENT', voiceExplanation);
                    this.speak(voiceExplanation, () => {
                        this.startListening();
                    });
                } else {
                    // Progressive slot filling question
                    let question = "";
                    if (this.sessionProfile.age === null) {
                        question = this.userName 
                            ? `Welcome to InsureVoice, ${this.userName}! To find the absolute best insurance recommendations for you, could you please tell me how old you are?`
                            : "Welcome to InsureVoice! To find the absolute best insurance recommendations for you, could you please tell me how old you are?";
                        this.lastQuestionAsked = 'AGE';
                    } else if (this.sessionProfile.smoker === null) {
                        question = this.userName
                            ? `Thank you, ${this.userName}. And do you smoke, consume nicotine, or use any tobacco products?`
                            : "Thank you. And do you smoke, consume nicotine, or use any tobacco products?";
                        this.lastQuestionAsked = 'SMOKER';
                    } else if (this.sessionProfile.income === null) {
                        question = this.userName
                            ? `Got it, ${this.userName}. Lastly, what is your approximate annual income or salary? This helps us check premium eligibility and compliance.`
                            : "Got it. Lastly, what is your approximate annual income or salary? This helps us check premium eligibility and compliance.";
                        this.lastQuestionAsked = 'INCOME';
                    }
                    
                    window.addTranscriptBubble('AGENT', question);
                    this.speak(question, () => {
                        this.startListening();
                    });
                }
            }
        }, 1500); // 1.5s delay to show off "Processing" orb animation
    }

    // --- Helper Methods for Local Intent, Comparisons, and Policy QnA ---

    classifyLocalIntent(userText) {
        const lower = userText.toLowerCase();
        
        // 1. end_conversation
        if (/\b(goodbye|bye|thank you|thanks|no thank you|no thanks|that is all|that's all|i am done|i'm done|exit|stop|nah that's fine|no we are good|no that's all)\b/.test(lower)) {
            return "end_conversation";
        }
        
        // 2. start_application
        if (/\b(buy|proceed|apply|checkout|link|get this|interested|yes i want|i would like to buy|sign me up)\b/.test(lower)) {
            return "start_application";
        }
        
        // 3. compare_products
        if (/\b(compare|difference|versus|vs|better|cheaper|compared|differences)\b/.test(lower)) {
            return "compare_products";
        }
        
        // 4. more_options
        if (/\b(more option|other plan|other product|something else|other option|other options|different policy|different plans)\b/.test(lower)) {
            return "more_options";
        }
        
        // 5. policy_qna keywords
        const qnaKeywords = ["claim", "late payment", "grace period", "tax benefit", "section 80", "80c", "80d", "renew", "lapse", "revive", "cover", "benefit", "rider", "premium", "hospital", "illness", "cashless", "icu", "limit", "exclusion", "exclusions", "how much", "cost", "features", "highlight", "highlights"];
        if (qnaKeywords.some(keyword => lower.includes(keyword)) || lower.includes("?") || lower.startsWith("how") || lower.includes("what") || lower.includes("does")) {
            return "policy_qna";
        }
        
        // 6. off_topic
        const offTopicKeywords = ["weather", "sports", "football", "soccer", "cricket", "president", "movie", "song", "music", "joke", "chatter", "personal", "who are you", "what is your name", "tell me a joke"];
        if (offTopicKeywords.some(keyword => lower.includes(keyword))) {
            return "off_topic";
        }
        
        // Default fallback to policy_qna
        return "policy_qna";
    }

    generateLocalComparisonResponse(passedProducts, userText = "") {
        if (passedProducts.length < 2) {
            return "I have recommended our top-matched plan for you. We don't have another eligible plan in our catalog to compare side-by-side right now, but I can tell you more about this plan's features if you'd like!";
        }
        
        // Try to match products by name from user query
        let prod1 = null;
        let prod2 = null;
        if (userText) {
            const lower = userText.toLowerCase();
            // Score each passed product by how many name tokens appear in userText
            const scored = [];
            for (let p of passedProducts) {
                const nameTokens = p.name.toLowerCase().split(/\s+/).filter(t => t.length > 2);
                const score = nameTokens.reduce((acc, t) => acc + (lower.includes(t) ? 1 : 0), 0);
                if (score > 0) {
                    scored.push({ score, product: p });
                }
            }
            scored.sort((a, b) => b.score - a.score);
            if (scored.length >= 2) {
                prod1 = scored[0].product;
                prod2 = scored[1].product;
            }
        }

        // Fallback to top-2 if name match didn't yield 2 products
        if (prod1 === null || prod2 === null) {
            prod1 = passedProducts[0];
            prod2 = passedProducts[1];
        }
        
        const sanitizeProduct = (p) => {
            const copy = { ...p };
            if (copy.type === undefined) {
                copy.type = (copy.product_type || 'Insurance Plan').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
            }
            if (copy.premium === undefined) {
                if (copy.premium_min_monthly !== undefined && copy.premium_max_monthly !== undefined) {
                    copy.premium = `₹${copy.premium_min_monthly.toLocaleString()} - ₹${copy.premium_max_monthly.toLocaleString()}/mo`;
                } else if (copy.premium_min_monthly !== undefined) {
                    copy.premium = `₹${copy.premium_min_monthly.toLocaleString()}/mo`;
                } else {
                    copy.premium = "N/A";
                }
            }
            if (copy.sum_assured === undefined) {
                if (copy.max_sum_assured !== undefined) {
                    copy.sum_assured = `₹${copy.max_sum_assured.toLocaleString()}`;
                } else {
                    copy.sum_assured = "N/A";
                }
            }
            if (copy.highlights === undefined) {
                if (copy.key_feature) {
                    copy.highlights = [copy.key_feature];
                } else if (copy.sales_pitch) {
                    copy.highlights = [copy.sales_pitch];
                } else {
                    copy.highlights = [copy.description ? copy.description.substring(0, 60) : ''];
                }
            }
            return copy;
        };

        const prod1Sanitized = sanitizeProduct(prod1);
        const prod2Sanitized = sanitizeProduct(prod2);

        const tableHtml = `
        <div class="comparison-table-wrapper" style="margin-top: 10px; overflow-x: auto; width: 100%;">
            <table class="comparison-table" style="width: 100%; border-collapse: collapse; font-size: 0.85rem; color: #f3f4f6; text-align: left; background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.1); border-radius: 8px;">
                <thead>
                    <tr style="background: rgba(255,255,255,0.06); border-bottom: 1px solid rgba(255,255,255,0.1);">
                        <th style="padding: 8px 10px; font-weight: 700;">Feature</th>
                        <th style="padding: 8px 10px; font-weight: 700; color: #22d3ee;">${prod1Sanitized.name}</th>
                        <th style="padding: 8px 10px; font-weight: 700; color: #a855f7;">${prod2Sanitized.name}</th>
                    </tr>
                </thead>
                <tbody>
                    <tr style="border-bottom: 1px solid rgba(255,255,255,0.05);">
                        <td style="padding: 8px 10px; font-weight: 600; color: #9ca3af;">Type</td>
                        <td style="padding: 8px 10px;">${prod1Sanitized.type}</td>
                        <td style="padding: 8px 10px;">${prod2Sanitized.type}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid rgba(255,255,255,0.05);">
                        <td style="padding: 8px 10px; font-weight: 600; color: #9ca3af;">Premium</td>
                        <td style="padding: 8px 10px; font-weight: 700; color: #10b981;">${prod1Sanitized.premium}</td>
                        <td style="padding: 8px 10px; font-weight: 700; color: #10b981;">${prod2Sanitized.premium}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid rgba(255,255,255,0.05);">
                        <td style="padding: 8px 10px; font-weight: 600; color: #9ca3af;">Coverage</td>
                        <td style="padding: 8px 10px;">${prod1Sanitized.sum_assured}</td>
                        <td style="padding: 8px 10px;">${prod2Sanitized.sum_assured}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid rgba(255,255,255,0.05);">
                        <td style="padding: 8px 10px; font-weight: 600; color: #9ca3af;">Highlights</td>
                        <td style="padding: 8px 10px;">
                            <ul style="margin: 0; padding-left: 15px; font-size: 0.78rem;">
                                ${prod1Sanitized.highlights.map(h => `<li>${h}</li>`).join('')}
                            </ul>
                        </td>
                        <td style="padding: 8px 10px;">
                            <ul style="margin: 0; padding-left: 15px; font-size: 0.78rem;">
                                ${prod2Sanitized.highlights.map(h => `<li>${h}</li>`).join('')}
                            </ul>
                        </td>
                    </tr>
                </tbody>
            </table>
        </div>
        `;
        
        const vocalSummary = `Let's compare ${prod1Sanitized.name} and ${prod2Sanitized.name} side-by-side. ${prod1Sanitized.name} costs ${prod1Sanitized.premium} for ${prod1Sanitized.sum_assured} coverage, whereas ${prod2Sanitized.name} costs ${prod2Sanitized.premium} for ${prod2Sanitized.sum_assured} coverage. I have displayed a detailed glassmorphic side-by-side comparison table in your chat bubble. Which one looks better to you?`;
        return vocalSummary + "\n\n" + tableHtml;
    }

    getLocalPolicyQnaAnswer(userText, passedProducts) {
        const lower = userText.toLowerCase();
        
        // Escalation keywords for claims, late payments, grace periods, Section 80C/80D tax benefits
        const unavailableKeywords = ["claim", "late payment", "grace period", "tax benefit", "section 80", "80c", "80d", "renew", "lapse", "revive"];
        if (unavailableKeywords.some(keyword => lower.includes(keyword))) {
            return {
                answer: "That's a very important question. Specific details like claim filing, grace period rules, and tax benefit Section 80 coverage will be confirmed by our specialist team in your follow-up call. Anything else I can answer right now about the matched products — features, premiums, coverage, or exclusions?",
                escalated: true
            };
        }
        
        // Conversational feature Q&As
        if (lower.includes("cancer") || lower.includes("illness") || lower.includes("critical")) {
            return {
                answer: "Our 'Critical Illness Guard' is a specialized policy paying a lump sum of ₹25 Lakhs upon diagnostic detection of 36 major critical illnesses, including cancer, stroke, kidney failure, or bypass surgery. It includes a premium waiver on diagnosis so your family is fully protected.",
                escalated: false
            };
        }
        if (lower.includes("hospital") || lower.includes("cashless") || lower.includes("icu") || lower.includes("medical") || lower.includes("health")) {
            return {
                answer: "The 'Comprehensive Health Shield' is a full-scale medical plan. It covers in-patient hospitalization, ICU expenses, pre/post-operative treatments, and annual health checkups for the entire family. It offers cashless hospitalization with zero co-payment.",
                escalated: false
            };
        }
        if (lower.includes("ulip") || lower.includes("wealth") || lower.includes("investment") || lower.includes("market") || lower.includes("stock")) {
            return {
                answer: "Our 'ULIP Wealth Growth Builder' is a high-yield investment plan. It directs your premiums directly into stock market equity funds for capital appreciation. It features market-linked returns and free fund switching, with a five-year lock-in.",
                escalated: false
            };
        }
        if (lower.includes("child") || lower.includes("education") || lower.includes("future") || lower.includes("marriage")) {
            return {
                answer: "The 'Secure Child Future Plan' is an endowment savings and assurance policy designed to fund children's higher education and marriage milestones. It includes a Premium Waiver Rider, securing their future even in the parent's absence.",
                escalated: false
            };
        }
        if (lower.includes("term shield") || lower.includes("crore") || lower.includes("working professional")) {
            return {
                answer: "The 'Term Shield Pro' is our top pure term protection plan, offering a high sum assured of ₹1 Crore. It is ideal for working professionals and breadwinners, providing terminal illness riders and Section 80C tax savings at a low premium of ₹1,200 per month.",
                escalated: false
            };
        }
        if (lower.includes("smart protect") || lower.includes("return of premium") || lower.includes("guaranteed")) {
            return {
                answer: "The 'Smart Protect Life' plan offers comprehensive term protection with an attractive Return of Premium benefit. If you stay healthy until the end of the policy term, 100% of your paid premiums are returned to you. It also includes an accidental death benefit.",
                escalated: false
            };
        }
        
        if (passedProducts.length > 0) {
            const topProd = passedProducts[0];
            return {
                answer: `The recommended ${topProd.name} is a ${topProd.type} plan. It offers a coverage of ${topProd.sum_assured} with a premium of ${topProd.premium}. Key highlights include: ${topProd.highlights.join(', ')}. Would you like to proceed with the application?`,
                escalated: false
            };
        }
        
        return {
            answer: "I would be happy to help you with any questions about features, premiums, coverage, or exclusions. Feel free to ask about our matched insurance plans!",
            escalated: false
        };
    }

    // Heuristics for basic info extraction from spoken English (returns null if unmentioned)
    extractProfile(text, lastQuestion) {
        const lower = text.toLowerCase();
        
        const age = this.extractAgeFromText(lower, lastQuestion);
        const smoker = this.extractSmokerFromText(lower, lastQuestion);
        const income = this.extractIncomeFromText(lower, lastQuestion);
        
        return { age, smoker, income };
    }

    extractAgeFromText(lower, lastQuestion) {
        // Remove income/currency expressions from text before trying to extract age to prevent false positive matches
        let cleanText = lower.replace(/\b\d+(?:\.\d+)?\s*(?:lakh|lac|lakhs|lacs|l)\b/g, '');
        cleanText = cleanText.replace(/(?:income|salary|earn|earning|earns)\s*(?:is|of|around)?\s*(?:₹|rs\.?)?\s*\d+(?:\.\d+)?/g, '');
        
        let age = null;
        if (lastQuestion === 'AGE') {
            // Since we are explicitly asking for age, try parsing spoken number words
            const tens = ['twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty'];
            const ones = ['one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine'];
            const teens = ['eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen'];
            const exacts = {'ten': 10, 'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50, 'sixty': 60, 'seventy': 70, 'eighty': 80};
            
            // Look for combinations like "thirty five", "forty-two"
            for (let t of tens) {
                for (let o of ones) {
                    const regex = new RegExp(`\\b${t}[-\\s]${o}\\b`);
                    if (regex.test(cleanText)) {
                        age = exacts[t] + ones.indexOf(o) + 1;
                        break;
                    }
                }
                if (age) break;
            }
            
            // Teens
            if (!age) {
                for (let teen of teens) {
                    if (new RegExp(`\\b${teen}\\b`).test(cleanText)) {
                        age = teens.indexOf(teen) + 11;
                        break;
                    }
                }
            }
            
            // Exact tens
            if (!age) {
                for (let ex of Object.keys(exacts)) {
                    if (new RegExp(`\\b${ex}\\b`).test(cleanText)) {
                        age = exacts[ex];
                        break;
                    }
                }
            }
            
            // Standalone digits
            if (!age) {
                const digitMatch = cleanText.match(/\b([1-8][0-9])\b/);
                if (digitMatch) {
                    age = parseInt(digitMatch[1]);
                }
            }
            
            if (age !== null) {
                window.logDebug(`[Age Extracted] Parsed age: ${age} from phrase context`, "success");
            }
        } else {
            // Not explicitly asking for age: only extract if they explicitly use age-related words
            const explicitMatch = cleanText.match(/(?:i am|i'm|my age is|aged|years old|of)\s*(\d+)/) || 
                                  cleanText.match(/(\d+)\s*(?:years|yrs)/);
            if (explicitMatch) {
                const parsedAge = parseInt(explicitMatch[1]);
                if (parsedAge >= 18 && parsedAge <= 85) {
                    age = parsedAge;
                    window.logDebug(`[Age Extracted] Explicit age matching: ${age}`, "success");
                }
            } else {
                // Spoken explicit word check
                const ageWordsMatch = cleanText.match(/(?:i am|i'm|my age is|aged)\s+([a-z\s-]+)/);
                if (ageWordsMatch) {
                    const parsedAge = this.extractAgeFromText(ageWordsMatch[1], 'AGE'); // reuse parser with AGE context
                    if (parsedAge) {
                        age = parsedAge;
                        window.logDebug(`[Age Extracted] Explicit spoken age matching: ${age}`, "success");
                    }
                }
            }
        }
        return age;
    }

    extractSmokerFromText(lower, lastQuestion) {
        let smoker = null;
        if (lastQuestion === 'SMOKER') {
            // Conversational direct answers to smoking questions
            const positiveAnswers = [
                'yes', 'yeah', 'yep', 'yus', 'i do', 'smoke', 'smoker', 'smoking', 'tobacco', 'nicotine', 'cigarette', 'sometimes', 'always', 'habit'
            ];
            const negativeAnswers = [
                'no', 'dont', "don't", 'not', 'never', 'non', 'nope', 'nah', "i don't", "i dont", 'no i do not', 'no i dont', 'healthy'
            ];
            
            // Check negatives first to avoid false positives (like "no, I don't smoke" matching "smoke")
            let matchesNegative = false;
            for (let neg of negativeAnswers) {
                if (new RegExp(`\\b${neg}\\b`).test(lower)) {
                    matchesNegative = true;
                    break;
                }
            }
            
            if (matchesNegative) {
                smoker = false;
                window.logDebug(`[Tobacco Extracted] Conversational NEGATIVE recognized (Non-Smoker)`, "success");
            } else {
                let matchesPositive = false;
                for (let pos of positiveAnswers) {
                    if (new RegExp(`\\b${pos}\\b`).test(lower)) {
                        matchesPositive = true;
                        break;
                    }
                }
                if (matchesPositive) {
                    smoker = true;
                    window.logDebug(`[Tobacco Extracted] Conversational POSITIVE recognized (Smoker)`, "success");
                }
            }
        } else {
            // Regular check: only extract if explicitly smoking-related keywords are mentioned
            if (lower.includes('smoke') || lower.includes('smoker') || lower.includes('smoking') || lower.includes('cigarette')) {
                const hasNegation = lower.includes("don't") || 
                                    lower.includes("dont") || 
                                    lower.includes("not") || 
                                    lower.includes("never") || 
                                    lower.includes("non");
                smoker = !hasNegation;
                window.logDebug(`[Tobacco Extracted] Keyword smoker recognized: ${smoker}`, "success");
            }
        }
        return smoker;
    }

    extractIncomeFromText(lower, lastQuestion) {
        let income = null;
        if (lastQuestion === 'INCOME') {
            income = this.parseIncomeVal(lower);
            // If they just say a plain number (e.g., "5" or "10" or "six" answering "what is your income in lakhs?")
            if (income === null) {
                const singleNumMatch = lower.match(/\b(\d+(?:\.\d+)?)\b/);
                if (singleNumMatch) {
                    const val = parseFloat(singleNumMatch[1]);
                    if (val < 100) { // Assume lakhs if they say a small number like "5" or "12"
                        income = Math.round(val * 100000);
                    } else {
                        income = Math.round(val);
                    }
                } else {
                    // Try exact spoken word (e.g., "five", "ten")
                    const singleWordMatch = lower.match(/\b(one|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen|twenty|thirty|forty|fifty)\b/);
                    if (singleWordMatch) {
                        const wordsMap = {
                            'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
                            'twelve': 12, 'fifteen': 15, 'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50
                        };
                        const val = wordsMap[singleWordMatch[1]];
                        if (val) income = val * 100000;
                    }
                }
            }
            if (income !== null) {
                window.logDebug(`[Income Extracted] Parsed income: ₹${(income/100000).toFixed(1)}L from prompt context`, "success");
            }
        } else {
            // General check: only if income-related keywords are mentioned
            if (lower.includes('income') || lower.includes('salary') || lower.includes('earn') || lower.includes('lakh') || lower.includes('lac') || lower.includes('lacs') || lower.includes('lakhs')) {
                income = this.parseIncomeVal(lower);
                if (income !== null) {
                    window.logDebug(`[Income Extracted] Keyword income recognized: ₹${(income/100000).toFixed(1)}L`, "success");
                }
            }
        }
        return income;
    }

    parseIncomeVal(lowerText) {
        // Try lakhs
        const lakhMatch = lowerText.match(/(\d+(?:\.\d+)?)\s*(?:lakh|lac|lakhs|lacs|l)\b/i);
        if (lakhMatch) {
            return Math.round(parseFloat(lakhMatch[1]) * 100000);
        }
        
        // Try numeric text like "five lakh", "ten lakh"
        const lakhWordsMatch = lowerText.match(/(one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|twenty-five|thirty|forty|fifty|sixty|seventy|eighty|ninety)\s*(?:lakh|lac|lakhs|lacs)\b/);
        if (lakhWordsMatch) {
            const wordsMap = {
                'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
                'fifteen': 15, 'twenty': 20, 'twenty-five': 25, 'thirty': 30, 'forty': 40, 'fifty': 50, 'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90
            };
            const val = wordsMap[lakhWordsMatch[1]];
            if (val) return val * 100000;
        }
        
        // Standard number (e.g. 500000)
        const numberMatch = lowerText.match(/\b(\d{5,8})\b/);
        if (numberMatch) {
            return parseInt(numberMatch[1]);
        }
        
        return null;
    }

    // Simulates Elastic ELSER sparse semantic text expansion by weight matching
    simulateELSERMatches(query) {
        const lowerQuery = query.toLowerCase();
        const scoredProducts = INSURANCE_PRODUCTS.map(prod => {
            let matchScore = 15; // baseline
            
            // ELSER semantic synonyms map
            const semanticSynonyms = {
                "life": ["life", "term", "shield", "protect", "death", "family", "education", "child", "future"],
                "health": ["health", "medical", "hospital", "illness", "cancer", "sickness", "operation", "surgery", "icu"],
                "investment": ["ulip", "wealth", "growth", "invest", "builder", "savings", "capital", "equity", "market"],
                "child": ["child", "future", "savings", "education", "family", "marriage"]
            };

            // Calculate mock ELSER score based on keyword semantic group overlaps
            Object.keys(semanticSynonyms).forEach(category => {
                if (lowerQuery.includes(category)) {
                    semanticSynonyms[category].forEach(word => {
                        if (prod.description.toLowerCase().includes(word) || prod.name.toLowerCase().includes(word)) {
                            matchScore += 18;
                        }
                    });
                }
            });

            // Extra scores for direct description tokens
            const tokens = lowerQuery.split(/\s+/);
            tokens.forEach(token => {
                if (token.length > 3 && prod.description.toLowerCase().includes(token)) {
                    matchScore += 8;
                }
            });

            // Normalize score out of 100
            const finalPercent = Math.min(Math.floor((matchScore / 130) * 100), 99);
            return { ...prod, elser_score: finalPercent };
        });

        // Sort descending by simulated ELSER score
        return scoredProducts.sort((a, b) => b.elser_score - a.elser_score);
    }

    // Replicates the GCP Cloud Function rules deterministically
    runComplianceCheck(candidates, profile) {
        const passed = [];
        const rejected = [];

        candidates.forEach(prod => {
            let eligible = true;
            let reason = "";

            // Rule 1: Age Bounds Check
            if (profile.age < prod.min_age) {
                eligible = false;
                reason = `Age (${profile.age}) is below the minimum eligibility age of ${prod.min_age}.`;
            } else if (profile.age > prod.max_age) {
                eligible = false;
                reason = `Age (${profile.age}) exceeds the maximum eligibility limit of ${prod.max_age}.`;
            }

            // Rule 2: Smoker Exclusions Check
            if (profile.smoker && !prod.smoker_eligible) {
                eligible = false;
                reason = `Product is restricted to non-smokers. Patient/User is flagged as a smoker.`;
            }

            // Rule 3: Minimum Income Requirement
            if (profile.income < prod.min_income) {
                eligible = false;
                reason = `Income of ₹${(profile.income/100000).toFixed(1)}L is below the minimum required limit of ₹${(prod.min_income/100000).toFixed(1)}L.`;
            }

            if (eligible) {
                passed.push(prod);
            } else {
                rejected.push({ ...prod, reject_reason: reason });
            }
        });

        return { passed, rejected };
    }

    runSuitabilityRanking(passed, profile) {
        const age = profile.age || 30;
        const smoker = profile.smoker || false;
        const income = profile.income || 500000;
        const ranked = [];

        passed.forEach(prod => {
            // Calculate suitability score
            // Age Centrality (30% weight)
            const midAge = (prod.min_age + prod.max_age) / 2;
            const ageRange = Math.max(prod.max_age - prod.min_age, 1);
            const ageCentrality = 1.0 - Math.abs(age - midAge) / ageRange;

            // Income Fit (30% weight)
            const minIncome = prod.min_income || 300000;
            const incomeFit = Math.min(income / Math.max(minIncome, 1), 1.5) / 1.5;

            // ELSER Semantic score (30% weight)
            const elserScore = (prod.elser_score || 15) / 100;

            // Smoker Alignment / Bonus (10% weight)
            let smokerAlignment = 0.5;
            if (smoker) {
                if (prod.id === 'term_shield_pro' || prod.id === 'comprehensive_health_shield') {
                    smokerAlignment = 1.0;
                } else if (prod.id === 'smart_protect_life') {
                    smokerAlignment = 0.7;
                } else {
                    smokerAlignment = 0.2;
                }
            } else {
                if (prod.id === 'ulip_growth_builder' || prod.id === 'smart_protect_life') {
                    smokerAlignment = 1.0;
                } else {
                    smokerAlignment = 0.5;
                }
            }

            let suitability = (elserScore * 0.3) + (ageCentrality * 0.3) + (incomeFit * 0.3) + (smokerAlignment * 0.1);

            // Give a high explicit bonus (+0.25) to ulip_growth_builder for young non-smokers and term_shield_pro for smokers
            if (smoker && prod.id === 'term_shield_pro') {
                suitability += 0.25;
            }
            if (!smoker && age <= 35 && prod.id === 'ulip_growth_builder') {
                suitability += 0.25;
            }

            const finalPercent = Math.min(Math.max(Math.floor(suitability * 100), 15), 99);
            ranked.push({ ...prod, elser_score: finalPercent });
        });

        // Sort descending by calculated suitability score
        return ranked.sort((a, b) => b.elser_score - a.elser_score);
    }

    // Dynamic natural language response explainer (substituting Gemini-level synthesis)
    generateVoiceExplanation(report, profile) {
        if (report.passed.length === 0) {
            return `Based on my compliance check, I could not find an insurance plan matching your exact profile. This is usually due to age limits or income requirements. Can you please tell me if we can adjust the sum assured or look at family policies?`;
        }

        const topProduct = report.passed[0];
        let speech = `I have successfully analyzed your profile: Age ${profile.age}, ${profile.smoker ? 'smoker' : 'non-smoker'}, with an annual income of Rs ${(profile.income/100000).toFixed(1)} Lakhs. `;

        // Mention any rejected products in compliance checks for demo richness
        const rejectedTarget = report.rejected.find(r => r.id === 'ulip_growth_builder');
        if (profile.smoker && rejectedTarget) {
            speech += `Please note, because you smoke, your request for the Wealth Growth investment plan was flagged as ineligible under IRDAI regulations. However, `;
        }

        speech += `I matched you semantically to our premium product: "${topProduct.name}", which has an Elastic match score of ${topProduct.elser_score} percent. I recommend this because ${topProduct.description.substring(0, 110)}... The premium is estimated at ${topProduct.premium} for a coverage of ${topProduct.sum_assured}. `;

        if (report.passed.length > 1) {
            speech += `I have also unlocked another option: "${report.passed[1].name}". You can see both options displayed in the cards below. Would you like to proceed with the application for any of these?`;
        }

        return speech;
    }

    // ----------------------------------------------------
    // 4. Future Bridge Hook: Connecting to Dialogflow CX API
    // ----------------------------------------------------
    /**
     * Call this function to bridge directly to your GCP Dialogflow CX Agent:
     * Projects/voice-sales-agent/locations/global/agents/7a051be1-2b54-40fd-ad3c-eb88ce0accbf
     */
    async queryDialogflowCX(textInput, sessionId = "user-session-123") {
        const GCP_PROJECT_ID = "voice-sales-agent";
        const AGENT_ID = "7a051be1-2b54-40fd-ad3c-eb88ce0accbf";
        const LOCATION = "global";
        
        // This is a direct HTTP request to detect intent. Note that in a production client browser, 
        // you would route this via an intermediary secure Cloud Function backend (Option 2) 
        // to protect your GCP IAM bearer access tokens from exposure.
        const url = `https://${LOCATION}-dialogflow.googleapis.com/v3/projects/${GCP_PROJECT_ID}/locations/${LOCATION}/agents/${AGENT_ID}/sessions/${sessionId}:detectIntent`;
        
        try {
            const response = await fetch(url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer YOUR_GOOGLE_OAUTH_TOKEN', // Injected from secure middleware
                    'x-goog-user-project': GCP_PROJECT_ID
                },
                body: JSON.stringify({
                    queryInput: {
                        text: {
                            text: textInput
                        },
                        languageCode: "en-in" // Matches agent language!
                    }
                })
            });
            const data = await response.json();
            return data;
        } catch (error) {
            console.error("Dialogflow CX Connection Error:", error);
            throw error;
        }
    }
}

// Global initialization
try {
    window.voiceEngine = new VoiceSimulationEngine();
} catch (err) {
    console.error("Critical error constructing VoiceSimulationEngine:", err);
    if (window.logDebug) {
        window.logDebug("[Critical Error] Failed to start voice engine: " + err.message, "error");
    }
}

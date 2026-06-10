/* =================================------------------
 * InsureVoice — App Logic Orchestrator
 * Connects Web Audio APIs, handles mic tests & playbacks,
 * runs canvas sine waves animations, and updates UI cards.
 * =================================------------------ */

// State variables
let audioContext = null;
let analyser = null;
let micStream = null;
let mediaRecorder = null;
let recordedChunks = [];
let testAudioBlob = null;
let dbMeterAnimationId = null;
let orbAnimationId = null;

let isMuted = false;
window.isMuted = isMuted;
let sessionStartTime = 0;
let timerInterval = null;
let voiceState = 'IDLE'; // States: IDLE, LISTENING, PROCESSING, SPEAKING
window.voiceState = voiceState;

// DOM elements
const prepareScreen = document.getElementById('prepare-screen');
const activeScreen = document.getElementById('active-screen');
const micSelect = document.getElementById('mic-select');
const dbMeterBar = document.getElementById('db-meter-bar');
const btnRecordTest = document.getElementById('btn-record-test');
const btnPlayTest = document.getElementById('btn-play-test');
const btnStartSession = document.getElementById('btn-start-session');
const countdownDisplay = document.getElementById('countdown-display');

const btnMute = document.getElementById('btn-mute');
const btnEndCall = document.getElementById('btn-end-call');
const muteIcon = document.getElementById('mute-icon');
const sessionTimer = document.getElementById('session-timer');
const orbStatusLabel = document.getElementById('orb-status-label');
const transcriptScroller = document.getElementById('transcript-scroller');
const sliderContainer = document.getElementById('product-cards-slider');
const matchesCount = document.getElementById('matches-count');

const btnToggleDebug = document.getElementById('btn-toggle-debug');
const debugDrawer = document.getElementById('debug-drawer');
const btnClearDebug = document.getElementById('btn-clear-debug');
const debugLogsContainer = document.getElementById('debug-logs-container');

// Canvas context
const orbCanvas = document.getElementById('orb-canvas');
let ctx = null;

// ----------------------------------------------------
// 1. Microphone Discovery & Volume Meter
// ----------------------------------------------------

async function initializeAudio() {
    try {
        // Request microphone access
        micStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
        logDebug("Microphone permission granted.", "success");

        // Set up Web Audio context for visual analyser levels
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        audioContext = new AudioContextClass();
        analyser = audioContext.createAnalyser();
        analyser.fftSize = 256;
        
        const source = audioContext.createMediaStreamSource(micStream);
        source.connect(analyser);

        // List available devices and populate dropdown
        await populateMicrophoneList();
        
        // Start running the live level bar
        startVolumeMeter();

        // Enable start session button immediately!
        btnStartSession.disabled = false;
        btnStartSession.innerHTML = '<i class="fa-solid fa-microphone"></i> Start Conversation';
        btnStartSession.classList.add('active');
        logDebug("Microphone connected successfully. Start Conversation is unlocked!", "success");
        return true;

    } catch (error) {
        console.error("Audio Initialization Error:", error);
        logDebug("Microphone permission rejected or unavailable. " + error.message, "warning");
        micSelect.innerHTML = `<option value="">Microphone access is blocked. Please enable it in browser settings.</option>`;
        return false;
    }
}

async function populateMicrophoneList() {
    try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        const audioInputs = devices.filter(device => device.kind === 'audioinput');
        
        micSelect.innerHTML = '';
        if (audioInputs.length === 0) {
            micSelect.innerHTML = `<option value="">No microphone devices detected.</option>`;
            return;
        }

        audioInputs.forEach((device, index) => {
            const option = document.createElement('option');
            option.value = device.deviceId;
            option.text = device.label || `Microphone ${index + 1}`;
            micSelect.appendChild(option);
        });

        // Re-route mic selection triggers
        micSelect.onchange = async () => {
            if (micStream) {
                micStream.getTracks().forEach(track => track.stop());
            }
            const constraints = {
                audio: { deviceId: { exact: micSelect.value } }
            };
            micStream = await navigator.mediaDevices.getUserMedia(constraints);
            logDebug("Switched microphone source to: " + micSelect.options[micSelect.selectedIndex].text, "info");
        };

    } catch (e) {
        console.error("Failed to list mic sources:", e);
    }
}

function startVolumeMeter() {
    const dataArray = new Uint8Array(analyser.frequencyBinCount);
    
    function drawMeter() {
        analyser.getByteFrequencyData(dataArray);
        
        // Calculate root mean square (RMS) amplitude
        let total = 0;
        for (let i = 0; i < dataArray.length; i++) {
            total += dataArray[i];
        }
        const average = total / dataArray.length;
        
        // Map average sound output (0-255) to a UI level percent width (0-100)
        let percentWidth = Math.min((average / 110) * 100, 100);
        
        // Apply slight noise filter
        if (percentWidth < 4) percentWidth = 0;
        
        dbMeterBar.style.width = percentWidth + "%";
        
        dbMeterAnimationId = requestAnimationFrame(drawMeter);
    }
    
    drawMeter();
}

// ----------------------------------------------------
// 2. 3-Second Recording & Playback Test
// ----------------------------------------------------

btnRecordTest.onclick = () => {
    if (!micStream) {
        alert("Please grant microphone permissions first.");
        return;
    }

    recordedChunks = [];
    btnRecordTest.disabled = true;
    btnPlayTest.disabled = true;
    btnStartSession.disabled = true;
    
    logDebug("Starting 3-second recording test clip...", "info");
    
    // Set up media recorder
    mediaRecorder = new MediaRecorder(micStream);
    mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) {
            recordedChunks.push(e.data);
        }
    };
    
    mediaRecorder.onstop = () => {
        testAudioBlob = new Blob(recordedChunks, { type: 'audio/webm' });
        btnPlayTest.disabled = false;
        logDebug("Test clip recorded successfully. Ready for playback.", "success");
    };

    // Begin recording and count down 3 seconds
    mediaRecorder.start();
    countdownDisplay.classList.add('active');
    
    let secondsLeft = 3;
    countdownDisplay.textContent = `0${secondsLeft.toFixed(1)}s`;
    
    const countInterval = setInterval(() => {
        secondsLeft -= 0.1;
        if (secondsLeft <= 0.05) {
            clearInterval(countInterval);
            mediaRecorder.stop();
            countdownDisplay.classList.remove('active');
            countdownDisplay.textContent = "0.0s";
            btnRecordTest.disabled = false;
        } else {
            countdownDisplay.textContent = `0${secondsLeft.toFixed(1)}s`;
        }
    }, 100);
};

btnPlayTest.onclick = () => {
    if (!testAudioBlob) return;
    
    logDebug("Playing back recorded test clip...", "info");
    const audioUrl = URL.createObjectURL(testAudioBlob);
    const audio = new Audio(audioUrl);
    
    btnPlayTest.disabled = true;
    btnRecordTest.disabled = true;
    
    audio.onended = () => {
        btnPlayTest.disabled = false;
        btnRecordTest.disabled = false;
        btnStartSession.disabled = false; // Microphone is officially verified!
        btnStartSession.innerHTML = '<i class="fa-solid fa-circle-check animate-glow"></i> Start Conversation';
        logDebug("Audio verified. Start Conversation unlocked.", "success");
    };
    
    audio.play();
};

// ----------------------------------------------------
// 3. Screen Navigation & Active Sessions
// ----------------------------------------------------

btnStartSession.onclick = async () => {
    // If microphone access is not yet active, attempt to initialize it on click!
    if (!micStream) {
        logDebug("Attempting microphone connection on button click...", "info");
        const initialized = await initializeAudio();
        if (!initialized) {
            alert("Please allow microphone access in your browser settings to start the session.");
            return;
        }
    }

    // SpeechSynthesis warmup REMOVED (single-voice policy: Cloud TTS only).
    // The old warmup spoke an empty utterance on page load which triggered
    // the male browser voice. All audio now goes through voicePlayer (Cloud TTS).

    // Trigger simulation engine Speech greeting synchronously to capture
    // the click gesture's trusted state before any setTimeout splits the context.
    if (window.voiceEngine) {
        window.voiceEngine.playWelcomeGreeting();
        logDebug("[Simulation] Google Web Speech Welcome & STT Initialized.", "success");
    } else {
        alert("The voice simulation engine failed to load or has been blocked. Please check if your browser/frame permissions allow access.");
        logDebug("[Critical Error] window.voiceEngine is undefined upon session start. STT is not initialized.", "error");
    }

    // Transition panels smoothly with CSS opacity transitions
    prepareScreen.classList.remove('active');
    
    setTimeout(() => {
        activeScreen.classList.add('active');
        document.getElementById('global-status-text').textContent = "Connected to Agent";
        
        // Start conversation timer
        startSessionTimer();
        
        // Kick off the visual central orb wave generator
        startVoiceOrbCanvas();
    }, 450);
};

function startSessionTimer() {
    sessionStartTime = Date.now();
    timerInterval = setInterval(() => {
        const diffMs = Date.now() - sessionStartTime;
        const totalSecs = Math.floor(diffMs / 1000);
        const mins = Math.floor(totalSecs / 60).toString().padStart(2, '0');
        const secs = (totalSecs % 60).toString().padStart(2, '0');
        sessionTimer.textContent = `${mins}:${secs}`;
    }, 1000);
}

// Mute button logic
btnMute.onclick = () => {
    isMuted = !isMuted;
    window.isMuted = isMuted;
    if (isMuted) {
        btnMute.classList.add('muted');
        muteIcon.className = "fa-solid fa-microphone-slash";
        if (window.voiceEngine) {
            // Strategy 2: soft-pause (mute) the long-lived stream instead of a
            // full teardown. Hard stopListening() would force a per-turn-style
            // rebuild on un-mute — the exact failure class Strategy 2 removes.
            window.voiceEngine.pauseListening();
        }
        logDebug("Microphone input muted manually.", "warning");
    } else {
        btnMute.classList.remove('muted');
        muteIcon.className = "fa-solid fa-microphone";
        if (window.voiceEngine) {
            window.voiceEngine.startListening();
        }
        logDebug("Microphone input unmuted.", "info");
    }
};

// Reset/hangup logic
btnEndCall.onclick = () => {
    if (confirm("Are you sure you want to end this voice recommendation session?")) {
        // Refresh page back to standard start
        window.location.reload();
    }
};

// ----------------------------------------------------
// 4. Glowing AI Voice Orb (Sine Wave Canvas Render)
// ----------------------------------------------------

function startVoiceOrbCanvas() {
    if (!orbCanvas) return;
    if (!ctx) ctx = orbCanvas.getContext('2d');
    if (!ctx) return;

    let angle = 0;
    const dataArray = new Uint8Array(analyser ? analyser.frequencyBinCount : 128);

    function renderOrb() {
        if (!analyser) return;
        
        analyser.getByteFrequencyData(dataArray);
        
        // Average volume level
        let total = 0;
        for (let i = 0; i < dataArray.length; i++) {
            total += dataArray[i];
        }
        const avgVolume = total / dataArray.length;

        // Clear canvas with subtle alpha fade to preserve motion trails
        ctx.fillStyle = 'rgba(10, 11, 18, 0.2)';
        ctx.fillRect(0, 0, orbCanvas.width, orbCanvas.height);

        const centerX = orbCanvas.width / 2;
        const centerY = orbCanvas.height / 2;
        let baseRadius = 55;
        let ringGlowColor = 'rgba(6, 182, 212, 0.5)'; // Electric Cyan default

        // Custom wave parameters based on engine Voice States
        let waveCount = 3;
        let frequency = 2;
        let amplitude = 4;

        if (voiceState === 'LISTENING') {
            baseRadius += avgVolume * 0.45;
            ringGlowColor = `rgba(147, 51, 234, ${0.4 + avgVolume/120})`; // Purple pulse
            waveCount = 4;
            frequency = 3;
            amplitude = 6 + avgVolume * 0.2;
        } else if (voiceState === 'PROCESSING') {
            baseRadius += Math.sin(angle * 4) * 5;
            ringGlowColor = 'rgba(6, 182, 212, 0.7)'; // Swirling Teal
            waveCount = 2;
            frequency = 5;
            amplitude = 6;
        } else if (voiceState === 'SPEAKING') {
            baseRadius += avgVolume * 0.35;
            ringGlowColor = `rgba(16, 185, 129, ${0.45 + avgVolume/120})`; // Emerald pulse
            waveCount = 3;
            frequency = 2.5;
            amplitude = 5 + avgVolume * 0.15;
        } else if (voiceState === 'BLOCKED') {
            baseRadius += Math.sin(angle * 2) * 1.5;
            ringGlowColor = 'rgba(239, 68, 68, 0.55)'; // Alert Red pulse
            waveCount = 2;
            frequency = 1.5;
            amplitude = 3;
        } else { // IDLE
            baseRadius += Math.sin(angle) * 2;
            ringGlowColor = 'rgba(6, 182, 212, 0.35)'; // Calm cyan
            waveCount = 2;
            frequency = 1;
            amplitude = 2;
        }

        // Draw multiple overlapping transparent waves
        for (let w = 0; w < waveCount; w++) {
            ctx.beginPath();
            ctx.strokeStyle = ringGlowColor;
            ctx.lineWidth = 1.5 - w * 0.3;
            
            // Draw circle with sine wave oscillations
            for (let a = 0; a <= Math.PI * 2; a += 0.05) {
                // Sine oscillation added to radius
                const waveOffset = Math.sin(a * frequency + angle * (w + 1)) * amplitude;
                const currentRadius = baseRadius + waveOffset;
                
                const x = centerX + Math.cos(a) * currentRadius;
                const y = centerY + Math.sin(a) * currentRadius;
                
                if (a === 0) {
                    ctx.moveTo(x, y);
                } else {
                    ctx.lineTo(x, y);
                }
            }
            ctx.closePath();
            ctx.stroke();
        }

        angle += 0.05;
        orbAnimationId = requestAnimationFrame(renderOrb);
    }

    renderOrb();
}

// State controller called by Simulation.js to trigger color/shape swaps
window.updateVoiceState = function(state) {
    voiceState = state;
    window.voiceState = state;
    logDebug(`[Voice State] Swap to: ${state}`, "info");

    const labels = {
        'IDLE': 'Advisor: Ready',
        'LISTENING': 'Advisor: Listening...',
        'PROCESSING': 'Advisor: Thinking...',
        'SPEAKING': 'Advisor: Speaking...',
        'BLOCKED': 'Advisor: Mic Blocked'
    };

    // Don't stomp the orb label while narration is active (the spoken-progress
    // chain owns the label during the ~90s recommendation wait). Only allow
    // voice-state to set it when narration is NOT running.
    if (!_spokenProgressActive) {
        orbStatusLabel.textContent = labels[state] || 'Advisor: Online';
    }
    
    // Animate central core orb shadow glowing matching the color state
    const core = document.querySelector('.voice-orb-core');
    if (state === 'LISTENING') {
        core.style.boxShadow = 'inset 0 0 20px rgba(147, 51, 234, 0.2), 0 0 25px rgba(147, 51, 234, 0.4)';
    } else if (state === 'PROCESSING') {
        core.style.boxShadow = 'inset 0 0 20px rgba(6, 182, 212, 0.2), 0 0 25px rgba(6, 182, 212, 0.4)';
    } else if (state === 'SPEAKING') {
        core.style.boxShadow = 'inset 0 0 20px rgba(16, 185, 129, 0.2), 0 0 25px rgba(16, 185, 129, 0.4)';
    } else if (state === 'BLOCKED') {
        core.style.boxShadow = 'inset 0 0 20px rgba(239, 68, 68, 0.2), 0 0 25px rgba(239, 68, 68, 0.6)';
        core.style.background = 'radial-gradient(circle, rgba(239, 68, 68, 0.2) 0%, rgba(15, 23, 42, 0.9) 100%)';
    } else {
        core.style.boxShadow = 'inset 0 2px 10px rgba(255, 255, 255, 0.05), 0 0 15px rgba(6, 182, 212, 0.15)';
    }
};

// ----------------------------------------------------
// Issue 1 (2026-06-06) — progress narration during the ~80s recommendation wait.
// The /invoke POST blocks synchronously for the full pipeline (search →
// compliance → rank → recommend, 5 sequential hops). With no signal during the
// wait the orb just sits on a single label and the demo feels frozen. We rotate
// the EXISTING orb status label through stage messages while the POST is in
// flight. TEXT ONLY — deliberately NO spoken audio (spoken narration would
// collide with the mute-during-TTS / dead-mic machinery). Purely cosmetic;
// touches nothing on the backend or the request itself. Stops the moment the
// response lands (or errors). Self-clearing, idempotent.
// Each stage = the label shown on the orb AND (when spoken mode is on) the
// short line the agent voices. Kept short for TTS. The visual label uses the
// "Advisor: …" prefix; the spoken line is a natural sentence.
const _ORB_NARRATION_STAGES = [
    { label: 'Advisor: Understanding your needs...',        say: "Let me look into this for you." },
    { label: 'Advisor: Searching the plan catalog...',      say: "Searching our plans for the best fit." },
    { label: 'Advisor: Checking eligibility & guardrails...', say: "Now checking which ones you're eligible for." },
    { label: 'Advisor: Ranking your best matches...',       say: "Ranking your best matches." },
    { label: 'Advisor: Preparing your top matches...',      say: "Almost there — preparing your top matches." },
];
let _orbNarrationTimer = null;
let _orbNarrationIdx = 0;
let _spokenProgressActive = false;
let _spokenProgressCancelled = false;

// Toggle: speak the progress stages aloud (true) vs text-only on the orb
// (false). Exposed on window so it can be flipped from the console during a
// live test without a code change, e.g. `window.__SPEAK_PROGRESS = false`.
if (typeof window.__SPEAK_PROGRESS === 'undefined') window.__SPEAK_PROGRESS = true;

function _setOrbLabel(text) {
    if (orbStatusLabel) orbStatusLabel.textContent = text;
}

// Spoken progress: play pre-generated Cloud TTS clips from /audio/progress_N.mp3.
// These are generated at build time with the SAME en-IN-Neural2-A voice so there's
// no voice mismatch. Played via a plain <audio> element (no MediaSource needed —
// they're tiny complete MP3 files). Sequential + cancellable.
function _speakProgressChain() {
    let i = 0;
    const playNext = () => {
        if (_spokenProgressCancelled || i >= _ORB_NARRATION_STAGES.length) return;
        const stage = _ORB_NARRATION_STAGES[i];
        _setOrbLabel(stage.label);

        const audio = new Audio(`/audio/progress_${i}.mp3`);
        i++;
        audio.onplay = () => { window.__voiceMicSuspended = true; };
        audio.onended = () => {
            window.__voiceMicSuspended = false;
            if (_spokenProgressCancelled) return;
            // Gap between clips for calm pacing
            _orbNarrationTimer = setTimeout(playNext, 3000);
        };
        audio.onerror = () => {
            window.__voiceMicSuspended = false;
            if (_spokenProgressCancelled) return;
            // Clip failed to load — advance label silently
            _orbNarrationTimer = setTimeout(playNext, 5000);
        };
        audio.play().catch(() => {
            // Autoplay blocked or file missing — advance silently
            window.__voiceMicSuspended = false;
            if (!_spokenProgressCancelled) {
                _orbNarrationTimer = setTimeout(playNext, 5000);
            }
        });
    };
    playNext();
}

window.startOrbNarration = function() {
    if (_orbNarrationTimer !== null || _spokenProgressActive) return;  // idempotent
    if (!orbStatusLabel) return;
    _spokenProgressCancelled = false;
    _orbNarrationIdx = 0;
    _setOrbLabel(_ORB_NARRATION_STAGES[0].label);

    const canSpeak = window.__SPEAK_PROGRESS
        && window.voicePlayer
        && typeof window.voicePlayer.playTTS === 'function';

    if (canSpeak) {
        _spokenProgressActive = true;
        _speakProgressChain();
    } else {
        // Fallback: silent text rotation every ~6s, clamped on the last stage.
        _orbNarrationTimer = setInterval(() => {
            _orbNarrationIdx = Math.min(_orbNarrationIdx + 1, _ORB_NARRATION_STAGES.length - 1);
            _setOrbLabel(_ORB_NARRATION_STAGES[_orbNarrationIdx].label);
        }, 6000);
    }
};

window.stopOrbNarration = function() {
    _spokenProgressCancelled = true;
    _spokenProgressActive = false;
    if (_orbNarrationTimer !== null) {
        clearInterval(_orbNarrationTimer);
        clearTimeout(_orbNarrationTimer);
        _orbNarrationTimer = null;
    }
};

// ----------------------------------------------------
// 5. Dynamic UI Updates (Transcript & Cards Slider)
// ----------------------------------------------------

// Convert basic Markdown emitted by Gemini ("* **Age:** 28") into clean HTML for
// transcript bubbles. Agent prompt asks for plain prose but Gemini occasionally
// formats anyway; render it gracefully instead of leaking raw asterisks.
const renderAgentMarkdown = (raw) => {
    if (!raw) return '';
    let s = String(raw);
    // Bold: **text** → <strong>text</strong> (also __text__)
    s = s.replace(/\*\*([^*\n]+?)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/__([^_\n]+?)__/g, '<strong>$1</strong>');
    // Italic: *text* (not list bullets) → <em>text</em>
    s = s.replace(/(^|[^*])\*([^*\n]+?)\*(?!\*)/g, '$1<em>$2</em>');
    // List items: lines starting with "* " or "- " → "• "
    s = s.replace(/^[\s]*[*-]\s+/gm, '• ');
    // Strip leading "# " heading markers (rare, but happens)
    s = s.replace(/^#+\s+/gm, '');
    // Convert remaining newlines to <br> so list items wrap nicely
    s = s.replace(/\n/g, '<br>');
    return s;
};

window.addTranscriptBubble = function(sender, text) {
    const isUser = (sender === 'USER');
    const bubble = document.createElement('div');
    bubble.className = `bubble bubble-${isUser ? 'user' : 'agent'} animate-bubble`;

    if (isUser) {
        bubble.textContent = text;
    } else {
        bubble.innerHTML = renderAgentMarkdown(text); // Enable HTML tables + clean Markdown
    }

    transcriptScroller.appendChild(bubble);

    // Smooth auto-scroll to bottom of transcripts container
    const container = document.getElementById('transcript-scroller').parentElement;
    container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
};

const formatType = (p) => {
    const raw = p.type || p.product_type || 'Insurance Plan';
    return String(raw).replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
};

const formatPremium = (p) => {
    if (p.premium) return p.premium;
    if (p.premium_min_monthly !== undefined && p.premium_max_monthly !== undefined) {
        return `₹${p.premium_min_monthly.toLocaleString()} - ₹${p.premium_max_monthly.toLocaleString()}/mo`;
    }
    if (p.premium_min_monthly !== undefined) return `₹${p.premium_min_monthly.toLocaleString()}/mo`;
    return 'N/A';
};

const formatCoverage = (p) => {
    if (p.sum_assured) return p.sum_assured;
    if (p.max_sum_assured !== undefined) return `₹${p.max_sum_assured.toLocaleString()}`;
    return 'N/A';
};

// Issue 3 (2026-06-06): build a deterministic "Why it fits you" reason list
// from rank_products' score_breakdown (already on the wire in each top3 item).
// Ties the recommendation to the customer's stated profile to build confidence.
// Pure client-side derivation — no LLM, no backend change (per L-001: keep
// confidence-critical text deterministic). score_breakdown = {elser_relevance,
// age_centrality, income_fit}; these map to coverage-goal / age / income fit.
// (smoker/family/health are compliance filters, not in the score — eligibility
// is implied by the product being in the PASSED list.)
function buildWhyMatch(prod) {
    const sb = prod.score_breakdown || {};
    const reasons = [];
    if (typeof sb.age_centrality === 'number' && sb.age_centrality >= 0.7) {
        reasons.push('Well-suited to your age');
    }
    if (typeof sb.income_fit === 'number' && sb.income_fit >= 0.7) {
        reasons.push('Premium fits comfortably within your income');
    }
    if (typeof sb.elser_relevance === 'number' && sb.elser_relevance >= 0.7) {
        reasons.push('Closely matches the cover you asked for');
    }
    // Always give at least one confidence cue — passing compliance is itself a
    // profile-tied fact (eligible for your age / smoker status / health).
    if (reasons.length === 0) {
        reasons.push("Passed all eligibility checks for your profile");
    }
    return reasons.slice(0, 2);  // keep cards tight — top 2 reasons
}

window.displayRecommendedProducts = function(passed, rejected = []) {
    sliderContainer.innerHTML = '';
    
    const totalCount = passed.length;
    matchesCount.textContent = `${totalCount} Match${totalCount === 1 ? '' : 'es'}`;
    
    if (passed.length === 0 && rejected.length === 0) {
        sliderContainer.innerHTML = `
            <div class="slider-placeholder">
                <i class="fa-solid fa-comment-dots placeholder-bubble-icon"></i>
                <p>Speak to search, rank, and run compliance checks on insurance policies live...</p>
            </div>
        `;
        return;
    }

    // A. Output Passed Products First (Glowing Green Match Badges)
    // Replace raw ELSER score (sparse retrieval values land in 0.01-0.05 range and
    // read as broken on a UI) with ranking-position badges based on ELSER ordering.
    const rankLabels = ['Top Match', 'Strong Match', 'Recommended Match'];
    const sortedByScore = [...passed]
        .map((p, idx) => ({ p, idx, score: p.elser_score || 0 }))
        .sort((a, b) => b.score - a.score);
    const idxToLabel = {};
    sortedByScore.forEach((entry, rank) => {
        idxToLabel[entry.idx] = rankLabels[rank] || 'Match';
    });

    passed.forEach((prod, i) => {
        const card = document.createElement('div');
        card.className = "product-card";
        const matchLabel = idxToLabel[i] || 'Match';

        // Issue 3 — per-card "Why it fits you" reasons from score_breakdown.
        const whyReasons = buildWhyMatch(prod);
        const whyHtml = whyReasons.length
            ? `<div class="card-why">
                   <span class="card-why-label"><i class="fa-solid fa-circle-check"></i> Why it fits you</span>
                   <ul class="card-why-list">${whyReasons.map(r => `<li>${r}</li>`).join('')}</ul>
               </div>`
            : '';

        card.innerHTML = `
            <div class="card-header-row">
                <span class="card-title">${prod.name}</span>
                <span class="card-type-badge">${formatType(prod)}</span>
            </div>
            <div class="card-match-pct" title="Ranked by ELSER semantic similarity"><i class="fa-solid fa-fire-flame-curved"></i> ${matchLabel}</div>
            <p class="card-desc">${prod.description || prod.key_feature || ''}</p>
            ${whyHtml}
            <div class="card-meta-row">
                <span>Coverage: <span class="card-cover">${formatCoverage(prod)}</span></span>
                <span>Premium: <span class="card-price">${formatPremium(prod)}</span></span>
            </div>
        `;

        sliderContainer.appendChild(card);
    });

    // B. Output Rejected Products (Compliance Guards Triggered - Greyed Red Cards)
    rejected.forEach(prod => {
        const card = document.createElement('div');
        card.className = "product-card rejected";
        
        card.innerHTML = `
            <div class="card-header-row">
                <span class="card-title">${prod.name}</span>
                <span class="card-type-badge">${formatType(prod)}</span>
            </div>
            <div class="card-match-pct"><i class="fa-solid fa-ban"></i> Not Eligible</div>
            <div class="card-rejected-banner">
                <i class="fa-solid fa-triangle-exclamation"></i> ${prod.reject_reason}
            </div>
            <p class="card-desc" style="display:none">${prod.description || prod.key_feature || ''}</p>
        `;
        
        sliderContainer.appendChild(card);
    });
};

// ----------------------------------------------------
// 6. Developer Debug Terminal
// ----------------------------------------------------

btnToggleDebug.onclick = () => {
    debugDrawer.classList.toggle('active');
};

btnClearDebug.onclick = () => {
    debugLogsContainer.innerHTML = '<div class="log-line text-muted">[System] Console logs cleared.</div>';
};

function logDebug(message, level = "info") {
    const time = new Date().toLocaleTimeString();
    const line = document.createElement('div');
    line.className = `log-line text-${level}`;
    line.textContent = `[${time}] ${message}`;
    
    debugLogsContainer.appendChild(line);
    debugLogsContainer.scrollTop = debugLogsContainer.scrollHeight;

    // Also write to standard browser developer console for automated test visibility
    const consoleMsg = `[AppDebug][${level}] ${message}`;
    if (level === "warning") {
        console.warn(consoleMsg);
    } else if (level === "error" || level === "crimson") {
        console.error(consoleMsg);
    } else {
        console.log(consoleMsg);
    }
}

// Flush early debug buffer if it exists (captured before app.js was loaded)
if (window.logDebugBuffer && window.logDebugBuffer.length > 0) {
    window.logDebugBuffer.forEach(item => {
        const line = document.createElement('div');
        line.className = `log-line text-${item.level}`;
        line.textContent = `[${item.time}] ${item.message}`;
        debugLogsContainer.appendChild(line);
    });
    debugLogsContainer.scrollTop = debugLogsContainer.scrollHeight;
    window.logDebugBuffer = [];
}

// Global debug exposure
window.logDebug = logDebug;

// Trigger browser permissions on document load
window.onload = () => {
    initializeAudio();

    // Wire up the Ready to Connect badge to act as a session start button
    const statusBadge = document.querySelector('.status-badge');
    if (statusBadge) {
        statusBadge.onclick = () => {
            if (!btnStartSession.disabled) {
                logDebug("Session start triggered via status badge click.", "info");
                btnStartSession.click();
            } else {
                alert("Please connect/allow your microphone first to start the session.");
            }
        };
    }
};

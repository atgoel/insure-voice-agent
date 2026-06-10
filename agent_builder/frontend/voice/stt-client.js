/**
 * InsureVoice — B2 STT WebSocket Client
 * ======================================
 *
 * Replaces the browser `webkitSpeechRecognition` STT path with a server-side
 * Speech-to-Text v2 (Chirp 2) gRPC stream, fronted by a WebSocket bridge at
 * `/stt/stream`.
 *
 * Public API exposed on `window.SttClient`:
 *   * SttClient.create({ onInterim, onFinal, onActivity, onError, onClosed })
 *       → returns an `{ start, stop, dispose }` controller.
 *
 * Wire format (mirrors agent_builder/stt_websocket.py):
 *   * First text frame from FE → server:
 *       { "type": "config", "session_id": <str|null>, "sample_rate": 16000, "language": "en-IN" }
 *   * Server → FE: `{type:"ready"}`, then `event` / `interim` / `final` /
 *     `error` / `closed` JSON text frames.
 *   * FE → server audio: binary frames (16 kHz Int16 LE PCM, 30 ms = 960 B).
 *   * FE → server graceful end: `{ "type": "end" }` text frame.
 *
 * D8 contract (from Locked_Decisions.md): publish two globals on init so B1
 * (TTS streaming) can suspend/resume the mic capture during playback.
 *   * window.__voiceAudioCtx          — the 16 kHz mic-capture AudioContext
 *   * window.__voiceMicSuspended      — boolean flag, true while mic muted
 *
 * D9 contract: the AC-B2.6.5 echo-tail harness is reused for B1's AC-B1.11.
 * `muteSTTOutput` (per-instance flag) suppresses interim/final events from
 * being routed to the UI/processInputText callbacks while TTS plays. This is
 * defense-in-depth ON TOP of `audioCtx.suspend()`; not in conflict.
 */

(function () {
    'use strict';

    const TARGET_RATE = 16000;
    const WORKLET_URL = '/voice/audio-worklet-processor.js';
    const WS_PATH = '/stt/stream';
    const RECONNECT_GRACE_MS = 250;

    /**
     * Build the WebSocket URL: same-origin, ws/wss matching page protocol.
     */
    function _buildWsUrl() {
        const proto = (location.protocol === 'https:') ? 'wss:' : 'ws:';
        return `${proto}//${location.host}${WS_PATH}`;
    }

    function _logDbg(msg, level) {
        if (window.logDebug) {
            try { window.logDebug(msg, level || 'info'); } catch (_) {}
        }
        try { console.log(msg); } catch (_) {}
    }

    /**
     * Construct a new STT client.
     *
     * @param {Object} cfg
     * @param {(text:string, stability:number) => void} cfg.onInterim
     * @param {(text:string, confidence:number) => void} cfg.onFinal
     * @param {(event:string) => void} cfg.onActivity      VAD events
     * @param {(code:string, detail:string) => void} cfg.onError
     * @param {(reason:string) => void} cfg.onClosed
     * @returns {{ start: () => Promise<void>, stop: () => Promise<void>, dispose: () => void, getState: () => string }}
     */
    function create(cfg) {
        cfg = cfg || {};

        let audioCtx = null;
        let micStream = null;
        let workletNode = null;
        let ws = null;
        let reconnectAttempts = 0;
        let intentionallyClosed = false;
        let sessionId = null;
        let state = 'IDLE';            // IDLE | CONNECTING | OPEN | CLOSING
        let muteSTTOutput = false;     // FE suppression flag — see D8/D9
        let _frameDiag = null;         // DIAG (2026-06-06): frame send/drop counters

        // -----------------------------------------------------------------
        // D8 — publish globals so B1 can drive suspend/resume.
        // -----------------------------------------------------------------
        function _publishGlobals(ctx) {
            window.__voiceAudioCtx = ctx;
            window.__voiceMicSuspended = false;
        }

        function _setMicSuspended(flag) {
            muteSTTOutput = !!flag;
            window.__voiceMicSuspended = !!flag;
            // DIAG (2026-06-06): on unmute, report the PHYSICAL audio state so we
            // know if the dead-mic is a suspended ctx (resume fixes) vs an ended
            // mic track (needs re-getUserMedia). Decisive for root-cause.
            if (!flag) {
                let trackState = 'no-stream';
                try {
                    if (micStream) {
                        const tr = micStream.getAudioTracks()[0];
                        trackState = tr ? `${tr.readyState}/enabled=${tr.enabled}/muted=${tr.muted}` : 'no-track';
                    }
                } catch (_) {}
                _logDbg(`[STT UNMUTE] ctx=${audioCtx ? audioCtx.state : 'null'} `
                    + `micTrack=${trackState}`, 'info');
            }
            // DEAD-MIC FIX (2026-06-06): unmuting must PHYSICALLY reactivate the
            // mic, not just clear the flag. A suspended AudioContext halts the
            // worklet entirely → zero frames → mic deaf even with muteSTTOutput
            // =false (confirmed live: sent stops climbing, muteSTTOutput=false).
            // The ctx gets suspended during TTS (voice-player onplay) but the
            // browser-TTS / rapid-double-TTS path never reliably resumes it.
            // setMuted(false) is the canonical re-arm hook, so resume here.
            if (!flag && audioCtx && audioCtx.state === 'suspended') {
                try {
                    audioCtx.resume().then(
                        () => _logDbg('[STT] AudioContext resumed on unmute', 'info'),
                        (e) => _logDbg('[STT] AudioContext resume failed: ' + e, 'warning'),
                    );
                } catch (e) {
                    _logDbg('[STT] AudioContext resume threw: ' + e, 'warning');
                }
            }
        }

        // -----------------------------------------------------------------
        // WebSocket lifecycle
        // -----------------------------------------------------------------

        function _openSocket() {
            return new Promise((resolve, reject) => {
                const url = _buildWsUrl();
                _logDbg(`[STT WS] connecting → ${url}`, 'info');
                let s;
                try {
                    s = new WebSocket(url);
                } catch (err) {
                    reject(err);
                    return;
                }
                s.binaryType = 'arraybuffer';

                let resolved = false;
                let readyReceived = false;

                // Change 5 (InvB resilience): if the server accepts the WS but
                // never sends {type:"ready"}, the promise would hang forever and
                // `state` would stay CONNECTING, wedging every future start()
                // (the :342 guard early-returns on CONNECTING). In the long-lived
                // design there's no per-turn rebuild to paper over this. Reject
                // after the timeout, reset state→IDLE so a later start() can retry,
                // and close the half-open socket.
                // 2026-06-06: bumped 5s→10s. The server builds ready AFTER the
                // SpeechAsyncClient is available; even with startup pre-warm, a
                // cold/jittery first connection can exceed 5s (measured ~3.6s
                // standalone construct, longer under live loop). 10s clears it
                // with margin while still bounding a genuinely dead handshake.
                const READY_TIMEOUT_MS = 10000;
                const readyTimer = setTimeout(() => {
                    if (!resolved) {
                        resolved = true;
                        state = 'IDLE';
                        try { s.close(4000, 'ready_timeout'); } catch (_) {}
                        reject(new Error('STT ready not received within 10s'));
                    }
                }, READY_TIMEOUT_MS);

                s.onopen = () => {
                    _logDbg('[STT WS] open — sending config', 'info');
                    try {
                        s.send(JSON.stringify({
                            type: 'config',
                            session_id: sessionId,
                            sample_rate: TARGET_RATE,
                            language: 'en-IN',
                        }));
                    } catch (err) {
                        reject(err);
                    }
                };

                s.onmessage = (ev) => {
                    if (typeof ev.data !== 'string') {
                        // Server should not send binary back — ignore.
                        return;
                    }
                    let msg;
                    try {
                        msg = JSON.parse(ev.data);
                    } catch (err) {
                        _logDbg('[STT WS] non-JSON text frame ignored: ' + ev.data, 'warning');
                        return;
                    }
                    if (!readyReceived && msg.type === 'ready') {
                        readyReceived = true;
                        clearTimeout(readyTimer);
                        if (msg.session_id) {
                            sessionId = msg.session_id;
                        }
                        ws = s;
                        state = 'OPEN';
                        reconnectAttempts = 0;
                        if (!resolved) {
                            resolved = true;
                            resolve(s);
                        }
                        return;
                    }
                    _routeServerMessage(msg);
                };

                s.onerror = (ev) => {
                    clearTimeout(readyTimer);
                    _logDbg('[STT WS] error: ' + (ev && ev.message ? ev.message : '(no detail)'), 'warning');
                    if (!resolved) {
                        resolved = true;
                        reject(ev);
                    }
                };

                s.onclose = (ev) => {
                    clearTimeout(readyTimer);
                    _logDbg(`[STT WS] closed code=${ev.code} reason=${ev.reason}`, 'info');
                    const wasOpen = (ws === s);
                    ws = null;
                    if (state !== 'CLOSING') {
                        state = 'IDLE';
                    }
                    if (wasOpen && !intentionallyClosed) {
                        _scheduleReconnect();
                    }
                    if (cfg.onClosed) {
                        try { cfg.onClosed(ev.reason || 'ws_close'); } catch (_) {}
                    }
                };
            });
        }

        function _scheduleReconnect() {
            // Per B2 SPEC v2 §M4: ONE silent reconnect attempt; second failure
            // surfaces banner via onError.
            if (reconnectAttempts >= 1) {
                _logDbg('[STT WS] reconnect budget exhausted — surfacing error', 'warning');
                if (cfg.onError) {
                    try { cfg.onError('STT_RPC_ERROR', 'Mic disconnected. Click to retry.'); } catch (_) {}
                }
                return;
            }
            reconnectAttempts += 1;
            _logDbg('[STT WS] silent reconnect attempt #' + reconnectAttempts, 'warning');
            setTimeout(() => {
                _openSocket().catch((err) => {
                    _logDbg('[STT WS] silent reconnect failed: ' + err, 'warning');
                    if (cfg.onError) {
                        try { cfg.onError('STT_RPC_ERROR', 'Mic disconnected. Click to retry.'); } catch (_) {}
                    }
                });
            }, RECONNECT_GRACE_MS);
        }

        // -----------------------------------------------------------------
        // Server → FE message router
        // -----------------------------------------------------------------

        function _routeServerMessage(msg) {
            switch (msg.type) {
                case 'event':
                    if (muteSTTOutput) {
                        // D9 echo-hole fix: with the long-lived mic live across
                        // turns, TTS-bleed (or room echo before suspend() fully
                        // takes) can yield a SPEECH_ACTIVITY_BEGIN that resets the
                        // FE wrap-up silence timer mid-TTS. Gate it like interim/
                        // final so VAD events are suppressed while muted.
                        return;
                    }
                    if (cfg.onActivity) {
                        try { cfg.onActivity(msg.event); } catch (_) {}
                    }
                    break;
                case 'interim':
                    if (muteSTTOutput) {
                        return;  // D8/D9: suppress while TTS plays
                    }
                    if (cfg.onInterim) {
                        try { cfg.onInterim(msg.text || '', msg.stability || 0); } catch (_) {}
                    }
                    break;
                case 'final':
                    // Bug 1 fix (2026-06-09): do NOT suppress finals while muted.
                    // Finals represent a completed, committed speech recognition result.
                    // Dropping them while muteSTTOutput=true (set by TTS playback) caused
                    // user farewell utterances ("thank you") said during/after agent speech
                    // to be silently discarded — no user bubble, no /invoke call, silence
                    // timer fired goodbye instead. Only interim + event are echo-prone;
                    // finals are already latency-gated by the Chirp2 model itself.
                    if (cfg.onFinal) {
                        try { cfg.onFinal(msg.text || '', msg.confidence || 0); } catch (_) {}
                    }
                    break;
                case 'error':
                    if (cfg.onError) {
                        try { cfg.onError(msg.code || 'UNKNOWN', msg.detail || ''); } catch (_) {}
                    }
                    break;
                case 'closed':
                    // Server-initiated close notice; the actual close event
                    // will follow via ws.onclose.
                    _logDbg('[STT WS] server closed: ' + (msg.reason || ''), 'info');
                    break;
                default:
                    _logDbg('[STT WS] unknown msg.type=' + msg.type, 'warning');
            }
        }

        // -----------------------------------------------------------------
        // Mic capture + AudioWorklet wiring
        // -----------------------------------------------------------------

        async function _openMic() {
            if (audioCtx && audioCtx.state !== 'closed') {
                return;
            }
            const AudioCtxCtor = window.AudioContext || window.webkitAudioContext;
            // Request 16 kHz; Chrome on Windows ignores it (R7 = Cert) but the
            // worklet decimates server-side regardless.
            audioCtx = new AudioCtxCtor({ sampleRate: TARGET_RATE });
            _publishGlobals(audioCtx);

            try {
                await audioCtx.audioWorklet.addModule(WORKLET_URL);
            } catch (err) {
                throw new Error('audioWorklet.addModule failed: ' + err);
            }

            // getUserMedia — request 16 kHz mono with echo-cancel + noise-suppress.
            try {
                micStream = await navigator.mediaDevices.getUserMedia({
                    audio: {
                        sampleRate: TARGET_RATE,
                        channelCount: 1,
                        echoCancellation: true,
                        noiseSuppression: true,
                        autoGainControl: true,
                    },
                });
            } catch (err) {
                // Per B2 SPEC v2 §13.2: explicit MIC_DENIED error code.
                if (err && err.name === 'NotAllowedError') {
                    if (cfg.onError) {
                        try { cfg.onError('MIC_DENIED', 'Microphone permission denied'); } catch (_) {}
                    }
                }
                throw err;
            }

            const src = audioCtx.createMediaStreamSource(micStream);
            workletNode = new AudioWorkletNode(audioCtx, 'pcm-worklet', {
                numberOfInputs: 1,
                numberOfOutputs: 0,
                channelCount: 1,
                processorOptions: {
                    inputSampleRate: audioCtx.sampleRate,
                },
            });
            workletNode.port.onmessage = _onWorkletMessage;
            src.connect(workletNode);
            // No output — STT is sink-only.
        }

        function _onWorkletMessage(ev) {
            const data = ev && ev.data;
            if (!data || data.type !== 'pcm' || !data.buffer) return;
            // DIAG (2026-06-06 dead-mic investigation): count frames sent vs
            // dropped-by-mute, logged ≤1×/sec, to disambiguate "mic stuck muted"
            // from "mic live but STT didn't transcribe". Remove once root-caused.
            if (!_frameDiag) {
                _frameDiag = { sent: 0, mutedDrop: 0, closedDrop: 0, last: 0 };
            }
            // Suppress ALL frames upstream while suspended (defense-in-depth;
            // audioCtx.suspend() should already silence the worklet).
            if (muteSTTOutput) { _frameDiag.mutedDrop++; _frameDiagFlush(); return; }
            if (!ws || ws.readyState !== WebSocket.OPEN) { _frameDiag.closedDrop++; _frameDiagFlush(); return; }
            try {
                ws.send(data.buffer);
                _frameDiag.sent++;
                _frameDiagFlush();
            } catch (err) {
                _logDbg('[STT WS] send failed: ' + err, 'warning');
            }
        }

        function _frameDiagFlush() {
            const now = (window.performance && performance.now) ? performance.now() : 0;
            if (now - _frameDiag.last < 1000) return;
            _frameDiag.last = now;
            _logDbg(
                `[STT FRAMES] sent=${_frameDiag.sent} mutedDrop=${_frameDiag.mutedDrop} `
                + `closedDrop=${_frameDiag.closedDrop} (muteSTTOutput=${muteSTTOutput})`,
                'info',
            );
        }

        async function _closeMic() {
            if (workletNode) {
                try {
                    workletNode.port.postMessage({ type: 'flush' });
                    workletNode.disconnect();
                } catch (_) {}
                workletNode = null;
            }
            if (micStream) {
                try {
                    micStream.getTracks().forEach((t) => t.stop());
                } catch (_) {}
                micStream = null;
            }
            if (audioCtx && audioCtx.state !== 'closed') {
                try { await audioCtx.close(); } catch (_) {}
            }
            audioCtx = null;
            window.__voiceAudioCtx = null;
            window.__voiceMicSuspended = false;
        }

        // -----------------------------------------------------------------
        // Public API
        // -----------------------------------------------------------------

        async function start(opts) {
            if (state === 'OPEN' || state === 'CONNECTING') {
                return;
            }
            if (state === 'CLOSING') {
                // Wait briefly for an in-flight stop() to finish; if still CLOSING,
                // surface a soft error so the caller can retry.
                for (let i = 0; i < 20 && state === 'CLOSING'; i++) {
                    await new Promise((r) => setTimeout(r, 50));
                }
                if (state === 'CLOSING') {
                    throw new Error('STT still closing - retry shortly');
                }
            }
            opts = opts || {};
            if (opts.session_id) {
                sessionId = opts.session_id;
            }
            intentionallyClosed = false;
            state = 'CONNECTING';
            try {
                await _openMic();
                await _openSocket();
            } catch (err) {
                state = 'IDLE';
                throw err;
            }
        }

        async function stop() {
            intentionallyClosed = true;
            state = 'CLOSING';
            // Send graceful end frame.
            if (ws && ws.readyState === WebSocket.OPEN) {
                try {
                    ws.send(JSON.stringify({ type: 'end' }));
                } catch (_) {}
                try {
                    ws.close(1000, 'user_stop');
                } catch (_) {}
            }
            ws = null;
            await _closeMic();
            state = 'IDLE';
        }

        function dispose() {
            stop().catch(() => {});
        }

        function getState() { return state; }

        function setSessionId(sid) { sessionId = sid; }

        // Expose mute toggle so B1 (TTS) can drive defense-in-depth as well —
        // but the canonical hook in B1 is `audioCtx.suspend()` via the
        // window.__voiceAudioCtx + window.__voiceMicSuspended globals.
        function setMuted(flag) { _setMicSuspended(flag); }

        return {
            start,
            stop,
            dispose,
            getState,
            setSessionId,
            setMuted,
        };
    }

    window.SttClient = { create };
})();

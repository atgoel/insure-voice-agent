/**
 * InsureVoice — Server-side TTS player (B1)
 * -----------------------------------------
 * Replaces SpeechSynthesisUtterance with progressive MP3 playback of audio
 * streamed from the Cloud Run `/tts/stream` endpoint (Chirp 3 HD voice).
 *
 * Public surface (loaded as a plain script, no module bundler):
 *   window.VoicePlayer              — class
 *   window.voicePlayer              — singleton instance
 *   window.voicePlayer.playTTS(...) — primary entry point
 *   window.__voiceAudioCtx          — AudioContext (READ ONLY by B1; B2/
 *                                     stt-client.js is the SOLE publisher.
 *                                     B1 reads via _readAudioCtx() with null
 *                                     fallback so TTS works pre-mic-start.)
 *   window.__voiceMicSuspended      — boolean mirror of the suspended state.
 *                                     Written by B1 ONLY during onplay/onended
 *                                     transitions; B2 inits it on STT start.
 *
 * Cross-spec contracts (Locked Decisions D8, B1 SPEC v2 §5.1):
 *   <audio>.onplay   → __voiceMicSuspended=true + updateVoiceState('SPEAKING')
 *   <audio>.onended  → setTimeout(200ms) → __voiceMicSuspended=false
 *                      + updateVoiceState('IDLE') + callback()
 *   <audio>.onerror  → same recovery path as onended (NEVER leave mic gated
 *                      on error — would soft-brick conversation)
 *
 * DEAD-MIC FIX (2026-06-06): the original design ALSO called
 * __voiceAudioCtx.suspend()/resume() around playback. REMOVED — Chrome does
 * not reliably resume an AudioWorklet fed by a getUserMedia stream after
 * suspend()→resume(); the worklet stops pumping and the mic goes deaf after
 * the first TTS (ctx reports 'running', track 'live', but zero frames). Echo
 * protection is fully preserved by the __voiceMicSuspended flag (stt-client.js
 * drops frames while true) + getUserMedia echoCancellation. The ctx now runs
 * continuously; only the flag gates frames.
 *
 * Streaming model: MediaSource + 'audio/mpeg'. MP3 is universally supported
 * by modern Chrome/Edge MSE (MP3 is mandatory in the HTML spec MIME map).
 * Falls back to a plain `<audio src=blob:...>` path if MediaSource is
 * unavailable, or if any chunked-pipe error fires before first append.
 */

(function () {
    'use strict';

    const TTS_ENDPOINT = '/tts/stream';
    const RESUME_DELAY_MS = 200;       // §5.1 echo-tail discard
    const MEDIA_MIME = 'audio/mpeg';   // MP3 — locked in B1 SPEC v2

    // -----------------------------------------------------------------------
    // D8 — publish AudioContext + mic-suspended flag globally so B2 STT
    // implementer can hang its mic-capture pipeline off the same context.
    // We create an AudioContext lazily because Chrome blocks construction
    // until the first user gesture.
    // -----------------------------------------------------------------------
    // M1 fix (Reviewer Pass 2026-06-05): D8 contract says B2 (stt-client.js) is
    // the SOLE publisher of window.__voiceAudioCtx + window.__voiceMicSuspended.
    // B1 only READS them. The previous _ensureAudioCtx() created a playback-only
    // AudioContext when B2 hadn't started yet (welcome-message-before-mic),
    // which was published as the "mic" context but had nothing to suspend.
    // Worse: stt-client._closeMic() nulls the global on every stopListening(),
    // which simulation.js calls after every user turn — recreating the orphan.
    // Now: read-only access, null-safe. If B2 hasn't published, suspend/resume
    // become no-ops; the actual mic is suspended only once B2 owns the global.
    function _readAudioCtx() {
        return window.__voiceAudioCtx || null;
    }

    // -----------------------------------------------------------------------
    // VoicePlayer
    // -----------------------------------------------------------------------
    class VoicePlayer {
        constructor() {
            this.currentAudio = null;
            this.currentMediaSource = null;
            this.currentObjectUrl = null;
            this.currentAbortController = null;
            this.isPlaying = false;
            // Singleton AudioContext lives on window.__voiceAudioCtx; B2 reads
            // it directly. We do NOT create a separate playback AudioContext —
            // the <audio> element handles MP3 decode natively.
        }

        /**
         * Probe MediaSource support for our chosen MIME. Cached after first call.
         */
        canStream() {
            if (typeof this._canStream === 'boolean') return this._canStream;
            try {
                this._canStream = (
                    typeof window.MediaSource !== 'undefined'
                    && window.MediaSource.isTypeSupported(MEDIA_MIME)
                );
            } catch (err) {
                this._canStream = false;
            }
            return this._canStream;
        }

        /**
         * Primary entry point. Fetches `/tts/stream` for `text` and plays the
         * resulting MP3 stream. Resolves when playback ends (or errors out
         * gracefully). Honors the B1↔B2 mic suspend/resume contract.
         *
         * @param {string} text             plain-text string (server applies
         *                                  _strip_markdown + _fix_mojibake)
         * @param {Object} [opts]
         * @param {string} [opts.session_id]  forwarded to server for logging
         * @param {Object} [opts.voice_options] reserved; server ignores for now
         * @param {boolean} [opts.force=true] if true, cancel any current playback
         * @param {Function} [opts.onstart]   called on <audio>.onplay
         * @returns {Promise<{ok:boolean, error?:Error, fallback?:boolean}>}
         */
        async playTTS(text, opts) {
            opts = opts || {};
            if (!text || typeof text !== 'string' || !text.trim()) {
                return { ok: false, error: new Error('empty text') };
            }
            if (this.isPlaying && opts.force !== false) {
                this.stop();
            }

            // M1 fix: do NOT create an AudioContext here. B2 (stt-client.js)
            // owns publication. If B2 hasn't started yet (e.g. welcome message
            // before mic), suspend/resume hooks below become no-ops via the
            // null-safe _readAudioCtx() helper. The contract: TTS plays first,
            // STT later activation publishes the mic context.

            const body = { text };
            if (opts.session_id) body.session_id = opts.session_id;
            if (opts.voice_options) body.voice_options = opts.voice_options;

            const ctrl = new AbortController();
            this.currentAbortController = ctrl;

            let response;
            try {
                response = await fetch(TTS_ENDPOINT, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                    signal: ctrl.signal,
                });
            } catch (err) {
                this.currentAbortController = null;
                console.error('[VoicePlayer] /tts/stream fetch failed:', err);
                return { ok: false, error: err };
            }

            if (!response.ok) {
                this.currentAbortController = null;
                const status = response.status;
                console.warn(`[VoicePlayer] /tts/stream returned HTTP ${status}`);
                return { ok: false, error: new Error(`HTTP ${status}`) };
            }

            // Stream path — preferred. Falls back to blob playback on any error
            // before the first chunk reaches the decoder.
            if (this.canStream() && response.body && typeof response.body.getReader === 'function') {
                try {
                    return await this._playStreaming(response, opts);
                } catch (err) {
                    console.warn('[VoicePlayer] streaming path failed; falling back to blob:', err);
                    // fall through to blob path below
                }
            }

            // Blob fallback — buffer the full response, then play.
            try {
                const blob = await response.blob();
                return await this._playBlob(blob, opts);
            } catch (err) {
                console.error('[VoicePlayer] blob fallback failed:', err);
                return { ok: false, error: err, fallback: true };
            }
        }

        /**
         * MediaSource-based progressive playback. Returns a promise that
         * resolves on natural end / error.
         */
        _playStreaming(response, opts) {
            return new Promise((resolve) => {
                const mediaSource = new MediaSource();
                const objectUrl = URL.createObjectURL(mediaSource);
                const audio = new Audio();
                audio.src = objectUrl;
                audio.preload = 'auto';

                this.currentAudio = audio;
                this.currentMediaSource = mediaSource;
                this.currentObjectUrl = objectUrl;

                let settled = false;
                let sourceBuffer = null;
                const pendingChunks = [];
                let readerDone = false;
                const reader = response.body.getReader();

                const finish = (outcome) => {
                    if (settled) return;
                    settled = true;
                    this._teardown();
                    resolve(outcome);
                };

                const appendNext = () => {
                    if (!sourceBuffer || sourceBuffer.updating) return;
                    if (pendingChunks.length === 0) {
                        if (readerDone) {
                            try {
                                if (mediaSource.readyState === 'open') {
                                    mediaSource.endOfStream();
                                }
                            } catch (err) { /* ignore */ }
                        }
                        return;
                    }
                    try {
                        sourceBuffer.appendBuffer(pendingChunks.shift());
                    } catch (err) {
                        console.warn('[VoicePlayer] appendBuffer threw:', err);
                        try { reader.cancel(); } catch (e) { /* ignore */ }
                        try { mediaSource.endOfStream('decode'); } catch (e) { /* ignore */ }
                    }
                };

                const pumpReader = async () => {
                    try {
                        // eslint-disable-next-line no-constant-condition
                        while (true) {
                            const { value, done } = await reader.read();
                            if (done) {
                                readerDone = true;
                                appendNext();
                                return;
                            }
                            if (value && value.byteLength) {
                                pendingChunks.push(value);
                                appendNext();
                            }
                        }
                    } catch (err) {
                        readerDone = true;
                        console.warn('[VoicePlayer] reader pump error:', err);
                        try {
                            if (mediaSource.readyState === 'open') {
                                mediaSource.endOfStream('network');
                            }
                        } catch (e) { /* ignore */ }
                    }
                };

                mediaSource.addEventListener('sourceopen', () => {
                    try {
                        sourceBuffer = mediaSource.addSourceBuffer(MEDIA_MIME);
                    } catch (err) {
                        console.warn('[VoicePlayer] addSourceBuffer failed:', err);
                        finish({ ok: false, error: err });
                        return;
                    }
                    sourceBuffer.addEventListener('updateend', appendNext);
                    sourceBuffer.addEventListener('error', (e) => {
                        console.warn('[VoicePlayer] SourceBuffer error:', e);
                    });
                    pumpReader();
                });

                this._wireAudioLifecycle(audio, opts, finish);
                audio.play().catch((err) => {
                    console.warn('[VoicePlayer] audio.play() rejected:', err);
                    finish({ ok: false, error: err });
                });
            });
        }

        /**
         * Plain blob playback — fallback path when streaming pipeline fails
         * before first chunk decodes, or when MediaSource is unsupported.
         */
        _playBlob(blob, opts) {
            return new Promise((resolve) => {
                const objectUrl = URL.createObjectURL(blob);
                const audio = new Audio(objectUrl);
                audio.preload = 'auto';

                this.currentAudio = audio;
                this.currentMediaSource = null;
                this.currentObjectUrl = objectUrl;

                let settled = false;
                const finish = (outcome) => {
                    if (settled) return;
                    settled = true;
                    this._teardown();
                    resolve(outcome);
                };

                this._wireAudioLifecycle(audio, opts, finish);
                audio.play().catch((err) => {
                    console.warn('[VoicePlayer] audio.play() rejected (blob):', err);
                    finish({ ok: false, error: err, fallback: true });
                });
            });
        }

        /**
         * Wire the onplay / onended / onerror lifecycle per §5.1 contract.
         * Used by both the streaming and blob playback paths.
         */
        _wireAudioLifecycle(audio, opts, finish) {
            audio.onplay = async () => {
                this.isPlaying = true;
                // DEAD-MIC FIX (2026-06-06): set the mute FLAG only — do NOT
                // suspend the AudioContext. Chrome does not reliably resume an
                // AudioWorklet fed by a getUserMedia stream after suspend()→
                // resume(); the worklet's process() pump silently stops, so the
                // mic goes deaf after the first TTS even though ctx reports
                // 'running' and the track is 'live' (confirmed via [STT UNMUTE]
                // diag). Echo protection is FULLY preserved by muteSTTOutput
                // (stt-client.js:339 drops every frame while __voiceMicSuspended
                // is true) + getUserMedia echoCancellation:true. The ctx stays
                // running continuously → worklet never stops → mic never deaf.
                window.__voiceMicSuspended = true;
                if (typeof window.updateVoiceState === 'function') {
                    try { window.updateVoiceState('SPEAKING'); } catch (e) { /* ignore */ }
                }
                if (typeof opts.onstart === 'function') {
                    try { opts.onstart(); } catch (e) { /* ignore */ }
                }
            };

            audio.onended = () => {
                this.isPlaying = false;
                // 200 ms echo-tail — discard trailing room acoustic decay before
                // re-opening the mic gate. DEAD-MIC FIX: only clear the mute flag
                // (ctx was never suspended, so no resume needed). The worklet has
                // been pumping the whole time; muteSTTOutput=false simply lets its
                // frames through again.
                setTimeout(() => {
                    window.__voiceMicSuspended = false;
                    if (typeof window.updateVoiceState === 'function') {
                        try { window.updateVoiceState('IDLE'); } catch (e) { /* ignore */ }
                    }
                    finish({ ok: true });
                }, RESUME_DELAY_MS);
            };

            audio.onerror = (ev) => {
                console.error('[TTS] audio playback error:', ev && audio.error);
                this.isPlaying = false;
                // Same recovery as onended — clear the mute gate (no ctx resume
                // needed; ctx was never suspended). NEVER leave mic gated on error.
                setTimeout(() => {
                    window.__voiceMicSuspended = false;
                    if (typeof window.updateVoiceState === 'function') {
                        try { window.updateVoiceState('IDLE'); } catch (e) { /* ignore */ }
                    }
                    finish({ ok: false, error: audio.error || new Error('audio error') });
                }, RESUME_DELAY_MS);
            };
        }

        /**
         * Stop any in-flight playback. Used when a new utterance preempts the
         * current one (force=true) or when the user navigates away.
         */
        stop() {
            try {
                if (this.currentAbortController) {
                    this.currentAbortController.abort();
                }
            } catch (e) { /* ignore */ }
            try {
                if (this.currentAudio) {
                    this.currentAudio.pause();
                    this.currentAudio.src = '';
                }
            } catch (e) { /* ignore */ }
            this._teardown();
            // Clear the mic gate immediately on a hard stop so STT isn't soft-
            // bricked. DEAD-MIC FIX: flag only — ctx is never suspended now.
            window.__voiceMicSuspended = false;
        }

        _teardown() {
            try {
                if (this.currentMediaSource && this.currentMediaSource.readyState === 'open') {
                    this.currentMediaSource.endOfStream();
                }
            } catch (e) { /* ignore */ }
            try {
                if (this.currentObjectUrl) {
                    URL.revokeObjectURL(this.currentObjectUrl);
                }
            } catch (e) { /* ignore */ }
            this.currentAudio = null;
            this.currentMediaSource = null;
            this.currentObjectUrl = null;
            this.currentAbortController = null;
            this.isPlaying = false;
        }
    }

    // -----------------------------------------------------------------------
    // Export — singleton + class
    // -----------------------------------------------------------------------
    window.VoicePlayer = VoicePlayer;
    window.voicePlayer = new VoicePlayer();

    // Convenience: a one-liner the existing simulation.js can call without
    // touching the singleton plumbing.
    window.playTTS = function (text, opts) {
        return window.voicePlayer.playTTS(text, opts);
    };
})();

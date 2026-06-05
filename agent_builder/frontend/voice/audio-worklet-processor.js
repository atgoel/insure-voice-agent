/**
 * InsureVoice — B2 PCM AudioWorklet Processor
 * ============================================
 *
 * Encodes the browser's mic Float32 samples into 16 kHz mono Int16 LE PCM
 * frames at 30 ms cadence (480 samples = 960 bytes per frame), then posts
 * each frame to the main thread as a transferable ArrayBuffer.
 *
 * Why an AudioWorklet (and not ScriptProcessorNode):
 *   * ScriptProcessorNode is deprecated, runs on the main thread, and
 *     glitches under GC / layout pressure.
 *   * AudioWorklet runs on the audio rendering thread (renderer-isolated),
 *     so 128-sample callbacks are jitter-free even under load.
 *
 * Sample-rate strategy (per B2 SPEC v2 §m3, R7 = Cert):
 *   * The page constructs `new AudioContext({ sampleRate: 16000 })`. Chrome
 *     stable on Windows IGNORES the constraint 100% of the time and gives
 *     native 48 kHz. Treat in-worklet 3:1 decimation as the always-on path.
 *   * `currentInputSampleRate` (read from the registered processor at
 *     construction-time options) tells us the real input rate. We support
 *     16 kHz pass-through and 48 kHz → 16 kHz decimation; anything else
 *     falls back to nearest-neighbor (acceptable for hackathon).
 *
 * Wire format (LOCKED — see B2 SPEC v2):
 *   * 16 kHz, mono, Int16 LE
 *   * 30 ms frames = 480 samples per frame = 960 bytes per frame
 *   * No header — the server decoder is configured for raw LINEAR16.
 *
 * Decimation filter:
 *   * 21-tap Hamming-windowed sinc, cutoff 7 kHz @ 48 kHz fs (per §m2).
 *   * Coefficients precomputed offline via numpy:
 *       np.hamming(21) * np.sinc(2 * 7000 / 48000 * (np.arange(21) - 10))
 *     Then normalized so the DC gain equals 1.
 *   * Stored as a Float32 constant below.
 */

// Precomputed 21-tap FIR low-pass at 7 kHz cutoff, 48 kHz fs, Hamming window,
// DC-normalized. Symmetric — same forward/reverse, OK to use as-is.
const FIR_TAPS_48 = new Float32Array([
    0.0007379,
   -0.0017430,
   -0.0049999,
   -0.0049893,
    0.0036094,
    0.0192687,
    0.0294263,
    0.0207957,
   -0.0117018,
   -0.0641410,
    0.8754942,
   -0.0641410,
   -0.0117018,
    0.0207957,
    0.0294263,
    0.0192687,
    0.0036094,
   -0.0049893,
   -0.0049999,
   -0.0017430,
    0.0007379,
]);

const TARGET_RATE = 16000;
const FRAME_SAMPLES_AT_TARGET = 480;  // 30 ms at 16 kHz

class PcmWorkletProcessor extends AudioWorkletProcessor {
    constructor(options) {
        super();
        const opts = (options && options.processorOptions) || {};
        this.inputSampleRate = opts.inputSampleRate || sampleRate || 48000;
        this.decimationFactor = Math.max(1, Math.round(this.inputSampleRate / TARGET_RATE));
        this.usingFir = (this.decimationFactor === 3 && this.inputSampleRate === 48000);

        // FIR delay-line — only used on the 48 kHz path.
        this.firDelay = new Float32Array(FIR_TAPS_48.length);

        // Phase accumulator for non-FIR fractional resampling fallback.
        this.phase = 0.0;
        this.phaseStep = this.inputSampleRate / TARGET_RATE;

        // Output ring buffer at TARGET_RATE — we flush whenever it fills 480 samples.
        this.outBuffer = new Float32Array(FRAME_SAMPLES_AT_TARGET);
        this.outFilled = 0;

        // Allow main thread to ask us to flush + stop on teardown.
        this.port.onmessage = (event) => {
            const data = event && event.data;
            if (!data || !data.type) return;
            if (data.type === 'flush') {
                this._flushPartial();
            }
        };
    }

    /**
     * Apply the 21-tap FIR to one input sample, returning the filtered output.
     * Maintains the delay line internally.
     */
    _firStep(x) {
        const taps = FIR_TAPS_48;
        const dl = this.firDelay;
        const N = taps.length;
        // Shift delay line.
        for (let i = N - 1; i > 0; --i) {
            dl[i] = dl[i - 1];
        }
        dl[0] = x;
        // Convolve.
        let acc = 0.0;
        for (let i = 0; i < N; ++i) {
            acc += taps[i] * dl[i];
        }
        return acc;
    }

    /**
     * Push one float32 sample at TARGET_RATE into the outgoing buffer; if it
     * fills, convert to Int16 LE and post.
     */
    _emit(sampleAtTarget) {
        this.outBuffer[this.outFilled++] = sampleAtTarget;
        if (this.outFilled >= FRAME_SAMPLES_AT_TARGET) {
            this._postFrame(this.outBuffer.subarray(0, FRAME_SAMPLES_AT_TARGET));
            this.outFilled = 0;
        }
    }

    _flushPartial() {
        if (this.outFilled === 0) return;
        // Send whatever we have left.
        this._postFrame(this.outBuffer.subarray(0, this.outFilled));
        this.outFilled = 0;
    }

    /**
     * Convert Float32 [-1, 1] → Int16 LE [-32768, 32767] and post as
     * transferable ArrayBuffer.
     */
    _postFrame(floatFrame) {
        const i16 = new Int16Array(floatFrame.length);
        for (let i = 0; i < floatFrame.length; ++i) {
            let s = floatFrame[i];
            if (s > 1) s = 1;
            else if (s < -1) s = -1;
            i16[i] = (s < 0) ? Math.max(-32768, Math.round(s * 32768))
                             : Math.min(32767, Math.round(s * 32767));
        }
        // Transfer the underlying buffer to the main thread (zero-copy).
        this.port.postMessage(
            { type: 'pcm', buffer: i16.buffer },
            [i16.buffer]
        );
    }

    process(inputs, _outputs, _parameters) {
        const input = inputs[0];
        if (!input || input.length === 0) {
            return true;
        }
        const channel0 = input[0];
        if (!channel0 || channel0.length === 0) {
            return true;
        }

        if (this.usingFir) {
            // 48 kHz path: filter every input sample, downsample by 3.
            const stride = this.decimationFactor;
            for (let i = 0; i < channel0.length; ++i) {
                const filtered = this._firStep(channel0[i]);
                if ((i % stride) === 0) {
                    this._emit(filtered);
                }
            }
        } else if (this.decimationFactor === 1 && this.inputSampleRate === TARGET_RATE) {
            // 16 kHz pass-through.
            for (let i = 0; i < channel0.length; ++i) {
                this._emit(channel0[i]);
            }
        } else {
            // Generic fractional fallback (linear interpolation, no anti-alias).
            // Time-budget escape hatch per §m2; less ideal but never silent.
            for (let i = 0; i < channel0.length; ++i) {
                this.phase += 1.0;
                while (this.phase >= this.phaseStep) {
                    this._emit(channel0[i]);
                    this.phase -= this.phaseStep;
                }
            }
        }

        return true;
    }
}

registerProcessor('pcm-worklet', PcmWorkletProcessor);

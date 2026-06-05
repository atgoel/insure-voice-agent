"""
B2 — STT v2 streaming WebSocket bridge
=======================================

Replaces the browser ``webkitSpeechRecognition`` STT path with a server-side
Google Cloud Speech-to-Text v2 (Chirp 2) bidirectional gRPC stream, exposed as
an FastAPI WebSocket endpoint at ``/stt/stream``.

The browser's ``AudioWorklet`` (see ``frontend/voice/audio-worklet-processor.js``)
emits 16 kHz mono PCM Int16 LE frames at ~30 ms cadence. ``stt-client.js``
forwards them as binary WebSocket frames to this handler. We pump them into
``SpeechAsyncClient.streaming_recognize`` and forward ``interim`` / ``final`` /
``voice_activity_events`` back to the FE as JSON.

Why server-side STT (vs. browser):
  * Server-side VAD (``speech_end_timeout=800ms``) removes the brittle 1.2s
    client-side ``setTimeout`` debounce that caused premature mid-thought
    cutoffs in Day 7 live testing.
  * Chirp 2 ``en-IN`` outperforms Chrome's webkit STT on insurance-domain
    vocabulary (lakhs/crore, product names, etc.).
  * Single long-lived WebSocket multiplexes audio + transcripts — avoids the
    SSL-EOF / RemoteDisconnected behavior the G4 baseline observed under
    rapid HTTP POST cadence.

Hackathon constraints (do NOT break):
  * GCP-pure: only ``google-cloud-speech`` (proper Speech-to-Text v2 API).
  * No third-party VAD, no ML models we ship ourselves (Devpost rule risk).
  * Auth: matches ``/invoke`` posture — unauthenticated within Cloud Run, IAM
    is the only boundary (see B2 SPEC v2 §13.1).

Spec source-of-truth: ``B2_SPEC_v1.md`` + ``B2_SPEC_v2.md`` (delta).
Implementer reads v1 + v2 together; v2 wins on conflict.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import WebSocket, WebSocketDisconnect

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Speech-to-Text v2 SDK imports (lazy at module scope so unit tests that mock
# the client can import this module without google-cloud-speech installed).
# ---------------------------------------------------------------------------
try:
    from google.cloud.speech_v2 import SpeechAsyncClient
    from google.cloud.speech_v2.types import (
        ExplicitDecodingConfig,
        RecognitionConfig,
        RecognitionFeatures,
        StreamingRecognitionConfig,
        StreamingRecognitionFeatures,
        StreamingRecognizeRequest,
    )
    from google.protobuf.duration_pb2 import Duration

    _SDK_OK = True
    _SDK_ERR: Optional[str] = None
except Exception as _e:  # pragma: no cover - exercised only when SDK absent
    SpeechAsyncClient = None  # type: ignore
    ExplicitDecodingConfig = None  # type: ignore
    RecognitionConfig = None  # type: ignore
    RecognitionFeatures = None  # type: ignore
    StreamingRecognitionConfig = None  # type: ignore
    StreamingRecognitionFeatures = None  # type: ignore
    StreamingRecognizeRequest = None  # type: ignore
    Duration = None  # type: ignore
    _SDK_OK = False
    _SDK_ERR = repr(_e)
    _log.warning(
        "google-cloud-speech SDK not importable; /stt/stream will reject "
        "with SDK_UNAVAILABLE until the runtime image installs the package. "
        "Underlying error: %s",
        _SDK_ERR,
    )


# ---------------------------------------------------------------------------
# Config constants (LOCKED per B2 SPEC v2 §"Locked items for implementer")
# ---------------------------------------------------------------------------

_GCP_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "voice-sales-agent")
# REGIONAL endpoint required for chirp_2 — the model is NOT available in
# locations/global. Day 8 live-test discovery (rev 00032-lmm StreamingRecognize
# returned: InvalidArgument 'The model "chirp_2" does not exist in the location
# named "global"'). Switching to us-central1 keeps chirp_2 available.
_GCP_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
_RECOGNIZER = f"projects/{_GCP_PROJECT}/locations/{_GCP_LOCATION}/recognizers/_"
_SPEECH_API_ENDPOINT = f"{_GCP_LOCATION}-speech.googleapis.com"

_SAMPLE_RATE_HZ = 16000
_CHANNELS = 1
_LANGUAGE_CODE = "en-IN"  # B2 SPEC v2 §13.4: hardcoded for hackathon
_PRIMARY_MODEL = "chirp_2"
_FALLBACK_MODEL = "latest_long"  # G6 fallback if chirp_2/en-IN unavailable

# VAD timing knobs — LOCKED. Re-tune only on B7 golden eval evidence.
_SPEECH_START_TIMEOUT_S = 10
_SPEECH_END_TIMEOUT_NANOS = 800_000_000  # 800 ms

# Stream lifecycle bounds.
_GRPC_STREAM_MAX_S = 290  # 4 min 50 s — well under Google's 5 min hard cap
_WS_IDLE_TIMEOUT_S = 180  # long-lived stream: the idle clock is CONTINUOUS
                          # across turns (no per-turn WS teardown), and muted-
                          # during-TTS windows send ZERO binary frames — a long
                          # TTS reply + think-time can span tens of seconds with
                          # no frames. 30s killed the stream mid-conversation;
                          # the 15s FE silence nudge fires TTS (still no STT
                          # frames) so it does NOT reset this clock. 180s clears
                          # back-to-back long replies + think-time while staying
                          # safely under the 290s gRPC rollover ceiling
                          # (_GRPC_STREAM_MAX_S), which refreshes independently.
_AUDIO_QUEUE_MAX = 64  # backpressure ceiling (~2 s of 30 ms frames)

# Backpressure log throttle (B2 SPEC v2 §13.3).
_BACKPRESSURE_LOG_INTERVAL_S = 1.0


# ---------------------------------------------------------------------------
# Module-level Speech client — warmed on FastAPI startup to amortize TLS +
# gRPC channel cost on cold-start (Risk R2).
# ---------------------------------------------------------------------------
_speech_client: Optional["SpeechAsyncClient"] = None
_speech_client_lock = asyncio.Lock()


async def _get_speech_client() -> Optional["SpeechAsyncClient"]:
    """Return the lazily-initialized SpeechAsyncClient (or None if SDK absent)."""
    global _speech_client
    if not _SDK_OK:
        return None
    if _speech_client is not None:
        return _speech_client
    async with _speech_client_lock:
        if _speech_client is None:
            # Regional endpoint required for chirp_2 (not available in global).
            from google.api_core.client_options import ClientOptions
            _speech_client = SpeechAsyncClient(
                client_options=ClientOptions(api_endpoint=_SPEECH_API_ENDPOINT),
            )
            _log.info(
                "[STT] SpeechAsyncClient initialized (project=%s, endpoint=%s, recognizer=%s)",
                _GCP_PROJECT, _SPEECH_API_ENDPOINT, _RECOGNIZER,
            )
    return _speech_client


def _build_streaming_config(model: str = _PRIMARY_MODEL) -> "StreamingRecognitionConfig":
    """Build the once-per-stream ``StreamingRecognitionConfig`` proto.

    Per B2 SPEC v2 §m1: ``auto_decoding_config`` is dropped (oneof violation
    with ``explicit_decoding_config``). We always know the wire format
    (16 kHz Int16 LE PCM mono — see audio-worklet-processor.js).
    """
    recognition_config = RecognitionConfig(
        explicit_decoding_config=ExplicitDecodingConfig(
            encoding=ExplicitDecodingConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=_SAMPLE_RATE_HZ,
            audio_channel_count=_CHANNELS,
        ),
        language_codes=[_LANGUAGE_CODE],
        model=model,
        features=RecognitionFeatures(
            enable_automatic_punctuation=True,
            enable_word_time_offsets=False,
            profanity_filter=False,
            max_alternatives=1,
        ),
    )
    streaming_features = StreamingRecognitionFeatures(
        enable_voice_activity_events=True,
        interim_results=True,
        voice_activity_timeout=StreamingRecognitionFeatures.VoiceActivityTimeout(
            speech_start_timeout=Duration(seconds=_SPEECH_START_TIMEOUT_S),
            speech_end_timeout=Duration(seconds=0, nanos=_SPEECH_END_TIMEOUT_NANOS),
        ),
    )
    return StreamingRecognitionConfig(
        config=recognition_config,
        streaming_features=streaming_features,
    )


# ---------------------------------------------------------------------------
# Per-connection session state
# ---------------------------------------------------------------------------


class _SttSession:
    """One per WebSocket connection. Owns the audio pump + gRPC stream pump."""

    def __init__(self, ws: WebSocket, session_id: str, model: str):
        self.ws = ws
        self.session_id = session_id
        self.model = model
        self.audio_q: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=_AUDIO_QUEUE_MAX)
        self.last_frame_ts: float = time.monotonic()
        self.last_backpressure_warn_ts: float = 0.0
        self.client_closed: bool = False
        self.dropped_frames: int = 0

    def _now(self) -> float:
        return time.time()

    async def _send_json(self, payload: Dict[str, Any]) -> None:
        """Send a JSON text frame; swallow disconnect errors."""
        if self.client_closed:
            return
        try:
            await self.ws.send_text(json.dumps(payload))
        except (WebSocketDisconnect, RuntimeError) as exc:
            _log.info("[STT %s] client disconnected during send: %s", self.session_id, exc)
            self.client_closed = True

    # ------------------------------------------------------------------ #
    # Audio ingress: WS binary frames → bounded queue
    # ------------------------------------------------------------------ #

    async def ingest_audio(self, frame: bytes) -> None:
        """Push a binary audio frame onto the queue with overflow handling."""
        self.last_frame_ts = time.monotonic()
        try:
            self.audio_q.put_nowait(frame)
        except asyncio.QueueFull:
            # Drop the OLDEST frame (preserve recency of speech) and append new.
            try:
                self.audio_q.get_nowait()
                self.audio_q.task_done()
            except asyncio.QueueEmpty:
                pass
            try:
                self.audio_q.put_nowait(frame)
            except asyncio.QueueFull:
                pass
            self.dropped_frames += 1
            self._maybe_log_backpressure()

    def _maybe_log_backpressure(self) -> None:
        """Throttle backpressure WARN logs to 1/second (B2 SPEC v2 §13.3)."""
        now = time.monotonic()
        if now - self.last_backpressure_warn_ts >= _BACKPRESSURE_LOG_INTERVAL_S:
            _log.warning(
                "[STT %s] audio queue saturated; dropped %d frames cumulative",
                self.session_id,
                self.dropped_frames,
            )
            self.last_backpressure_warn_ts = now

    async def signal_end(self) -> None:
        """Sentinel pushed to drain the gRPC pump cleanly."""
        await self.audio_q.put(None)

    # ------------------------------------------------------------------ #
    # gRPC request iterator: yields config first, then audio frames
    # ------------------------------------------------------------------ #

    async def _request_iter(
        self, streaming_config: "StreamingRecognitionConfig"
    ) -> AsyncIterator["StreamingRecognizeRequest"]:
        # First request carries config only.
        yield StreamingRecognizeRequest(
            recognizer=_RECOGNIZER,
            streaming_config=streaming_config,
        )

        deadline = time.monotonic() + _GRPC_STREAM_MAX_S
        while True:
            timeout_remaining = deadline - time.monotonic()
            if timeout_remaining <= 0:
                _log.info(
                    "[STT %s] gRPC stream rollover deadline reached; ending iter",
                    self.session_id,
                )
                return
            try:
                frame = await asyncio.wait_for(
                    self.audio_q.get(),
                    timeout=min(timeout_remaining, 5.0),
                )
            except asyncio.TimeoutError:
                # Idle frame slot — keep looping; outer task handles WS idle.
                continue
            if frame is None:
                # Sentinel: client requested graceful end.
                return
            yield StreamingRecognizeRequest(audio=frame)

    # ------------------------------------------------------------------ #
    # Response pump: gRPC → WS JSON
    # ------------------------------------------------------------------ #

    async def _pump_responses(
        self, responses: AsyncIterator[Any]
    ) -> None:
        """Translate gRPC StreamingRecognizeResponse messages to FE JSON.

        Per B2 SPEC v2 §M3 the ordering of ``final`` vs ``SPEECH_ACTIVITY_END``
        is non-strict for short utterances. We forward events as they arrive
        and let the FE commit on ``final`` only.
        """
        async for resp in responses:
            ts = self._now()

            # Voice activity events (BEGIN / END).
            if resp.speech_event_type:
                event_name = resp.speech_event_type.name
                # Accept any of: SPEECH_ACTIVITY_BEGIN / END (proto enum names).
                # Older SDKs may emit SPEECH_EVENT_UNSPECIFIED — skip those.
                if event_name and event_name != "SPEECH_EVENT_UNSPECIFIED":
                    await self._send_json(
                        {"type": "event", "event": event_name, "ts": ts}
                    )

            # Recognition results — interim + final.
            for result in resp.results or []:
                if not result.alternatives:
                    continue
                alt = result.alternatives[0]
                text = alt.transcript or ""
                if result.is_final:
                    await self._send_json(
                        {
                            "type": "final",
                            "text": text,
                            "confidence": float(getattr(alt, "confidence", 0.0) or 0.0),
                            "ts": ts,
                        }
                    )
                else:
                    await self._send_json(
                        {
                            "type": "interim",
                            "text": text,
                            "stability": float(getattr(result, "stability", 0.0) or 0.0),
                            "ts": ts,
                        }
                    )

    # ------------------------------------------------------------------ #
    # Single gRPC streaming_recognize attempt (used by the rollover loop)
    # ------------------------------------------------------------------ #

    async def _run_one_grpc_stream(
        self,
        client: "SpeechAsyncClient",
        streaming_config: "StreamingRecognitionConfig",
    ) -> None:
        """Run a single ``streaming_recognize`` call until iter is exhausted."""
        responses = await client.streaming_recognize(
            requests=self._request_iter(streaming_config),
        )
        await self._pump_responses(responses)


# ---------------------------------------------------------------------------
# WebSocket handler entry point
# ---------------------------------------------------------------------------


async def stt_stream_handler(ws: WebSocket) -> None:
    """FastAPI WebSocket handler for ``/stt/stream``.

    Lifecycle:
      1. ``await ws.accept()``.
      2. Read first text frame; expect ``{"type": "config", ...}``.
      3. Reply ``{"type": "ready"}``.
      4. Spawn (a) audio-receive task and (b) gRPC-pump task; race them.
      5. On graceful end / disconnect / error, send ``{"type": "closed", ...}``
         and close the WebSocket.

    Any state owned by ``/invoke`` (``_INTAKE_BY_SESSION[session_id]`` etc.) is
    preserved across STT WS close/reopen cycles per B2 SPEC v2 §M4.
    """
    await ws.accept()
    session_id = "unknown"
    sess: Optional[_SttSession] = None

    try:
        # ---- 1. Negotiate config ----------------------------------------
        try:
            first_msg = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        except asyncio.TimeoutError:
            await ws.close(code=1008, reason="config_timeout")
            return
        try:
            cfg = json.loads(first_msg)
        except json.JSONDecodeError:
            await ws.send_text(
                json.dumps({"type": "error", "code": "BAD_CONFIG", "detail": "first frame must be JSON"})
            )
            await ws.close(code=1008, reason="bad_config")
            return

        if cfg.get("type") != "config":
            await ws.send_text(
                json.dumps(
                    {"type": "error", "code": "BAD_CONFIG", "detail": "first frame must be type=config"}
                )
            )
            await ws.close(code=1008, reason="bad_config")
            return

        session_id = cfg.get("session_id") or str(uuid.uuid4())

        # SDK presence gate.
        if not _SDK_OK:
            await ws.send_text(
                json.dumps(
                    {
                        "type": "error",
                        "code": "SDK_UNAVAILABLE",
                        "detail": "google-cloud-speech not installed on runtime",
                    }
                )
            )
            await ws.close(code=1011, reason="sdk_unavailable")
            return

        client = await _get_speech_client()
        if client is None:
            await ws.send_text(
                json.dumps(
                    {"type": "error", "code": "SDK_UNAVAILABLE", "detail": _SDK_ERR or "init failed"}
                )
            )
            await ws.close(code=1011, reason="sdk_unavailable")
            return

        sess = _SttSession(ws=ws, session_id=session_id, model=_PRIMARY_MODEL)
        await sess._send_json({"type": "ready", "session_id": session_id})

        # ---- 2. Spin two cooperating tasks ------------------------------
        ingress_task = asyncio.create_task(_ingress_loop(sess), name=f"stt-ingress-{session_id}")
        grpc_task = asyncio.create_task(_grpc_loop(sess, client), name=f"stt-grpc-{session_id}")

        # Wait for either to finish (graceful end OR error).
        done, pending = await asyncio.wait(
            {ingress_task, grpc_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for p in pending:
            p.cancel()
            try:
                await p
            except (asyncio.CancelledError, Exception):
                pass

        # Surface any exception from the completed tasks.
        for d in done:
            exc = d.exception()
            if exc is not None and not isinstance(exc, (asyncio.CancelledError, WebSocketDisconnect)):
                _log.exception(
                    "[STT %s] task %s ended with error: %r",
                    session_id,
                    d.get_name(),
                    exc,
                )

        await sess._send_json({"type": "closed", "reason": "rpc_done"})

    except WebSocketDisconnect:
        _log.info("[STT %s] WebSocket disconnected by client", session_id)
    except Exception as exc:  # pragma: no cover - defensive
        _log.exception("[STT %s] handler crashed: %r", session_id, exc)
        try:
            await ws.send_text(
                json.dumps({"type": "error", "code": "INTERNAL", "detail": str(exc)})
            )
        except Exception:
            pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Cooperating tasks
# ---------------------------------------------------------------------------


async def _ingress_loop(sess: _SttSession) -> None:
    """Read WS frames; route binary→audio queue, text→control."""
    while True:
        # Idle timeout: kill if no audio frame for _WS_IDLE_TIMEOUT_S.
        recv_timeout = max(1.0, _WS_IDLE_TIMEOUT_S)
        try:
            msg = await asyncio.wait_for(sess.ws.receive(), timeout=recv_timeout)
        except asyncio.TimeoutError:
            _log.info("[STT %s] WS idle timeout (%ds)", sess.session_id, _WS_IDLE_TIMEOUT_S)
            await sess._send_json(
                {"type": "closed", "reason": "idle_timeout"}
            )
            await sess.signal_end()
            return

        # Handle the FastAPI envelope.
        msg_type = msg.get("type")
        if msg_type == "websocket.disconnect":
            sess.client_closed = True
            await sess.signal_end()
            return

        # Binary audio frame.
        if "bytes" in msg and msg["bytes"] is not None:
            await sess.ingest_audio(msg["bytes"])
            continue

        # Text control frame.
        if "text" in msg and msg["text"] is not None:
            try:
                payload = json.loads(msg["text"])
            except json.JSONDecodeError:
                continue
            if payload.get("type") == "end":
                _log.info("[STT %s] client requested graceful end", sess.session_id)
                await sess.signal_end()
                return
            # Unknown text frames are ignored (forward-compatible).
            continue


async def _grpc_loop(sess: _SttSession, client: "SpeechAsyncClient") -> None:
    """Drive ``streaming_recognize`` calls; rollover every ~5 min.

    On gRPC error we attempt ONE silent reconnect (B2 SPEC v1 §4 error
    recovery). A second failure surfaces a STT_RPC_ERROR + close 1011.
    """
    streaming_config = _build_streaming_config(model=sess.model)
    reconnect_attempted = False

    while True:
        try:
            await sess._run_one_grpc_stream(client, streaming_config)
            # Stream completed cleanly (rollover deadline or client_end).
            if sess.client_closed:
                return
            # Rollover: open a fresh gRPC stream transparently.
            _log.info("[STT %s] rolling gRPC stream", sess.session_id)
            reconnect_attempted = False
            continue
        except Exception as exc:
            _log.warning(
                "[STT %s] streaming_recognize failed: %r (reconnect_attempted=%s)",
                sess.session_id,
                exc,
                reconnect_attempted,
            )
            if reconnect_attempted:
                await sess._send_json(
                    {"type": "error", "code": "STT_RPC_ERROR", "detail": repr(exc)}
                )
                return
            reconnect_attempted = True
            await sess._send_json(
                {"type": "event", "event": "RECONNECT", "ts": time.time()}
            )
            # Brief backoff before retry.
            await asyncio.sleep(0.25)
            continue

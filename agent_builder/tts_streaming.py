"""
InsureVoice — Chirp 3 HD Streaming TTS module (B1)
===================================================
Server-side text-to-speech using Google Cloud's Chirp 3 HD voice family.
Replaces the on-device `SpeechSynthesisUtterance` path with a deterministic,
high-quality voice rendered at 24 kHz MP3 and streamed back to the FE through
FastAPI's `StreamingResponse`.

Public API (consumed by main.py `/tts/stream` handler):
    VOICE_NAME        — locked voice identifier
    SAMPLE_RATE_HZ    — 24000 (matches Chirp 3 HD GA per G3 PoC)
    MODEL             — informational tier label (Chirp 3 HD)
    SPEAKING_RATE     — 1.0, locked per SPEC v2 §"Lock decisions"
    MAX_INPUT_CHARS   — hard cap on synthesizable text per SPEC v2 AC-B1.6
    PerIPRateLimiter  — D7 rate limiter helper (30 req/min per source IP)
    synthesize_chunks — async generator yielding MP3 byte chunks for streaming
    synthesize_bytes  — convenience: collect chunks into a single bytes blob
    _strip_markdown   — pre-processing (also calls _fix_mojibake on every input)
    _fix_mojibake     — L-004 cp1252 round-trip fix (mandatory per M1)

References:
    - Spec: tasks/2026-06-04_hackathon_day7_polish_bugs/p3_tier_b_voice_stack/reports/B1_SPEC_v2.md
    - PoC:  tasks/.../G3_Chirp3HD_POC_Result.md (curl-confirmed, 1.57 s cold)
    - Locked decisions D7-D9 in Locked_Decisions.md
"""

from __future__ import annotations

import asyncio
import base64
import collections
import logging
import os
import re
import time
from typing import AsyncGenerator, Deque, Dict, Optional, Tuple

import httpx

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Locked synthesis configuration (SPEC v2 §"Lock decisions")
# ---------------------------------------------------------------------------
VOICE_NAME: str = "en-IN-Chirp3-HD-Aoede"
SAMPLE_RATE_HZ: int = 24000
MODEL: str = "chirp-3-hd"  # informational; the v1 REST endpoint takes voice name
SPEAKING_RATE: float = 1.0
LANGUAGE_CODE: str = "en-IN"
AUDIO_ENCODING: str = "MP3"  # G3 PoC confirmed plain MP3 over /text:synthesize

# Hard cap — protects against runaway costs per AC-B1.6 worst-case envelope.
MAX_INPUT_CHARS: int = 5000

# Google Cloud TTS REST endpoint (G3 PoC verified working at this URL).
TTS_ENDPOINT: str = "https://texttospeech.googleapis.com/v1/text:synthesize"

# Streaming chunk size — splits the MP3 blob into N-byte slices when handing
# off to FastAPI. Chirp 3 HD currently returns full payloads via REST, so we
# emulate streaming on the server side. Small enough that the FE's MediaSource
# `appendBuffer` calls fire frequently for low time-to-first-audio.
_STREAM_CHUNK_BYTES: int = 4096

# Mojibake markers (mirrors main.py:78 _MOJIBAKE_MARKERS to keep behavior aligned
# without forcing a runtime import cycle when this module is loaded standalone).
_MOJIBAKE_MARKERS: Tuple[str, ...] = ("â", "Ã", "Â", "â€", "â‚")


# ---------------------------------------------------------------------------
# Text normalization — markdown strip + mojibake fix
# ---------------------------------------------------------------------------

def _fix_mojibake(s: str) -> str:
    """
    Reverse cp1252 → utf-8 double-encoding mojibake (L-004).

    Mirrors main.py:80 implementation. Applied to EVERY TTS input per
    SPEC v2 §"M1 — _fix_mojibake is MANDATORY on every tts_streaming input".
    """
    if not isinstance(s, str) or not s:
        return s
    if not any(m in s for m in _MOJIBAKE_MARKERS):
        return s
    try:
        return s.encode("cp1252", errors="ignore").decode("utf-8", errors="ignore")
    except Exception:
        return s


# Pre-compiled markdown patterns — cheaper than chained .replace() calls when
# this is on the hot path of every TTS request.
_MD_HTML_TAG = re.compile(r"<[^>]*>")
_MD_BOLD_AST = re.compile(r"\*\*([^*]+)\*\*")
_MD_BOLD_UND = re.compile(r"__([^_]+)__")
_MD_ITALIC_AST = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_MD_ITALIC_UND = re.compile(r"(?<!_)_([^_]+)_(?!_)")
_MD_CODE = re.compile(r"`([^`]+)`")
_MD_HEADING = re.compile(r"^#+\s+", re.MULTILINE)
_MD_RESIDUAL = re.compile(r"[*_~`]")
_MD_WS = re.compile(r"\s+")


def _strip_markdown(text: str) -> str:
    """
    Normalize TTS input: fix mojibake, strip markdown/HTML, collapse whitespace.

    The mojibake fix is mandatory and unconditional — this is the single egress
    point for the new `/tts/stream` boundary, so we cannot rely on upstream
    sanitizers (per L-004 + SPEC v2 §M1).
    """
    if not text:
        return ""

    # M1 mandate — _fix_mojibake on EVERY input, not "if present".
    cleaned = _fix_mojibake(text)

    cleaned = _MD_HTML_TAG.sub("", cleaned)
    cleaned = _MD_BOLD_AST.sub(r"\1", cleaned)
    cleaned = _MD_BOLD_UND.sub(r"\1", cleaned)
    cleaned = _MD_ITALIC_AST.sub(r"\1", cleaned)
    cleaned = _MD_ITALIC_UND.sub(r"\1", cleaned)
    cleaned = _MD_CODE.sub(r"\1", cleaned)
    cleaned = _MD_HEADING.sub("", cleaned)
    cleaned = _MD_RESIDUAL.sub("", cleaned)
    cleaned = _MD_WS.sub(" ", cleaned).strip()
    return cleaned


# ---------------------------------------------------------------------------
# Per-IP rate limiter — D7 / SPEC v2 M4 Option B
# ---------------------------------------------------------------------------

class PerIPRateLimiter:
    """
    Sliding-window rate limiter (in-memory, per source IP).

    Default policy from D7: 30 requests / 60 seconds per IP. Returns False
    from `allow()` when the cap is exceeded; the FastAPI handler then maps
    that to HTTP 429.

    Caveat (R10): in-memory state does NOT survive Cloud Run scale-out.
    Adequate for the demo's `--max-instances=1` footprint; for production
    swap in Redis or Memorystore.
    """

    def __init__(self, max_requests: int = 30, window_seconds: float = 60.0) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._buckets: Dict[str, Deque[float]] = {}
        # Lock guards bucket creation + deque mutation under concurrent requests.
        self._lock = asyncio.Lock()

    async def allow(self, ip: str) -> bool:
        """Return True if `ip` is below cap, False if it has hit the limit."""
        if not ip:
            ip = "_unknown_"
        now = time.monotonic()
        cutoff = now - self.window_seconds
        async with self._lock:
            bucket = self._buckets.get(ip)
            if bucket is None:
                bucket = collections.deque()
                self._buckets[ip] = bucket
            # Evict expired entries from the head of the deque.
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.max_requests:
                return False
            bucket.append(now)
            return True

    async def gc(self) -> int:
        """
        Evict empty buckets to keep memory bounded under high IP cardinality.
        Returns count of buckets removed. Safe to call from a background task
        or skip entirely for the hackathon footprint.
        """
        removed = 0
        now = time.monotonic()
        cutoff = now - self.window_seconds
        async with self._lock:
            stale_ips = []
            for ip, bucket in self._buckets.items():
                while bucket and bucket[0] < cutoff:
                    bucket.popleft()
                if not bucket:
                    stale_ips.append(ip)
            for ip in stale_ips:
                self._buckets.pop(ip, None)
                removed += 1
        return removed


# ---------------------------------------------------------------------------
# Authenticated HTTP client — google-auth + httpx.AsyncClient
# ---------------------------------------------------------------------------

# Module-level client + credentials are cached for warm-call latency
# (AC-B1.1(a) p50 < 800 ms). google.auth.default() is expensive on cold paths;
# we lazily initialize once and reuse.
_HTTP_CLIENT: Optional[httpx.AsyncClient] = None
_CREDS = None  # google.auth.credentials.Credentials


def _ensure_credentials():
    """Lazily fetch ADC credentials. Called from the request path."""
    global _CREDS
    if _CREDS is not None:
        return _CREDS
    # Imported locally to avoid an import-time crash if google-auth is missing
    # in some test environment. google-cloud-aiplatform pulls google-auth into
    # the project already (requirements.txt:7).
    from google.auth import default as _google_auth_default

    creds, _project = _google_auth_default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    _CREDS = creds
    return _CREDS


def _refresh_token_blocking(creds) -> str:
    """
    Synchronously refresh the bearer token. Wrapped in `asyncio.to_thread`
    by callers — google-auth's transport.requests is sync-only.
    """
    from google.auth.transport.requests import Request as _AuthRequest

    if not creds.valid:
        creds.refresh(_AuthRequest())
    return creds.token


async def _get_bearer_token() -> str:
    creds = _ensure_credentials()
    # The first call performs network I/O; subsequent calls hit the cached
    # token until it expires (~1 hour), so the await overhead is negligible
    # on the warm path.
    return await asyncio.to_thread(_refresh_token_blocking, creds)


def _ensure_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        # 8s read timeout — comfortably above the 1.6 s cold-call observed in
        # G3 PoC, well below AC-B1.10 (cold start < 6 s) so a hung request
        # doesn't soft-brick the FE waiting for first chunk.
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=3.0, read=8.0, write=5.0, pool=5.0),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
    return _HTTP_CLIENT


# ---------------------------------------------------------------------------
# Synthesis core
# ---------------------------------------------------------------------------

async def _synthesize_request(text: str) -> bytes:
    """
    POST `/v1/text:synthesize` and return the raw MP3 bytes.

    Request shape mirrors G3 PoC exactly (verified-working at 1.57 s cold).
    Raises httpx.HTTPStatusError on non-2xx, RuntimeError on missing audio.
    """
    if not text:
        raise ValueError("synthesize text cannot be empty")
    if len(text) > MAX_INPUT_CHARS:
        raise ValueError(
            f"synthesize text exceeds MAX_INPUT_CHARS ({len(text)} > {MAX_INPUT_CHARS})"
        )

    token = await _get_bearer_token()
    client = _ensure_client()

    payload = {
        "input": {"text": text},
        "voice": {
            "languageCode": LANGUAGE_CODE,
            "name": VOICE_NAME,
        },
        "audioConfig": {
            "audioEncoding": AUDIO_ENCODING,
            "sampleRateHertz": SAMPLE_RATE_HZ,
            "speakingRate": SPEAKING_RATE,
        },
    }
    # Optional GCP project hint — the REST endpoint infers project from the
    # bearer token, but x-goog-user-project keeps quota attribution clean
    # when ADC carries multiple projects.
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    if project:
        headers["x-goog-user-project"] = project

    resp = await client.post(TTS_ENDPOINT, json=payload, headers=headers)
    resp.raise_for_status()
    body = resp.json()
    audio_b64 = body.get("audioContent")
    if not audio_b64:
        raise RuntimeError(
            "Chirp 3 HD response missing audioContent field — payload=%r"
            % {k: v for k, v in body.items() if k != "audioContent"}
        )
    return base64.b64decode(audio_b64)


async def synthesize_bytes(text: str) -> bytes:
    """
    Public wrapper: normalize input, call Chirp 3 HD, return MP3 bytes.

    All callers — `/tts/stream`, internal pipeline code, tests — go through
    this function so the M1 mojibake fix is unconditional.
    """
    cleaned = _strip_markdown(text)
    if not cleaned:
        return b""
    return await _synthesize_request(cleaned)


async def synthesize_chunks(
    text: str, chunk_size: int = _STREAM_CHUNK_BYTES
) -> AsyncGenerator[bytes, None]:
    """
    Yield MP3 byte chunks suitable for `fastapi.responses.StreamingResponse`.

    Cancels cleanly when the consumer disconnects (R14 mitigation):
    `StreamingResponse` raises `asyncio.CancelledError` into this generator
    on client-disconnect, which propagates out without leaking httpx state
    (the client is module-level and reused).
    """
    audio = await synthesize_bytes(text)
    if not audio:
        return
    for offset in range(0, len(audio), chunk_size):
        yield audio[offset : offset + chunk_size]


async def aclose() -> None:
    """
    Tear down the module-level httpx client. Useful from FastAPI's shutdown
    handler; not strictly required on Cloud Run because the process exits
    on revision teardown.
    """
    global _HTTP_CLIENT
    if _HTTP_CLIENT is not None:
        await _HTTP_CLIENT.aclose()
        _HTTP_CLIENT = None


# ---------------------------------------------------------------------------
# Standalone smoke test — `python -m agent_builder.tts_streaming`
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    async def _smoke() -> int:
        sample = (
            "Hi Abhishek, this is the InsureVoice agent speaking with Chirp three HD. "
            "Can you hear me clearly?"
        )
        # M1 mojibake check — feed a garbled rupee-sign sample to confirm
        # the egress fix fires.
        garbled = "Premium is â‚¹500 per month."
        fixed = _strip_markdown(garbled)
        # ascii() avoids cp1252-stdout crashes on Windows consoles.
        print(f"[smoke] mojibake fix: {ascii(garbled)} -> {ascii(fixed)}")
        assert "₹500" in fixed, "M1 mojibake fix did not produce ₹500"

        # Rate limiter sanity: 31st call inside the window must be denied.
        rl = PerIPRateLimiter(max_requests=30, window_seconds=60.0)
        outcomes = [await rl.allow("203.0.113.5") for _ in range(31)]
        denied = sum(1 for ok in outcomes if not ok)
        print(f"[smoke] rate limit: 31 calls => denied={denied} (expected 1)")
        assert denied == 1, "rate limiter did not cap at 30/min"

        # Live synthesis (only runs when ADC is configured; skip cleanly otherwise).
        try:
            t0 = time.perf_counter()
            audio = await synthesize_bytes(sample)
            dt = time.perf_counter() - t0
            print(
                f"[smoke] synthesize ok: {len(audio)} bytes in {dt:.3f}s "
                f"(voice={VOICE_NAME}, rate={SAMPLE_RATE_HZ}Hz)"
            )
        except Exception as exc:
            print(f"[smoke] live synth skipped/failed: {type(exc).__name__}: {exc}")
        finally:
            await aclose()
        return 0

    sys.exit(asyncio.run(_smoke()))

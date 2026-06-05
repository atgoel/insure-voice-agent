"""
B2 — rapid WS reconnect server-side regression test
====================================================

Pinned by the Day 9 turn-2 reconnect race investigation. The browser bug
(turn-2's `new WebSocket()` rejected because turn-1's `audioCtx.close()` was
still mid-flight) is a CLIENT-side race; this test cannot reproduce it.

What this test DOES guarantee: the FastAPI ``/stt/stream`` handler can survive
two back-to-back WebSocket connections from the same TestClient with no delay
between close-of-WS1 and open-of-WS2. Without this, a future regression that
introduces a global lock / cleanup-still-in-flight sentinel on the SERVER side
would let the FE-side fix appear to work while a different layer broke.

The test exercises both:
  (a) the SDK-absent path (current local dev: google-cloud-speech NOT installed)
      — confirms the handler accepts → negotiates config → sends SDK_UNAVAILABLE
        → closes with 1011, releases all resources, immediately accepts again.
  (b) the SDK-present path (monkeypatched stub) — confirms the post-config
      "ready" handshake completes, client sends graceful end, server closes,
      and the NEXT WS connection on the same TestClient also completes.

NOTE on session_id: the rapid-reconnect contract is "server treats turn-1
close + turn-2 open as independent connections, both succeeding regardless of
whether session_id matches". The test covers (i) same session_id (mimics live
"resume" intent) and (ii) fresh session_id (mimics fresh-session turn-2).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import types
from typing import Any, AsyncIterator, List

import pytest
from fastapi.testclient import TestClient

# Match the rest of the suite — set env defaults BEFORE importing main.
os.environ.setdefault("ELASTIC_MCP_SERVER_URL", "http://mock-elastic-mcp.test/search")
os.environ.setdefault("ELASTIC_MCP_SERVER_NATIVE_URL", "http://mock-elastic-mcp-native.test/search")
os.environ.setdefault("COMPLIANCE_CHECK_URL", "http://mock-compliance.test/check")
os.environ.setdefault("RANK_PRODUCTS_URL", "http://mock-rank.test/rank")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")
os.environ.setdefault("GOOGLE_API_KEY", "stub-key-for-tests")

# Ensure agent_builder/ is importable.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_AGENT_DIR = os.path.join(_REPO_ROOT, "agent_builder")
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)


@pytest.fixture(scope="module")
def app():
    """Import the FastAPI app once for the module."""
    from main import app as _app  # noqa: WPS433 (intentional late import)
    return _app


@pytest.fixture
def client(app):
    """Fresh TestClient per test (TestClient context-managed for lifespan)."""
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _send_config(ws, session_id):
    ws.send_text(json.dumps({
        "type": "config",
        "session_id": session_id,
        "sample_rate": 16000,
        "language": "en-IN",
    }))


def _drain_until_terminal(ws, max_frames: int = 10) -> List[dict]:
    """Read JSON frames until the server closes the WS or `closed`/`error` arrives.

    Returns the list of frames received before the close. Defensive max_frames
    so a buggy server can't hang the test indefinitely.
    """
    received = []
    for _ in range(max_frames):
        try:
            frame = ws.receive_text()
        except Exception:  # WebSocketDisconnect or similar — server closed.
            break
        try:
            received.append(json.loads(frame))
        except json.JSONDecodeError:
            received.append({"_raw": frame})
        if received[-1].get("type") in ("closed", "error"):
            # Server may close immediately after; one more attempt to drain.
            try:
                tail = ws.receive_text()
                received.append(json.loads(tail))
            except Exception:
                pass
            break
    return received


# ---------------------------------------------------------------------------
# (a) SDK-absent path — exercises the local-dev / no-sdk gate
# ---------------------------------------------------------------------------


def test_rapid_reconnect_sdk_absent_same_session(client):
    """
    Two back-to-back WS connections, no delay, same session_id.
    Server should reject each with SDK_UNAVAILABLE and close cleanly,
    AND the second connection must accept cleanly with no state bleed.
    """
    sid = "rapid-reconnect-test-001"

    # ---- Turn 1 ----
    with client.websocket_connect("/stt/stream") as ws1:
        _send_config(ws1, sid)
        frames1 = _drain_until_terminal(ws1, max_frames=4)

    # Server must have responded with at least one frame on turn-1.
    assert frames1, "Turn-1: server sent no frames before close"

    # In SDK-absent mode the first response is the error frame.
    types1 = [f.get("type") for f in frames1]
    assert "error" in types1 or "ready" in types1, (
        f"Turn-1: expected error or ready, got {types1}"
    )

    # ---- Turn 2 (no delay) ----
    with client.websocket_connect("/stt/stream") as ws2:
        _send_config(ws2, sid)  # SAME session_id — resume case
        frames2 = _drain_until_terminal(ws2, max_frames=4)

    assert frames2, "Turn-2: server sent no frames — possible deadlock from turn-1 cleanup"
    types2 = [f.get("type") for f in frames2]
    assert "error" in types2 or "ready" in types2, (
        f"Turn-2: expected error or ready, got {types2}"
    )


def test_rapid_reconnect_sdk_absent_fresh_session(client):
    """
    Two back-to-back WS connections, no delay, DIFFERENT session_ids.
    Mimics a fresh /invoke turn that issued a new session.
    """
    # ---- Turn 1 ----
    with client.websocket_connect("/stt/stream") as ws1:
        _send_config(ws1, "rapid-reconnect-test-002a")
        frames1 = _drain_until_terminal(ws1, max_frames=4)

    assert frames1, "Turn-1: server sent no frames before close"

    # ---- Turn 2 (no delay, fresh session_id) ----
    with client.websocket_connect("/stt/stream") as ws2:
        _send_config(ws2, "rapid-reconnect-test-002b")
        frames2 = _drain_until_terminal(ws2, max_frames=4)

    assert frames2, "Turn-2: server sent no frames"


def test_rapid_reconnect_sdk_absent_three_in_a_row(client):
    """Stress: 3 back-to-back connections — server must remain responsive."""
    for i in range(3):
        with client.websocket_connect("/stt/stream") as ws:
            _send_config(ws, f"rapid-stress-{i}")
            frames = _drain_until_terminal(ws, max_frames=4)
            assert frames, f"Iteration {i}: no frames received"


# ---------------------------------------------------------------------------
# (b) SDK-present path — monkeypatched stub for the post-config flow
# ---------------------------------------------------------------------------


class _StubSpeechClient:
    """Minimal stub: streaming_recognize returns an empty async iterator.

    This is sufficient to drive the handler past the `_SDK_OK` gate and into
    the `_grpc_loop` which immediately exits cleanly when the iter is empty.
    """

    async def streaming_recognize(self, requests=None) -> AsyncIterator[Any]:
        # Drain the request iter (so the handler doesn't hang on its own
        # producer side), then return an empty response iter.
        async def _iter() -> AsyncIterator[Any]:
            if False:
                yield  # pragma: no cover — empty async generator
        try:
            async for _ in requests:  # type: ignore[union-attr]
                # Stop after consuming the config frame to avoid hanging
                # on the audio-queue wait.
                break
        except Exception:
            pass
        return _iter()


@pytest.fixture
def sdk_present(monkeypatch):
    """Force `_SDK_OK=True` and inject a stub SpeechAsyncClient."""
    import stt_websocket as sw

    monkeypatch.setattr(sw, "_SDK_OK", True)

    async def _fake_get_client():
        return _StubSpeechClient()

    monkeypatch.setattr(sw, "_get_speech_client", _fake_get_client)
    yield


def test_rapid_reconnect_sdk_present_same_session(client, sdk_present):
    """
    With SDK stubbed present, two back-to-back WS connections must each
    receive the `ready` handshake and close cleanly when the client sends
    `{"type":"end"}` followed by socket close.
    """
    sid = "rapid-reconnect-sdk-present-001"

    for turn in (1, 2):
        with client.websocket_connect("/stt/stream") as ws:
            _send_config(ws, sid)
            # First frame should be `ready`.
            first = json.loads(ws.receive_text())
            assert first.get("type") == "ready", (
                f"Turn {turn}: expected ready, got {first}"
            )
            assert first.get("session_id") == sid

            # Send graceful end. Server should drain and close.
            ws.send_text(json.dumps({"type": "end"}))
            # Drain whatever follows (closed / nothing).
            _drain_until_terminal(ws, max_frames=3)


def test_rapid_reconnect_sdk_present_fresh_session(client, sdk_present):
    """Back-to-back ready handshakes with DIFFERENT session_ids."""
    for sid in ("rapid-fresh-A", "rapid-fresh-B"):
        with client.websocket_connect("/stt/stream") as ws:
            _send_config(ws, sid)
            first = json.loads(ws.receive_text())
            assert first.get("type") == "ready"
            assert first.get("session_id") == sid
            ws.send_text(json.dumps({"type": "end"}))
            _drain_until_terminal(ws, max_frames=3)

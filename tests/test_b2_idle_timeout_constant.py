"""
B2 — WS idle-timeout constant guard (Strategy 2: long-lived stream)
===================================================================

Strategy 2 makes the STT WebSocket long-lived across the whole conversation
instead of tearing it down per turn. During TTS the mic is muted, so ZERO
binary frames reach the server for the length of a reply + user think-time.
The server idle timeout (`_WS_IDLE_TIMEOUT_S`) is keyed on `ws.receive()`
returning ANY frame within the window — so a muted window with no frames
counts down against it.

At the old 30s value, a long recommendation read-out + think-time killed the
stream mid-conversation (the historical turn-3 death class). The fix bumps the
constant to 180s (still safely under the 290s `_GRPC_STREAM_MAX_S` gRPC
rollover ceiling).

This is a one-assertion guard so a future edit that silently reverts the
constant (or pushes it past the gRPC ceiling) fails loudly here instead of
only surfacing in a live multi-turn browser test we can't run headless.

See: tasks/2026-06-09_hackathon_day9/reports/Live_Voice_Stream_Spec_v1.md
     (Change 6 / Critical Q1) and the Plan-Reviewer audit (Blocking Q B1).
"""

from __future__ import annotations

import os
import sys

# Match the rest of the suite — set env defaults BEFORE importing the module.
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


def test_ws_idle_timeout_is_180():
    """Strategy 2 requires the idle timeout bumped 30 -> 180s."""
    import stt_websocket as sw

    assert sw._WS_IDLE_TIMEOUT_S == 180, (
        "Strategy 2 (long-lived stream) requires _WS_IDLE_TIMEOUT_S == 180. "
        f"Got {sw._WS_IDLE_TIMEOUT_S}. A muted-during-TTS window sends zero "
        "binary frames; a smaller value kills the stream mid-conversation "
        "(the turn-3 dead-mic class). Do not revert."
    )


def test_ws_idle_timeout_below_grpc_rollover():
    """The WS idle ceiling must stay under the gRPC stream rollover bound,
    so gRPC rollover (transparent) never races a premature WS idle-close."""
    import stt_websocket as sw

    assert sw._WS_IDLE_TIMEOUT_S < sw._GRPC_STREAM_MAX_S, (
        f"_WS_IDLE_TIMEOUT_S ({sw._WS_IDLE_TIMEOUT_S}) must be < "
        f"_GRPC_STREAM_MAX_S ({sw._GRPC_STREAM_MAX_S}); otherwise the WS can "
        "idle-close before the gRPC stream rolls over."
    )

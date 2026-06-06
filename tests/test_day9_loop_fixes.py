"""
Agent-loop runaway fixes — unit tests (2026-06-06)
===================================================
Root cause: the ADK runner is a `while True` that only terminates on a pure-text
final response; a before_model_callback forcing tool_config{mode:ANY} can prevent
that forever (the 72.9s recommendation loop / >150s follow-up loop that burned
tokens for 2.5min after client disconnect). These tests lock the fixes WITHOUT
launching the live server (which is exactly what made the loop expensive to test).

Covered:
  - Fix A: _force_tool_call_mid_pipeline
      * releases the force (forced_tool=None) once recommend_and_explain has run
        in the CURRENT invocation  (the latch — kills the recommendation loop)
      * scopes the scan to the current invocation_id, so a PRIOR turn's
        rank_products function_response cannot re-arm forcing on a later turn
      * still forces the normal mid-pipeline transitions (regression guard)
  - Fix B: the /invoke consumer event-count backstop constant exists and is sane.

Run locally:
    pytest tests/test_day9_loop_fixes.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# agent_builder/ on sys.path so `import agent_definition` works.
_AGENT_BUILDER = str(Path(__file__).parent.parent / "agent_builder")
if _AGENT_BUILDER not in sys.path:
    sys.path.insert(0, _AGENT_BUILDER)

# Stub env so importing agent_definition (reads tool URLs at import) succeeds.
os.environ.setdefault("ELASTIC_MCP_SERVER_URL", "http://stub.test")
os.environ.setdefault("ELASTIC_MCP_SERVER_NATIVE_URL", "http://stub.test")
os.environ.setdefault("COMPLIANCE_CHECK_URL", "http://stub.test")
os.environ.setdefault("RANK_PRODUCTS_URL", "http://stub.test")
os.environ.setdefault("SIMULATE_PREMIUM_URL", "http://stub.test")

import agent_definition  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic ADK objects — match exactly what the callback reads:
#   event.invocation_id
#   event.content.parts[i].function_response.name / .response
#   llm_request.config.tool_config.function_calling_config.mode / allowed_function_names
# ---------------------------------------------------------------------------

def _fr_event(name: str, response: dict, invocation_id: str = "inv-current"):
    """A session event carrying ONE function_response part."""
    fr = MagicMock()
    fr.name = name
    fr.response = response
    part = MagicMock()
    part.function_response = fr
    # The callback also does getattr(p, "function_call"/"text") elsewhere; ensure
    # this part only looks like a function_response.
    content = MagicMock()
    content.parts = [part]
    ev = MagicMock()
    ev.content = content
    ev.invocation_id = invocation_id
    return ev


class _FakeCtx:
    """Stands in for CallbackContext._invocation_context."""
    def __init__(self, events, invocation_id="inv-current"):
        sess = MagicMock()
        sess.events = events
        inv = MagicMock()
        inv.session = sess
        inv.invocation_id = invocation_id
        self._invocation_context = inv


class _FakeLlmRequest:
    def __init__(self):
        self.config = None


def _forced_tool_after(events, invocation_id="inv-current"):
    """Run the real callback against synthetic events; return the forced tool
    name (or None) by inspecting the tool_config it wrote on llm_request."""
    ctx = _FakeCtx(events, invocation_id=invocation_id)
    req = _FakeLlmRequest()
    agent_definition._force_tool_call_mid_pipeline(ctx, req)  # type: ignore[arg-type]
    cfg = getattr(req, "config", None)
    tc = getattr(cfg, "tool_config", None) if cfg is not None else None
    fcc = getattr(tc, "function_calling_config", None) if tc is not None else None
    if fcc is None:
        return None  # AUTO (no forcing) — config never set
    mode = getattr(fcc, "mode", None)
    if mode != "ANY":
        return None
    allowed = list(getattr(fcc, "allowed_function_names", []) or [])
    return allowed[0] if allowed else None


# ---------------------------------------------------------------------------
# Fix A — the latch + invocation scoping
# ---------------------------------------------------------------------------

def test_mid_pipeline_transitions_still_forced():
    """Regression: the normal pipeline forcing must still work."""
    assert _forced_tool_after(
        [_fr_event("search_products", {"candidates": [{"id": "p1"}]})]
    ) == "compliance_check"
    assert _forced_tool_after(
        [_fr_event("compliance_check", {"passed": [{"id": "p1"}]})]
    ) == "rank_products"
    assert _forced_tool_after(
        [_fr_event("rank_products", {"top_3": [{"id": "p1"}, {"id": "p2"}, {"id": "p3"}]})]
    ) == "recommend_and_explain"


def test_latch_releases_after_recommend_and_explain():
    """THE FIX: once recommend_and_explain has produced output in this
    invocation, the callback must STOP forcing (return None) so the model can
    emit final text and run_async can terminate. This is what kills the 72.9s
    recommendation loop."""
    events = [
        _fr_event("search_products", {"candidates": [{"id": "p1"}]}),
        _fr_event("compliance_check", {"passed": [{"id": "p1"}]}),
        _fr_event("rank_products", {"top_3": [{"id": "p1"}, {"id": "p2"}, {"id": "p3"}]}),
        _fr_event("recommend_and_explain", {"text": "Here are your top 3..."}),
    ]
    assert _forced_tool_after(events) is None


def test_latch_holds_even_if_pipeline_tool_reappears_after_recommend():
    """The exact loop trap: after recommend_and_explain, flash-lite re-emits a
    rank_products call (prompt 'STOP' doesn't bind a small model — L-001). The
    latch must STILL release because recommend_and_explain ran THIS invocation —
    otherwise the re-emitted rank_products FR re-arms forcing forever."""
    events = [
        _fr_event("rank_products", {"top_3": [{"id": "p1"}, {"id": "p2"}, {"id": "p3"}]}),
        _fr_event("recommend_and_explain", {"text": "Here are your top 3..."}),
        # flash-lite re-enters the pipeline (the bug trigger):
        _fr_event("rank_products", {"top_3": [{"id": "p1"}, {"id": "p2"}, {"id": "p3"}]}),
    ]
    assert _forced_tool_after(events) is None


def test_stale_prior_turn_fr_does_not_rearm_forcing():
    """Invocation scoping: a PRIOR turn's rank_products FR (different
    invocation_id) must be IGNORED, so a fresh turn with no current-invocation
    pipeline activity does NOT get forced into recommend_and_explain. This is
    what stops a post-recommendation follow-up turn from re-driving the
    pipeline on stale history."""
    events = [
        # prior turn (already produced a recommendation last time):
        _fr_event("rank_products", {"top_3": [{"id": "p1"}]}, invocation_id="inv-OLD"),
        _fr_event("recommend_and_explain", {"text": "old"}, invocation_id="inv-OLD"),
    ]
    # Current invocation has NO function_responses of its own.
    assert _forced_tool_after(events, invocation_id="inv-current") is None


def test_empty_payload_does_not_force():
    """Guard: an empty/failed tool result must not force the next tool."""
    assert _forced_tool_after(
        [_fr_event("search_products", {"candidates": []})]
    ) is None
    assert _forced_tool_after([]) is None


# ---------------------------------------------------------------------------
# Fix B — the consumer event-count backstop (constant sanity, no live server)
# ---------------------------------------------------------------------------

def test_invoke_consumer_has_event_backstop_constant():
    """The /invoke consumer must define a hard event cap so a runaway tool-
    forcing loop self-terminates. We assert the constant is present in main.py
    and in a sane range (well above the ~8-10 happy path, well below infinity)."""
    main_src = (Path(_AGENT_BUILDER) / "main.py").read_text(encoding="utf-8")
    assert "_MAX_AGENT_EVENTS" in main_src, "event-count backstop constant missing"
    assert "AGENT_LOOP_BACKSTOP" in main_src, "backstop break/log missing"
    # extract the integer and bound-check it
    import re
    m = re.search(r"_MAX_AGENT_EVENTS\s*=\s*(\d+)", main_src)
    assert m, "could not parse _MAX_AGENT_EVENTS value"
    val = int(m.group(1))
    assert 12 <= val <= 60, f"_MAX_AGENT_EVENTS={val} outside sane range"


def test_classifier_has_loop_guard_and_cap():
    """Fix C-L2 sentinel: the classifier callback must release to AUTO once the
    tool has produced a response (loop-guard), and the classifier runner must
    have a hard event cap. Both prevent the classifier-side unbounded-force loop
    (hazard #2) from hanging a follow-up turn."""
    clf_src = (Path(_AGENT_BUILDER) / "intent_classifier.py").read_text(encoding="utf-8")
    assert "_CLF_MAX_EVENTS" in clf_src, "classifier event cap missing"
    assert "LOOP GUARD" in clf_src, "classifier loop-guard missing"

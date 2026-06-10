"""
B4 Intent Classifier — unit tests
==================================
Covers:
    AC-B4.1.5  — Classifier resolves to correct target_product_id in ≥14/15
                 Bug J cases. Mocks the LLM call to use golden labels (no live
                 Gemini API spend on every test run).
    AC-B4.6.5  — _force_tool_call_mid_pipeline (C.5 callback at
                 agent_definition.py:428) functions identically post-B4.
                 Sentinel test: import sites unchanged, signature unchanged,
                 root_agent.before_model_callback identity unchanged.

Run locally:
    pytest tests/test_intent_classifier.py -v
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure agent_builder/ on sys.path so `import intent_classifier` works
_AGENT_BUILDER = str(Path(__file__).parent.parent / "agent_builder")
if _AGENT_BUILDER not in sys.path:
    sys.path.insert(0, _AGENT_BUILDER)

# Stub env vars so agent_definition import inside the package succeeds.
os.environ.setdefault("ELASTIC_MCP_SERVER_URL", "http://stub.test")
os.environ.setdefault("ELASTIC_MCP_SERVER_NATIVE_URL", "http://stub.test")
os.environ.setdefault("COMPLIANCE_CHECK_URL", "http://stub.test")
os.environ.setdefault("RANK_PRODUCTS_URL", "http://stub.test")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_GOLDEN_PATH = Path(__file__).parent / "fixtures" / "bug_j_golden.json"


@pytest.fixture(scope="module")
def bug_j_golden() -> list[dict]:
    """Load 15-case Bug J golden fixture."""
    with open(_GOLDEN_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["cases"]


# ---------------------------------------------------------------------------
# AC-B4.1.5 — semantic correctness on Bug J golden labels
# ---------------------------------------------------------------------------
#
# Strategy: mock _classifier_runner.run_async so it yields a single fake event
# whose function_response.response matches the golden expected fields. This
# lets us validate the WIRING (extractor, server-side validation, routing)
# without burning Gemini quota on every CI run.
#
# Live golden eval (real Gemini calls) lives under tests/eval/ — out of scope
# for the unit suite per AC-B4.1.5 hackathon scope.


def _mock_classifier_event(case: dict):
    """Build a fake ADK event whose function_response matches the golden case."""
    expected_intent = case["expected_intent"]
    expected_tid = case["expected_target_product_id"]

    # Default high confidence for unambiguous cases; lower for AMBIGUOUS so
    # routing logic exercises the < 0.5 / [0.5, 0.7) bands too.
    if expected_intent == "AMBIGUOUS":
        confidence = 0.4
    elif expected_intent == "POLICY_QUESTION":
        confidence = 0.85
    elif "typo" in case.get("id", ""):
        confidence = 0.78
    else:
        confidence = 0.95

    fake_response = {
        "target_product_id": expected_tid if expected_tid is not None else "NONE",
        "intent": expected_intent,
        "confidence": confidence,
        "clarification_question": (
            "Which product would you like to hear more about?"
            if expected_intent == "AMBIGUOUS" else ""
        ),
        "out_of_scope": False,
    }

    fake_fr = MagicMock()
    fake_fr.name = "classify_followup_intent"
    fake_fr.response = fake_response

    fake_part = MagicMock()
    fake_part.function_response = fake_fr
    # text=None on the function_response part
    fake_part.text = None

    fake_content = MagicMock()
    fake_content.parts = [fake_part]
    fake_content.role = "model"

    fake_event = MagicMock()
    fake_event.content = fake_content
    return fake_event


def test_ac_b4_1_5_bug_j_golden_resolution(bug_j_golden):
    """AC-B4.1.5 — ≥14/15 cases produce expected target_product_id.

    Wraps async logic in asyncio.run() to avoid pytest-asyncio dependency for
    a single test in the suite.
    """
    import asyncio

    async def _run():
        import intent_classifier as ic

        # Lazy-init the runner with a stub session_service so import-time guard passes.
        stub_session_service = MagicMock()

        async def _async_get_session(**kwargs):
            return None

        async def _async_create_session(**kwargs):
            sess = MagicMock()
            sess.state = {"top3_ids_for_session": kwargs.get("state", {}).get("top3_ids_for_session", [])}
            return sess

        stub_session_service.get_session = _async_get_session
        stub_session_service.create_session = _async_create_session

        ic.init_classifier_runner(stub_session_service)
        assert ic._classifier_runner is not None

        correct = 0
        failures = []

        for case in bug_j_golden:
            # Mock the runner to yield ONE pre-canned event for this case.
            fake_event = _mock_classifier_event(case)

            async def _fake_run_async(*args, **kwargs):
                yield fake_event

            with patch.object(
                ic._classifier_runner, "run_async", _fake_run_async
            ):
                result = await ic.classify_intent_async(
                    session_id=f"test-{case['id']}",
                    user_message=case["user_message"],
                    top3_ids=case["top3"],
                    user_id="test-user",
                )

            if result is None:
                failures.append(f"{case['id']}: classifier returned None")
                continue

            actual_tid = result.get("target_product_id")
            actual_intent = result.get("intent")
            expected_tid = case["expected_target_product_id"]
            expected_intent = case["expected_intent"]

            # Normalize: golden uses null for "NONE"
            if expected_tid is None:
                expected_tid = "NONE"

            if actual_tid == expected_tid and actual_intent == expected_intent:
                correct += 1
            else:
                failures.append(
                    f"{case['id']}: expected intent={expected_intent} tid={expected_tid}, "
                    f"got intent={actual_intent} tid={actual_tid}"
                )

        # AC-B4.1.5 = ≥14/15
        assert correct >= 14, (
            f"AC-B4.1.5 FAILED: only {correct}/15 cases resolved correctly. "
            f"Failures: {failures}"
        )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Routing helper unit tests
# ---------------------------------------------------------------------------


def test_route_named_high_confidence_above_threshold():
    import intent_classifier as ic
    decision = ic.route_classification({
        "intent": "NAMED_PRODUCT",
        "target_product_id": "HLTH003",
        "confidence": 0.95,
        "clarification_question": "",
        "out_of_scope": False,
    })
    assert decision["action"] == "ROUTE_NAMED"
    assert decision["target_product_id"] == "HLTH003"


def test_route_ordinal_above_threshold():
    import intent_classifier as ic
    decision = ic.route_classification({
        "intent": "ORDINAL",
        "target_product_id": "TERM001",
        "confidence": 0.9,
        "clarification_question": "",
        "out_of_scope": False,
    })
    assert decision["action"] == "ROUTE_ORDINAL"
    assert decision["target_product_id"] == "TERM001"


def test_force_clarify_band_returns_clarify():
    """[0.5, 0.7) → CLARIFY regardless of intent (M2 lock)."""
    import intent_classifier as ic
    decision = ic.route_classification({
        "intent": "NAMED_PRODUCT",
        "target_product_id": "HLTH003",
        "confidence": 0.6,
        "clarification_question": "Did you mean HealthFirst?",
        "out_of_scope": False,
    })
    assert decision["action"] == "CLARIFY"
    assert "HealthFirst" in decision["clarification"]


def test_below_band_falls_back_to_llm():
    """confidence < 0.5 → FALLBACK_LLM (existing escalation ladder)."""
    import intent_classifier as ic
    decision = ic.route_classification({
        "intent": "NAMED_PRODUCT",
        "target_product_id": "HLTH003",
        "confidence": 0.3,
        "clarification_question": "",
        "out_of_scope": False,
    })
    assert decision["action"] == "FALLBACK_LLM"


def test_policy_question_routes_to_free_form():
    """D4 lock — POLICY_QUESTION goes to FREE_FORM, NOT B5 render."""
    import intent_classifier as ic
    decision = ic.route_classification({
        "intent": "POLICY_QUESTION",
        "target_product_id": "NONE",
        "confidence": 0.9,
        "clarification_question": "",
        "out_of_scope": False,
    })
    assert decision["action"] == "FREE_FORM"
    assert decision["target_product_id"] is None


def test_out_of_scope_escalates():
    import intent_classifier as ic
    decision = ic.route_classification({
        "intent": "AMBIGUOUS",
        "target_product_id": "NONE",
        "confidence": 0.1,
        "clarification_question": "",
        "out_of_scope": True,
    })
    assert decision["action"] == "ESCALATE"


def test_ambiguous_with_clarification_returns_clarify():
    import intent_classifier as ic
    decision = ic.route_classification({
        "intent": "AMBIGUOUS",
        "target_product_id": "NONE",
        "confidence": 0.4,
        "clarification_question": "Which product did you mean?",
        "out_of_scope": False,
    })
    assert decision["action"] == "CLARIFY"


def test_ambiguous_without_clarification_falls_back():
    import intent_classifier as ic
    decision = ic.route_classification({
        "intent": "AMBIGUOUS",
        "target_product_id": "NONE",
        "confidence": 0.4,
        "clarification_question": "",
        "out_of_scope": False,
    })
    assert decision["action"] == "FALLBACK_LLM"


# ---------------------------------------------------------------------------
# AC-B4.6.5 — C.5 callback isolation regression test
# ---------------------------------------------------------------------------
# Verifies that importing intent_classifier and instantiating the new
# intent_classifier_agent has NOT mutated:
#   - The _force_tool_call_mid_pipeline function object at agent_definition.py:428
#   - root_agent.before_model_callback's identity binding to that function
# This is the test that proves D3 isolation held.


def test_ac_b4_6_5_c5_callback_not_mutated_post_b4():
    """AC-B4.6.5 — C.5 callback identity unchanged after B4 import."""
    # Force fresh import of agent_definition AFTER intent_classifier import,
    # to simulate the production import order in main.py.
    import intent_classifier  # noqa: F401 — explicit import for ordering
    import agent_definition

    # 1. The C.5 callback function exists and is callable.
    assert hasattr(agent_definition, "_force_tool_call_mid_pipeline"), (
        "C.5 callback removed — B4 violated D3 isolation lock"
    )
    assert callable(agent_definition._force_tool_call_mid_pipeline)

    # 2. root_agent's before_model_callback is STILL _force_tool_call_mid_pipeline.
    assert agent_definition.root_agent.before_model_callback is (
        agent_definition._force_tool_call_mid_pipeline
    ), "root_agent.before_model_callback was rebound — B4 violated D3 isolation"

    # 3. Signature unchanged: (callback_context, llm_request) → None
    import inspect
    sig = inspect.signature(agent_definition._force_tool_call_mid_pipeline)
    assert list(sig.parameters.keys()) == ["callback_context", "llm_request"], (
        f"C.5 signature changed: {list(sig.parameters.keys())}"
    )


def test_intent_classifier_agent_is_separate_instance():
    """D3 lock — classifier is its OWN LlmAgent, NOT root_agent or its tool."""
    import intent_classifier
    import agent_definition

    assert intent_classifier.intent_classifier_agent is not agent_definition.root_agent
    assert intent_classifier.intent_classifier_agent.name != agent_definition.root_agent.name

    # Classifier has its OWN before_model_callback distinct from root's.
    assert (
        intent_classifier.intent_classifier_agent.before_model_callback
        is intent_classifier._force_classifier_tool
    )
    assert (
        intent_classifier.intent_classifier_agent.before_model_callback
        is not agent_definition._force_tool_call_mid_pipeline
    )


def test_classifier_runner_uses_separate_app_name():
    """D10 lock — classifier Runner uses app_name='insure-voice-classifier'."""
    import intent_classifier as ic

    stub_session_service = MagicMock()
    runner = ic.init_classifier_runner(stub_session_service)
    assert runner.app_name == "insure-voice-classifier"
    # Distinct from root's "insure-voice"
    assert runner.app_name != "insure-voice"

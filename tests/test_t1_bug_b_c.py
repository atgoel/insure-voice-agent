"""
T1 in-proc gate — unit tests for Bug B (bail-out override) + Bug C
(programmatic product_type injection + defense-in-depth gamma filter).

Unit-level only: validates the core deterministic logic that lives inside
agent_builder/main.py without requiring ADK runner / Vertex AI / Cloud
Functions. Full-pipeline integration is covered by:
  - the local uvicorn gate (SPEC v2 §8.2.5), and
  - the live HTTP gate against the deployed revision (SPEC v2 §8.3).

Tests cover ACs:
  AC-B1  — _looks_like_bailout helper behaviour (6 cases)
  AC-B2  — bail-out override fires when bail-out string + top3 >= 1
  AC-B3  — override does NOT fire when top3 == 0
  AC-B4  — override does NOT fire on legitimate non-bail-out text
  AC-B6  — C.5b deterministic template renders correctly for n=1, n=2, n=3
  AC-C2  — programmatic-path product_type extraction logic
  AC-C3  — gamma filter drops mismatched product_type
"""
import logging
import os
import re
import sys

import pytest

# Direct symbol imports from agent_builder/main.py. We only need:
#   _BAILOUT_PHRASES, _looks_like_bailout — pure helpers, no ADK init needed
# We avoid `import main` because main.py instantiates an ADK Runner at
# module-load time which requires GCP creds. Instead we re-define the
# helpers here from the SAME source-of-truth substring list, and parse
# main.py for the _BAILOUT_PHRASES tuple to verify drift-protection.
HERE = os.path.dirname(os.path.abspath(__file__))
MAIN_PY = os.path.join(HERE, "..", "agent_builder", "main.py")


def _load_main_source():
    with open(MAIN_PY, encoding="utf-8") as f:
        return f.read()


def _extract_bailout_phrases_from_source(src):
    """Pull the _BAILOUT_PHRASES tuple from main.py via regex so tests
    stay locked to the actual deployed pattern list, not a copy."""
    m = re.search(
        r"_BAILOUT_PHRASES\s*=\s*\((?P<body>.*?)\)",
        src,
        re.DOTALL,
    )
    assert m is not None, "Could not locate _BAILOUT_PHRASES in main.py"
    body = m.group("body")
    phrases = re.findall(r'"([^"]+)"', body)
    assert phrases, f"No quoted phrases extracted from {body!r}"
    return tuple(phrases)


_PHRASES = _extract_bailout_phrases_from_source(_load_main_source())


def _looks_like_bailout(s):
    """Mirror of main.py:_looks_like_bailout — sourced from the same tuple."""
    if not s:
        return False
    s_lower = s.strip().lower()
    return any(p in s_lower for p in _PHRASES)


# ---------------------------------------------------------------------------
# AC-B1 — bail-out detection helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        # Day 6 surfaced bail-out wording (verbatim from session bc2396e6).
        ("I wasn't able to find eligible products for your profile.", True),
        ("i was not able to find eligible products", True),
        ("  I WASN'T ABLE TO FIND ELIGIBLE PRODUCTS  ", True),
        # Day 7 live test surfaced new bail-out wording (different sub-agent
        # phrasing, same intent — must also trigger override). See
        # tasks/2026-06-04_hackathon_day7_polish_bugs/reports/Day7_Live_Test_Findings.md
        ("I could not find products matching your criteria; could you broaden your goal?", True),
        ("I could not find products matching your criteria", True),
        ("could you broaden your goal?", True),
        ("Could you broaden your goal please", True),
        # Negative cases — legitimate prose / null inputs.
        ("Based on your profile, here are my top recommendations.", False),
        ("", False),
        (None, False),
    ],
)
def test_b1_looks_like_bailout(text, expected):
    """AC-B1: helper correctly identifies bail-out vs non-bail-out vs null."""
    assert _looks_like_bailout(text) is expected


def test_b1_phrase_list_is_non_trivial():
    """Defensive: phrase list must contain at least the canonical bail-out."""
    assert any("eligible products" in p for p in _PHRASES), (
        f"_BAILOUT_PHRASES is missing the canonical bail-out string: {_PHRASES}"
    )
    assert len(_PHRASES) >= 2, f"Expected >=2 phrase variants, got {_PHRASES}"


# ---------------------------------------------------------------------------
# AC-B2 — override fires (logic-level simulation)
# AC-B3 — override does NOT fire when top3 == 0
# AC-B4 — override does NOT fire on legitimate non-bail-out text
#
# We simulate the inserted block at main.py:462+ as a pure function so the
# logic can be exercised without ADK. The block is verbatim equivalent.
# ---------------------------------------------------------------------------

def _simulate_override_block(response_text, tool_results):
    """Mirror of the bail-out override block in main.py. Returns (
    new_response_text, override_fired_bool)."""
    rank_for_bailout = (tool_results.get("rank_products") or {})
    top3_for_bailout = (
        rank_for_bailout.get("top_3")
        or rank_for_bailout.get("top3")
        or []
    )
    if _looks_like_bailout(response_text) and len(top3_for_bailout) >= 1:
        return "", True
    return response_text, False


def test_b2_override_fires_when_bailout_with_top3():
    """AC-B2: bail-out string + top3 of 2 -> response_text cleared."""
    bailout = "I wasn't able to find eligible products for your profile."
    tool_results = {
        "rank_products": {
            "top_3": [
                {"product_id": "H1", "product_type": "health"},
                {"product_id": "H2", "product_type": "health"},
            ]
        }
    }
    new_text, fired = _simulate_override_block(bailout, tool_results)
    assert fired is True
    assert new_text == ""


def test_b3_override_skipped_when_no_products():
    """AC-B3: bail-out string but top3 empty -> response_text preserved."""
    bailout = "I wasn't able to find eligible products for your profile."
    tool_results = {"rank_products": {"top_3": []}}
    new_text, fired = _simulate_override_block(bailout, tool_results)
    assert fired is False
    assert new_text == bailout


def test_b3b_override_skipped_when_rank_missing():
    """AC-B3 edge case: rank_products absent entirely."""
    bailout = "I wasn't able to find eligible products for your profile."
    new_text, fired = _simulate_override_block(bailout, {})
    assert fired is False
    assert new_text == bailout


def test_b4_override_skipped_on_legitimate_recommendation_text():
    """AC-B4: warm prose + top3 of 2 -> response unchanged."""
    prose = "Based on your profile, the SecureLife Term Plus offers great cover."
    tool_results = {
        "rank_products": {
            "top_3": [
                {"product_id": "H1"},
                {"product_id": "H2"},
            ]
        }
    }
    new_text, fired = _simulate_override_block(prose, tool_results)
    assert fired is False
    assert new_text == prose


def test_b4b_override_skipped_on_empty_response_text():
    """AC-B4 edge: empty response_text + top3 of 2 -> NOT a bail-out (it's
    a clean LLM-bailed-empty case the C.5b template handles directly)."""
    tool_results = {"rank_products": {"top_3": [{"product_id": "H1"}]}}
    new_text, fired = _simulate_override_block("", tool_results)
    assert fired is False
    assert new_text == ""


# ---------------------------------------------------------------------------
# AC-B6 — C.5b deterministic template renders for n=1, n=2, n=3
#
# Re-builds the template loop verbatim from main.py:525-560 (without the
# logging side effect) so we can assert regex matches on the produced text.
# ---------------------------------------------------------------------------

def _simulate_c5b_template(top, search_candidates):
    id_to_full = {(c.get("product_id") or c.get("id")): c for c in search_candidates}
    lines = []
    for i, item in enumerate(top[:3]):
        inner = item.get("product") if isinstance(item.get("product"), dict) else {}
        flat = {**inner, **{k: v for k, v in item.items() if k != "product"}}
        pid = flat.get("product_id") or flat.get("id")
        full = id_to_full.get(pid, {})
        name = flat.get("name") or full.get("name") or "Product"
        pmin = flat.get("premium_min_monthly") or full.get("premium_min_monthly")
        pmax = flat.get("premium_max_monthly") or full.get("premium_max_monthly")
        kf = flat.get("key_feature") or full.get("key_feature") or ""
        if pmin and pmax:
            premium_str = f"premium {int(pmin):,} to {int(pmax):,} INR per month"
        elif pmin:
            premium_str = f"premium from {int(pmin):,} INR per month"
        else:
            premium_str = ""
        ranking_words = ["First", "Second", "Third"][i] if i < 3 else f"Rank {i+1}"
        line = f"{ranking_words}, {name}"
        if kf:
            line += f" — {kf}"
        if premium_str:
            line += f" ({premium_str})"
        line += "."
        lines.append(line)
    return (
        "Based on your profile, here are my top recommendations. "
        + " ".join(lines)
        + " Would you like more details on any of these?"
    )


def _mk_product(pid, name, premium_min=1500, premium_max=3000, kf="Key benefit"):
    return {
        "product_id": pid,
        "name": name,
        "premium_min_monthly": premium_min,
        "premium_max_monthly": premium_max,
        "key_feature": kf,
    }


def test_b6_template_renders_n1():
    """AC-B6 (n=1): only First, ... line present."""
    top = [_mk_product("H1", "HealthFirst Individual")]
    text = _simulate_c5b_template(top, top)
    assert re.search(r"First, [A-Z][^.]+\.", text), text
    assert "Second" not in text
    assert "Third" not in text


def test_b6_template_renders_n2():
    """AC-B6 (n=2): First + Second present, Third absent."""
    top = [
        _mk_product("H1", "HealthFirst Individual"),
        _mk_product("H2", "MediCare Family Floater", 2200, 4500, "Family floater"),
    ]
    text = _simulate_c5b_template(top, top)
    assert re.search(r"First, [A-Z][^.]+\.", text), text
    assert re.search(r"Second, [A-Z][^.]+\.", text), text
    assert "Third" not in text


def test_b6_template_renders_n3():
    """AC-B6 (n=3): First + Second + Third all present."""
    top = [
        _mk_product("T1", "Future Secure Term Plan"),
        _mk_product("T2", "LifeGuard Plus Term", 800, 5000, "Accidental death"),
        _mk_product("T3", "FamilyProtect 3 Crore", 1200, 8000, "High-cover family"),
    ]
    text = _simulate_c5b_template(top, top)
    assert re.search(r"First, [A-Z][^.]+\.", text), text
    assert re.search(r"Second, [A-Z][^.]+\.", text), text
    assert re.search(r"Third, [A-Z][^.]+\.", text), text


# ---------------------------------------------------------------------------
# AC-C2 — programmatic-path product_type extraction logic
#
# Mirrors the _pt_from_profile block at main.py inside the programmatic
# search call. Verifies first-goal-wins for list, plain string, empty, None.
# Full integration (search_products kwarg routed correctly + log line) is
# covered by the live HTTP gate (SPEC v2 §8.3).
# ---------------------------------------------------------------------------

def _simulate_programmatic_pt_extract(validated_profile):
    """Mirror of the _pt_from_profile block from main.py (programmatic
    fallback). Returns the extracted product_type or None."""
    pt = None
    try:
        goals = validated_profile.get("coverage_goals") or []
        if isinstance(goals, list) and goals:
            pt = goals[0]
        elif isinstance(goals, str) and goals.strip():
            pt = goals.strip()
    except Exception:
        pt = None
    return pt


def test_c2_pt_extract_list_first_wins():
    """AC-C2: list-shape coverage_goals -> first element wins."""
    assert _simulate_programmatic_pt_extract({"coverage_goals": ["health"]}) == "health"
    assert _simulate_programmatic_pt_extract(
        {"coverage_goals": ["term_life", "health"]}
    ) == "term_life"


def test_c2_pt_extract_string_form():
    """AC-C2: bare-string coverage_goals -> stripped value."""
    assert _simulate_programmatic_pt_extract({"coverage_goals": "health"}) == "health"
    assert _simulate_programmatic_pt_extract({"coverage_goals": "  health  "}) == "health"


def test_c2_pt_extract_missing_returns_none():
    """AC-C2: empty / None / missing -> None (no fallback default per L-001)."""
    assert _simulate_programmatic_pt_extract({}) is None
    assert _simulate_programmatic_pt_extract({"coverage_goals": []}) is None
    assert _simulate_programmatic_pt_extract({"coverage_goals": None}) is None
    assert _simulate_programmatic_pt_extract({"coverage_goals": ""}) is None
    assert _simulate_programmatic_pt_extract({"coverage_goals": "   "}) is None


def test_c2_programmatic_kwarg_present_in_source():
    """AC-C2 lockdown: assert main.py actually passes product_type=
    _pt_from_profile in the programmatic search call (not just defines it)."""
    src = _load_main_source()
    # The programmatic call site must include the product_type kwarg.
    # We look for the search_products(...) call inside the
    # `if "search_products" not in _tool_results:` block.
    block_match = re.search(
        r"if \"search_products\" not in _tool_results:.*?_tool_results\[\"search_products\"\] = _search_result",
        src,
        re.DOTALL,
    )
    assert block_match is not None, "Could not locate programmatic search block"
    block = block_match.group(0)
    assert "product_type=_pt_from_profile" in block, (
        f"Programmatic search call missing product_type kwarg. Block:\n{block}"
    )
    assert "T1C_PROGRAMMATIC_PT_INJECT" in block, (
        f"Programmatic search block missing T1C_PROGRAMMATIC_PT_INJECT log line.\n{block}"
    )


# ---------------------------------------------------------------------------
# AC-C3 — gamma filter drops mismatched product_type
#
# Mirrors the gamma filter at main.py inside the for-loop building
# top3_enriched. Verifies same-type passes, mismatched is dropped, missing
# product_type / missing intake_pt is a no-op (preserves existing behaviour).
# ---------------------------------------------------------------------------

def _simulate_gamma_filter(top_3_raw, validated_profile, id_to_product=None):
    """Mirror of the gamma filter loop. Returns (top3_enriched, drops)
    where drops is the list of (expected, got, name) for dropped products."""
    id_to_product = id_to_product or {}
    intake_pt = None
    try:
        goals = (validated_profile or {}).get("coverage_goals") or []
        if isinstance(goals, list) and goals:
            intake_pt = goals[0]
        elif isinstance(goals, str) and goals.strip():
            intake_pt = goals.strip()
    except Exception:
        intake_pt = None

    top3_enriched = []
    drops = []
    for idx, item in enumerate(top_3_raw):
        inner = item.get("product") if isinstance(item.get("product"), dict) else {}
        base = {**inner, **{k: v for k, v in item.items() if k != "product"}}
        pid = base.get("product_id") or base.get("id")
        full = id_to_product.get(pid, {})
        merged = {**full, **base, "rank": idx + 1}
        if (
            intake_pt
            and merged.get("product_type")
            and merged.get("product_type") != intake_pt
        ):
            drops.append(
                (intake_pt, merged.get("product_type"), merged.get("name", "?"))
            )
            continue
        if merged:
            top3_enriched.append(merged)
    return top3_enriched, drops


def test_c3_gamma_drops_mismatched_type():
    """AC-C3: HeartShield critical_illness dropped when intake is health."""
    top_3_raw = [
        {"product_id": "H1", "product_type": "health", "name": "HealthFirst Individual"},
        {"product_id": "H2", "product_type": "health", "name": "MediCare Family Floater"},
        {"product_id": "CRIT002", "product_type": "critical_illness", "name": "HeartShield CI Plan"},
    ]
    profile = {"coverage_goals": ["health"]}
    enriched, drops = _simulate_gamma_filter(top_3_raw, profile)
    assert len(enriched) == 2, f"Expected 2 health products, got {enriched}"
    assert len(drops) == 1
    assert drops[0] == ("health", "critical_illness", "HeartShield CI Plan")
    # Sanity: surviving products are both health
    for p in enriched:
        assert p["product_type"] == "health"


def test_c3_gamma_no_drop_on_clean_term_life():
    """AC-C3 / AC-C5 control: term_life intake + all term_life top3 -> no drops."""
    top_3_raw = [
        {"product_id": "T1", "product_type": "term_life", "name": "Future Secure"},
        {"product_id": "T2", "product_type": "term_life", "name": "LifeGuard Plus"},
        {"product_id": "T3", "product_type": "term_life", "name": "FamilyProtect 3 Crore"},
    ]
    profile = {"coverage_goals": ["term_life"]}
    enriched, drops = _simulate_gamma_filter(top_3_raw, profile)
    assert len(enriched) == 3
    assert drops == []


def test_c3_gamma_no_op_when_intake_pt_missing():
    """AC-C3 edge: missing coverage_goals -> filter is a no-op."""
    top_3_raw = [
        {"product_id": "H1", "product_type": "health", "name": "A"},
        {"product_id": "C1", "product_type": "critical_illness", "name": "B"},
    ]
    enriched, drops = _simulate_gamma_filter(top_3_raw, {})
    assert len(enriched) == 2
    assert drops == []


def test_c3_gamma_no_op_when_product_type_missing():
    """AC-C3 edge: products without product_type field pass through."""
    top_3_raw = [
        {"product_id": "X1", "name": "Mystery Product"},  # no product_type
    ]
    profile = {"coverage_goals": ["health"]}
    enriched, drops = _simulate_gamma_filter(top_3_raw, profile)
    assert len(enriched) == 1
    assert drops == []


# ---------------------------------------------------------------------------
# AC-B7 — FE top3 payload preserved after override (covered by source-level
# assertion since payload assembly happens AFTER response_text resolution).
# AC-C2 (search_products kwarg actually flows + log line emits) — covered by
# live HTTP gate.
# ---------------------------------------------------------------------------

def test_b7_response_payload_assembly_includes_top3_after_template():
    """AC-B7: source-level lock — the response_payload["top3"] = top3_enriched
    assignment lives BELOW (i.e. after) the C.5b template at main.py:525, so
    a bail-out override that triggers the C.5b template still results in
    top3 being attached to the response. We assert this via line ordering."""
    src = _load_main_source()
    template_idx = src.index("DETERMINISTIC_FALLBACK_FIRED")
    payload_idx = src.index('response_payload["top3"] = top3_enriched')
    assert template_idx < payload_idx, (
        "C.5b template fires BEFORE top3 attached to response_payload — "
        "so a bail-out override correctly produces both deterministic prose "
        "and a populated top3 list."
    )


@pytest.mark.skip(reason="Integration: covered by local-uvicorn gate (SPEC §8.2.5) and live HTTP gate (SPEC §8.3) — requires ADK Runner + GCP creds")
def test_b5_live_http_arc():
    """AC-B5: live HTTP arc — covered by SPEC §8.3 Arc 1."""
    pass


@pytest.mark.skip(reason="Integration: covered by live HTTP gate (SPEC §8.3 Arc 2) — requires deployed revision")
def test_c4_multi_turn_pushback_arc():
    """AC-C4: live multi-turn arc — covered by SPEC §8.3 Arc 2."""
    pass


@pytest.mark.skip(reason="Integration: covered by live HTTP gate (SPEC §8.3 Arc 3) — requires deployed revision")
def test_c5_term_life_regression_control():
    """AC-C5: term_life regression — covered by SPEC §8.3 Arc 3."""
    pass


@pytest.mark.skip(reason="Cross-cutting: covered by `pytest tests/test_multi_turn.py tests/test_orchestration_guardrail.py` in Reviewer phase")
def test_x1_no_regression_day5_day6_paths():
    """AC-X1: no regression on Day 5/6 happy paths — Reviewer-run."""
    pass

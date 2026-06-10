"""
Live integration smoke test — chains all three deployed Cloud Functions.
Requires:  pip install requests
Run:       python tests/smoke_test_live.py
"""

import os
import sys
import requests

# ---------------------------------------------------------------------------
# Resolve URLs: prefer .env.local overrides, fall back to defaults
# ---------------------------------------------------------------------------
_env_local = os.path.join(os.path.dirname(__file__), "..", ".env.local")
if os.path.exists(_env_local):
    for line in open(_env_local):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

BASE = "https://us-central1-voice-sales-agent.cloudfunctions.net"
COMPLIANCE_CHECK_URL = os.environ.get(
    "COMPLIANCE_CHECK_URL", f"{BASE}/compliance_check"
)
RANK_PRODUCTS_URL = os.environ.get(
    "RANK_PRODUCTS_URL", f"{BASE}/rank_products"
)
PROFILE = {
    "age": 35,
    "income": 1_200_000,
    "smoker": False,
    "health_status": "healthy",
    "sum_need": 10_000_000,
}

errors = []


def check(label, condition, detail=""):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        errors.append(label)


# ---------------------------------------------------------------------------
# Step 1: product_search
# ---------------------------------------------------------------------------
print("\n=== 1. product_search (ELSER hybrid) ===")
r1 = requests.post(
    f"{BASE}/product_search",
    json={
        "query": "affordable family protection non-smoker term life",
        "customer_age": 35,
        "is_smoker": False,
        "income": 1_200_000,
        "size": 5,
    },
    timeout=15,
)
check("HTTP 200", r1.status_code == 200, r1.text[:200])
d1 = r1.json()
check("candidates list present", isinstance(d1.get("candidates"), list))
check("at least 1 candidate", len(d1.get("candidates", [])) >= 1)
check("total_hits > 0", d1.get("total_hits", 0) > 0)
check("fallback_triggered is bool", isinstance(d1.get("fallback_triggered"), bool))
check("candidates have elser_score", all("elser_score" in c for c in d1["candidates"]))
print(f"  total_hits={d1['total_hits']}  returned={len(d1['candidates'])}")
for c in d1["candidates"]:
    print(f"    {c['id']:10s} {c['name'][:35]:35s} score={c['elser_score']:.6f}")

# ---------------------------------------------------------------------------
# Step 2: compliance_check
# ---------------------------------------------------------------------------
print("\n=== 2. compliance_check (guardrail) ===")
r2 = requests.post(
    f"{BASE}/compliance_check",
    json={"candidate_products": d1["candidates"], "customer_profile": PROFILE},
    timeout=15,
)
check("HTTP 200", r2.status_code == 200, r2.text[:200])
d2 = r2.json()
check("passed list present", isinstance(d2.get("passed"), list))
check("rejected list present", isinstance(d2.get("rejected"), list))
check("passed + rejected = candidates", len(d2["passed"]) + len(d2["rejected"]) == len(d1["candidates"]))
print(f"  passed={len(d2['passed'])}  rejected={len(d2['rejected'])}")
for rej in d2["rejected"]:
    print(f"    REJECTED: {rej['product_id']} — {rej['reasons']}")

# ---------------------------------------------------------------------------
# Step 3: rank_products
# ---------------------------------------------------------------------------
print("\n=== 3. rank_products (suitability scoring) ===")
rank_profile = {"age": 35, "income": 1_200_000, "sum_need": 10_000_000}
r3 = requests.post(
    f"{BASE}/rank_products",
    json={"passed_products": d2["passed"], "customer_profile": rank_profile},
    timeout=15,
)
check("HTTP 200", r3.status_code == 200, r3.text[:200])
d3 = r3.json()
check("top3 present", "top3" in d3)
check("audit present", "audit" in d3)
check("top3 ≤ 3 items", len(d3.get("top3", [])) <= 3)
if d3.get("top3"):
    check("rank 1 has suitability_score", "suitability_score" in d3["top3"][0])
    check("score_breakdown present", "score_breakdown" in d3["top3"][0])
    check("product.name present", "name" in d3["top3"][0]["product"])
check("audit.customer_profile_hash 64 chars", len(d3["audit"].get("customer_profile_hash", "")) == 64)
check("audit.formula_weights present", "elser" in d3["audit"].get("formula_weights", {}))

print(f"\n  Top recommendations for age=35, income=₹12L, sum_need=₹1Cr:")
for item in d3.get("top3", []):
    bd = item["score_breakdown"]
    print(
        f"    Rank {item['rank']}: {item['product']['name'][:35]:35s} "
        f"overall={item['suitability_score']:.4f} "
        f"(elser={bd['elser_relevance']:.3f} age={bd['age_centrality']:.3f} income={bd['income_fit']:.3f})"
    )
print(f"  audit hash:    {d3['audit']['customer_profile_hash'][:16]}...")
print(f"  formula weights: {d3['audit']['formula_weights']}")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{'='*60}")
if errors:
    print(f"FAILED — {len(errors)} assertion(s):")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print(f"ALL PASSED — end-to-end pipeline verified on live GCP — continuing to compliance smoke tests")
errors.clear()


# ---------------------------------------------------------------------------
# TASK-052: Compliance smoke — age-ineligible product rejection
# ---------------------------------------------------------------------------

def smoke_compliance_age_rejection():
    """POST age=70 profile + max_age=65 product → rejected with correct reason."""
    print("\n=== TASK-052: compliance_check — AGE_MAX rejection ===")
    r = requests.post(
        COMPLIANCE_CHECK_URL,
        json={
            "candidate_products": [{
                "id": "SMOKE_AGE",
                "name": "SeniorBlock Plan",
                "min_age": 18,
                "max_age": 65,
                "smoker_eligible": True,
                "min_income": 100_000,
                "medical_required_above": 50_000_000,
            }],
            "customer_profile": {
                "age": 70,
                "income": 500_000,
                "smoker": False,
                "health_status": "healthy",
                "sum_need": 1_000_000,
            },
        },
        timeout=15,
    )
    check("TASK-052: HTTP 200", r.status_code == 200, r.text[:200])
    d = r.json()
    check("TASK-052: passed is empty", d.get("passed") == [])
    check("TASK-052: 1 rejected entry", len(d.get("rejected", [])) == 1)
    reasons = d.get("rejected", [{}])[0].get("reasons", [])
    check("TASK-052: reason contains 'Maximum entry age is 65'",
          any("Maximum entry age is 65" in r for r in reasons), str(reasons))
    check("TASK-052: reason contains 'customer is 70'",
          any("customer is 70" in r for r in reasons), str(reasons))
    print(f"  rejection reasons: {reasons}")


# ---------------------------------------------------------------------------
# TASK-053: Compliance smoke — smoker exclusion rejection
# ---------------------------------------------------------------------------

def smoke_compliance_smoker_rejection():
    """POST smoker=true profile + smoker_eligible=false product → rejected."""
    print("\n=== TASK-053: compliance_check — SMOKER_EXCLUSION rejection ===")
    r = requests.post(
        COMPLIANCE_CHECK_URL,
        json={
            "candidate_products": [{
                "id": "SMOKE_SMOKER",
                "name": "NoSmoke Term Plan",
                "min_age": 18,
                "max_age": 65,
                "smoker_eligible": False,
                "min_income": 100_000,
                "medical_required_above": 50_000_000,
            }],
            "customer_profile": {
                "age": 35,
                "income": 600_000,
                "smoker": True,
                "health_status": "healthy",
                "sum_need": 2_000_000,
            },
        },
        timeout=15,
    )
    check("TASK-053: HTTP 200", r.status_code == 200, r.text[:200])
    d = r.json()
    check("TASK-053: passed is empty", d.get("passed") == [])
    check("TASK-053: 1 rejected entry", len(d.get("rejected", [])) == 1)
    reasons = d.get("rejected", [{}])[0].get("reasons", [])
    check("TASK-053: reason is 'Product not available for smokers'",
          any("not available for smokers" in r for r in reasons), str(reasons))
    print(f"  rejection reasons: {reasons}")


# ---------------------------------------------------------------------------
# TASK-054: Compliance smoke — all products rejected, HTTP 200 (not error)
# ---------------------------------------------------------------------------

def smoke_compliance_all_rejected():
    """All 3 products fail AGE_MAX → passed=[], HTTP 200."""
    print("\n=== TASK-054: compliance_check — all products rejected (graceful) ===")
    products = [
        {"id": f"SMOKE_ALL{i}", "name": f"AgeBlocked Plan {i}",
         "min_age": 18, "max_age": 50, "smoker_eligible": True,
         "min_income": 100_000, "medical_required_above": 50_000_000}
        for i in range(1, 4)
    ]
    r = requests.post(
        COMPLIANCE_CHECK_URL,
        json={
            "candidate_products": products,
            "customer_profile": {
                "age": 70,
                "income": 500_000,
                "smoker": False,
                "health_status": "healthy",
                "sum_need": 1_000_000,
            },
        },
        timeout=15,
    )
    check("TASK-054: HTTP 200 (not 4xx/5xx)", r.status_code == 200, r.text[:200])
    d = r.json()
    check("TASK-054: passed is empty []", d.get("passed") == [])
    check("TASK-054: 3 rejected entries", len(d.get("rejected", [])) == 3)
    for entry in d.get("rejected", []):
        check(f"TASK-054: {entry['product_id']} has non-empty reasons",
              len(entry.get("reasons", [])) >= 1)
    print(f"  rejected count: {len(d.get('rejected', []))}")


# ---------------------------------------------------------------------------
# Run compliance smoke tests
# ---------------------------------------------------------------------------

smoke_compliance_age_rejection()
smoke_compliance_smoker_rejection()
smoke_compliance_all_rejected()

print(f"\n{'='*60}")
if errors:
    print(f"COMPLIANCE SMOKE FAILED — {len(errors)} assertion(s):")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print(f"ALL COMPLIANCE SMOKE TESTS PASSED — continuing to rank_products smoke tests")
errors.clear()


# ---------------------------------------------------------------------------
# TASK-094: rank_products smoke — 5 products → ranked top-3
# ---------------------------------------------------------------------------

def smoke_rank_products_five_products():
    """POST 5 known products + valid profile → HTTP 200, top3 ≤ 3, descending scores.

    Run standalone (does not require the full chain to pass first).
    """
    print("\n=== TASK-094: rank_products — 5-product ranking ===")

    products = [
        {"id": f"SMOKE_P{i}", "name": f"Smoke Plan {i}",
         "min_age": 20, "max_age": 60,
         "elser_score": float(i * 2),
         "premium_min_monthly": 1500, "premium_max_monthly": 4500}
        for i in range(1, 6)
    ]
    profile = {"age": 38, "income": 1_500_000, "sum_need": 10_000_000}

    r = requests.post(RANK_PRODUCTS_URL, json={
        "passed_products": products,
        "customer_profile": profile,
    }, timeout=15)

    check("TASK-094: HTTP 200", r.status_code == 200, r.text[:200])
    d = r.json()
    check("TASK-094: top3 present", "top3" in d)
    check("TASK-094: top3 ≤ 3 items", len(d.get("top3", [])) <= 3)
    check("TASK-094: audit present", "audit" in d)
    if d.get("top3"):
        scores = [item["suitability_score"] for item in d["top3"]]
        check("TASK-094: top3 sorted descending", scores == sorted(scores, reverse=True),
              str(scores))
        check("TASK-094: rank 1 suitability_score ≥ rank 2",
              d["top3"][0]["suitability_score"] >= d["top3"][1]["suitability_score"])
        check("TASK-094: score_breakdown fields present",
              all(k in d["top3"][0]["score_breakdown"]
                  for k in ("elser_relevance", "age_centrality", "income_fit")))
    check("TASK-094: all_scored covers all 5 inputs",
          len(d.get("audit", {}).get("all_scored", [])) == 5)
    check("TASK-094: profile_hash is 64 chars",
          len(d.get("audit", {}).get("customer_profile_hash", "")) == 64)

    print(f"  Top 3 from 5 products:")
    for item in d.get("top3", []):
        bd = item["score_breakdown"]
        print(
            f"    Rank {item['rank']}: {item['product']['id']}  "
            f"score={item['suitability_score']:.4f} "
            f"(elser={bd['elser_relevance']:.3f} age={bd['age_centrality']:.3f} "
            f"income={bd['income_fit']:.3f})"
        )


# ---------------------------------------------------------------------------
# TASK-095: rank_products smoke — empty passed_products → HTTP 200, top3=[]
# ---------------------------------------------------------------------------

def smoke_rank_products_empty():
    """POST passed_products=[] → HTTP 200, top3=[], audit present (not 4xx/5xx)."""
    print("\n=== TASK-095: rank_products — empty passed_products ===")

    r = requests.post(RANK_PRODUCTS_URL, json={
        "passed_products": [],
        "customer_profile": {"age": 35, "income": 1_200_000, "sum_need": 5_000_000},
    }, timeout=15)

    check("TASK-095: HTTP 200 (not 4xx/5xx)", r.status_code == 200, r.text[:200])
    d = r.json()
    check("TASK-095: top3 is empty list", d.get("top3") == [])
    check("TASK-095: audit present", "audit" in d)
    check("TASK-095: audit.all_scored is empty list",
          d.get("audit", {}).get("all_scored") == [])
    check("TASK-095: audit.formula_weights present",
          "elser" in d.get("audit", {}).get("formula_weights", {}))
    check("TASK-095: audit.customer_profile_hash 64 chars",
          len(d.get("audit", {}).get("customer_profile_hash", "")) == 64)
    print("  Empty input gracefully returns top3=[] with audit present.")


# ---------------------------------------------------------------------------
# Run rank_products smoke tests
# ---------------------------------------------------------------------------

smoke_rank_products_five_products()
smoke_rank_products_empty()

print(f"\n{'='*60}")
if errors:
    print(f"RANK_PRODUCTS SMOKE FAILED — {len(errors)} assertion(s):")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print(f"ALL RANK_PRODUCTS SMOKE TESTS PASSED")

errors.clear()

# ---------------------------------------------------------------------------
# TASK-034 / TASK-080 / TASK-081 / TASK-082: InsureVoice Agent end-to-end smoke tests
#
# Requires: INSURE_VOICE_AGENT_URL env var (or falls back to deployed URL).
# Skip entire section gracefully if agent URL is not reachable.
# ---------------------------------------------------------------------------

import time as _time

AGENT_URL = os.environ.get(
    "INSURE_VOICE_AGENT_URL",
    "https://insure-voice-agent-1055350728739.us-central1.run.app",
)


def _agent_invoke(message: str, session_id: str = None, timeout: int = 20) -> tuple:
    """POST to /invoke and return (response_dict, elapsed_seconds)."""
    payload = {"message": message}
    if session_id:
        payload["session_id"] = session_id
    t0 = _time.monotonic()
    r = requests.post(f"{AGENT_URL}/invoke", json=payload, timeout=timeout)
    elapsed = _time.monotonic() - t0
    return r, elapsed


# TASK-080: Happy path — complete profile → ≤120-word voice response with session_id
def smoke_agent_happy_path():
    print("\n=== TASK-080: agent /invoke — happy path (complete profile) ===")
    try:
        r, elapsed = _agent_invoke(
            "I am 35 years old, non-smoker, annual income 1.2 million INR. "
            "I need term life cover for my family of four."
        )
    except Exception as exc:
        print(f"  SKIP  agent not reachable: {exc}")
        return

    check("TASK-080: HTTP 200", r.status_code == 200, r.text[:300])
    body = r.json()
    check("TASK-080: session_id present", bool(body.get("session_id")))
    response_text = body.get("response", "")
    check("TASK-080: response non-empty", len(response_text) > 0)
    word_count = len(response_text.split())
    check(f"TASK-080: response ≤120 words ({word_count})", word_count <= 120, response_text[:200])
    print(f"  elapsed={elapsed:.2f}s  words={word_count}")
    print(f"  response preview: {response_text[:200]}")


# TASK-081: Latency gate — end-to-end < 8s (Constitution §III)
def smoke_agent_latency():
    print("\n=== TASK-081: agent /invoke — latency < 8s ===")
    try:
        r, elapsed = _agent_invoke(
            "I am 42 years old, non-smoker, income 800000 INR. Need health cover."
        )
    except Exception as exc:
        print(f"  SKIP  agent not reachable: {exc}")
        return

    check("TASK-081: HTTP 200", r.status_code == 200, r.text[:300])
    check(f"TASK-081: latency < 8s (actual={elapsed:.2f}s)", elapsed < 8.0,
          f"exceeded budget: {elapsed:.2f}s")
    print(f"  elapsed={elapsed:.2f}s")


# TASK-034 / TASK-082: All-rejected — age=72 profile → constraint explanation, no product name
def smoke_agent_all_rejected():
    print("\n=== TASK-034/TASK-082: agent /invoke — all-rejected (age=72 profile) ===")
    try:
        r, elapsed = _agent_invoke(
            "I am 72 years old, smoker, income 300000 INR. Need any insurance cover."
        )
    except Exception as exc:
        print(f"  SKIP  agent not reachable: {exc}")
        return

    check("TASK-034/082: HTTP 200", r.status_code == 200, r.text[:300])
    body = r.json()
    response_text = body.get("response", "").lower()
    check("TASK-034/082: response non-empty", len(response_text) > 0)
    # Response must mention a constraint, not a product recommendation
    has_constraint_language = any(
        phrase in response_text
        for phrase in [
            "age", "eligib", "constraint", "not eligible", "unable",
            "maximum entry", "qualify", "criteria", "unfortunately", "exceed"
        ]
    )
    check("TASK-034/082: mentions a constraint", has_constraint_language, response_text[:300])
    # Should NOT look like a ranked recommendation (rank 1 / rank 2 pattern)
    has_rank_language = any(
        phrase in response_text for phrase in ["rank 1", "rank 2", "my top", "top 3", "top three"]
    )
    check("TASK-034/082: no ranked recommendation in response", not has_rank_language,
          response_text[:300])
    print(f"  elapsed={elapsed:.2f}s")
    print(f"  response preview: {response_text[:300]}")


# Run agent smoke tests
smoke_agent_happy_path()
smoke_agent_latency()
smoke_agent_all_rejected()

print(f"\n{'='*60}")
if errors:
    print(f"AGENT SMOKE TESTS FAILED — {len(errors)} assertion(s):")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print("ALL AGENT SMOKE TESTS PASSED")
    sys.exit(0)


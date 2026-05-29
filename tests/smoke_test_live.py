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
    print(f"ALL PASSED — end-to-end pipeline verified on live GCP")
    sys.exit(0)


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
    print(f"ALL COMPLIANCE SMOKE TESTS PASSED")
    sys.exit(0)

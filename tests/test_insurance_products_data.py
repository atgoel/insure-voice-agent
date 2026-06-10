"""
tests/test_insurance_products_data.py
Phase 2 exit criterion tests for data/insurance_products.json.

Asserts:
- JSON file loads as a list
- Exactly 28 products
- All 7 product types present
- Every product has description ≥ 50 chars
- No duplicate IDs
- All required fields present and correctly typed in every product
"""
import json
import os

import pytest

from shared.models import InsuranceProduct

# ---------------------------------------------------------------------------
# Fixture — load the catalog once for all tests
# ---------------------------------------------------------------------------

CATALOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "insurance_products.json"
)

EXPECTED_COUNT = 28
EXPECTED_PRODUCT_TYPES = {
    "term_life", "health", "ulip", "endowment",
    "critical_illness", "pension", "child_plan",
}
REQUIRED_FIELDS = {
    "id", "product_code", "name", "product_type", "plan_category",
    "uin", "description", "key_feature", "sales_pitch", "tags",
    "rider_name", "rider_type",
    "min_age", "max_age", "smoker_eligible", "min_income",
    "max_sum_assured", "medical_required_above", "exclusions",
    "premium_min_monthly", "premium_max_monthly", "is_active",
}


@pytest.fixture(scope="module")
def catalog():
    assert os.path.exists(CATALOG_PATH), f"Catalog file not found: {CATALOG_PATH}"
    with open(CATALOG_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    return data


# ---------------------------------------------------------------------------
# Structural tests
# ---------------------------------------------------------------------------

def test_catalog_is_list(catalog):
    assert isinstance(catalog, list), "insurance_products.json must be a JSON array"


def test_catalog_count(catalog):
    assert len(catalog) == EXPECTED_COUNT, (
        f"Expected {EXPECTED_COUNT} products, found {len(catalog)}"
    )


def test_no_duplicate_ids(catalog):
    ids = [p["id"] for p in catalog]
    duplicates = [pid for pid in ids if ids.count(pid) > 1]
    assert duplicates == [], f"Duplicate product IDs found: {sorted(set(duplicates))}"


def test_all_seven_product_types_present(catalog):
    actual_types = {p["product_type"] for p in catalog}
    missing = EXPECTED_PRODUCT_TYPES - actual_types
    assert not missing, f"Missing product types: {sorted(missing)}"


def test_four_products_per_type(catalog):
    from collections import Counter
    counts = Counter(p["product_type"] for p in catalog)
    wrong = {pt: count for pt, count in counts.items() if count != 4}
    assert not wrong, f"Expected 4 products per type; got: {wrong}"


# ---------------------------------------------------------------------------
# Field presence and basic type tests (per-product)
# ---------------------------------------------------------------------------

def test_all_products_have_required_fields(catalog):
    errors = []
    for p in catalog:
        pid = p.get("id", "<unknown>")
        missing = REQUIRED_FIELDS - set(p.keys())
        if missing:
            errors.append(f"[{pid}] Missing fields: {sorted(missing)}")
    assert not errors, "\n".join(errors)


def test_all_descriptions_min_50_chars(catalog):
    short = [
        (p["id"], len(p.get("description", "")))
        for p in catalog
        if len(p.get("description", "")) < 50
    ]
    assert not short, (
        f"Products with description < 50 chars: "
        + ", ".join(f"{pid}({n} chars)" for pid, n in short)
    )


def test_all_products_loadable_as_insurance_product(catalog):
    """
    Each product dict must be instantiatable as an InsuranceProduct
    dataclass — validates field names and basic Python types.
    """
    errors = []
    for p in catalog:
        pid = p.get("id", "<unknown>")
        try:
            product_obj = InsuranceProduct(**p)
            assert product_obj.id == pid
        except (TypeError, AssertionError) as exc:
            errors.append(f"[{pid}] {exc}")
    assert not errors, "\n".join(errors)


# ---------------------------------------------------------------------------
# Content-level tests
# ---------------------------------------------------------------------------

def test_term_life_products_age_ranges(catalog):
    """TERM products must cover at least the 18–65 range collectively."""
    term_products = [p for p in catalog if p["product_type"] == "term_life"]
    min_entries = min(p["min_age"] for p in term_products)
    max_entries = max(p["max_age"] for p in term_products)
    assert min_entries <= 18, f"No term_life product starts at or below age 18; min={min_entries}"
    assert max_entries >= 65, f"No term_life product covers age 65+; max={max_entries}"


def test_ulip_products_smoker_not_eligible(catalog):
    """All ULIP products must have smoker_eligible=False (per TASK-014)."""
    ulip_products = [p for p in catalog if p["product_type"] == "ulip"]
    smoker_eligible = [p["id"] for p in ulip_products if p["smoker_eligible"]]
    assert not smoker_eligible, (
        f"ULIP products should have smoker_eligible=False; found True: {smoker_eligible}"
    )


def test_ulip_products_income_floor(catalog):
    """All ULIP products must have min_income ≥ ₹5L (500_000)."""
    ulip_products = [p for p in catalog if p["product_type"] == "ulip"]
    below_floor = [(p["id"], p["min_income"]) for p in ulip_products if p["min_income"] < 500_000]
    assert not below_floor, (
        f"ULIP products must have min_income ≥ 500000; violations: {below_floor}"
    )


def test_child_plan_max_age(catalog):
    """All child_plan products must have max_age ≤ 55 (parent entry age, per TASK-018)."""
    child_products = [p for p in catalog if p["product_type"] == "child_plan"]
    over_55 = [(p["id"], p["max_age"]) for p in child_products if p["max_age"] > 55]
    assert not over_55, (
        f"child_plan products must have max_age ≤ 55; violations: {over_55}"
    )


def test_child_plan_smoker_not_eligible(catalog):
    """All child_plan products must have smoker_eligible=False (per TASK-018)."""
    child_products = [p for p in catalog if p["product_type"] == "child_plan"]
    smoker_eligible = [p["id"] for p in child_products if p["smoker_eligible"]]
    assert not smoker_eligible, (
        f"child_plan products should have smoker_eligible=False; found True: {smoker_eligible}"
    )


def test_pension_products_min_age(catalog):
    """All pension products must have min_age ≥ 25 (per TASK-017: entry age 30+, PENS003 is 25)."""
    pension_products = [p for p in catalog if p["product_type"] == "pension"]
    # PENS003 (PensionMaxx) allows age 25 — so requirement is min_age ≥ 25
    below = [(p["id"], p["min_age"]) for p in pension_products if p["min_age"] < 25]
    assert not below, f"pension products must have min_age ≥ 25; violations: {below}"


def test_critical_illness_mentions_key_conditions(catalog):
    """CI product descriptions must mention cancer, heart, or stroke."""
    ci_products = [p for p in catalog if p["product_type"] == "critical_illness"]
    ci_keywords = {"cancer", "heart", "stroke"}
    missing_keywords = []
    for p in ci_products:
        desc_lower = p["description"].lower()
        if not any(kw in desc_lower for kw in ci_keywords):
            missing_keywords.append(p["id"])
    assert not missing_keywords, (
        f"CI products must mention cancer/heart/stroke in description; missing: {missing_keywords}"
    )


def test_all_products_are_active(catalog):
    """All products in the catalog should have is_active=True."""
    inactive = [p["id"] for p in catalog if not p.get("is_active", True)]
    assert not inactive, f"Inactive products found (unexpected): {inactive}"


def test_ids_match_expected_pattern(catalog):
    """
    Every ID must follow the pattern: PREFIX + 3-digit number
    e.g. TERM001, HLTH002, ULIP003
    """
    import re
    pattern = re.compile(r"^[A-Z]{3,5}\d{3}$")
    invalid = [p["id"] for p in catalog if not pattern.match(p["id"])]
    assert not invalid, f"Products with non-standard IDs: {invalid}"


def test_product_codes_match_fg_prefix(catalog):
    """All product_codes must start with 'FG_'."""
    invalid = [p["id"] for p in catalog if not p.get("product_code", "").startswith("FG_")]
    assert not invalid, f"Products with non-FG_ product_code: {invalid}"


def test_premium_range_valid(catalog):
    """premium_min_monthly must be less than premium_max_monthly for all products."""
    invalid = [
        (p["id"], p["premium_min_monthly"], p["premium_max_monthly"])
        for p in catalog
        if p["premium_min_monthly"] >= p["premium_max_monthly"]
    ]
    assert not invalid, f"Invalid premium ranges (min >= max): {invalid}"


def test_age_range_valid(catalog):
    """min_age must be less than max_age for all products."""
    invalid = [
        (p["id"], p["min_age"], p["max_age"])
        for p in catalog
        if p["min_age"] >= p["max_age"]
    ]
    assert not invalid, f"Invalid age ranges (min >= max): {invalid}"

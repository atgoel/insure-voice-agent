"""
simulate_premium — Cloud Function (2nd gen)
===========================================
Deterministic premium simulation engine for the InsureVoice Premium Simulation
feature (Story 6, spec 005-multi-agent-orchestration).

Rules (Constitution §II — Zero hallucination on deterministic logic):
  • ALL numbers returned are computed from product catalog fields only.
  • NO LLM is involved anywhere in this function.
  • Invalid inputs return HTTP 400 with a structured validation_errors list.
  • Returns (maturity projection) are only computed for savings products:
    product_type ∈ {endowment, ulip, pension, child_plan}.

Endpoint: POST /simulate_premium
Request body (JSON):
  product_id         : str   — catalog product ID (e.g. "TERM001")
  sum_assured        : int   — desired sum assured in INR (≥ product min_income * 5)
  customer_age       : int   — customer's age in years
  is_smoker          : bool  — True if customer is a smoker
  premium_frequency  : str   — "monthly" | "quarterly" | "semi_annual" | "annual"
  policy_term        : int   — policy term in years (must be in product's available_terms)

Response body (JSON) — HTTP 200:
  product_id             : str
  product_name           : str
  product_type           : str
  period_premium         : float   — premium for each selected payment period (INR)
  annual_premium         : float   — annualised premium after all loadings/discounts (INR)
  total_premium_outflow  : float   — total premiums paid over full policy term (INR)
  projected_maturity_value: float | None  — None for protection-only products
  net_gain               : float | None   — projected_maturity - total_outflow; None for protection
  simulation_inputs      : dict    — echo of validated inputs for display
  formula_breakdown      : dict    — step-by-step loading details for transparency

Response body (JSON) — HTTP 400:
  validation_errors : list[str]
"""

import json
import math
import os
from pathlib import Path

import functions_framework

# ---------------------------------------------------------------------------
# Product catalog loader
# ---------------------------------------------------------------------------

_CATALOG: dict = {}   # product_id → product dict; lazy-loaded on first request

_SAVINGS_TYPES = {"endowment", "ulip", "pension", "child_plan"}
_VALID_FREQUENCIES = {"monthly", "quarterly", "semi_annual", "annual"}
_PERIODS_PER_YEAR = {"monthly": 12, "quarterly": 4, "semi_annual": 2, "annual": 1}


def _load_catalog() -> dict:
    """Load insurance_products.json once and cache it in _CATALOG."""
    global _CATALOG
    if _CATALOG:
        return _CATALOG

    # Try several candidate paths so the function works both in Cloud Run and locally.
    candidates = [
        Path(__file__).parent.parent.parent / "data" / "insurance_products.json",
        Path(os.environ.get("PRODUCTS_JSON_PATH", "")) if os.environ.get("PRODUCTS_JSON_PATH") else None,
        Path("/workspace/data/insurance_products.json"),
    ]
    for path in candidates:
        if path and path.exists():
            with open(path, encoding="utf-8") as fh:
                products = json.load(fh)
            _CATALOG = {p["id"]: p for p in products}
            return _CATALOG

    raise FileNotFoundError(
        "insurance_products.json not found. Set PRODUCTS_JSON_PATH env var or place it "
        "relative to the function at ../../data/insurance_products.json"
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate(body: dict, catalog: dict) -> list[str]:
    errors: list[str] = []

    product_id = body.get("product_id")
    if not product_id:
        errors.append("product_id is required")
        return errors  # nothing else can be validated without a product

    product = catalog.get(product_id)
    if not product:
        errors.append(f"product_id '{product_id}' not found in catalog")
        return errors

    # sum_assured
    sa = body.get("sum_assured")
    if sa is None:
        errors.append("sum_assured is required")
    elif not isinstance(sa, (int, float)) or sa <= 0:
        errors.append("sum_assured must be a positive number")
    elif sa > product.get("max_sum_assured", float("inf")):
        errors.append(
            f"sum_assured ₹{sa:,.0f} exceeds product maximum ₹{product['max_sum_assured']:,.0f}"
        )
    elif sa < 100_000:
        errors.append("sum_assured must be at least ₹1,00,000 (1 lakh)")

    # customer_age
    age = body.get("customer_age")
    if age is None:
        errors.append("customer_age is required")
    elif not isinstance(age, int) or age < 0:
        errors.append("customer_age must be a non-negative integer")
    else:
        if age < product.get("min_age", 18):
            errors.append(
                f"customer_age {age} is below product minimum age {product['min_age']}"
            )
        if age > product.get("max_age", 99):
            errors.append(
                f"customer_age {age} exceeds product maximum age {product['max_age']}"
            )

    # is_smoker
    is_smoker = body.get("is_smoker")
    if is_smoker is None:
        errors.append("is_smoker is required (true or false)")
    elif not isinstance(is_smoker, bool):
        errors.append("is_smoker must be a boolean (true or false)")
    elif is_smoker and not product.get("smoker_eligible", True):
        errors.append(f"Product '{product_id}' is not available to smokers")

    # premium_frequency
    freq = body.get("premium_frequency")
    if not freq:
        errors.append("premium_frequency is required")
    elif freq not in _VALID_FREQUENCIES:
        errors.append(
            f"premium_frequency must be one of: {', '.join(sorted(_VALID_FREQUENCIES))}"
        )

    # policy_term
    term = body.get("policy_term")
    available_terms = product.get("available_terms", [])
    if term is None:
        errors.append("policy_term is required")
    elif not isinstance(term, int) or term <= 0:
        errors.append("policy_term must be a positive integer (years)")
    elif available_terms and term not in available_terms:
        errors.append(
            f"policy_term {term} is not available for this product. "
            f"Valid terms: {available_terms}"
        )

    return errors


# ---------------------------------------------------------------------------
# Simulation formula
# ---------------------------------------------------------------------------

def _find_age_loading(age: int, age_bands: list) -> float:
    """Return the loading % for the customer's age from the product's age_bands."""
    for band in age_bands:
        if band["min_age"] <= age <= band["max_age"]:
            return float(band["loading_pct"])
    # If age is above all bands, use the last band's loading
    if age_bands:
        return float(age_bands[-1]["loading_pct"])
    return 0.0


def _simulate(product: dict, sa: float, age: int, is_smoker: bool,
              frequency: str, term: int) -> dict:
    """
    Run the deterministic premium simulation.

    Formula:
      1. base_annual  = (sa / 100_000) * base_rate_per_lakh
      2. age_adjusted = base_annual * (1 + age_loading_pct / 100)
      3. smoker_adj   = age_adjusted * (1 + smoker_loading_pct / 100)   [if smoker]
      4. annual_disc  = smoker_adj * frequency_multiplier[frequency]     [discount for freq]
      5. period_prem  = annual_disc / periods_per_year[frequency]
      6. total_outflow= period_prem * periods_per_year[frequency] * term

    For savings products:
      7. maturity = total_outflow * (1 + return_rate/100) ** term
         net_gain = maturity - total_outflow
    """
    base_rate = float(product.get("base_rate_per_lakh", 500))
    age_bands = product.get("age_bands", [])
    smoker_loading_pct = float(product.get("smoker_loading_pct", 0))
    freq_multipliers = product.get("frequency_multipliers",
                                   {"monthly": 1.0, "quarterly": 0.99,
                                    "semi_annual": 0.975, "annual": 0.95})
    return_rate = product.get("return_rate")  # None for protection products
    product_type = product.get("product_type", "")

    # Step 1 — base annual premium
    base_annual = (sa / 100_000) * base_rate

    # Step 2 — age loading
    age_loading_pct = _find_age_loading(age, age_bands)
    age_adjusted = base_annual * (1 + age_loading_pct / 100)

    # Step 3 — smoker loading
    if is_smoker and smoker_loading_pct > 0:
        smoker_adjusted = age_adjusted * (1 + smoker_loading_pct / 100)
    else:
        smoker_adjusted = age_adjusted

    # Step 4 — frequency discount (multiplier < 1 = cheaper for less-frequent payment)
    freq_mult = float(freq_multipliers.get(frequency, 1.0))
    annual_discounted = smoker_adjusted * freq_mult

    # Step 5 — per-period premium
    periods = _PERIODS_PER_YEAR[frequency]
    period_premium = annual_discounted / periods

    # Step 6 — total outflow over full term
    total_outflow = period_premium * periods * term

    # Step 7 — maturity projection for savings products
    projected_maturity = None
    net_gain = None
    if return_rate is not None and product_type in _SAVINGS_TYPES:
        r = float(return_rate) / 100
        if r > 0:
            # Standard compound accumulation of periodic payments (future value of annuity)
            # FV = PMT * ((1+r_period)^n - 1) / r_period  where r_period = r/periods, n = periods*term
            r_period = r / periods
            n = periods * term
            if r_period > 0:
                projected_maturity = period_premium * ((math.pow(1 + r_period, n) - 1) / r_period)
            else:
                projected_maturity = total_outflow
        else:
            projected_maturity = total_outflow
        # Also add death benefit component — the sum assured is the minimum maturity value
        projected_maturity = max(projected_maturity, sa)
        net_gain = projected_maturity - total_outflow

    return {
        "period_premium": round(period_premium, 2),
        "annual_premium": round(annual_discounted, 2),
        "total_premium_outflow": round(total_outflow, 2),
        "projected_maturity_value": round(projected_maturity, 2) if projected_maturity is not None else None,
        "net_gain": round(net_gain, 2) if net_gain is not None else None,
        "formula_breakdown": {
            "base_annual_premium": round(base_annual, 2),
            "age_loading_pct": age_loading_pct,
            "age_adjusted_premium": round(age_adjusted, 2),
            "smoker_loading_pct": smoker_loading_pct if is_smoker else 0,
            "smoker_adjusted_premium": round(smoker_adjusted, 2),
            "frequency_multiplier": freq_mult,
            "annual_discounted_premium": round(annual_discounted, 2),
            "periods_per_year": periods,
            "period_premium": round(period_premium, 2),
            "total_premium_outflow": round(total_outflow, 2),
            "return_rate_pct": return_rate,
            "policy_term_years": term,
        },
    }


# ---------------------------------------------------------------------------
# Cloud Function entry point
# ---------------------------------------------------------------------------

@functions_framework.http
def simulate_premium(request):
    """
    HTTP Cloud Function — deterministic premium simulation.
    Accepts POST with JSON body; returns JSON.
    """
    if request.method == "OPTIONS":
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Max-Age": "3600",
        }
        return ("", 204, headers)

    cors_headers = {"Access-Control-Allow-Origin": "*"}

    if request.method != "POST":
        return (
            json.dumps({"validation_errors": ["Only POST is supported"]}),
            405,
            {**cors_headers, "Content-Type": "application/json"},
        )

    try:
        body = request.get_json(silent=True) or {}
    except Exception:
        return (
            json.dumps({"validation_errors": ["Request body must be valid JSON"]}),
            400,
            {**cors_headers, "Content-Type": "application/json"},
        )

    try:
        catalog = _load_catalog()
    except FileNotFoundError as exc:
        return (
            json.dumps({"error": str(exc)}),
            500,
            {**cors_headers, "Content-Type": "application/json"},
        )

    errors = _validate(body, catalog)
    if errors:
        return (
            json.dumps({"validation_errors": errors}),
            400,
            {**cors_headers, "Content-Type": "application/json"},
        )

    product_id: str = body["product_id"]
    sa: float = float(body["sum_assured"])
    age: int = int(body["customer_age"])
    is_smoker: bool = bool(body["is_smoker"])
    frequency: str = body["premium_frequency"]
    term: int = int(body["policy_term"])

    product = catalog[product_id]
    result = _simulate(product, sa, age, is_smoker, frequency, term)

    response = {
        "product_id": product_id,
        "product_name": product.get("name", ""),
        "product_type": product.get("product_type", ""),
        "period_premium": result["period_premium"],
        "annual_premium": result["annual_premium"],
        "total_premium_outflow": result["total_premium_outflow"],
        "projected_maturity_value": result["projected_maturity_value"],
        "net_gain": result["net_gain"],
        "simulation_inputs": {
            "product_id": product_id,
            "sum_assured": sa,
            "customer_age": age,
            "is_smoker": is_smoker,
            "premium_frequency": frequency,
            "policy_term": term,
        },
        "formula_breakdown": result["formula_breakdown"],
    }

    return (
        json.dumps(response),
        200,
        {**cors_headers, "Content-Type": "application/json"},
    )

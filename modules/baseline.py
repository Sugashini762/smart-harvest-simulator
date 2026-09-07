"""
baseline.py
-----------
Implements the simple "traditional" decision process used as a
comparison point against the SmartHarvest simulator.

Baseline rule (Section 10 of the spec):
  1. Harvest when crop reaches maturity (i.e. harvest today - no delay logic).
  2. Select the market with the highest CURRENT price.
  3. Ignore spoilage risk (assume a small fixed/naive spoilage only).
  4. Ignore future price uncertainty (use current price as-is, no risk model).
"""

from dataclasses import dataclass
from typing import List, Dict, Optional


@dataclass
class BaselineResult:
    harvest_option: str
    market_name: str
    price_used: float
    saleable_quantity_kg: float
    spoilage_rate_assumed: float
    gross_revenue: float
    transport_cost: float
    storage_cost: float
    farmer_value: float
    market_available: bool = True
    warning: Optional[str] = None


# The baseline farmer does not model spoilage in detail; they assume a
# small flat naive spoilage figure based only on rough experience.
BASELINE_NAIVE_SPOILAGE_RATE = 0.05


def run_baseline(
    markets: List[Dict],
    harvest_quantity_kg: float,
) -> BaselineResult:
    """
    Traditional decision: harvest today, pick the market with the
    highest current price, ignore spoilage detail and price risk.
    """
    # Filter out markets with missing/invalid current price (still need
    # *some* minimal sanity check or this would crash - Edge Case 1).
    valid_markets = [
        m for m in markets
        if m.get("current_price_per_kg") is not None and m.get("current_price_per_kg") > 0
    ]

    if not valid_markets:
        return BaselineResult(
            harvest_option="Harvest Today",
            market_name="None",
            price_used=0.0,
            saleable_quantity_kg=0.0,
            spoilage_rate_assumed=BASELINE_NAIVE_SPOILAGE_RATE,
            gross_revenue=0.0,
            transport_cost=0.0,
            storage_cost=0.0,
            farmer_value=float("-inf"),
            market_available=False,
            warning="No market has a valid current price.",
        )

    # Rule 2: select the market with the highest CURRENT price only.
    best_market = max(valid_markets, key=lambda m: m["current_price_per_kg"])

    price = best_market["current_price_per_kg"]
    transport_cost_per_kg = best_market.get("transport_cost_per_kg", 0.0) or 0.0

    saleable_quantity = harvest_quantity_kg * (1 - BASELINE_NAIVE_SPOILAGE_RATE)
    gross_revenue = saleable_quantity * price
    transport_cost = transport_cost_per_kg * saleable_quantity
    storage_cost = 0.0  # baseline sells immediately, no storage cost

    farmer_value = gross_revenue - transport_cost - storage_cost

    return BaselineResult(
        harvest_option="Harvest Today",
        market_name=best_market.get("market_name", "Unknown Market"),
        price_used=price,
        saleable_quantity_kg=saleable_quantity,
        spoilage_rate_assumed=BASELINE_NAIVE_SPOILAGE_RATE,
        gross_revenue=gross_revenue,
        transport_cost=transport_cost,
        storage_cost=storage_cost,
        farmer_value=farmer_value,
        market_available=True,
        warning=None,
    )

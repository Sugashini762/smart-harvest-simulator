"""
simulator.py
------------
Core SmartHarvest simulation engine.

Responsibilities:
  * Build price-risk scenarios (Low / Normal / High) and compute expected price.
  * Combine harvest timing options x market options into a full decision matrix.
  * Apply the spoilage model to every combination.
  * Compute Expected Farmer Value for every combination.
  * Apply edge-case / input-validation rules (Section 13 of the spec).
  * Pick the recommended (harvest option, market) combination.

This module is intentionally free of any Streamlit calls so it can be
unit-tested and reused (e.g. by the 100-case evaluation experiment).
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import math

from modules.spoilage import calculate_spoilage, SpoilageBreakdown


def _is_missing(value) -> bool:
    """True for None and for NaN (e.g. a blank cell from a pandas/Streamlit data editor)."""
    if value is None:
        return True
    try:
        return isinstance(value, float) and math.isnan(value)
    except TypeError:
        return False


# ---------------------------------------------------------------------------
# Input validation (Edge Case 6: invalid negative values, etc.)
# ---------------------------------------------------------------------------

class ValidationError(Exception):
    """Raised when a user input fails a sanity check."""
    pass


def validate_inputs(
    harvest_quantity_kg: float,
    crop_maturity_pct: float,
    storage_capacity_kg: float,
    storage_duration_days: float,
    transport_time_hours: float,
    transport_cost_per_kg: float,
) -> List[str]:
    """
    Validate numeric inputs. Returns a list of human-readable error
    messages (empty list = valid). Does not raise, so the UI can
    display every problem at once instead of failing on the first one.
    """
    errors = []
    if harvest_quantity_kg is None or harvest_quantity_kg < 0:
        errors.append("Harvest quantity cannot be negative.")
    if crop_maturity_pct is None or crop_maturity_pct < 0 or crop_maturity_pct > 150:
        errors.append("Crop maturity percentage must be between 0 and 150.")
    if storage_capacity_kg is None or storage_capacity_kg < 0:
        errors.append("Storage capacity cannot be negative.")
    if storage_duration_days is None or storage_duration_days < 0:
        errors.append("Storage duration cannot be negative.")
    if transport_time_hours is None or transport_time_hours < 0:
        errors.append("Transport time cannot be negative.")
    if transport_cost_per_kg is None or transport_cost_per_kg < 0:
        errors.append("Transport cost cannot be negative.")
    return errors


# ---------------------------------------------------------------------------
# Price risk model (Section 9)
# ---------------------------------------------------------------------------

@dataclass
class PriceScenario:
    low_price: float
    normal_price: float
    high_price: float
    p_low: float
    p_normal: float
    p_high: float

    def expected_price(self) -> float:
        total_p = self.p_low + self.p_normal + self.p_high
        if total_p <= 0:
            return self.normal_price
        # Normalize probabilities in case they don't sum exactly to 1.
        p_low, p_normal, p_high = (self.p_low / total_p, self.p_normal / total_p, self.p_high / total_p)
        return (p_low * self.low_price) + (p_normal * self.normal_price) + (p_high * self.high_price)

    def best_case(self) -> float:
        return self.high_price

    def worst_case(self) -> float:
        return self.low_price


def build_price_scenario(
    base_price: float,
    low_pct: float = 0.20,
    high_pct: float = 0.20,
    p_low: float = 0.30,
    p_normal: float = 0.50,
    p_high: float = 0.20,
) -> PriceScenario:
    """Builds a Low/Normal/High price scenario around a base (expected) price."""
    base_price = max(0.0, base_price)
    return PriceScenario(
        low_price=round(base_price * (1 - low_pct), 2),
        normal_price=round(base_price, 2),
        high_price=round(base_price * (1 + high_pct), 2),
        p_low=p_low,
        p_normal=p_normal,
        p_high=p_high,
    )


# ---------------------------------------------------------------------------
# Decision-option data structures
# ---------------------------------------------------------------------------

HARVEST_OPTIONS = ["Harvest Today", "Harvest Tomorrow", "Harvest in 2 Days"]
HARVEST_OPTION_DAYS = {"Harvest Today": 0, "Harvest Tomorrow": 1, "Harvest in 2 Days": 2}

# How much crop maturity typically advances per day of delay (prototype assumption).
MATURITY_GAIN_PER_DAY_PCT = 4.0

# Simple storage cost assumption (prototype): cost per kg per day of storage.
STORAGE_COST_PER_KG_PER_DAY = 0.15


@dataclass
class DecisionResult:
    harvest_option: str
    market_name: str
    expected_maturity_pct: float
    spoilage: SpoilageBreakdown
    saleable_quantity_kg: float
    price_scenario: PriceScenario
    expected_price: float
    storage_duration_days: float
    transport_time_hours: float
    gross_revenue: float
    storage_cost: float
    transport_cost: float
    expected_farmer_value: float
    best_case_value: float
    worst_case_value: float
    market_available: bool = True
    warnings: List[str] = field(default_factory=list)


def _price_for_day(market_row: Dict, day_offset: int) -> Optional[float]:
    """Pick the correct price column for a given harvest-day offset."""
    if day_offset == 0:
        return market_row.get("current_price_per_kg")
    elif day_offset == 1:
        return market_row.get("price_tomorrow_per_kg")
    else:
        return market_row.get("price_day2_per_kg")


def evaluate_option(
    harvest_option: str,
    market_row: Dict,
    crop_params: Dict,
    harvest_quantity_kg: float,
    crop_maturity_pct: float,
    temperature_c: float,
    weather_scenario: str,
    rain_probability_pct: float,
    storage_capacity_kg: float,
    planned_storage_days: float,
    storage_condition: str,
    transport_reliability: str,
    low_pct: float = 0.20,
    high_pct: float = 0.20,
    p_low: float = 0.30,
    p_normal: float = 0.50,
    p_high: float = 0.20,
) -> DecisionResult:
    """
    Evaluate exactly one (harvest_option, market) combination and return
    a fully populated DecisionResult, handling the relevant edge cases:
      - Edge Case 1: missing market price -> market marked unavailable.
      - Edge Case 2: transport unavailable -> market marked unavailable.
      - Edge Case 3: over-mature crop -> warning attached.
      - Edge Case 4: zero storage capacity -> multi-day storage blocked.
      - Edge Case 5: spoilage > 100% -> capped inside calculate_spoilage.
    """
    warnings: List[str] = []
    day_offset = HARVEST_OPTION_DAYS[harvest_option]

    # --- Edge Case 2: transport unavailable ---
    transport_time = market_row.get("transport_time_hours")
    transport_cost_per_kg = market_row.get("transport_cost_per_kg")
    if (_is_missing(transport_time) or _is_missing(transport_cost_per_kg)
            or transport_time < 0 or transport_cost_per_kg < 0):
        return DecisionResult(
            harvest_option=harvest_option,
            market_name=market_row.get("market_name", "Unknown Market"),
            expected_maturity_pct=crop_maturity_pct,
            spoilage=SpoilageBreakdown(),
            saleable_quantity_kg=0.0,
            price_scenario=build_price_scenario(0.0),
            expected_price=0.0,
            storage_duration_days=planned_storage_days,
            transport_time_hours=0.0,
            gross_revenue=0.0,
            storage_cost=0.0,
            transport_cost=0.0,
            expected_farmer_value=float("-inf"),
            best_case_value=float("-inf"),
            worst_case_value=float("-inf"),
            market_available=False,
            warnings=["Transport unavailable for this market. Market excluded from recommendation."],
        )

    # --- Edge Case 1: missing market price ---
    base_price = _price_for_day(market_row, day_offset)
    if _is_missing(base_price) or base_price <= 0:
        return DecisionResult(
            harvest_option=harvest_option,
            market_name=market_row.get("market_name", "Unknown Market"),
            expected_maturity_pct=crop_maturity_pct,
            spoilage=SpoilageBreakdown(),
            saleable_quantity_kg=0.0,
            price_scenario=build_price_scenario(0.0),
            expected_price=0.0,
            storage_duration_days=planned_storage_days,
            transport_time_hours=transport_time,
            gross_revenue=0.0,
            storage_cost=0.0,
            transport_cost=0.0,
            expected_farmer_value=float("-inf"),
            best_case_value=float("-inf"),
            worst_case_value=float("-inf"),
            market_available=False,
            warnings=["Market price missing for this harvest day. Market excluded from recommendation."],
        )

    # --- Expected maturity after delay ---
    expected_maturity = min(150.0, crop_maturity_pct + (day_offset * MATURITY_GAIN_PER_DAY_PCT))

    # --- Edge Case 3: over-mature crop warning ---
    if expected_maturity >= 100.0:
        warnings.append(
            f"Crop maturity reaches {expected_maturity:.0f}% under '{harvest_option}'. "
            "Over-mature crop increases spoilage risk; immediate harvest is generally safer "
            "unless another option clearly provides greater expected value."
        )

    # --- Edge Case 4: zero storage capacity blocks multi-day storage ---
    effective_storage_days = planned_storage_days
    if storage_capacity_kg <= 0 and planned_storage_days > 0:
        effective_storage_days = 0
        warnings.append(
            "Storage capacity is zero: multi-day storage is not possible. "
            "Storage duration forced to 0 days for this calculation."
        )

    # --- Spoilage model ---
    spoilage = calculate_spoilage(
        base_spoilage_rate=crop_params["base_spoilage_rate"],
        temperature_c=temperature_c,
        temperature_sensitivity=crop_params["temperature_sensitivity"],
        storage_duration_days=effective_storage_days,
        storage_condition=storage_condition,
        storage_sensitivity=crop_params["storage_sensitivity"],
        transport_time_hours=transport_time,
        transport_sensitivity=crop_params["transport_sensitivity"],
        transport_reliability=transport_reliability,
        crop_maturity_pct=expected_maturity,
        maturity_sensitivity=crop_params["maturity_sensitivity"],
        weather_scenario=weather_scenario,
        rain_probability_pct=rain_probability_pct,
    )

    # --- Saleable quantity ---
    saleable_quantity = max(0.0, harvest_quantity_kg) * (1 - spoilage.final_rate)

    # --- Price risk / expected price ---
    price_scenario = build_price_scenario(
        base_price, low_pct=low_pct, high_pct=high_pct, p_low=p_low, p_normal=p_normal, p_high=p_high
    )
    expected_price = price_scenario.expected_price()

    # --- Revenue and costs ---
    gross_revenue = saleable_quantity * expected_price
    storage_cost = effective_storage_days * STORAGE_COST_PER_KG_PER_DAY * harvest_quantity_kg
    transport_cost = transport_cost_per_kg * saleable_quantity

    expected_farmer_value = gross_revenue - transport_cost - storage_cost

    best_case_value = (saleable_quantity * price_scenario.best_case()) - transport_cost - storage_cost
    worst_case_value = (saleable_quantity * price_scenario.worst_case()) - transport_cost - storage_cost

    return DecisionResult(
        harvest_option=harvest_option,
        market_name=market_row.get("market_name", "Unknown Market"),
        expected_maturity_pct=expected_maturity,
        spoilage=spoilage,
        saleable_quantity_kg=saleable_quantity,
        price_scenario=price_scenario,
        expected_price=expected_price,
        storage_duration_days=effective_storage_days,
        transport_time_hours=transport_time,
        gross_revenue=gross_revenue,
        storage_cost=storage_cost,
        transport_cost=transport_cost,
        expected_farmer_value=expected_farmer_value,
        best_case_value=best_case_value,
        worst_case_value=worst_case_value,
        market_available=True,
        warnings=warnings,
    )


def run_full_simulation(
    markets: List[Dict],
    crop_params: Dict,
    harvest_quantity_kg: float,
    crop_maturity_pct: float,
    temperature_c: float,
    weather_scenario: str,
    rain_probability_pct: float,
    storage_capacity_kg: float,
    planned_storage_days: float,
    storage_condition: str,
    transport_reliability: str,
    low_pct: float = 0.20,
    high_pct: float = 0.20,
    p_low: float = 0.30,
    p_normal: float = 0.50,
    p_high: float = 0.20,
) -> Tuple[List[DecisionResult], Optional[DecisionResult]]:
    """
    Evaluate every (harvest option x market) combination and return:
      (all_results, recommended_result_or_None)

    The recommendation is the combination with the highest Expected
    Farmer Value among AVAILABLE markets. If no market is available,
    returns None as the recommendation (caller should show a warning).
    """
    all_results: List[DecisionResult] = []
    for harvest_option in HARVEST_OPTIONS:
        for market_row in markets:
            result = evaluate_option(
                harvest_option=harvest_option,
                market_row=market_row,
                crop_params=crop_params,
                harvest_quantity_kg=harvest_quantity_kg,
                crop_maturity_pct=crop_maturity_pct,
                temperature_c=temperature_c,
                weather_scenario=weather_scenario,
                rain_probability_pct=rain_probability_pct,
                storage_capacity_kg=storage_capacity_kg,
                planned_storage_days=planned_storage_days,
                storage_condition=storage_condition,
                transport_reliability=transport_reliability,
                low_pct=low_pct,
                high_pct=high_pct,
                p_low=p_low,
                p_normal=p_normal,
                p_high=p_high,
            )
            all_results.append(result)

    available = [r for r in all_results if r.market_available]
    if not available:
        return all_results, None

    recommended = max(available, key=lambda r: r.expected_farmer_value)
    return all_results, recommended

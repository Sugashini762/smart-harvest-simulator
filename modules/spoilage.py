"""
spoilage.py
-----------
A transparent, rule-based (NOT black-box) spoilage estimation model.

Spoilage Risk =
    Base Spoilage
    + Temperature Effect
    + Storage Effect
    + Transport Effect
    + Maturity Effect
    + Weather Effect

All contributors are returned individually so the UI can explain
*why* spoilage is high. The final rate is clamped to [0, 1] (0%-100%).

All coefficients here are SIMULATED / PROTOTYPE ASSUMPTIONS for a
college proof-of-concept and are NOT verified agricultural facts.
"""

from dataclasses import dataclass, field
from typing import Dict


# Reference points used to build simple linear penalty terms.
# These are prototype assumptions, deliberately kept transparent
# and easy to tune rather than hidden inside a trained model.
REFERENCE_TEMPERATURE_C = 25.0      # "comfortable" baseline temperature
REFERENCE_MATURITY_PCT = 90.0       # maturity above which over-ripening accelerates spoilage
RAIN_PENALTY_SCALE = 0.06           # max additional spoilage fraction from 100% rain probability
WEATHER_SCENARIO_PENALTY = {
    "Normal": 0.0,
    "Hot": 0.03,
    "Rainy": 0.02,
    "Extreme": 0.06,
}
STORAGE_CONDITION_MULTIPLIER = {
    "Cool storage": 0.6,   # cool storage slows spoilage
    "Normal": 1.0,
    "Poor storage": 1.6,   # poor storage accelerates spoilage
}
TRANSPORT_RELIABILITY_PENALTY = {
    "High": 0.0,
    "Medium": 0.015,
    "Low": 0.04,
}


@dataclass
class SpoilageBreakdown:
    """Holds each individual contributor plus the final clamped rate."""
    base_spoilage: float = 0.0
    temperature_effect: float = 0.0
    storage_effect: float = 0.0
    transport_effect: float = 0.0
    maturity_effect: float = 0.0
    weather_effect: float = 0.0
    raw_total: float = 0.0
    final_rate: float = 0.0
    was_capped: bool = False

    def as_dict(self) -> Dict[str, float]:
        return {
            "Base Spoilage": self.base_spoilage,
            "Temperature Effect": self.temperature_effect,
            "Storage Effect": self.storage_effect,
            "Transport Effect": self.transport_effect,
            "Maturity Effect": self.maturity_effect,
            "Weather Effect": self.weather_effect,
            "Raw Total (uncapped)": self.raw_total,
            "Final Spoilage Rate": self.final_rate,
        }


def calculate_spoilage(
    base_spoilage_rate: float,
    temperature_c: float,
    temperature_sensitivity: float,
    storage_duration_days: float,
    storage_condition: str,
    storage_sensitivity: float,
    transport_time_hours: float,
    transport_sensitivity: float,
    transport_reliability: str,
    crop_maturity_pct: float,
    maturity_sensitivity: float,
    weather_scenario: str,
    rain_probability_pct: float,
) -> SpoilageBreakdown:
    """
    Compute a transparent, additive spoilage rate broken into named
    contributors. Every term is a simple, explainable linear function
    of an input variable - there is no hidden model.

    Returns a SpoilageBreakdown with each contributor plus the final
    rate clamped to [0, 1].
    """
    b = SpoilageBreakdown()

    # 1. Base spoilage - intrinsic to the crop, always present.
    b.base_spoilage = max(0.0, base_spoilage_rate)

    # 2. Temperature effect - spoilage rises above the reference temperature.
    #    (No penalty is given for being colder than reference; cold is not
    #    modeled as harmful in this simplified prototype.)
    temp_delta = max(0.0, temperature_c - REFERENCE_TEMPERATURE_C)
    b.temperature_effect = temp_delta * temperature_sensitivity

    # 3. Storage effect - longer storage and worse storage condition raise spoilage.
    storage_multiplier = STORAGE_CONDITION_MULTIPLIER.get(storage_condition, 1.0)
    b.storage_effect = max(0.0, storage_duration_days) * storage_sensitivity * storage_multiplier

    # 4. Transport effect - longer transport time and lower reliability raise spoilage.
    reliability_penalty = TRANSPORT_RELIABILITY_PENALTY.get(transport_reliability, 0.015)
    b.transport_effect = (max(0.0, transport_time_hours) * transport_sensitivity) + reliability_penalty

    # 5. Maturity effect - over-mature crop spoils faster once above the reference maturity.
    maturity_over = max(0.0, crop_maturity_pct - REFERENCE_MATURITY_PCT)
    b.maturity_effect = (maturity_over / 10.0) * maturity_sensitivity

    # 6. Weather effect - scenario penalty plus a rain-probability term.
    scenario_penalty = WEATHER_SCENARIO_PENALTY.get(weather_scenario, 0.0)
    rain_penalty = (max(0.0, min(100.0, rain_probability_pct)) / 100.0) * RAIN_PENALTY_SCALE
    b.weather_effect = scenario_penalty + rain_penalty

    # Sum and clamp.
    b.raw_total = (
        b.base_spoilage
        + b.temperature_effect
        + b.storage_effect
        + b.transport_effect
        + b.maturity_effect
        + b.weather_effect
    )
    b.final_rate = min(1.0, max(0.0, b.raw_total))
    b.was_capped = b.raw_total > 1.0

    return b

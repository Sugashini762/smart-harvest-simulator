"""
sensitivity.py
---------------
Interactive one-at-a-time sensitivity analysis.

For each parameter of interest, we vary it across a small range while
holding everything else fixed, re-run the full simulation, and record:

    Parameter, Baseline Value, Changed Value,
    Recommended Decision Before, Recommended Decision After,
    Change in Expected Farmer Value

We also scan a finer grid to detect "decision-changing assumptions":
the threshold value of a parameter at which the recommended
(harvest option, market) combination actually flips. These thresholds
are DISCOVERED by re-running the simulator repeatedly - never hard-coded.
"""

from dataclasses import dataclass
from typing import List, Dict, Callable, Optional, Tuple
import copy

from modules.simulator import run_full_simulation


@dataclass
class SensitivityRow:
    parameter: str
    baseline_value: float
    changed_value: float
    decision_before: str
    decision_after: str
    value_before: float
    value_after: float
    change_in_value: float


@dataclass
class DecisionChangePoint:
    parameter: str
    threshold_value: float
    decision_before: str
    decision_after: str
    description: str


def _decision_label(recommended) -> str:
    if recommended is None:
        return "No valid recommendation"
    return f"{recommended.harvest_option} -> {recommended.market_name}"


def _run(sim_kwargs: Dict):
    all_results, recommended = run_full_simulation(**sim_kwargs)
    return recommended


def run_one_at_a_time(
    base_kwargs: Dict,
    parameter_ranges: Dict[str, List[float]],
) -> List[SensitivityRow]:
    """
    parameter_ranges maps a parameter name (matching a key in base_kwargs,
    OR a special composite key handled below) to a list of values to try.
    Returns one SensitivityRow comparing the baseline value to each tested value.
    """
    rows: List[SensitivityRow] = []

    baseline_recommended = _run(base_kwargs)
    baseline_value = baseline_recommended.expected_farmer_value if baseline_recommended else 0.0
    baseline_decision = _decision_label(baseline_recommended)

    for param, values in parameter_ranges.items():
        original = base_kwargs.get(param)
        for v in values:
            trial_kwargs = copy.deepcopy(base_kwargs)
            trial_kwargs[param] = v
            trial_recommended = _run(trial_kwargs)
            trial_value = trial_recommended.expected_farmer_value if trial_recommended else 0.0
            trial_decision = _decision_label(trial_recommended)

            rows.append(SensitivityRow(
                parameter=param,
                baseline_value=original if original is not None else 0.0,
                changed_value=v,
                decision_before=baseline_decision,
                decision_after=trial_decision,
                value_before=baseline_value,
                value_after=trial_value,
                change_in_value=trial_value - baseline_value,
            ))

    return rows


def find_decision_change_points(
    base_kwargs: Dict,
    parameter: str,
    scan_values: List[float],
    friendly_name: Optional[str] = None,
) -> List[DecisionChangePoint]:
    """
    Scans `parameter` across `scan_values` (should be sorted ascending)
    and detects every point where the recommended decision flips
    relative to the previous value in the scan. This is computed by
    brute-force re-simulation, not hard-coded.
    """
    friendly_name = friendly_name or parameter
    change_points: List[DecisionChangePoint] = []

    prev_decision = None
    prev_value = None
    for v in scan_values:
        trial_kwargs = copy.deepcopy(base_kwargs)
        trial_kwargs[parameter] = v
        recommended = _run(trial_kwargs)
        decision = _decision_label(recommended)

        if prev_decision is not None and decision != prev_decision:
            change_points.append(DecisionChangePoint(
                parameter=friendly_name,
                threshold_value=v,
                decision_before=prev_decision,
                decision_after=decision,
                description=(
                    f"{friendly_name} crossing {prev_value} -> {v} changes the "
                    f"recommended decision from '{prev_decision}' to '{decision}'."
                ),
            ))
        prev_decision = decision
        prev_value = v

    return change_points


def default_parameter_ranges(base_kwargs: Dict) -> Dict[str, List[float]]:
    """
    Builds a reasonable default set of one-at-a-time ranges around the
    current base_kwargs values, used by the Streamlit sensitivity page.
    """
    maturity = base_kwargs.get("crop_maturity_pct", 85)
    temperature = base_kwargs.get("temperature_c", 28)
    storage_days = base_kwargs.get("planned_storage_days", 1)
    transport_reliability_options = ["High", "Medium", "Low"]
    rain = base_kwargs.get("rain_probability_pct", 20)

    return {
        "crop_maturity_pct": sorted(set([
            max(0, maturity - 20), max(0, maturity - 10), maturity,
            min(150, maturity + 10), min(150, maturity + 20)
        ])),
        "temperature_c": sorted(set([
            max(0, temperature - 8), max(0, temperature - 4), temperature,
            temperature + 4, temperature + 8
        ])),
        "planned_storage_days": sorted(set([
            0, max(0, storage_days - 1), storage_days,
            storage_days + 1, storage_days + 3
        ])),
        "rain_probability_pct": sorted(set([
            0, max(0, rain - 20), rain, min(100, rain + 20), min(100, rain + 40)
        ])),
    }

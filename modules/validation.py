"""
validation.py
--------------
Two responsibilities:

1. Stakeholder feedback storage (SQLite) - Section 17 of the spec.
   Real feedback only. Nothing here is fabricated; the app ships with
   an EMPTY feedback table and a form for real users to fill in.

2. The 100-case measurable experiment comparing Baseline vs Simulator
   (Section 14) plus the Baseline/Target/Measured table (Section 15).
"""

import sqlite3
import random
import time
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional
import os

from modules.simulator import run_full_simulation, evaluate_option
from modules.baseline import run_baseline

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "feedback.db")


# ---------------------------------------------------------------------------
# Feedback storage
# ---------------------------------------------------------------------------

def _get_connection():
    conn = sqlite3.connect(DB_PATH)
    return conn


def init_feedback_db():
    conn = _get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            respondent_name TEXT,
            recommendation_clear INTEGER,
            interface_easy INTEGER,
            spoilage_info_useful INTEGER,
            market_comparison_useful INTEGER,
            would_use INTEGER,
            improvement_suggestion TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_feedback(
    respondent_name: str,
    recommendation_clear: int,
    interface_easy: int,
    spoilage_info_useful: int,
    market_comparison_useful: int,
    would_use: int,
    improvement_suggestion: str,
):
    init_feedback_db()
    conn = _get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO feedback (
            timestamp, respondent_name, recommendation_clear, interface_easy,
            spoilage_info_useful, market_comparison_useful, would_use, improvement_suggestion
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        time.strftime("%Y-%m-%d %H:%M:%S"),
        respondent_name or "Anonymous",
        recommendation_clear, interface_easy, spoilage_info_useful,
        market_comparison_useful, would_use, improvement_suggestion,
    ))
    conn.commit()
    conn.close()


def load_feedback() -> List[Dict]:
    init_feedback_db()
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM feedback ORDER BY id DESC")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def feedback_summary() -> Optional[Dict]:
    rows = load_feedback()
    if not rows:
        return None
    n = len(rows)
    summary = {
        "responses": n,
        "avg_recommendation_clear": sum(r["recommendation_clear"] for r in rows) / n,
        "avg_interface_easy": sum(r["interface_easy"] for r in rows) / n,
        "avg_spoilage_info_useful": sum(r["spoilage_info_useful"] for r in rows) / n,
        "avg_market_comparison_useful": sum(r["market_comparison_useful"] for r in rows) / n,
        "pct_would_use": 100.0 * sum(1 for r in rows if r["would_use"] >= 4) / n,
    }
    return summary


# ---------------------------------------------------------------------------
# 100-case measurable experiment
# ---------------------------------------------------------------------------

WEATHER_SCENARIOS = ["Normal", "Hot", "Rainy", "Extreme"]
STORAGE_CONDITIONS = ["Normal", "Cool storage", "Poor storage"]
TRANSPORT_RELIABILITY = ["High", "Medium", "Low"]


def _random_case(seed: int, crop_params: Dict, base_markets: List[Dict]) -> Dict:
    """Generate one randomized but realistic decision case."""
    rng = random.Random(seed)

    maturity = rng.uniform(60, 130)
    temperature = rng.uniform(20, 42)
    weather = rng.choice(WEATHER_SCENARIOS)
    rain_prob = rng.uniform(0, 90)
    storage_capacity = rng.choice([0, 50, 200, 500, 1000])
    storage_days = rng.choice([0, 1, 2, 3])
    storage_condition = rng.choice(STORAGE_CONDITIONS)
    transport_reliability = rng.choice(TRANSPORT_RELIABILITY)
    harvest_qty = rng.uniform(100, 2000)

    # Randomize market prices a bit per case, occasionally drop a price
    # (Edge Case 1) to exercise robustness during the experiment.
    markets = []
    for m in base_markets:
        m2 = dict(m)
        jitter = rng.uniform(0.85, 1.15)
        m2["current_price_per_kg"] = round(m["current_price_per_kg"] * jitter, 2)
        m2["price_tomorrow_per_kg"] = round(m["price_tomorrow_per_kg"] * rng.uniform(0.85, 1.15), 2)
        m2["price_day2_per_kg"] = round(m["price_day2_per_kg"] * rng.uniform(0.85, 1.15), 2)
        # 5% chance a price is missing this case (Edge Case 1 stress test)
        if rng.random() < 0.05:
            m2["current_price_per_kg"] = None
        markets.append(m2)

    return dict(
        markets=markets,
        crop_params=crop_params,
        harvest_quantity_kg=harvest_qty,
        crop_maturity_pct=maturity,
        temperature_c=temperature,
        weather_scenario=weather,
        rain_probability_pct=rain_prob,
        storage_capacity_kg=storage_capacity,
        planned_storage_days=storage_days,
        storage_condition=storage_condition,
        transport_reliability=transport_reliability,
    )


@dataclass
class ExperimentCaseResult:
    case_id: int
    baseline_value: float
    baseline_spoilage: float
    simulator_value: float
    simulator_spoilage: float
    saleable_quantity_kg: float
    decision_time_seconds: float
    simulator_valid: bool
    simulator_decision: str
    baseline_decision: str


def run_experiment(
    n_cases: int,
    crop_params: Dict,
    base_markets: List[Dict],
    seed: int = 42,
) -> List[ExperimentCaseResult]:
    """
    Runs n_cases randomized decision cases, evaluating both the Baseline
    method and the SmartHarvest simulator on each, and timing the
    simulator's decision computation.
    """
    results: List[ExperimentCaseResult] = []
    base_rng = random.Random(seed)

    for i in range(n_cases):
        case_seed = base_rng.randint(0, 10_000_000)
        case = _random_case(case_seed, crop_params, base_markets)

        t0 = time.perf_counter()
        all_results, recommended = run_full_simulation(
            markets=case["markets"],
            crop_params=case["crop_params"],
            harvest_quantity_kg=case["harvest_quantity_kg"],
            crop_maturity_pct=case["crop_maturity_pct"],
            temperature_c=case["temperature_c"],
            weather_scenario=case["weather_scenario"],
            rain_probability_pct=case["rain_probability_pct"],
            storage_capacity_kg=case["storage_capacity_kg"],
            planned_storage_days=case["planned_storage_days"],
            storage_condition=case["storage_condition"],
            transport_reliability=case["transport_reliability"],
        )
        t1 = time.perf_counter()
        decision_time = t1 - t0  # computation time; real-world decision time is dominated by data entry (<2 min target)

        # Baseline picks its decision using its own naive rule (highest
        # CURRENT price, flat 5% spoilage assumption, no risk model).
        baseline_naive = run_baseline(case["markets"], case["harvest_quantity_kg"])

        if recommended is not None:
            sim_value = recommended.expected_farmer_value
            sim_spoilage = recommended.spoilage.final_rate
            sim_qty = recommended.saleable_quantity_kg
            sim_valid = True
            sim_decision = f"{recommended.harvest_option} -> {recommended.market_name}"
        else:
            sim_value = 0.0
            sim_spoilage = 1.0
            sim_qty = 0.0
            sim_valid = False
            sim_decision = "No valid recommendation"

        # Fair comparison: what does baseline's chosen decision ACTUALLY
        # yield once the real spoilage physics (temperature, storage,
        # transport, maturity, weather) are applied - not baseline's own
        # optimistic flat-5% belief? We re-evaluate baseline's exact
        # (harvest today, chosen market) decision through the same
        # realistic spoilage model used for the simulator, holding the
        # real-world conditions identical for both. This isolates the
        # effect of the DECISION PROCESS rather than differing spoilage
        # bookkeeping.
        baseline_decision = f"{baseline_naive.harvest_option} -> {baseline_naive.market_name}"
        if baseline_naive.market_available:
            chosen_market_row = next(
                (m for m in case["markets"] if m.get("market_name") == baseline_naive.market_name), None
            )
            if chosen_market_row is not None:
                baseline_realistic = evaluate_option(
                    harvest_option="Harvest Today",
                    market_row=chosen_market_row,
                    crop_params=case["crop_params"],
                    harvest_quantity_kg=case["harvest_quantity_kg"],
                    crop_maturity_pct=case["crop_maturity_pct"],
                    temperature_c=case["temperature_c"],
                    weather_scenario=case["weather_scenario"],
                    rain_probability_pct=case["rain_probability_pct"],
                    storage_capacity_kg=case["storage_capacity_kg"],
                    planned_storage_days=0,  # baseline sells immediately, no planned storage
                    storage_condition=case["storage_condition"],
                    transport_reliability=case["transport_reliability"],
                    # Baseline ignores price risk - use a degenerate scenario
                    # equal to the current price (no low/high spread).
                    low_pct=0.0, high_pct=0.0, p_low=0.0, p_normal=1.0, p_high=0.0,
                )
                baseline_value = baseline_realistic.expected_farmer_value
                baseline_spoilage = baseline_realistic.spoilage.final_rate
            else:
                baseline_value = 0.0
                baseline_spoilage = 1.0
        else:
            baseline_value = 0.0
            baseline_spoilage = 1.0

        results.append(ExperimentCaseResult(
            case_id=i + 1,
            baseline_value=baseline_value,
            baseline_spoilage=baseline_spoilage,
            simulator_value=sim_value,
            simulator_spoilage=sim_spoilage,
            saleable_quantity_kg=sim_qty,
            decision_time_seconds=decision_time,
            simulator_valid=sim_valid,
            simulator_decision=sim_decision,
            baseline_decision=baseline_decision,
        ))

    return results


def summarize_experiment(results: List[ExperimentCaseResult]) -> Dict:
    """Aggregate the 100-case experiment into the Baseline/Target/Measured table values."""
    n = len(results)
    valid = [r for r in results if r.simulator_valid and r.baseline_value > 0]

    if not valid:
        return {
            "n_cases": n,
            "n_valid": 0,
            "avg_baseline_value": 0.0,
            "avg_simulator_value": 0.0,
            "value_improvement_pct": 0.0,
            "avg_baseline_spoilage": 0.0,
            "avg_simulator_spoilage": 0.0,
            "spoilage_reduction_pct": 0.0,
            "avg_decision_time_seconds": 0.0,
            "pct_valid_recommendations": 0.0,
        }

    avg_baseline_value = sum(r.baseline_value for r in valid) / len(valid)
    avg_simulator_value = sum(r.simulator_value for r in valid) / len(valid)
    value_improvement_pct = (
        ((avg_simulator_value - avg_baseline_value) / avg_baseline_value) * 100
        if avg_baseline_value != 0 else 0.0
    )

    avg_baseline_spoilage = sum(r.baseline_spoilage for r in valid) / len(valid)
    avg_simulator_spoilage = sum(r.simulator_spoilage for r in valid) / len(valid)
    spoilage_reduction_pct = (
        ((avg_baseline_spoilage - avg_simulator_spoilage) / avg_baseline_spoilage) * 100
        if avg_baseline_spoilage != 0 else 0.0
    )

    avg_decision_time = sum(r.decision_time_seconds for r in results) / n
    pct_valid = 100.0 * sum(1 for r in results if r.simulator_valid) / n

    return {
        "n_cases": n,
        "n_valid": len(valid),
        "avg_baseline_value": avg_baseline_value,
        "avg_simulator_value": avg_simulator_value,
        "value_improvement_pct": value_improvement_pct,
        "avg_baseline_spoilage": avg_baseline_spoilage,
        "avg_simulator_spoilage": avg_simulator_spoilage,
        "spoilage_reduction_pct": spoilage_reduction_pct,
        "avg_decision_time_seconds": avg_decision_time,
        "pct_valid_recommendations": pct_valid,
    }

"""
test_cases.py
-------------
Unit tests covering the spoilage model, the simulator's edge-case
handling (Section 13), the baseline method, and the sensitivity engine.

Run with:
    python -m pytest tests/test_cases.py -v
or:
    python tests/test_cases.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


import unittest

from modules.spoilage import calculate_spoilage
from modules.simulator import (
    run_full_simulation, evaluate_option, validate_inputs, build_price_scenario
)
from modules.baseline import run_baseline


CROP_PARAMS = {
    "base_spoilage_rate": 0.03,
    "temperature_sensitivity": 0.004,
    "storage_sensitivity": 0.02,
    "transport_sensitivity": 0.015,
    "maturity_sensitivity": 0.05,
}

GOOD_MARKETS = [
    {"market_name": "Market A", "current_price_per_kg": 18.0, "price_tomorrow_per_kg": 18.5,
     "price_day2_per_kg": 19.0, "transport_time_hours": 2, "transport_cost_per_kg": 1.0, "demand_risk": "Low"},
    {"market_name": "Market B", "current_price_per_kg": 22.0, "price_tomorrow_per_kg": 21.0,
     "price_day2_per_kg": 17.5, "transport_time_hours": 7, "transport_cost_per_kg": 2.5, "demand_risk": "High"},
    {"market_name": "Market C", "current_price_per_kg": 19.5, "price_tomorrow_per_kg": 20.0,
     "price_day2_per_kg": 20.5, "transport_time_hours": 4, "transport_cost_per_kg": 1.5, "demand_risk": "Medium"},
]


class TestSpoilageModel(unittest.TestCase):

    def test_spoilage_within_bounds(self):
        b = calculate_spoilage(
            base_spoilage_rate=0.03, temperature_c=28, temperature_sensitivity=0.004,
            storage_duration_days=1, storage_condition="Normal", storage_sensitivity=0.02,
            transport_time_hours=4, transport_sensitivity=0.015, transport_reliability="High",
            crop_maturity_pct=85, maturity_sensitivity=0.05,
            weather_scenario="Normal", rain_probability_pct=20,
        )
        self.assertGreaterEqual(b.final_rate, 0.0)
        self.assertLessEqual(b.final_rate, 1.0)

    def test_extreme_conditions_cap_at_100_percent(self):
        # Edge Case 5: spoilage calculation exceeds 100% -> must be capped.
        b = calculate_spoilage(
            base_spoilage_rate=0.5, temperature_c=60, temperature_sensitivity=0.5,
            storage_duration_days=30, storage_condition="Poor storage", storage_sensitivity=0.5,
            transport_time_hours=100, transport_sensitivity=0.5, transport_reliability="Low",
            crop_maturity_pct=200, maturity_sensitivity=0.5,
            weather_scenario="Extreme", rain_probability_pct=100,
        )
        self.assertEqual(b.final_rate, 1.0)
        self.assertTrue(b.was_capped)
        self.assertGreater(b.raw_total, 1.0)

    def test_higher_temperature_increases_spoilage(self):
        low = calculate_spoilage(0.03, 25, 0.004, 1, "Normal", 0.02, 4, 0.015, "High", 85, 0.05, "Normal", 20)
        high = calculate_spoilage(0.03, 40, 0.004, 1, "Normal", 0.02, 4, 0.015, "High", 85, 0.05, "Normal", 20)
        self.assertGreater(high.final_rate, low.final_rate)

    def test_cool_storage_reduces_spoilage_vs_poor_storage(self):
        cool = calculate_spoilage(0.03, 28, 0.004, 3, "Cool storage", 0.02, 4, 0.015, "High", 85, 0.05, "Normal", 20)
        poor = calculate_spoilage(0.03, 28, 0.004, 3, "Poor storage", 0.02, 4, 0.015, "High", 85, 0.05, "Normal", 20)
        self.assertLess(cool.final_rate, poor.final_rate)


class TestInputValidation(unittest.TestCase):

    def test_negative_quantity_is_invalid(self):
        errors = validate_inputs(
            harvest_quantity_kg=-10, crop_maturity_pct=80, storage_capacity_kg=100,
            storage_duration_days=1, transport_time_hours=2, transport_cost_per_kg=1.0,
        )
        self.assertTrue(any("quantity" in e.lower() for e in errors))

    def test_valid_inputs_produce_no_errors(self):
        errors = validate_inputs(
            harvest_quantity_kg=500, crop_maturity_pct=85, storage_capacity_kg=200,
            storage_duration_days=1, transport_time_hours=4, transport_cost_per_kg=1.5,
        )
        self.assertEqual(errors, [])


class TestEdgeCases(unittest.TestCase):

    def test_missing_market_price_excluded(self):
        markets = [dict(m) for m in GOOD_MARKETS]
        markets[0]["current_price_per_kg"] = None
        result = evaluate_option(
            harvest_option="Harvest Today", market_row=markets[0], crop_params=CROP_PARAMS,
            harvest_quantity_kg=500, crop_maturity_pct=85, temperature_c=28,
            weather_scenario="Normal", rain_probability_pct=20, storage_capacity_kg=200,
            planned_storage_days=1, storage_condition="Normal", transport_reliability="High",
        )
        self.assertFalse(result.market_available)

    def test_transport_unavailable_market_excluded(self):
        markets = [dict(m) for m in GOOD_MARKETS]
        markets[0]["transport_time_hours"] = None
        result = evaluate_option(
            harvest_option="Harvest Today", market_row=markets[0], crop_params=CROP_PARAMS,
            harvest_quantity_kg=500, crop_maturity_pct=85, temperature_c=28,
            weather_scenario="Normal", rain_probability_pct=20, storage_capacity_kg=200,
            planned_storage_days=1, storage_condition="Normal", transport_reliability="High",
        )
        self.assertFalse(result.market_available)

    def test_over_mature_crop_warns(self):
        result = evaluate_option(
            harvest_option="Harvest in 2 Days", market_row=GOOD_MARKETS[0], crop_params=CROP_PARAMS,
            harvest_quantity_kg=500, crop_maturity_pct=98, temperature_c=28,
            weather_scenario="Normal", rain_probability_pct=20, storage_capacity_kg=200,
            planned_storage_days=1, storage_condition="Normal", transport_reliability="High",
        )
        self.assertTrue(any("over-mature" in w.lower() or "maturity" in w.lower() for w in result.warnings))

    def test_zero_storage_capacity_blocks_multiday_storage(self):
        result = evaluate_option(
            harvest_option="Harvest Today", market_row=GOOD_MARKETS[0], crop_params=CROP_PARAMS,
            harvest_quantity_kg=500, crop_maturity_pct=85, temperature_c=28,
            weather_scenario="Normal", rain_probability_pct=20, storage_capacity_kg=0,
            planned_storage_days=3, storage_condition="Normal", transport_reliability="High",
        )
        self.assertEqual(result.storage_duration_days, 0)

    def test_full_simulation_returns_recommendation(self):
        all_results, recommended = run_full_simulation(
            markets=GOOD_MARKETS, crop_params=CROP_PARAMS, harvest_quantity_kg=500,
            crop_maturity_pct=85, temperature_c=28, weather_scenario="Normal",
            rain_probability_pct=20, storage_capacity_kg=200, planned_storage_days=1,
            storage_condition="Normal", transport_reliability="High",
        )
        self.assertIsNotNone(recommended)
        self.assertEqual(len(all_results), 3 * 3)  # 3 harvest options x 3 markets

    def test_all_markets_unavailable_returns_none_recommendation(self):
        broken_markets = [
            dict(m, current_price_per_kg=None, price_tomorrow_per_kg=None, price_day2_per_kg=None)
            for m in GOOD_MARKETS
        ]
        all_results, recommended = run_full_simulation(
            markets=broken_markets, crop_params=CROP_PARAMS, harvest_quantity_kg=500,
            crop_maturity_pct=85, temperature_c=28, weather_scenario="Normal",
            rain_probability_pct=20, storage_capacity_kg=200, planned_storage_days=1,
            storage_condition="Normal", transport_reliability="High",
        )
        self.assertIsNone(recommended)


class TestBaseline(unittest.TestCase):

    def test_baseline_picks_highest_current_price(self):
        result = run_baseline(GOOD_MARKETS, 500)
        self.assertEqual(result.market_name, "Market B")  # highest current price in fixture

    def test_baseline_handles_no_valid_market(self):
        broken_markets = [dict(m, current_price_per_kg=None) for m in GOOD_MARKETS]
        result = run_baseline(broken_markets, 500)
        self.assertFalse(result.market_available)


class TestPriceRisk(unittest.TestCase):

    def test_expected_price_between_low_and_high(self):
        scenario = build_price_scenario(20.0)
        expected = scenario.expected_price()
        self.assertGreaterEqual(expected, scenario.low_price)
        self.assertLessEqual(expected, scenario.high_price)

    def test_probabilities_normalize(self):
        scenario = build_price_scenario(20.0, p_low=3, p_normal=5, p_high=2)  # sums to 10, not 1
        expected = scenario.expected_price()
        self.assertGreaterEqual(expected, scenario.low_price)
        self.assertLessEqual(expected, scenario.high_price)


if __name__ == "__main__":
    unittest.main(verbosity=2)

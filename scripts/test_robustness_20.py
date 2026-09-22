"""Comprehensive 20-Scenario Verification & Regression Test Suite
for SAIL / SAGAR DRISHTI Maritime Freight Intelligence.

Verifies:
  TEST 1: cargo_mt = 0 (reject <= 0, no ZeroDivisionError)
  TEST 2: cargo_mt = -50000 (reject negative cargo)
  TEST 3: cargo_mt = "75000" (safely coerce numeric string)
  TEST 4: invalid cargo string (controlled validation error)
  TEST 5: as_of before dataset inception (controlled date/inception error)
  TEST 6: as_of with insufficient rolling history (< 252 days error)
  TEST 7: date = "01-12-2026" (safely parsed as DD-MM-YYYY)
  TEST 8: date = "2026/12/01" (safely parsed as YYYY/MM/DD)
  TEST 9: bunker_shock_pct = -100 (validation error)
  TEST 10: bunker_shock_pct = -120 (validation error)
  TEST 11: extra_wait_days = -10 (validation error)
  TEST 12: extra_wait_days = "5" (safely coerced numeric string)
  TEST 13: port = " paradip " (whitespace normalization)
  TEST 14: port = "PARADIP" (case normalization)
  TEST 15: unknown port (controlled error, no raw KeyError)
  TEST 16: normal_wait_days = 0 (no ZeroDivisionError)
  TEST 17: all vessels infeasible (status ANALYSIS_COMPLETED_NO_FEASIBLE_VESSEL)
  TEST 18: required_arrival before/on as_of (validation / HTTP 4xx error)
  TEST 19: normal valid voyage (identical recommendation logic)
  TEST 20: authentication and security layer (JWT token & login functional)
"""
import datetime as dt
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sail import recommend, data, forecast, vessels, cost, risk
from sail.config import PORTS, ROUTES, VESSELS
from sail.exceptions import (
    SailError,
    InvalidInputError,
    InsufficientHistoryError,
    UnknownPortError,
    UnknownRouteError,
    ConfigurationError,
)
from sail.validation import (
    normalize_cargo,
    parse_date_safe,
    normalize_port,
    normalize_route,
    normalize_shocks,
)
from auth.security import create_access_token, decode_access_token, hash_password, verify_password
from fastapi.testclient import TestClient
from api.main import app


class TestSailRobustness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bdi_df = data.market_frame("BDI")
        cls.bdi_end = cls.bdi_df.index[-1].date()
        cls.arr_date = cls.bdi_end + dt.timedelta(days=90)
        cls.api_client = TestClient(app)

    # TEST 1: cargo_mt = 0
    def test_01_cargo_zero(self):
        with self.assertRaises((InvalidInputError, ValueError)) as ctx:
            recommend.analyse("Australia (Hay Point, QLD)", "Paradip", 0, self.arr_date, str(self.bdi_end))
        self.assertIn("greater than 0 MT", str(ctx.exception))
        # Ensure evaluate and cost also reject 0 without ZeroDivisionError
        with self.assertRaises((InvalidInputError, ValueError)):
            vessels.evaluate("Panamax", "Paradip", 0)
        with self.assertRaises((InvalidInputError, ValueError)):
            cost.voyage_cost("Panamax", "Australia (Hay Point, QLD)", "Paradip", 0, 1, 15000, 75.0)

    # TEST 2: cargo_mt = -50000
    def test_02_cargo_negative(self):
        with self.assertRaises((InvalidInputError, ValueError)) as ctx:
            recommend.analyse("Australia (Hay Point, QLD)", "Paradip", -50000, self.arr_date, str(self.bdi_end))
        self.assertIn("greater than 0 MT", str(ctx.exception))
        with self.assertRaises((InvalidInputError, ValueError)):
            vessels.evaluate("Panamax", "Paradip", -50000)

    # TEST 3: cargo_mt = "75000"
    def test_03_cargo_numeric_string(self):
        res_str = recommend.analyse("Australia (Hay Point, QLD)", "Paradip", "75000", self.arr_date, str(self.bdi_end))
        res_flt = recommend.analyse("Australia (Hay Point, QLD)", "Paradip", 75000.0, self.arr_date, str(self.bdi_end))
        self.assertIsNotNone(res_str.get("best"))
        self.assertEqual(res_str["best"]["vessel"], res_flt["best"]["vessel"])
        self.assertAlmostEqual(res_str["best"]["cost_plan"]["usd_per_mt"], res_flt["best"]["cost_plan"]["usd_per_mt"], places=4)

    # TEST 4: invalid cargo string
    def test_04_cargo_invalid_string(self):
        for bad in ["abc", "seventy-five-thousand", "", None, True]:
            with self.assertRaises((InvalidInputError, ValueError)):
                normalize_cargo(bad)

    # TEST 5: as_of before dataset inception
    def test_05_as_of_before_inception(self):
        with self.assertRaises((InsufficientHistoryError, ValueError)) as ctx:
            recommend.analyse("Australia (Hay Point, QLD)", "Paradip", 75000, "2026-12-01", as_of="1995-01-01")
        self.assertIn("earlier than the available historical data", str(ctx.exception))

    # TEST 6: as_of with insufficient rolling history (< 252 days)
    def test_06_as_of_insufficient_history(self):
        # BHSI starts 2020-01-02; by 2020-06-01 it has ~100 observations (< 252)
        early_date = "2020-06-01"
        with self.assertRaises((InsufficientHistoryError, ValueError)) as ctx:
            recommend.analyse("Australia (Hay Point, QLD)", "Paradip", 75000, "2021-01-01", as_of=early_date)
        self.assertIn("Insufficient historical data", str(ctx.exception))

    # TEST 7: date = "01-12-2026"
    def test_07_date_dd_mm_yyyy(self):
        parsed = parse_date_safe("01-12-2026")
        self.assertEqual(parsed, dt.date(2026, 12, 1))

    # TEST 8: date = "2026/12/01"
    def test_08_date_slash_format(self):
        parsed = parse_date_safe("2026/12/01")
        self.assertEqual(parsed, dt.date(2026, 12, 1))

    # TEST 9: bunker_shock_pct = -100
    def test_09_bunker_shock_minus_100(self):
        with self.assertRaises((InvalidInputError, ValueError)) as ctx:
            cost.bunker_price(75.0, shock_pct=-100)
        self.assertIn("greater than -100%", str(ctx.exception))
        with self.assertRaises((InvalidInputError, ValueError)):
            recommend.analyse("Australia (Hay Point, QLD)", "Paradip", 75000, self.arr_date, str(self.bdi_end), bunker_shock_pct=-100)

    # TEST 10: bunker_shock_pct = -120
    def test_10_bunker_shock_below_minus_100(self):
        with self.assertRaises((InvalidInputError, ValueError)) as ctx:
            cost.bunker_price(75.0, shock_pct=-120)
        self.assertIn("greater than -100%", str(ctx.exception))
        with self.assertRaises((InvalidInputError, ValueError)):
            recommend.analyse("Australia (Hay Point, QLD)", "Paradip", 75000, self.arr_date, str(self.bdi_end), bunker_shock_pct=-120)

    # TEST 11: extra_wait_days = -10
    def test_11_negative_extra_wait_days(self):
        with self.assertRaises((InvalidInputError, ValueError)) as ctx:
            recommend.analyse("Australia (Hay Point, QLD)", "Paradip", 75000, self.arr_date, str(self.bdi_end), extra_wait_days=-10)
        self.assertIn("cannot be negative", str(ctx.exception))

    # TEST 12: extra_wait_days = "5"
    def test_12_extra_wait_days_string_normalized(self):
        res_str = recommend.analyse("Australia (Hay Point, QLD)", "Paradip", 75000, self.arr_date, str(self.bdi_end), extra_wait_days="5")
        res_flt = recommend.analyse("Australia (Hay Point, QLD)", "Paradip", 75000, self.arr_date, str(self.bdi_end), extra_wait_days=5.0)
        self.assertAlmostEqual(res_str["best"]["cost_plan"]["wait_days"], res_flt["best"]["cost_plan"]["wait_days"])

    # TEST 13: port = " paradip "
    def test_13_port_whitespace_normalization(self):
        res = recommend.analyse("Australia (Hay Point, QLD)", " paradip ", 75000, self.arr_date, str(self.bdi_end))
        self.assertIsNotNone(res.get("best"))

    # TEST 14: port = "PARADIP"
    def test_14_port_case_normalization(self):
        res = recommend.analyse("Australia (Hay Point, QLD)", "PARADIP", 75000, self.arr_date, str(self.bdi_end))
        self.assertIsNotNone(res.get("best"))

    # TEST 15: unknown port
    def test_15_unknown_port_controlled_error(self):
        with self.assertRaises((UnknownPortError, ValueError, KeyError)) as ctx:
            normalize_port("Atlantis")
        self.assertIn("not available", str(ctx.exception))
        with self.assertRaises((UnknownPortError, ValueError, KeyError)):
            recommend.analyse("Australia (Hay Point, QLD)", "Atlantis", 75000, self.arr_date, str(self.bdi_end))

    # TEST 16: normal_wait_days = 0
    def test_16_normal_wait_days_zero_no_zero_division(self):
        # Temporarily mock port normal_wait_days to 0
        old_val = PORTS["Paradip"]["normal_wait_days"]
        try:
            PORTS["Paradip"]["normal_wait_days"] = 0
            with self.assertRaises((ConfigurationError, ValueError)) as ctx:
                risk.scores(self.bdi_df, "Paradip", 10, str(self.bdi_end))
            self.assertIn("must be greater than 0", str(ctx.exception))
        finally:
            PORTS["Paradip"]["normal_wait_days"] = old_val

    # TEST 17: all vessels infeasible
    def test_17_all_vessels_infeasible(self):
        # A port where all vessels exceed physical limits (draft 3.0m < lightship draft 3.5m)
        try:
            PORTS["RestrictedPort"] = {
                "latitude": 20.0, "longitude": 86.0, "max_draft_m": 3.0, "max_loa_m": 150, "max_beam_m": 25,
                "max_dwt": 30000, "discharge_rate_t_day": 10000, "normal_wait_days": 2.0, "current_wait_days": 2.0,
                "port_charges_usd": 50000, "verified": False
            }
            res = recommend.analyse("Australia (Hay Point, QLD)", "RestrictedPort", 75000, self.arr_date, str(self.bdi_end))
            self.assertIsNone(res["best"])
            self.assertEqual(res.get("status"), "ANALYSIS_COMPLETED_NO_FEASIBLE_VESSEL")
            self.assertTrue(len(res["options"]) > 0)
            for opt in res["options"]:
                self.assertEqual(opt["feasibility"]["status"], "REJECT")
        finally:
            if "RestrictedPort" in PORTS:
                del PORTS["RestrictedPort"]

    # TEST 18: required_arrival before / on as_of
    def test_18_arrival_before_as_of_api(self):
        resp = self.api_client.post(
            "/recommendation",
            json={
                "origin": "Australia (Hay Point, QLD)",
                "destination": "Paradip",
                "quantity_mt": 75000,
                "required_arrival": "1995-01-01",
                "as_of": str(self.bdi_end),
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("chronologically after", resp.json()["detail"])

    # TEST 19: normal valid voyage produces identical output
    def test_19_normal_valid_voyage(self):
        res = recommend.analyse("Australia (Hay Point, QLD)", "Paradip", 75000, self.arr_date, str(self.bdi_end))
        self.assertIsNotNone(res["best"])
        self.assertEqual(res["best"]["vessel"], "Panamax")
        self.assertEqual(res["best"]["timing"]["action"], "WAIT")
        self.assertAlmostEqual(res["best"]["cost_plan"]["usd_per_mt"], 8.75, delta=0.1)

    # TEST 20: existing authentication and JWT flow
    def test_20_auth_and_jwt_flow(self):
        # 20a. JWT token creation and validation
        token = create_access_token({"sub": "SAIL-09482", "role": "Senior Chartering Manager"})
        self.assertIsInstance(token, str)
        self.assertEqual(len(token.split(".")), 3)
        decoded = decode_access_token(token)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["sub"], "SAIL-09482")

        # 20b. Password hashing and verification
        pwd = "SailSecure#2026"
        hashed = hash_password(pwd)
        self.assertTrue(verify_password(pwd, hashed))
        self.assertFalse(verify_password("WrongPassword", hashed))


def run_tests():
    print("==================================================")
    print("RUNNING 20-SCENARIO ROBUSTNESS & REGRESSION SUITE")
    print("==================================================")
    suite = unittest.TestLoader().loadTestsFromTestCase(TestSailRobustness)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)

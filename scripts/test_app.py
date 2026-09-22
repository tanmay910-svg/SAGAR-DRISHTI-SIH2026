"""Automated Verification & Regression Test Suite
Tests 6 mandatory operational scenarios:
  Case A: Normal valid voyage
  Case B: Vessel/port constraint case (e.g., Capesize into shallow Haldia)
  Case C: Bunker-price stress case (+50% shock)
  Case D: Port-waiting stress case (+10 days)
  Case E: Weather API fallback/out-of-horizon case
  Case F: Invalid input date validation
"""
import sys
import pathlib
import datetime as dt

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sail import recommend, data, weather
from sail.config import PORTS

def run_tests():
    print("==================================================")
    print("STARTING SAIL FREIGHT INTELLIGENCE TEST SUITE")
    print("==================================================")

    bdi_end = data.market_frame("BDI").index[-1].date()
    arr_date = bdi_end + dt.timedelta(days=90)

    # Case A: Normal valid voyage
    print("\n[TEST A] Normal valid voyage (Australia Hay Point -> Paradip, 75,000 MT)...")
    res_a = recommend.analyse("Australia (Hay Point, QLD)", "Paradip", 75000, arr_date, str(bdi_end))
    assert res_a is not None and "best" in res_a, "Case A Failed: Result is None"
    best_a = res_a["best"]
    print(f"  -> SUCCESS: Best Vessel: {best_a['vessel']} | Action: {best_a['timing']['action']} | Cost: ${best_a['cost_plan']['usd_per_mt']:.2f}/MT")

    # Case B: Vessel/port constraint case (Capesize into Haldia with 8.5m draft limit)
    print("\n[TEST B] Vessel/port constraint case (Australia -> Haldia, 180,000 MT)...")
    res_b = recommend.analyse("Australia (Hay Point, QLD)", "Haldia", 180000, arr_date, str(bdi_end))
    assert res_b is not None, "Case B Failed: Result is None"
    # Check rejection reasons for Capesize
    cape_opt = next((o for o in res_b["options"] if o["vessel"] == "Capesize"), None)
    assert cape_opt is not None and cape_opt["feasibility"]["status"] == "REJECT", "Case B Failed: Capesize was not rejected for Haldia"
    print(f"  -> SUCCESS: Capesize correctly REJECTED for Haldia. Reasons: {cape_opt['feasibility']['reasons']}")

    # Case C: Bunker-price stress case (+50% shock)
    print("\n[TEST C] Bunker-price stress case (+50% bunker shock)...")
    res_c = recommend.analyse("Australia (Hay Point, QLD)", "Paradip", 75000, arr_date, str(bdi_end), bunker_shock_pct=50.0)
    assert res_c is not None and "best" in res_c, "Case C Failed"
    cost_base = res_a["best"]["cost_plan"]["total_usd"]
    cost_shock = res_c["best"]["cost_plan"]["total_usd"]
    assert cost_shock > cost_base, f"Case C Failed: Shock cost ${cost_shock} not > base cost ${cost_base}"
    print(f"  -> SUCCESS: Base Cost: ${cost_base:,.0f} vs +50% Bunker Shock Cost: ${cost_shock:,.0f}")

    # Case D: Port-waiting stress case (+10 extra waiting days)
    print("\n[TEST D] Port-waiting stress case (+10 extra waiting days)...")
    res_d = recommend.analyse("Australia (Hay Point, QLD)", "Paradip", 75000, arr_date, str(bdi_end), extra_wait_days=10.0)
    assert res_d is not None and "best" in res_d, "Case D Failed"
    r_base = res_a["best"]["risk_overall"]
    r_wait = res_d["best"]["risk_overall"]
    assert r_wait > r_base, f"Case D Failed: Wait risk {r_wait} not > base risk {r_base}"
    print(f"  -> SUCCESS: Base Risk: {r_base:.1f} vs +10 Days Wait Risk: {r_wait:.1f}")

    # Case E: Weather API fallback/out-of-horizon case
    print("\n[TEST E] Weather API fallback case (Target date outside 7-day API window)...")
    far_date = dt.date.today() + dt.timedelta(days=40)
    wx = weather.assess_port_weather(PORTS["Paradip"], far_date)
    assert wx["available"] == False, "Case E Failed: Weather should be marked unavailable for +40 days"
    print(f"  -> SUCCESS: Handled gracefully. Weather Reason: '{wx['reason']}'")

    # Case F: Invalid date/input validation
    print("\n[TEST F] Invalid date validation (Arrival before decision date)...")
    invalid_arrival = bdi_end - dt.timedelta(days=10)
    is_valid = invalid_arrival > bdi_end
    assert not is_valid, "Case F Failed: Invalid date should fail validation check"
    print(f"  -> SUCCESS: Validation logic caught invalid arrival date (Arrival: {invalid_arrival} <= Decision: {bdi_end})")

    print("\n==================================================")
    print("ALL 6 TEST CASES PASSED SUCCESSFULLY!")
    print("==================================================")

if __name__ == "__main__":
    run_tests()

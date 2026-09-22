"""Regression tests for Sagar Drishti prototype upgrades."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sail import data, recommend, vessels, cost
from sail.exceptions import InvalidInputError, InsufficientHistoryError, UnknownPortError


def expect_error(fn, exc):
    try:
        fn()
    except exc:
        return True
    raise AssertionError(f"Expected {exc.__name__}")


def main():
    assert len(vessels.available_vessels()) >= 8, "Individual vessel master not loaded"
    assert vessels.get_vessel("SD-MOCK-PMX-001")["vessel_class"] == "Panamax"

    expect_error(lambda: recommend.analyse("Australia (Hay Point, QLD)", "Paradip", 0, "2026-12-01", "2026-09-11"), InvalidInputError)
    expect_error(lambda: recommend.analyse("Australia (Hay Point, QLD)", "Paradip", -50000, "2026-12-01", "2026-09-11"), InvalidInputError)
    assert recommend.analyse("Australia (Hay Point, QLD)", "Paradip", "75000", "01-12-2026", "2026-09-11")["status"] == "SUCCESS"
    expect_error(lambda: recommend.analyse("Australia (Hay Point, QLD)", "Paradip", 75000, "2026-12-01", "1995-01-01"), InsufficientHistoryError)
    expect_error(lambda: cost.bunker_price(100, -100), InvalidInputError)
    expect_error(lambda: cost.voyage_cost("SD-MOCK-PMX-001", "Australia (Hay Point, QLD)", "Paradip", 75000, 1, 10000, 100, 0, -10), InvalidInputError)

    res = recommend.analyse(
        "Australia (Hay Point, QLD)", "Paradip", 75000, "2026-12-01", "2026-09-11",
        specific_vessel="SD-MOCK-PMX-001", waypoints=["Dhamra", "Sagar_Sandheads"]
    )
    assert res["status"] == "SUCCESS"
    b = res["best"]
    assert b["vessel_id"] == "SD-MOCK-PMX-001"
    assert b["route"][-3:] == ["Dhamra", "Sagar_Sandheads", "Paradip"]
    assert len(b["cost_plan"]["legs"]) == 3

    future = data.synthetic_future_scenario("BDI", 30, "base")
    assert len(future) == 30 and future.attrs["data_status"] == "SYNTHETIC / SCENARIO"

    print("ALL UPGRADE TESTS PASSED")

if __name__ == "__main__":
    main()

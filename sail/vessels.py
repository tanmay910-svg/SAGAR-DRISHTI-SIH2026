"""Vessel-port compatibility: a constraint problem, not an ML problem (blueprint §9-13)."""
import math
from .config import VESSELS, VESSEL_PROFILES, PORTS
from .validation import normalize_cargo, normalize_port


def get_vessel(vessel_name):
    """Resolve either a vessel class or an individual vessel profile."""
    if vessel_name in VESSEL_PROFILES:
        return VESSEL_PROFILES[vessel_name]
    if vessel_name in VESSELS:
        v = dict(VESSELS[vessel_name])
        v.setdefault("vessel_id", vessel_name)
        v.setdefault("vessel_name", vessel_name)
        v.setdefault("vessel_class", vessel_name)
        v.setdefault("data_source", "Class master / indicative")
        return v
    raise KeyError(f"Unknown vessel or vessel class: {vessel_name}")


def available_vessels():
    return VESSEL_PROFILES if VESSEL_PROFILES else VESSELS


def part_laden_intake(v, port_draft):
    """Approximate max cargo if the ship must arrive at less than full draft.
    Linear draft-to-displacement approximation with lightship draft ~ 35% of design draft.
    ASSUMPTION: replace with the ship's actual TPC / hydrostatic table for real fixtures."""
    light = 0.35 * v["draft_m"]
    frac = (port_draft - light) / (v["draft_m"] - light)
    return max(0.0, min(1.0, frac)) * v["dwt"] * v["cargo_factor"]


def evaluate(vessel_name, port_name, cargo_mt):
    cargo_mt = normalize_cargo(cargo_mt)
    port_name = normalize_port(port_name)
    v, p = get_vessel(vessel_name), PORTS[port_name]
    full_capacity = v["dwt"] * v["cargo_factor"]
    reasons, hard_fail = [], False
    checks = {}
    checks["LOA"] = v["loa_m"] <= p["max_loa_m"]
    checks["Beam"] = v["beam_m"] <= p["max_beam_m"]
    checks["DWT"] = v["dwt"] <= p["max_dwt"]
    checks["Draft"] = v["draft_m"] <= p["max_draft_m"]
    if not checks["LOA"]:
        reasons.append(f"LOA {v['loa_m']} m exceeds {port_name} limit {p['max_loa_m']} m"); hard_fail = True
    if not checks["Beam"]:
        reasons.append(f"Beam {v['beam_m']} m exceeds limit {p['max_beam_m']} m"); hard_fail = True
    capacity = full_capacity
    if not checks["Draft"]:
        capacity = part_laden_intake(v, p["max_draft_m"])
        reasons.append(f"Design draft {v['draft_m']} m > port max {p['max_draft_m']} m: "
                       f"only ~{capacity:,.0f} MT if part-laden")
    if not checks["DWT"]:
        reasons.append(f"DWT {v['dwt']:,} above port max {p['max_dwt']:,}")
        hard_fail = True
    voyages = math.ceil(cargo_mt / capacity) if capacity > 0 else None
    checks["Cargo fit"] = voyages == 1
    if voyages and voyages > 1:
        reasons.append(f"Capacity ~{capacity:,.0f} MT per voyage: needs {voyages} voyages")
    utilisation = cargo_mt / (capacity * voyages) if voyages else 0
    if voyages == 1 and utilisation < 0.6:
        reasons.append(f"Only {utilisation:.0%} utilised: paying for unused space")
    status = "REJECT" if hard_fail or voyages is None else (
        "FEASIBLE" if voyages == 1 and checks["Draft"] else "CONDITIONAL")
    if status == "FEASIBLE" and not reasons:
        reasons.append("All port limits satisfied and cargo fits in one voyage")
    return dict(vessel=vessel_name, status=status, capacity_per_voyage=capacity, voyages=voyages,
                utilisation=utilisation, checks=checks, reasons=reasons, verified=v["verified"] and p["verified"])

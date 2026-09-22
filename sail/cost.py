"""Voyage cost engine with transparent weather-adjusted voyage impacts.

Weather is converted into estimated operational effects (speed reduction, extra
route distance, and port delay) and then into incremental fuel/hire cost. These
are heuristics, not marine-safety calculations, and are shown separately in the UI.
"""
from .config import VESSELS, PORTS, ROUTES, SETTINGS
from .vessels import get_vessel
from .validation import normalize_cargo, normalize_port, normalize_route, normalize_shocks
from .exceptions import InvalidInputError


def bunker_price(brent_usd_bbl, shock_pct=0.0):
    b = SETTINGS["bunker"]
    shock_pct = float(shock_pct)
    if shock_pct <= -100:
        raise InvalidInputError("Bunker shock must be greater than -100%.")
    return brent_usd_bbl * b["bbl_per_tonne"] * b["premium"] * (1 + shock_pct / 100)


def weather_impact(vessel_name, port_name, weather_detail, base_sea_days, base_wait_days,
                  brent, hire_usd_day, cargo_mt, voyages):
    """Estimate weather-driven incremental voyage cost.

    Uses only an available near-term API assessment. The mapping is deliberately
    conservative and transparent: wind/gust severity drives a bounded speed
    reduction and route contingency; precipitation contributes port-delay risk.
    For unavailable long-range forecasts, all weather cost impacts are zero and
    the separate seasonal risk score remains responsible for long-range weather.
    """
    if not weather_detail or not weather_detail.get("available"):
        return {"available": False, "reason": weather_detail.get("reason", "Weather forecast unavailable") if weather_detail else "Weather forecast unavailable",
                "speed_reduction_pct": 0.0, "extra_sea_days": 0.0, "route_deviation_pct": 0.0,
                "extra_route_nm": 0.0, "extra_wait_days": 0.0, "extra_fuel_usd": 0.0,
                "extra_time_cost_usd": 0.0, "extra_route_cost_usd": 0.0, "extra_port_delay_cost_usd": 0.0,
                "weather_impact_usd": 0.0, "weather_impact_usd_per_mt": 0.0}

    v = get_vessel(vessel_name)
    r = ROUTES.get(next((k for k in ROUTES if True), None), None)  # replaced below; only used for type context
    wind = float(weather_detail.get("wind_kmh", 0.0))
    gust = float(weather_detail.get("gust_kmh", 0.0))
    precip = float(weather_detail.get("precip_mm", 0.0))
    score = float(weather_detail.get("score", 0.0))

    # Bounded operational heuristic: no effect below moderate conditions.
    severity = max(0.0, min(1.0, (score - 30.0) / 70.0))
    wind_factor = max(0.0, min(1.0, (max(wind, gust * 0.75) - 25.0) / 55.0))
    speed_reduction_pct = min(15.0, 2.0 + 13.0 * max(severity, wind_factor) * 0.65) if score >= 30 else 0.0
    speed_reduction_pct = round(speed_reduction_pct, 2)
    extra_sea_days = base_sea_days * (speed_reduction_pct / max(1e-9, 100.0 - speed_reduction_pct))

    # Route contingency: severe weather can require a bounded extra-distance allowance.
    route_deviation_pct = round(min(5.0, 5.0 * severity), 2)
    extra_route_nm = 0.0  # distance is already embedded in base_sea_days; converted below via speed-equivalent time
    route_extra_days = base_sea_days * (route_deviation_pct / 100.0)

    # Port delay: precipitation + severe weather, capped to avoid exaggerated economics.
    precip_factor = min(1.0, precip / 30.0)
    extra_wait_days = round(min(1.5, 0.15 + 1.35 * max(severity, precip_factor) * 0.7) if score >= 30 else 0.0, 2)

    bp = bunker_price(brent)
    base_fuel_rate = v["sea_cons_t_day"]
    extra_fuel = base_fuel_rate * (extra_sea_days + route_extra_days) * bp * voyages
    extra_time_cost = hire_usd_day * (extra_sea_days + route_extra_days) * voyages
    extra_port_delay_cost = (hire_usd_day * extra_wait_days + v["port_cons_t_day"] * extra_wait_days * bp) * voyages
    # Route deviation cost is represented by its incremental sea-time/fuel above.
    extra_route_cost = 0.0
    total = extra_fuel + extra_time_cost + extra_port_delay_cost + extra_route_cost

    return {
        "available": True, "date": weather_detail.get("date"), "band": weather_detail.get("band"),
        "wind_kmh": wind, "gust_kmh": gust, "precip_mm": precip, "weather_score": score,
        "speed_reduction_pct": speed_reduction_pct, "extra_sea_days": round(extra_sea_days, 2),
        "route_deviation_pct": route_deviation_pct, "extra_route_nm": extra_route_nm,
        "extra_wait_days": extra_wait_days, "extra_fuel_usd": round(extra_fuel, 2),
        "extra_time_cost_usd": round(extra_time_cost, 2), "extra_route_cost_usd": round(extra_route_cost, 2),
        "extra_port_delay_cost_usd": round(extra_port_delay_cost, 2),
        "weather_impact_usd": round(total, 2),
        "weather_impact_usd_per_mt": round(total / max(cargo_mt, 1), 4),
        "method": "Near-term weather heuristic: wind/gust → speed; severity → route contingency; precipitation/severity → port delay."
    }


def voyage_cost(vessel_name, origin, port_name, cargo_mt, voyages, hire_usd_day, brent,
                bunker_shock_pct=0.0, extra_wait_days=0.0, weather_detail=None):
    origin = normalize_route(origin)
    port_name = normalize_port(port_name)
    cargo_mt = normalize_cargo(cargo_mt)
    if voyages is None or voyages <= 0:
        raise InvalidInputError("Voyages must be greater than 0.")
    bunker_shock_pct, extra_wait_days, _ = normalize_shocks(bunker_shock_pct, extra_wait_days, 0.0)
    v, p, r = get_vessel(vessel_name), PORTS[port_name], ROUTES[origin]
    per_voyage_cargo = cargo_mt / voyages
    laden_nm = r["distance_nm"] + SETTINGS["port_offset_nm"].get(port_name, 0)
    ballast_nm = r["ballast_nm"] * SETTINGS["ballast_charge_fraction"]
    sea_days = (laden_nm + ballast_nm) / (v["speed_kn"] * 24)
    port_days = per_voyage_cargo / r["load_rate_t_day"] + per_voyage_cargo / p["discharge_rate_t_day"]
    wait_days = p["current_wait_days"] + extra_wait_days
    total_days = sea_days + port_days + wait_days
    bp = bunker_price(brent, bunker_shock_pct)
    hire = hire_usd_day * (sea_days + port_days)
    wait_cost = hire_usd_day * wait_days + v["port_cons_t_day"] * wait_days * bp
    fuel = v["sea_cons_t_day"] * sea_days * bp + v["port_cons_t_day"] * port_days * bp
    port = p["port_charges_usd"]
    per_voyage = hire + wait_cost + fuel + port
    total = per_voyage * voyages

    wx = weather_impact(vessel_name, port_name, weather_detail, sea_days, wait_days, brent, hire_usd_day, cargo_mt, voyages)
    total_weather = total + wx["weather_impact_usd"]
    total_days_weather = total_days + wx["extra_sea_days"] + (wx["extra_wait_days"] if wx.get("available") else 0.0) + (sea_days * wx.get("route_deviation_pct", 0.0) / 100.0 if wx.get("available") else 0.0)

    return dict(sea_days=sea_days, port_days=port_days, wait_days=wait_days, voyage_days=total_days,
                weather_adjusted_voyage_days=round(total_days_weather, 2), bunker_usd_t=bp, hire_usd_day=hire_usd_day,
                hire_usd=hire * voyages, fuel_usd=fuel * voyages, wait_usd=wait_cost * voyages,
                port_usd=port * voyages, weather_impact_usd=wx["weather_impact_usd"],
                weather_impact_usd_per_mt=wx["weather_impact_usd_per_mt"], weather=wx,
                total_usd=total_weather, base_total_usd=total, usd_per_mt=total_weather / cargo_mt,
                base_usd_per_mt=total / cargo_mt)


def _haversine_nm(lat1, lon1, lat2, lon2):
    import math
    R_km = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dl/2)**2
    return (2 * R_km * math.asin(math.sqrt(a))) / 1.852


def _segment_distance_nm(origin, destination):
    """Use the existing calibrated source-to-Paradip route for the first leg,
    and geodesic distance for subsequent Indian-port legs. Geodesic distance is
    a transparent prototype estimate, not a navigational route."""
    if destination not in PORTS:
        raise InvalidInputError(f"Unknown route waypoint: {destination}")
    if origin in ROUTES:
        return ROUTES[origin]["distance_nm"] + SETTINGS["port_offset_nm"].get(destination, 0)
    if origin in PORTS:
        a, b = PORTS[origin], PORTS[destination]
        return _haversine_nm(a["latitude"], a["longitude"], b["latitude"], b["longitude"])
    raise InvalidInputError(f"Unknown route node: {origin}")


def voyage_cost_multileg(vessel_name, origin, destination, waypoints, cargo_mt, voyages,
                         hire_usd_day, brent, bunker_shock_pct=0.0, extra_wait_days=0.0,
                         weather_by_port=None):
    """Cost an A→C→D→B itinerary. Intermediate ports are treated as operational
    calls with their configured waiting/port charges. This is a planning model,
    not a navigational or stowage/safety calculation."""
    origin = normalize_route(origin)
    destination = normalize_port(destination)
    cargo_mt = normalize_cargo(cargo_mt)
    bunker_shock_pct, extra_wait_days, _ = normalize_shocks(bunker_shock_pct, extra_wait_days, 0.0)
    if voyages is None or voyages <= 0:
        raise InvalidInputError("Voyages must be greater than 0.")
    nodes = [destination] if not waypoints else [normalize_port(x) for x in waypoints] + [destination]
    # remove accidental duplicate consecutive destinations
    clean_nodes = []
    for n in nodes:
        if not clean_nodes or n != clean_nodes[-1]: clean_nodes.append(n)
    nodes = clean_nodes
    v = get_vessel(vessel_name)
    bp = bunker_price(brent, bunker_shock_pct)
    current = origin
    legs = []
    total = 0.0
    total_days = 0.0
    total_hire = total_fuel = total_wait = total_port = 0.0
    for i, node in enumerate(nodes, 1):
        dist = _segment_distance_nm(current, node)
        ballast_nm = dist * SETTINGS["ballast_charge_fraction"]
        sea_days = (dist + ballast_nm) / (v["speed_kn"] * 24)
        p = PORTS[node]
        per_cargo = cargo_mt / voyages
        # Intermediate calls are represented as handling/waiting events. Final
        # destination uses the normal discharge rate; waypoints use a bounded
        # handling estimate so the route remains economically comparable.
        handling_days = per_cargo / max(1.0, p["discharge_rate_t_day"])
        wait_days = max(0.0, p["current_wait_days"] + extra_wait_days)
        hire = hire_usd_day * (sea_days + handling_days)
        fuel = v["sea_cons_t_day"] * sea_days * bp + v["port_cons_t_day"] * handling_days * bp
        wait = hire_usd_day * wait_days + v["port_cons_t_day"] * wait_days * bp
        port_charge = p["port_charges_usd"]
        leg_base = (hire + fuel + wait + port_charge) * voyages
        wx = None
        if weather_by_port and node in weather_by_port:
            wx = weather_impact(vessel_name, node, weather_by_port[node], sea_days, wait_days,
                                brent, hire_usd_day, cargo_mt, voyages)
            wx_cost = wx.get("weather_impact_usd", 0.0)
            wx_days = wx.get("extra_sea_days", 0.0) + wx.get("extra_wait_days", 0.0)
        else:
            wx_cost, wx_days = 0.0, 0.0
        leg_total = leg_base + wx_cost
        total_hire += hire * voyages
        total_fuel += fuel * voyages
        total_wait += wait * voyages
        total_port += port_charge * voyages
        leg_days = sea_days + handling_days + wait_days + wx_days
        legs.append({"leg": i, "from": current, "to": node, "distance_nm": round(dist,1),
                     "sea_days": round(sea_days,2), "handling_days": round(handling_days,2),
                     "wait_days": round(wait_days,2), "cost_usd": round(leg_total,2),
                     "weather_impact_usd": round(wx_cost,2)})
        total += leg_total
        total_days += leg_days
        current = node
    return {"legs": legs, "route": [origin] + nodes, "total_usd": round(total,2),
            "usd_per_mt": round(total/cargo_mt,2), "voyage_days": round(total_days,2),
            "base_total_usd": round(total,2), "weather_impact_usd": round(sum(x["weather_impact_usd"] for x in legs),2),
            "hire_usd": round(total_hire,2), "fuel_usd": round(total_fuel,2),
            "wait_usd": round(total_wait,2), "port_usd": round(total_port,2),
            "sea_days": round(sum(x["sea_days"] for x in legs),2),
            "port_days": round(sum(x["handling_days"] for x in legs),2),
            "wait_days": round(sum(x["wait_days"] for x in legs),2),
            "bunker_usd_t": bp, "hire_usd_day": hire_usd_day}

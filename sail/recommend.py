"""Optimisation + decision layer (blueprint §20-22). Enumerates vessel classes, applies hard
constraints, costs each feasible option at today's hire and at the forecast hire in the
recommended window, scores them with configurable weights and explains the result."""
import datetime as dt
from functools import lru_cache
from .config import VESSELS, VESSEL_PROFILES, PORTS, SETTINGS
from .validation import normalize_cargo, normalize_port, normalize_route, normalize_shocks, parse_date_safe
from .exceptions import InvalidInputError, InsufficientHistoryError
from . import data, forecast, vessels, cost, timing, risk, weather


@lru_cache(maxsize=8)
def _market(index_name):
    return data.market_frame(index_name)


@lru_cache(maxsize=8)
def _models(index_name):
    return forecast.load_models(index_name)


def hire_from_index(level, vcfg, kind):
    return level if kind == "tce" else level * vcfg["tce_per_index_point"]


def analyse(origin, port, cargo_mt, required_arrival, as_of=None,
            bunker_shock_pct=0.0, extra_wait_days=0.0, freight_shock_pct=0.0,
            specific_vessel=None, waypoints=None):
    # Boundary validation and normalization
    cargo_mt = normalize_cargo(cargo_mt)
    port = normalize_port(port)
    origin = normalize_route(origin)
    bunker_shock_pct, extra_wait_days, freight_shock_pct = normalize_shocks(
        bunker_shock_pct, extra_wait_days, freight_shock_pct
    )
    required_arrival = parse_date_safe(required_arrival, "Required arrival date")
    
    if as_of is not None:
        as_of_dt = parse_date_safe(as_of, "Analysis date")
        as_of_str = str(as_of_dt)
    else:
        as_of_dt = None
        as_of_str = None

    # Arrival date must be chronologically after the decision date
    ref_date = as_of_dt if as_of_dt is not None else dt.date.today()
    if required_arrival <= ref_date:
        raise InvalidInputError("Required arrival date must be chronologically after the analysis date.")

    # Benchmark date for freshness assessment
    bdi_df = _market("BDI")
    benchmark_date = as_of_dt if as_of_dt is not None else bdi_df.index[-1].date()

    options, warnings = [], []
    vessel_catalog = VESSEL_PROFILES if VESSEL_PROFILES else VESSELS
    if specific_vessel:
        if specific_vessel not in vessel_catalog:
            raise InvalidInputError(f"Unknown specific vessel: {specific_vessel}")
        vessel_items = [(specific_vessel, vessel_catalog[specific_vessel])]
    else:
        vessel_items = list(vessel_catalog.items())

    # Establish a common market cut-off so vessel classes are not compared using
    # different historical end dates. The earliest available index date becomes
    # the common comparison date when the requested as-of date is later.
    available_latest = []
    for _, cfg in vessel_items:
        idx0, _, _ = data.available_index_for(cfg)
        try:
            available_latest.append(_market(idx0).index[-1].date())
        except Exception:
            pass
    common_latest = min(available_latest) if available_latest else benchmark_date
    common_cutoff = min(benchmark_date, common_latest)
    if benchmark_date > common_cutoff:
        warnings.append(f"Common market cut-off applied at {common_cutoff}: vessel classes are compared on a shared historical date to avoid data-horizon bias.")

    for name, vcfg in vessel_items:
        feas = vessels.evaluate(name, port, cargo_mt)
        if feas["status"] == "REJECT":
            options.append(dict(feasibility=feas, vessel=name)); continue
        idx, kind, proxy = data.available_index_for(vcfg)
        if proxy:
            w = f"{name}: class index {vcfg['freight_index']} not loaded; using BDI x {vcfg['tce_per_index_point']} $/day per point (assumption)."
            warnings.append(w)
        df = _market(idx)
        bundle, rep = _models(idx)
        forecast_cutoff = str(min(parse_date_safe(as_of_str, "Analysis date"), common_latest)) if as_of_str else str(common_latest)
        cur, as_of_used, path = forecast.predict_path(df, bundle, rep, forecast_cutoff)
        
        # Dataset freshness and horizon tracking
        latest_data_date = str(df.index[-1].date())
        data_age_days = max(0, (benchmark_date - df.index[-1].date()).days)
        if data_age_days > 30:
            data_status = "STALE"
            w_stale = (f"{name}: Class index {idx} has market observations only through {latest_data_date} "
                       f"({data_age_days} days lag vs analysis date {benchmark_date}). Forecast is based on latest available data.")
            if w_stale not in warnings:
                warnings.append(w_stale)
        else:
            data_status = "CURRENT"

        shock = 1 + freight_shock_pct / 100
        brent = float(df.loc[:as_of_used, "brent"].iloc[-1])
        usdinr = float(df.loc[:as_of_used, "usdinr"].iloc[-1])
        hire_now = hire_from_index(cur, vcfg, kind) * shock
        wx = weather.assess_port_weather(PORTS[port], required_arrival)
        if waypoints:
            waypoint_names = [normalize_port(x) for x in waypoints if normalize_port(x) != port]
            c_now = cost.voyage_cost_multileg(name, origin, port, waypoint_names, cargo_mt, feas["voyages"],
                                              hire_now, brent, bunker_shock_pct, extra_wait_days)
        else:
            c_now = cost.voyage_cost(name, origin, port, cargo_mt, feas["voyages"], hire_now, brent,
                                     bunker_shock_pct, extra_wait_days, wx)
        latest_fix = required_arrival - dt.timedelta(days=round(c_now["voyage_days"]) + SETTINGS["timing"]["booking_lead_days"])
        tm = timing.recommend(cur, path, as_of_used, latest_fix)
        target_level = cur * (1 + tm["expected_change_pct"] / 100) if tm["action"] in ("WAIT", "CHARTER PARTIALLY") else cur
        if tm["action"] == "CHARTER PARTIALLY":
            target_level = (cur + target_level) / 2
        if waypoints:
            c_plan = cost.voyage_cost_multileg(name, origin, port, waypoint_names, cargo_mt, feas["voyages"],
                                               hire_from_index(target_level, vcfg, kind) * shock, brent,
                                               bunker_shock_pct, extra_wait_days)
        else:
            c_plan = cost.voyage_cost(name, origin, port, cargo_mt, feas["voyages"],
                                      hire_from_index(target_level, vcfg, kind) * shock, brent,
                                      bunker_shock_pct, extra_wait_days, wx)
        rsk, overall, rband = risk.scores(df, port, required_arrival.month, as_of_used,
                                          extra_wait_days, bunker_shock_pct, required_arrival)
        slack = (latest_fix - dt.date.fromisoformat(as_of_used)).days
        options.append(dict(vessel=name, feasibility=feas, index=idx, index_kind=kind, proxy=proxy,
                            as_of=as_of_used, current_level=cur, path=path, brent=brent, usdinr=usdinr,
                            cost_now=c_now, cost_plan=c_plan, timing=tm, latest_fix=latest_fix,
                            risk=rsk, risk_overall=overall, risk_band=rband, slack_days=slack,
                            report=rep, weather=wx,
                            vessel_id=vcfg.get("vessel_id", name), vessel_name=vcfg.get("vessel_name", name),
                            vessel_class=vcfg.get("vessel_class", name), data_source=vcfg.get("data_source", "Configured master"),
                            route=[origin] + (waypoint_names if waypoints else []) + [port],
                            latest_data_date=latest_data_date, data_age_days=data_age_days,
                            data_status=data_status))
    viable = [o for o in options if "cost_plan" in o]
    if not viable:
        return dict(options=options, best=None, status="ANALYSIS_COMPLETED_NO_FEASIBLE_VESSEL", warnings=warnings)
    best_cost = min(o["cost_plan"]["usd_per_mt"] for o in viable)
    w = SETTINGS["score_weights"]
    for o in viable:
        s = {"cost": 100 * best_cost / o["cost_plan"]["usd_per_mt"],
             "forecast": max(0, min(100, 50 - 2.5 * o["timing"]["expected_change_pct"]
                                    if o["timing"]["action"] != "CHARTER NOW" else 50 + 2.5 * o["timing"]["expected_change_pct"])),
             "port": 100 if o["feasibility"]["status"] == "FEASIBLE" else 60,
             "risk": 100 - o["risk_overall"],
             "schedule": max(0, min(100, 40 + o["slack_days"]))}
        o["scores"] = s
        o["overall_score"] = sum(s[k] * w[k] for k in w)
    viable.sort(key=lambda o: -o["overall_score"])
    best = viable[0]
    best["explanation"] = explain(best, viable)
    return dict(options=options, best=best, ranked=viable, status="SUCCESS", warnings=warnings)


def explain(b, ranked):
    e = []
    f = b["feasibility"]
    e.append(f"{f['voyages']} voyage(s) of ~{f['capacity_per_voyage']:,.0f} MT capacity; utilisation {f['utilisation']:.0%}.")
    e.append("Port limits (LOA, beam, DWT, draft) satisfied." if f["status"] == "FEASIBLE"
             else "Conditional: " + "; ".join(f["reasons"]))
    others = [o for o in ranked if o is not b]
    if others:
        nxt = min(others, key=lambda o: o["cost_plan"]["usd_per_mt"])
        diff = nxt["cost_plan"]["usd_per_mt"] - b["cost_plan"]["usd_per_mt"]
        e.append(f"Estimated total cost ${b['cost_plan']['usd_per_mt']:.2f}/MT vs {nxt['vessel']} ${nxt['cost_plan']['usd_per_mt']:.2f}/MT ({diff:+.2f}).")
    e.append(f"Timing: {b['timing']['action']} — {b['timing']['reason']}")
    e.append(f"Expected port wait {b['cost_now']['wait_days']:.1f} days; overall risk {b['risk_overall']:.0f}/100 ({b['risk_band']}).")
    return e

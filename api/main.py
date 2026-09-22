"""FastAPI service (blueprint §29, §32-33).  Run:  uvicorn api.main:app --reload"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from datetime import date
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sail import recommend, data, forecast
from sail.config import VESSELS, VESSEL_PROFILES, PORTS, ROUTES
from sail.validation import normalize_cargo, normalize_port, normalize_route, normalize_shocks
from sail.exceptions import SailError

app = FastAPI(title="SAIL Freight Intelligence & Chartering Advisor", version="0.1")


class ForecastReq(BaseModel):
    vessel_type: str = "Panamax"
    as_of: Optional[date] = None


class RecReq(BaseModel):
    origin: str = "Australia (Hay Point, QLD)"
    destination: str = "Paradip"
    quantity_mt: float = Field(75000, gt=0)
    required_arrival: date
    as_of: Optional[date] = None
    bunker_shock_pct: float = Field(0, gt=-100)
    extra_wait_days: float = Field(0, ge=0)
    freight_shock_pct: float = Field(0, gt=-100)
    specific_vessel: Optional[str] = None
    intermediate_ports: list[str] = Field(default_factory=list)


@app.get("/reference")
def reference():
    return {"vessels": VESSELS, "vessel_profiles": VESSEL_PROFILES, "ports": PORTS, "routes": ROUTES}


@app.post("/forecast")
def post_forecast(r: ForecastReq):
    if r.vessel_type not in VESSELS:
        raise HTTPException(400, "unknown vessel_type")
    idx, kind, proxy = data.available_index_for(VESSELS[r.vessel_type])
    df = recommend._market(idx)
    b, rep = recommend._models(idx)
    try:
        cur, as_of, path = forecast.predict_path(df, b, rep, str(r.as_of) if r.as_of else None)
    except (SailError, ValueError) as e:
        raise HTTPException(400, detail=str(e))
    return {"index": idx, "unit": "USD/day" if kind == "tce" else "index points", "proxy": proxy,
            "as_of": as_of, "current": cur, "interval": "80% conformal", "path": path.to_dict("records")}


@app.post("/recommendation")
def post_rec(r: RecReq):
    try:
        norm_origin = normalize_route(r.origin)
        norm_dest = normalize_port(r.destination)
    except (SailError, KeyError, ValueError):
        raise HTTPException(400, "unknown origin/destination; see /reference")

    ref_date = r.as_of if r.as_of is not None else date.today()
    if r.required_arrival <= ref_date:
        raise HTTPException(400, "Required arrival date must be chronologically after the analysis date.")

    if r.quantity_mt <= 0:
        raise HTTPException(400, "Cargo quantity must be greater than 0 MT.")
    if r.bunker_shock_pct <= -100:
        raise HTTPException(400, "Bunker shock must be greater than -100%.")
    if r.extra_wait_days < 0:
        raise HTTPException(400, "Additional waiting days cannot be negative.")

    try:
        res = recommend.analyse(norm_origin, norm_dest, r.quantity_mt, r.required_arrival,
                                str(r.as_of) if r.as_of else None, r.bunker_shock_pct,
                                r.extra_wait_days, r.freight_shock_pct, r.specific_vessel, r.intermediate_ports)
    except (SailError, ValueError) as e:
        raise HTTPException(400, detail=str(e))

    b = res["best"]
    if b is None:
        return {"recommended_vessel": None, "options": [o["feasibility"] for o in res["options"]]}
    return {"recommended_vessel": b["vessel"], "recommended_vessel_name": b.get("vessel_name", b["vessel"]), "vessel_class": b.get("vessel_class"), "charter_action": b["timing"]["action"],
            "charter_window": b["timing"]["window"], "latest_fixture_date": b["latest_fix"],
            "cost_usd_per_mt_now": round(b["cost_now"]["usd_per_mt"], 2),
            "cost_usd_per_mt_planned": round(b["cost_plan"]["usd_per_mt"], 2),
            "risk": b["risk_band"], "risk_score": round(b["risk_overall"]),
            "score": round(b["overall_score"], 1), "explanation": b["explanation"],
            "data_as_of": b["as_of"], "warnings": res["warnings"],
            "ranking": [{"vessel": o["vessel"], "vessel_name": o.get("vessel_name", o["vessel"]), "vessel_class": o.get("vessel_class"), "score": round(o["overall_score"], 1),
                         "usd_per_mt": round(o["cost_plan"]["usd_per_mt"], 2)} for o in res["ranked"]]}

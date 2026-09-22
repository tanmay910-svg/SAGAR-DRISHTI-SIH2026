"""Lightweight weather integration using the public Open-Meteo API.

The prototype uses weather as a near-term voyage-risk input. Forecasts are only
used inside the API's available horizon; longer-dated decisions fall back to the
existing seasonal weather rule rather than pretending a long-range forecast is
known.
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache
import requests

BASE_URL = "https://api.open-meteo.com/v1/forecast"


@lru_cache(maxsize=64)
def forecast_for_port(lat: float, lon: float, days: int = 7) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "forecast_days": min(max(int(days), 1), 7),
        "daily": "weather_code,precipitation_sum,wind_speed_10m_max,wind_gusts_10m_max",
        "timezone": "auto",
    }
    r = requests.get(BASE_URL, params=params, timeout=8)
    r.raise_for_status()
    return r.json()


def assess_port_weather(port_cfg: dict, target_date: dt.date | None = None) -> dict:
    """Return a transparent near-term weather assessment.

    Score is 0–100 (higher = more disruptive). If target_date is outside the
    next 7 days, return unavailable so the caller can use a seasonal rule.
    """
    if not port_cfg.get("latitude") or not port_cfg.get("longitude"):
        return {"available": False, "reason": "No port coordinates configured."}

    today = dt.date.today()
    target = target_date or today
    delta = (target - today).days
    if delta < 0 or delta > 6:
        return {
            "available": False,
            "reason": "Target date is outside the reliable 7-day API forecast horizon.",
        }

    try:
        payload = forecast_for_port(float(port_cfg["latitude"]), float(port_cfg["longitude"]))
        daily = payload.get("daily", {})
        dates = daily.get("time", [])
        if not dates:
            return {"available": False, "reason": "Weather API returned no daily forecast."}
        i = dates.index(target.isoformat()) if target.isoformat() in dates else 0
        wind = float((daily.get("wind_speed_10m_max") or [0])[i] or 0)
        gust = float((daily.get("wind_gusts_10m_max") or [0])[i] or 0)
        precip = float((daily.get("precipitation_sum") or [0])[i] or 0)
        code = int((daily.get("weather_code") or [0])[i] or 0)

        # Transparent heuristic; not a marine-operational safety forecast.
        score = min(100.0, 0.8 * wind + 0.5 * gust + min(25.0, precip * 1.2))
        band = "LOW" if score < 30 else "MODERATE" if score < 60 else "HIGH"
        return {
            "available": True,
            "date": target.isoformat(),
            "wind_kmh": wind,
            "gust_kmh": gust,
            "precip_mm": precip,
            "weather_code": code,
            "score": round(score, 1),
            "band": band,
            "source": "Open-Meteo forecast",
        }
    except Exception as exc:
        return {"available": False, "reason": f"Weather API unavailable: {type(exc).__name__}."}

"""Risk engine (blueprint §17). Each score is a PERCENTILE of the current reading against
history (0 = calmest ever, 100 = most extreme), so it is data-driven and explainable.
Dimensions without a real data feed are reported as unavailable, never invented."""
import datetime as dt
import numpy as np
from .config import SETTINGS, PORTS
from .validation import normalize_port, normalize_shocks, parse_date_safe
from .exceptions import ConfigurationError, InsufficientHistoryError
from . import weather


def _pct(series, value):
    s = series.dropna()
    return float((s < value).mean() * 100) if len(s) else np.nan


def band(score):
    for name, upper in SETTINGS["risk_bands"].items():
        if score <= upper:
            return name
    return "CRITICAL"


def scores(df, port_name, arrival_month, as_of=None, extra_wait_days=0.0, bunker_shock_pct=0.0, target_date=None):
    port_name = normalize_port(port_name)
    bunker_shock_pct, extra_wait_days, _ = normalize_shocks(bunker_shock_pct, extra_wait_days, 0.0)
    p = PORTS[port_name]
    if p.get("normal_wait_days", 0) <= 0:
        raise ConfigurationError(
            f"Port '{port_name}' has invalid baseline waiting days: {p.get('normal_wait_days')}. Baseline waiting days must be greater than 0."
        )
    if as_of is not None:
        as_of_dt = parse_date_safe(as_of)
        as_of = str(as_of_dt)
        if df.empty or as_of_dt < df.index[0].date():
            raise InsufficientHistoryError("Selected analysis date is earlier than the available historical data.")
    d = df if as_of is None else df.loc[:as_of]
    if d.empty:
        raise InsufficientHistoryError("Selected analysis date is earlier than the available historical data.")
    lf = np.log(d["freight"])
    vol = lf.diff().rolling(21).std()
    market = _pct(vol, vol.iloc[-1])
    b = np.log(d["brent"]).diff(21)
    fuel = _pct(b, b.iloc[-1] + np.log(1 + bunker_shock_pct / 100))
    ratio = (p["current_wait_days"] + extra_wait_days) / p["normal_wait_days"]
    port = float(np.clip(30 + (ratio - 1) * 50, 0, 100))
    seasonal_weather = 65.0 if arrival_month in SETTINGS["cyclone_months_bay_of_bengal"] else 20.0
    wx = weather.assess_port_weather(p, target_date)
    weather_score = wx.get("score") if wx.get("available") else seasonal_weather
    out = {"Market (freight volatility)": market, "Fuel (Brent momentum)": fuel,
           "Port (waiting vs normal)": port, "Weather": weather_score,
           "Supply / Demand": None}
    vals = [v for v in out.values() if v is not None]
    overall = float(np.mean(vals))
    out["Weather detail"] = wx
    return out, overall, band(overall)

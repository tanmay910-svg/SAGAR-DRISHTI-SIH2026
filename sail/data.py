"""Loaders for REAL market data. Nothing in here generates or fills in fake values.

Freight: Investing.com historical-data CSV exports (Date, Price, Open, High, Low, Vol., Change %).
  Put files in data/raw/ named  BDI_investing_export.csv  (required)
  and optionally BPI_/BCI_/BSI_/BHSI_investing_export.csv for class-specific indices,
  or <INDEX>_tce_usd_day.csv with columns date,value for real $/day TCE series.
Brent: datasets/oil-prices (EIA source).  USD/INR: datasets/exchange-rates (FRED H.10 source).
"""
import pandas as pd
from .config import DATA_DIR


def _read_investing(path):
    df = pd.read_csv(path, encoding="utf-8-sig", thousands=",")
    df.columns = [c.strip() for c in df.columns]
    df["date"] = pd.to_datetime(df["Date"], format="mixed", dayfirst=False)
    df["value"] = pd.to_numeric(df["Price"].astype(str).str.replace(",", ""), errors="coerce")
    return df[["date", "value"]].dropna().sort_values("date").drop_duplicates("date")


def load_freight_index(name="BDI"):
    """Returns (series, kind) where kind is 'index' or 'tce'."""
    tce = DATA_DIR / f"{name}_tce_usd_day.csv"
    if tce.exists():
        df = pd.read_csv(tce, parse_dates=["date"])
        return df.set_index("date")["value"].sort_index(), "tce"
    inv = DATA_DIR / f"{name}_investing_export.csv"
    if inv.exists():
        return _read_investing(inv).set_index("date")["value"], "index"
    return None, None


def load_brent():
    df = pd.read_csv(DATA_DIR / "brent_daily.csv", parse_dates=["Date"])
    return df.set_index("Date")["Price"].rename("brent").sort_index()


def load_usdinr():
    df = pd.read_csv(DATA_DIR / "usdinr_daily.csv", parse_dates=["Date"])
    return df.set_index("Date")["Exchange rate"].rename("usdinr").sort_index()


def market_frame(index_name="BDI"):
    """Daily frame on freight trading days. Exogenous series are forward-filled only
    from PAST observations (no look-ahead)."""
    fr, kind = load_freight_index(index_name)
    if fr is None:
        raise FileNotFoundError(f"No data file for {index_name} in {DATA_DIR}")
    df = fr.rename("freight").to_frame()
    for s in (load_brent(), load_usdinr()):
        df = pd.merge_asof(df, s.to_frame(), left_index=True, right_index=True, direction="backward")
    df.attrs["kind"] = kind
    df.attrs["index"] = index_name
    return df.dropna()


def available_index_for(vessel_cfg):
    """Use the class-specific index if the team has it, else fall back to BDI (flagged)."""
    name = vessel_cfg.get("freight_index", "BDI")
    s, kind = load_freight_index(name)
    if s is not None:
        return name, kind, False
    return "BDI", load_freight_index("BDI")[1], True



def synthetic_future_scenario(index_name="BDI", days=180, scenario="base"):
    """Create clearly labelled deterministic scenario data after the latest real observation.
    This is for prototype what-if/future visualization only and is never mixed into training."""
    import numpy as np
    real = market_frame(index_name)
    last_date = real.index[-1]
    last = float(real["freight"].iloc[-1])
    scenarios = {"base": (0.0005, 0.012), "bull": (0.0015, 0.016), "bear": (-0.0012, 0.018)}
    drift, vol = scenarios.get(str(scenario).lower(), scenarios["base"])
    idx = pd.bdate_range(last_date + pd.Timedelta(days=1), periods=int(days))
    rng = np.random.default_rng(26006 + len(index_name) + int(days))
    shocks = rng.normal(drift, vol, len(idx))
    freight = last * np.exp(np.cumsum(shocks))
    # Carry the latest real exogenous values; these are scenario assumptions, not observations.
    brent = float(real["brent"].iloc[-1])
    fx = float(real["usdinr"].iloc[-1])
    out = pd.DataFrame({"freight": freight, "brent": brent, "usdinr": fx}, index=idx)
    out["data_status"] = "SYNTHETIC / SCENARIO"
    out["scenario"] = str(scenario).lower()
    out.attrs["index"] = index_name
    out.attrs["data_status"] = "SYNTHETIC / SCENARIO"
    return out

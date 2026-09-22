import numpy as np
import pandas as pd

LAGS = [1, 5, 10, 21]
WINDOWS = [5, 10, 21, 63]


def build_features(df):
    """All features use information available at the close of day t only."""
    f = pd.DataFrame(index=df.index)
    lf = np.log(df["freight"])
    for l in LAGS:
        f[f"ret_{l}"] = lf - lf.shift(l)                     # momentum
    for w in WINDOWS:
        f[f"dev_ma_{w}"] = lf - lf.rolling(w).mean()         # distance from rolling mean
        f[f"vol_{w}"] = lf.diff().rolling(w).std()           # realised volatility
    f["zscore_252"] = (lf - lf.rolling(252).mean()) / lf.rolling(252).std()
    f["brent_ret_5"] = np.log(df["brent"]).diff(5)
    f["brent_ret_21"] = np.log(df["brent"]).diff(21)
    f["inr_ret_21"] = np.log(df["usdinr"]).diff(21)
    f["month_sin"] = np.sin(2 * np.pi * df.index.month / 12)
    f["month_cos"] = np.cos(2 * np.pi * df.index.month / 12)
    f["week_of_year"] = df.index.isocalendar().week.astype(int).values
    return f


def target(df, h):
    """Target: log change of freight h trading days ahead. Forecasting a return rather than a
    level keeps the problem stationary; the level is recovered as current * exp(pred)."""
    lf = np.log(df["freight"])
    return (lf.shift(-h) - lf).rename(f"y_{h}")

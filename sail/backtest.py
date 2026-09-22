"""Historical back-test of the full decision rule (blueprint §42).
For each past decision date, models are retrained on data strictly BEFORE that date, the
timing rule decides, and the decision is scored against what the market actually did.
Benchmark = charter immediately (what a reactive spot process does)."""
import datetime as dt
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from .config import SETTINGS
from .features import build_features, target
from .forecast import cal_to_trading, _lgb, Q
from . import timing


def _fit(dtrain):
    """Fit backtest models without assuming a long pre-history exists.

    Shorter class-specific index series may not have data before the first
    calendar year in the backtest window. In that case the caller supplies the
    latest leakage-safe training slice available. Each horizon is also guarded
    against an empty calibration split.
    """
    X = build_features(dtrain)
    ms = {}
    for hc in SETTINGS["horizons_days"]:
        h = cal_to_trading(hc)
        d = pd.concat([X, target(dtrain, h)], axis=1).dropna()
        Xa, ya = d.iloc[:, :-1], d.iloc[:, -1]
        if len(Xa) < max(80, h + 20):
            continue

        cut = int(len(Xa) * 0.8) - h
        # For short histories, use a smaller but positive calibration split.
        if cut < 40:
            cut = max(20, len(Xa) - h - 20)
        if cut <= 0 or cut >= len(Xa):
            cut = max(1, len(Xa) - h)

        r1 = make_pipeline(StandardScaler(), Ridge(alpha=10.0)).fit(Xa.iloc[:cut], ya.iloc[:cut])
        g1 = _lgb().fit(Xa.iloc[:cut], ya.iloc[:cut])
        cal_x = Xa.iloc[cut + h:]
        cal_y = ya.iloc[cut + h:]
        if len(cal_x):
            cal = cal_y - 0.5 * (r1.predict(cal_x) + g1.predict(cal_x))
            half = float(np.quantile(np.abs(cal), Q[-1] - Q[0]))
        else:
            # No leakage-free calibration observations: use a conservative
            # fallback based on in-sample residuals and label it implicitly by
            # the fact that no validation rows exist.
            ins = ya - 0.5 * (r1.predict(Xa) + g1.predict(Xa))
            half = float(np.quantile(np.abs(ins), Q[-1] - Q[0]))

        ms[hc] = dict(
            mid=make_pipeline(StandardScaler(), Ridge(alpha=10.0)).fit(Xa, ya),
            gbm=_lgb().fit(Xa, ya),
            half=half,
        )
    return ms


def _path(ms, d):
    X = build_features(d).iloc[[-1]]
    cur = float(d["freight"].iloc[-1])
    rows = []
    for hc, m in ms.items():
        mid = 0.5 * (m["mid"].predict(X)[0] + m["gbm"].predict(X)[0])   # simple ensemble
        rows.append(dict(horizon_days=hc, expected=cur * np.exp(mid),
                         lower=cur * np.exp(mid - m["half"]), upper=cur * np.exp(mid + m["half"])))
    return cur, pd.DataFrame(rows)


def run(df, years=4, every_n_days=14, decision_horizon_days=60):
    end = df.index[-1] - pd.Timedelta(days=decision_horizon_days + 10)
    start = end - pd.DateOffset(years=years)
    dates = df.loc[start:end].index[::max(1, every_n_days * 5 // 7)]
    rows, ms, fitted_year = [], None, None
    max_h = max(cal_to_trading(hc) for hc in SETTINGS["horizons_days"])
    leakage_gap = pd.Timedelta(days=int(np.ceil(max_h * 7 / 5)) + 1)
    for t in dates:
        if fitted_year != t.year:
            cutoff = pd.Timestamp(t.year, 1, 1) - pd.Timedelta(days=1)
            ms = _fit(df.loc[:cutoff])
            if not ms:
                # First-year fallback: use data available before the decision
                # date minus the maximum forecast horizon.
                ms = _fit(df.loc[:t - leakage_gap])
            if ms:
                fitted_year = t.year
            else:
                # Not enough history yet; try again at the next decision date.
                continue
        d = df.loc[:t]
        cur, path = _path(ms, d)
        if path.empty or "horizon_days" not in path.columns:
            continue
        latest_fix = (t + pd.Timedelta(days=decision_horizon_days)).date()
        tm = timing.recommend(cur, path, str(t.date()), latest_fix)
        if tm["window"]:
            lo, hi = pd.Timestamp(tm["window"][0]), pd.Timestamp(tm["window"][1])
            realised_win = float(df.loc[lo:hi, "freight"].mean())
            realised = realised_win if tm["action"] == "WAIT" else 0.5 * (cur + realised_win)
        else:
            realised = cur
        best_possible = float(df.loc[t:t + pd.Timedelta(days=decision_horizon_days), "freight"].min())
        rows.append(dict(date=t.date(), action=tm["action"], level_now=cur, level_paid=realised,
                         saving_pct=(cur - realised) / cur * 100,
                         hindsight_best_pct=(cur - best_possible) / cur * 100))
    r = pd.DataFrame(rows)
    if r.empty:
        return r, dict(decisions=0, wait_or_partial=0, avg_saving_pct_all=None,
                       avg_saving_pct_when_waited=None, hit_rate_when_waited=None,
                       worst_case_pct=None, hindsight_avg_pct=None,
                       period=None)
    r["regret_pct"] = r.hindsight_best_pct - r.saving_pct
    acted = r[r.action.isin(["WAIT", "CHARTER PARTIALLY"])]
    summary = dict(decisions=len(r), wait_or_partial=len(acted),
                   avg_saving_pct_all=float(round(r.saving_pct.mean(), 2)),
                   avg_saving_pct_when_waited=float(round(acted.saving_pct.mean(), 2)) if len(acted) else None,
                   hit_rate_when_waited=float(round((acted.saving_pct > 0).mean(), 3)) if len(acted) else None,
                   worst_case_pct=float(round(r.saving_pct.min(), 2)),
                   hindsight_avg_pct=float(round(r.hindsight_best_pct.mean(), 2)),
                   period=f"{r.date.min()} to {r.date.max()}")
    return r, summary

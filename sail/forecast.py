"""Progressive forecasting ladder (blueprint §7) with time-aware walk-forward validation (§8).

Level 1: last value (random walk), moving average
Level 2: Ridge regression, LightGBM (point + quantile models for the interval)
Model selection is by out-of-sample MAE, per horizon. The reported 'confidence' is the
EMPIRICAL coverage of the interval on held-out folds, not a made-up number.
"""
import json
import joblib
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .config import SETTINGS, MODEL_DIR
from .features import build_features, target
from .validation import parse_date_safe
from .exceptions import InsufficientHistoryError

Q = SETTINGS["interval_quantiles"]


def cal_to_trading(days):
    return max(1, int(round(days * 5 / 7)))


def _lgb(obj="regression", alpha=None):
    p = dict(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=40,
             subsample=0.8, subsample_freq=1, colsample_bytree=0.8, verbose=-1)
    if obj == "quantile":
        p.update(objective="quantile", alpha=alpha)
    return lgb.LGBMRegressor(**p)


def _metrics(actual_lvl, pred_lvl):
    e = actual_lvl - pred_lvl
    return dict(MAE=float(np.mean(np.abs(e))),
                RMSE=float(np.sqrt(np.mean(e ** 2))),
                MAPE=float(np.mean(np.abs(e) / actual_lvl) * 100))


def walk_forward(df, h, n_folds=5, test_size=250, min_train=1000):
    """Time-aware validation that adapts fold size to available history."""
    X = build_features(df)
    y = target(df, h)
    data = pd.concat([X, y], axis=1).dropna()
    Xa, ya = data[X.columns], data[y.name]
    cur = df["freight"].reindex(data.index)
    ma21 = df["freight"].rolling(21).mean().reindex(data.index)
    n = len(data)
    rows, cover, resid = [], [], {}
    effective_min_train = min(min_train, max(100, n // 2))
    effective_test_size = min(test_size, max(50, (n - effective_min_train) // 2))
    if n <= effective_min_train + h + 20:
        raise ValueError(f"Insufficient usable observations for {h}-trading-day validation: {n} available")
    max_folds = max(1, (n - effective_min_train - h) // max(1, effective_test_size))
    folds = min(n_folds, max_folds)
    starts = [n - effective_test_size * (k + 1) for k in range(folds)][::-1]
    for s in starts:
        if s < effective_min_train:
            continue
        tr_end = s - h            # gap of h days so training targets never overlap the test period
        Xtr, ytr = Xa.iloc[:tr_end], ya.iloc[:tr_end]
        Xte, yte = Xa.iloc[s:s + test_size], ya.iloc[s:s + test_size]
        c = cur.iloc[s:s + test_size]
        actual = c * np.exp(yte)
        preds = {
            "Baseline: last value": c,
            "Baseline: 21d moving avg": ma21.iloc[s:s + test_size],
        }
        ridge = make_pipeline(StandardScaler(), Ridge(alpha=10.0)).fit(Xtr, ytr)
        preds["Ridge regression"] = c * np.exp(ridge.predict(Xte))
        gbm = _lgb().fit(Xtr, ytr)
        preds["LightGBM"] = c * np.exp(gbm.predict(Xte))
        lo = _lgb("quantile", Q[0]).fit(Xtr, ytr).predict(Xte)
        hi = _lgb("quantile", Q[-1]).fit(Xtr, ytr).predict(Xte)
        cover.append(np.mean((yte.values >= lo) & (yte.values <= hi)))
        for name, p in preds.items():
            resid.setdefault(name, []).extend(np.abs(np.log(actual / p)).dropna().tolist())
            rows.append(dict(model=name, fold_start=str(data.index[s].date()), **_metrics(actual, p)))
    if not rows:
        raise ValueError(f"No valid walk-forward folds for {h}-trading-day horizon")
    res = pd.DataFrame(rows).groupby("model")[["MAE", "RMSE", "MAPE"]].mean().sort_values("MAE")
    # Split-conformal half-width (in log space) from out-of-sample residuals of the best model:
    # gives an interval whose coverage on held-out data matches the nominal level.
    nominal = Q[-1] - Q[0]
    half = float(np.quantile(resid[res.index[0]], nominal))
    return res, float(np.mean(cover)) if cover else float("nan"), half


def train_all(df, tag):
    """Fit final models on all data for each horizon; store validation results alongside."""
    out = {"index": df.attrs.get("index"), "kind": df.attrs.get("kind"),
           "data_end": str(df.index[-1].date()), "horizons": {}}
    X = build_features(df)
    models = {}
    for hc in SETTINGS["horizons_days"]:
        h = cal_to_trading(hc)
        res, coverage, half = walk_forward(df, h)
        best = res.index[0]
        y = target(df, h)
        d = pd.concat([X, y], axis=1).dropna()
        Xa, ya = d[X.columns], d[y.name]
        m = {"best": best,
             "ridge": make_pipeline(StandardScaler(), Ridge(alpha=10.0)).fit(Xa, ya),
             "lgb": _lgb().fit(Xa, ya),
             "lo": _lgb("quantile", Q[0]).fit(Xa, ya),
             "hi": _lgb("quantile", Q[-1]).fit(Xa, ya)}
        models[hc] = m
        imp = pd.Series(m["lgb"].feature_importances_, index=X.columns).sort_values(ascending=False)
        out["horizons"][hc] = {"trading_days": h, "best_model": best,
                               "raw_quantile_coverage": round(coverage, 3),
                               "interval_coverage": Q[-1] - Q[0],
                               "conformal_halfwidth_log": round(half, 4),
                               "validation": res.round(3).reset_index().to_dict("records"),
                               "top_features": imp.head(6).index.tolist()}
    MODEL_DIR.mkdir(exist_ok=True)
    joblib.dump({"models": models, "columns": list(X.columns)}, MODEL_DIR / f"{tag}.joblib")
    (MODEL_DIR / f"{tag}_report.json").write_text(json.dumps(out, indent=2))
    return out


def load_models(tag):
    b = joblib.load(MODEL_DIR / f"{tag}.joblib")
    rep = json.loads((MODEL_DIR / f"{tag}_report.json").read_text())
    return b, rep


def predict_path(df, bundle, report, as_of=None):
    """Forecast at each horizon using data up to as_of (inclusive)."""
    if as_of is not None:
        as_of_dt = parse_date_safe(as_of, "Analysis date")
        as_of_str = str(as_of_dt)
        if df.empty or as_of_dt < df.index[0].date():
            raise InsufficientHistoryError("Selected analysis date is earlier than the available historical data.")
        d = df.loc[:as_of_str]
    else:
        d = df

    if d.empty:
        raise InsufficientHistoryError("Selected analysis date is earlier than the available historical data.")
    if len(d) < 252:
        raise InsufficientHistoryError("Insufficient historical data for this analysis date. Please select a later date.")

    feat = build_features(d).iloc[[-1]]
    X = feat[bundle["columns"]]
    if X.isna().any().any():
        raise InsufficientHistoryError("Insufficient historical data for this analysis date. Please select a later date.")
    cur = float(d["freight"].iloc[-1])
    rows = []
    for hc, m in bundle["models"].items():
        best = m["best"]
        if best == "Baseline: last value":
            mid = cur
        elif best == "Baseline: 21d moving avg":
            mid = float(d["freight"].iloc[-21:].mean())
        elif best == "Ridge regression":
            mid = cur * float(np.exp(m["ridge"].predict(X)[0]))
        else:
            mid = cur * float(np.exp(m["lgb"].predict(X)[0]))
        half = report["horizons"][str(hc)]["conformal_halfwidth_log"]
        lo, hi = mid * float(np.exp(-half)), mid * float(np.exp(half))
        rows.append(dict(horizon_days=int(hc), expected=mid, lower=lo, upper=hi, model=best,
                         coverage=report["horizons"][str(hc)]["interval_coverage"]))
    return cur, str(d.index[-1].date()), pd.DataFrame(rows).sort_values("horizon_days")

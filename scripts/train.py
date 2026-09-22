"""Train forecasting models for every freight index present in data/raw and write reports."""
import sys, json
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from sail import data, forecast, backtest
from sail.config import ROOT

for name in ["BDI", "BPI", "BCI", "BSI", "BHSI"]:
    s, kind = data.load_freight_index(name)
    if s is None:
        continue
    df = data.market_frame(name)
    print(f"== {name} ({kind}) {df.index[0].date()} -> {df.index[-1].date()}, {len(df)} rows")
    rep = forecast.train_all(df, name)
    for h, r in rep["horizons"].items():
        print(f"  {h:>3}d best={r['best_model']:<26} raw quantile coverage={r['raw_quantile_coverage']:.2f} -> conformal +/-{r['conformal_halfwidth_log']:.3f} log")
        for v in r["validation"]:
            print(f"        {v['model']:<26} MAE={v['MAE']:8.1f} RMSE={v['RMSE']:8.1f} MAPE={v['MAPE']:5.1f}%")
    try:
        bt, summ = backtest.run(df)
        bt.to_csv(ROOT / "reports" / f"backtest_{name}.csv", index=False)
        (ROOT / "reports" / f"backtest_{name}_summary.json").write_text(json.dumps(summ, indent=2, default=str))
        print("  BACKTEST", summ)
    except Exception as exc:
        # A short history should not prevent other indices from training.
        summ = {"decisions": 0, "status": "skipped", "reason": str(exc)}
        (ROOT / "reports" / f"backtest_{name}_summary.json").write_text(json.dumps(summ, indent=2))
        print(f"  BACKTEST SKIPPED: {exc}")

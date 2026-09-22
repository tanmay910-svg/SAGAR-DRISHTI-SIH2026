"""Charter-timing recommendation (blueprint §15-16). Turns a forecast path into an action
and a window, restricted to dates that still leave time to reach the required arrival."""
import datetime as dt
from .config import SETTINGS


def recommend(current, path, as_of, latest_fix_date):
    t = SETTINGS["timing"]
    as_of = dt.date.fromisoformat(as_of)
    days_left = (latest_fix_date - as_of).days
    usable = path[path.horizon_days <= max(days_left, 0)]
    if days_left <= 7 or usable.empty:
        return dict(action="CHARTER NOW", window=None, reason=
                    f"Only {max(days_left,0)} days left before the latest fixture date "
                    f"({latest_fix_date}); no room to wait.", expected_change_pct=0.0)
    best = usable.loc[usable.expected.idxmin()]
    worst = usable.loc[usable.expected.idxmax()]
    fall = (best.expected / current - 1) * 100
    rise = (worst.expected / current - 1) * 100
    near = usable.iloc[0]
    if rise >= t["now_threshold_pct"] and near.expected > current and fall > -t["wait_threshold_pct"]:
        return dict(action="CHARTER NOW", window=None, expected_change_pct=rise,
                    reason=f"Model expects rates {rise:+.1f}% within {int(worst.horizon_days)} days and no meaningful dip before that.")
    if fall <= -t["wait_threshold_pct"]:
        # upside risk: if the 80% band's upper edge at the target date is well above today, hedge
        up = (best.upper / current - 1) * 100
        centre = as_of + dt.timedelta(days=int(best.horizon_days))
        window = (centre - dt.timedelta(days=4), min(centre + dt.timedelta(days=4), latest_fix_date))
        action = "WAIT" if up < t["now_threshold_pct"] * 2 else "CHARTER PARTIALLY"
        why = (f"Expected low of {fall:+.1f}% around {centre}; ")
        why += ("upper band stays close to today's level, so waiting is low-regret."
                if action == "WAIT" else
                f"but the 80% band allows up to {up:+.1f}%, so cover part of the requirement now and the rest in the window.")
        return dict(action=action, window=window, expected_change_pct=fall, reason=why,
                    expected_range=(best.lower, best.upper))
    return dict(action="MONITOR MARKET", window=None, expected_change_pct=fall,
                reason=f"Expected moves ({fall:+.1f}% / {rise:+.1f}%) are inside the noise threshold of ±{t['wait_threshold_pct']}%.")

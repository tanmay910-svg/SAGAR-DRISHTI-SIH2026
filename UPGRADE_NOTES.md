# Sagar Drishti Prototype Upgrade

## What was changed

1. Replaced the prototype BDI raw file with the merged real BDI series covering 2000-01-04 through 2026-09-11.
2. Added class-specific freight-index files using the prototype's expected Investing-style schema:
   - BPI: 2020-01-02 through 2025-03-31
   - BCI: 2020-01-02 through 2025-03-31
   - BSI: 2020-01-02 through 2025-12-02
   - BHSI: 2020-01-02 through 2025-03-31
3. Hardened walk-forward validation in `sail/forecast.py` so shorter histories do not crash because of the fixed 1000-row training assumption.
4. Hardened historical backtesting in `sail/backtest.py` so short histories can skip unavailable early years/horizons rather than terminating the entire run.
5. Hardened `scripts/train.py` so a backtest failure for one index does not stop model training for subsequent indices.

## Validation performed

- `python -m py_compile sail/forecast.py sail/backtest.py scripts/train.py` passed.
- `python scripts/train.py` successfully trained BDI, BPI, BCI, BSI and BHSI models.
- Model prediction smoke test successfully returned 7/14/30/60/90-day forecasts for all five indices.

## Important data limitation

BPI, BCI and BHSI currently end in March 2025; BSI ends in December 2025. BDI, Brent and USD/INR extend into 2026. No synthetic values were added to fill these gaps.

## Running locally

From the directory containing `requirements.txt`:

```powershell
python -m pip install -r requirements.txt
python scripts/train.py
python -m streamlit run app/streamlit_app.py
```

If `streamlit` is installed, `streamlit run app/streamlit_app.py` also works.


## Weather integration
- Added `sail/weather.py` using the public Open-Meteo forecast API (no API key required).
- Added latitude/longitude to configured discharge ports.
- Weather risk uses forecast wind, gusts and precipitation for near-term arrival dates within the API's 7-day horizon.
- For longer-dated or unavailable forecasts, the existing Bay of Bengal seasonal rule remains the fallback; the system does not invent long-range weather.
- Added `requests` to requirements.

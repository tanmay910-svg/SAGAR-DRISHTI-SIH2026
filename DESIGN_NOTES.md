# SAGAR DRISHTI Sarkari UI v2

This build keeps the existing SAIL Freight Intelligence decision engines and replaces the presentation layer with the approved high-fidelity government-portal style.

## Included visual assets
- `assets/sail_logo.svg` — SAIL identity mark for the header
- `assets/ministry_emblem.png` — government emblem crop from the approved design reference
- `assets/sagar_logo.png` — SAGAR branding crop from the approved design reference
- `assets/hero_ship.png` — maritime hero image from the approved design reference

## Main screens
1. Home / Voyage Analysis input
2. Voyage Analysis Results
3. Voyage Cost + Weather Impact details
4. Route Map + Port Information

## Existing intelligence retained
- Freight forecast models and walk-forward reports
- Vessel-port compatibility checks
- Voyage cost calculation
- Weather API integration through Open-Meteo
- Risk scoring
- Timing recommendation
- Back-test data
- 2026 market data already present in the package

## Run
From the folder containing `requirements.txt`:

```powershell
pip install -r requirements.txt
streamlit run app\streamlit_app.py
```

Do not `cd sail_chartering` if `app` is already directly under the current directory. Use `Get-ChildItem -Recurse -Filter streamlit_app.py` to locate the actual project root.

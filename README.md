# SAIL Freight Intelligence & Chartering Advisor (SIH 2026 · PS 26006) — working prototype

Converts freight-market forecasts into vessel-chartering decisions: which specific vessel (or vessel class), when to fix,
what the whole voyage will cost, how alternative multi-port routes compare, and what could go wrong.

## Prototype upgrade
- Individual vessel master: 8 clearly labelled synthetic/mock vessel profiles with DWT, LOA, beam, draft, speed, consumption, class and cargo compatibility fields.
- Multi-leg planning: supports A→B and A→C→D→B style intermediate-port itineraries with leg-wise distance, time, handling, waiting and cost.
- Future scenario sandbox: deterministic synthetic base/bull/bear data is available for future what-if visualization and is never used to train the historical models.
- Seven-day scenario simulator: freight path, demand pressure and congestion stress can be explored together for decision support.
- Charter Strategy module: compares spot, short-term multi-voyage and medium-term multi-voyage planning with voyage-cost context.
- Idle / alternative-employment planner: quantifies waiting exposure versus repositioning exposure without inventing an available fixture.
- Market context: historical freight, Brent fuel proxy and USD/INR are surfaced alongside explicitly synthetic commodity-demand and congestion stress variables.
- Common market cut-off: vessel comparisons use a shared historical cutoff when class-specific datasets end at different dates, preventing stale-data ranking bias.
- Input hardening: cargo, date, port, route and stress parameters are validated before model execution.
- USD/INR presentation: the UI uses the project reference FX rule (₹89.92/USD through 07-Jan-2026; ₹95.92/USD after that date) and displays the applied rate.
- Glossary: see `docs/PROJECT_GLOSSARY.md` for DWT, LOA, ETA, TCE, BDI, BCI, BPI, BSI, BHSI, Q88 and other terms.

## Run it
```bash
pip install -r requirements.txt
python scripts/fetch_data.py        # refresh Brent + USD/INR (optional)
python scripts/train.py             # walk-forward validation, final models, back-test  (~2-4 min)
streamlit run app/streamlit_app.py  # dashboard
uvicorn api.main:app --reload       # REST API  -> http://127.0.0.1:8000/docs
```

## Decision chain
requirement → vessel candidates → port constraint check (`sail/vessels.py`) → freight forecast
(`sail/forecast.py`) → voyage cost (`sail/cost.py`) → risk (`sail/risk.py`) → charter timing
(`sail/timing.py`) → weighted score + explanation (`sail/recommend.py`) → dashboard / API.

## SIH objective coverage
The prototype now exposes an additional **Charter Strategy & Market Intelligence** page under Streamlit's page navigation:
- **Market context:** freight history, fuel proxy and FX context.
- **Seven-day scenario simulation:** base / bull / bear synthetic freight paths, with demand and congestion stress.
- **Contract strategy:** spot vs short-term vs medium-term multiple-voyage scenarios.
- **Idle management:** waiting-cost exposure versus repositioning exposure.
- **Vessel-port gate:** LOA, beam, DWT and draft checks remain hard operational constraints.
- **Human-in-the-loop:** scenario outputs are decision support, not binding commercial quotes or autonomous charter authority.

Live commodity-price and real-time port-congestion feeds are not claimed where they are not connected. Synthetic scenario variables are isolated from historical ML training data and are clearly described as planning assumptions.

## Key design choices
- **Forecast target** is the log change of the freight index h days ahead, not the level (stationary).
- **Model ladder:** last value, 21-day MA, Ridge, LightGBM. Best model is chosen per horizon by walk-forward MAE.
- **Intervals** are split-conformal from out-of-sample residuals; raw quantile regression under-covered (52–66% vs 80%).
- **Cost engine** is time-charter-equivalent: hire × days + bunkers + waiting + port charges, so the index drives cost through hire.
- **Vessel choice** is constraint-based, including part-laden and multi-voyage options.
- **Back-test** retrains yearly on past data only and scores the timing rule against "fix immediately".

## Honest results on the current data (BDI 2000–2020)
- ML beats the random-walk baseline modestly: 60-day MAPE 24.7% vs 30.3%; 7-day 6.6% vs 7.5%.
- Timing-rule back-test 2015–2019: small average gain (~1–2%), ~50% hit rate, worst case about −36%.
  The timing signal alone is weak; most value comes from correct vessel/port/cost choice and risk awareness.
- Use these as the baseline to beat once class-specific indices and SAIL fixture data are loaded.

See `data/DATA_SOURCES.md` for what is real, what is a proxy, and what the team must obtain.

## SIH Prototype Demo Mode

SAGAR DRISHTI includes a self-contained **Enter Demo Mode** path for prototype evaluation. Demo Mode creates an in-memory evaluation session and does not require MongoDB credentials or a registered account. Real Login / Create Account remains available for authenticated deployments.

The **Vessel Comparison** tab exposes the individual vessel registry before a voyage analysis is run. The current registry contains 8 clearly labelled synthetic/scenario vessel records. These records are prototype fixtures and must not be interpreted as live vessel, ownership, position, or commercial-contract data.

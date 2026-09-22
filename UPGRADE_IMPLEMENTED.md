# Sagar Drishti — Prototype Upgrade Implemented

## Implemented
1. Individual vessel profiles: 8 synthetic/mock fixtures with vessel name, class, DWT, LOA, beam, draft, speed, consumption, build year and cargo types.
2. Ship-specific analysis: dashboard can nominate one individual vessel or evaluate the full individual-vessel catalog.
3. Multi-leg voyage planning: A→B and A→C→D→B itineraries with leg-wise distance, sailing, handling, waiting and cost.
4. Route map supports intermediate port calls.
5. Future scenario sandbox: deterministic base/bull/bear synthetic future series for controlled demonstrations; not used for historical model training.
6. Common market cutoff: when class-specific datasets have different end dates, comparisons use a shared cutoff to avoid stale-data ranking bias.
7. Input validation and prior robustness fixes retained.
8. USD/INR presentation retains the project reference rule: ₹89.92/USD through 07-Jan-2026 and ₹95.92/USD after that date.
9. Vessel comparison now exposes vessel ID/name/class and data basis instead of old data-freshness columns.
10. Maritime glossary added for DWT, LOA, ETA, TCE, BDI, BCI, BPI, BSI, BHSI, Q88, IMO and related terms.
11. Regression test suite added at `scripts/test_upgrade.py`.

## Verification
- Python compilation passed for modified core, API and Streamlit files.
- `scripts/test_upgrade.py` passed: all upgrade tests passed.
- Existing full robustness suite could not be executed in the build container because PyMongo is not installed there; `requirements.txt` now includes `pymongo>=4.8` and `python-dotenv>=1.0`.

## Data integrity
Synthetic/mock records are explicitly labelled `SYNTHETIC / SCENARIO`. Historical market data remains in `data/raw/` and is not silently replaced by synthetic observations.

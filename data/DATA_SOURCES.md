# Data Availability Matrix (blueprint §34)

| Data | In build? | Frequency | Source | Access | Action for the team |
|---|---|---|---|---|---|
| Baltic Dry Index | Yes, 2000-01 to 2020-01 | Daily | Investing.com export (via a public GitHub repo) | Free, login to download | Export full history to today from Investing.com "Baltic Dry Historical Data"; save as `BDI_investing_export.csv` |
| Panamax / Capesize / Supramax / Handysize indices (BPI, BCI, BSI, BHSI) | No | Daily | Baltic Exchange (licensed); some history on Investing.com / TradingEconomics | Paid / partial | Save as `BPI_investing_export.csv` etc.; or `BPI_tce_usd_day.csv` (date,value) for $/day TCE, which removes the index→hire assumption |
| Route $/MT freight (e.g. Queensland–East India) | No | Weekly | Baltic Exchange route assessments, Clarksons, brokers, **SAIL's own past fixtures** | Paid / request from SAIL | Best source: ask SAIL (problem-statement owner) for anonymised historical fixtures — use them to calibrate the cost engine |
| Brent crude | Yes, to 2026-09 | Daily | EIA via github.com/datasets/oil-prices | Free | `scripts/fetch_data.py` |
| Bunker (VLSFO) | Proxy from Brent | Daily | Ship & Bunker, port bunker quotes | Partly paid | Replace proxy with a real Singapore/Fujairah VLSFO series |
| USD/INR | Yes, to 2026-09 | Daily | FRED H.10 via github.com/datasets/exchange-rates | Free | `scripts/fetch_data.py` |
| Coking/thermal coal prices | No | Monthly | World Bank Commodity "Pink Sheet" | Free | Add as feature (monthly, forward-filled) |
| Port limits | Indicative | Static | Port authority / terminal handbooks | Free | Verify every value, set `verified: true` |
| Port congestion / turnaround | Manual input | Monthly | Indian Ports Association statistics, Ministry of Ports annual reports, AIS providers | Free (aggregate) / paid (AIS) | Populate `normal_wait_days` from IPA average pre-berthing detention |
| Vessel specs | Typical class values | Static | Class registers, Q88, broker class descriptions | Free-ish | Use nominated ship's particulars per fixture |
| Sea distances | Indicative | Static | sea-distances.org, Netpas | Free / paid | Verify route table |

Nothing in this project generates synthetic market data. If a series is missing, the system says so.

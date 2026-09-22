"""Refresh the freely available series. Freight indices must be exported manually (see DATA_SOURCES.md)."""
import pathlib, urllib.request, csv, io
RAW = pathlib.Path(__file__).resolve().parent.parent / "data" / "raw"
urllib.request.urlretrieve("https://raw.githubusercontent.com/datasets/oil-prices/main/data/brent-daily.csv", RAW / "brent_daily.csv")
txt = urllib.request.urlopen("https://raw.githubusercontent.com/datasets/exchange-rates/main/data/daily.csv").read().decode()
lines = txt.splitlines()
(RAW / "usdinr_daily.csv").write_text("\n".join([lines[0]] + [l for l in lines[1:] if ",India," in l]))
print("Brent and USD/INR refreshed. Now drop the latest BDI/BPI/BCI/BSI exports into data/raw and run scripts/train.py")

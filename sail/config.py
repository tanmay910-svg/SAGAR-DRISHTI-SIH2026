from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent

def load(name):
    with open(ROOT / "config" / f"{name}.yaml") as f:
        return yaml.safe_load(f)

SETTINGS = load("settings")
VESSELS = load("vessels")
VESSEL_PROFILES = load("vessel_profiles")
PORTS = load("ports")
ROUTES = load("routes")
DATA_DIR = ROOT / SETTINGS["data_dir"]
MODEL_DIR = ROOT / "models"

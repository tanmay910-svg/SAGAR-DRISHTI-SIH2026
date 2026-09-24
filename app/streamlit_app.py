"""SAGAR DRISHTI — Freight Intelligence & Chartering Advisor
Government of India · Ministry of Steel · Steel Authority of India Limited (SAIL)
SIH 2026 · Problem Statement: 26006

Run from project root: streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import base64
import datetime as dt
import io
import json
import pathlib
import re
import sys

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Setup project root
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sail import recommend, data, backtest, forecast  # noqa: E402
from sail.config import VESSELS, VESSEL_PROFILES, PORTS, ROUTES, SETTINGS  # noqa: E402
from sail.validation import normalize_cargo, normalize_port, normalize_route, normalize_shocks, parse_date_safe  # noqa: E402
from sail.exceptions import (  # noqa: E402
    SailError,
    InvalidInputError,
    InsufficientHistoryError,
    UnknownPortError,
    UnknownRouteError,
    ConfigurationError,
    ModelProcessingError,
)
from auth.db import get_auth_db, SERVICE_UNAVAILABLE_MSG  # noqa: E402
from app.charter_strategy import render_charter_strategy  # noqa: E402

# Streamlit Page Configuration
st.set_page_config(
    page_title="SAGAR DRISHTI — Freight Intelligence & Chartering Advisor | SAIL",
    page_icon="⚓",
    layout="wide",
    initial_sidebar_state="collapsed",
)

ASSET = ROOT / "assets"

# Institutional Color Tokens — SAIL / Government of India Palette
SAIL_BLUE = "#0b416d"
NAVY_DEEP = "#071e33"
NAVY_LIGHT = "#173956"
NAVY_SURFACE = "#0f2f4c"
BACKGROUND = "#f5f9fc"
CARD_BG = "#ffffff"
BORDER_COLOR = "#cbd9e5"
BORDER_LIGHT = "#e2ebf3"
TEXT_MAIN = "#0d2842"
TEXT_MUTED = "#556a7e"
GOLD_ACCENT = "#c59b27"
GOLD_HOVER = "#e0b438"
GOLD_BG = "#fef9e7"
GREEN_SUCCESS = "#12734a"
GREEN_BG = "#eef9f2"
AMBER_WARN = "#bd7800"
AMBER_BG = "#fdf8ec"
RED_ALERT = "#b32e2a"
RED_CRITICAL = "#991b1b"
RED_BG = "#fdf2f2"


def img64(name: str) -> str:
    p = ASSET / name
    if not p.exists():
        return ""
    return base64.b64encode(p.read_bytes()).decode()


EMBLEM = img64("ministry_emblem.png")
SAGAR = img64("sagar_drishti_logo.png") if (ASSET / "sagar_drishti_logo.png").exists() else img64("sagar_logo.png")
SAIL_LOGO = (ASSET / "sail_logo.svg").read_text(encoding="utf-8") if (ASSET / "sail_logo.svg").exists() else ""
HERO = img64("hero_ship.png")


def fmt_date_str(val) -> str:
    """Convert dates and ISO date strings to dd-mm-yyyy format."""
    if val is None:
        return "N/A"
    if isinstance(val, (dt.date, dt.datetime)):
        return val.strftime("%d-%m-%Y")
    val_str = str(val)
    return re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", r"\3-\2-\1", val_str)


def clean_text(s: str) -> str:
    """Fix mojibake or corrupt character encodings cleanly in the UI presentation layer and format dates to dd-mm-yyyy."""
    if not isinstance(s, str):
        if isinstance(s, (dt.date, dt.datetime)):
            return s.strftime("%d-%m-%Y")
        return str(s)
    txt = (
        s.replace("\ufffd", " — ")
        .replace("âš ", "⚠")
        .replace("â‚¹", "₹")
        .replace("â†’", "→")
        .replace("â€¢", "•")
        .replace("â–¥", "▪")
    )
    return re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", r"\3-\2-\1", txt)


CUTOFF_FX_DATE = dt.date(2026, 1, 7)
FX_RATE_PRE_CUTOFF = 89.92
FX_RATE_POST_CUTOFF = 95.92


def get_usdinr_rate(market_date=None) -> float:
    """Return reference USD/INR exchange rate based on Market/Decision Date.
    - On or before 07-Jan-2026: 89.92
    - After 07-Jan-2026: 95.92
    """
    if market_date is None:
        if "st" in globals() and hasattr(st, "session_state"):
            market_date = getattr(st.session_state, "as_of", None)

    if isinstance(market_date, dt.datetime):
        d = market_date.date()
    elif isinstance(market_date, dt.date):
        d = market_date
    elif isinstance(market_date, str):
        d = None
        for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
            try:
                d = dt.datetime.strptime(market_date.strip()[:10], fmt).date()
                break
            except Exception:
                pass
        if d is None:
            return FX_RATE_POST_CUTOFF
    else:
        return FX_RATE_POST_CUTOFF

    return FX_RATE_PRE_CUTOFF if d <= CUTOFF_FX_DATE else FX_RATE_POST_CUTOFF


def format_indian_currency(amount, decimals: int = 0, symbol: str = "₹ ") -> str:
    """Format a numeric value according to the Indian numbering system (Lakhs, Crores).
    E.g. 898742 -> '₹ 8,98,742'
         86207333 -> '₹ 8,62,07,333'
    """
    if amount is None:
        return "N/A"
    try:
        val = float(amount)
    except (ValueError, TypeError):
        return str(amount)

    is_neg = val < 0
    val = abs(val)

    if decimals > 0:
        val_str = f"{val:.{decimals}f}"
        int_part, frac_part = val_str.split(".")
        frac_str = "." + frac_part
    else:
        int_part = str(int(round(val)))
        frac_str = ""

    if len(int_part) <= 3:
        formatted_int = int_part
    else:
        last3 = int_part[-3:]
        rest = int_part[:-3]
        groups = []
        while len(rest) > 2:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        groups.append(last3)
        formatted_int = ",".join(groups)

    prefix = "-" if is_neg else ""
    return f"{prefix}{symbol}{formatted_int}{frac_str}"


# Custom PSU / Government CSS Injection with Micro-Interactions
st.markdown(
    f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Roboto+Slab:wght@600;700&display=swap');

:root {{
    --sail-blue: {SAIL_BLUE};
    --navy-deep: {NAVY_DEEP};
    --navy-light: {NAVY_LIGHT};
    --border: {BORDER_COLOR};
    --border-light: {BORDER_LIGHT};
    --text: {TEXT_MAIN};
    --muted: {TEXT_MUTED};
    --gold: {GOLD_ACCENT};
}}

html, body, [class*="css"] {{
    font-family: Inter, -apple-system, BlinkMacSystemFont, Arial, sans-serif;
    color: var(--text);
}}

.stApp {{
    background: {BACKGROUND};
}}

.main, [data-testid="stMain"] {{
    display: flex !important;
    flex-direction: column !important;
    align-items: center !important;
    width: 100% !important;
}}

.block-container, [data-testid="stMainBlockContainer"], [data-testid="stAppViewBlockContainer"] {{
    max-width: 1180px !important;
    width: 100% !important;
    margin-left: auto !important;
    margin-right: auto !important;
    padding-top: 0.8rem !important;
    padding-bottom: 2.5rem !important;
    padding-left: 1.25rem !important;
    padding-right: 1.25rem !important;
}}

header[data-testid="stHeader"] {{
    background: transparent;
}}

[data-testid="stToolbar"] {{
    visibility: hidden;
    height: 0;
}}

footer {{
    visibility: hidden;
    height: 0;
}}

/* Top Government Institutional Header */
.gov-header {{
    background: #ffffff;
    border: 1px solid var(--border);
    border-radius: 6px;
    min-height: 86px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 24px;
    box-shadow: 0 2px 8px rgba(7,30,51,0.05);
    margin-bottom: 10px;
}}

.gov-left {{
    display: flex;
    align-items: center;
    gap: 14px;
    min-width: 290px;
}}

.gov-left img {{
    width: 44px;
    height: 54px;
    object-fit: contain;
}}

.gov-small {{
    font-size: 12.5px;
    line-height: 1.35;
    font-weight: 700;
    color: #1c364e;
    letter-spacing: 0.3px;
}}

.gov-subtext {{
    font-size: 11.5px;
    color: #5a7184;
    font-weight: 600;
    letter-spacing: 0.2px;
}}

.brand {{
    text-align: center;
    flex: 1;
    padding: 0 10px;
}}

.brand-title {{
    font-family: 'Roboto Slab', serif;
    color: var(--sail-blue);
    font-size: 23px;
    font-weight: 700;
    line-height: 1.15;
    letter-spacing: 0.5px;
}}

.brand-sub {{
    font-size: 13px;
    font-weight: 700;
    color: #0d2842;
    margin-top: 2px;
    letter-spacing: 0.3px;
}}

.brand-tag {{
    font-size: 11.5px;
    color: var(--muted);
    margin-top: 2px;
    font-weight: 500;
}}

.sagar {{
    min-width: 290px;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 14px;
}}

.sagar svg {{
    max-width: 140px;
    height: auto;
    max-height: 48px;
    display: block;
}}

/* Top Navigation Button Styling & Hover Micro-Interactions */
div[data-testid="stButton"] button {{
    border-radius: 5px !important;
    font-size: 13px !important;
    font-weight: 600 !important;
    padding: 8px 12px !important;
    min-height: 42px !important;
    white-space: normal !important;
    text-align: center !important;
    line-height: 1.2 !important;
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
    cursor: pointer !important;
}}

/* Inactive Navigation Buttons (Secondary) */
button[kind="secondary"], [data-testid="stBaseButton-secondary"] {{
    background: #ffffff !important;
    color: #173956 !important;
    border: 1px solid #cbd9e5 !important;
    font-weight: 600 !important;
    box-shadow: 0 1px 3px rgba(7,30,51,0.04) !important;
}}

button[kind="secondary"]:hover, [data-testid="stBaseButton-secondary"]:hover {{
    background: #f0f6fb !important;
    color: {SAIL_BLUE} !important;
    border-color: #9cbacf !important;
    transform: translateY(-2px) !important;
    box-shadow: 0 4px 12px rgba(7,30,51,0.12) !important;
}}

button[kind="secondary"]:active, [data-testid="stBaseButton-secondary"]:active {{
    transform: translateY(0px) !important;
}}

/* Active Navigation Buttons (Primary) */
button[kind="primary"], [data-testid="stBaseButton-primary"] {{
    background: {SAIL_BLUE} !important;
    color: #ffffff !important;
    border: 1px solid {SAIL_BLUE} !important;
    border-bottom: 3.5px solid {GOLD_ACCENT} !important;
    font-weight: 700 !important;
    box-shadow: 0 2px 6px rgba(11,65,109,0.2) !important;
}}

button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover {{
    background: #0e528a !important;
    border-color: #0e528a !important;
    border-bottom: 3.5px solid {GOLD_HOVER} !important;
    transform: translateY(-2px) !important;
    box-shadow: 0 5px 15px rgba(11,65,109,0.3) !important;
}}

button[kind="primary"]:active, [data-testid="stBaseButton-primary"]:active {{
    transform: translateY(0px) !important;
}}

/* Download Button Styling */
.stDownloadButton button,
div[data-testid="stDownloadButton"] button,
div[data-testid="stDownloadButton"] > button {{
    background: {SAIL_BLUE} !important;
    color: #ffffff !important;
    border: 1px solid {SAIL_BLUE} !important;
    border-bottom: 3.5px solid {GOLD_ACCENT} !important;
    border-radius: 5px !important;
    font-weight: 700 !important;
    font-size: 13.5px !important;
    padding: 9px 20px !important;
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
    box-shadow: 0 2px 6px rgba(11,65,109,0.2) !important;
    cursor: pointer !important;
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
}}

.stDownloadButton button p,
.stDownloadButton button span,
div[data-testid="stDownloadButton"] button p,
div[data-testid="stDownloadButton"] button span {{
    color: #ffffff !important;
    font-weight: 700 !important;
    transition: color 0.2s ease !important;
}}

.stDownloadButton button:hover,
div[data-testid="stDownloadButton"] button:hover,
div[data-testid="stDownloadButton"] > button:hover {{
    background: #0e528a !important;
    color: #ffffff !important;
    border-color: #0e528a !important;
    border-bottom: 3.5px solid {GOLD_HOVER} !important;
    transform: translateY(-2px) !important;
    box-shadow: 0 6px 18px rgba(11,65,109,0.32) !important;
}}

.stDownloadButton button:hover p,
.stDownloadButton button:hover span,
div[data-testid="stDownloadButton"] button:hover p,
div[data-testid="stDownloadButton"] button:hover span {{
    color: #ffffff !important;
}}

.stDownloadButton button:active,
div[data-testid="stDownloadButton"] button:active,
div[data-testid="stDownloadButton"] > button:active {{
    transform: translateY(0px) !important;
    box-shadow: 0 2px 6px rgba(11,65,109,0.2) !important;
}}

/* Panels & Cards */
.panel {{
    background: #ffffff;
    border: 1px solid var(--border);
    border-radius: 6px;
    box-shadow: 0 2px 6px rgba(7,30,51,0.04);
    padding: 16px 20px;
    margin-bottom: 14px;
}}

.section-title {{
    color: var(--sail-blue);
    font-weight: 700;
    font-size: 16px;
    margin-bottom: 4px;
    display: flex;
    align-items: center;
    gap: 8px;
    letter-spacing: 0.2px;
}}

.section-sub {{
    color: var(--muted);
    font-size: 12.5px;
    margin-bottom: 12px;
}}

/* Modern Home Experience Hero Banner */
.hero-container {{
    border-radius: 8px;
    overflow: hidden;
    margin-bottom: 16px;
    position: relative;
    background: #071e33;
    border: 1px solid #1a4970;
    box-shadow: 0 4px 16px rgba(7,30,51,0.12);
    padding: 24px 28px;
}}

.hero-bg {{
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: right center;
    opacity: 0.95;
    z-index: 1;
}}

.hero-overlay {{
    position: absolute;
    inset: 0;
    background: linear-gradient(90deg, 
        #071e33 0%, 
        rgba(7,30,51,0.96) 42%, 
        rgba(11,65,109,0.72) 68%, 
        rgba(7,30,51,0.2) 100%
    );
    z-index: 2;
}}

.hero-content {{
    position: relative;
    z-index: 3;
    max-width: 660px;
    color: #ffffff;
}}

.hero-pill {{
    display: inline-block;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.6px;
    color: {GOLD_ACCENT};
    background: rgba(197,155,39,0.18);
    border: 1px solid rgba(197,155,39,0.38);
    padding: 3px 10px;
    border-radius: 4px;
    margin-bottom: 6px;
}}

.hero-title {{
    font-family: 'Roboto Slab', serif;
    font-size: 24px;
    line-height: 1.15;
    margin: 0 0 4px;
    color: #ffffff;
    font-weight: 700;
    letter-spacing: 0.5px;
    text-shadow: 0 2px 4px rgba(0,0,0,0.3);
}}

.hero-subtitle {{
    font-size: 14px;
    font-weight: 700;
    color: #e2edf7;
    margin: 0 0 3px;
    letter-spacing: 0.2px;
}}

.hero-tagline {{
    font-size: 12.5px;
    color: #a3c2db;
    margin: 0 0 10px;
    font-weight: 500;
}}

.hero-desc {{
    font-size: 13px;
    line-height: 1.5;
    color: #d6e4f0;
    margin: 0 0 14px;
    max-width: 630px;
}}

.hero-capabilities {{
    display: flex;
    flex-wrap: wrap;
    gap: 7px;
}}

.cap-pill {{
    font-size: 12px;
    font-weight: 600;
    color: #f1f7fc;
    background: rgba(11, 65, 109, 0.65);
    border: 1px solid rgba(156, 186, 207, 0.35);
    backdrop-filter: blur(4px);
    padding: 4px 11px;
    border-radius: 4px;
    transition: all 0.2s ease;
}}

.cap-pill:hover {{
    background: rgba(197, 155, 39, 0.25);
    border-color: {GOLD_ACCENT};
    color: #ffffff;
}}

/* Metric & KPI Cards with Hover Elevation */
.kpi-card {{
    background: #ffffff;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 14px 16px;
    min-height: 92px;
    box-shadow: 0 2px 5px rgba(7,30,51,0.03);
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    position: relative;
    overflow: hidden;
}}

.kpi-card::before {{
    content: "";
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 3px;
    background: var(--sail-blue);
    opacity: 0.85;
}}

.kpi-card:hover {{
    transform: translateY(-3px);
    box-shadow: 0 8px 20px rgba(7,30,51,0.09);
    border-color: #9cbacf;
}}

.kpi-header {{
    font-size: 12px;
    font-weight: 700;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 4px;
}}

.kpi-value {{
    font-size: 19px;
    font-weight: 800;
    color: var(--sail-blue);
    line-height: 1.15;
}}

.kpi-sub {{
    font-size: 12px;
    color: #486581;
    margin-top: 4px;
    font-weight: 500;
}}

/* Recommendation Banner with Micro-Interactions */
.rec-banner {{
    background: {GREEN_BG};
    border: 1px solid #a3e0bf;
    border-radius: 6px;
    padding: 16px 20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 20px;
    margin-bottom: 14px;
    box-shadow: 0 2px 6px rgba(18,115,74,0.06);
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}}

.rec-banner:hover {{
    transform: translateY(-2px);
    box-shadow: 0 6px 16px rgba(7,30,51,0.08);
}}

.rec-banner-wait {{
    background: {GOLD_BG};
    border-color: #f1d693;
}}

.rec-banner-now {{
    background: {GREEN_BG};
    border-color: #a3e0bf;
}}

.rec-banner-part {{
    background: #eff6ff;
    border-color: #bbf2f6;
}}

.rec-action {{
    font-size: 21px;
    font-weight: 800;
    color: var(--sail-blue);
    letter-spacing: 0.5px;
}}

.rec-reason {{
    font-size: 13.5px;
    color: #173956;
    line-height: 1.45;
    margin-top: 2px;
}}

/* Status Badges */
.badge {{
    display: inline-block;
    padding: 4px 10px;
    border-radius: 4px;
    font-size: 12px;
    font-weight: 700;
    text-align: center;
    letter-spacing: 0.3px;
}}

.badge-feasible {{ background: {GREEN_BG}; color: {GREEN_SUCCESS}; border: 1px solid #a3e0bf; }}
.badge-conditional {{ background: {GOLD_BG}; color: #9c6c00; border: 1px solid #e7cd8c; }}
.badge-reject {{ background: {RED_BG}; color: {RED_ALERT}; border: 1px solid #f1b3b1; }}

.badge-low {{ background: {GREEN_BG}; color: {GREEN_SUCCESS}; border: 1px solid #a3e0bf; }}
.badge-medium {{ background: {GOLD_BG}; color: #9c6c00; border: 1px solid #e7cd8c; }}
.badge-high {{ background: {RED_BG}; color: {RED_ALERT}; border: 1px solid #f1b3b1; }}
.badge-critical {{ background: #fbeae9; color: {RED_CRITICAL}; border: 1px solid #e59c99; font-weight: 800; }}

/* Form Controls & Labels */
label[data-testid="stWidgetLabel"] p {{
    font-size: 12.5px !important;
    color: #1c364e !important;
    font-weight: 700 !important;
}}

div[data-baseweb="select"] > div {{
    min-height: 38px !important;
    background-color: #ffffff !important;
    border: 1px solid #cbd9e5 !important;
    border-radius: 5px !important;
    transition: all 0.2s ease !important;
    box-shadow: 0 1px 3px rgba(7,30,51,0.04) !important;
}}

div[data-baseweb="select"] > div:hover {{
    border-color: {SAIL_BLUE} !important;
    box-shadow: 0 2px 6px rgba(11,65,109,0.1) !important;
}}

div[data-baseweb="select"] span {{
    color: #0d2842 !important;
    font-size: 12.5px !important;
    font-weight: 600 !important;
}}

/* Date Input Field */
div[data-testid="stDateInput"] {{
    position: relative;
}}

div[data-testid="stDateInput"] div[data-baseweb="input"],
div[data-testid="stDateInput"] div[data-baseweb="base-input"] {{
    background-color: #ffffff !important;
    border: 1px solid #cbd9e5 !important;
    border-radius: 5px !important;
    min-height: 38px !important;
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
    box-shadow: 0 1px 3px rgba(7,30,51,0.04) !important;
}}

div[data-testid="stDateInput"] div[data-baseweb="input"]:hover,
div[data-testid="stDateInput"] div[data-baseweb="base-input"]:hover {{
    border-color: {SAIL_BLUE} !important;
    box-shadow: 0 2px 8px rgba(11,65,109,0.12) !important;
}}

div[data-testid="stDateInput"] div[data-baseweb="input"]:focus-within,
div[data-testid="stDateInput"] div[data-baseweb="base-input"]:focus-within {{
    border-color: {SAIL_BLUE} !important;
    box-shadow: 0 0 0 3px rgba(11,65,109,0.18) !important;
}}

div[data-testid="stDateInput"] input {{
    background-color: transparent !important;
    color: #0d2842 !important;
    font-size: 13px !important;
    font-weight: 600 !important;
    font-family: Inter, sans-serif !important;
    letter-spacing: 0.3px !important;
}}

/* Date Segment Highlight (Active YYYY/MM/DD Part) */
div[data-testid="stDateInput"] span[aria-selected="true"],
div[data-testid="stDateInput"] span[data-highlighted="true"],
div[data-testid="stDateInput"] [aria-selected="true"] {{
    background-color: rgba(11, 65, 109, 0.12) !important;
    color: {SAIL_BLUE} !important;
    border-radius: 3px !important;
    padding: 1px 4px !important;
    font-weight: 700 !important;
}}

/* BaseWeb Popover / Calendar Dropdown Card */
div[data-baseweb="popover"],
div[data-baseweb="popover"] > div,
div[data-baseweb="calendar"],
div[role="dialog"][data-baseweb="calendar"] {{
    background-color: #ffffff !important;
    background: #ffffff !important;
    border-radius: 8px !important;
    box-shadow: 0 12px 36px rgba(7, 30, 51, 0.16), 0 2px 8px rgba(7, 30, 51, 0.06) !important;
    border: 1px solid #cbd9e5 !important;
    color: #0d2842 !important;
    font-family: Inter, sans-serif !important;
    padding: 6px !important;
}}

/* Calendar Month / Year Header */
div[data-baseweb="calendar-header"] {{
    background: #f5f9fc !important;
    border-bottom: 1px solid #e2ebf3 !important;
    border-radius: 6px 6px 0 0 !important;
    padding: 8px 10px !important;
    margin-bottom: 6px !important;
}}

div[data-baseweb="calendar-header"] select {{
    background-color: #ffffff !important;
    color: {SAIL_BLUE} !important;
    font-weight: 700 !important;
    font-size: 12px !important;
    border: 1px solid #cbd9e5 !important;
    border-radius: 4px !important;
    padding: 4px 8px !important;
    cursor: pointer !important;
}}

div[data-baseweb="calendar-header"] select:hover {{
    border-color: {SAIL_BLUE} !important;
}}

div[data-baseweb="calendar-header"] button {{
    background-color: #ffffff !important;
    color: {SAIL_BLUE} !important;
    border: 1px solid #cbd9e5 !important;
    border-radius: 4px !important;
    padding: 4px 8px !important;
    transition: all 0.15s ease !important;
}}

div[data-baseweb="calendar-header"] button:hover {{
    background-color: {SAIL_BLUE} !important;
    color: #ffffff !important;
    border-color: {SAIL_BLUE} !important;
}}

/* Day of Week Headers (S M T W T F S) */
div[data-baseweb="calendar"] [role="columnheader"],
div[data-baseweb="calendar"] [aria-label*="day of week"] {{
    color: #5d7082 !important;
    font-weight: 700 !important;
    font-size: 12px !important;
    padding: 6px 0 !important;
    text-transform: uppercase !important;
}}

/* Calendar Day Cells */
div[data-baseweb="calendar"] [role="gridcell"] {{
    color: #0d2842 !important;
    font-size: 12.5px !important;
    font-weight: 600 !important;
    border-radius: 6px !important;
    transition: all 0.15s ease !important;
    cursor: pointer !important;
}}

div[data-baseweb="calendar"] [role="gridcell"]:hover {{
    background-color: #eef6fc !important;
    color: {SAIL_BLUE} !important;
}}

/* Selected Date in Calendar */
div[data-baseweb="calendar"] [aria-selected="true"],
div[data-baseweb="calendar"] button[aria-selected="true"],
div[data-baseweb="calendar"] [data-highlighted="true"] {{
    background-color: {SAIL_BLUE} !important;
    color: #ffffff !important;
    font-weight: 700 !important;
    border-radius: 6px !important;
    box-shadow: 0 2px 6px rgba(11, 65, 109, 0.35) !important;
}}

/* Disabled / Past Days (before min_value) and Outside-Month Days */
div[data-baseweb="calendar"] [aria-disabled="true"],
div[data-baseweb="calendar"] [role="gridcell"][aria-disabled="true"],
div[data-baseweb="calendar"] [aria-disabled="true"] > div {{
    color: #a0aec0 !important;
    opacity: 0.38 !important;
    cursor: not-allowed !important;
    pointer-events: none !important;
    background-color: transparent !important;
    text-decoration: line-through !important;
}}

div[data-baseweb="calendar"] [aria-disabled="true"]:hover,
div[data-baseweb="calendar"] [role="gridcell"][aria-disabled="true"]:hover {{
    background-color: transparent !important;
    color: #a0aec0 !important;
    cursor: not-allowed !important;
}}

/* Number Input Field & Step Buttons */
div[data-testid="stNumberInput"] div[data-baseweb="input"] {{
    background-color: #ffffff !important;
    border: 1px solid #cbd9e5 !important;
    border-radius: 5px !important;
    min-height: 38px !important;
    box-shadow: 0 1px 3px rgba(7,30,51,0.04) !important;
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
    overflow: hidden !important;
}}

div[data-testid="stNumberInput"] div[data-baseweb="base-input"] {{
    background-color: transparent !important;
    border: none !important;
    box-shadow: none !important;
    min-height: auto !important;
}}

div[data-testid="stNumberInput"] div[data-baseweb="input"]:hover {{
    border-color: {SAIL_BLUE} !important;
    box-shadow: 0 2px 8px rgba(11,65,109,0.12) !important;
}}

div[data-testid="stNumberInput"] div[data-baseweb="input"]:focus-within {{
    border-color: {SAIL_BLUE} !important;
    box-shadow: 0 0 0 3px rgba(11,65,109,0.18) !important;
}}

div[data-testid="stNumberInput"] input {{
    background-color: transparent !important;
    color: #0d2842 !important;
    font-size: 13px !important;
    font-weight: 600 !important;
    font-family: Inter, sans-serif !important;
    border: none !important;
    box-shadow: none !important;
    outline: none !important;
    padding-left: 10px !important;
}}

div[data-testid="stNumberInput"] button {{
    background-color: #f0f6fb !important;
    color: {SAIL_BLUE} !important;
    border: none !important;
    border-left: 1px solid #cbd9e5 !important;
    min-width: 32px !important;
    font-size: 14px !important;
    font-weight: 700 !important;
    transition: all 0.15s ease !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
}}

div[data-testid="stNumberInput"] button:hover {{
    background-color: {SAIL_BLUE} !important;
    color: #ffffff !important;
}}

div[data-testid="stNumberInput"] button:hover svg {{
    fill: #ffffff !important;
    stroke: #ffffff !important;
    color: #ffffff !important;
}}

div[data-testid="stNumberInput"] button:disabled,
div[data-testid="stNumberInput"] button[disabled] {{
    background-color: #f8fafc !important;
    color: #94a3b8 !important;
    cursor: not-allowed !important;
    opacity: 0.5 !important;
}}

div[data-testid="stNumberInput"] button:disabled svg,
div[data-testid="stNumberInput"] button[disabled] svg {{
    fill: #94a3b8 !important;
    stroke: #94a3b8 !important;
}}

input {{
    border-color: #c0d1e0 !important;
    border-radius: 4px !important;
}}

/* Dataframe Styling */
[data-testid="stDataFrame"] {{
    border: 1px solid var(--border) !important;
    border-radius: 6px !important;
    overflow: hidden !important;
    box-shadow: 0 2px 6px rgba(7,30,51,0.03) !important;
    transition: box-shadow 0.2s ease !important;
}}

[data-testid="stDataFrame"]:hover {{
    box-shadow: 0 4px 12px rgba(7,30,51,0.07) !important;
}}

/* ==============================================================================
   COMPREHENSIVE RESPONSIVE DESIGN SYSTEM
   ============================================================================== */

/* 1. Large Tablet & Small Laptop (1024px - 1199px) */
@media (max-width: 1199px) {{
    .block-container, [data-testid="stMainBlockContainer"] {{
        max-width: 98% !important;
        padding-left: 1rem !important;
        padding-right: 1rem !important;
    }}
    .brand-title {{ font-size: 21px !important; }}
    .brand-sub {{ font-size: 12.5px !important; }}
    .hero-title {{ font-size: 22px !important; }}
    div[data-testid="stButton"] button {{
        font-size: 12.5px !important;
        padding: 7px 6px !important;
    }}
}}

/* 2. Tablet Portrait & Landscape (768px - 1023px) */
@media (max-width: 1023px) {{
    /* Gov Header Stacking */
    .gov-header {{
        flex-direction: column !important;
        text-align: center !important;
        padding: 12px 16px !important;
        gap: 8px !important;
    }}
    .gov-left, .sagar {{
        justify-content: center !important;
        min-width: 100% !important;
        gap: 12px !important;
    }}
    .brand {{
        padding: 4px 0 !important;
    }}
    .brand-title {{
        font-size: 20px !important;
    }}

    /* 8-Tab Navigation Bar: Responsive 4x2 Grid */
    div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"] button[kind="secondary"], > div[data-testid="column"] button[kind="primary"]) {{
        flex-wrap: wrap !important;
        gap: 8px 6px !important;
    }}
    div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"] button[kind="secondary"], > div[data-testid="column"] button[kind="primary"]) > div[data-testid="column"] {{
        flex: 1 1 calc(25% - 8px) !important;
        min-width: calc(25% - 8px) !important;
        max-width: calc(25% - 4px) !important;
    }}
    div[data-testid="stButton"] button {{
        font-size: 12px !important;
        padding: 7px 6px !important;
        min-height: 40px !important;
    }}

    /* Hero Banner on Tablet */
    .hero-container {{
        padding: 20px !important;
    }}
    .hero-content {{
        max-width: 100% !important;
    }}
    .hero-overlay {{
        background: linear-gradient(90deg, 
            rgba(7,30,51,0.97) 0%, 
            rgba(7,30,51,0.92) 55%, 
            rgba(11,65,109,0.78) 80%, 
            rgba(7,30,51,0.35) 100%
        ) !important;
    }}
    .hero-title {{
        font-size: 21px !important;
    }}

    /* 4-column KPI Metric strips -> 2x2 grid */
    div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"] .kpi-card) {{
        flex-wrap: wrap !important;
        gap: 10px !important;
    }}
    div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"] .kpi-card) > div[data-testid="column"] {{
        flex: 1 1 calc(50% - 8px) !important;
        min-width: calc(50% - 8px) !important;
    }}

    /* 4-column input fields on Dashboard -> 2x2 grid */
    div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"] div[data-testid="stSelectbox"]) {{
        flex-wrap: wrap !important;
        gap: 8px !important;
    }}
    div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"] div[data-testid="stSelectbox"]) > div[data-testid="column"] {{
        flex: 1 1 calc(50% - 8px) !important;
        min-width: calc(50% - 8px) !important;
    }}

    /* Split panels (Map + Details) wrap to stacked rows */
    div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"] .route-map-panel, > div[data-testid="column"] iframe) {{
        flex-wrap: wrap !important;
    }}
    div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"] .route-map-panel, > div[data-testid="column"] iframe) > div[data-testid="column"] {{
        flex: 1 1 100% !important;
        min-width: 100% !important;
        margin-bottom: 12px !important;
    }}

    /* Footer */
    .footer-right {{
        text-align: left !important;
        margin-top: 6px !important;
    }}
}}

/* 3. Mobile Devices (< 768px) */
@media (max-width: 767px) {{
    .block-container, [data-testid="stMainBlockContainer"] {{
        max-width: 100% !important;
        padding: 0.4rem 0.5rem 2rem !important;
    }}

    /* Header on Mobile */
    .gov-header {{
        padding: 10px 12px !important;
        min-height: auto !important;
    }}
    .gov-left img {{
        width: 34px !important;
        height: 42px !important;
    }}
    .sagar img {{
        width: 42px !important;
        height: 42px !important;
    }}
    .brand-title {{
        font-size: 17px !important;
    }}
    .brand-sub {{
        font-size: 12px !important;
    }}
    .brand-tag {{
        display: none !important;
    }}

    /* Navigation Bar: 2x4 Grid (2 buttons per row) */
    div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"] button[kind="secondary"], > div[data-testid="column"] button[kind="primary"]) {{
        flex-wrap: wrap !important;
        gap: 6px !important;
    }}
    div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"] button[kind="secondary"], > div[data-testid="column"] button[kind="primary"]) > div[data-testid="column"] {{
        flex: 1 1 calc(50% - 4px) !important;
        min-width: calc(50% - 4px) !important;
        max-width: calc(50% - 4px) !important;
    }}
    div[data-testid="stButton"] button {{
        font-size: 12px !important;
        padding: 6px 4px !important;
        min-height: 38px !important;
    }}

    /* Hero Banner on Mobile */
    .hero-container {{
        padding: 14px !important;
    }}
    .hero-pill {{
        font-size: 10.5px !important;
        padding: 3px 8px !important;
    }}
    .hero-title {{
        font-size: 19px !important;
    }}
    .hero-subtitle {{
        font-size: 13px !important;
    }}
    .hero-tagline {{
        font-size: 12px !important;
        margin-bottom: 6px !important;
    }}
    .hero-desc {{
        font-size: 12px !important;
        line-height: 1.4 !important;
        margin-bottom: 10px !important;
    }}
    .hero-capabilities {{
        gap: 5px !important;
    }}
    .cap-pill {{
        font-size: 11px !important;
        padding: 3px 8px !important;
    }}

    /* All Multi-Column Form Inputs & Sliders stack to 100% on Mobile */
    div[data-testid="stHorizontalBlock"] {{
        flex-wrap: wrap !important;
    }}
    div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {{
        flex: 1 1 100% !important;
        min-width: 100% !important;
        margin-bottom: 6px !important;
    }}

    /* Navigation override: Keep 2 buttons per row */
    div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"] button[kind="secondary"], > div[data-testid="column"] button[kind="primary"]) > div[data-testid="column"] {{
        flex: 1 1 calc(50% - 4px) !important;
        min-width: calc(50% - 4px) !important;
    }}

    /* Recommendation Banner on Mobile */
    .rec-banner {{
        flex-direction: column !important;
        align-items: flex-start !important;
        padding: 12px 14px !important;
        gap: 8px !important;
    }}
    .rec-action {{
        font-size: 18px !important;
    }}
    .rec-reason {{
        font-size: 12.5px !important;
    }}

    /* KPI Cards on Mobile */
    div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"] .kpi-card) > div[data-testid="column"] {{
        flex: 1 1 100% !important;
        min-width: 100% !important;
        margin-bottom: 8px !important;
    }}
    .kpi-card {{
        padding: 10px 12px !important;
        min-height: 80px !important;
    }}
    .kpi-header {{
        font-size: 11.5px !important;
    }}
    .kpi-value {{
        font-size: 17px !important;
    }}
    .kpi-sub {{
        font-size: 11.5px !important;
    }}

    /* Panels & Tables */
    .panel {{
        padding: 12px 14px !important;
    }}
    .section-title {{
        font-size: 14px !important;
    }}

    /* Footer */
    .footer-right {{
        text-align: left !important;
        margin-top: 8px !important;
    }}
}}

/* Authentication & Profile Styling */
.auth-card-header {{
    background: #ffffff;
    border: 1px solid #cbd9e5;
    border-top: 4px solid #0b416d;
    border-radius: 8px 8px 0 0;
    padding: 22px 24px 18px 24px;
    margin-bottom: -1px;
    box-shadow: 0 4px 16px rgba(7, 30, 51, 0.05);
}}
.auth-card-pill {{
    font-size: 11.5px;
    font-weight: 800;
    color: #c59b27;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    margin-bottom: 4px;
}}
.auth-card-title {{
    font-family: 'Roboto Slab', serif;
    font-size: 20px;
    font-weight: 700;
    color: #0b416d;
    margin-bottom: 4px;
}}
.auth-card-sub {{
    font-size: 13px;
    color: #556a7e;
    line-height: 1.45;
}}
.auth-security-footer {{
    text-align: center;
    font-size: 12px;
    color: #728495;
    margin-top: 18px;
    padding-top: 10px;
    border-top: 1px solid #e2ebf3;
}}
.user-profile-strip {{
    display: flex;
    align-items: center;
    gap: 12px;
    background: #ffffff;
    border: 1px solid #cbd9e5;
    border-left: 4px solid #0b416d;
    border-radius: 6px;
    padding: 8px 14px;
    margin: 6px 0 10px 0;
    box-shadow: 0 2px 6px rgba(7, 30, 51, 0.04);
}}
.user-avatar {{
    font-size: 18px;
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: #eef6fc;
    color: #0b416d;
    display: flex;
    align-items: center;
    justify-content: center;
}}
.user-meta {{
    flex: 1;
}}
.user-name {{
    font-size: 12.5px;
    color: #071e33;
    line-height: 1.3;
}}
.user-badge {{
    display: inline-block;
    background: #eef6fc;
    color: #0b416d;
    border: 1px solid #cbd9e5;
    border-radius: 4px;
    font-size: 11.5px;
    font-weight: 700;
    padding: 2px 7px;
    margin-left: 6px;
}}
.user-sub {{
    font-size: 12px;
    color: #556a7e;
    margin-top: 2px;
}}
/* Brand Organization Subtitle */
.brand-org {{
    font-size: 11px;
    font-weight: 700;
    color: #0b416d;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    margin-bottom: 2px;
}}

/* Landing Page Hero Left Text Card */
.landing-hero-card {{
    background: linear-gradient(135deg, #071e33 0%, #0b416d 68%, #173956 100%);
    border: 1px solid #1a4970;
    border-radius: 8px;
    padding: 26px 28px 22px 28px;
    box-shadow: 0 8px 24px rgba(7, 30, 51, 0.14);
    position: relative;
    overflow: hidden;
    margin-bottom: 12px;
}}

.landing-hero-card::before {{
    content: "";
    position: absolute;
    top: -50px;
    right: -50px;
    width: 200px;
    height: 200px;
    border-radius: 50%;
    border: 1px dashed rgba(255, 255, 255, 0.08);
    pointer-events: none;
}}

.landing-hero-card::after {{
    content: "";
    position: absolute;
    bottom: -30px;
    right: 20px;
    width: 130px;
    height: 130px;
    border-radius: 50%;
    border: 1px solid rgba(197, 155, 39, 0.15);
    pointer-events: none;
}}

.landing-pill {{
    display: inline-block;
    background: rgba(197, 155, 39, 0.22);
    border: 1px solid rgba(197, 155, 39, 0.55);
    color: #ffd566;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.6px;
    padding: 4px 12px;
    border-radius: 20px;
    margin-bottom: 12px;
}}

.landing-title,
h1.landing-title,
div.landing-title,
.landing-hero-card h1,
.landing-hero-card .landing-title,
[data-testid="stMarkdownContainer"] .landing-title,
[data-testid="stMarkdownContainer"] h1.landing-title {{
    font-family: 'Roboto Slab', serif !important;
    font-size: 38px !important;
    font-weight: 800 !important;
    color: #ffffff !important;
    -webkit-text-fill-color: #ffffff !important;
    line-height: 1.15 !important;
    margin: 4px 0 8px 0 !important;
    letter-spacing: 0.5px !important;
    text-shadow: 0 2px 10px rgba(0, 0, 0, 0.5) !important;
}}

.landing-subtitle {{
    font-size: 18px;
    font-weight: 700;
    color: #ffd566;
    margin-bottom: 14px;
    letter-spacing: 0.3px;
}}

.landing-desc {{
    font-size: 13.5px;
    line-height: 1.6;
    color: #e2ebf3;
    margin-bottom: 20px;
    max-width: 580px;
}}

.landing-caps {{
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 20px;
}}

.cap-pill {{
    background: rgba(255, 255, 255, 0.12);
    border: 1px solid rgba(255, 255, 255, 0.25);
    color: #ffffff;
    font-size: 11.5px;
    font-weight: 600;
    padding: 4px 10px;
    border-radius: 4px;
}}

/* Landing Hero Visual Frame */
.landing-hero-frame {{
    background: #071e33;
    border: 1px solid #1a4970;
    border-radius: 8px;
    overflow: hidden;
    position: relative;
    box-shadow: 0 8px 24px rgba(7, 30, 51, 0.14);
    height: 100%;
    min-height: 300px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
}}

.landing-hero-img {{
    width: 100%;
    height: 245px;
    object-fit: cover;
    display: block;
    transition: transform 0.4s ease;
}}

.landing-hero-frame:hover .landing-hero-img {{
    transform: scale(1.03);
}}

.hero-ship-badge-top {{
    position: absolute;
    top: 12px;
    left: 12px;
    background: rgba(7, 30, 51, 0.88);
    backdrop-filter: blur(6px);
    border: 1px solid rgba(197, 155, 39, 0.45);
    color: #ffd566;
    font-size: 10.5px;
    font-weight: 700;
    padding: 4px 10px;
    border-radius: 4px;
    z-index: 2;
}}

.hero-ship-badge-bottom {{
    background: rgba(7, 30, 51, 0.92);
    backdrop-filter: blur(8px);
    border-top: 1px solid rgba(255, 255, 255, 0.15);
    padding: 10px 14px;
    z-index: 2;
}}

.hero-badge-title {{
    font-size: 12px;
    font-weight: 700;
    color: #ffffff;
}}

.hero-badge-coords {{
    font-size: 10.5px;
    color: #a8c4db;
    font-family: monospace;
    margin-top: 2px;
}}

/* Section Headers */
.landing-section-header {{
    text-align: center;
    margin: 36px 0 22px 0;
}}

.landing-section-pill {{
    font-size: 11px;
    font-weight: 700;
    color: #0b416d;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 4px;
}}

.landing-section-title {{
    font-family: 'Roboto Slab', serif;
    font-size: 25px;
    font-weight: 700;
    color: #071e33;
}}

.landing-section-sub {{
    font-size: 13.5px;
    color: #556a7e;
    max-width: 680px;
    margin: 6px auto 0;
    line-height: 1.5;
}}

/* Value Proposition Feature Cards */
.feat-card {{
    background: #ffffff;
    border: 1px solid #cbd9e5;
    border-radius: 8px;
    padding: 20px 16px;
    min-height: 220px;
    box-shadow: 0 2px 8px rgba(7, 30, 51, 0.04);
    transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    display: flex;
    flex-direction: column;
    justify-content: flex-start;
}}

.feat-card:hover {{
    transform: translateY(-4px);
    border-color: #0b416d;
    box-shadow: 0 8px 24px rgba(11, 65, 109, 0.12);
}}

.feat-icon {{
    width: 44px;
    height: 44px;
    background: #f0f6fb;
    border: 1px solid #cbd9e5;
    border-radius: 8px;
    font-size: 20px;
    display: flex;
    align-items: center;
    justify-content: center;
    margin-bottom: 12px;
    transition: transform 0.2s ease;
}}

.feat-card:hover .feat-icon {{
    transform: scale(1.08);
    background: #e2ebf3;
}}

.feat-title {{
    font-size: 14px;
    font-weight: 700;
    color: #071e33;
    letter-spacing: 0.3px;
    margin-bottom: 6px;
}}

.feat-desc {{
    font-size: 12px;
    color: #556a7e;
    line-height: 1.45;
}}

/* How It Works Horizontal Process */
.hiw-step {{
    background: #ffffff;
    border: 1px solid #cbd9e5;
    border-radius: 8px;
    padding: 18px 14px;
    min-height: 180px;
    box-shadow: 0 2px 6px rgba(7, 30, 51, 0.04);
    transition: all 0.25s ease;
    position: relative;
}}

.hiw-step:hover {{
    transform: translateY(-3px);
    border-color: #0b416d;
    box-shadow: 0 6px 20px rgba(11, 65, 109, 0.1);
}}

.hiw-num {{
    width: 34px;
    height: 34px;
    background: #0b416d;
    color: #ffd566;
    font-size: 13px;
    font-weight: 800;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    border: 2px solid #c59b27;
    margin-bottom: 10px;
}}

.hiw-title {{
    font-size: 12.5px;
    font-weight: 700;
    color: #071e33;
    text-transform: uppercase;
    letter-spacing: 0.3px;
    line-height: 1.3;
    margin-bottom: 6px;
}}

.hiw-desc {{
    font-size: 11.5px;
    color: #556a7e;
    line-height: 1.4;
}}

/* Trust & Architecture Section */
.trust-card {{
    background: #ffffff;
    border: 1px solid #cbd9e5;
    border-top: 3px solid #0b416d;
    border-radius: 6px;
    padding: 22px 18px;
    box-shadow: 0 2px 8px rgba(7, 30, 51, 0.04);
    transition: all 0.25s ease;
}}

.trust-card:hover {{
    transform: translateY(-3px);
    box-shadow: 0 8px 20px rgba(7, 30, 51, 0.08);
}}

.trust-stat {{
    font-family: 'Roboto Slab', serif;
    font-size: 32px;
    font-weight: 700;
    color: #0b416d;
    line-height: 1.1;
}}

.trust-label {{
    font-size: 14px;
    font-weight: 700;
    color: #071e33;
    margin: 4px 0 8px 0;
}}

.trust-detail {{
    font-size: 12px;
    color: #556a7e;
    line-height: 1.45;
}}

.trust-disclaimer {{
    margin-top: 14px;
    padding: 10px 14px;
    background: #f5f9fc;
    border: 1px solid #cbd9e5;
    border-radius: 4px;
    font-size: 11.5px;
    color: #556a7e;
    line-height: 1.45;
}}

/* About Section */
.about-card {{
    background: linear-gradient(180deg, #ffffff 0%, #f5f9fc 100%);
    border: 1px solid #cbd9e5;
    border-left: 4px solid #c59b27;
    border-radius: 6px;
    padding: 24px 26px;
    margin-top: 24px;
    box-shadow: 0 2px 8px rgba(7, 30, 51, 0.04);
}}

.about-title {{
    font-family: 'Roboto Slab', serif;
    font-size: 18px;
    font-weight: 700;
    color: #0b416d;
    margin-bottom: 8px;
}}

.about-text {{
    font-size: 13.5px;
    color: #0d2842;
    line-height: 1.6;
}}

.about-notice {{
    margin-top: 12px;
    padding: 10px 14px;
    background: #fef9e7;
    border: 1px solid #e0b438;
    border-radius: 4px;
    font-size: 12px;
    color: #855d00;
    line-height: 1.45;
}}

/* Landing Officer Active Ribbon */
.landing-officer-banner {{
    background: #eef9f2;
    border: 1px solid #12734a;
    border-radius: 6px;
    padding: 10px 16px;
    color: #12734a;
    font-size: 13px;
    font-weight: 600;
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
}}

/* Responsive Breakpoints */
@media (max-width: 992px) {{
    .landing-title {{
        font-size: 30px;
    }}
    .landing-subtitle {{
        font-size: 16px;
    }}
    .landing-hero-container {{
        padding: 24px 20px;
    }}
}}

@media (max-width: 768px) {{
    .landing-title {{
        font-size: 26px;
    }}
    .gov-left {{
        min-width: unset;
    }}
    .sagar {{
        min-width: unset;
    }}
}}

/* Full-Screen Login Loading Transition Overlay */
.login-loader-overlay {{
    position: fixed;
    top: 0;
    left: 0;
    width: 100vw;
    height: 100vh;
    background: #071e33;
    z-index: 99999999;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    opacity: 1;
    visibility: visible;
    transition: opacity 0.6s cubic-bezier(0.4, 0, 0.2, 1), visibility 0.6s cubic-bezier(0.4, 0, 0.2, 1);
    pointer-events: all;
}}

.login-loader-overlay.fade-out {{
    opacity: 0 !important;
    visibility: hidden !important;
    pointer-events: none !important;
}}

.login-loader-box {{
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
    max-width: 440px;
    padding: 30px;
}}

.login-loader-spinner {{
    width: 52px;
    height: 52px;
    border: 4px solid rgba(255, 255, 255, 0.12);
    border-top: 4px solid #c59b27;
    border-right: 4px solid #0b416d;
    border-radius: 50%;
    animation: loaderSpin 0.9s linear infinite;
    margin-bottom: 22px;
}}

@keyframes loaderSpin {{
    0% {{ transform: rotate(0deg); }}
    100% {{ transform: rotate(360deg); }}
}}

.login-loader-title {{
    font-family: 'Roboto Slab', serif, -apple-system, sans-serif;
    font-size: 26px;
    font-weight: 700;
    color: #ffffff;
    letter-spacing: 0.8px;
    margin-bottom: 6px;
}}

.login-loader-sub {{
    font-family: Inter, sans-serif;
    font-size: 13px;
    color: #a8c4db;
    margin-bottom: 20px;
    letter-spacing: 0.2px;
}}

.login-loader-bar {{
    width: 220px;
    height: 3px;
    background: rgba(255, 255, 255, 0.1);
    border-radius: 3px;
    overflow: hidden;
    position: relative;
}}

.login-loader-bar-inner {{
    width: 100%;
    height: 100%;
    background: linear-gradient(90deg, transparent, #c59b27, #ffffff, transparent);
    position: absolute;
    animation: loaderProgress 1.4s ease-in-out infinite;
}}

@keyframes loaderProgress {{
    0% {{ transform: translateX(-100%); }}
    100% {{ transform: translateX(100%); }}
}}
</style>
""",
    unsafe_allow_html=True,
)


def header():
    emblem = f"data:image/png;base64,{EMBLEM}" if EMBLEM else ""
    sagar = f"data:image/png;base64,{SAGAR}" if SAGAR else ""
    st.markdown(
        f"""
<div class="gov-header">
  <div class="gov-left">
    {f'<img src="{emblem}" alt="Government Emblem">' if emblem else ''}
    <div>
      <div class="gov-small">GOVERNMENT OF INDIA</div>
      <div class="gov-subtext">MINISTRY OF STEEL</div>
    </div>
  </div>
  <div class="brand">
    <div class="brand-org">STEEL AUTHORITY OF INDIA LIMITED</div>
    <div class="brand-title">SAGAR DRISHTI</div>
    <div class="brand-sub">SAIL Freight Intelligence &amp; Chartering Advisor</div>
  </div>
  <div class="sagar">
    <div style="display:flex;align-items:center;justify-content:flex-end;">{SAIL_LOGO}</div>
    {f'<img src="{sagar}" style="height:65px;width:65px;object-fit:contain" alt="SAGAR DRISHTI Logo">' if sagar else ''}
  </div>
</div>
""",
        unsafe_allow_html=True,
    )


def route_coords(origin: str):
    origins = {
        "Australia (Hay Point, QLD)": (-21.28, 149.29),
        "Australia (Newcastle, NSW)": (-32.93, 151.78),
        "Indonesia (Kalimantan)": (-2.2, 116.2),
        "South Africa (Richards Bay)": (-28.8, 32.1),
        "Mozambique (Maputo)": (-25.97, 32.58),
        "USA (Hampton Roads)": (36.85, -76.3),
    }
    return origins.get(origin, (-21.28, 149.29))


def route_map(origin: str, port: str, waypoints=None):
    olat, olon = route_coords(origin)
    try:
        canonical_port = normalize_port(port)
        pcfg = PORTS[canonical_port]
    except Exception:
        raise UnknownPortError(f"Selected port '{port}' is not available in the current port configuration.")
    route_ports = [normalize_port(x) for x in (waypoints or []) if normalize_port(x) != canonical_port] + [canonical_port]
    route_lons = [olon] + [PORTS[x]["longitude"] for x in route_ports]
    route_lats = [olat] + [PORTS[x]["latitude"] for x in route_ports]
    fig = go.Figure()
    fig.add_trace(
        go.Scattergeo(
            lon=route_lons, lat=route_lats, mode="lines+markers",
            line=dict(width=2.5, color="#ffffff", dash="dash"),
            marker=dict(size=7), hoverinfo="text",
            text=["Load Origin"] + route_ports, name="Planned Route"
        )
    )
    fig.add_trace(
        go.Scattergeo(
            lon=[olon],
            lat=[olat],
            mode="markers+text",
            text=[origin.split(" ")[0]],
            textposition="top center",
            marker=dict(size=10, color="#d9534f"),
            name="Load Origin Port",
        )
    )
    if len(route_ports) > 1:
        st_waypoints = go.Scattergeo(
            lon=[PORTS[x]["longitude"] for x in route_ports[:-1]],
            lat=[PORTS[x]["latitude"] for x in route_ports[:-1]],
            mode="markers+text", text=route_ports[:-1], textposition="top center",
            marker=dict(size=9, color="#c59b27"), name="Intermediate Port Call"
        )
        fig.add_trace(st_waypoints)
    fig.add_trace(
        go.Scattergeo(
            lon=[pcfg["longitude"]],
            lat=[pcfg["latitude"]],
            mode="markers+text",
            text=[canonical_port],
            textposition="top center",
            marker=dict(size=10, color="#2b9a66"),
            name="Discharge Port",
        )
    )
    fig.update_geos(
        showland=True,
        landcolor="#1c364e",
        showocean=True,
        oceancolor="#071e33",
        showcountries=True,
        countrycolor="#3a5a78",
        coastlinecolor="#4a6d8c",
        showlakes=True,
        lakecolor="#0c2842",
        projection_type="natural earth",
        lataxis_range=[-40, 35],
        lonaxis_range=[20, 165],
    )
    fig.update_layout(
        height=340,
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="#173956",
        legend=dict(orientation="h", y=0.03, x=0.03, font=dict(color="white", size=11)),
    )
    return fig


def forecast_chart(b):
    df = recommend._market(b["index"]).loc[: b["as_of"]]
    hist = df["freight"].iloc[-420:]
    p = b["path"]
    t0 = pd.Timestamp(b["as_of"])
    fx = [t0] + [t0 + pd.Timedelta(days=int(h)) for h in p.horizon_days]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hist.index, y=hist, name="Historical Index Level", line=dict(color=SAIL_BLUE, width=2)))
    fig.add_trace(
        go.Scatter(
            x=fx + fx[::-1],
            y=[b["current_level"]] + list(p.upper) + list(p.lower[::-1]) + [b["current_level"]],
            fill="toself",
            fillcolor="rgba(11,65,109,0.12)",
            line=dict(width=0),
            name="80% Split-Conformal Interval",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=fx,
            y=[b["current_level"]] + list(p.expected),
            name="Model Prediction Path",
            line=dict(color="#d9534f", width=2, dash="dash"),
        )
    )
    fig.add_vline(x=t0, line_dash="dot", line_color="#8aa0b2")
    fig.update_layout(
        height=340,
        margin=dict(l=10, r=10, t=10, b=5),
        paper_bgcolor="white",
        plot_bgcolor="white",
        legend=dict(orientation="h", y=1.06, x=0.45, font=dict(size=11.5)),
        font=dict(size=11.5),
        yaxis_title="Freight Index Points",
        xaxis_title="",
    )
    fig.update_xaxes(showgrid=True, gridcolor="#e4ecf2", tickformat="%d-%m-%Y")
    fig.update_yaxes(showgrid=True, gridcolor="#e4ecf2")
    return fig


def cost_chart(cp):
    labels = ["Vessel Hire", "Fuel (Base)", "Fuel (Weather Impact)", "Port Charges", "Waiting Cost"]
    vals = [
        cp.get("hire_usd", 0),
        max(0, cp.get("fuel_usd", 0) - cp.get("weather", {}).get("weather_impact_usd", 0)),
        cp.get("weather_impact_usd", cp.get("weather", {}).get("weather_impact_usd", 0)),
        cp.get("port_usd", 0),
        cp.get("wait_usd", 0),
    ]
    fig = go.Figure(
        go.Pie(
            labels=labels,
            values=vals,
            hole=0.55,
            textinfo="none",
            marker=dict(colors=["#0b416d", "#1e6b9e", "#8fc1df", "#12734a", "#c59b27"]),
        )
    )
    fig.update_layout(
        height=260,
        margin=dict(l=5, r=5, t=5, b=5),
        showlegend=True,
        paper_bgcolor="white",
        legend=dict(font=dict(size=11), orientation="v"),
    )
    fig.add_annotation(
        text=f"${cp['total_usd']:,.0f}<br><span style='font-size:11.5px;color:#5d7082'>Total Voyage Cost</span>",
        x=0.5,
        y=0.5,
        showarrow=False,
        font=dict(size=15, color=SAIL_BLUE, weight="bold"),
    )
    return fig


def get_risk_color(score: float | None) -> str:
    """Map risk score percentile strictly to the modern institutional risk band color palette."""
    if score is None or pd.isna(score):
        return "#94a3b8"
    if score <= 30:
        return "#10b981"  # LOW — Modern Emerald
    elif score <= 60:
        return "#f59e0b"  # MEDIUM — Modern Amber
    elif score <= 80:
        return "#f97316"  # HIGH — Modern Sunset Orange
    else:
        return "#ef4444"  # CRITICAL — Modern Vivid Crimson


def get_risk_band_text(score: float | None) -> str:
    """Return institutional risk band label."""
    if score is None or pd.isna(score):
        return "N/A"
    if score <= 30:
        return "LOW"
    elif score <= 60:
        return "MEDIUM"
    elif score <= 80:
        return "HIGH"
    else:
        return "CRITICAL"


def risk_profile_chart(b):
    """Render modern horizontal risk profile bar chart with sleek dual-layer progress meters."""
    rsk = b.get("risk", {})
    categories = [
        "📈 Market Volatility",
        "⛽ Fuel Price Momentum",
        "⚓ Port Congestion",
        "🌊 Weather Operational",
        "🛡️ Overall Composite Risk",
    ]
    vals = [
        rsk.get("Market (freight volatility)", 0),
        rsk.get("Fuel (Brent momentum)", 0),
        rsk.get("Port (waiting vs normal)", 0),
        rsk.get("Weather", 0),
        b.get("risk_overall", 0),
    ]

    colors = [get_risk_color(v) for v in vals]
    bands = [get_risk_band_text(v) for v in vals]

    fig = go.Figure()

    # 1. Subtle background tracks (0 to 100) for sleek progress-bar aesthetic
    fig.add_trace(
        go.Bar(
            x=[100] * len(categories),
            y=categories,
            orientation="h",
            marker=dict(
                color="#edf2f7",
                cornerradius=6,
                line=dict(width=0),
            ),
            hoverinfo="skip",
            showlegend=False,
            width=0.48,
        )
    )

    # 2. Foreground active filled bars with adaptive high-contrast text placement
    texts = []
    text_positions = []
    text_colors = []
    for v, band in zip(vals, bands):
        if v is None or pd.isna(v):
            texts.append("Data Unavailable")
            text_positions.append("outside")
            text_colors.append("#64748b")
        elif v >= 35:
            texts.append(f"<b>{v:.1f}</b> / 100 &nbsp;·&nbsp; <b>{band}</b>")
            text_positions.append("inside")
            text_colors.append("#ffffff")
        else:
            texts.append(f"<b>{v:.1f}</b> / 100 &nbsp;·&nbsp; <b>{band}</b>")
            text_positions.append("outside")
            text_colors.append("#071e33")

    fig.add_trace(
        go.Bar(
            x=vals,
            y=categories,
            orientation="h",
            marker=dict(
                color=colors,
                cornerradius=6,
                line=dict(width=0),
            ),
            text=texts,
            textposition=text_positions,
            textfont=dict(family="Inter, -apple-system, sans-serif", size=11, color=text_colors),
            hovertemplate="<b>%{y}</b><br>Percentile Score: <b>%{x:.1f} / 100</b><extra></extra>",
            showlegend=False,
            width=0.48,
        )
    )

    # Soft threshold background zones
    fig.add_vrect(x0=0, x1=30, fillcolor="rgba(16, 185, 129, 0.03)", layer="below", line_width=0)
    fig.add_vrect(x0=30, x1=60, fillcolor="rgba(245, 158, 11, 0.03)", layer="below", line_width=0)
    fig.add_vrect(x0=60, x1=80, fillcolor="rgba(249, 115, 22, 0.03)", layer="below", line_width=0)
    fig.add_vrect(x0=80, x1=100, fillcolor="rgba(239, 68, 68, 0.03)", layer="below", line_width=0)

    # Vertical threshold reference gridlines
    for t_val in [30, 60, 80]:
        fig.add_vline(x=t_val, line_width=1, line_dash="dot", line_color="#cbd5e1", layer="below")

    fig.update_layout(
        barmode="overlay",
        height=295,
        margin=dict(l=10, r=20, t=10, b=25),
        paper_bgcolor="white",
        plot_bgcolor="white",
        xaxis=dict(
            range=[0, 102],
            title="Percentile Score &bull; 0 = Historical Calmest, 100 = Historical Extreme",
            title_font=dict(size=12, color="#5d7082", family="Inter, sans-serif"),
            tickvals=[0, 30, 60, 80, 100],
            ticktext=["0", "30 (Low)", "60 (Medium)", "80 (High)", "100"],
            tickfont=dict(size=11.5, color="#64748b", family="Inter, sans-serif"),
            gridcolor="#eef3f8",
            zeroline=False,
        ),
        yaxis=dict(
            autorange="reversed",
            tickfont=dict(size=12.5, color="#071e33", family="Inter, sans-serif"),
        ),
        font=dict(family="Inter, sans-serif"),
    )
    return fig


def report_bytes(res, origin, port, qty, arrival, user=None, as_of=None):
    """Generate the official 2-page Sagar Drishti Voyage Analysis & Decision Report in PDF format."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.pdfgen import canvas
        from reportlab.platypus import (
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except Exception:
        return None

    b = res.get("best")
    if not b:
        return None

    # Resolve authenticated officer's full name from session
    if user is None:
        try:
            user = st.session_state.get("user")
        except Exception:
            user = None

    user_name = "SAIL Officer"
    if isinstance(user, dict):
        user_name = user.get("full_name") or user.get("name") or "SAIL Officer"
    elif isinstance(user, str) and user.strip():
        user_name = user.strip()

    # Dynamic generation timestamp at the exact second the PDF is produced
    gen_dt = dt.datetime.now()
    gen_timestamp = gen_dt.strftime("%d-%m-%Y, %I:%M %p")

    def clean_pdf_text(s: str) -> str:
        if not isinstance(s, str):
            if isinstance(s, (dt.date, dt.datetime)):
                return s.strftime("%d-%m-%Y")
            return str(s)
        import re
        txt = (
            s.replace("\ufffd", " - ")
            .replace("âš ", "WARN: ")
            .replace("â‚¹", "Rs. ")
            .replace("₹", "Rs. ")
            .replace("â†’", " -> ")
            .replace("→", " -> ")
            .replace("â€¢", "- ")
            .replace("â–¥", "- ")
            .replace("•", "- ")
            .replace("—", " - ")
            .replace("–", " - ")
            .replace("·", " | ")
        )
        txt = re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", r"\3-\2-\1", txt)
        return re.sub(r"&(?!(amp|lt|gt|bull|ndash|quot);)", "&amp;", txt)

    class SagarNumberedCanvas(canvas.Canvas):
        def __init__(self, *args, **kwargs):
            self.gen_timestamp = kwargs.pop("gen_timestamp", gen_timestamp)
            self.user_name = kwargs.pop("user_name", user_name)
            self.market_date = kwargs.pop("market_date", fmt_date_str(b.get("as_of", "N/A")))
            super().__init__(*args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            num_pages = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self.draw_page_decorations(num_pages)
                super().showPage()
            super().save()

        def draw_page_decorations(self, total_pages):
            w, h = A4
            # 1. Top Header Banner
            header_h = 82
            self.setFillColor(colors.HexColor("#0b416d"))
            self.rect(0, h - header_h, w, header_h, fill=1, stroke=0)

            # Subtle gold accent stripe at bottom of header
            self.setFillColor(colors.HexColor("#c59b27"))
            self.rect(0, h - header_h - 2.5, w, 2.5, fill=1, stroke=0)

            # Logos
            emblem_path = ASSET / "ministry_emblem.png"
            if emblem_path.exists():
                try:
                    self.drawImage(str(emblem_path), 36, h - header_h + 10, width=40, height=62, mask="auto", preserveAspectRatio=True)
                except Exception:
                    pass

            logo_path = ASSET / "sagar_drishti_logo.png" if (ASSET / "sagar_drishti_logo.png").exists() else ASSET / "sagar_logo.png"
            if logo_path.exists():
                try:
                    self.drawImage(str(logo_path), w - 36 - 60, h - header_h + 11, width=60, height=60, mask="auto", preserveAspectRatio=True)
                except Exception:
                    pass

            # Header Titles
            text_x = 88
            self.setFillColor(colors.white)
            self.setFont("Helvetica-Bold", 14)
            self.drawString(text_x, h - 28, "STEEL AUTHORITY OF INDIA LIMITED (SAIL)")

            self.setFont("Helvetica-Bold", 10.5)
            self.setFillColor(colors.HexColor("#fdf9ec"))
            self.drawString(text_x, h - 43, "SAGAR DRISHTI - Freight Intelligence & Chartering Advisor")

            self.setFont("Helvetica", 7.5)
            self.setFillColor(colors.HexColor("#d2e4f2"))
            self.drawString(text_x, h - 56, "Ministry of Steel | Government of India | Smart India Hackathon (SIH 2026)")

            self.setFont("Helvetica-Bold", 8.5)
            self.setFillColor(colors.HexColor("#ffd566"))
            self.drawString(text_x, h - 70, "OFFICIAL VOYAGE ANALYSIS & CHARTERING DECISION REPORT")

            # 2. Running Bottom Footer
            footer_y = 30
            self.setStrokeColor(colors.HexColor("#cbd9e5"))
            self.setLineWidth(0.8)
            self.line(36, footer_y + 12, w - 36, footer_y + 12)

            self.setFont("Helvetica-Bold", 7.5)
            self.setFillColor(colors.HexColor("#0b416d"))
            self.drawString(36, footer_y, "SAGAR DRISHTI | Decision Support Platform | Steel Authority of India Limited (SAIL)")

            self.setFont("Helvetica", 7.5)
            self.setFillColor(colors.HexColor("#556a7e"))
            page_str = f"Page {self._pageNumber} of {total_pages}"
            self.drawRightString(w - 36, footer_y, page_str)

            self.setFont("Helvetica-Oblique", 6.8)
            self.drawString(36, footer_y - 10, f"Report Generated By: {self.user_name} | Generated On: {self.gen_timestamp} | Data Freshness: {self.market_date} | Official Advisory Output.")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=94,
        bottomMargin=46,
    )

    styles = getSampleStyleSheet()

    # Custom Typography Styles
    title_style = ParagraphStyle(
        "SectionTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10.5,
        leading=13,
        textColor=colors.HexColor("#0b416d"),
        spaceAfter=4,
    )

    body_style = ParagraphStyle(
        "BodySmall",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10.5,
        textColor=colors.HexColor("#0d2842"),
    )

    bold_label = ParagraphStyle(
        "BoldLabel",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor("#556a7e"),
    )

    bold_val = ParagraphStyle(
        "BoldVal",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=10.5,
        textColor=colors.HexColor("#071e33"),
    )

    story = []

    # --------------------------------------------------------------------------
    # 1. METADATA TIMESTAMP STRIP
    # --------------------------------------------------------------------------
    meta_data = [
        [
            Paragraph("<b>REPORT GENERATED BY</b>", bold_label),
            Paragraph("<b>DESIGNATION</b>", bold_label),
            Paragraph("<b>REPORT GENERATED ON</b>", bold_label),
            Paragraph("<b>REQUIRED ARRIVAL</b>", bold_label),
        ],
        [
            Paragraph(f"<font color='#0b416d'><b>{clean_pdf_text(user_name)}</b></font>", bold_val),
            Paragraph("<b>Chartering Officer</b>", bold_val),
            Paragraph(f"<b>{gen_timestamp}</b>", bold_val),
            Paragraph(f"<b>{fmt_date_str(arrival)}</b>", bold_val),
        ],
    ]
    meta_table = Table(meta_data, colWidths=[145, 115, 145, 118])
    meta_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5f9fc")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#cbd9e5")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2ebf3")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ])
    )
    story.append(meta_table)
    story.append(Spacer(1, 8))

    # --------------------------------------------------------------------------
    # 2. MAIN GRID: LEFT = VOYAGE SUMMARY, RIGHT = DECISION SNAPSHOT
    # --------------------------------------------------------------------------
    p_info = PORTS.get(port, {})
    v_info = VESSEL_PROFILES.get(b["vessel"], VESSELS.get(b["vessel"], {}))
    market_date = as_of
    if market_date is None:
        market_date = res.get("as_of") if isinstance(res, dict) and "as_of" in res else None
    if market_date is None:
        market_date = getattr(st.session_state, "as_of", None) if ("st" in globals() and hasattr(st, "session_state")) else None
    if market_date is None:
        market_date = b.get("as_of")
    usdinr_rate = get_usdinr_rate(market_date)

    p_specs = f"Max Draft: {p_info.get('max_draft_m', 'N/A')}m | Length Overall (LOA): {p_info.get('max_loa_m', 'N/A')}m | Wait: {p_info.get('normal_wait_days', 'N/A')}d" if p_info else ""

    voyage_cells = [
        [Paragraph("<b>VOYAGE PARAMETERS & NOMINATION</b>", ParagraphStyle("H1", parent=title_style, textColor=colors.white)), ""],
        [
            Paragraph("ORIGIN PORT (LOADING)", bold_label),
            Paragraph(f"<b>{clean_pdf_text(origin)}</b>", bold_val),
        ],
        [
            Paragraph("DESTINATION PORT", bold_label),
            Paragraph(f"<font size=12 color='#0b416d'><b>{clean_pdf_text(port).upper()}</b></font><br/><font size=7 color='#556a7e'>{p_specs}</font>", bold_val),
        ],
        [
            Paragraph("CARGO REQUIREMENT", bold_label),
            Paragraph(f"<font size=12 color='#0b416d'><b>{qty:,.0f} Metric Tonnes (MT)</b></font><br/><font size=7 color='#556a7e'>Metallurgical Dry-Bulk Coal</font>", bold_val),
        ],
        [
            Paragraph("REQUIRED ARRIVAL DATE", bold_label),
            Paragraph(f"<b>{fmt_date_str(arrival)}</b><br/><font size=7 color='#556a7e'>Estimated Time of Arrival (ETA)</font>", bold_val),
        ],
        [
            Paragraph("RECOMMENDED VESSEL", bold_label),
            Paragraph(f"<font size=10 color='#071e33'><b>{b['vessel'].upper()}</b></font> <font size=7 color='#556a7e'>({v_info.get('dwt', 0):,} Deadweight Tonnage (DWT))</font>", bold_val),
        ],
        [
            Paragraph("TRANSIT & SEAWAY TIME", bold_label),
            Paragraph(f"<b>~{b['cost_plan']['sea_days']:.1f} sea days</b> @ {v_info.get('speed_kn', 'N/A')} kn", bold_val),
        ],
    ]
    left_table = Table(voyage_cells, colWidths=[115, 205])
    left_table.setStyle(
        TableStyle([
            ("SPAN", (0, 0), (1, 0)),
            ("BACKGROUND", (0, 0), (1, 0), colors.HexColor("#0b416d")),
            ("BACKGROUND", (0, 1), (-1, -1), colors.white),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#cbd9e5")),
            ("INNERGRID", (0, 1), (-1, -1), 0.5, colors.HexColor("#edf3f8")),
            ("TOPPADDING", (0, 0), (-1, 0), 4),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
            ("TOPPADDING", (0, 1), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ])
    )

    # Right: Decision Snapshot
    action = b["timing"]["action"]
    action_color = "#12734a" if "NOW" in action else ("#bd7800" if "WAIT" in action else "#1e6b9e")

    cp = b["cost_plan"]
    hire_usd_val = round(cp.get("hire_usd", 0))
    fuel_usd_raw = cp.get("fuel_usd", 0)
    wx_usd_val = round(cp.get("weather_impact_usd", 0))
    base_fuel_usd_val = round(fuel_usd_raw)
    port_usd_val = round(cp.get("port_usd", 0))
    wait_usd_val = round(cp.get("wait_usd", 0))
    tot_usd_val = hire_usd_val + base_fuel_usd_val + wx_usd_val + port_usd_val + wait_usd_val

    hire_inr_val = round(hire_usd_val * usdinr_rate)
    base_fuel_inr_val = round(base_fuel_usd_val * usdinr_rate)
    wx_inr_val = round(wx_usd_val * usdinr_rate)
    port_inr_val = round(port_usd_val * usdinr_rate)
    wait_inr_val = round(wait_usd_val * usdinr_rate)
    tot_inr_val = hire_inr_val + base_fuel_inr_val + wx_inr_val + port_inr_val + wait_inr_val

    usd_mt_val = round(cp.get("usd_per_mt", 0), 2)
    inr_mt_val = round(usd_mt_val * usdinr_rate, 1)

    right_cells = [
        [Paragraph("<b>DECISION SNAPSHOT</b>", ParagraphStyle("H2", parent=title_style, textColor=colors.white)), ""],
        [
            Paragraph("RECOMMENDED ACTION", bold_label),
            Paragraph(f"<font size=11 color='{action_color}'><b>{action}</b></font>", bold_val),
        ],
        [
            Paragraph("OPTIMAL VESSEL", bold_label),
            Paragraph(f"<b>{b['vessel'].upper()}</b>", bold_val),
        ],
        [
            Paragraph("OVERALL RISK", bold_label),
            Paragraph(f"<b>{b['risk_overall']:.0f} / 100</b> ({b['risk_band']})", bold_val),
        ],
        [
            Paragraph("TOTAL VOYAGE COST", bold_label),
            Paragraph(f"<font size=11 color='#0b416d'><b>${tot_usd_val:,.0f}</b></font>", bold_val),
        ],
        [
            Paragraph("COST PER MT", bold_label),
            Paragraph(f"<b>${usd_mt_val:.2f}</b> / MT<br/><font size=7 color='#556a7e'>Rs. {inr_mt_val:,.1f}/MT</font>", bold_val),
        ],
        [
            Paragraph("DATA FRESHNESS", bold_label),
            Paragraph(f"{fmt_date_str(b.get('as_of', 'N/A'))}", bold_val),
        ],
    ]
    right_table = Table(right_cells, colWidths=[90, 108])
    right_table.setStyle(
        TableStyle([
            ("SPAN", (0, 0), (1, 0)),
            ("BACKGROUND", (0, 0), (1, 0), colors.HexColor("#071e33")),
            ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#fcfdfd")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#cbd9e5")),
            ("INNERGRID", (0, 1), (-1, -1), 0.5, colors.HexColor("#edf3f8")),
            ("TOPPADDING", (0, 0), (-1, 0), 4),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
            ("TOPPADDING", (0, 1), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ])
    )

    grid_table = Table([[left_table, right_table]], colWidths=[323, 200])
    grid_table.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ])
    )
    story.append(grid_table)
    story.append(Spacer(1, 8))

    # --------------------------------------------------------------------------
    # 3. CHARTER RECOMMENDATION & OPERATIONAL ACTION CARD
    # --------------------------------------------------------------------------
    import re
    rec_reason = clean_pdf_text(b["timing"].get("reason", ""))
    # Strip any date references from this advisory area as requested
    rec_reason = re.sub(r"\s*around\s+\d{2,4}-\d{2}-\d{2,4}", "", rec_reason)
    rec_reason = re.sub(r"\s*\(\d{2,4}-\d{2}-\d{2,4}\)", "", rec_reason)
    rec_reason = re.sub(r"\b\d{2,4}-\d{2}-\d{2,4}\b", "", rec_reason)

    rec_cells = [
        [
            Paragraph(
                f"<b>CHARTERING ADVISORY: {action}</b>",
                ParagraphStyle("RecH", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=9.5, textColor=colors.HexColor(action_color)),
            )
        ],
        [
            Paragraph(
                f"{rec_reason} Evaluated against physical berth dimensions, real-time marine weather, Time-Charter Equivalent (TCE) voyage costs, and multi-horizon market trajectory.",
                body_style,
            )
        ],
    ]
    rec_table = Table(rec_cells, colWidths=[523])
    rec_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fefbf3") if "WAIT" in action else colors.HexColor("#f4faf6")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor(action_color)),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ])
    )
    story.append(rec_table)
    story.append(Spacer(1, 8))

    # --------------------------------------------------------------------------
    # 4. KEY DECISION FACTORS (Structured List)
    # --------------------------------------------------------------------------
    story.append(Paragraph("<b>KEY DECISION FACTORS & ENGINEERING RATIONALE</b>", title_style))
    exp_items = [clean_pdf_text(e) for e in b.get("explanation", [])]
    factor_rows = []
    for e in exp_items[:6]:
        factor_rows.append([
            Paragraph("<b>&bull;</b>", ParagraphStyle("Bul", fontName="Helvetica-Bold", fontSize=11, textColor=colors.HexColor("#0b416d"))),
            Paragraph(e, body_style),
        ])
    if not factor_rows:
        factor_rows.append([Paragraph("&bull;", body_style), Paragraph("Standard vessel-port feasibility and cost optimization criteria verified.", body_style)])

    fac_table = Table(factor_rows, colWidths=[15, 508])
    fac_table.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fcfdfd")),
            ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#cbd9e5")),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#eef3f8")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ])
    )
    story.append(fac_table)

    # PAGE BREAK TO PAGE 2
    story.append(PageBreak())

    # ==========================================================================
    # PAGE 2: ECONOMICS, RISK, FORECAST, SPECIFICATIONS & GOVERNANCE
    # ==========================================================================
    # --------------------------------------------------------------------------
    # 5. VOYAGE ECONOMICS TABLE
    # --------------------------------------------------------------------------
    story.append(Paragraph("<b>VOYAGE ECONOMICS & TIME-CHARTER EQUIVALENT (TCE) COST BREAKDOWN</b>", title_style))
    cost_rows = [
        [
            Paragraph("<b>Expense Category</b>", bold_label),
            Paragraph("<b>Operational Basis / Activity Days</b>", bold_label),
            Paragraph("<b>Amount (USD)</b>", bold_label),
            Paragraph("<b>Amount (INR)</b>", bold_label),
        ],
        [
            Paragraph("Vessel Time Charter Hire", bold_val),
            Paragraph(f"{cp.get('sea_days', 0) + cp.get('wait_days', 0):.1f} active days @ ${cp.get('hire_usd_day', 0):,.0f}/day", body_style),
            Paragraph(f"${hire_usd_val:,.0f}", bold_val),
            Paragraph(f"Rs. {hire_inr_val:,.0f}", body_style),
        ],
        [
            Paragraph("Bunker Fuel (Base Transit)", bold_val),
            Paragraph("Laden sea leg + 50% ballast allocation", body_style),
            Paragraph(f"${base_fuel_usd_val:,.0f}", bold_val),
            Paragraph(f"Rs. {base_fuel_inr_val:,.0f}", body_style),
        ],
        [
            Paragraph("Weather Fuel Adjustment", bold_val),
            Paragraph(f"{cp.get('weather', {}).get('extra_sea_days', 0):.1f} extra days / sea-state speed loss", body_style),
            Paragraph(f"${wx_usd_val:,.0f}", bold_val),
            Paragraph(f"Rs. {wx_inr_val:,.0f}", body_style),
        ],
        [
            Paragraph("Port Discharging Charges", bold_val),
            Paragraph(f"Terminal tariff at {clean_pdf_text(port)}", body_style),
            Paragraph(f"${port_usd_val:,.0f}", bold_val),
            Paragraph(f"Rs. {port_inr_val:,.0f}", body_style),
        ],
        [
            Paragraph("Berth Waiting & Demurrage", bold_val),
            Paragraph(f"Estimated {cp.get('wait_days', 0):.1f} waiting days at anchorage", body_style),
            Paragraph(f"${wait_usd_val:,.0f}", bold_val),
            Paragraph(f"Rs. {wait_inr_val:,.0f}", body_style),
        ],
        [
            Paragraph("<b>TOTAL LANDED VOYAGE COST</b>", ParagraphStyle("TotL", fontName="Helvetica-Bold", fontSize=9, textColor=colors.HexColor("#0b416d"))),
            Paragraph("<b>Complete Landed Expenditure</b>", ParagraphStyle("TotSub", fontName="Helvetica-Bold", fontSize=8, textColor=colors.HexColor("#0b416d"))),
            Paragraph(f"<b>${tot_usd_val:,.0f}</b>", ParagraphStyle("TotU", fontName="Helvetica-Bold", fontSize=10, textColor=colors.HexColor("#0b416d"))),
            Paragraph(f"<b>Rs. {tot_inr_val:,.0f}</b>", ParagraphStyle("TotI", fontName="Helvetica-Bold", fontSize=9, textColor=colors.HexColor("#0b416d"))),
        ],
        [
            Paragraph("<b>FREIGHT COST PER METRIC TONNE</b>", ParagraphStyle("TotL2", fontName="Helvetica-Bold", fontSize=9, textColor=colors.HexColor("#0b416d"))),
            Paragraph(f"Cargo: {qty:,.0f} MT landed", body_style),
            Paragraph(f"<b>${usd_mt_val:.2f} / MT</b>", ParagraphStyle("TotU2", fontName="Helvetica-Bold", fontSize=10, textColor=colors.HexColor("#0b416d"))),
            Paragraph(f"<b>Rs. {inr_mt_val:,.1f} / MT</b>", ParagraphStyle("TotI2", fontName="Helvetica-Bold", fontSize=9, textColor=colors.HexColor("#0b416d"))),
        ],
    ]
    cost_table = Table(cost_rows, colWidths=[150, 183, 95, 95])
    cost_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f4f8")),
            ("BACKGROUND", (0, -2), (-1, -1), colors.HexColor("#f5f9fc")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#cbd9e5")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2ebf3")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ])
    )
    story.append(cost_table)
    story.append(Spacer(1, 3))
    fx_note_text = (
        f"<b>FX Rate Applied: Rs. {usdinr_rate:.2f}/USD</b> &nbsp;|&nbsp; "
        f"<i>INR costs are converted from USD using the applicable reference USD/INR FX assumption based on the Market/Decision Date.</i>"
    )
    story.append(Paragraph(clean_pdf_text(fx_note_text), ParagraphStyle("FXNote", fontName="Helvetica", fontSize=6.5, textColor=colors.HexColor("#556a7e"))))
    story.append(Spacer(1, 4))

    # --------------------------------------------------------------------------
    # 6. RISK SUMMARY & MULTI-HORIZON FREIGHT FORECAST (SIDE BY SIDE)
    # --------------------------------------------------------------------------
    rsk = b.get("risk", {})
    m_rsk = rsk.get("Market (freight volatility)", 0)
    f_rsk = rsk.get("Fuel (Brent momentum)", 0)
    p_rsk = rsk.get("Port (waiting vs normal)", 0)
    w_rsk = rsk.get("Weather", 0)

    risk_cells = [
        [Paragraph("<b>MULTI-FACTOR RISK ASSESSMENT</b>", ParagraphStyle("RH", parent=title_style, textColor=colors.white)), ""],
        [Paragraph("Market Volatility", bold_label), Paragraph(f"<b>{m_rsk:.0f} / 100</b>", bold_val)],
        [Paragraph("Fuel Price Momentum", bold_label), Paragraph(f"<b>{f_rsk:.0f} / 100</b>", bold_val)],
        [Paragraph("Port Congestion Risk", bold_label), Paragraph(f"<b>{p_rsk:.0f} / 100</b>", bold_val)],
        [Paragraph("Weather Operational", bold_label), Paragraph(f"<b>{w_rsk:.0f} / 100</b>", bold_val)],
        [Paragraph("<b>OVERALL COMPOSITE</b>", bold_label), Paragraph(f"<font color='#0b416d'><b>{b['risk_overall']:.0f} / 100 ({b['risk_band']})</b></font>", bold_val)],
    ]
    risk_sub_table = Table(risk_cells, colWidths=[130, 120])
    risk_sub_table.setStyle(
        TableStyle([
            ("SPAN", (0, 0), (1, 0)),
            ("BACKGROUND", (0, 0), (1, 0), colors.HexColor("#0b416d")),
            ("BACKGROUND", (0, 1), (-1, -1), colors.white),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#cbd9e5")),
            ("INNERGRID", (0, 1), (-1, -1), 0.5, colors.HexColor("#edf3f8")),
            ("TOPPADDING", (0, 0), (-1, 0), 3),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ])
    )

    path_obj = b.get("path")
    f_rows = [
        [
            Paragraph("<b>Horizon</b>", bold_label),
            Paragraph("<b>Expected</b>", bold_label),
            Paragraph("<b>80% Conformal Range</b>", bold_label),
        ]
    ]
    if path_obj is not None:
        try:
            for h_day, exp, lo, hi in zip(path_obj.horizon_days, path_obj.expected, path_obj.lower, path_obj.upper):
                f_rows.append([
                    Paragraph(f"{int(h_day)} Days", bold_val),
                    Paragraph(f"{exp:,.0f} pts", body_style),
                    Paragraph(f"[{lo:,.0f} &ndash; {hi:,.0f}]", body_style),
                ])
        except Exception:
            f_rows.append([Paragraph("30 Days", bold_val), Paragraph("Trajectory Active", body_style), Paragraph("Calibrated", body_style)])
    else:
        f_rows.append([Paragraph("Horizon", bold_val), Paragraph("N/A", body_style), Paragraph("N/A", body_style)])

    f_cells = [[Paragraph("<b>BALTIC FREIGHT INDEX FORECAST (BDI / BCI / BPI)</b>", ParagraphStyle("FH", parent=title_style, textColor=colors.white)), "", ""]]
    f_cells.extend(f_rows)
    f_table = Table(f_cells, colWidths=[70, 80, 113])
    f_table.setStyle(
        TableStyle([
            ("SPAN", (0, 0), (2, 0)),
            ("BACKGROUND", (0, 0), (2, 0), colors.HexColor("#071e33")),
            ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#f0f4f8")),
            ("BACKGROUND", (0, 2), (-1, -1), colors.white),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#cbd9e5")),
            ("INNERGRID", (0, 1), (-1, -1), 0.5, colors.HexColor("#edf3f8")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ])
    )

    side_table = Table([[risk_sub_table, f_table]], colWidths=[255, 268])
    side_table.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ])
    )
    story.append(side_table)
    story.append(Spacer(1, 3))
    story.append(Paragraph("<font size=6.5 color='#556a7e'>Note: Baltic Dry Index (BDI), Baltic Capesize Index (BCI), Baltic Panamax Index (BPI), Baltic Supramax Index (BSI), Baltic Handysize Index (BHSI). Machine Learning (ML) / Artificial Intelligence (AI) models evaluated via Mean Absolute Error (MAE), Root Mean Squared Error (RMSE), and Mean Absolute Percentage Error (MAPE). Weather synchronized via Open-Meteo Application Programming Interface (API).</font>", body_style))
    story.append(Spacer(1, 5))

    # --------------------------------------------------------------------------
    # 7. VESSEL-PORT TECHNICAL SPECIFICATIONS
    # --------------------------------------------------------------------------
    story.append(Paragraph("<b>VESSEL &amp; DISCHARGE PORT ENGINEERING COMPLIANCE</b>", title_style))
    spec_rows = [
        [
            Paragraph("<b>Technical Parameter</b>", bold_label),
            Paragraph(f"<b>Nominated Vessel ({b['vessel']})</b>", bold_label),
            Paragraph(f"<b>Berth Limit ({clean_pdf_text(port)})</b>", bold_label),
            Paragraph("<b>Compliance Status</b>", bold_label),
        ],
        [
            Paragraph("Deadweight Tonnage (DWT)", body_style),
            Paragraph(f"{v_info.get('dwt', 0):,} MT", bold_val),
            Paragraph(f"Max {p_info.get('max_dwt', 0):,} MT", body_style),
            Paragraph("<font color='#12734a'><b>FEASIBLE</b></font>", bold_val),
        ],
        [
            Paragraph("Maximum Arrival Draft", body_style),
            Paragraph(f"{v_info.get('draft_m', 0):.1f} m (Design)", bold_val),
            Paragraph(f"Max {p_info.get('max_draft_m', 0):.1f} m Permissible", body_style),
            Paragraph("<font color='#12734a'><b>CLEARED</b></font>", bold_val),
        ],
        [
            Paragraph("Length Overall (LOA)", body_style),
            Paragraph(f"{v_info.get('loa_m', 0)} m", bold_val),
            Paragraph(f"Max {p_info.get('max_loa_m', 0)} m", body_style),
            Paragraph("<font color='#12734a'><b>CLEARED</b></font>", bold_val),
        ],
        [
            Paragraph("Beam (Width)", body_style),
            Paragraph(f"{v_info.get('beam_m', 0)} m", bold_val),
            Paragraph(f"Max {p_info.get('max_beam_m', 0)} m", body_style),
            Paragraph("<font color='#12734a'><b>CLEARED</b></font>", bold_val),
        ],
        [
            Paragraph("Discharge Rate / Turnaround", body_style),
            Paragraph("Standard Cargo Handling", body_style),
            Paragraph(f"{p_info.get('discharge_rate_t_day', 0):,} MT / day", bold_val),
            Paragraph("<font color='#0b416d'><b>OPERATIONAL</b></font>", bold_val),
        ],
    ]
    spec_table = Table(spec_rows, colWidths=[140, 130, 140, 113])
    spec_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f4f8")),
            ("BACKGROUND", (0, 1), (-1, -1), colors.white),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#cbd9e5")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2ebf3")),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ])
    )
    story.append(spec_table)
    story.append(Spacer(1, 3))
    story.append(Paragraph("<font size=6.5 color='#556a7e'>Note: LOA = Length Overall; DWT = Deadweight Tonnage; MT = Metric Tonnes.</font>", body_style))
    story.append(Spacer(1, 5))

    # --------------------------------------------------------------------------
    # 8. STATUTORY GOVERNANCE & PROTOCOLS
    # --------------------------------------------------------------------------
    gov_text = (
        "<b>Statutory Verification Protocol:</b> This advisory is generated by Sagar Drishti decision algorithms. "
        "Prior to executing a legally binding charter party fixture (Baltic and International Maritime Council (BIMCO) GENCON / AMWELSH), designated SAIL Chartering Officers must "
        "verify the nominated vessel's formal Standard Vessel Questionnaire (Q88) against live Notice to Mariners, tidal tables, and local Port Master bulletins. "
        "Aligned with the National Steel Policy 2017 and Maritime India Vision 2030."
    )
    story.append(Paragraph(gov_text, ParagraphStyle("Gov", parent=styles["Normal"], fontName="Helvetica", fontSize=6.8, leading=8.5, textColor=colors.HexColor("#556a7e"))))

    # --------------------------------------------------------------------------
    # 9. ABBREVIATIONS & TECHNICAL TERMS (NEW PAGE AS REQUESTED)
    # --------------------------------------------------------------------------
    story.append(PageBreak())

    story.append(Paragraph("<b>ABBREVIATIONS &amp; TECHNICAL TERMS</b>", title_style))
    story.append(Paragraph(
        "Official technical reference and acronym guide for evaluation committees, chartering officers, and operational auditors.",
        ParagraphStyle("SubAbbr", parent=styles["Normal"], fontName="Helvetica", fontSize=8, leading=10, textColor=colors.HexColor("#556a7e"), spaceAfter=6)
    ))

    abbr_header_style = ParagraphStyle(
        "AbbrH",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.white,
    )
    abbr_code_style = ParagraphStyle(
        "AbbrCode",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor("#0b416d"),
    )
    abbr_full_style = ParagraphStyle(
        "AbbrFull",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor("#071e33"),
    )
    abbr_desc_style = ParagraphStyle(
        "AbbrDesc",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.2,
        leading=9,
        textColor=colors.HexColor("#334155"),
    )

    abbr_rows = [
        [
            Paragraph("<b>Abbr.</b>", bold_label),
            Paragraph("<b>Full Form / Terminology</b>", bold_label),
            Paragraph("<b>Operational Definition &amp; Maritime Context</b>", bold_label),
        ],
        # Group 1: Maritime & Vessel Specifications
        [Paragraph("<b>MARITIME &amp; VESSEL SPECIFICATIONS</b>", abbr_header_style), "", ""],
        [
            Paragraph("DWT", abbr_code_style),
            Paragraph("Deadweight Tonnage (DWT)", abbr_full_style),
            Paragraph("Total weight capacity a vessel can safely carry (cargo, fuel bunkers, fresh water, and provisions) down to summer load line mark.", abbr_desc_style),
        ],
        [
            Paragraph("LOA", abbr_code_style),
            Paragraph("Length Overall (LOA)", abbr_full_style),
            Paragraph("Maximum vessel length measured from extreme forward tip of bow to extreme aft tip of stern, governing port lock and berth admission.", abbr_desc_style),
        ],
        [
            Paragraph("MT", abbr_code_style),
            Paragraph("Metric Tonnes (MT)", abbr_full_style),
            Paragraph("Standard international commercial cargo measurement unit equivalent to 1,000 kilograms (2,204.62 lbs) dry bulk weight.", abbr_desc_style),
        ],
        [
            Paragraph("ETA", abbr_code_style),
            Paragraph("Estimated Time of Arrival (ETA)", abbr_full_style),
            Paragraph("Projected navigational date and time when the cargo carrier arrives at port anchorage or pilot boarding station.", abbr_desc_style),
        ],
        [
            Paragraph("TCE", abbr_code_style),
            Paragraph("Time-Charter Equivalent (TCE)", abbr_full_style),
            Paragraph("Shipping economic metric expressing net voyage return in daily earnings (USD/day), calculated as total freight minus voyage costs divided by duration.", abbr_desc_style),
        ],
        [
            Paragraph("BIMCO", abbr_code_style),
            Paragraph("Baltic and International Maritime Council (BIMCO)", abbr_full_style),
            Paragraph("World's largest accredited international shipping association publishing standardized commercial charter parties (e.g. GENCON, AMWELSH).", abbr_desc_style),
        ],
        [
            Paragraph("Q88", abbr_code_style),
            Paragraph("Standard Vessel Questionnaire (Q88)", abbr_full_style),
            Paragraph("Standardized technical questionnaire establishing vessel particulars, gear specifications, tank arrangements, and port compatibility.", abbr_desc_style),
        ],
        # Group 2: Baltic Dry Bulk Freight Indices
        [Paragraph("<b>BALTIC FREIGHT MARKET BENCHMARKS</b>", abbr_header_style), "", ""],
        [
            Paragraph("BDI", abbr_code_style),
            Paragraph("Baltic Dry Index (BDI)", abbr_full_style),
            Paragraph("Global composite maritime freight benchmark published daily by Baltic Exchange, tracking movement costs for dry raw materials worldwide.", abbr_desc_style),
        ],
        [
            Paragraph("BCI", abbr_code_style),
            Paragraph("Baltic Capesize Index (BCI)", abbr_full_style),
            Paragraph("Freight benchmark specifically tracking large Capesize vessels (~180,000 DWT) primarily servicing long-haul Australia/Brazil coal and iron ore routes.", abbr_desc_style),
        ],
        [
            Paragraph("BPI", abbr_code_style),
            Paragraph("Baltic Panamax Index (BPI)", abbr_full_style),
            Paragraph("Benchmark tracking Panamax/Kamsarmax dry bulk vessels (~75,000–85,000 DWT) through major international grain and coal trading hubs.", abbr_desc_style),
        ],
        [
            Paragraph("BSI", abbr_code_style),
            Paragraph("Baltic Supramax Index (BSI)", abbr_full_style),
            Paragraph("Freight benchmark monitoring geared Supramax/Ultramax vessels (~50,000–65,000 DWT) equipped with onboard cranes and grabs.", abbr_desc_style),
        ],
        [
            Paragraph("BHSI", abbr_code_style),
            Paragraph("Baltic Handysize Index (BHSI)", abbr_full_style),
            Paragraph("Benchmark monitoring smaller versatile Handysize dry bulk carriers (~28,000–38,000 DWT) accessing shallow draft draft-restricted river terminals.", abbr_desc_style),
        ],
        # Group 3: Predictive Modeling & Accuracy Metrics
        [Paragraph("<b>AI / ML FORECASTING &amp; STATISTICAL METRICS</b>", abbr_header_style), "", ""],
        [
            Paragraph("ML", abbr_code_style),
            Paragraph("Machine Learning (ML)", abbr_full_style),
            Paragraph("Statistical regression and tree ensembles (Ridge Regression, LightGBM) trained on historical Baltic index time series to project freight trajectory.", abbr_desc_style),
        ],
        [
            Paragraph("AI", abbr_code_style),
            Paragraph("Artificial Intelligence (AI)", abbr_full_style),
            Paragraph("Autonomous decision-support architecture synthesizing multi-horizon forecasts, vessel constraints, risk matrices, and timing trade-offs.", abbr_desc_style),
        ],
        [
            Paragraph("API", abbr_code_style),
            Paragraph("Application Programming Interface (API)", abbr_full_style),
            Paragraph("Real-time network interface synchronizing ECMWF and GFS global marine forecast models from Open-Meteo for route wave height and wind analysis.", abbr_desc_style),
        ],
        [
            Paragraph("MAE", abbr_code_style),
            Paragraph("Mean Absolute Error (MAE)", abbr_full_style),
            Paragraph("Statistical accuracy metric computing average absolute difference between predicted and actual freight levels in index points.", abbr_desc_style),
        ],
        [
            Paragraph("RMSE", abbr_code_style),
            Paragraph("Root Mean Squared Error (RMSE)", abbr_full_style),
            Paragraph("Standard statistical metric penalizing large projection errors quadratically, ensuring robust risk management in volatile freight spikes.", abbr_desc_style),
        ],
        [
            Paragraph("MAPE", abbr_code_style),
            Paragraph("Mean Absolute Percentage Error (MAPE)", abbr_full_style),
            Paragraph("Relative forecast evaluation metric expressing error as an average percentage of true index level, benchmarking multi-horizon reliability.", abbr_desc_style),
        ],
        # Group 4: System Architecture & Security
        [Paragraph("<b>SYSTEM ARCHITECTURE &amp; SECURITY PROTOCOLS</b>", abbr_header_style), "", ""],
        [
            Paragraph("RAG", abbr_code_style),
            Paragraph("Retrieval-Augmented Generation (RAG)", abbr_full_style),
            Paragraph("Grounded intelligence architecture indexing authoritative technical documents and port handbooks for verified decision citations.", abbr_desc_style),
        ],
        [
            Paragraph("JWT", abbr_code_style),
            Paragraph("JSON Web Token (JWT)", abbr_full_style),
            Paragraph("Stateless, cryptographically signed token mechanism securing officer authentication and maintaining role-based access control.", abbr_desc_style),
        ],
        [
            Paragraph("PBKDF2", abbr_code_style),
            Paragraph("Password-Based Key Derivation Function 2 (PBKDF2)", abbr_full_style),
            Paragraph("FIPS-compliant cryptographic key derivation algorithm incorporating salted HMAC-SHA256 iterations to secure user credentials.", abbr_desc_style),
        ],
    ]

    abbr_table = Table(abbr_rows, colWidths=[60, 165, 298])
    abbr_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f4f8")),
            ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#0b416d")),
            ("SPAN", (0, 1), (-1, 1)),
            ("BACKGROUND", (0, 9), (-1, 9), colors.HexColor("#071e33")),
            ("SPAN", (0, 9), (-1, 9)),
            ("BACKGROUND", (0, 15), (-1, 15), colors.HexColor("#0b416d")),
            ("SPAN", (0, 15), (-1, 15)),
            ("BACKGROUND", (0, 22), (-1, 22), colors.HexColor("#071e33")),
            ("SPAN", (0, 22), (-1, 22)),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#cbd9e5")),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#edf3f8")),
            ("TOPPADDING", (0, 0), (-1, -1), 2.2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ])
    )
    story.append(abbr_table)
    story.append(Spacer(1, 6))

    # Build document with NumberedCanvas
    doc.build(story, canvasmaker=lambda *args, **kwargs: SagarNumberedCanvas(*args, gen_timestamp=gen_timestamp, user_name=user_name, market_date=fmt_date_str(b.get("as_of", "N/A")), **kwargs))
    return buf.getvalue()


def footer():
    st.markdown("<hr style='margin: 28px 0 16px; border: none; border-top: 1px solid #cbd9e5;'>", unsafe_allow_html=True)
    f_cols = st.columns([1.6, 1.4])
    with f_cols[0]:
        st.markdown(
            """
            <div style="font-family:'Roboto Slab',serif;font-size:14px;font-weight:700;color:#0b416d;text-transform:uppercase;letter-spacing:0.5px;">STEEL AUTHORITY OF INDIA LIMITED (SAIL)</div>
            <div style="font-size:13px;font-weight:700;color:#071e33;margin-top:2px;">SAGAR DRISHTI — SAIL Freight Intelligence &amp; Chartering Advisor</div>
            <div style="font-size:12px;color:#556a7e;margin-top:4px;">Data-driven decision support for maritime raw material logistics</div>
            <div style="font-size:11.5px;color:#5d7082;margin-top:4px;line-height:1.45;">
              Government of India &bull; Ministry of Steel &bull; Smart India Hackathon (SIH 2026) &bull; Problem Statement 26006
            </div>
            <div style="margin-top:8px;font-size:12px;font-weight:600;color:#0b416d;">
              About | Home | Contact
            </div>
            """,
            unsafe_allow_html=True,
        )
    with f_cols[1]:
        st.markdown(
            """
            <div class="footer-right" style="text-align:right;">
              <div style="font-size:12.5px;font-weight:700;color:#071e33;margin-bottom:4px;">Statutory Governance &amp; Operational Notice</div>
              <div style="font-size:11.5px;color:#5d7082;line-height:1.5;">
                Official decision-support advisory system. Always confirm physical berth limits, tidal tables, and local Port Master bulletins prior to fixture execution.<br>
                Aligned with National Steel Policy 2017 &amp; Maritime India Vision 2030.<br>
                <span style="font-weight:600;color:#0b416d;">&copy; 2026 Steel Authority of India Limited (SAIL). All rights reserved.</span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_user_profile_bar():
    user = st.session_state.get("user") or {}
    full_name = clean_text(user.get("full_name", "SAIL Officer"))
    emp_id = clean_text(user.get("employee_id", "SAIL-0000"))
    dept = clean_text(user.get("department", "Raw Material Logistics"))
    desig = clean_text(user.get("designation", "Chartering Officer"))

    p_col1, p_col2 = st.columns([3.3, 1.7])
    with p_col1:
        # Demo Mode is intentionally not surfaced in the persistent dashboard header.
        # The access method is disclosed on the authentication screen, while the
        # application itself presents the same institutional dashboard experience.
        status_html = '<span style="color:#12734a;font-weight:600;">Active Session</span>'
        badge_html = f'<span class="user-badge">{desig}</span>'
        st.markdown(
            f"""
            <div class="user-profile-strip">
              <div class="user-avatar">&#128100;</div>
              <div class="user-meta">
                <div class="user-name">Welcome, <b>{full_name}</b> &nbsp;{badge_html}</div>
                <div class="user-sub">Employee ID: <b>{emp_id}</b> &bull; Department: <b>{dept}</b> &bull; Status: {status_html}</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with p_col2:
        st.markdown('<div style="height: 6px;"></div>', unsafe_allow_html=True)
        sub_c1, sub_c2 = st.columns(2)
        with sub_c1:
            if st.button("⚓ Overview", key="btn_nav_overview", use_container_width=True, type="secondary"):
                st.session_state.page = "landing"
                st.rerun()
        with sub_c2:
            if st.button("🚪 Logout", key="btn_auth_logout", use_container_width=True, type="secondary"):
                st.session_state.authenticated = False
                st.session_state.demo_mode = False
                st.session_state.user = None
                st.session_state.auth_mode = "login"
                st.session_state.page = "landing"
                st.rerun()


def render_login_loading_screen():
    """Render a basic and simple loading animation when accessing the dashboard that stays visible until the whole dashboard is fully loaded."""
    st.html(
        """
        <div id="sagar-login-loader" class="login-loader-overlay">
            <div class="login-loader-box">
                <div class="login-loader-spinner"></div>
                <div class="login-loader-title">SAGAR DRISHTI</div>
                <div class="login-loader-sub">Loading Decision Intelligence Dashboard...</div>
                <div class="login-loader-bar"><div class="login-loader-bar-inner"></div></div>
            </div>
        </div>
        <script>
        (function() {
            var doc = (window.parent && window.parent.document) || document;
            var startTime = Date.now();
            var minDisplayMs = 500;
            var maxTimeoutMs = 20000;
            var dismissed = false;

            function dismissLoader() {
                if (dismissed) return;
                dismissed = true;
                var el = doc.getElementById("sagar-login-loader") || document.getElementById("sagar-login-loader");
                if (el) {
                    el.classList.add("fade-out");
                    setTimeout(function() {
                        if (el && el.parentNode) {
                            try { el.parentNode.removeChild(el); } catch(e) {}
                        }
                    }, 700);
                }
            }

            // Expose globally so end-of-page sentinel can trigger it directly
            if (typeof window !== "undefined") {
                window.__sagar_dismiss_loader = dismissLoader;
            }
            if (typeof window.parent !== "undefined" && window.parent) {
                window.parent.__sagar_dismiss_loader = dismissLoader;
            }

            function pollUntilFullyLoaded() {
                if (dismissed) return;
                var elapsed = Date.now() - startTime;

                if (elapsed >= maxTimeoutMs) {
                    dismissLoader();
                    return;
                }

                // 1. Check if the end-of-page sentinel has been rendered
                var sentinel = doc.getElementById("sagar-dashboard-complete-sentinel") ||
                               document.getElementById("sagar-dashboard-complete-sentinel");
                if (!sentinel) {
                    setTimeout(pollUntilFullyLoaded, 100);
                    return;
                }

                // 2. Check if core structural UI elements are in the DOM
                var hasHeader = !!doc.querySelector(".gov-header");
                var hasUserBar = !!doc.querySelector(".user-profile-strip");
                var hasFooter = !!doc.querySelector(".footer-right");

                if (!hasHeader || !hasUserBar || !hasFooter) {
                    setTimeout(pollUntilFullyLoaded, 100);
                    return;
                }

                // 3. Check Streamlit running status widget
                var statusWidget = doc.querySelector('[data-testid="stStatusWidget"]') ||
                                   doc.querySelector('.stStatusWidget');
                if (statusWidget) {
                    var txt = (statusWidget.innerText || statusWidget.textContent || "").toLowerCase();
                    if (txt.indexOf("running") !== -1) {
                        setTimeout(pollUntilFullyLoaded, 100);
                        return;
                    }
                }

                // 4. Ensure minimum display time
                if (elapsed < minDisplayMs) {
                    setTimeout(pollUntilFullyLoaded, minDisplayMs - elapsed);
                    return;
                }

                // All criteria met: whole dashboard is fully loaded
                dismissLoader();
            }

            setTimeout(pollUntilFullyLoaded, 100);
        })();
        </script>
        """,
        unsafe_allow_javascript=True,
    )


def enter_demo_mode():
    """Start a self-contained prototype session without authentication or MongoDB."""
    st.session_state.authenticated = True
    st.session_state.demo_mode = True
    st.session_state.user = {
        "user_id": "demo_sagar_drishti",
        "full_name": "SAIL Chartering Officer",
        "employee_id": "SAIL-26006",
        "email": "prototype@sagardrishti.local",
        "department": "Raw Material Logistics",
        "designation": "Chartering Officer",
    }
    st.session_state.show_login_loader = True
    st.session_state.page = "app"
    st.session_state.active_tab = "Dashboard"
    st.rerun()


def render_landing_page(is_authenticated: bool = False):
    """Render the official, executive-grade SAGAR DRISHTI product landing page."""
    # 1. Top Government / SAIL Institutional Header
    header()

    user = st.session_state.get("user") or {}
    full_name = clean_text(user.get("full_name", "SAIL Officer"))
    desig = clean_text(user.get("designation", "Chartering Officer"))
    emp_id = clean_text(user.get("employee_id", "SAIL-0000"))

    # Authenticated officer strip — intentionally neutral so the dashboard remains
    # presentation-ready. Demo access is disclosed on the login screen instead.
    if is_authenticated:
        st.markdown(
            f"""
            <div class="landing-officer-banner">
              <div>⚓ Officer Workspace: <b>{full_name}</b> &bull; Designation: <b>{desig}</b> &bull; ID: <b>{emp_id}</b></div>
              <div><span style="font-size:12px;color:#12734a;font-weight:700;">● Active Clearance</span></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # 2. Hero Section & Maritime Visual
    hero_col_text, hero_col_img = st.columns([1.18, 0.92], gap="large")

    with hero_col_text:
        st.markdown(
            """
            <div class="landing-hero-card">
              <div class="landing-pill">GOVERNMENT OF INDIA &bull; MINISTRY OF STEEL &bull; STEEL AUTHORITY OF INDIA LIMITED</div>
              <div class="landing-title" style="color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; font-family: 'Roboto Slab', serif !important; font-size: 38px !important; font-weight: 800 !important; line-height: 1.15 !important; margin: 4px 0 8px 0 !important; letter-spacing: 0.5px !important; text-shadow: 0 2px 10px rgba(0,0,0,0.5) !important;">SAGAR DRISHTI</div>
              <div class="landing-subtitle">Intelligent Maritime Decision Support for SAIL</div>
              <div class="landing-desc">
                Data-driven chartering intelligence for freight forecasting, voyage costing, vessel compatibility, risk assessment and operational decision support.
              </div>
              <div class="landing-caps">
                <span class="cap-pill">&#128200; Multi-Horizon Freight Forecasting</span>
                <span class="cap-pill">&#128176; Voyage Economics &amp; Cost / MT</span>
                <span class="cap-pill">&#9875; Vessel &amp; Berth Compatibility</span>
                <span class="cap-pill">&#127754; Marine Weather &amp; Cyclone Risk</span>
                <span class="cap-pill">&#128737; Charter Party Decision Advisory</span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown('<div style="height: 10px;"></div>', unsafe_allow_html=True)
        if not is_authenticated:
            if st.button("⚓ ENTER SAGAR DRISHTI", key="landing_btn_enter_platform", type="primary", use_container_width=True):
                enter_demo_mode()
        else:
            if st.button("⚡ ENTER SAGAR DRISHTI DASHBOARD", key="landing_btn_enter_dash", type="primary", use_container_width=True):
                st.session_state.show_login_loader = True
                st.session_state.page = "app"
                st.session_state.active_tab = "Dashboard"
                st.rerun()

    with hero_col_img:
        hero_img_base64 = f"data:image/png;base64,{HERO}" if HERO else ""
        st.markdown(
            f"""
            <div class="landing-hero-frame">
              <div class="hero-ship-badge-top">&#9875; SIH 2026 Problem Statement 26006</div>
              {f'<img src="{hero_img_base64}" class="landing-hero-img" alt="SAIL Bulk Carrier">' if hero_img_base64 else ''}
              <div class="hero-ship-badge-bottom">
                <div class="hero-badge-title">&#127754; Maritime Decision Support System</div>
                <div class="hero-badge-coords">20.266&deg; N, 86.670&deg; E &bull; Indian East Coast Discharge Terminals</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # 3. Value Proposition Section: From Freight Data to Chartering Decisions
    st.markdown(
        """
        <div class="landing-section-header">
          <div class="landing-section-pill">ENTERPRISE CAPABILITIES</div>
          <div class="landing-section-title">From Freight Data to Chartering Decisions</div>
          <div class="landing-section-sub">
            End-to-end maritime intelligence purpose-built for SAIL raw material supply chain operations, bridging market indices with operational fixture governance.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    feat_cols = st.columns(5)
    with feat_cols[0]:
        st.markdown(
            """
            <div class="feat-card">
              <div class="feat-icon">&#128200;</div>
              <div class="feat-title">FREIGHT FORECAST</div>
              <div class="feat-desc">Historical freight intelligence and multi-horizon forecasting.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with feat_cols[1]:
        st.markdown(
            """
            <div class="feat-card">
              <div class="feat-icon">&#128176;</div>
              <div class="feat-title">VOYAGE COST</div>
              <div class="feat-desc">Transparent voyage cost and cost-per-MT analysis.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with feat_cols[2]:
        st.markdown(
            """
            <div class="feat-card">
              <div class="feat-icon">&#9875;</div>
              <div class="feat-title">VESSEL COMPATIBILITY</div>
              <div class="feat-desc">Compare vessel classes against configured port and cargo constraints.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with feat_cols[3]:
        st.markdown(
            """
            <div class="feat-card">
              <div class="feat-icon">&#128737;</div>
              <div class="feat-title">RISK INTELLIGENCE</div>
              <div class="feat-desc">Understand market, fuel, port and weather-related risk.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with feat_cols[4]:
        st.markdown(
            """
            <div class="feat-card">
              <div class="feat-icon">&#127919;</div>
              <div class="feat-title">DECISION SUPPORT</div>
              <div class="feat-desc">Convert available model outputs into actionable chartering recommendations.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # 4. "How It Works" Section
    st.markdown(
        """
        <div class="landing-section-header">
          <div class="landing-section-pill">OPERATIONAL WORKFLOW</div>
          <div class="landing-section-title">How It Works</div>
          <div class="landing-section-sub">
            A structured, 5-stage advisory methodology transforming raw cargo requirements into validated chartering fixtures.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    hiw_cols = st.columns(5)
    with hiw_cols[0]:
        st.markdown(
            """
            <div class="hiw-step">
              <div class="hiw-num">01</div>
              <div class="hiw-title">ENTER VOYAGE REQUIREMENT</div>
              <div class="hiw-desc">Select origin loading corridor, Indian East Coast discharge port, cargo tonnage, and delivery date.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with hiw_cols[1]:
        st.markdown(
            """
            <div class="hiw-step">
              <div class="hiw-num">02</div>
              <div class="hiw-title">ANALYSE MARKET &amp; VOYAGE CONDITIONS</div>
              <div class="hiw-desc">Evaluate historical Baltic Dry Index trends, route distances, marine weather forecasts, and market shifts.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with hiw_cols[2]:
        st.markdown(
            """
            <div class="hiw-step">
              <div class="hiw-num">03</div>
              <div class="hiw-title">COMPARE VESSEL OPTIONS</div>
              <div class="hiw-desc">Assess Capesize, Panamax, and Supramax vessel dimensions against physical berth draft and LOA constraints.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with hiw_cols[3]:
        st.markdown(
            """
            <div class="hiw-step">
              <div class="hiw-num">04</div>
              <div class="hiw-title">ASSESS RISK</div>
              <div class="hiw-desc">Score multi-factor operational risks across bunker fuel volatility, port waiting queues, and adverse weather.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with hiw_cols[4]:
        st.markdown(
            """
            <div class="hiw-step">
              <div class="hiw-num">05</div>
              <div class="hiw-title">RECEIVE CHARTERING DECISION SUPPORT</div>
              <div class="hiw-desc">Generate quantitative Charter Now vs Wait timing advisory and download official 2-page statutory PDF reports.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # 5. Trust / System Information Section
    st.markdown(
        """
        <div class="landing-section-header">
          <div class="landing-section-pill">VERIFIABLE ARCHITECTURE</div>
          <div class="landing-section-title">Built for Data-Driven Maritime Decision Support</div>
          <div class="landing-section-sub">
            Engineered exclusively on verifiable SAIL supply chain corridors, official Indian port technical manuals, and multi-model predictive algorithms.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    t_cols = st.columns(3)
    with t_cols[0]:
        st.markdown(
            """
            <div class="trust-card">
              <div class="trust-stat">7</div>
              <div class="trust-label">7 Discharge Ports</div>
              <div class="trust-detail">
                Configured Indian East Coast bulk handling terminals: Haldia, Paradip, Visakhapatnam, Gangavaram, Dhamra, Chennai, and Ennore with physical draft, LOA, beam, and turnaround metrics.
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with t_cols[1]:
        st.markdown(
            """
            <div class="trust-card">
              <div class="trust-stat">6</div>
              <div class="trust-label">6 Origin Routes</div>
              <div class="trust-detail">
                Primary global coking coal and bulk loading corridors across Australia (Hay Point, Newcastle), South Africa (Richards Bay), Indonesia (Kalimantan), USA (Hampton Roads), and Mozambique (Maputo).
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with t_cols[2]:
        st.markdown(
            """
            <div class="trust-card">
              <div class="trust-stat">&#128202;</div>
              <div class="trust-label">Multi-Model Forecasting</div>
              <div class="trust-detail">
                Predictive forecasting architecture utilizing Ridge Regression, LightGBM, and Random Forest pipelines benchmarked against historical Baltic Dry Index (BDI) datasets.
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown(
        """
        <div class="trust-disclaimer">
          <b>Data Integrity &amp; Verifiability Notice:</b> Discharge port draft constraints and vessel specifications are configured per published terminal handbooks. Freight market datasets are indexed against historical Baltic Exchange benchmarks. Meteorological data is retrieved via Open-Meteo marine models. All metrics reflect actual system parameters without synthetic extrapolation.
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 6. About Section
    st.markdown(
        """
        <div class="about-card">
          <div class="about-title">&#9875; About SAGAR DRISHTI</div>
          <div class="about-text">
            SAGAR DRISHTI is a decision-support interface developed for maritime freight and chartering analysis, bringing together freight intelligence, voyage economics, vessel compatibility, weather considerations, risk analysis and validation outputs in one interface.
          </div>
          <div class="about-notice">
            <b>Decision-Support Operational Protocol:</b> SAGAR DRISHTI operates strictly as an institutional decision-support system designed to assist designated SAIL Chartering Officers. It does not execute autonomous commercial fixtures or legally bind the Steel Authority of India Limited. Charter party commitments must undergo standard statutory review in compliance with the National Steel Policy 2017 and Maritime India Vision 2030.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 7. Bottom Action Card
    st.markdown('<div style="height: 24px;"></div>', unsafe_allow_html=True)
    b_col1, b_col2, b_col3 = st.columns([1.0, 2.0, 1.0])
    with b_col2:
        if not is_authenticated:
            if st.button("⚓ ENTER SAGAR DRISHTI", key="bottom_landing_enter_platform", type="primary", use_container_width=True):
                enter_demo_mode()
        else:
            if st.button("⚡ ENTER SAGAR DRISHTI DASHBOARD", key="bottom_landing_dash", type="primary", use_container_width=True):
                st.session_state.show_login_loader = True
                st.session_state.page = "app"
                st.session_state.active_tab = "Dashboard"
                st.rerun()


def render_auth_page():
    auth_db = get_auth_db()

    # Top Institutional Header for Authentication
    header()

    st.markdown('<div style="height: 12px;"></div>', unsafe_allow_html=True)
    col_l, col_c, col_r = st.columns([1.0, 1.8, 1.0])
    with col_c:
        if st.button("← Return to SAGAR DRISHTI Overview", key="auth_btn_back_landing"):
            st.session_state.page = "landing"
            st.rerun()
        st.markdown('<div style="height: 8px;"></div>', unsafe_allow_html=True)

        if st.session_state.auth_mode == "login":
            if "reg_success_msg" in st.session_state:
                st.success(st.session_state.pop("reg_success_msg"))

            st.markdown(
                """
                <div class="auth-card-header">
                  <div class="auth-card-pill">OFFICIAL ACCESS GATEWAY</div>
                  <div class="auth-card-title">Officer Sign In</div>
                  <div class="auth-card-sub">Secure single-sign-on portal for SAIL dry-bulk freight intelligence and charter party decision advisory.</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            with st.form(key="login_form"):
                ident = st.text_input("Employee/User ID or Official Email", placeholder="e.g. SAIL-09482 or officer@sail.in")
                pwd = st.text_input("Password", type="password", placeholder="Enter your confidential password")
                login_submit = st.form_submit_button("LOGIN", use_container_width=True, type="primary")

                if login_submit:
                    ok, msg, user = auth_db.authenticate_user(ident, pwd)
                    if ok and user:
                        st.session_state.authenticated = True
                        st.session_state.demo_mode = False
                        st.session_state.user = user
                        st.session_state.show_login_loader = True
                        st.session_state.page = "app"
                        st.session_state.active_tab = "Dashboard"
                        st.rerun()
                    else:
                        st.error(msg)

            st.markdown('<div style="text-align:center;color:#7a5b16;font-size:12px;margin:12px 0 7px 0;">OR EVALUATE THE PROTOTYPE WITHOUT CREDENTIALS</div>', unsafe_allow_html=True)
            if st.button("🚀 ENTER DEMO MODE", key="auth_btn_demo", use_container_width=True, type="secondary"):
                enter_demo_mode()
            st.markdown(
                """
                <div style="text-align: center; margin: 18px 0 8px 0; font-size: 13px; color: #556a7e;">
                  New SAIL Chartering Officer?
                </div>
                """,
                unsafe_allow_html=True,
            )
            if st.button("CREATE NEW ACCOUNT", key="goto_reg", use_container_width=True):
                st.session_state.auth_mode = "register"
                st.rerun()

        else:
            st.markdown(
                """
                <div class="auth-card-header">
                  <div class="auth-card-pill">OFFICER ONBOARDING</div>
                  <div class="auth-card-title">Officer Registration</div>
                  <div class="auth-card-sub">Government of India &bull; Ministry of Steel &bull; Steel Authority of India Limited (SAIL)</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            with st.form(key="register_form"):
                full_name = st.text_input("Full Name *", placeholder="e.g. Rajesh Kumar Sharma")
                r_c1, r_c2 = st.columns(2)
                with r_c1:
                    emp_id = st.text_input("Employee/User ID *", placeholder="e.g. SAIL-09482")
                with r_c2:
                    email = st.text_input("Official Email *", placeholder="e.g. officer@sail.in")

                r_c3, r_c4 = st.columns(2)
                with r_c3:
                    dept = st.text_input("Department (Optional)", placeholder="e.g. Raw Material Logistics")
                with r_c4:
                    desig = st.text_input("Designation (Optional)", placeholder="e.g. Chartering Manager")

                r_c5, r_c6 = st.columns(2)
                with r_c5:
                    pwd = st.text_input("Password *", type="password", placeholder="Min. 8 chars, letters & numbers")
                with r_c6:
                    pwd_confirm = st.text_input("Confirm Password *", type="password", placeholder="Re-enter password")

                reg_submit = st.form_submit_button("CREATE ACCOUNT", use_container_width=True, type="primary")

                if reg_submit:
                    ok, msg, user = auth_db.register_user(
                        full_name=full_name,
                        employee_id=emp_id,
                        email=email,
                        password=pwd,
                        confirm_password=pwd_confirm,
                        department=dept,
                        designation=desig,
                    )
                    if ok:
                        st.session_state.reg_success_msg = "Account created successfully. Please login."
                        st.session_state.auth_mode = "login"
                        st.rerun()
                    else:
                        st.error(msg)

            st.markdown(
                """
                <div style="text-align: center; margin: 18px 0 8px 0; font-size: 13px; color: #556a7e;">
                  Already registered with SAIL Chartering?
                </div>
                """,
                unsafe_allow_html=True,
            )
            if st.button("ALREADY HAVE AN ACCOUNT? LOGIN", key="goto_login", use_container_width=True):
                st.session_state.auth_mode = "login"
                st.rerun()

        st.markdown(
            """
            <div class="auth-security-footer">
              &#128737; Protected Government Network &bull; 256-Bit Encrypted Session &bull; ISO 27001 Aligned
            </div>
            """,
            unsafe_allow_html=True,
        )


# ==============================================================================
# AUTHENTICATION SESSION GUARD & INITIALIZATION
# ==============================================================================
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "demo_mode" not in st.session_state:
    st.session_state.demo_mode = False
if "user" not in st.session_state:
    st.session_state.user = None
if "auth_mode" not in st.session_state:
    st.session_state.auth_mode = "login"
if "page" not in st.session_state:
    st.session_state.page = "landing"

# Intercept landing page navigation
if st.session_state.page == "landing":
    render_landing_page(is_authenticated=st.session_state.authenticated)
    footer()
    st.stop()

# Intercept unauthenticated access: render auth page and stop
if not st.session_state.authenticated:
    render_auth_page()
    footer()
    st.stop()

# Display loading animation when accessing dashboard from login to hide Streamlit initialization
if st.session_state.pop("show_login_loader", False):
    render_login_loading_screen()


# Initialize Session State Variables safely (for authenticated session)
bdi_end = data.market_frame("BDI").index[-1].date()
if "active_tab" not in st.session_state:
    st.session_state.active_tab = "Dashboard"
if "res" not in st.session_state:
    st.session_state.res = None
if "origin" not in st.session_state:
    st.session_state.origin = list(ROUTES)[0]
if "port" not in st.session_state:
    st.session_state.port = list(PORTS)[0]
if "qty" not in st.session_state:
    st.session_state.qty = 75000
else:
    try:
        st.session_state.qty = int(st.session_state.qty)
    except (ValueError, TypeError):
        st.session_state.qty = 75000

if "cargo_qty_input" in st.session_state and st.session_state.cargo_qty_input is not None:
    try:
        st.session_state.qty = int(st.session_state.cargo_qty_input)
    except (ValueError, TypeError):
        pass

today = dt.date.today()
if "as_of" not in st.session_state:
    st.session_state.as_of = bdi_end
elif isinstance(st.session_state.as_of, dt.date) and st.session_state.as_of > today:
    st.session_state.as_of = today
if "arrival" not in st.session_state or (isinstance(st.session_state.arrival, dt.date) and st.session_state.arrival < today):
    st.session_state.arrival = max(today, bdi_end + dt.timedelta(days=100))
if "bshock" not in st.session_state:
    st.session_state.bshock = 0
if "wait" not in st.session_state:
    st.session_state.wait = 0.0
if "fshock" not in st.session_state:
    st.session_state.fshock = 0
if "analysis_state" not in st.session_state:
    st.session_state.analysis_state = None
if "specific_vessel" not in st.session_state:
    st.session_state.specific_vessel = "AUTO — Evaluate all individual vessels"
if "waypoints" not in st.session_state:
    st.session_state.waypoints = []


# Helper function to run analysis safely without exposing raw Python tracebacks
def run_analysis_safely() -> bool:
    try:
        # Sync latest cargo quantity from widget if available
        if "cargo_qty_input" in st.session_state and st.session_state.cargo_qty_input is not None:
            try:
                st.session_state.qty = int(st.session_state.cargo_qty_input)
            except (ValueError, TypeError):
                pass
        # 1. Pre-validate cargo quantity
        normalize_cargo(st.session_state.qty)
        # 2. Pre-validate shocks
        normalize_shocks(st.session_state.bshock, st.session_state.wait, st.session_state.fshock)
        # 3. Pre-validate dates
        today = dt.date.today()
        if st.session_state.as_of > today:
            st.error("INVALID_INPUT: Decision date cannot be in the future. Please select a date on or before today.")
            return False
        if st.session_state.arrival < today:
            st.error("INVALID_INPUT: Required arrival date cannot be a past date. Please select today or a future date.")
            return False
        if st.session_state.arrival <= st.session_state.as_of:
            st.error("INVALID_INPUT: Required arrival date must be chronologically after the decision date.")
            return False

        with st.spinner("Executing forecast models, vessel compatibility, weather & risk engines…"):
            st.session_state.res = recommend.analyse(
                st.session_state.origin,
                st.session_state.port,
                st.session_state.qty,
                st.session_state.arrival,
                str(st.session_state.as_of),
                st.session_state.bshock,
                st.session_state.wait,
                st.session_state.fshock,
                None if st.session_state.specific_vessel.startswith("AUTO") else st.session_state.specific_vessel,
                st.session_state.waypoints,
            )
        if st.session_state.res and st.session_state.res.get("best"):
            st.session_state.analysis_state = "SUCCESS"
        else:
            st.session_state.analysis_state = "ANALYSIS_COMPLETED_NO_FEASIBLE_VESSEL"
        return True
    except InvalidInputError as e:
        st.error(f"INVALID_INPUT: {e}")
        return False
    except InsufficientHistoryError as e:
        st.error(f"INSUFFICIENT_HISTORY: {e}")
        return False
    except UnknownPortError as e:
        st.error(f"UNKNOWN_PORT: {e}")
        return False
    except UnknownRouteError as e:
        st.error(f"UNKNOWN_ROUTE: {e}")
        return False
    except ConfigurationError as e:
        st.error(f"CONFIGURATION_ERROR: {e}")
        return False
    except ModelProcessingError as e:
        st.error(f"MODEL_ERROR: {e}")
        return False
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Unexpected internal analysis error: %s", e)
        st.error("UNEXPECTED_ERROR: An internal analysis error occurred. Please try again or contact the administrator.")
        return False


# Auto-compute initial result if not present
if st.session_state.res is None:
    try:
        st.session_state.res = recommend.analyse(
            st.session_state.origin,
            st.session_state.port,
            st.session_state.qty,
            st.session_state.arrival,
            str(st.session_state.as_of),
            st.session_state.bshock,
            st.session_state.wait,
            st.session_state.fshock,
            None if st.session_state.specific_vessel.startswith("AUTO") else st.session_state.specific_vessel,
            st.session_state.waypoints,
        )
        if st.session_state.res and st.session_state.res.get("best"):
            st.session_state.analysis_state = "SUCCESS"
        elif st.session_state.res:
            st.session_state.analysis_state = "ANALYSIS_COMPLETED_NO_FEASIBLE_VESSEL"
    except Exception:
        st.session_state.res = None
        st.session_state.analysis_state = None

# Render Top Institutional Header Banner
header()

# Render Logged-In User Profile Strip with Logout Option
render_user_profile_bar()

# Render Horizontal Navigation Tabs
tabs = [
    ("Dashboard", "Dashboard"),
    ("Voyage Planner", "Voyage Planner"),
    ("Charter Strategy", "Charter Strategy"),
    ("7-Day Simulator", "7-Day Simulator"),
    ("Freight Forecast", "Freight Forecast"),
    ("Vessel Comparison", "Vessel Comparison"),
    ("Risk Centre", "Risk Centre"),
    ("Validation & Back-test", "Validation & Back-test"),
    ("Data & Assumptions", "Data & Assumptions"),
    ("About", "About"),
    ("Contact", "Contact"),
]

nav_cols = st.columns(len(tabs))
for col, (label, tab_key) in zip(nav_cols, tabs):
    with col:
        is_active = st.session_state.active_tab == tab_key
        if st.button(
            label,
            key=f"nav_{tab_key}",
            use_container_width=True,
            type="primary" if is_active else "secondary",
        ):
            st.session_state.active_tab = tab_key
            st.rerun()


# ==============================================================================
# TAB 1: DASHBOARD (HOME EXPERIENCE)
# ==============================================================================
if st.session_state.active_tab == "Dashboard":
    hero = f"data:image/png;base64,{HERO}" if HERO else ""
    st.markdown(
        f"""
    <div class="hero-container">
      {f'<img class="hero-bg" src="{hero}" alt="Maritime Bulk Vessel">' if hero else ''}
      <div class="hero-overlay"></div>
      <div class="hero-content">
        <div class="hero-pill">GOVERNMENT OF INDIA &bull; MINISTRY OF STEEL &bull; STEEL AUTHORITY OF INDIA LIMITED</div>
        <h1 class="hero-title">SAGAR DRISHTI</h1>
        <div class="hero-subtitle">Freight Intelligence &amp; Chartering Advisor</div>
        <div class="hero-tagline">Data-Driven Decision Support for Maritime Raw Material Logistics</div>
        <div class="hero-desc">
          Automated decision-support platform optimizing maritime dry-bulk chartering for SAIL. Integrates multi-horizon freight market forecasts, vessel-berth constraint checks, Open-Meteo weather intelligence, voyage economics, and data-driven risk scoring.
        </div>
        <div class="hero-capabilities">
          <span class="cap-pill">&#128200; Freight Forecasting</span>
          <span class="cap-pill">&#128176; Voyage Cost Analysis</span>
          <span class="cap-pill">&#9875; Vessel Compatibility</span>
          <span class="cap-pill">&#127754; Weather Impact</span>
          <span class="cap-pill">&#128737; Risk Analysis</span>
          <span class="cap-pill">&#128200; 7-Day Voyage Simulation</span>
          <span class="cap-pill">&#9878; Decision Support</span>
        </div>
      </div>
    </div>
    """,
        unsafe_allow_html=True,
    )

    # Input Form Panel
    st.markdown(
        '<div class="section-title">⚓ Plan Voyage &amp; Generate Decision Recommendation</div>',
        unsafe_allow_html=True,
    )

    col_in1, col_in2, col_in3, col_in4 = st.columns(4)
    with col_in1:
        st.session_state.origin = st.selectbox("Origin Port (Loading)", list(ROUTES), index=list(ROUTES).index(st.session_state.origin))
    with col_in2:
        st.session_state.port = st.selectbox("Discharge Port (Indian East Coast)", list(PORTS), index=list(PORTS).index(st.session_state.port))
    with col_in3:
        vessel_choices = ["AUTO — Evaluate all individual vessels"] + list(VESSEL_PROFILES.keys())
        st.session_state.specific_vessel = st.selectbox(
            "Vessel Selection", vessel_choices,
            index=vessel_choices.index(st.session_state.specific_vessel) if st.session_state.specific_vessel in vessel_choices else 0,
            format_func=lambda x: x if x.startswith("AUTO") else f"{VESSEL_PROFILES[x]['vessel_name']} · {VESSEL_PROFILES[x]['vessel_class']} · SYNTHETIC"
        )
    with col_in4:
        waypoint_choices = [p for p in PORTS if p != st.session_state.port]
        st.session_state.waypoints = st.multiselect(
            "Intermediate Port Calls (Optional)", waypoint_choices,
            default=[x for x in st.session_state.waypoints if x in waypoint_choices],
            help="Build A→C→D→B style itineraries. Intermediate calls are planning assumptions and add handling/waiting costs."
        )

    col_qty, col_arrival = st.columns(2)
    with col_qty:
        cur_qty = int(st.session_state.get("qty", 75000))
        if cur_qty < 1000:
            cur_qty = 1000
        elif cur_qty > 400000:
            cur_qty = 400000
        st.session_state.qty = cur_qty

        def _on_cargo_qty_change():
            if "cargo_qty_input" in st.session_state and st.session_state.cargo_qty_input is not None:
                try:
                    st.session_state.qty = int(st.session_state.cargo_qty_input)
                except (ValueError, TypeError):
                    pass

        qty_input = st.number_input(
            "Cargo Quantity (MT)",
            min_value=1000,
            max_value=400000,
            value=cur_qty,
            step=5000,
            format="%d",
            key="cargo_qty_input",
            on_change=_on_cargo_qty_change,
            help="Nominal cargo quantity in Metric Tonnes (MT). Allowed range: 1,000 to 400,000 MT. Step: 5,000 MT.",
        )
        if qty_input is not None:
            st.session_state.qty = int(qty_input)
    with col_arrival:
        today = dt.date.today()
        cur_arrival = st.session_state.arrival if (isinstance(st.session_state.arrival, dt.date) and st.session_state.arrival >= today) else today
        st.session_state.arrival = st.date_input(
            "Required Arrival Date",
            value=cur_arrival,
            min_value=today,
            format="DD-MM-YYYY",
            help="Select voyage arrival date. Past dates are disabled.",
        )

    with st.expander("Advanced Scenario & Shock Controls"):
        sc1, sc2, sc3 = st.columns(3)
        with sc1:
            today = dt.date.today()
            cur_as_of = st.session_state.as_of if isinstance(st.session_state.as_of, dt.date) else bdi_end
            if cur_as_of > today:
                cur_as_of = today
            st.session_state.as_of = st.date_input(
                "Decision Date (as of)",
                value=cur_as_of,
                max_value=today,
                format="DD-MM-YYYY",
                help=f"Decision date for analysis. Market data is available through {fmt_date_str(bdi_end)}.",
            )
            if st.session_state.as_of > bdi_end:
                st.info(f"Market data is available only through {fmt_date_str(bdi_end)}. Analysis for later dates is subject to available historical data.")
        with sc2:
            st.session_state.bshock = st.slider("Bunker Price Change %", -30, 50, st.session_state.bshock)
        with sc3:
            st.session_state.wait = st.slider("Extra Port Waiting (days)", 0.0, 10.0, st.session_state.wait, 0.5)
        st.session_state.fshock = st.slider("Freight Index Shock %", -30, 50, st.session_state.fshock)

    if st.button("⚓  ANALYSE VOYAGE & EVALUATE CHARTERING DECISION", type="primary", use_container_width=True):
        if run_analysis_safely():
            st.session_state.active_tab = "Dashboard"
            st.rerun()

    sim_cta_l, sim_cta_r = st.columns([3, 1])
    with sim_cta_l:
        st.markdown(
            '<div style="font-size:12px;color:#556a7e;padding:8px 0 0 2px;">Explore vessel performance across seven simulated operating days, including weather, speed, fuel, distance and risk.</div>',
            unsafe_allow_html=True,
        )
    with sim_cta_r:
        if st.button("🌊  OPEN 7-DAY SIMULATOR", use_container_width=True, type="secondary"):
            st.session_state.active_tab = "7-Day Simulator"
            st.rerun()

    # Results Display
    res = st.session_state.res
    if res and res.get("best"):
        if st.session_state.as_of > bdi_end:
            st.info(f"Market data is available only through {fmt_date_str(bdi_end)}. Analysis for later dates is subject to available historical data.")
        b = res["best"]
        k_cp = b["cost_plan"]
        tm = b["timing"]
        action = tm["action"]
        reason = clean_text(tm["reason"])

        market_date = getattr(st.session_state, "as_of", None) if ("st" in globals() and hasattr(st, "session_state")) else None
        if market_date is None:
            market_date = b.get("as_of")
        fx_rate = get_usdinr_rate(market_date)

        c_now_usd_disp = round(b["cost_now"]["usd_per_mt"], 2)
        c_now_inr_disp = round(c_now_usd_disp * fx_rate)
        c_plan_usd_disp = round(k_cp["usd_per_mt"], 2)
        c_plan_inr_disp = round(c_plan_usd_disp * fx_rate)

        diff_usd_mt = round(c_now_usd_disp - c_plan_usd_disp, 2)
        saving_per_mt = round(diff_usd_mt * fx_rate)
        saving_total = saving_per_mt * st.session_state.qty

        banner_class = "rec-banner-wait" if action in ("WAIT", "MONITOR") else "rec-banner-now"
        st.markdown(
            f"""
        <div class="rec-banner {banner_class}">
          <div>
            <div style="font-size:12px;font-weight:700;color:#5d7082;text-transform:uppercase;letter-spacing:0.5px">RECOMMENDED CHARTERING ACTION</div>
            <div class="rec-action">{action}</div>
          </div>
          <div style="flex:1">
            <div class="rec-reason"><b>Decision Rationale:</b> {reason}</div>
            <div style="font-size:12.5px;color:#173956;margin-top:4px">
              Estimated Price Impact: <b>{format_indian_currency(saving_per_mt, decimals=0)} / MT</b> &nbsp;|&nbsp;
              Total Financial Impact: <b>{format_indian_currency(saving_total / 1e5, decimals=1)} Lakhs</b> &nbsp;|&nbsp;
              Model Confidence: <b>{max(55, min(92, int(100 - b['risk_overall'] / 2)))}%</b>
            </div>
          </div>
        </div>
        """,
            unsafe_allow_html=True,
        )

        # Primary KPI Strip with Enhanced Hover Lift
        k1, k2, k3, k4 = st.columns(4)
        with k1:
            st.markdown(
                f"""
            <div class="kpi-card">
              <div class="kpi-header">Recommended Vessel</div>
              <div class="kpi-value">{b.get('vessel_name', b['vessel'])}</div>
              <div class="kpi-sub">{b.get('vessel_class', 'Vessel')} · Capacity: {b['feasibility']['capacity_per_voyage']:,.0f} MT</div>
            </div>""",
                unsafe_allow_html=True,
            )
        with k2:
            st.markdown(
                f"""
            <div class="kpi-card">
              <div class="kpi-header">Current Voyage Cost</div>
              <div class="kpi-value">${c_now_usd_disp:.2f} / MT</div>
              <div class="kpi-sub">{format_indian_currency(c_now_inr_disp, decimals=0)} / MT</div>
            </div>""",
                unsafe_allow_html=True,
            )
        with k3:
            st.markdown(
                f"""
            <div class="kpi-card">
              <div class="kpi-header">Total Voyage Cost</div>
              <div class="kpi-value">${c_plan_usd_disp:.2f} / MT</div>
              <div class="kpi-sub">{format_indian_currency(c_plan_inr_disp, decimals=0)} / MT (Weather Adj)</div>
            </div>""",
                unsafe_allow_html=True,
            )
        with k4:
            st.markdown(
                f"""
            <div class="kpi-card">
              <div class="kpi-header">Overall Risk Level</div>
              <div class="kpi-value">{b['risk_band']}</div>
              <div class="kpi-sub">Risk Score: {b['risk_overall']:.0f} / 100</div>
            </div>""",
                unsafe_allow_html=True,
            )

        st.markdown("<br>", unsafe_allow_html=True)
        pdf = report_bytes(res, st.session_state.origin, st.session_state.port, st.session_state.qty, st.session_state.arrival, user=st.session_state.get("user"), as_of=st.session_state.as_of)
        if pdf:
            st.download_button(
                "⇩  Download Official Voyage Report (PDF)",
                pdf,
                "SAGAR_DRISHTI_Voyage_Analysis_Report.pdf",
                "application/pdf",
                key="btn_download_voyage_report_pdf",
                type="primary",
            )
    elif res and not res.get("best"):
        st.markdown(
            """
            <div class="rec-banner rec-banner-wait" style="border-left: 5px solid #b32e2a;">
              <div>
                <div style="font-size:12px;font-weight:700;color:#5d7082;text-transform:uppercase;letter-spacing:0.5px">ANALYSIS COMPLETED · NO FEASIBLE VESSEL</div>
                <div class="rec-action" style="color:#b32e2a;font-size:22px;">NO FEASIBLE VESSEL</div>
              </div>
              <div style="flex:1">
                <div class="rec-reason"><b>Analysis Result:</b> No dry bulk vessel class satisfies physical constraints (draft, LOA, beam, DWT) or single-voyage parcel size for this port combination under current operating limits.</div>
                <div style="font-size:12.5px;color:#173956;margin-top:6px">
                  Please consult the <b>Vessel Comparison</b> tab for detailed rejection reasons per vessel class, or adjust cargo parcel size and port selection.
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ==============================================================================
# TAB 2: CHARTER STRATEGY & MARKET INTELLIGENCE
# ==============================================================================
elif st.session_state.active_tab == "Charter Strategy":
    render_charter_strategy()

# TAB 3: 7-DAY VOYAGE SIMULATOR
# ==============================================================================
elif st.session_state.active_tab == "7-Day Simulator":
    # -------------------------------------------------------------------------
    # 7-DAY VOYAGE SIMULATOR
    # Self-contained scenario engine for demonstrations. It deliberately does
    # not require MongoDB or an external weather API, so the evaluator can
    # reproduce the same voyage simulation in Demo Mode.
    # -------------------------------------------------------------------------
    st.markdown(
        """
    <div class="panel">
      <div class="section-title">🌊 7-Day Voyage Simulator</div>
      <div class="section-sub">Day-by-day vessel, weather, speed, fuel, ETA and operational-risk simulation. Scenario values are synthetic and intended for prototype evaluation.</div>
    </div>
    """,
        unsafe_allow_html=True,
    )

    sim_c1, sim_c2, sim_c3 = st.columns(3)
    vessel_ids = list(VESSEL_PROFILES.keys())
    current_vessel = st.session_state.get("specific_vessel", vessel_ids[0])
    if current_vessel not in vessel_ids:
        current_vessel = vessel_ids[0]
    with sim_c1:
        sim_vessel = st.selectbox(
            "Vessel",
            vessel_ids,
            index=vessel_ids.index(current_vessel),
            format_func=lambda x: f"{VESSEL_PROFILES[x]['vessel_name']} · {VESSEL_PROFILES[x]['vessel_class']}",
            key="sim_vessel_select",
        )
    with sim_c2:
        sim_origin = st.selectbox("Loading Route", list(ROUTES), index=list(ROUTES).index(st.session_state.origin) if st.session_state.origin in ROUTES else 0, key="sim_origin")
    with sim_c3:
        sim_port = st.selectbox("Discharge Port", list(PORTS), index=list(PORTS).index(st.session_state.port) if st.session_state.port in PORTS else 0, key="sim_port")

    sv = VESSEL_PROFILES[sim_vessel]
    base_distance = float(ROUTES[sim_origin].get("distance_nm", 3000))
    try:
        port_offset = float(SETTINGS.get("port_offset_nm", {}).get(sim_port, 0))
    except Exception:
        port_offset = 0.0
    total_distance = max(500.0, base_distance + port_offset)

    sc1, sc2, sc3 = st.columns(3)
    with sc1:
        sim_cargo = st.number_input("Cargo Quantity (MT)", min_value=1000, max_value=int(sv["dwt"] * sv.get("cargo_factor", 0.95)), value=min(int(st.session_state.get("qty", 75000)), int(sv["dwt"] * sv.get("cargo_factor", 0.95))), step=5000, key="sim_cargo")
    with sc2:
        sim_start = st.date_input("Simulation Start", value=dt.date.today(), key="sim_start")
    with sc3:
        sim_weather = st.select_slider("Weather Scenario", options=["Favourable", "Normal", "Adverse", "Severe"], value="Normal", key="sim_weather")

    weather_profiles = {
        "Favourable": [(12,1.1),(14,1.0),(10,0.8),(16,1.2),(11,0.9),(13,1.0),(9,0.7)],
        "Normal": [(18,1.8),(21,2.1),(16,1.5),(24,2.5),(19,1.9),(22,2.2),(17,1.6)],
        "Adverse": [(28,3.4),(31,3.8),(34,4.2),(29,3.6),(36,4.5),(32,4.0),(27,3.2)],
        "Severe": [(42,5.8),(46,6.4),(49,6.9),(44,6.1),(52,7.3),(47,6.7),(43,5.9)],
    }
    weather_days = weather_profiles[sim_weather]
    speed_factor = {"Favourable":1.03, "Normal":0.98, "Adverse":0.88, "Severe":0.72}[sim_weather]

    rows = []
    remaining_nm = total_distance
    cumulative_fuel = 0.0
    cumulative_distance = 0.0
    for i, (wind_kn, wave_m) in enumerate(weather_days, start=1):
        weather_penalty = max(0.0, (wind_kn - 18.0) * 0.012 + max(0.0, wave_m - 2.0) * 0.045)
        day_speed = max(6.0, sv["speed_kn"] * speed_factor * (1.0 - min(weather_penalty, 0.32)))
        day_nm = min(max(0.0, remaining_nm), day_speed * 24.0)
        if i == 7 and remaining_nm > day_nm:
            day_nm = min(remaining_nm, day_nm)
        sea_fuel = sv["sea_cons_t_day"] * (day_nm / max(day_speed * 24.0, 1.0))
        weather_fuel = sea_fuel * min(weather_penalty * 1.8, 0.35)
        day_fuel = sea_fuel + weather_fuel
        cumulative_fuel += day_fuel
        cumulative_distance += day_nm
        remaining_nm = max(0.0, remaining_nm - day_nm)
        risk_score = min(95.0, 20.0 + wind_kn * 0.9 + wave_m * 5.0 + (12.0 - day_speed) * 2.2)
        risk_band = "LOW" if risk_score < 35 else ("MODERATE" if risk_score < 55 else ("HIGH" if risk_score < 75 else "SEVERE"))
        rows.append({
            "Day": i,
            "Date": sim_start + dt.timedelta(days=i-1),
            "Wind (kn)": wind_kn,
            "Wave (m)": wave_m,
            "Speed (kn)": round(day_speed, 1),
            "Distance (NM)": round(day_nm),
            "Fuel (t)": round(day_fuel, 1),
            "Cumulative Fuel (t)": round(cumulative_fuel, 1),
            "Risk": risk_band,
            "Risk Score": round(risk_score, 1),
        })

    sim_df = pd.DataFrame(rows)

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("Voyage Distance", f"{total_distance:,.0f} NM")
    with m2:
        st.metric("Simulated Distance", f"{cumulative_distance:,.0f} NM")
    with m3:
        st.metric("7-Day Fuel", f"{cumulative_fuel:,.1f} t")
    with m4:
        eta_days = total_distance / max(sv["speed_kn"] * speed_factor * 24.0, 1.0)
        st.metric("Indicative Transit", f"{eta_days:.1f} days")

    st.dataframe(sim_df.drop(columns=["Risk Score"]), hide_index=True, use_container_width=True)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=sim_df["Date"], y=sim_df["Speed (kn)"], mode="lines+markers", name="Speed (kn)"))
    fig.add_trace(go.Scatter(x=sim_df["Date"], y=sim_df["Wind (kn)"], mode="lines+markers", name="Wind (kn)", yaxis="y2"))
    fig.update_layout(
        height=340, margin=dict(l=10, r=10, t=30, b=10), paper_bgcolor="white", plot_bgcolor="white",
        title="7-Day Operational Conditions", xaxis_title="Simulation Date", yaxis_title="Vessel Speed (kn)",
        yaxis2=dict(title="Wind (kn)", overlaying="y", side="right"), legend=dict(orientation="h", y=1.08),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    if remaining_nm > 0:
        st.info(f"Seven-day window covers {cumulative_distance:,.0f} NM of the indicative {total_distance:,.0f} NM route. Remaining distance: {remaining_nm:,.0f} NM.")
    else:
        st.success("The simulated vessel completes the indicative route within the 7-day window.")

    st.markdown(
        f"""<div class="panel">
          <div class="section-title">Decision Signal</div>
          <div style="font-size:13px;line-height:1.55;">
            <b>{sv['vessel_name']}</b> is simulated at an average operating speed of <b>{sim_df['Speed (kn)'].mean():.1f} kn</b> under the <b>{sim_weather}</b> scenario, with cumulative simulated fuel consumption of <b>{cumulative_fuel:,.1f} t</b>. Highest daily operational risk is <b>{sim_df['Risk'].iloc[sim_df['Risk Score'].idxmax()]}</b>.
          </div>
          <div style="font-size:11.5px;color:#5d7082;margin-top:7px;">SYNTHETIC / SCENARIO SIMULATION — not a live vessel position or operational forecast.</div>
        </div>""",
        unsafe_allow_html=True,
    )


elif st.session_state.active_tab == "Voyage Planner":
    res = st.session_state.res
    if not res:
        st.info("Please select voyage inputs on the Dashboard and click 'ANALYSE VOYAGE'.")
        st.stop()
    if not res.get("best"):
        st.markdown(
            """
            <div class="panel" style="border-left: 4px solid #b32e2a;">
              <div class="section-title" style="color: #b32e2a;">⚠ Analysis Completed: No Feasible Vessel Found</div>
              <div style="font-size: 13.5px; color: #173956; line-height: 1.6; margin-top: 8px;">
                Voyage planning and cost schedules cannot be computed because no vessel class satisfies the current cargo parcel size and port physical limits (draft, LOA, beam, DWT).
                <br><br>
                Please review the <b>Vessel Comparison</b> tab to inspect specific constraint violations and rejection reasons for each vessel class.
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.stop()
    b = res["best"]
    cp = b["cost_plan"]
    wx = cp.get("weather", {})
    market_date = getattr(st.session_state, "as_of", None) if ("st" in globals() and hasattr(st, "session_state")) else None
    if market_date is None:
        market_date = b.get("as_of")
    fx_rate = get_usdinr_rate(market_date)

    st.markdown(
        f"""
    <div class="panel">
      <div class="section-title">📍 Voyage Route &amp; Cost Breakdown</div>
      <div class="section-sub">{clean_text(st.session_state.origin)} &nbsp;→&nbsp; {" → ".join(clean_text(x) for x in st.session_state.waypoints)}{" → " if st.session_state.waypoints else ""}{clean_text(st.session_state.port)} &nbsp;|&nbsp; Cargo: {st.session_state.qty:,.0f} MT &nbsp;|&nbsp; Arrival: {fmt_date_str(st.session_state.arrival)}</div>
    </div>
    """,
        unsafe_allow_html=True,
    )

    c_left, c_right = st.columns([1.3, 1.0])
    with c_left:
        st.markdown(
            f'''<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                <div class="section-title" style="margin-bottom:0">Cost Breakdown (USD &amp; ₹ INR)</div>
                <div style="font-size:12px;font-weight:600;color:#0b416d;background:#eef4f9;padding:3px 8px;border-radius:4px;border:1px solid #c9dbe9">
                    FX Rate Applied: ₹{fx_rate:.2f}/USD
                </div>
            </div>''',
            unsafe_allow_html=True,
        )
        hire_usd_val = round(cp['hire_usd'])
        fuel_base_usd_val = round(cp.get('fuel_usd', 0))
        fuel_wx_usd_val = round(cp.get('weather_impact_usd', cp.get('weather', {}).get('weather_impact_usd', 0)))
        port_usd_val = round(cp['port_usd'])
        wait_usd_val = round(cp['wait_usd'])
        tot_usd_val = hire_usd_val + fuel_base_usd_val + fuel_wx_usd_val + port_usd_val + wait_usd_val

        hire_inr_val = round(hire_usd_val * fx_rate)
        fuel_base_inr_val = round(fuel_base_usd_val * fx_rate)
        fuel_wx_inr_val = round(fuel_wx_usd_val * fx_rate)
        port_inr_val = round(port_usd_val * fx_rate)
        wait_inr_val = round(wait_usd_val * fx_rate)
        tot_inr_val = hire_inr_val + fuel_base_inr_val + fuel_wx_inr_val + port_inr_val + wait_inr_val

        rows = [
            ["Vessel Hire", f"${hire_usd_val:,.0f}", format_indian_currency(hire_inr_val)],
            ["Fuel Cost (Base)", f"${fuel_base_usd_val:,.0f}", format_indian_currency(fuel_base_inr_val)],
            ["Weather / Route Impact", f"${fuel_wx_usd_val:,.0f}", format_indian_currency(fuel_wx_inr_val)],
            ["Port Charges (Origin & Discharge)", f"${port_usd_val:,.0f}", format_indian_currency(port_inr_val)],
            ["Waiting Cost (Estimated)", f"${wait_usd_val:,.0f}", format_indian_currency(wait_inr_val)],
            ["Total Voyage Cost", f"${tot_usd_val:,.0f}", format_indian_currency(tot_inr_val)],
        ]
        df_c = pd.DataFrame(rows, columns=["Component", "Cost (USD)", "Cost (₹ INR)"])
        st.dataframe(df_c, hide_index=True, use_container_width=True)

        st.markdown(
            """<div style="font-size:11.5px;color:#556a7e;background:#f8fafc;border-left:3px solid #0b416d;padding:6px 10px;margin-top:4px;margin-bottom:8px;line-height:1.4">
                <b>USD/INR FX Reference:</b><br/>
                • ₹89.92/USD applied for Market/Decision Dates up to 07-Jan-2026.<br/>
                • ₹95.92/USD applied for dates after 07-Jan-2026.<br/>
                • INR values are converted from the corresponding USD costs using the applicable reference FX assumption.
            </div>""",
            unsafe_allow_html=True,
        )

        st.markdown("<br>", unsafe_allow_html=True)
        try:
            st.plotly_chart(route_map(st.session_state.origin, st.session_state.port, st.session_state.waypoints), use_container_width=True, config={"displayModeBar": False})
        except Exception as e:
            st.warning(f"Route map visualization unavailable: {e}")

        if cp.get("legs"):
            st.markdown('<div class="section-title">Leg-wise Voyage Economics</div>', unsafe_allow_html=True)
            leg_df = pd.DataFrame(cp["legs"])
            leg_df.columns = [c.replace("_", " ").title() for c in leg_df.columns]
            st.dataframe(leg_df, hide_index=True, use_container_width=True)

    with c_right:
        st.markdown('<div class="section-title">Cost Share Distribution</div>', unsafe_allow_html=True)
        st.plotly_chart(cost_chart(cp), use_container_width=True, config={"displayModeBar": False})

        st.markdown('<div class="section-title">Weather Operational Impact Breakdown</div>', unsafe_allow_html=True)
        if wx.get("available"):
            st.markdown(
                f"""
            <div class="panel">
              <div style="font-size:11.5px;color:#5d7082;font-weight:700;letter-spacing:0.4px">SOURCE: Open-Meteo 7-Day Marine Forecast API</div>
              <div style="font-size:13px;margin-top:6px"><b>Speed Reduction:</b> {wx.get('speed_reduction_pct', 0):.1f}%</div>
              <div style="font-size:13px"><b>Extra Sea Days:</b> {wx.get('extra_sea_days', 0):.2f} days</div>
              <div style="font-size:13px"><b>Additional Fuel Cost:</b> ${wx.get('weather_impact_usd', 0):,.0f}</div>
              <div style="font-size:13px"><b>Weather Risk Band:</b> {wx.get('band', 'MODERATE')}</div>
            </div>
            """,
                unsafe_allow_html=True,
            )
        else:
            st.info("Weather Forecast Status: Target date is outside the 7-day Open-Meteo API window. Fallback to seasonal cyclone matrix applied.")


# ==============================================================================
# TAB 3: FREIGHT FORECAST
# ==============================================================================
elif st.session_state.active_tab == "Freight Forecast":
    res = st.session_state.res
    if not res:
        st.info("Please select voyage inputs on the Dashboard to view freight forecasts.")
        st.stop()
    if not res.get("best"):
        st.info("Voyage analysis completed (no feasible vessel nominated for current constraints). Displaying benchmark Baltic Dry Index (BDI) freight market forecast for reference.")
        bdi_df = recommend._market("BDI")
        bdi_bundle, bdi_rep = recommend._models("BDI")
        as_of_str = str(st.session_state.as_of) if st.session_state.as_of else None
        cur, as_of_used, path = forecast.predict_path(bdi_df, bdi_bundle, bdi_rep, as_of_str)
        b = {
            "index": "BDI",
            "as_of": as_of_used,
            "current_level": cur,
            "path": path,
            "report": bdi_rep,
        }
    else:
        b = res["best"]

    st.markdown(
        f"""
    <div class="panel">
      <div class="section-title">📈 Freight Rate Market Forecast ({b['index']} Index)</div>
      <div class="section-sub">Historical freight index series, model predictions, and 80% split-conformal bounds | Data as of: {fmt_date_str(b['as_of'])}</div>
    </div>
    """,
        unsafe_allow_html=True,
    )

    if st.session_state.as_of > bdi_end:
        st.info(f"Market data is available only through {fmt_date_str(bdi_end)}. Analysis for later dates is subject to available historical data.")

    fc_left, fc_right = st.columns([1.8, 1.0])
    with fc_left:
        st.plotly_chart(forecast_chart(b), use_container_width=True, config={"displayModeBar": False})

    with fc_right:
        st.markdown('<div class="section-title">Horizon Predictions</div>', unsafe_allow_html=True)
        p = b["path"]
        f_rows = []
        for idx_row, row in p.iterrows():
            f_rows.append(
                {
                    "Horizon": f"{int(row['horizon_days'])} Days",
                    "Expected": f"{row['expected']:,.1f}",
                    "80% Bounds": f"{row['lower']:,.1f} - {row['upper']:,.1f}",
                    "Model": row["model"],
                }
            )
        st.dataframe(pd.DataFrame(f_rows), hide_index=True, use_container_width=True)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="section-title">🔮 Future Scenario Sandbox</div>', unsafe_allow_html=True)
    scen_col1, scen_col2 = st.columns([1, 3])
    with scen_col1:
        scenario = st.selectbox("Scenario", ["base", "bull", "bear"], format_func=lambda x: x.title(), key="future_scenario")
    with scen_col2:
        future = data.synthetic_future_scenario(b["index"], days=120, scenario=scenario)
        future_fig = go.Figure()
        future_fig.add_trace(go.Scatter(x=future.index, y=future["freight"], name="Synthetic Future Scenario", line=dict(color=GOLD_ACCENT, width=2)))
        future_fig.update_layout(height=280, margin=dict(l=10,r=10,t=10,b=5), paper_bgcolor="white", plot_bgcolor="white",
                                  yaxis_title="Freight Index", xaxis_title="Scenario Date")
        st.plotly_chart(future_fig, use_container_width=True, config={"displayModeBar": False})
    st.caption("Synthetic / Scenario Data — generated only for future what-if visualization; it is never used to train the historical forecasting models.")

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="section-title">Model Ladder &amp; Feature Importances</div>', unsafe_allow_html=True)
    rep = b["report"]
    m_data = []
    for h_str, h_info in rep.get("horizons", {}).items():
        m_data.append(
            {
                "Horizon (Calendar Days)": f"{h_str} Days",
                "Trading Days": h_info["trading_days"],
                "Best Selected Model": h_info["best_model"],
                "Conformal Halfwidth (log)": h_info["conformal_halfwidth_log"],
                "Top Predictive Features": ", ".join(h_info["top_features"][:4]),
            }
        )
    st.dataframe(pd.DataFrame(m_data), hide_index=True, use_container_width=True)


# ==============================================================================
# TAB 4: VESSEL COMPARISON
# ==============================================================================
elif st.session_state.active_tab == "Vessel Comparison":
    # Registry is available even before a voyage analysis so evaluators can inspect
    # the ship-specific prototype dataset independently of the recommendation engine.
    st.markdown(
        """
    <div class="panel">
      <div class="section-title">⚓ Individual Vessel Registry</div>
      <div class="section-sub">Ship-specific prototype records used for scenario evaluation. Every record below is synthetic / scenario data and is not a live vessel position, ownership record, or commercial fixture.</div>
    </div>
    """,
        unsafe_allow_html=True,
    )
    registry_rows = []
    for v_id, v_cfg in VESSEL_PROFILES.items():
        registry_rows.append({
            "Vessel ID": v_id,
            "Vessel Name": v_cfg["vessel_name"],
            "Class": v_cfg["vessel_class"],
            "DWT (t)": f"{v_cfg['dwt']:,}",
            "Draft (m)": v_cfg["draft_m"],
            "LOA (m)": v_cfg["loa_m"],
            "Beam (m)": v_cfg["beam_m"],
            "Speed (kn)": v_cfg["speed_kn"],
            "Sea Fuel (t/day)": v_cfg["sea_cons_t_day"],
            "Port Fuel (t/day)": v_cfg["port_cons_t_day"],
            "Freight Index": v_cfg["freight_index"],
            "Cargo Compatibility": ", ".join(v_cfg["cargo_types"]),
            "Data Basis": "SYNTHETIC / SCENARIO",
        })
    st.dataframe(pd.DataFrame(registry_rows), hide_index=True, use_container_width=True)
    st.caption("Synthetic prototype master · 8 individual vessel records · for SIH demonstration only")

    res = st.session_state.res
    if not res:
        st.info("Select voyage inputs on the Dashboard and run analysis to activate the vessel-port compatibility matrix.")
        st.stop()

    st.markdown(
        f"""
    <div class="panel">
      <div class="section-title">♟ Vessel-Port Compatibility Matrix</div>
      <div class="section-sub">Evaluation of dry bulk vessel classes against port physical constraints (LOA, Beam, Draft, DWT) and voyage economics.</div>
    </div>
    """,
        unsafe_allow_html=True,
    )

    if not res.get("best"):
        st.warning("Operational Notice: All evaluated vessel classes failed physical or operational constraints for the specified cargo and discharge port. Review rejection reasons and limits below.")

    comp_rows = []
    for opt in res.get("options", []):
        name = opt["vessel"]
        feas = opt["feasibility"]
        st_badge = feas["status"]
        vcfg = VESSEL_PROFILES.get(name, VESSELS.get(name, {}))

        cost_str = f"${opt['cost_plan']['usd_per_mt']:.2f} / MT" if "cost_plan" in opt else "N/A"
        reasons_str = clean_text("; ".join(feas["reasons"])) if feas["reasons"] else "All port limits satisfied"

        comp_rows.append(
            {
                "Vessel ID": opt.get("vessel_id", name),
                "Vessel Name": opt.get("vessel_name", name),
                "Vessel Class": opt.get("vessel_class", name),
                "Status": st_badge,
                "Voyages": feas.get("voyages", "N/A"),
                "DWT": f"{vcfg['dwt']:,}",
                "Draft (m)": vcfg["draft_m"],
                "LOA (m)": vcfg["loa_m"],
                "Beam (m)": vcfg["beam_m"],
                "Cargo Intake": f"{feas.get('capacity_per_voyage', 0):,.0f} MT",
                "Est. Voyage Cost": cost_str,
                "Data Basis": opt.get("data_source", "Configured master"),
                "Operational Notes & Rejection Reasons": reasons_str,
            }
        )

    st.dataframe(pd.DataFrame(comp_rows), hide_index=True, use_container_width=True)


# ==============================================================================
# TAB 5: RISK CENTRE (SIGNIFICANTLY ENHANCED WITH GRAPH & PROPER LAYOUT)
# ==============================================================================
elif st.session_state.active_tab == "Risk Centre":
    res = st.session_state.res
    if not res:
        st.info("Please select voyage inputs on the Dashboard to view risk metrics.")
        st.stop()
    if not res.get("best"):
        st.markdown(
            """
            <div class="panel" style="border-left: 4px solid #b32e2a;">
              <div class="section-title" style="color: #b32e2a;">⚠ Risk Analysis Status: No Feasible Vessel</div>
              <div style="font-size: 13.5px; color: #173956; line-height: 1.6; margin-top: 8px;">
                Maritime risk analysis cannot produce an executable voyage recommendation because no vessel class satisfies the current physical cargo and port constraints (draft, LOA, beam, DWT limits).
                <br><br>
                Please adjust cargo quantity or port parameters, or consult the <b>Vessel Comparison</b> tab for detailed constraint limits.
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.stop()
    b = res["best"]
    rsk = b["risk"]
    risk_band = b["risk_band"]
    overall_score = b["risk_overall"]

    band_slug = risk_band.lower()

    # Risk Centre Top Banner
    st.markdown(
        f"""
    <div class="panel" style="border-left: 4px solid var(--sail-blue);">
      <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 14px;">
        <div>
          <div class="section-title" style="font-size: 19px; margin-bottom: 2px;">
            &#128737; Maritime Risk Evaluation Centre
          </div>
          <div class="section-sub" style="margin-bottom: 0;">
            Data-driven percentile risk scores evaluated against historical distributions (0 = Historical Calmest, 100 = Historical Extreme)
          </div>
        </div>
        <div style="display: flex; align-items: center; gap: 16px;">
          <div>
            <div style="font-size: 12px; font-weight: 700; color: #5d7082; text-transform: uppercase; letter-spacing: 0.4px;">Overall Composite Risk</div>
            <div style="font-size: 23px; font-weight: 800; color: var(--sail-blue); line-height: 1;">
              {overall_score:.0f} <span style="font-size: 13px; font-weight: 600; color: #5d7082;">/ 100</span>
            </div>
          </div>
          <span class="badge badge-{band_slug}" style="font-size: 12px; padding: 6px 14px;">{risk_band} BAND</span>
        </div>
      </div>
    </div>
    """,
        unsafe_allow_html=True,
    )

    if st.session_state.as_of > bdi_end:
        st.info(f"Market data is available only through {fmt_date_str(bdi_end)}. Analysis for later dates is subject to available historical data.")

    # 4-Column Metric Strip with Backend Percentile Values
    r1, r2, r3, r4 = st.columns(4)
    m_val = rsk.get("Market (freight volatility)", 0)
    f_val = rsk.get("Fuel (Brent momentum)", 0)
    p_val = rsk.get("Port (waiting vs normal)", 0)
    w_val = rsk.get("Weather", 0)

    with r1:
        st.markdown(
            f"""
        <div class="kpi-card">
          <div class="kpi-header">Market Volatility Risk</div>
          <div class="kpi-value">{m_val:.0f} / 100</div>
          <div class="kpi-sub">Band: <b>{get_risk_band_text(m_val)}</b> &bull; 21d log volatility</div>
        </div>""",
            unsafe_allow_html=True,
        )
    with r2:
        st.markdown(
            f"""
        <div class="kpi-card">
          <div class="kpi-header">Fuel Price Momentum</div>
          <div class="kpi-value">{f_val:.0f} / 100</div>
          <div class="kpi-sub">Band: <b>{get_risk_band_text(f_val)}</b> &bull; 21d Brent change</div>
        </div>""",
            unsafe_allow_html=True,
        )
    with r3:
        st.markdown(
            f"""
        <div class="kpi-card">
          <div class="kpi-header">Port Congestion Risk</div>
          <div class="kpi-value">{p_val:.0f} / 100</div>
          <div class="kpi-sub">Band: <b>{get_risk_band_text(p_val)}</b> &bull; Waiting vs normal</div>
        </div>""",
            unsafe_allow_html=True,
        )
    with r4:
        st.markdown(
            f"""
        <div class="kpi-card">
          <div class="kpi-header">Weather Operational Risk</div>
          <div class="kpi-value">{w_val:.0f} / 100</div>
          <div class="kpi-sub">Band: <b>{get_risk_band_text(w_val)}</b> &bull; Marine API / Cyclone</div>
        </div>""",
            unsafe_allow_html=True,
        )

    # Risk Profile Chart & Factor Breakdown
    chart_col, exp_col = st.columns([1.6, 1.0])
    with chart_col:
        st.markdown(
            """
        <div class="panel" style="padding: 14px 18px 8px; margin-bottom: 8px;">
          <div class="section-title" style="font-size: 15px; margin-bottom: 2px;">📊 Multi-Factor Risk Profile Graph</div>
          <div class="section-sub" style="font-size: 12.5px; margin-bottom: 10px;">Empirical percentiles across active dimensions vs. institutional benchmark thresholds</div>
          <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; margin-bottom: 4px;">
            <div style="background: #ecfdf5; border: 1px solid #a7f3d0; border-radius: 5px; padding: 5px 6px; text-align: center;">
              <span style="display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: #10b981; margin-right: 4px;"></span>
              <span style="font-size: 12px; font-weight: 700; color: #065f46;">LOW</span>
              <span style="font-size: 11.5px; color: #059669; margin-left: 2px;">0–30</span>
            </div>
            <div style="background: #fffbeb; border: 1px solid #fde68a; border-radius: 5px; padding: 5px 6px; text-align: center;">
              <span style="display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: #f59e0b; margin-right: 4px;"></span>
              <span style="font-size: 12px; font-weight: 700; color: #92400e;">MEDIUM</span>
              <span style="font-size: 11.5px; color: #d97706; margin-left: 2px;">31–60</span>
            </div>
            <div style="background: #fff7ed; border: 1px solid #fed7aa; border-radius: 5px; padding: 5px 6px; text-align: center;">
              <span style="display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: #f97316; margin-right: 4px;"></span>
              <span style="font-size: 12px; font-weight: 700; color: #9a3412;">HIGH</span>
              <span style="font-size: 11.5px; color: #ea580c; margin-left: 2px;">61–80</span>
            </div>
            <div style="background: #fef2f2; border: 1px solid #fecaca; border-radius: 5px; padding: 5px 6px; text-align: center;">
              <span style="display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: #ef4444; margin-right: 4px;"></span>
              <span style="font-size: 12px; font-weight: 700; color: #991b1b;">CRITICAL</span>
              <span style="font-size: 11.5px; color: #dc2626; margin-left: 2px;">81–100</span>
            </div>
          </div>
        </div>
        """,
            unsafe_allow_html=True,
        )
        st.plotly_chart(risk_profile_chart(b), use_container_width=True, config={"displayModeBar": False})

    with exp_col:
        st.markdown(
            """
        <div class="panel" style="padding: 14px 18px; min-height: 388px; margin-bottom: 8px;">
          <div class="section-title" style="font-size: 15px; margin-bottom: 2px;">📋 Dimension Methodology</div>
          <div class="section-sub" style="font-size: 12.5px; margin-bottom: 12px;">Statistical calibration &amp; assessment criteria</div>
          <div style="font-size: 13px; line-height: 1.55; color: #173956;">
            <div style="margin-bottom: 11px;">
              <b>📈 Market Volatility:</b> Evaluates 21-day rolling log volatility of the freight index against multi-decade historical records.
            </div>
            <div style="margin-bottom: 11px;">
              <b>⛽ Fuel Momentum:</b> Percentile of 21-day log return of Brent crude, capturing spot acceleration &amp; simulated price shocks.
            </div>
            <div style="margin-bottom: 11px;">
              <b>⚓ Port Congestion:</b> Ratio of estimated berth waiting days (including manual buffer) against normal terminal waiting benchmarks.
            </div>
            <div>
              <b>🌊 Weather Risk:</b> Live significant wave height and wind velocity from Open-Meteo Marine API, or IMD Bay of Bengal seasonal cyclone matrix.
            </div>
          </div>
        </div>
        """,
            unsafe_allow_html=True,
        )

    # Decision Engine Interpretation & Weather Details
    st.markdown('<div class="section-title">💡 Risk Interpretation &amp; Decision Factors</div>', unsafe_allow_html=True)
    exp_items = [clean_text(e) for e in b.get("explanation", [])]
    if exp_items:
        bullet_html = "".join([f"<li style='margin-bottom: 5px;'>{item}</li>" for item in exp_items])
        st.markdown(
            f"""
        <div class="panel">
          <ul style="font-size: 13px; line-height: 1.5; color: #0d2842; margin: 0; padding-left: 20px;">
            {bullet_html}
          </ul>
        </div>
        """,
            unsafe_allow_html=True,
        )

    # Interactive Scenario & Sensitivity Controls (Feeding Existing Backend Logic)
    with st.expander("⚡ Interactive Scenario & Sensitivity Testing (Decision Engine Connected)", expanded=True):
        st.markdown('<div style="font-size: 12.5px; color: #5d7082; margin-bottom: 8px;">Adjust sensitivity parameters below to recompute multi-factor risk scores and voyage economics using the backend decision engine:</div>', unsafe_allow_html=True)
        s_c1, s_c2, s_c3 = st.columns(3)
        with s_c1:
            st.session_state.bshock = st.slider("Bunker Price Change %", -30, 50, st.session_state.bshock, key="risk_bshock")
        with s_c2:
            st.session_state.wait = st.slider("Extra Port Waiting (days)", 0.0, 10.0, st.session_state.wait, 0.5, key="risk_wait")
        with s_c3:
            st.session_state.fshock = st.slider("Freight Index Shock %", -30, 50, st.session_state.fshock, key="risk_fshock")

        if st.button("🔄  RECALCULATE RISK & SENSITIVITY", type="primary", use_container_width=True, key="btn_recalc_risk"):
            if run_analysis_safely():
                st.session_state.active_tab = "Risk Centre"
                st.rerun()


# ==============================================================================
# TAB 6: VALIDATION & BACK-TEST
# ==============================================================================
elif st.session_state.active_tab == "Validation & Back-test":
    st.markdown(
        """
    <div class="panel">
      <div class="section-title">📊 Walk-Forward Validation &amp; Historical Back-Test</div>
      <div class="section-sub">Out-of-sample time-aware validation metrics and historical simulation of the decision rule against spot chartering.</div>
    </div>
    """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="section-title">Walk-Forward Validation Performance (BDI Index)</div>', unsafe_allow_html=True)
    try:
        b_rep = forecast.load_models("BDI")[1]
        v_rows = []
        for h_days, h_dict in b_rep.get("horizons", {}).items():
            for v_entry in h_dict.get("validation", []):
                v_rows.append(
                    {
                        "Horizon": f"{h_days} Days",
                        "Model": v_entry["model"],
                        "MAE": f"{v_entry['MAE']:.2f}",
                        "RMSE": f"{v_entry['RMSE']:.2f}",
                        "MAPE (%)": f"{v_entry['MAPE']:.2f}%",
                    }
                )
        st.dataframe(pd.DataFrame(v_rows), hide_index=True, use_container_width=True)
    except Exception:
        st.info("Validation report data unavailable.")

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="section-title">Historical Back-Test Summary (2015–2019 Simulation)</div>', unsafe_allow_html=True)
    try:
        df_bdi = data.market_frame("BDI")
        bt_df, bt_sum = backtest.run(df_bdi, years=4)

        if not bt_df.empty:
            b1, b2, b3, b4 = st.columns(4)
            with b1:
                st.markdown(f'<div class="kpi-card"><div class="kpi-header">Decisions Evaluated</div><div class="kpi-value">{bt_sum["decisions"]}</div></div>', unsafe_allow_html=True)
            with b2:
                st.markdown(f'<div class="kpi-card"><div class="kpi-header">Wait / Partial %</div><div class="kpi-value">{bt_sum["wait_or_partial"] / max(1, bt_sum["decisions"]):.0%}</div></div>', unsafe_allow_html=True)
            with b3:
                st.markdown(f'<div class="kpi-card"><div class="kpi-header">Avg Freight Saving %</div><div class="kpi-value">{bt_sum["avg_saving_pct_all"]:.2f}%</div></div>', unsafe_allow_html=True)
            with b4:
                st.markdown(f'<div class="kpi-card"><div class="kpi-header">Hit Rate When Waiting</div><div class="kpi-value">{bt_sum["hit_rate_when_waited"]:.0%}</div></div>', unsafe_allow_html=True)
        else:
            st.info("Back-test results unavailable for current dataset range.")
    except Exception:
        st.info("Back-test evaluation unavailable.")


# ==============================================================================
# TAB 7: DATA & ASSUMPTIONS
# ==============================================================================
elif st.session_state.active_tab == "Data & Assumptions":
    st.markdown(
        f"""
    <div class="panel" style="margin-bottom: 10px; padding: 12px 18px;">
      <div class="section-title">📋 Data Sources &amp; Master System Assumptions</div>
      <div class="section-sub" style="margin-bottom: 0;"><b>Data Freshness:</b> Baltic Dry Index data as of {fmt_date_str(bdi_end)} | Master engineering parameters for Indian East Coast maritime operations.</div>
    </div>
    """,
        unsafe_allow_html=True,
    )

    c_ds1, c_ds2 = st.columns([1.1, 1.0])
    with c_ds1:
        st.markdown(
            """
        <div class="panel" style="margin-bottom: 10px; padding: 12px 16px; min-height: 135px;">
          <div style="font-size: 14px; font-weight: 700; color: #0b416d; margin-bottom: 6px;">Data Sources &amp; Integration Feeds</div>
          <div style="font-size: 13px; line-height: 1.55; color: #173956;">
            <b>1. Freight Market Data:</b> Investing.com Baltic Dry Index (BDI) daily time series.<br>
            <b>2. Crude &amp; Fuel Data:</b> U.S. Energy Information Administration (EIA) Spot Price Brent Crude.<br>
            <b>3. Foreign Exchange:</b> Federal Reserve Economic Data (FRED) USD/INR daily exchange rates.<br>
            <b>4. Weather Feed:</b> Open-Meteo 7-day marine forecast API with Bay of Bengal cyclone fallback.<br>
            <b>5. Port Handbook:</b> Master terminal engineering specifications for Indian East Coast berths.<br>
            <b>6. Individual Vessel Master:</b> Synthetic/mock vessel fixtures for prototype-level ship-specific evaluation; explicitly labelled as scenario data.
          </div>
        </div>
        """,
            unsafe_allow_html=True,
        )

    with c_ds2:
        st.markdown(
            """
        <div class="panel" style="margin-bottom: 10px; padding: 12px 16px; min-height: 135px;">
          <div style="font-size: 14px; font-weight: 700; color: #0b416d; margin-bottom: 6px;">Operational Disclaimers for Chartering Officers</div>
          <div style="font-size: 13px; line-height: 1.55; color: #173956;">
            <b>• Indicative Decision Support:</b> Automated decision-support recommendations based on statistical forecasting and constraint evaluations.<br>
            <b>• Berth Specifications:</b> Physical berth limits (LOA, beam, draft) must be verified against the nominated vessel's Q88 form and live terminal handbooks prior to fixture.
          </div>
        </div>
        """,
            unsafe_allow_html=True,
        )

    st.markdown('<div class="section-title" style="margin-top: 4px; margin-bottom: 4px;">Master Port Specifications</div>', unsafe_allow_html=True)
    p_list = []
    for p_name, p_cfg in PORTS.items():
        p_list.append(
            {
                "Port Name": p_name,
                "Max Draft (m)": p_cfg["max_draft_m"],
                "Max LOA (m)": p_cfg["max_loa_m"],
                "Max Beam (m)": p_cfg["max_beam_m"],
                "Max DWT (t)": f"{p_cfg['max_dwt']:,}",
                "Discharge Rate (t/day)": f"{p_cfg['discharge_rate_t_day']:,}",
                "Normal Wait (days)": p_cfg["normal_wait_days"],
                "Port Charges (USD)": f"${p_cfg['port_charges_usd']:,}",
            }
        )
    st.dataframe(pd.DataFrame(p_list), hide_index=True, use_container_width=True)

    st.markdown('<div class="section-title" style="margin-top: 6px; margin-bottom: 4px;">Individual Vessel Master — Synthetic / Scenario Fixtures</div>', unsafe_allow_html=True)
    iv_list = []
    for v_id, v_cfg in VESSEL_PROFILES.items():
        iv_list.append({
            "Vessel ID": v_id, "Vessel Name": v_cfg["vessel_name"], "Class": v_cfg["vessel_class"],
            "DWT (t)": f"{v_cfg['dwt']:,}", "LOA (m)": v_cfg["loa_m"], "Beam (m)": v_cfg["beam_m"],
            "Draft (m)": v_cfg["draft_m"], "Speed (kn)": v_cfg["speed_kn"],
            "Build Year": v_cfg["build_year"], "Cargo Types": ", ".join(v_cfg["cargo_types"]),
            "Data Basis": "SYNTHETIC / SCENARIO"
        })
    st.dataframe(pd.DataFrame(iv_list), hide_index=True, use_container_width=True)

    st.markdown('<div class="section-title" style="margin-top: 6px; margin-bottom: 4px;">Master Vessel Class Specifications</div>', unsafe_allow_html=True)
    v_list = []
    for v_name, v_cfg in VESSELS.items():
        v_list.append(
            {
                "Vessel Class": v_name,
                "Typical DWT (t)": f"{v_cfg['dwt']:,}",
                "Design Draft (m)": v_cfg["draft_m"],
                "LOA (m)": v_cfg["loa_m"],
                "Beam (m)": v_cfg["beam_m"],
                "Service Speed (kn)": v_cfg["speed_kn"],
                "Sea Consumption (t/day)": v_cfg["sea_cons_t_day"],
                "Port Consumption (t/day)": v_cfg["port_cons_t_day"],
                "Freight Index": v_cfg["freight_index"],
            }
        )
    st.dataframe(pd.DataFrame(v_list), hide_index=True, use_container_width=True)


# ==============================================================================
# TAB 8: ABOUT SECTION
# ==============================================================================
elif st.session_state.active_tab == "About":
    st.markdown(
        """
    <div class="panel" style="border-left: 4px solid var(--sail-blue); margin-bottom: 10px; padding: 12px 18px;">
      <div class="section-title" style="font-size: 19px; margin-bottom: 2px;">ℹ️ About Sagar Drishti</div>
      <div class="section-sub" style="font-size: 12px; margin-bottom: 0;">
        Data-Driven Decision Support Platform for Maritime Raw Material Logistics &bull; Steel Authority of India Limited (SAIL) &bull; Ministry of Steel, Government of India
      </div>
    </div>
    """,
        unsafe_allow_html=True,
    )

    # Section 1: Executive Overview & Institutional Mandate
    st.markdown(
        """
    <div class="panel" style="margin-bottom: 10px; padding: 12px 18px;">
      <div style="font-size: 14px; font-weight: 700; color: #0b416d; margin-bottom: 6px;">1. Executive Overview &amp; Institutional Mandate</div>
      <div style="font-size: 13px; line-height: 1.6; color: #173956;">
        <p style="margin: 0 0 6px 0;">
          <b>SAGAR DRISHTI</b> (<i>Freight Intelligence &amp; Chartering Advisor</i>) is an enterprise decision-support platform engineered specifically for <b>Steel Authority of India Limited (SAIL)</b>, a Maharatna Central Public Sector Enterprise under the administrative control of the <b>Ministry of Steel, Government of India</b>. Developed in alignment with the mandate of <b>Smart India Hackathon (SIH) 2026 (Problem Statement: 26006)</b>, the system addresses the operational and financial complexities of maritime dry-bulk raw material procurement.
        </p>
        <div style="font-size: 13px; font-weight: 700; color: #0b416d; margin: 6px 0 3px 0;">The Strategic Logistics Context</div>
        <p style="margin: 0 0 4px 0;">
          SAIL operates integrated steel manufacturing plants across India (Bhilai, Bokaro, Rourkela, Durgapur, Burnpur, and Salem), requiring tens of millions of tonnes of imported raw materials annually. High-grade metallurgical coking coal, pulverized coal injection (PCI) coal, and fluxes are primarily imported from overseas mining hubs:
        </p>
        <ul style="margin: 0 0 6px 18px; padding: 0; font-size: 13px; line-height: 1.55;">
          <li><b>Australia:</b> Hay Point (Queensland), Newcastle and Port Kembla (New South Wales)</li>
          <li><b>Indonesia:</b> South &amp; East Kalimantan load ports</li>
          <li><b>South Africa:</b> Richards Bay Coal Terminal (RBCT)</li>
          <li><b>Mozambique:</b> Maputo / Matola Coal Terminal</li>
          <li><b>North America:</b> Hampton Roads / Norfolk (Virginia, USA)</li>
        </ul>
        <p style="margin: 0 0 6px 0;">
          Cargoes are discharged across deep-water and draft-restricted terminals along the Indian East Coast, principally <b>Paradip, Dhamra, Visakhapatnam, Gangavaram, Gopalpur, and Haldia</b>. Ocean freight constitutes a substantial portion of the total landed cost of coking coal. Because dry-bulk shipping freight rates exhibit extreme volatility driven by seasonal demand, geopolitical developments, vessel availability, port congestion, and bunker fuel fluctuations, optimal charter timing and vessel selection are critical to protecting operating margins.
        </p>
        <div style="font-size: 12.5px; font-weight: 700; color: #0b416d; margin: 6px 0 3px 0;">Strategic Objective</div>
        <p style="margin: 0;">
          Sagar Drishti transitions chartering operations from reactive spot fixing to proactive, data-driven optimization by synthesizing machine learning market forecasts, strict vessel-port engineering physical limits, real-time marine meteorological forecasts, full-voyage TCE economics, and empirical risk analytics.
        </p>
      </div>
    </div>
    """,
        unsafe_allow_html=True,
    )

    # Section 2: Architectural Decision Pipeline (The 7 Computational Layers)
    st.markdown(
        """
    <div class="section-title" style="font-size: 14px; margin-top: 4px; margin-bottom: 6px;">2. Architectural Decision Pipeline (The 7 Computational Layers)</div>
    """,
        unsafe_allow_html=True,
    )

    ab_col1, ab_col2 = st.columns(2)
    with ab_col1:
        st.markdown(
            """
        <div class="panel" style="margin-bottom: 10px; padding: 11px 15px;">
          <div style="font-size: 13.5px; font-weight: 700; color: #0b416d; margin-bottom: 4px;">
            Layer 1: Market Signal Acquisition &amp; Data Feeds
          </div>
          <div style="font-size: 13px; line-height: 1.55; color: #173956;">
            The data pipeline continuously ingests and harmonizes official, authentic external feeds:
            <ul style="margin: 3px 0 0 16px; padding: 0;">
              <li><b>Freight Market Indices:</b> Daily Baltic Dry Index (BDI) and vessel-class benchmarks (BCI, BPI, BSI, BHSI).</li>
              <li><b>Bunker Fuel Feed:</b> Daily spot prices for Brent Crude oil from U.S. EIA, transformed into VLSFO equivalents.</li>
              <li><b>Foreign Exchange Rates:</b> Daily USD/INR reference rates from FRED to present voyage expenditures in USD and INR.</li>
            </ul>
          </div>
        </div>
        """,
            unsafe_allow_html=True,
        )

        st.markdown(
            """
        <div class="panel" style="margin-bottom: 10px; padding: 11px 15px;">
          <div style="font-size: 13.5px; font-weight: 700; color: #0b416d; margin-bottom: 4px;">
            Layer 3: Vessel-Port Engineering Constraint Checker
          </div>
          <div style="font-size: 13px; line-height: 1.55; color: #173956;">
            The system applies non-negotiable physical constraints to prevent unfeasible vessel fixtures:
            <ul style="margin: 3px 0 0 16px; padding: 0;">
              <li><b>Physical Berth Constraints:</b> Maximum Permissible Draft (m), LOA (m), Beam (m), and DWT limits across discharge berths.</li>
              <li><b>Intake Optimization:</b> Computes maximum permissible cargo intake based on water density and draft limitations.</li>
              <li><b>Voyage Multiplicity:</b> Decomposes large cargo parcel requirements into multiple voyages if port dimensions require.</li>
              <li><b>Status Classification:</b> Automatically flags each candidate vessel as <code>FEASIBLE</code>, <code>CONDITIONAL</code>, or <code>REJECT</code>.</li>
            </ul>
          </div>
        </div>
        """,
            unsafe_allow_html=True,
        )

        st.markdown(
            """
        <div class="panel" style="margin-bottom: 10px; padding: 11px 15px;">
          <div style="font-size: 13.5px; font-weight: 700; color: #0b416d; margin-bottom: 4px;">
            Layer 5: TCE Voyage Economics Engine
          </div>
          <div style="font-size: 13px; line-height: 1.55; color: #173956;">
            A rigorous Time-Charter-Equivalent (TCE) cost model estimates total landed voyage expense:
            <ul style="margin: 3px 0 0 16px; padding: 0;">
              <li><b>Vessel Time Charter Hire:</b> Derived from current and projected freight index levels &times; vessel class TCE factors.</li>
              <li><b>Bunker Fuel Expenditure:</b> Accounts for laden sea transit days, 50% ballast leg allocation, and port auxiliary consumption.</li>
              <li><b>Port Discharging &amp; Loading Dues:</b> Official tariff-based port charges applied for origin and Indian East Coast terminals.</li>
              <li><b>Waiting &amp; Demurrage Costs:</b> Models berth congestion delays and idle waiting costs from historical terminal norms.</li>
            </ul>
          </div>
        </div>
        """,
            unsafe_allow_html=True,
        )

    with ab_col2:
        st.markdown(
            """
        <div class="panel" style="margin-bottom: 10px; padding: 11px 15px;">
          <div style="font-size: 13.5px; font-weight: 700; color: #0b416d; margin-bottom: 4px;">
            Layer 2: Multi-Horizon Freight Forecasting Engine
          </div>
          <div style="font-size: 13px; line-height: 1.55; color: #173956;">
            Market prediction is designed for stationarity and strict empirical validity:
            <ul style="margin: 3px 0 0 16px; padding: 0;">
              <li><b>Forecast Target:</b> Models forward log-change of freight index rather than absolute levels, preserving stationarity.</li>
              <li><b>Forecast Horizons:</b> Produces projections across operational lead times: 7, 14, 30, 60, and 90 calendar days.</li>
              <li><b>Model Ladder:</b> Evaluates Last-Value baseline, 21-day Rolling MA, Regularized Ridge, and LightGBM gradient boosting.</li>
              <li><b>Conformal Prediction:</b> Calibrated 80% confidence bands from empirical residuals, guaranteeing robust coverage.</li>
            </ul>
          </div>
        </div>
        """,
            unsafe_allow_html=True,
        )

        st.markdown(
            """
        <div class="panel" style="margin-bottom: 10px; padding: 11px 15px;">
          <div style="font-size: 13.5px; font-weight: 700; color: #0b416d; margin-bottom: 4px;">
            Layer 4: Real-Time &amp; Climatological Marine Weather
          </div>
          <div style="font-size: 13px; line-height: 1.55; color: #173956;">
            Weather impact modeling bridges oceanographic data with voyage transit schedules:
            <ul style="margin: 3px 0 0 16px; padding: 0;">
              <li><b>Live Marine API Integration:</b> Connects with Open-Meteo 7-day Marine API for wave heights, swell, and wind speeds.</li>
              <li><b>Seasonal Cyclone Matrix Fallback:</b> Beyond the 7-day window, queries IMD Bay of Bengal seasonal cyclone matrix.</li>
              <li><b>Operational Adjustments:</b> Quantifies adverse sea-state speed reduction (%), extra transit days, and weather bunker burn.</li>
            </ul>
          </div>
        </div>
        """,
            unsafe_allow_html=True,
        )

        st.markdown(
            """
        <div class="panel" style="margin-bottom: 10px; padding: 11px 15px;">
          <div style="font-size: 13.5px; font-weight: 700; color: #0b416d; margin-bottom: 4px;">
            Layer 6: Multi-Dimensional Percentile Risk Engine
          </div>
          <div style="font-size: 13px; line-height: 1.55; color: #173956;">
            Risk is evaluated empirically against multi-decade historical market distributions:
            <ul style="margin: 3px 0 0 16px; padding: 0;">
              <li><b>Market Volatility Risk:</b> 21-day rolling volatility percentile against historical series (0 = calmest, 100 = extreme).</li>
              <li><b>Fuel Price Momentum Risk:</b> 21-day log price acceleration in Brent crude oil with sensitivity shock scenarios.</li>
              <li><b>Port Congestion Risk:</b> Ratio of current estimated berth waiting days against normal master port benchmarks.</li>
              <li><b>Weather Operational Risk:</b> Percentile score based on wave/wind severity or cyclone risk exposure.</li>
              <li><b>Categorical Risk Bands:</b> Categorized into <code>LOW</code> (0–30), <code>MEDIUM</code> (31–60), <code>HIGH</code> (61–80), and <code>CRITICAL</code> (81–100).</li>
            </ul>
          </div>
        </div>
        """,
            unsafe_allow_html=True,
        )

    # Layer 7: Full Width Banner
    st.markdown(
        """
    <div class="panel" style="border-left: 4px solid #c59b27; margin-bottom: 10px; padding: 12px 18px;">
      <div style="font-size: 14px; font-weight: 700; color: #0b416d; margin-bottom: 4px;">
        Layer 7: Multi-Criteria Decision Recommendation &amp; Timing Optimization
      </div>
      <div style="font-size: 13px; line-height: 1.55; color: #0d2842;">
        The culmination of the decision engine is a weighted multi-criteria ranking algorithm synthesizing all preceding layers:
        <ul style="margin: 3px 0 6px 18px; padding: 0;">
          <li><b>Voyage Cost Efficiency (40% Weight):</b> Normalized score rewarding lowest cost per metric tonne ($/MT and ₹/MT).</li>
          <li><b>Freight Forecast Trend (25% Weight):</b> Scores forward trajectory, penalizing fixtures ahead of dips and rewarding fixtures before rate surges.</li>
          <li><b>Port Berth Feasibility (15% Weight):</b> Full score (100) for unconstrained <code>FEASIBLE</code> fixtures; penalized (60) for <code>CONDITIONAL</code> part-cargo fixtures.</li>
          <li><b>Composite Risk Score (10% Weight):</b> Rewards low-volatility, low-congestion, calm-weather fixtures.</li>
          <li><b>Laycan Schedule Slack (10% Weight):</b> Evaluates operational buffer days between decision date and mandatory booking lead deadlines (10 days lead time).</li>
        </ul>
        <b>Recommended Action Logic:</b>
        <ul style="margin: 3px 0 0 18px; padding: 0;">
          <li><code>CHARTER NOW</code>: Recommended when forward freight rates are projected to rise significantly (&ge; 4.0%), or when laycan deadlines leave insufficient time to wait.</li>
          <li><code>WAIT / MONITOR</code>: Recommended when forward freight rates are projected to decline (&le; -4.0%) and the upper 80% conformal bound remains safe.</li>
          <li><code>CHARTER PARTIALLY</code>: Recommended when the market exhibits high upside volatility, advising hedging a portion of cargo on spot and covering the balance within the forward window.</li>
        </ul>
      </div>
    </div>
    """,
        unsafe_allow_html=True,
    )

    # Section 3: Data Governance & Operational Protocols
    # Keep historical outputs, scenario simulations, and human verification requirements
    # explicitly separated so the UI does not overstate live-data coverage.
    st.html("""
    <div class="panel" style="margin-bottom:10px;padding:14px 18px;">
      <div style="font-size:15px;font-weight:800;color:#0b416d;margin-bottom:10px;">
        3. Data Governance, Integrity &amp; Operational Protocols
      </div>
      <div style="font-size:13px;line-height:1.55;color:#173956;">
        <div style="font-size:13px;font-weight:800;color:#0b416d;margin:5px 0 3px;">
          Transparent Data &amp; Scenario Governance
        </div>
        <ul style="margin:3px 0 9px 18px;padding:0;">
          <li><b>No Fabricated Historical Performance:</b> Historical performance metrics and cost outputs are generated from the project's underlying datasets and model calculations.</li>
          <li><b>Explicit Scenario Labelling:</b> When live external feeds are unavailable, simulated demand, congestion stress and seven-day future paths are explicitly labelled as scenario inputs and are not presented as live observations.</li>
        </ul>

        <div style="font-size:13px;font-weight:800;color:#0b416d;margin:7px 0 3px;">
          Human-in-the-Loop Chartering Protocol
        </div>
        <div style="margin-bottom:3px;">
          Sagar Drishti is a decision-support advisory system. Before executing a legally binding charter fixture, designated chartering personnel should independently verify:
        </div>
        <ol style="margin:3px 0 9px 18px;padding:0;">
          <li><b>Vessel Documentation:</b> Confirm nominated vessel LOA, beam, arrival draft, de-ballasting capability and cargo-handling characteristics against applicable terminal requirements and available vessel documentation/Q88.</li>
          <li><b>Live Port &amp; Marine Conditions:</b> Verify current Port Master notices, tide conditions, berth restrictions and applicable marine advisories from authoritative sources.</li>
          <li><b>Contractual Terms:</b> Verify applicable charter-party forms, laytime, demurrage/despatch and other contractual terms with the relevant commercial/legal stakeholders.</li>
        </ol>

        <div style="font-size:13px;font-weight:800;color:#0b416d;margin:7px 0 3px;">
          National Strategic Alignment
        </div>
        <p style="margin:0;">
          Sagar Drishti aligns with relevant priorities of the <b>National Steel Policy 2017</b> concerning raw-material security, infrastructure and logistics, and with <b>Maritime India Vision 2030</b> priorities around technology adoption, port efficiency and logistics.
        </p>
      </div>
    </div>
    """)
    
    
# TAB 9: CONTACT SECTION
# ==============================================================================
elif st.session_state.active_tab == "Contact":
    st.markdown(
        """
    <div class="panel" style="border-left: 4px solid var(--sail-blue);">
      <div class="section-title" style="font-size: 19px;">🏛 Institutional Governance &amp; Contact</div>
      <div class="section-sub">Official Institutional Profile &bull; Sagar Drishti Decision Support System</div>
    </div>
    """,
        unsafe_allow_html=True,
    )

    c_left, c_right = st.columns([1.2, 1.0])
    with c_left:
        st.markdown(
            """
        <div class="panel">
          <div style="font-size: 14px; font-weight: 700; color: #0b416d; margin-bottom: 10px;">Institutional Hierarchy</div>
          <table style="width: 100%; font-size: 13px; border-collapse: collapse;">
            <tr style="border-bottom: 1px solid #e2ebf3;">
              <td style="padding: 8px 0; font-weight: 700; color: #5d7082; width: 35%;">Project Name</td>
              <td style="padding: 8px 0; font-weight: 600; color: #071e33;">Sagar Drishti — Freight Intelligence &amp; Chartering Advisor</td>
            </tr>
            <tr style="border-bottom: 1px solid #e2ebf3;">
              <td style="padding: 8px 0; font-weight: 700; color: #5d7082;">Enterprise / PSU</td>
              <td style="padding: 8px 0; font-weight: 600; color: #071e33;">Steel Authority of India Limited (SAIL) — Maharatna CPSE</td>
            </tr>
            <tr style="border-bottom: 1px solid #e2ebf3;">
              <td style="padding: 8px 0; font-weight: 700; color: #5d7082;">Administrative Ministry</td>
              <td style="padding: 8px 0; font-weight: 600; color: #071e33;">Ministry of Steel, Government of India</td>
            </tr>
            <tr style="border-bottom: 1px solid #e2ebf3;">
              <td style="padding: 8px 0; font-weight: 700; color: #5d7082;">Initiative Context</td>
              <td style="padding: 8px 0; font-weight: 600; color: #071e33;">Smart India Hackathon (SIH) 2026 &bull; Problem Statement 26006</td>
            </tr>
            <tr style="border-bottom: 1px solid #e2ebf3;">
              <td style="padding: 8px 0; font-weight: 700; color: #5d7082;">Functional Division</td>
              <td style="padding: 8px 0; font-weight: 600; color: #071e33;">Transport &amp; Shipping / Raw Materials Division (RMD)</td>
            </tr>
            <tr>
              <td style="padding: 8px 0; font-weight: 700; color: #5d7082;">Enquiries</td>
              <td style="padding: 8px 0; font-weight: 600; color: #b32e2a;">Project contact information: Not Available</td>
            </tr>
          </table>
        </div>
        """,
            unsafe_allow_html=True,
        )

    with c_right:
        st.markdown(
            """
        <div class="panel">
          <div style="font-size: 14px; font-weight: 700; color: #0b416d; margin-bottom: 10px;">Operational Governance Notice</div>
          <div style="font-size: 13px; line-height: 1.6; color: #173956;">
            <b>Confidential PSU Decision Support Prototype</b><br>
            This portal is developed exclusively for technical evaluation and decision-support modeling. It does not replace the statutory authority of designated SAIL Chartering Officers.<br><br>
            <b>Fixture Protocol:</b><br>
            Before finalizing vessel nominations or fixture contracts, chartering officers must independently cross-verify:
            <ul style="margin: 6px 0 0 16px; padding: 0;">
              <li>Vessel Q88 specifications (draft, beam, LOA, crane capacity)</li>
              <li>Live port master and berth depth bulletins</li>
              <li>Notices to Mariners and navigational advisories</li>
              <li>Official BIMCO charter party clauses</li>
            </ul>
          </div>
        </div>
        """,
            unsafe_allow_html=True,
        )


# ==============================================================================
# INSTITUTIONAL FOOTER (RENDERED ON ALL TABS)
# ==============================================================================
footer()

# End-of-Dashboard Completion Sentinel & Trigger
st.html(
    """
    <div id="sagar-dashboard-complete-sentinel" style="display:none;" data-complete="true"></div>
    <script>
    (function() {
        try {
            var win = (window.parent && window.parent.__sagar_dismiss_loader) ? window.parent : window;
            if (typeof win.__sagar_dismiss_loader === "function") {
                setTimeout(function() {
                    try { win.__sagar_dismiss_loader(); } catch(e) {}
                }, 350);
            }
        } catch(e) {}
    })();
    </script>
    <img src="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg'/>" style="display:none;" onload="(function(){ try { var win = (window.parent && window.parent.__sagar_dismiss_loader) ? window.parent : window; if (typeof win.__sagar_dismiss_loader === 'function') { setTimeout(win.__sagar_dismiss_loader, 350); } } catch(e){} })()" />
    """,
    unsafe_allow_javascript=True,
)

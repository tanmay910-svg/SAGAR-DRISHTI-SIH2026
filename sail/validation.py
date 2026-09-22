"""Input normalization and boundary validation layer for SAIL Sagar Drishti.
Validates all parameters at the application and engine boundary before mathematical
or econometric model execution.
"""
from __future__ import annotations

import datetime as dt
import math
import re
from typing import Any

from .config import PORTS, ROUTES
from .exceptions import (
    InvalidInputError,
    UnknownPortError,
    UnknownRouteError,
)


def normalize_cargo(cargo_mt: Any) -> float:
    """Safely coerce and validate cargo quantity.
    
    Rejects non-numeric values, negative numbers, and zero.
    """
    if isinstance(cargo_mt, bool):
        raise InvalidInputError("Cargo quantity must be a valid number.")
    if isinstance(cargo_mt, str):
        cleaned = cargo_mt.strip().replace(",", "")
        try:
            val = float(cleaned)
        except ValueError:
            raise InvalidInputError(f"Invalid cargo quantity '{cargo_mt}'. Must be a valid positive number.")
    elif isinstance(cargo_mt, (int, float)):
        val = float(cargo_mt)
    else:
        raise InvalidInputError("Cargo quantity must be a valid number.")

    if math.isnan(val) or math.isinf(val):
        raise InvalidInputError("Cargo quantity must be a finite number.")

    if val <= 0:
        raise InvalidInputError("Cargo quantity must be greater than 0 MT.")

    return val


def parse_date_safe(d: Any, field_name: str = "Date") -> dt.date:
    """Parse date safely across expected formats:
    - YYYY-MM-DD
    - DD-MM-YYYY
    - YYYY/MM/DD
    - DD/MM/YYYY
    - Python date/datetime objects
    
    Rejects invalid or ambiguous date representations.
    """
    if isinstance(d, dt.datetime):
        return d.date()
    if isinstance(d, dt.date):
        return d
    if not isinstance(d, str):
        raise InvalidInputError(f"Invalid {field_name}: expected date string or date object.")

    s = d.strip()
    if not s:
        raise InvalidInputError(f"{field_name} cannot be empty.")

    # Try explicit formats to preserve day/month/year semantics unambiguously
    patterns = [
        (r"^\d{4}-\d{2}-\d{2}$", "%Y-%m-%d"),
        (r"^\d{2}-\d{2}-\d{4}$", "%d-%m-%Y"),
        (r"^\d{4}/\d{2}/\d{2}$", "%Y/%m/%d"),
        (r"^\d{2}/\d{2}/\d{4}$", "%d/%m/%Y"),
    ]

    for regex, fmt in patterns:
        if re.match(regex, s):
            try:
                return dt.datetime.strptime(s, fmt).date()
            except ValueError as e:
                raise InvalidInputError(f"Invalid {field_name} calendar value: '{s}'. {e}")

    # Fallback to standard ISO format
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        raise InvalidInputError(
            f"Invalid {field_name} format or value: '{s}'. Expected YYYY-MM-DD, DD-MM-YYYY, or YYYY/MM/DD."
        )


def normalize_port(port: str) -> str:
    """Normalize user-entered port name:
    - Strip leading/trailing whitespace
    - Case-insensitive lookup against configured PORTS
    - Handle space vs underscore variations
    """
    if not isinstance(port, str) or not port.strip():
        raise UnknownPortError("Selected port is not available in the current port configuration.")

    cleaned = port.strip()
    if cleaned in PORTS:
        return cleaned

    # Case-insensitive and whitespace/underscore mapping
    norm_map = {}
    for p in PORTS:
        norm_map[p.lower()] = p
        norm_map[p.lower().replace("_", " ")] = p
        norm_map[p.lower().replace(" ", "_")] = p

    q = cleaned.lower()
    if q in norm_map:
        return norm_map[q]
    if q.replace(" ", "_") in norm_map:
        return norm_map[q.replace(" ", "_")]
    if q.replace("_", " ") in norm_map:
        return norm_map[q.replace("_", " ")]

    raise UnknownPortError(f"Selected port '{port}' is not available in the current port configuration.")


def normalize_route(origin: str) -> str:
    """Normalize origin route name:
    - Strip leading/trailing whitespace
    - Case-insensitive lookup against configured ROUTES
    """
    if not isinstance(origin, str) or not origin.strip():
        raise UnknownRouteError("Selected origin route is not available in the current route configuration.")

    cleaned = origin.strip()
    if cleaned in ROUTES:
        return cleaned

    norm_map = {k.lower(): k for k in ROUTES}
    if cleaned.lower() in norm_map:
        return norm_map[cleaned.lower()]

    raise UnknownRouteError(f"Selected origin route '{origin}' is not available in the current route configuration.")


def normalize_shocks(
    bunker_shock_pct: Any = 0.0,
    extra_wait_days: Any = 0.0,
    freight_shock_pct: Any = 0.0,
) -> tuple[float, float, float]:
    """Coerce and validate stress shock parameters."""
    # 1. Bunker shock pct
    if isinstance(bunker_shock_pct, bool):
        raise InvalidInputError("Bunker shock must be a valid number.")
    try:
        bs = float(str(bunker_shock_pct).strip())
    except (ValueError, TypeError):
        raise InvalidInputError(f"Invalid bunker shock parameter: '{bunker_shock_pct}'. Must be numeric.")
    if math.isnan(bs) or math.isinf(bs):
        raise InvalidInputError("Bunker shock must be a finite number.")
    if bs <= -100:
        raise InvalidInputError("Bunker shock must be greater than -100%.")

    # 2. Extra waiting days
    if isinstance(extra_wait_days, bool):
        raise InvalidInputError("Additional waiting days must be a valid number.")
    try:
        ew = float(str(extra_wait_days).strip())
    except (ValueError, TypeError):
        raise InvalidInputError(f"Invalid additional waiting days parameter: '{extra_wait_days}'. Must be numeric.")
    if math.isnan(ew) or math.isinf(ew):
        raise InvalidInputError("Additional waiting days must be a finite number.")
    if ew < 0:
        raise InvalidInputError("Additional waiting days cannot be negative.")

    # 3. Freight shock pct
    if isinstance(freight_shock_pct, bool):
        raise InvalidInputError("Freight shock must be a valid number.")
    try:
        fs = float(str(freight_shock_pct).strip())
    except (ValueError, TypeError):
        raise InvalidInputError(f"Invalid freight shock parameter: '{freight_shock_pct}'. Must be numeric.")
    if math.isnan(fs) or math.isinf(fs):
        raise InvalidInputError("Freight shock must be a finite number.")
    if fs <= -100:
        raise InvalidInputError("Freight shock must be greater than -100%.")

    return bs, ew, fs

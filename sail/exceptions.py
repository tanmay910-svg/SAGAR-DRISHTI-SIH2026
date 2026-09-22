"""Controlled exception hierarchy for SAIL Sagar Drishti freight intelligence.
Ensures specific, meaningful error classification across UI, API, and calculation layers.
"""

class SailError(Exception):
    """Base exception for all SAIL freight intelligence operations."""
    pass


class InvalidInputError(SailError, ValueError):
    """Raised when user or API input parameters are invalid or out of permissible bounds."""
    pass


class InsufficientHistoryError(SailError, ValueError):
    """Raised when the selected analysis date has insufficient historical data for rolling feature models."""
    pass


class UnknownPortError(SailError, KeyError, ValueError):
    """Raised when a requested port is not found in the port configuration database."""
    pass


class UnknownRouteError(SailError, KeyError, ValueError):
    """Raised when a requested origin route is not found in the route configuration database."""
    pass


class ConfigurationError(SailError, ValueError):
    """Raised when port, vessel, or system configuration contains invalid parameters (e.g. zero normal waiting days)."""
    pass


class NoFeasibleVesselError(SailError):
    """Raised or referenced when no vessel satisfies physical and operational draft/capacity constraints."""
    pass


class ModelProcessingError(SailError):
    """Raised when internal econometric/machine learning model prediction fails."""
    pass

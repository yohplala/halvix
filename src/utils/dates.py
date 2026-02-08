"""
Date utility functions for Halvix.

Provides common date conversion and cycle timing helpers used across
the codebase, particularly in pattern analysis and visualization.
"""

from datetime import date, timedelta
from typing import Any

from config import (
    EXPECTED_PEAK_DAYS_AFTER_HALVING,
    HALVING_DATES,
)


def to_date(value: Any) -> date:
    """
    Convert a timestamp, datetime, or date-like object to a date.

    Handles pandas Timestamps, datetime objects, and date objects uniformly.

    Args:
        value: A date-like object (Timestamp, datetime, date, or similar)

    Returns:
        A date object

    Examples:
        >>> import pandas as pd
        >>> to_date(pd.Timestamp("2024-01-15"))
        datetime.date(2024, 1, 15)
        >>> to_date(datetime.date(2024, 1, 15))
        datetime.date(2024, 1, 15)
    """
    if hasattr(value, "date") and callable(value.date):
        return value.date()
    if isinstance(value, date):
        return value
    # Fallback: try to convert via string if possible
    raise TypeError(f"Cannot convert {type(value).__name__} to date")


def get_expected_cycle_peak_date(cycle_num: int) -> date | None:
    """
    Get the expected peak date for a halving cycle.

    The peak typically occurs ~550 days (18 months) after the halving,
    based on historical BTC cycle patterns.

    Args:
        cycle_num: Cycle number (1-5). Cycle 1 is the 2012 halving.

    Returns:
        Expected peak date, or None if cycle number is invalid.

    Examples:
        >>> get_expected_cycle_peak_date(5)  # Cycle 5 (2028 halving)
        datetime.date(2029, 10, 2)  # ~550 days after 2028-03-31
    """
    if cycle_num < 1:
        return None

    # Get halving date for this cycle
    if cycle_num <= len(HALVING_DATES):
        halving_date = HALVING_DATES[cycle_num - 1]
    else:
        return None

    return halving_date + timedelta(days=EXPECTED_PEAK_DAYS_AFTER_HALVING)


def get_halving_date(cycle_num: int) -> date | None:
    """
    Get the halving date for a cycle number.

    Args:
        cycle_num: Cycle number (1-5). Cycle 1 is the 2012 halving.

    Returns:
        Halving date, or None if cycle number is invalid.
    """
    if cycle_num < 1:
        return None

    if cycle_num <= len(HALVING_DATES):
        return HALVING_DATES[cycle_num - 1]
    else:
        return None

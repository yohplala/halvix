"""
Bitcoin halving calendar: known halvings plus a forecast of the next one.

``config.HALVING_DATES`` lists only halvings that have actually happened. The
next halving is forecast here; once its real date is added to the config, the
real date is used instead and the forecast moves on to the following halving.

The forecast aligns the new cycle with the past ones on their bottoms: once the
current bear-market bottom is confirmed (a ``BTC_CYCLE_BOTTOMS`` entry after
the last halving), the next halving is that bottom plus the average
bottom-to-halving lead of past cycles. Before that, it falls back to the last
halving plus the average interval between halvings.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise
from statistics import mean

from config import (
    BTC_CYCLE_BOTTOMS,
    DAYS_AFTER_HALVING,
    DAYS_BEFORE_HALVING,
    HALVING_DATES,
    PEAK_DAYS_AFTER_HALVING,
)


@dataclass(frozen=True)
class HalvingCycle:
    """A halving cycle: its number, halving date and chart window around it."""

    number: int
    halving: date
    forecast: bool = False  # True when the halving date is a forecast

    @property
    def start(self) -> date:
        return self.halving - timedelta(days=DAYS_BEFORE_HALVING)

    @property
    def end(self) -> date:
        return self.halving + timedelta(days=DAYS_AFTER_HALVING)

    @property
    def label(self) -> str:
        """Legend label, e.g. ``Cycle 4 (2024)`` or ``Cycle 5 (2027, est.)``."""
        suffix = ", est." if self.forecast else ""
        return f"Cycle {self.number} ({self.halving.year}{suffix})"

    def contains(self, day: date) -> bool:
        """Whether ``day`` falls inside this cycle's chart window."""
        return self.start <= day <= self.end

    def days_from_halving(self, day: date) -> int:
        return (day - self.halving).days


def _bottom_between(start: date, end: date, bottoms: Sequence[date]) -> date | None:
    """The latest configured bottom strictly between ``start`` and ``end``."""
    return max((b for b in bottoms if start < b < end), default=None)


def bottom_to_halving_days(
    halvings: Sequence[date] = HALVING_DATES,
    bottoms: Sequence[date] = BTC_CYCLE_BOTTOMS,
) -> int:
    """
    Average days from a cycle's bear-market bottom to the halving that follows it.

    Each halving is paired with the bottom configured since the previous
    halving. Cycle 1 has no previous halving and is therefore excluded, which
    is intended: the pre-2012 market is not representative.
    """
    leads = [
        (halving - bottom).days
        for previous, halving in pairwise(halvings)
        if (bottom := _bottom_between(previous, halving, bottoms)) is not None
    ]
    if not leads:
        raise ValueError("No cycle bottom configured between two known halvings")
    return round(mean(leads))


def _confirmed_bottom(
    halvings: Sequence[date] = HALVING_DATES,
    bottoms: Sequence[date] = BTC_CYCLE_BOTTOMS,
) -> date | None:
    """The current cycle's bottom (after the last known halving), if configured."""
    return _bottom_between(halvings[-1], date.max, bottoms)


def forecast_next_halving(
    halvings: Sequence[date] = HALVING_DATES,
    bottoms: Sequence[date] = BTC_CYCLE_BOTTOMS,
) -> date:
    """
    Forecast the date of the next halving (the first one not in the config).

    Confirmed bottom + average bottom-to-halving lead when the current cycle's
    bottom is known; otherwise last halving + average halving interval.
    """
    bottom = _confirmed_bottom(halvings, bottoms)
    if bottom is not None:
        return bottom + timedelta(days=bottom_to_halving_days(halvings, bottoms))
    interval = round(mean((b - a).days for a, b in pairwise(halvings)))
    return halvings[-1] + timedelta(days=interval)


def current_cycle_bottom(
    halvings: Sequence[date] = HALVING_DATES,
    bottoms: Sequence[date] = BTC_CYCLE_BOTTOMS,
) -> date:
    """
    Bottom date of the current cycle: the confirmed BTC bottom when configured,
    otherwise the forecast halving minus the average bottom-to-halving lead.
    """
    bottom = _confirmed_bottom(halvings, bottoms)
    if bottom is not None:
        return bottom
    lead = bottom_to_halving_days(halvings, bottoms)
    return forecast_next_halving(halvings, bottoms) - timedelta(days=lead)


def projected_peak_date() -> date:
    """Expected date of the next cycle peak (forecast halving + peak offset)."""
    return forecast_next_halving() + timedelta(days=PEAK_DAYS_AFTER_HALVING)


def halving_cycles() -> list[HalvingCycle]:
    """All cycles in order: the known halvings, then the forecast next one."""
    cycles = [HalvingCycle(n, h) for n, h in enumerate(HALVING_DATES, start=1)]
    cycles.append(HalvingCycle(len(cycles) + 1, forecast_next_halving(), forecast=True))
    return cycles


def cycle_halving_dates() -> list[date]:
    """Halving date of every cycle, known then forecast (cycle n at index n-1)."""
    return [cycle.halving for cycle in halving_cycles()]

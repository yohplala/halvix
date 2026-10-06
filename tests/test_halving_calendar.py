"""
Tests for the halving calendar (known halvings + next-halving forecast).
"""

from datetime import date, timedelta

import pytest

from analysis.halving_calendar import (
    HalvingCycle,
    bottom_to_halving_days,
    current_cycle_bottom,
    cycle_halving_dates,
    forecast_next_halving,
    halving_cycles,
    projected_peak_date,
)
from config import (
    BTC_CYCLE_BOTTOMS,
    DAYS_AFTER_HALVING,
    DAYS_BEFORE_HALVING,
    HALVING_DATES,
    PEAK_DAYS_AFTER_HALVING,
)

HALVINGS = [date(2012, 11, 28), date(2016, 7, 9), date(2020, 5, 11), date(2024, 4, 19)]
BOTTOMS = [date(2015, 1, 14), date(2018, 12, 15), date(2022, 11, 21)]
CONFIRMED_BOTTOM = date(2026, 6, 30)


class TestBottomToHalvingDays:
    def test_average_lead_of_cycles_2_to_4(self):
        # 542, 513 and 515 days → 523.3
        assert bottom_to_halving_days(HALVINGS, BOTTOMS) == 523

    def test_cycle_1_is_excluded(self):
        """A bottom before the first halving has no cycle to pair with."""
        with_cycle1 = [date(2011, 11, 18), *BOTTOMS]
        assert bottom_to_halving_days(HALVINGS, with_cycle1) == 523

    def test_current_cycle_bottom_is_not_a_lead(self):
        """The bottom after the last halving has no halving yet."""
        assert bottom_to_halving_days(HALVINGS, [*BOTTOMS, CONFIRMED_BOTTOM]) == 523

    def test_requires_a_bottom(self):
        with pytest.raises(ValueError):
            bottom_to_halving_days(HALVINGS, [])


class TestForecastNextHalving:
    def test_confirmed_bottom_plus_average_lead(self):
        bottoms = [*BOTTOMS, CONFIRMED_BOTTOM]
        assert forecast_next_halving(HALVINGS, bottoms) == CONFIRMED_BOTTOM + timedelta(days=523)

    def test_falls_back_to_average_halving_interval(self):
        """Before the current bottom is confirmed: last halving + mean interval."""
        # Intervals: 1319, 1402, 1439 days → 1387
        assert forecast_next_halving(HALVINGS, BOTTOMS) == date(2024, 4, 19) + timedelta(days=1387)

    def test_config_forecast(self):
        """With the configured 2026-06-30 bottom, the 5th halving is 2027-12-05."""
        assert CONFIRMED_BOTTOM in BTC_CYCLE_BOTTOMS
        assert forecast_next_halving() == date(2027, 12, 5)


class TestCurrentCycleBottom:
    def test_confirmed_bottom(self):
        assert current_cycle_bottom(HALVINGS, [*BOTTOMS, CONFIRMED_BOTTOM]) == CONFIRMED_BOTTOM

    def test_forecast_bottom_before_confirmation(self):
        expected = forecast_next_halving(HALVINGS, BOTTOMS) - timedelta(days=523)
        assert current_cycle_bottom(HALVINGS, BOTTOMS) == expected


class TestHalvingCycles:
    def test_known_then_forecast(self):
        cycles = halving_cycles()
        assert [c.halving for c in cycles[:-1]] == HALVING_DATES
        assert [c.number for c in cycles] == list(range(1, len(HALVING_DATES) + 2))
        assert not any(c.forecast for c in cycles[:-1])
        assert cycles[-1].forecast
        assert cycles[-1].halving == forecast_next_halving()
        assert cycle_halving_dates() == [c.halving for c in cycles]

    def test_projected_peak_date(self):
        assert projected_peak_date() == forecast_next_halving() + timedelta(
            days=PEAK_DAYS_AFTER_HALVING
        )

    def test_cycle_window(self):
        cycle = HalvingCycle(4, date(2024, 4, 19))
        assert cycle.start == date(2024, 4, 19) - timedelta(days=DAYS_BEFORE_HALVING)
        assert cycle.end == date(2024, 4, 19) + timedelta(days=DAYS_AFTER_HALVING)
        assert cycle.contains(cycle.start) and cycle.contains(cycle.end)
        assert not cycle.contains(cycle.end + timedelta(days=1))
        assert cycle.days_from_halving(CONFIRMED_BOTTOM) == 802

    def test_labels(self):
        assert HalvingCycle(4, date(2024, 4, 19)).label == "Cycle 4 (2024)"
        assert HalvingCycle(5, date(2027, 12, 5), forecast=True).label == "Cycle 5 (2027, est.)"

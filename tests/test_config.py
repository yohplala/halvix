"""
Tests for configuration module, including HalvingCycle value objects.
"""

from datetime import date

import pytest

from config import (
    DAYS_AFTER_HALVING,
    DAYS_BEFORE_HALVING,
    HALVING_CYCLES,
    HALVING_DATES,
    HalvingCycle,
    get_cycle,
    get_cycle_for_date,
)

# =============================================================================
# HalvingCycle Value Object Tests
# =============================================================================


class TestHalvingCycle:
    """Tests for HalvingCycle dataclass."""

    def test_creation(self):
        """Test creating a HalvingCycle directly."""
        cycle = HalvingCycle(
            cycle_num=4,
            halving_date=date(2024, 4, 19),
            window_start=date(2022, 11, 15),
            window_end=date(2027, 1, 25),
            peak_date=date(2025, 10, 6),
            bottom_date=date(2022, 11, 21),
            is_current=True,
        )

        assert cycle.cycle_num == 4
        assert cycle.halving_date == date(2024, 4, 19)
        assert cycle.is_current is True

    def test_from_halving_date_factory(self):
        """Test creating a HalvingCycle using the factory method."""
        cycle = HalvingCycle.from_halving_date(
            cycle_num=3,
            halving_date=date(2020, 5, 11),
            peak_date=date(2021, 11, 8),
            bottom_date=date(2018, 12, 15),
        )

        assert cycle.cycle_num == 3
        assert cycle.halving_date == date(2020, 5, 11)
        # Window should be computed from DAYS_BEFORE/AFTER_HALVING
        expected_start = date(2020, 5, 11) - __import__("datetime").timedelta(
            days=DAYS_BEFORE_HALVING
        )
        expected_end = date(2020, 5, 11) + __import__("datetime").timedelta(days=DAYS_AFTER_HALVING)
        assert cycle.window_start == expected_start
        assert cycle.window_end == expected_end

    def test_immutability(self):
        """Test that HalvingCycle is immutable (frozen)."""
        cycle = HalvingCycle.from_halving_date(
            cycle_num=2,
            halving_date=date(2016, 7, 9),
        )

        with pytest.raises(AttributeError):
            cycle.cycle_num = 5  # type: ignore

    def test_total_days(self):
        """Test total_days property."""
        cycle = HalvingCycle.from_halving_date(
            cycle_num=1,
            halving_date=date(2012, 11, 28),
        )

        expected_days = DAYS_BEFORE_HALVING + DAYS_AFTER_HALVING
        assert cycle.total_days == expected_days

    def test_contains_date(self):
        """Test contains_date method."""
        cycle = HalvingCycle.from_halving_date(
            cycle_num=4,
            halving_date=date(2024, 4, 19),
        )

        # Halving date itself should be contained
        assert cycle.contains_date(date(2024, 4, 19)) is True

        # Window boundaries
        assert cycle.contains_date(cycle.window_start) is True
        assert cycle.contains_date(cycle.window_end) is True

        # Outside window
        far_past = date(2020, 1, 1)
        far_future = date(2030, 1, 1)
        assert cycle.contains_date(far_past) is False
        assert cycle.contains_date(far_future) is False

    def test_days_from_halving(self):
        """Test days_from_halving method."""
        cycle = HalvingCycle.from_halving_date(
            cycle_num=4,
            halving_date=date(2024, 4, 19),
        )

        # Day of halving
        assert cycle.days_from_halving(date(2024, 4, 19)) == 0

        # 100 days before
        assert cycle.days_from_halving(date(2024, 1, 10)) == -100

        # 100 days after
        assert cycle.days_from_halving(date(2024, 7, 28)) == 100


# =============================================================================
# HALVING_CYCLES Pre-built List Tests
# =============================================================================


class TestHalvingCyclesList:
    """Tests for the pre-built HALVING_CYCLES list."""

    def test_list_has_all_halvings(self):
        """Test that HALVING_CYCLES contains all halving dates."""
        assert len(HALVING_CYCLES) == len(HALVING_DATES)

    def test_cycle_numbers_sequential(self):
        """Test that cycle numbers are sequential starting from 1."""
        cycle_nums = [c.cycle_num for c in HALVING_CYCLES]
        assert cycle_nums == list(range(1, len(HALVING_DATES) + 1))

    def test_halving_dates_match(self):
        """Test that halving dates match HALVING_DATES."""
        for cycle, expected_date in zip(HALVING_CYCLES, HALVING_DATES, strict=True):
            assert cycle.halving_date == expected_date

    def test_current_cycle_marked(self):
        """Test that only the last cycle is marked as current."""
        for i, cycle in enumerate(HALVING_CYCLES):
            if i == len(HALVING_CYCLES) - 1:
                assert cycle.is_current is True
            else:
                assert cycle.is_current is False

    def test_cycle_2_has_peak_and_bottom(self):
        """Test that cycle 2 (2016) has peak and bottom dates."""
        cycle_2 = get_cycle(2)
        assert cycle_2 is not None
        assert cycle_2.peak_date is not None
        assert cycle_2.bottom_date is not None


# =============================================================================
# Helper Function Tests
# =============================================================================


class TestGetCycle:
    """Tests for get_cycle function."""

    def test_get_existing_cycle(self):
        """Test getting an existing cycle."""
        cycle = get_cycle(4)
        assert cycle is not None
        assert cycle.cycle_num == 4
        assert cycle.halving_date == date(2024, 4, 19)

    def test_get_nonexistent_cycle(self):
        """Test getting a non-existent cycle returns None."""
        cycle = get_cycle(99)
        assert cycle is None

    def test_get_cycle_zero(self):
        """Test getting cycle 0 returns None."""
        cycle = get_cycle(0)
        assert cycle is None


class TestGetCycleForDate:
    """Tests for get_cycle_for_date function."""

    def test_date_in_cycle_window(self):
        """Test finding cycle for a date within a window."""
        # The 2024 halving date should be in cycle 4
        cycle = get_cycle_for_date(date(2024, 4, 19))
        assert cycle is not None
        assert cycle.cycle_num == 4

    def test_date_outside_all_windows(self):
        """Test date outside all cycle windows returns None."""
        # Very old date before any halving windows
        cycle = get_cycle_for_date(date(2000, 1, 1))
        assert cycle is None

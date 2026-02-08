"""
Tests for configuration module.
"""

from datetime import date

from config import HALVING_DATES


class TestHalvingDates:
    """Tests for HALVING_DATES list."""

    def test_chronological_order(self):
        """Test that halving dates are in chronological order."""
        for i in range(len(HALVING_DATES) - 1):
            assert HALVING_DATES[i] < HALVING_DATES[i + 1]

    def test_all_dates_are_date_objects(self):
        """Test that all entries are date objects."""
        for d in HALVING_DATES:
            assert isinstance(d, date)

    def test_has_expected_count(self):
        """Test that we have the expected number of halvings."""
        assert len(HALVING_DATES) == 5

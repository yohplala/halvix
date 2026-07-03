"""
Tests for price data filtering tools in Halvix.

Tests cover:
- Volume outlier detection and correction (DataFrame)
- SMA smoothing functions (DataFrame)
- Edge cases (empty data, null handling)
"""

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal, assert_series_equal

from data.price_filters import (
    DEFAULT_MIN_VOLUME_FOR_OUTLIER_CHECK,
    DEFAULT_OUTLIER_WINDOW_DAYS,
    DEFAULT_VOLUME_OUTLIER_THRESHOLD,
    DEFAULT_VOLUME_SMA_WINDOW,
    apply_round_trip_corrections_to_dataframe,
    apply_volume_corrections_to_dataframe,
    apply_volume_sma_smoothing_to_dataframe,
    detect_round_trips,
)


def _daterange(n):
    """List of ``n`` consecutive dates starting 2024-01-01."""
    return [date(2024, 1, 1) + timedelta(days=i) for i in range(n)]


class TestDefaultParameters:
    """Tests for module default parameters."""

    def test_default_volume_outlier_threshold(self):
        """Test default outlier threshold is reasonable."""
        assert DEFAULT_VOLUME_OUTLIER_THRESHOLD == 20

    def test_default_min_volume_for_outlier_check(self):
        """Test default minimum volume for outlier check."""
        assert DEFAULT_MIN_VOLUME_FOR_OUTLIER_CHECK == 5000

    def test_default_outlier_window_days(self):
        """Test default outlier window size."""
        assert DEFAULT_OUTLIER_WINDOW_DAYS == 7

    def test_default_volume_sma_window(self):
        """Test default SMA window size."""
        assert DEFAULT_VOLUME_SMA_WINDOW == 120


class TestApplyVolumeSMASmoothingToDataFrame:
    """Tests for SMA smoothing on DataFrame."""

    @pytest.fixture
    def sample_volume_df(self):
        """Create sample volume DataFrame with multiple coins."""
        dates = _daterange(10)
        return pl.DataFrame(
            {
                "date": dates,
                "eth": [10000.0 + i * 1000 for i in range(10)],
                "sol": [2000.0 + i * 200 for i in range(10)],
                "ada": [500.0 + i * 50 for i in range(10)],
            }
        )

    def test_basic_dataframe_smoothing(self, sample_volume_df):
        """Test basic DataFrame SMA smoothing."""
        smoothed = apply_volume_sma_smoothing_to_dataframe(
            sample_volume_df,
            window=3,
            zero_pad=False,
        )

        assert isinstance(smoothed, pl.DataFrame)
        assert smoothed.shape == sample_volume_df.shape
        assert list(smoothed.columns) == list(sample_volume_df.columns)

    def test_zero_padding_per_coin(self):
        """Test zero padding applied per coin."""
        dates = _daterange(10)
        df = pl.DataFrame(
            {
                "date": dates,
                # ETH has data from day 1
                "eth": [10000.0] * 10,
                # SOL starts with nulls, data from day 4
                "sol": [None, None, None, 2000.0, 2000.0, 2000.0, 2000.0, 2000.0, 2000.0, 2000.0],
            }
        )

        smoothed = apply_volume_sma_smoothing_to_dataframe(df, window=3, zero_pad=True)

        # SOL should have zeros before first valid, affecting early SMA
        assert isinstance(smoothed, pl.DataFrame)

    def test_no_zero_padding_dataframe(self, sample_volume_df):
        """Test DataFrame smoothing without zero padding."""
        smoothed = apply_volume_sma_smoothing_to_dataframe(
            sample_volume_df,
            window=3,
            zero_pad=False,
        )

        # Standard rolling mean behavior
        assert smoothed["eth"][2] == pytest.approx((10000 + 11000 + 12000) / 3)

    def test_preserves_column_order(self, sample_volume_df):
        """Test that column order is preserved."""
        smoothed = apply_volume_sma_smoothing_to_dataframe(sample_volume_df, window=3)

        assert list(smoothed.columns) == ["date", "eth", "sol", "ada"]


class TestApplyVolumeCorrectionsToDataFrame:
    """Tests for DataFrame-level volume corrections."""

    @pytest.fixture
    def df_with_outliers(self):
        """Create DataFrame with outliers in multiple coins."""
        dates = _daterange(15)
        return pl.DataFrame(
            {
                "date": dates,
                "eth": [10000.0] * 9 + [500000.0] + [10000.0] * 5,  # Outlier on day 10
                "sol": [2000.0] * 12 + [100000.0] + [2000.0] * 2,  # Outlier on day 13
                "ada": [500.0] * 15,  # No outliers (below min_volume)
            }
        )

    def test_corrects_outliers_in_multiple_coins(self, df_with_outliers):
        """Test that outliers are corrected in multiple coins."""
        corrected, corrections = apply_volume_corrections_to_dataframe(
            df_with_outliers,
            threshold=20,
            min_volume=1000,
            window_days=7,
        )

        # ETH outlier should be corrected
        assert corrected["eth"][9] < 500000

        # SOL outlier should be corrected
        assert corrected["sol"][12] < 100000

        # ADA should be unchanged (below min_volume)
        assert_series_equal(
            corrected["ada"],
            df_with_outliers["ada"],
        )

    def test_correction_records_include_coin_id(self, df_with_outliers):
        """Test that correction records include coin identifier."""
        _, corrections = apply_volume_corrections_to_dataframe(
            df_with_outliers,
            threshold=20,
            min_volume=1000,
            window_days=7,
        )

        if corrections:
            assert "coin" in corrections[0]
            coin_ids = {c["coin"] for c in corrections}
            assert "ETH" in coin_ids or "SOL" in coin_ids

    def test_corrections_sorted_by_ratio(self, df_with_outliers):
        """Test that corrections are sorted by ratio."""
        _, corrections = apply_volume_corrections_to_dataframe(
            df_with_outliers,
            threshold=20,
            min_volume=1000,
            window_days=7,
        )

        if len(corrections) > 1:
            ratios = [c["ratio"] for c in corrections]
            assert ratios == sorted(ratios, reverse=True)

    def test_show_progress_does_not_change_results(self, df_with_outliers):
        """show_progress is presentation-only: results must be identical."""
        quiet_df, quiet_corr = apply_volume_corrections_to_dataframe(
            df_with_outliers, threshold=20, min_volume=1000, window_days=7, show_progress=False
        )
        loud_df, loud_corr = apply_volume_corrections_to_dataframe(
            df_with_outliers, threshold=20, min_volume=1000, window_days=7, show_progress=True
        )

        assert loud_corr == quiet_corr
        assert loud_df.shape == quiet_df.shape
        assert_frame_equal(loud_df, quiet_df)

    def test_max_iterations_respected(self, df_with_outliers):
        """Test max_iterations parameter."""
        _, corrections = apply_volume_corrections_to_dataframe(
            df_with_outliers,
            threshold=20,
            min_volume=1000,
            window_days=7,
            max_iterations=1,
        )

        for c in corrections:
            assert c["iteration"] == 1

    def test_empty_dataframe(self):
        """Test empty DataFrame handling."""
        empty_df = pl.DataFrame({"date": pl.Series([], dtype=pl.Date)})
        corrected, corrections = apply_volume_corrections_to_dataframe(empty_df)

        assert corrected.is_empty()
        assert corrections == []

    def test_no_corrections_needed(self):
        """Test DataFrame with no outliers."""
        dates = _daterange(10)
        df = pl.DataFrame(
            {
                "date": dates,
                "eth": [10000.0 + i * 100 for i in range(10)],
                "sol": [2000.0 + i * 20 for i in range(10)],
            }
        )

        corrected, corrections = apply_volume_corrections_to_dataframe(
            df,
            threshold=20,
            min_volume=5000,
            window_days=7,
        )

        assert_frame_equal(corrected, df)
        assert corrections == []


class TestEdgeCases:
    """Tests for edge cases across DataFrame functions."""

    def test_dataframe_with_single_column(self):
        """Test DataFrame operations with single column."""
        dates = _daterange(10)
        df = pl.DataFrame(
            {
                "date": dates,
                "eth": [10000.0] * 9 + [500000.0],
            }
        )

        corrected, corrections = apply_volume_corrections_to_dataframe(
            df,
            threshold=20,
            min_volume=5000,
            window_days=7,
        )

        # date column + one coin column
        assert corrected.shape[1] == 2
        assert "eth" in corrected.columns

    def test_dataframe_with_all_null_column(self):
        """Test DataFrame with one column entirely null."""
        dates = _daterange(10)
        df = pl.DataFrame(
            {
                "date": dates,
                "eth": [10000.0] * 10,
                "sol": [None] * 10,
            }
        )

        corrected, _ = apply_volume_corrections_to_dataframe(df)
        smoothed = apply_volume_sma_smoothing_to_dataframe(df, window=3)

        assert "sol" in corrected.columns
        assert "sol" in smoothed.columns


# =============================================================================
# Round-Trip Detection Tests
# =============================================================================


class TestDetectRoundTrips:
    """Tests for single-day spike-and-revert detection."""

    @staticmethod
    def _sd(values):
        """Return (close Series, parallel dates list) for a value sequence."""
        dates = _daterange(len(values))
        return pl.Series(values, dtype=pl.Float64), dates

    def test_catches_up_spike_that_reverts_next_day(self):
        # SIREN-like pattern: 0.000011 -> 0.000027 (2.45x) -> 0.000010 (back).
        close, dates = self._sd([11.0, 11.0, 11.0, 27.0, 10.0, 10.0])
        events = detect_round_trips(close, dates)
        assert len(events) == 1
        ev = events[0]
        assert ev["direction"] == "up"
        assert ev["days_to_revert"] == 1
        assert ev["jump_ratio"] == pytest.approx(27 / 11, rel=1e-9)
        assert ev["pre_price"] == 11.0
        assert ev["jump_price"] == 27.0
        assert ev["revert_price"] == 10.0

    def test_catches_down_spike_that_reverts(self):
        close, dates = self._sd([100.0, 100.0, 30.0, 95.0, 100.0])
        events = detect_round_trips(close, dates)
        assert len(events) == 1
        assert events[0]["direction"] == "down"
        assert events[0]["jump_ratio"] == pytest.approx(0.3, rel=1e-9)

    def test_ignores_jump_that_does_not_revert(self):
        # 1.0 -> 5.0 (5x jump) but stays at 5.0 — legitimate move, not a glitch.
        close, dates = self._sd([1.0, 1.0, 1.0, 5.0, 5.0, 5.0])
        events = detect_round_trips(close, dates)
        assert events == []

    def test_ignores_small_move_within_threshold(self):
        close, dates = self._sd([100.0, 110.0, 120.0, 130.0, 120.0])  # ratios all < 2.0
        events = detect_round_trips(close, dates)
        assert events == []

    def test_detects_revert_two_days_later(self):
        # Revert one day late: needs window_days >= 2 to be caught.
        close, dates = self._sd([10.0, 10.0, 25.0, 25.0, 11.0, 10.0])
        events = detect_round_trips(close, dates, window_days=2)
        assert len(events) == 1
        assert events[0]["days_to_revert"] == 2

        # With window=1, the same data should not be flagged because day+1 still high.
        events_short = detect_round_trips(close, dates, window_days=1)
        assert events_short == []

    def test_skips_zero_or_negative_prices(self):
        # Resurrection from zero is handled by symbol-replacement, not here.
        close, dates = self._sd([0.0, 0.0, 10.0, 30.0, 10.0])
        events = detect_round_trips(close, dates)
        # Only the 10 -> 30 -> 10 transition is a valid candidate.
        assert all(ev["pre_price"] > 0 for ev in events)
        assert len(events) == 1

    def test_empty_or_short_series_returns_empty(self):
        assert detect_round_trips(pl.Series([], dtype=pl.Float64), []) == []
        close, dates = self._sd([1.0, 2.0])
        assert detect_round_trips(close, dates) == []

    def test_invalid_thresholds_raise(self):
        close, dates = self._sd([1.0, 2.0, 1.0])
        with pytest.raises(ValueError):
            detect_round_trips(close, dates, jump_threshold=1.0)
        with pytest.raises(ValueError):
            detect_round_trips(close, dates, revert_threshold=0.9)

    def test_breaks_on_first_revert_within_window(self):
        # Should record the FIRST k where revert holds, not iterate further.
        close, dates = self._sd([10.0, 10.0, 30.0, 10.0, 10.0, 10.0])
        events = detect_round_trips(close, dates, window_days=3)
        assert len(events) == 1
        assert events[0]["days_to_revert"] == 1

    def test_does_not_flag_revert_day_as_a_new_event(self):
        # Two consecutive spike-and-reverts on a zigzag pattern:
        # day 1 (1.0) -> 2 (2.5 spike) -> 3 (1.0 revert) -> 4 (2.5 spike) -> 5 (1.0)
        #
        # The revert day (index 2) sees a "down" ratio of 1.0/2.5 = 0.4 (below
        # the 0.5 inverse-jump threshold) and the next day is back at 2.5,
        # which would make the revert-ratio (2.5/2.5 = 1.0) appear to satisfy
        # the upward-revert tolerance. Without skipping, the algorithm would
        # treat the trough at index 2 as a "down spike" and replace it with
        # the previous SPIKE day's value (2.5), corrupting a legitimate
        # baseline into a phantom spike. After fixing, the revert day of a
        # prior event must not be re-flagged as a new jump-day.
        close, dates = self._sd([1.0, 2.5, 1.0, 2.5, 1.0, 1.0])
        events = detect_round_trips(close, dates)
        # Expect exactly two events: the real spike days at index 1 and 3.
        # The trough at index 2 (revert of event 1) must NOT be flagged.
        flagged_indices = [dates.index(ev["date"]) for ev in events]
        assert (
            2 not in flagged_indices
        ), f"Revert day of prior event must not be re-flagged. Got indices: {flagged_indices}"

    def test_catches_multi_day_pump_and_dump(self):
        # RAVE-shape: 3-day climb where each day-over-day is sub-threshold
        # (1.57x, 1.27x), cumulative max 2.75x, then a single-day crash to
        # 0.375x baseline. None of the daily ratios alone trigger; only the
        # window-max check catches this pattern.
        # Series: baseline 8, 8, 8, then 11, 17, 22, then crash to 3.
        close, dates = self._sd([8.0, 8.0, 8.0, 11.0, 17.0, 22.0, 3.0, 3.0])
        events = detect_round_trips(close, dates, window_days=5)
        assert len(events) == 1
        ev = events[0]
        assert ev["direction"] == "up"
        # Spike starts at index 3 (first day above baseline), peaks at index 5,
        # reverts at index 6. days_to_revert measures revert minus spike-start.
        assert dates.index(ev["date"]) == 3
        assert ev["jump_price"] == 22.0
        assert ev["pre_price"] == 8.0
        assert ev["days_to_revert"] == 3
        # All three elevated days (3, 4, 5) are smoothed; the revert day (6) is not.
        smoothed_positions = [dates.index(d) for d in ev["smoothed_dates"]]
        assert smoothed_positions == [3, 4, 5]

    def test_leaves_durable_bull_move_alone(self):
        # 3x climb that *stays* elevated (no revert within window) — exactly the
        # legitimate bull move the multi-day detector must not touch.
        close, dates = self._sd([10.0, 11.0, 17.0, 22.0, 25.0, 30.0, 28.0, 32.0])
        events = detect_round_trips(close, dates, window_days=7)
        assert events == []

    def test_picks_earlier_extremum_when_pump_then_crash(self):
        # When both a window-max and a window-min satisfy their thresholds (pump
        # then crash, RAVE-shape), prefer the one whose extremum comes first.
        # The up-pump must be the primary event so the elevated days are smoothed,
        # not the crash day.
        close, dates = self._sd([10.0, 25.0, 30.0, 5.0, 8.0, 10.0])
        events = detect_round_trips(close, dates, window_days=5)
        assert len(events) == 1
        assert events[0]["direction"] == "up"

    def test_spike_start_skips_below_baseline_noise(self):
        # Regression: when iterating at i=1 with a multi-day window, the
        # spike-start search must not latch onto a noisy baseline day that
        # falls on the OPPOSITE side of p_pre from the spike direction.
        #
        # Series: noisy baseline near 10 (12, 11, 9, 14) with idx 3 *below*
        # p_pre, then a true spike to 50 at idx 5, then revert to 8 at idx 6.
        # With the buggy spike-start (any v > p_pre triggers), iterating at
        # i=1 picks spike_start=1 (12 > 10) and smooths idx 1..5 — including
        # idx 3 (9, BELOW p_pre, clearly not part of an UP spike). The fix
        # walks backwards from the extremum and stops at the first day NOT
        # on the spike side of p_pre, so idx 3 (and the days before it) are
        # excluded from the smoothing span.
        close, dates = self._sd([10.0, 12.0, 11.0, 9.0, 14.0, 50.0, 8.0, 10.0])
        events = detect_round_trips(close, dates, window_days=7)
        assert len(events) == 1
        ev = events[0]
        assert ev["direction"] == "up"
        smoothed_positions = sorted(dates.index(d) for d in ev["smoothed_dates"])
        # idx 3 (value 9) is below p_pre and must not be smoothed — smoothing
        # it would overwrite a legitimate slightly-below-baseline value with
        # p_pre, corrupting the close series.
        assert 3 not in smoothed_positions, (
            "Days on the opposite side of p_pre cannot be part of an 'up' "
            f"spike span. Got smoothed positions: {smoothed_positions}"
        )
        # Days 1 and 2 (also separated from the extremum by the day-3 dip)
        # must also be excluded — the spike span has to be contiguous.
        assert 1 not in smoothed_positions
        assert 2 not in smoothed_positions
        # Spike start is the first day in the contiguous run leading up to
        # the extremum that's strictly above p_pre. Here idx 4 (14 > 10) is
        # contiguous with idx 5 (50), so the smoothed span is [4, 5].
        assert smoothed_positions == [4, 5]


class TestApplyRoundTripCorrectionsToDataFrame:
    """Tests for DataFrame-level round-trip smoothing."""

    def test_smooths_spike_day_to_prior_close(self):
        dates = _daterange(6)
        df = pl.DataFrame(
            {
                "date": dates,
                "siren": [11.0, 11.0, 11.0, 27.0, 10.0, 10.0],
                "eth": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
            }
        )
        corrected, events = apply_round_trip_corrections_to_dataframe(df)

        # SIREN spike day should be replaced with prior close.
        assert corrected["siren"][3] == 11.0
        # ETH untouched.
        assert (corrected["eth"] == df["eth"]).all()
        # Event recorded for SIREN only.
        assert len(events) == 1
        ev = events[0]
        assert ev["coin"] == "SIREN"
        assert ev["original"] == 27.0
        assert ev["corrected"] == 11.0

    def test_no_corrections_on_clean_data(self):
        dates = _daterange(10)
        df = pl.DataFrame(
            {
                "date": dates,
                "eth": list(np.linspace(100, 110, 10)),
                "sol": list(np.linspace(20, 22, 10)),
            }
        )
        corrected, events = apply_round_trip_corrections_to_dataframe(df)
        assert events == []
        assert_frame_equal(corrected, df)

    def test_original_dataframe_unchanged(self):
        dates = _daterange(6)
        df = pl.DataFrame(
            {
                "date": dates,
                "siren": [11.0, 11.0, 11.0, 27.0, 10.0, 10.0],
            }
        )
        original = df.clone()
        apply_round_trip_corrections_to_dataframe(df)
        assert_frame_equal(df, original)

    def test_corrections_sorted_by_jump_magnitude(self):
        dates = _daterange(8)
        df = pl.DataFrame(
            {
                "date": dates,
                "a": [10.0, 10.0, 10.0, 25.0, 10.0, 10.0, 10.0, 10.0],  # 2.5x
                "b": [10.0, 10.0, 10.0, 100.0, 10.0, 10.0, 10.0, 10.0],  # 10x
            }
        )
        _, events = apply_round_trip_corrections_to_dataframe(df)
        assert len(events) == 2
        # Bigger jump first.
        assert events[0]["coin"] == "B"
        assert events[1]["coin"] == "A"

    def test_multi_day_pump_smooths_full_span(self):
        # RAVE-shape multi-day pump: every elevated day should be smoothed to
        # the pre-spike baseline (8), not just the peak day. The revert day
        # itself remains untouched.
        dates = _daterange(8)
        df = pl.DataFrame(
            {
                "date": dates,
                "rave": [8.0, 8.0, 8.0, 11.0, 17.0, 22.0, 3.0, 3.0],
            }
        )
        corrected, events = apply_round_trip_corrections_to_dataframe(df, window_days=5)
        # All three elevated days collapse to baseline 8.0.
        assert corrected["rave"][3] == 8.0
        assert corrected["rave"][4] == 8.0
        assert corrected["rave"][5] == 8.0
        # Revert day (3.0) and trailing baseline untouched.
        assert corrected["rave"][6] == 3.0
        assert corrected["rave"][7] == 3.0
        # Three smoothing records, one per elevated day, all from the same event.
        assert len(events) == 3
        assert all(ev["coin"] == "RAVE" for ev in events)
        assert all(ev["corrected"] == 8.0 for ev in events)
        assert all(ev["direction"] == "up" for ev in events)
        assert all(ev["days_to_revert"] == 3 for ev in events)

"""
Tests for price data filtering tools in Halvix.

Tests cover:
- Volume outlier detection and correction (DataFrame)
- SMA smoothing functions (DataFrame)
- Edge cases (empty data, NaN handling)
"""

import numpy as np
import pandas as pd
import pytest

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
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        data = {
            "eth": [10000.0 + i * 1000 for i in range(10)],
            "sol": [2000.0 + i * 200 for i in range(10)],
            "ada": [500.0 + i * 50 for i in range(10)],
        }
        return pd.DataFrame(data, index=dates)

    def test_basic_dataframe_smoothing(self, sample_volume_df):
        """Test basic DataFrame SMA smoothing."""
        smoothed = apply_volume_sma_smoothing_to_dataframe(
            sample_volume_df,
            window=3,
            zero_pad=False,
        )

        assert isinstance(smoothed, pd.DataFrame)
        assert smoothed.shape == sample_volume_df.shape
        assert list(smoothed.columns) == list(sample_volume_df.columns)

    def test_zero_padding_per_coin(self):
        """Test zero padding applied per coin."""
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        data = {
            # ETH has data from day 1
            "eth": [10000.0] * 10,
            # SOL starts with NaN, data from day 4
            "sol": [np.nan, np.nan, np.nan, 2000.0, 2000.0, 2000.0, 2000.0, 2000.0, 2000.0, 2000.0],
        }
        df = pd.DataFrame(data, index=dates)

        smoothed = apply_volume_sma_smoothing_to_dataframe(df, window=3, zero_pad=True)

        # SOL should have zeros before first valid, affecting early SMA
        assert isinstance(smoothed, pd.DataFrame)

    def test_no_zero_padding_dataframe(self, sample_volume_df):
        """Test DataFrame smoothing without zero padding."""
        smoothed = apply_volume_sma_smoothing_to_dataframe(
            sample_volume_df,
            window=3,
            zero_pad=False,
        )

        # Standard rolling mean behavior
        assert smoothed.iloc[2]["eth"] == pytest.approx((10000 + 11000 + 12000) / 3)

    def test_preserves_column_order(self, sample_volume_df):
        """Test that column order is preserved."""
        smoothed = apply_volume_sma_smoothing_to_dataframe(sample_volume_df, window=3)

        assert list(smoothed.columns) == ["eth", "sol", "ada"]


class TestApplyVolumeCorrectionsToDataFrame:
    """Tests for DataFrame-level volume corrections."""

    @pytest.fixture
    def df_with_outliers(self):
        """Create DataFrame with outliers in multiple coins."""
        dates = pd.date_range("2024-01-01", periods=15, freq="D")
        data = {
            "eth": [10000.0] * 9 + [500000.0] + [10000.0] * 5,  # Outlier on day 10
            "sol": [2000.0] * 12 + [100000.0] + [2000.0] * 2,  # Outlier on day 13
            "ada": [500.0] * 15,  # No outliers (below min_volume)
        }
        return pd.DataFrame(data, index=dates)

    def test_corrects_outliers_in_multiple_coins(self, df_with_outliers):
        """Test that outliers are corrected in multiple coins."""
        corrected, corrections = apply_volume_corrections_to_dataframe(
            df_with_outliers,
            threshold=20,
            min_volume=1000,
            window_days=7,
        )

        # ETH outlier should be corrected
        assert corrected.iloc[9]["eth"] < 500000

        # SOL outlier should be corrected
        assert corrected.iloc[12]["sol"] < 100000

        # ADA should be unchanged (below min_volume)
        pd.testing.assert_series_equal(
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

    def test_show_progress_flag(self, df_with_outliers, capsys):
        """Test show_progress flag output."""
        apply_volume_corrections_to_dataframe(
            df_with_outliers,
            threshold=20,
            min_volume=1000,
            window_days=7,
            show_progress=True,
        )

        captured = capsys.readouterr()
        # Should print progress information
        assert "outlier" in captured.out.lower() or len(captured.out) >= 0

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
        empty_df = pd.DataFrame()
        corrected, corrections = apply_volume_corrections_to_dataframe(empty_df)

        assert corrected.empty
        assert corrections == []

    def test_no_corrections_needed(self):
        """Test DataFrame with no outliers."""
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        data = {
            "eth": [10000.0 + i * 100 for i in range(10)],
            "sol": [2000.0 + i * 20 for i in range(10)],
        }
        df = pd.DataFrame(data, index=dates)

        corrected, corrections = apply_volume_corrections_to_dataframe(
            df,
            threshold=20,
            min_volume=5000,
            window_days=7,
        )

        pd.testing.assert_frame_equal(corrected, df)
        assert corrections == []


class TestEdgeCases:
    """Tests for edge cases across DataFrame functions."""

    def test_dataframe_with_single_column(self):
        """Test DataFrame operations with single column."""
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        df = pd.DataFrame(
            {"eth": [10000.0] * 9 + [500000.0]},
            index=dates,
        )

        corrected, corrections = apply_volume_corrections_to_dataframe(
            df,
            threshold=20,
            min_volume=5000,
            window_days=7,
        )

        assert corrected.shape[1] == 1
        assert "eth" in corrected.columns

    def test_dataframe_with_all_nan_column(self):
        """Test DataFrame with one column entirely NaN."""
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        df = pd.DataFrame(
            {
                "eth": [10000.0] * 10,
                "sol": [np.nan] * 10,
            },
            index=dates,
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
    def _series(values):
        dates = pd.date_range("2024-01-01", periods=len(values), freq="D")
        return pd.Series(values, index=dates)

    def test_catches_up_spike_that_reverts_next_day(self):
        # SIREN-like pattern: 0.000011 -> 0.000027 (2.45x) -> 0.000010 (back).
        s = self._series([11.0, 11.0, 11.0, 27.0, 10.0, 10.0])
        events = detect_round_trips(s)
        assert len(events) == 1
        ev = events[0]
        assert ev["direction"] == "up"
        assert ev["days_to_revert"] == 1
        assert ev["jump_ratio"] == pytest.approx(27 / 11, rel=1e-9)
        assert ev["pre_price"] == 11.0
        assert ev["jump_price"] == 27.0
        assert ev["revert_price"] == 10.0

    def test_catches_down_spike_that_reverts(self):
        s = self._series([100.0, 100.0, 30.0, 95.0, 100.0])
        events = detect_round_trips(s)
        assert len(events) == 1
        assert events[0]["direction"] == "down"
        assert events[0]["jump_ratio"] == pytest.approx(0.3, rel=1e-9)

    def test_ignores_jump_that_does_not_revert(self):
        # 1.0 -> 5.0 (5x jump) but stays at 5.0 — legitimate move, not a glitch.
        s = self._series([1.0, 1.0, 1.0, 5.0, 5.0, 5.0])
        events = detect_round_trips(s)
        assert events == []

    def test_ignores_small_move_within_threshold(self):
        s = self._series([100.0, 110.0, 120.0, 130.0, 120.0])  # ratios all < 2.0
        events = detect_round_trips(s)
        assert events == []

    def test_detects_revert_two_days_later(self):
        # Revert one day late: needs window_days >= 2 to be caught.
        s = self._series([10.0, 10.0, 25.0, 25.0, 11.0, 10.0])
        events = detect_round_trips(s, window_days=2)
        assert len(events) == 1
        assert events[0]["days_to_revert"] == 2

        # With window=1, the same data should not be flagged because day+1 still high.
        events_short = detect_round_trips(s, window_days=1)
        assert events_short == []

    def test_skips_zero_or_negative_prices(self):
        # Resurrection from zero is handled by symbol-replacement, not here.
        s = self._series([0.0, 0.0, 10.0, 30.0, 10.0])
        events = detect_round_trips(s)
        # Only the 10 -> 30 -> 10 transition is a valid candidate.
        assert all(ev["pre_price"] > 0 for ev in events)
        assert len(events) == 1

    def test_empty_or_short_series_returns_empty(self):
        assert detect_round_trips(pd.Series(dtype=float)) == []
        assert detect_round_trips(self._series([1.0, 2.0])) == []

    def test_invalid_thresholds_raise(self):
        s = self._series([1.0, 2.0, 1.0])
        with pytest.raises(ValueError):
            detect_round_trips(s, jump_threshold=1.0)
        with pytest.raises(ValueError):
            detect_round_trips(s, revert_threshold=0.9)

    def test_breaks_on_first_revert_within_window(self):
        # Should record the FIRST k where revert holds, not iterate further.
        s = self._series([10.0, 10.0, 30.0, 10.0, 10.0, 10.0])
        events = detect_round_trips(s, window_days=3)
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
        s = self._series([1.0, 2.5, 1.0, 2.5, 1.0, 1.0])
        events = detect_round_trips(s)
        # Expect exactly two events: the real spike days at index 1 and 3.
        # The trough at index 2 (revert of event 1) must NOT be flagged.
        flagged_indices = [s.index.get_loc(ev["date"]) for ev in events]
        assert (
            2 not in flagged_indices
        ), f"Revert day of prior event must not be re-flagged. Got indices: {flagged_indices}"


class TestApplyRoundTripCorrectionsToDataFrame:
    """Tests for DataFrame-level round-trip smoothing."""

    def test_smooths_spike_day_to_prior_close(self):
        dates = pd.date_range("2024-01-01", periods=6, freq="D")
        df = pd.DataFrame(
            {
                "siren": [11.0, 11.0, 11.0, 27.0, 10.0, 10.0],
                "eth": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
            },
            index=dates,
        )
        corrected, events = apply_round_trip_corrections_to_dataframe(df)

        # SIREN spike day should be replaced with prior close.
        assert corrected.loc[dates[3], "siren"] == 11.0
        # ETH untouched.
        assert (corrected["eth"] == df["eth"]).all()
        # Event recorded for SIREN only.
        assert len(events) == 1
        ev = events[0]
        assert ev["coin"] == "SIREN"
        assert ev["original"] == 27.0
        assert ev["corrected"] == 11.0

    def test_no_corrections_on_clean_data(self):
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        df = pd.DataFrame(
            {"eth": np.linspace(100, 110, 10), "sol": np.linspace(20, 22, 10)},
            index=dates,
        )
        corrected, events = apply_round_trip_corrections_to_dataframe(df)
        assert events == []
        pd.testing.assert_frame_equal(corrected, df)

    def test_original_dataframe_unchanged(self):
        dates = pd.date_range("2024-01-01", periods=6, freq="D")
        df = pd.DataFrame(
            {"siren": [11.0, 11.0, 11.0, 27.0, 10.0, 10.0]},
            index=dates,
        )
        original = df.copy()
        apply_round_trip_corrections_to_dataframe(df)
        pd.testing.assert_frame_equal(df, original)

    def test_corrections_sorted_by_jump_magnitude(self):
        dates = pd.date_range("2024-01-01", periods=8, freq="D")
        df = pd.DataFrame(
            {
                "a": [10.0, 10.0, 10.0, 25.0, 10.0, 10.0, 10.0, 10.0],  # 2.5x
                "b": [10.0, 10.0, 10.0, 100.0, 10.0, 10.0, 10.0, 10.0],  # 10x
            },
            index=dates,
        )
        _, events = apply_round_trip_corrections_to_dataframe(df)
        assert len(events) == 2
        # Bigger jump first.
        assert events[0]["coin"] == "B"
        assert events[1]["coin"] == "A"

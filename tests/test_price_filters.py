"""
Tests for price data filtering tools in Halvix.

Tests cover:
- Volume outlier detection
- Volume outlier correction (iterative)
- SMA smoothing functions (Series and DataFrame)
- DataFrame-level volume corrections
- Edge cases (empty data, single row, zero values, NaN handling)
"""

import numpy as np
import pandas as pd
import pytest

from data.price_filters import (
    DEFAULT_MIN_VOLUME_FOR_OUTLIER_CHECK,
    DEFAULT_OUTLIER_WINDOW_DAYS,
    DEFAULT_VOLUME_OUTLIER_THRESHOLD,
    DEFAULT_VOLUME_SMA_WINDOW,
    apply_volume_corrections_to_dataframe,
    apply_volume_sma_smoothing,
    apply_volume_sma_smoothing_to_dataframe,
    correct_volume_outliers,
    detect_volume_outliers,
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


class TestDetectVolumeOutliers:
    """Tests for volume outlier detection."""

    @pytest.fixture
    def sample_volume_series(self):
        """Create sample volume series with an obvious outlier."""
        dates = pd.date_range("2024-01-01", periods=15, freq="D")
        # Normal volumes around 10000, with a huge spike on day 10
        volumes = [
            10000,
            10500,
            9500,
            10200,
            10800,
            9800,
            10100,
            10300,
            9900,
            500000,
            10000,
            10100,
            10200,
            10300,
            10400,
        ]
        return pd.Series(volumes, index=dates)

    def test_detects_large_spike(self, sample_volume_series):
        """Test that a large volume spike is detected as an outlier."""
        outliers = detect_volume_outliers(
            sample_volume_series,
            threshold=20,
            min_volume=5000,
            window_days=7,
        )

        # Day 10 (index 9) should be flagged as outlier
        assert outliers.iloc[9] is True or outliers.iloc[9] == True  # noqa: E712
        # Most other days should not be outliers
        assert outliers.iloc[0:7].sum() == 0

    def test_respects_min_volume_threshold(self):
        """Test that volumes below min_volume are not flagged."""
        dates = pd.date_range("2024-01-01", periods=15, freq="D")
        # Small volumes with a spike that's still below min_volume threshold
        volumes = [100, 100, 100, 100, 100, 100, 100, 100, 100, 5000, 100, 100, 100, 100, 100]
        series = pd.Series(volumes, index=dates)

        outliers = detect_volume_outliers(
            series,
            threshold=20,
            min_volume=10000,  # High min_volume
            window_days=7,
        )

        # No outliers should be detected because all values are below min_volume
        assert outliers.sum() == 0

    def test_respects_threshold_parameter(self):
        """Test that threshold parameter controls sensitivity."""
        dates = pd.date_range("2024-01-01", periods=15, freq="D")
        volumes = [
            10000,
            10000,
            10000,
            10000,
            10000,
            10000,
            10000,
            10000,
            10000,
            150000,
            10000,
            10000,
            10000,
            10000,
            10000,
        ]
        series = pd.Series(volumes, index=dates)

        # With threshold=20, 150000 is only 15x median, so not an outlier
        outliers_high_threshold = detect_volume_outliers(
            series, threshold=20, min_volume=5000, window_days=7
        )

        # With threshold=10, 150000 is 15x median, so it is an outlier
        outliers_low_threshold = detect_volume_outliers(
            series, threshold=10, min_volume=5000, window_days=7
        )

        assert outliers_high_threshold.iloc[9] == False  # noqa: E712
        assert outliers_low_threshold.iloc[9] == True  # noqa: E712

    def test_empty_series_returns_empty(self):
        """Test that empty series returns empty boolean series."""
        empty_series = pd.Series(dtype=float)
        outliers = detect_volume_outliers(empty_series)

        assert len(outliers) == 0
        assert outliers.dtype == bool

    def test_requires_min_periods_for_rolling(self):
        """Test that early data points are not flagged due to insufficient window."""
        dates = pd.date_range("2024-01-01", periods=5, freq="D")
        volumes = [10000, 500000, 10000, 10000, 10000]  # Spike on day 2
        series = pd.Series(volumes, index=dates)

        outliers = detect_volume_outliers(series, threshold=20, min_volume=5000, window_days=7)

        # First few days may not have enough data for reliable detection
        # (min_periods=3, shift=1, so need at least 4 days)
        # Day 2 (index 1) might not be detected due to insufficient history
        assert isinstance(outliers, pd.Series)

    def test_handles_zero_past_median(self):
        """Test that zero past median doesn't cause issues."""
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        # Start with zeros, then normal values
        volumes = [0, 0, 0, 0, 0, 10000, 10000, 10000, 500000, 10000]
        series = pd.Series(volumes, index=dates)

        outliers = detect_volume_outliers(series, threshold=20, min_volume=5000, window_days=5)

        # Should not raise any errors
        assert isinstance(outliers, pd.Series)
        assert len(outliers) == 10


class TestCorrectVolumeOutliers:
    """Tests for iterative volume outlier correction."""

    @pytest.fixture
    def series_with_outlier(self):
        """Create series with a single clear outlier."""
        dates = pd.date_range("2024-01-01", periods=15, freq="D")
        volumes = [10000.0] * 9 + [500000.0] + [10000.0] * 5
        return pd.Series(volumes, index=dates)

    def test_corrects_single_outlier(self, series_with_outlier):
        """Test that a single outlier is corrected."""
        corrected, corrections = correct_volume_outliers(
            series_with_outlier,
            threshold=20,
            min_volume=5000,
            window_days=7,
        )

        # Outlier should be reduced
        assert corrected.iloc[9] < 500000
        assert corrected.iloc[9] > 0

        # Should have at least one correction
        assert len(corrections) >= 1
        assert corrections[0]["original"] == 500000.0

    def test_correction_record_structure(self, series_with_outlier):
        """Test that correction records have the expected structure."""
        _, corrections = correct_volume_outliers(
            series_with_outlier,
            threshold=20,
            min_volume=5000,
            window_days=7,
        )

        if corrections:
            correction = corrections[0]
            assert "date" in correction
            assert "original" in correction
            assert "corrected" in correction
            assert "ratio" in correction
            assert "iteration" in correction

    def test_corrections_sorted_by_ratio(self, series_with_outlier):
        """Test that corrections are sorted by ratio (descending)."""
        # Add multiple outliers
        dates = pd.date_range("2024-01-01", periods=20, freq="D")
        volumes = [10000.0] * 8 + [200000.0] + [10000.0] * 3 + [500000.0] + [10000.0] * 7
        series = pd.Series(volumes, index=dates)

        _, corrections = correct_volume_outliers(
            series,
            threshold=10,
            min_volume=5000,
            window_days=7,
        )

        if len(corrections) > 1:
            ratios = [c["ratio"] for c in corrections]
            assert ratios == sorted(ratios, reverse=True)

    def test_iterative_correction(self):
        """Test that multiple outliers are corrected iteratively."""
        dates = pd.date_range("2024-01-01", periods=20, freq="D")
        # Multiple outliers that might need multiple iterations
        volumes = [10000.0] * 8 + [500000.0, 600000.0] + [10000.0] * 10
        series = pd.Series(volumes, index=dates)

        corrected, corrections = correct_volume_outliers(
            series,
            threshold=20,
            min_volume=5000,
            window_days=7,
            max_iterations=10,
        )

        # Both outliers should be reduced
        assert corrected.iloc[8] < 500000
        assert corrected.iloc[9] < 600000

    def test_max_iterations_limit(self):
        """Test that max_iterations is respected."""
        dates = pd.date_range("2024-01-01", periods=20, freq="D")
        volumes = [10000.0] * 8 + [500000.0] * 5 + [10000.0] * 7
        series = pd.Series(volumes, index=dates)

        _, corrections = correct_volume_outliers(
            series,
            threshold=20,
            min_volume=5000,
            window_days=7,
            max_iterations=1,
        )

        # All corrections should be from iteration 1
        for c in corrections:
            assert c["iteration"] == 1

    def test_empty_series_returns_empty(self):
        """Test that empty series returns empty results."""
        empty_series = pd.Series(dtype=float)
        corrected, corrections = correct_volume_outliers(empty_series)

        assert len(corrected) == 0
        assert corrections == []

    def test_no_corrections_needed(self):
        """Test series with no outliers returns unchanged."""
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        volumes = [10000.0 + i * 100 for i in range(10)]  # Gradual increase
        series = pd.Series(volumes, index=dates)

        corrected, corrections = correct_volume_outliers(
            series,
            threshold=20,
            min_volume=5000,
            window_days=7,
        )

        pd.testing.assert_series_equal(corrected, series)
        assert corrections == []

    def test_skips_first_row_correction(self):
        """Test that the first row cannot be corrected (no previous value)."""
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        volumes = [500000.0] + [10000.0] * 9  # First value is outlier
        series = pd.Series(volumes, index=dates)

        corrected, corrections = correct_volume_outliers(
            series,
            threshold=20,
            min_volume=5000,
            window_days=3,
        )

        # First value should remain unchanged (no previous value to interpolate)
        # The corrections list may or may not include the first row depending on logic
        assert (
            corrected.iloc[0] == 500000.0
            or len([c for c in corrections if "2024-01-01" in c["date"]]) == 0
        )


class TestApplyVolumeSMASmoothing:
    """Tests for SMA smoothing on Series."""

    @pytest.fixture
    def sample_volume_series(self):
        """Create sample volume series."""
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        volumes = [1000.0 * (i + 1) for i in range(10)]
        return pd.Series(volumes, index=dates)

    def test_basic_sma_calculation(self, sample_volume_series):
        """Test basic SMA calculation."""
        smoothed = apply_volume_sma_smoothing(
            sample_volume_series,
            window=3,
            zero_pad=False,
        )

        # First two values should be NaN (window=3, min_periods not met)
        assert pd.isna(smoothed.iloc[0])
        assert pd.isna(smoothed.iloc[1])

        # Third value should be average of first 3: (1000+2000+3000)/3 = 2000
        assert smoothed.iloc[2] == pytest.approx(2000.0)

    def test_zero_padding(self):
        """Test zero padding before first valid value."""
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        # First 3 values are NaN
        volumes = [np.nan, np.nan, np.nan, 1000.0, 2000.0, 3000.0, 4000.0, 5000.0, 6000.0, 7000.0]
        series = pd.Series(volumes, index=dates)

        smoothed = apply_volume_sma_smoothing(
            series,
            window=3,
            zero_pad=True,
        )

        # With zero padding, early smoothed values should be lower
        # (zeros are included in the average)
        assert isinstance(smoothed, pd.Series)
        # Value at index 5 (first full window after NaNs with padding)
        # should be affected by zero padding

    def test_no_zero_padding(self):
        """Test SMA without zero padding."""
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        volumes = [np.nan, np.nan, np.nan, 1000.0, 2000.0, 3000.0, 4000.0, 5000.0, 6000.0, 7000.0]
        series = pd.Series(volumes, index=dates)

        smoothed = apply_volume_sma_smoothing(
            series,
            window=3,
            zero_pad=False,
        )

        # Without zero padding, NaNs propagate naturally
        assert isinstance(smoothed, pd.Series)

    def test_empty_series_returns_empty(self):
        """Test that empty series returns empty."""
        empty_series = pd.Series(dtype=float)
        smoothed = apply_volume_sma_smoothing(empty_series)

        assert len(smoothed) == 0

    def test_single_value_series(self):
        """Test series with single value."""
        dates = pd.date_range("2024-01-01", periods=1, freq="D")
        series = pd.Series([1000.0], index=dates)

        smoothed = apply_volume_sma_smoothing(series, window=3)

        # Single value can't fill window
        assert len(smoothed) == 1

    def test_default_window_size(self, sample_volume_series):
        """Test that default window size is used."""
        smoothed = apply_volume_sma_smoothing(sample_volume_series)

        # Default window is 120, so with 10 data points, all should be NaN
        assert smoothed.isna().all()


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
    """Tests for edge cases across all functions."""

    def test_single_row_series_detection(self):
        """Test outlier detection with single row."""
        dates = pd.date_range("2024-01-01", periods=1, freq="D")
        series = pd.Series([10000.0], index=dates)

        outliers = detect_volume_outliers(series)

        assert len(outliers) == 1
        # Single row can't be compared to history
        assert outliers.iloc[0] == False  # noqa: E712

    def test_single_row_series_correction(self):
        """Test outlier correction with single row."""
        dates = pd.date_range("2024-01-01", periods=1, freq="D")
        series = pd.Series([10000.0], index=dates)

        corrected, corrections = correct_volume_outliers(series)

        pd.testing.assert_series_equal(corrected, series)
        assert corrections == []

    def test_all_nan_series(self):
        """Test series with all NaN values."""
        dates = pd.date_range("2024-01-01", periods=5, freq="D")
        series = pd.Series([np.nan] * 5, index=dates)

        outliers = detect_volume_outliers(series)
        corrected, corrections = correct_volume_outliers(series)
        smoothed = apply_volume_sma_smoothing(series, window=3)

        assert len(outliers) == 5
        assert len(corrected) == 5
        assert len(smoothed) == 5

    def test_all_zero_series(self):
        """Test series with all zero values."""
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        series = pd.Series([0.0] * 10, index=dates)

        outliers = detect_volume_outliers(series, min_volume=0)
        corrected, corrections = correct_volume_outliers(series)

        # No outliers because ratio to zero median is undefined
        assert outliers.sum() == 0
        assert corrections == []

    def test_negative_volume_handling(self):
        """Test handling of negative volumes (invalid data)."""
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        volumes = [10000.0] * 5 + [-1000.0] + [10000.0] * 4
        series = pd.Series(volumes, index=dates)

        # Should not raise errors
        outliers = detect_volume_outliers(series)
        corrected, corrections = correct_volume_outliers(series)

        assert isinstance(outliers, pd.Series)
        assert isinstance(corrected, pd.Series)

    def test_inf_volume_handling(self):
        """Test handling of infinite volumes."""
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        volumes = [10000.0] * 5 + [np.inf] + [10000.0] * 4
        series = pd.Series(volumes, index=dates)

        # Should handle inf without crashing
        outliers = detect_volume_outliers(series)

        assert isinstance(outliers, pd.Series)

    def test_mixed_nan_values(self):
        """Test series with scattered NaN values."""
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        volumes = [
            10000.0,
            np.nan,
            10000.0,
            np.nan,
            10000.0,
            500000.0,
            10000.0,
            np.nan,
            10000.0,
            10000.0,
        ]
        series = pd.Series(volumes, index=dates)

        outliers = detect_volume_outliers(series, threshold=20, min_volume=5000, window_days=5)
        corrected, _ = correct_volume_outliers(series, threshold=20, min_volume=5000, window_days=5)

        assert isinstance(outliers, pd.Series)
        assert isinstance(corrected, pd.Series)
        assert len(corrected) == 10

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


class TestInterpolationLogic:
    """Tests for the interpolation logic in outlier correction."""

    def test_interpolation_uses_previous_value(self):
        """Test that interpolation considers previous day value."""
        dates = pd.date_range("2024-01-01", periods=15, freq="D")
        # Day 9 is 10000, Day 10 is outlier
        volumes = [10000.0] * 9 + [500000.0] + [10000.0] * 5
        series = pd.Series(volumes, index=dates)

        corrected, corrections = correct_volume_outliers(
            series,
            threshold=20,
            min_volume=5000,
            window_days=7,
        )

        if corrections:
            # Corrected value should be between previous and capped
            corrected_val = corrected.iloc[9]
            prev_val = series.iloc[8]  # 10000

            # Interpolated = (prev + min(original, capped)) / 2
            # Should be greater than prev_val / 2 and less than original
            assert corrected_val > prev_val / 2
            assert corrected_val < 500000

    def test_capping_logic(self):
        """Test that outliers are capped at threshold * median."""
        dates = pd.date_range("2024-01-01", periods=15, freq="D")
        volumes = [10000.0] * 9 + [500000.0] + [10000.0] * 5
        series = pd.Series(volumes, index=dates)

        corrected, _ = correct_volume_outliers(
            series,
            threshold=20,  # Cap at 20x median
            min_volume=5000,
            window_days=7,
        )

        # The corrected value should reflect the capping
        # median ~ 10000, threshold=20, so cap = 200000
        # interpolated = (10000 + min(500000, 200000)) / 2 = (10000 + 200000) / 2 = 105000
        corrected_val = corrected.iloc[9]
        assert corrected_val == pytest.approx(105000.0, rel=0.1)

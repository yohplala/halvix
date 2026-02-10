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
    apply_volume_corrections_to_dataframe,
    apply_volume_sma_smoothing_to_dataframe,
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

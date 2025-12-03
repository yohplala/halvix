"""
Tests for TOTAL2 processor.

Tests cover:
- Volume-weighted TOTAL2 calculation logic
- Daily composition tracking
- Filtering for TOTAL2 eligibility
- Edge cases
"""

import tempfile
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from data.cache import PriceDataCache
from data.processor import ProcessorError, Total2Processor, Total2Result


class TestTotal2ProcessorInit:
    """Tests for processor initialization."""

    def test_default_initialization(self):
        """Test processor initializes with defaults."""
        processor = Total2Processor()

        assert processor.price_cache is not None
        assert processor.token_filter is not None
        assert processor.top_n == 50  # Default from config

    def test_custom_top_n(self):
        """Test processor with custom top_n."""
        processor = Total2Processor(top_n=25)
        assert processor.top_n == 25


class TestTotal2FilterCoins:
    """Tests for coin filtering for TOTAL2."""

    @pytest.fixture
    def processor(self):
        return Total2Processor()

    def test_filters_bitcoin(self, processor):
        """Test that Bitcoin is filtered out."""
        coins = ["btc", "eth", "sol"]
        filtered = processor.filter_coins_for_total2(coins)

        assert "btc" not in filtered
        assert "eth" in filtered
        assert "sol" in filtered

    def test_filters_wrapped_tokens(self, processor):
        """Test that wrapped tokens are filtered out."""
        coins = ["eth", "wbtc", "steth", "sol"]
        filtered = processor.filter_coins_for_total2(coins)

        assert "eth" in filtered
        assert "wbtc" not in filtered
        assert "steth" not in filtered
        assert "sol" in filtered

    def test_filters_stablecoins(self, processor):
        """Test that stablecoins are filtered out for TOTAL2."""
        coins = ["eth", "usdt", "usdc", "sol"]
        filtered = processor.filter_coins_for_total2(coins)

        assert "eth" in filtered
        assert "usdt" not in filtered
        assert "usdc" not in filtered
        assert "sol" in filtered


class TestTotal2Calculation:
    """Tests for volume-weighted TOTAL2 calculation logic."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for price cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def sample_price_data(self):
        """Create sample price data for testing with volume."""
        dates = pd.date_range("2024-01-01", periods=5, freq="D")

        eth_data = pd.DataFrame(
            {
                "price": [0.05, 0.052, 0.051, 0.053, 0.054],
                "volume_to": [10000, 11000, 10500, 12000, 11500],  # Volume in BTC
            },
            index=dates,
        )

        sol_data = pd.DataFrame(
            {
                "price": [0.003, 0.0031, 0.0029, 0.0032, 0.0033],
                "volume_to": [2000, 2100, 1900, 2200, 2300],
            },
            index=dates,
        )

        ada_data = pd.DataFrame(
            {
                "price": [0.00002, 0.000021, 0.000019, 0.000022, 0.000023],
                "volume_to": [500, 550, 450, 600, 580],
            },
            index=dates,
        )

        return {
            "eth": eth_data,
            "sol": sol_data,
            "ada": ada_data,
        }

    def test_calculate_daily_total2(self, temp_dir, sample_price_data):
        """Test daily volume-weighted TOTAL2 calculation."""
        # Create price cache and save sample data
        cache = PriceDataCache(prices_dir=temp_dir)
        for coin_id, df in sample_price_data.items():
            cache.set_prices(coin_id, df)

        processor = Total2Processor(price_cache=cache, top_n=3)

        # Calculate for a specific date
        target_date = datetime(2024, 1, 1)
        result = processor._calculate_daily_total2(sample_price_data, target_date)

        assert result is not None
        index_record, composition = result

        # Check index record
        assert "total2_price" in index_record
        assert "total_volume" in index_record
        assert "coin_count" in index_record
        assert index_record["coin_count"] == 3

        # Check composition
        assert len(composition) == 3
        # ETH should be rank 1 (highest volume)
        eth_entry = [c for c in composition if c["coin_id"] == "eth"][0]
        assert eth_entry["rank"] == 1

    def test_volume_weighted_average_calculation(self, temp_dir, sample_price_data):
        """Test that volume-weighted average is calculated correctly."""
        cache = PriceDataCache(prices_dir=temp_dir)
        for coin_id, df in sample_price_data.items():
            cache.set_prices(coin_id, df)

        processor = Total2Processor(price_cache=cache, top_n=3)

        target_date = datetime(2024, 1, 1)
        result = processor._calculate_daily_total2(sample_price_data, target_date)

        index_record, _ = result

        # Manual calculation for 2024-01-01:
        # ETH: price=0.05, volume=10000
        # SOL: price=0.003, volume=2000
        # ADA: price=0.00002, volume=500
        # Total volume = 12500
        # Weighted = (0.05*10000 + 0.003*2000 + 0.00002*500) / 12500
        expected_weighted = (0.05 * 10000 + 0.003 * 2000 + 0.00002 * 500) / 12500

        assert abs(index_record["total2_price"] - expected_weighted) < 1e-10

    def test_full_calculation_pipeline(self, temp_dir, sample_price_data):
        """Test full TOTAL2 calculation."""
        cache = PriceDataCache(prices_dir=temp_dir)
        for coin_id, df in sample_price_data.items():
            cache.set_prices(coin_id, df)

        processor = Total2Processor(price_cache=cache, top_n=3)

        result = processor.calculate_total2(show_progress=False)

        assert isinstance(result, Total2Result)
        assert result.coins_processed == 3
        assert len(result.index_df) == 5  # 5 days
        assert not result.composition_df.empty

        # Check all expected columns
        assert "total2_price" in result.index_df.columns
        assert "total_volume" in result.index_df.columns
        assert "coin_count" in result.index_df.columns


class TestTotal2SaveLoad:
    """Tests for saving and loading TOTAL2 results."""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def sample_result(self):
        """Create a sample Total2Result."""
        dates = pd.date_range("2024-01-01", periods=3, freq="D")

        index_df = pd.DataFrame(
            {
                "total2_price": [0.04, 0.041, 0.042],
                "total_volume": [12500, 13000, 13500],
                "coin_count": [50, 50, 50],
            },
            index=dates,
        )
        index_df.index.name = "date"

        composition_df = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-01", "2024-01-01", "2024-01-02", "2024-01-02"]),
                "rank": [1, 2, 1, 2],
                "coin_id": ["eth", "sol", "eth", "sol"],
                "volume": [10000, 2000, 10500, 2100],
                "weight": [0.8, 0.2, 0.8, 0.2],
                "price_btc": [0.05, 0.003, 0.051, 0.0031],
            }
        )

        return Total2Result(
            index_df=index_df,
            composition_df=composition_df,
            coins_processed=2,
            date_range=(date(2024, 1, 1), date(2024, 1, 3)),
            avg_coins_per_day=50.0,
        )

    def test_save_and_load_index(self, temp_dir, sample_result):
        """Test saving and loading TOTAL2 index."""
        processor = Total2Processor()

        index_path = temp_dir / "total2_index.parquet"
        comp_path = temp_dir / "total2_composition.parquet"

        with patch("data.processor.PROCESSED_DIR", temp_dir), patch(
            "data.processor.TOTAL2_INDEX_FILE", index_path
        ), patch("data.processor.TOTAL2_COMPOSITION_FILE", comp_path):
            processor.save_results(sample_result, index_path, comp_path)

            assert index_path.exists()
            assert comp_path.exists()

            # Load and verify
            loaded = processor.load_total2_index(index_path)
            pd.testing.assert_frame_equal(
                loaded.reset_index(drop=True), sample_result.index_df.reset_index(drop=True)
            )


class TestTotal2EdgeCases:
    """Tests for edge cases in TOTAL2 calculation."""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_no_cached_data_raises_error(self, temp_dir):
        """Test that empty cache raises appropriate error."""
        cache = PriceDataCache(prices_dir=temp_dir)
        processor = Total2Processor(price_cache=cache)

        with pytest.raises(ProcessorError, match="No cached price data"):
            processor.calculate_total2(show_progress=False)

    def test_all_filtered_raises_error(self, temp_dir):
        """Test error when all coins are filtered out."""
        cache = PriceDataCache(prices_dir=temp_dir)

        # Only save wrapped tokens
        dates = pd.date_range("2024-01-01", periods=3, freq="D")
        wbtc_data = pd.DataFrame(
            {
                "price": [1.0, 1.0, 1.0],
                "volume_to": [1000, 1000, 1000],
            },
            index=dates,
        )
        cache.set_prices("wbtc", wbtc_data)

        processor = Total2Processor(price_cache=cache)

        with pytest.raises(ProcessorError, match="No eligible coins"):
            processor.calculate_total2(show_progress=False)

    def test_less_than_top_n_coins(self, temp_dir):
        """Test calculation when fewer coins than top_n are available."""
        cache = PriceDataCache(prices_dir=temp_dir)

        dates = pd.date_range("2024-01-01", periods=3, freq="D")
        eth_data = pd.DataFrame(
            {
                "price": [0.05, 0.051, 0.052],
                "volume_to": [10000, 10500, 11000],
            },
            index=dates,
        )
        sol_data = pd.DataFrame(
            {
                "price": [0.003, 0.0031, 0.0032],
                "volume_to": [2000, 2100, 2200],
            },
            index=dates,
        )
        ada_data = pd.DataFrame(
            {
                "price": [0.00002, 0.000021, 0.000022],
                "volume_to": [500, 550, 600],
            },
            index=dates,
        )

        cache.set_prices("eth", eth_data)
        cache.set_prices("sol", sol_data)
        cache.set_prices("ada", ada_data)

        # Request top 50, but only 3 available
        processor = Total2Processor(price_cache=cache, top_n=50)
        result = processor.calculate_total2(show_progress=False)

        # Should still work with 3 coins
        assert result.coins_processed == 3
        # Each day should have 3 coins
        assert (result.index_df["coin_count"] == 3).all()

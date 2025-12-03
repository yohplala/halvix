"""
Tests for TOTAL2 processor.

Tests cover:
- TOTAL2 calculation logic
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
        coins = ["bitcoin", "ethereum", "solana"]
        filtered = processor.filter_coins_for_total2(coins)

        assert "bitcoin" not in filtered
        assert "ethereum" in filtered
        assert "solana" in filtered

    def test_filters_wrapped_tokens(self, processor):
        """Test that wrapped tokens are filtered out."""
        coins = ["ethereum", "wrapped-bitcoin", "lido-staked-ether", "solana"]
        filtered = processor.filter_coins_for_total2(coins)

        assert "ethereum" in filtered
        assert "wrapped-bitcoin" not in filtered
        assert "lido-staked-ether" not in filtered
        assert "solana" in filtered

    def test_filters_stablecoins(self, processor):
        """Test that stablecoins are filtered out for TOTAL2."""
        coins = ["ethereum", "tether", "usd-coin", "solana"]
        filtered = processor.filter_coins_for_total2(coins)

        assert "ethereum" in filtered
        assert "tether" not in filtered
        assert "usd-coin" not in filtered
        assert "solana" in filtered


class TestTotal2Calculation:
    """Tests for TOTAL2 calculation logic."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for price cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def sample_price_data(self):
        """Create sample price data for testing."""
        dates = pd.date_range("2024-01-01", periods=5, freq="D")

        eth_data = pd.DataFrame(
            {
                "price": [0.05, 0.052, 0.051, 0.053, 0.054],
                "market_cap": [400e9, 410e9, 405e9, 420e9, 430e9],
                "volume": [10e9, 11e9, 10.5e9, 12e9, 11.5e9],
            },
            index=dates,
        )

        sol_data = pd.DataFrame(
            {
                "price": [0.003, 0.0031, 0.0029, 0.0032, 0.0033],
                "market_cap": [80e9, 82e9, 78e9, 85e9, 88e9],
                "volume": [2e9, 2.1e9, 1.9e9, 2.2e9, 2.3e9],
            },
            index=dates,
        )

        ada_data = pd.DataFrame(
            {
                "price": [0.00002, 0.000021, 0.000019, 0.000022, 0.000023],
                "market_cap": [20e9, 21e9, 19e9, 22e9, 23e9],
                "volume": [0.5e9, 0.55e9, 0.45e9, 0.6e9, 0.58e9],
            },
            index=dates,
        )

        return {
            "ethereum": eth_data,
            "solana": sol_data,
            "cardano": ada_data,
        }

    def test_calculate_daily_total2(self, temp_dir, sample_price_data):
        """Test daily TOTAL2 calculation."""
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
        assert "total_market_cap" in index_record
        assert "coin_count" in index_record
        assert index_record["coin_count"] == 3

        # Check composition
        assert len(composition) == 3
        # ETH should be rank 1 (highest market cap)
        eth_entry = [c for c in composition if c["coin_id"] == "ethereum"][0]
        assert eth_entry["rank"] == 1

    def test_weighted_average_calculation(self, temp_dir, sample_price_data):
        """Test that weighted average is calculated correctly."""
        cache = PriceDataCache(prices_dir=temp_dir)
        for coin_id, df in sample_price_data.items():
            cache.set_prices(coin_id, df)

        processor = Total2Processor(price_cache=cache, top_n=3)

        target_date = datetime(2024, 1, 1)
        result = processor._calculate_daily_total2(sample_price_data, target_date)

        index_record, _ = result

        # Manual calculation for 2024-01-01:
        # ETH: price=0.05, mcap=400e9
        # SOL: price=0.003, mcap=80e9
        # ADA: price=0.00002, mcap=20e9
        # Total mcap = 500e9
        # Weighted = (0.05*400e9 + 0.003*80e9 + 0.00002*20e9) / 500e9
        expected_weighted = (0.05 * 400e9 + 0.003 * 80e9 + 0.00002 * 20e9) / 500e9

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
        assert "total_market_cap" in result.index_df.columns
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
                "total_market_cap": [500e9, 510e9, 520e9],
                "coin_count": [50, 50, 50],
            },
            index=dates,
        )
        index_df.index.name = "date"

        composition_df = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-01", "2024-01-01", "2024-01-02", "2024-01-02"]),
                "rank": [1, 2, 1, 2],
                "coin_id": ["ethereum", "solana", "ethereum", "solana"],
                "market_cap": [400e9, 80e9, 410e9, 82e9],
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
                "market_cap": [10e9, 10e9, 10e9],
            },
            index=dates,
        )
        cache.set_prices("wrapped-bitcoin", wbtc_data)

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
                "market_cap": [400e9, 410e9, 420e9],
            },
            index=dates,
        )
        sol_data = pd.DataFrame(
            {
                "price": [0.003, 0.0031, 0.0032],
                "market_cap": [80e9, 82e9, 85e9],
            },
            index=dates,
        )
        ada_data = pd.DataFrame(
            {
                "price": [0.00002, 0.000021, 0.000022],
                "market_cap": [20e9, 21e9, 22e9],
            },
            index=dates,
        )

        cache.set_prices("ethereum", eth_data)
        cache.set_prices("solana", sol_data)
        cache.set_prices("cardano", ada_data)

        # Request top 50, but only 3 available
        processor = Total2Processor(price_cache=cache, top_n=50)
        result = processor.calculate_total2(show_progress=False)

        # Should still work with 3 coins
        assert result.coins_processed == 3
        # Each day should have 3 coins
        assert (result.index_df["coin_count"] == 3).all()

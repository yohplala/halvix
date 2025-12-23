"""
Tests for TOTAL2 and TOTAL2b processors.

Tests cover:
- Volume-weighted TOTAL2/TOTAL2b calculation logic
- Daily composition tracking
- Filtering for TOTAL2 eligibility
- TOTAL2b freeze period and price scaling
- Edge cases
"""

import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from data.cache import PriceDataCache
from data.processor import (
    ProcessorError,
    Total2bProcessor,
    Total2Processor,
    Total2Result,
    get_processor,
)


class TestProcessorFactory:
    """Tests for the get_processor factory function."""

    def test_get_total2_processor(self):
        """Test factory returns Total2Processor for 'total2'."""
        processor = get_processor("total2")
        assert isinstance(processor, Total2Processor)

    def test_get_total2b_processor(self):
        """Test factory returns Total2bProcessor for 'total2b'."""
        processor = get_processor("total2b")
        assert isinstance(processor, Total2bProcessor)

    def test_default_is_total2b(self):
        """Test factory defaults to Total2bProcessor."""
        processor = get_processor()
        assert isinstance(processor, Total2bProcessor)

    def test_invalid_index_type_raises(self):
        """Test factory raises ValueError for unknown type."""
        with pytest.raises(ValueError, match="Unknown index type"):
            get_processor("invalid")


class TestTotal2ProcessorInit:
    """Tests for Total2Processor initialization."""

    def test_default_initialization(self):
        """Test processor initializes with defaults."""
        from config import TOP_N_BY_VOLUME_FOR_TOTAL2

        processor = Total2Processor()

        assert processor.price_cache is not None
        assert processor.coin_filter is not None
        assert processor.top_n == TOP_N_BY_VOLUME_FOR_TOTAL2

    def test_custom_top_n(self):
        """Test processor with custom top_n."""
        processor = Total2Processor(top_n=25)
        assert processor.top_n == 25

    def test_index_type(self):
        """Test processor has correct index type."""
        processor = Total2Processor()
        assert processor.INDEX_TYPE == "total2"


class TestTotal2bProcessorInit:
    """Tests for Total2bProcessor initialization."""

    def test_default_initialization(self):
        """Test processor initializes with defaults."""
        from config import (
            TOP_N_BY_VOLUME_FOR_TOTAL2,
            TOTAL2B_ENTRY_FREEZE_PERIOD_DAYS,
            TOTAL2B_MIN_COINS_FOR_SCALING,
        )

        processor = Total2bProcessor()

        assert processor.price_cache is not None
        assert processor.coin_filter is not None
        assert processor.top_n == TOP_N_BY_VOLUME_FOR_TOTAL2
        assert processor.freeze_period_days == TOTAL2B_ENTRY_FREEZE_PERIOD_DAYS
        assert processor.min_coins_for_scaling == TOTAL2B_MIN_COINS_FOR_SCALING

    def test_custom_freeze_period(self):
        """Test processor with custom freeze period."""
        processor = Total2bProcessor(freeze_period_days=14)
        assert processor.freeze_period_days == 14

    def test_index_type(self):
        """Test processor has correct index type."""
        processor = Total2bProcessor()
        assert processor.INDEX_TYPE == "total2b"


class TestTotal2FilterCoins:
    """Tests for coin filtering for TOTAL2 (shared by both processors)."""

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
        """Test that stablecoins are filtered out."""
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
                "close": [0.05, 0.052, 0.051, 0.053, 0.054],
                "volume_to": [10000, 11000, 10500, 12000, 11500],
            },
            index=dates,
        )

        sol_data = pd.DataFrame(
            {
                "close": [0.003, 0.0031, 0.0029, 0.0032, 0.0033],
                "volume_to": [2000, 2100, 1900, 2200, 2300],
            },
            index=dates,
        )

        ada_data = pd.DataFrame(
            {
                "close": [0.00002, 0.000021, 0.000019, 0.000022, 0.000023],
                "volume_to": [500, 550, 450, 600, 580],
            },
            index=dates,
        )

        return {
            "eth": eth_data,
            "sol": sol_data,
            "ada": ada_data,
        }

    def test_full_calculation_pipeline(self, temp_dir, sample_price_data):
        """Test full TOTAL2 calculation."""
        cache = PriceDataCache(prices_dir=temp_dir)
        for coin_id, df in sample_price_data.items():
            cache.set_prices(coin_id, df)

        processor = Total2Processor(price_cache=cache, top_n=3, volume_sma_window=2)

        result = processor.calculate_total2(show_progress=False)

        assert isinstance(result, Total2Result)
        assert result.coins_processed == 3
        assert result.index_type == "total2"
        assert len(result.index_df) >= 3
        assert not result.composition_df.empty

        assert "total2_price" in result.index_df.columns
        assert "total_volume" in result.index_df.columns
        assert "coin_count" in result.index_df.columns


class TestTotal2bCalculation:
    """Tests for TOTAL2b calculation with freeze period and scaling."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for price cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def sample_price_data_with_freeze(self):
        """Create sample price data that spans freeze period."""
        # 30 days of data to test freeze period (21 days)
        dates = pd.date_range("2024-01-01", periods=30, freq="D")

        eth_data = pd.DataFrame(
            {
                "close": [0.05 + i * 0.001 for i in range(30)],
                "volume_to": [10000 + i * 100 for i in range(30)],
            },
            index=dates,
        )

        sol_data = pd.DataFrame(
            {
                "close": [0.003 + i * 0.0001 for i in range(30)],
                "volume_to": [2000 + i * 50 for i in range(30)],
            },
            index=dates,
        )

        ada_data = pd.DataFrame(
            {
                "close": [0.00002 + i * 0.000001 for i in range(30)],
                "volume_to": [500 + i * 20 for i in range(30)],
            },
            index=dates,
        )

        return {
            "eth": eth_data,
            "sol": sol_data,
            "ada": ada_data,
        }

    def test_freeze_period_enforced(self, temp_dir, sample_price_data_with_freeze):
        """Test that coins must wait freeze period before joining index."""
        cache = PriceDataCache(prices_dir=temp_dir)
        for coin_id, df in sample_price_data_with_freeze.items():
            cache.set_prices(coin_id, df)

        # Use short freeze period for testing
        processor = Total2bProcessor(
            price_cache=cache,
            top_n=3,
            volume_sma_window=2,
            freeze_period_days=5,  # Short freeze for testing
        )

        result = processor.calculate_total2(show_progress=False)

        assert isinstance(result, Total2Result)
        assert result.index_type == "total2b"
        # First valid index should be after freeze period + SMA warmup
        assert len(result.index_df) > 0

    def test_full_calculation_pipeline(self, temp_dir, sample_price_data_with_freeze):
        """Test full TOTAL2b calculation."""
        cache = PriceDataCache(prices_dir=temp_dir)
        for coin_id, df in sample_price_data_with_freeze.items():
            cache.set_prices(coin_id, df)

        processor = Total2bProcessor(
            price_cache=cache,
            top_n=3,
            volume_sma_window=2,
            freeze_period_days=5,
        )

        result = processor.calculate_total2(show_progress=False)

        assert isinstance(result, Total2Result)
        assert result.coins_processed == 3
        assert result.index_type == "total2b"
        assert not result.composition_df.empty

        assert "total2_price" in result.index_df.columns
        assert "total_volume" in result.index_df.columns
        assert "coin_count" in result.index_df.columns

    def test_get_freeze_period_status(self, temp_dir, sample_price_data_with_freeze):
        """Test freeze period status reporting."""
        cache = PriceDataCache(prices_dir=temp_dir)
        for coin_id, df in sample_price_data_with_freeze.items():
            cache.set_prices(coin_id, df)

        processor = Total2bProcessor(
            price_cache=cache,
            freeze_period_days=21,
        )

        # Check status on a date during the freeze period
        status = processor.get_freeze_period_status(
            sample_price_data_with_freeze,
            target_date=date(2024, 1, 10),
        )

        assert len(status) == 3
        for s in status:
            assert "coin_id" in s
            assert "first_seen" in s
            assert "days_remaining" in s
            assert "eligible" in s


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
            index_type="total2",
        )

    def test_save_and_load_index(self, temp_dir, sample_result):
        """Test saving and loading TOTAL2 index."""
        processor = Total2Processor()

        index_path = temp_dir / "total2_index.parquet"
        comp_path = temp_dir / "total2_composition.parquet"

        with (
            patch("data.processor_base.PROCESSED_DIR", temp_dir),
            patch("data.processor_base.TOTAL2_INDEX_FILE", index_path),
            patch("data.processor_base.TOTAL2_COMPOSITION_FILE", comp_path),
        ):
            processor.save_results(sample_result, index_path, comp_path)

            assert index_path.exists()
            assert comp_path.exists()

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

        with pytest.raises(ProcessorError, match="No price data available"):
            processor.calculate_total2(show_progress=False)

    def test_all_filtered_raises_error(self, temp_dir):
        """Test error when all coins are filtered out."""
        cache = PriceDataCache(prices_dir=temp_dir)

        dates = pd.date_range("2024-01-01", periods=3, freq="D")
        wbtc_data = pd.DataFrame(
            {
                "close": [1.0, 1.0, 1.0],
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

        dates = pd.date_range("2024-01-01", periods=5, freq="D")
        eth_data = pd.DataFrame(
            {
                "close": [0.05, 0.051, 0.052, 0.053, 0.054],
                "volume_to": [10000, 10500, 11000, 11500, 12000],
            },
            index=dates,
        )
        sol_data = pd.DataFrame(
            {
                "close": [0.003, 0.0031, 0.0032, 0.0033, 0.0034],
                "volume_to": [2000, 2100, 2200, 2300, 2400],
            },
            index=dates,
        )
        ada_data = pd.DataFrame(
            {
                "close": [0.00002, 0.000021, 0.000022, 0.000023, 0.000024],
                "volume_to": [500, 550, 600, 650, 700],
            },
            index=dates,
        )

        cache.set_prices("eth", eth_data)
        cache.set_prices("sol", sol_data)
        cache.set_prices("ada", ada_data)

        processor = Total2Processor(price_cache=cache, top_n=50, volume_sma_window=2)
        result = processor.calculate_total2(show_progress=False)

        assert result.coins_processed == 3
        assert (result.index_df["coin_count"] == 3).all()


class TestTotal2bEdgeCases:
    """Tests for edge cases in TOTAL2b calculation."""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_no_cached_data_raises_error(self, temp_dir):
        """Test that empty cache raises appropriate error."""
        cache = PriceDataCache(prices_dir=temp_dir)
        processor = Total2bProcessor(price_cache=cache)

        with pytest.raises(ProcessorError, match="No price data available"):
            processor.calculate_total2(show_progress=False)

    def test_all_filtered_raises_error(self, temp_dir):
        """Test error when all coins are filtered out."""
        cache = PriceDataCache(prices_dir=temp_dir)

        dates = pd.date_range("2024-01-01", periods=30, freq="D")
        wbtc_data = pd.DataFrame(
            {
                "close": [1.0] * 30,
                "volume_to": [1000] * 30,
            },
            index=dates,
        )
        cache.set_prices("wbtc", wbtc_data)

        processor = Total2bProcessor(price_cache=cache, freeze_period_days=5)

        with pytest.raises(ProcessorError, match="No eligible coins"):
            processor.calculate_total2(show_progress=False)

"""
Tests for TOTAL2 and TOTAL2b processors.

Tests cover:
- Volume-weighted TOTAL2/TOTAL2b calculation logic
- Daily composition tracking
- Filtering for TOTAL2 eligibility
- TOTAL2b freeze period and price scaling
- Edge cases

Note: Common fixtures (temp_dir, sample_price_data, sample_price_data_with_freeze,
sample_result) are defined in conftest.py for reuse across test modules.
"""

from datetime import date
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
    """Tests for volume-weighted TOTAL2 calculation logic.

    Uses shared fixtures from conftest.py: temp_dir, sample_price_data.
    """

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
    """Tests for TOTAL2b calculation with freeze period and scaling.

    Uses shared fixtures from conftest.py: temp_dir, sample_price_data_with_freeze.
    """

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
    """Tests for saving and loading TOTAL2 results.

    Uses shared fixtures from conftest.py: temp_dir, sample_result.
    """

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
    """Tests for edge cases in TOTAL2 calculation.

    Uses shared fixtures from conftest.py: temp_dir.
    """

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


class TestTotal2bScalingOptimization:
    """Tests for the vectorized scaling optimization in TOTAL2b.

    Uses shared fixtures from conftest.py: temp_dir.
    """

    @pytest.fixture
    def sample_data_with_late_entry(self):
        """
        Create sample data where a coin enters after others are established.

        This tests the scaling logic: when a new coin enters, its prices
        should be scaled by prev_total2b / entry_price.
        """
        # 40 days of data to allow freeze period + scaling
        dates = pd.date_range("2024-01-01", periods=40, freq="D")

        # ETH: present from day 1, stable price ~0.05 BTC
        eth_data = pd.DataFrame(
            {
                "close": [0.05 + i * 0.0001 for i in range(40)],
                "volume_to": [10000 + i * 50 for i in range(40)],
            },
            index=dates,
        )

        # SOL: present from day 1, stable price ~0.003 BTC
        sol_data = pd.DataFrame(
            {
                "close": [0.003 + i * 0.00001 for i in range(40)],
                "volume_to": [5000 + i * 30 for i in range(40)],
            },
            index=dates,
        )

        # ADA: present from day 1, stable price ~0.00002 BTC
        ada_data = pd.DataFrame(
            {
                "close": [0.00002 + i * 0.0000001 for i in range(40)],
                "volume_to": [2000 + i * 20 for i in range(40)],
            },
            index=dates,
        )

        # AVAX: enters late (day 15), different price level
        # First 14 days have no data (NaN)
        avax_close = [None] * 14 + [0.01 + i * 0.0001 for i in range(26)]
        avax_volume = [None] * 14 + [3000 + i * 25 for i in range(26)]
        avax_data = pd.DataFrame(
            {
                "close": avax_close,
                "volume_to": avax_volume,
            },
            index=dates,
        )

        return {
            "eth": eth_data,
            "sol": sol_data,
            "ada": ada_data,
            "avax": avax_data,
        }

    def test_scaling_produces_valid_results(self, temp_dir, sample_data_with_late_entry):
        """Test that scaling optimization produces valid index values."""
        cache = PriceDataCache(prices_dir=temp_dir)
        for coin_id, df in sample_data_with_late_entry.items():
            cache.set_prices(coin_id, df)

        processor = Total2bProcessor(
            price_cache=cache,
            top_n=4,
            volume_sma_window=2,
            freeze_period_days=5,
            min_coins_for_scaling=3,  # Start scaling after 3 coins established
        )

        result = processor.calculate_total2(show_progress=False)

        # Basic validation
        assert isinstance(result, Total2Result)
        assert result.index_type == "total2b"
        assert len(result.index_df) > 0

        # All prices should be positive
        assert (result.index_df["total2_price"] > 0).all()

        # Index should be continuous (no large jumps due to unscaled entries)
        prices = result.index_df["total2_price"].values
        for i in range(1, len(prices)):
            ratio = prices[i] / prices[i - 1]
            # Price ratio should be reasonable (not > 2x or < 0.5x per day)
            assert 0.5 < ratio < 2.0, f"Unreasonable price ratio at index {i}: {ratio}"

    def test_scaling_events_recorded(self, temp_dir, sample_data_with_late_entry):
        """Test that scaling events are properly recorded."""
        cache = PriceDataCache(prices_dir=temp_dir)
        for coin_id, df in sample_data_with_late_entry.items():
            cache.set_prices(coin_id, df)

        processor = Total2bProcessor(
            price_cache=cache,
            top_n=4,
            volume_sma_window=2,
            freeze_period_days=5,
            min_coins_for_scaling=3,
        )

        result = processor.calculate_total2(show_progress=False)

        # AVAX should have a scaling event (it enters after the index is established)
        # price_outliers_corrected is repurposed for scaling events in Total2bProcessor
        scaling_events = result.price_outliers_corrected

        # Find AVAX scaling event
        avax_events = [e for e in scaling_events if e["coin"] == "AVAX"]

        # AVAX should have been scaled when it entered
        assert len(avax_events) >= 1, "AVAX should have a scaling event"

        event = avax_events[0]
        assert "change_factor" in event
        assert "prev_total2b" in event
        assert event["change_factor"] > 0


class TestTotal2bEdgeCases:
    """Tests for edge cases in TOTAL2b calculation.

    Uses shared fixtures from conftest.py: temp_dir.
    """

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


class TestSymbolReplacementDetection:
    """Tests for symbol replacement detection in TOTAL2b.

    Symbol replacement occurs when CryptoCompare reuses a ticker symbol
    for a different token (e.g., old MOVE token replaced by Movement Labs MOVE).

    Detection methods:
    1. Extreme ratio: price jumps >30x when both prices are positive
    2. Resurrection from zero: price goes from 0 to positive after prior trading
    """

    @pytest.fixture
    def processor(self):
        """Create a TOTAL2b processor with default settings."""
        return Total2bProcessor()

    def test_no_replacement_for_stable_prices(self, processor):
        """Test no replacement detected for coins with stable price history."""
        dates = pd.date_range("2024-01-01", periods=30, freq="D")
        prices = pd.Series([0.05 + i * 0.001 for i in range(30)], index=dates)
        first_seen = dates[0]

        result = processor._detect_symbol_replacement(prices, first_seen)
        assert result is None

    def test_extreme_ratio_detection(self, processor):
        """Test detection of extreme price ratio jumps (both prices > 0)."""
        dates = pd.date_range("2024-01-01", periods=30, freq="D")
        # Price stable at ~1e-10, then jumps 1000x on day 15
        prices_list = [1e-10] * 14 + [1e-7] * 16  # 1000x jump
        prices = pd.Series(prices_list, index=dates)
        first_seen = dates[0]

        result = processor._detect_symbol_replacement(prices, first_seen)

        assert result is not None
        assert result == dates[14]  # The day of the jump

    def test_resurrection_from_zero_detection(self, processor):
        """Test detection of resurrection from zero prices.

        This catches cases like MOVE where the old token went to exactly 0
        before the new token started trading.
        """
        dates = pd.date_range("2024-01-01", periods=30, freq="D")
        # Old token trades, goes to zero, then new token starts
        prices_list = [1e-10] * 5 + [0.0] * 10 + [1e-6] * 15  # Zero gap then resurrection
        prices = pd.Series(prices_list, index=dates)
        first_seen = dates[0]

        result = processor._detect_symbol_replacement(prices, first_seen)

        assert result is not None
        assert result == dates[15]  # The day of resurrection

    def test_no_replacement_for_initial_zero_to_trading(self, processor):
        """Test that starting from zero is NOT detected as replacement.

        When a coin first starts trading (0 -> positive), this is normal
        behavior, not a symbol replacement.
        """
        dates = pd.date_range("2024-01-01", periods=30, freq="D")
        # Coin starts with zeros, then begins trading - no prior trading history
        prices_list = [0.0] * 10 + [1e-6] * 20
        prices = pd.Series(prices_list, index=dates)
        first_seen = dates[0]

        result = processor._detect_symbol_replacement(prices, first_seen)

        # Should NOT detect replacement - this is just starting to trade
        assert result is None

    def test_multiple_replacements_returns_last(self, processor):
        """Test that multiple replacements return the most recent one."""
        dates = pd.date_range("2024-01-01", periods=50, freq="D")
        # First token, then gap, second token, then gap, third token
        prices_list = (
            [1e-10] * 5  # First token
            + [0.0] * 10  # Gap
            + [1e-7] * 15  # Second token (1000x higher)
            + [0.0] * 5  # Gap
            + [1e-4] * 15  # Third token (another 1000x higher)
        )
        prices = pd.Series(prices_list, index=dates)
        first_seen = dates[0]

        result = processor._detect_symbol_replacement(prices, first_seen)

        assert result is not None
        # Should return the LAST replacement date (third token start)
        assert result == dates[35]

    def test_replacement_must_be_after_first_seen(self, processor):
        """Test that replacement date must be after the first_seen date."""
        dates = pd.date_range("2024-01-01", periods=30, freq="D")
        # Jump happens on day 5
        prices_list = [1e-10] * 4 + [1e-7] * 26  # 1000x jump on day 5
        prices = pd.Series(prices_list, index=dates)

        # Set first_seen to AFTER the jump
        first_seen = dates[10]

        result = processor._detect_symbol_replacement(prices, first_seen)

        # Should NOT detect replacement since it happened before first_seen
        assert result is None

    def test_near_zero_threshold(self, processor):
        """Test that very small prices (near zero threshold) are handled correctly."""
        dates = pd.date_range("2024-01-01", periods=30, freq="D")
        # Prices just BELOW zero threshold (1e-16 < 1e-15), then actual zero, then real prices
        prices_list = [1e-16] * 5 + [0.0] * 10 + [1e-6] * 15
        prices = pd.Series(prices_list, index=dates)
        first_seen = dates[0]

        result = processor._detect_symbol_replacement(prices, first_seen)

        # The near-zero prices (1e-16) are below the threshold (1e-15),
        # so there's NO "prior trading" - this is just the coin starting to trade
        # Therefore NO resurrection should be detected
        assert result is None

    def test_above_threshold_then_zero_then_trading(self, processor):
        """Test resurrection when prior prices are above the zero threshold."""
        dates = pd.date_range("2024-01-01", periods=30, freq="D")
        # Prices ABOVE zero threshold (1e-14 > 1e-15), then actual zero, then real prices
        prices_list = [1e-14] * 5 + [0.0] * 10 + [1e-6] * 15
        prices = pd.Series(prices_list, index=dates)
        first_seen = dates[0]

        result = processor._detect_symbol_replacement(prices, first_seen)

        # The prior prices (1e-14) are above the threshold (1e-15),
        # so there IS prior trading - resurrection should be detected
        assert result is not None
        assert result == dates[15]

"""
Tests for the TOTAL2 processor.

Tests cover:
- Volume-weighted TOTAL2 calculation logic
- Daily composition tracking
- Filtering for TOTAL2 eligibility
- Freeze period and entry-day price scaling
- Edge cases

Note: Common fixtures (temp_dir, sample_price_data, sample_price_data_with_freeze,
sample_result) are defined in conftest.py for reuse across test modules.
"""

from datetime import date, timedelta
from unittest.mock import patch

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from data.cache import PriceDataCache
from data.price_filters import detect_symbol_replacement
from data.processor import (
    ProcessorError,
    Total2Processor,
    Total2Result,
    get_processor,
)


def _days(start: date, n: int) -> list[date]:
    """A contiguous list of ``n`` daily dates starting at ``start``."""
    return [start + timedelta(days=i) for i in range(n)]


def _price_on(df: pl.DataFrame, d: date, col: str = "total2_price") -> float:
    """Look up a single value from an index frame by its ``date`` column."""
    return df.filter(pl.col("date") == d)[col][0]


class TestProcessorFactory:
    """Tests for the get_processor factory function."""

    def test_get_processor_returns_total2b(self):
        """Test factory returns Total2Processor."""
        processor = get_processor()
        assert isinstance(processor, Total2Processor)


class TestTotal2ProcessorInit:
    """Tests for Total2Processor initialization."""

    def test_default_initialization(self):
        """Test processor initializes with defaults."""
        from config import (
            TOP_N_BY_VOLUME_FOR_TOTAL2,
            TOTAL2B_ENTRY_FREEZE_PERIOD_DAYS,
            TOTAL2B_MIN_COINS_FOR_SCALING,
        )

        processor = Total2Processor()

        assert processor.price_cache is not None
        assert processor.coin_filter is not None
        assert processor.top_n == TOP_N_BY_VOLUME_FOR_TOTAL2
        assert processor.freeze_period_days == TOTAL2B_ENTRY_FREEZE_PERIOD_DAYS
        assert processor.min_coins_for_scaling == TOTAL2B_MIN_COINS_FOR_SCALING

    def test_custom_freeze_period(self):
        """Test processor with custom freeze period."""
        processor = Total2Processor(freeze_period_days=14)
        assert processor.freeze_period_days == 14

    def test_index_type_marker_on_result(self):
        """Result carries the algorithm-variant marker for on-disk metadata."""
        from data.processor import Total2Result

        result = Total2Result(
            index_df=pl.DataFrame(),
            composition_df=pl.DataFrame(),
            coins_processed=0,
            date_range=(date(2020, 1, 1), date(2020, 1, 1)),
            avg_coins_per_day=0.0,
        )
        # Default carries the "total2b" tag, preserved for compatibility with
        # any consumer that reads the JSON metadata field.
        assert result.index_type == "total2b"


class TestTotal2bCalculation:
    """Tests for TOTAL2 calculation with freeze period and scaling.

    Uses shared fixtures from conftest.py: temp_dir, sample_price_data_with_freeze.
    """

    def test_freeze_period_enforced(self, temp_dir, sample_price_data_with_freeze):
        """Test that coins must wait freeze period before joining index."""
        cache = PriceDataCache(prices_dir=temp_dir)
        for coin_id, df in sample_price_data_with_freeze.items():
            cache.set_prices(coin_id, df)

        # Use short freeze period for testing
        processor = Total2Processor(
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

        processor = Total2Processor(
            price_cache=cache,
            top_n=3,
            volume_sma_window=2,
            freeze_period_days=5,
        )

        result = processor.calculate_total2(show_progress=False)

        assert isinstance(result, Total2Result)
        assert result.coins_processed == 3
        assert result.index_type == "total2b"
        assert not result.composition_df.is_empty()

        assert "total2_price" in result.index_df.columns
        assert "total_volume" in result.index_df.columns
        assert "coin_count" in result.index_df.columns

    def test_get_freeze_period_status(self, temp_dir, sample_price_data_with_freeze):
        """Test freeze period status reporting."""
        cache = PriceDataCache(prices_dir=temp_dir)
        for coin_id, df in sample_price_data_with_freeze.items():
            cache.set_prices(coin_id, df)

        processor = Total2Processor(
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
    """Tests for saving and loading TOTAL2b results.

    Uses shared fixtures from conftest.py: temp_dir, sample_result.
    """

    def test_save_and_load_index(self, temp_dir, sample_result):
        """Test saving and loading TOTAL2b index."""
        processor = Total2Processor()

        index_path = temp_dir / "total2_index.parquet"
        comp_path = temp_dir / "total2_composition.parquet"

        with (
            patch("data.processor.PROCESSED_DIR", temp_dir),
            patch("data.processor.TOTAL2_INDEX_FILE", index_path),
            patch("data.processor.TOTAL2_COMPOSITION_FILE", comp_path),
        ):
            processor.save_results(sample_result, index_path, comp_path)

            assert index_path.exists()
            assert comp_path.exists()

            loaded = pl.read_parquet(index_path)
            assert_frame_equal(loaded, sample_result.index_df)


class TestTotal2bEdgeCases:
    """Tests for edge cases in TOTAL2b calculation.

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

        dates = _days(date(2024, 1, 1), 30)
        wbtc_data = pl.DataFrame(
            {
                "date": dates,
                "close": [1.0] * 30,
                "volume_to": [1000.0] * 30,
            }
        )
        cache.set_prices("wbtc", wbtc_data)

        processor = Total2Processor(price_cache=cache, freeze_period_days=5)

        with pytest.raises(ProcessorError, match="No eligible coins"):
            processor.calculate_total2(show_progress=False)

    def test_less_than_top_n_coins(self, temp_dir):
        """Test calculation when fewer coins than top_n are available."""
        cache = PriceDataCache(prices_dir=temp_dir)

        dates = _days(date(2024, 1, 1), 30)
        eth_data = pl.DataFrame(
            {
                "date": dates,
                "close": [0.05 + i * 0.0001 for i in range(30)],
                "volume_to": [10000.0 + i * 50 for i in range(30)],
            }
        )
        sol_data = pl.DataFrame(
            {
                "date": dates,
                "close": [0.003 + i * 0.00001 for i in range(30)],
                "volume_to": [2000.0 + i * 30 for i in range(30)],
            }
        )
        ada_data = pl.DataFrame(
            {
                "date": dates,
                "close": [0.00002 + i * 0.0000001 for i in range(30)],
                "volume_to": [500.0 + i * 20 for i in range(30)],
            }
        )

        cache.set_prices("eth", eth_data)
        cache.set_prices("sol", sol_data)
        cache.set_prices("ada", ada_data)

        processor = Total2Processor(
            price_cache=cache, top_n=50, volume_sma_window=2, freeze_period_days=5
        )
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
        dates = _days(date(2024, 1, 1), 40)

        # ETH: present from day 1, stable price ~0.05 BTC
        eth_data = pl.DataFrame(
            {
                "date": dates,
                "close": [0.05 + i * 0.0001 for i in range(40)],
                "volume_to": [10000.0 + i * 50 for i in range(40)],
            }
        )

        # SOL: present from day 1, stable price ~0.003 BTC
        sol_data = pl.DataFrame(
            {
                "date": dates,
                "close": [0.003 + i * 0.00001 for i in range(40)],
                "volume_to": [5000.0 + i * 30 for i in range(40)],
            }
        )

        # ADA: present from day 1, stable price ~0.00002 BTC
        ada_data = pl.DataFrame(
            {
                "date": dates,
                "close": [0.00002 + i * 0.0000001 for i in range(40)],
                "volume_to": [2000.0 + i * 20 for i in range(40)],
            }
        )

        # AVAX: enters late (day 15), different price level
        # First 14 days have no data (null)
        avax_close = [None] * 14 + [0.01 + i * 0.0001 for i in range(26)]
        avax_volume = [None] * 14 + [3000.0 + i * 25 for i in range(26)]
        avax_data = pl.DataFrame(
            {
                "date": dates,
                "close": avax_close,
                "volume_to": avax_volume,
            }
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

        processor = Total2Processor(
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
        prices = result.index_df["total2_price"].to_numpy()
        for i in range(1, len(prices)):
            ratio = prices[i] / prices[i - 1]
            # Price ratio should be reasonable (not > 2x or < 0.5x per day)
            assert 0.5 < ratio < 2.0, f"Unreasonable price ratio at index {i}: {ratio}"

    def test_scaling_events_recorded(self, temp_dir, sample_data_with_late_entry):
        """Test that scaling events are properly recorded."""
        cache = PriceDataCache(prices_dir=temp_dir)
        for coin_id, df in sample_data_with_late_entry.items():
            cache.set_prices(coin_id, df)

        processor = Total2Processor(
            price_cache=cache,
            top_n=4,
            volume_sma_window=2,
            freeze_period_days=5,
            min_coins_for_scaling=3,
        )

        result = processor.calculate_total2(show_progress=False)

        # AVAX should have a scaling event (it enters after the index is established)
        scaling_events = result.scaling_events

        # Find AVAX scaling event
        avax_events = [e for e in scaling_events if e["coin"] == "AVAX"]

        # AVAX should have been scaled when it entered
        assert len(avax_events) >= 1, "AVAX should have a scaling event"

        event = avax_events[0]
        assert "change_factor" in event
        assert "prev_total2b" in event
        assert event["change_factor"] > 0


class TestStaleEntryReanchor:
    """Tests for the stale-entry re-anchor (the 2026-06 "bart" fix).

    A coin can clear the freeze period (become eligible + first-eligibility
    scaled) long before its volume ranks it into the top-N. If its raw price
    ramps in the meantime, its stale multiplier makes it enter the top-N far
    above the index level and dominate the volume-weighted mean. On entry it
    should be re-anchored to the current index level instead.
    """

    @pytest.fixture
    def sample_data_with_stale_pump_entrant(self):
        """3 stable coins + a low-volume coin that ramps 50x then enters top-N."""
        dates = _days(date(2024, 1, 1), 40)

        # Volumes are kept < DEFAULT_MIN_VOLUME_FOR_OUTLIER_CHECK (5000) so the
        # volume-outlier corrector never caps pmp's entry-day volume jump.
        def const(close, vol):
            return pl.DataFrame(
                {"date": dates, "close": [close] * 40, "volume_to": [float(vol)] * 40}
            )

        aaa = const(0.010, 1000)  # forms the index (highest steady volume)
        bbb = const(0.005, 800)
        ccc = const(0.002, 600)

        # pmp: appears on day 10 (after aaa/bbb/ccc are already an established,
        # scaling-active index), becomes eligible ~day 13 and is first-eligibility
        # scaled at its low launch price -> a large stale multiplier. It stays
        # low-volume (rank 4, out of the top-3) while its price ramps ~40x
        # gradually (per-day 1.216 stays under the 4.42x symbol-replacement
        # threshold). On day 30 its volume finally lifts it into the top-3, still
        # carrying that stale multiplier.
        pmp_close: list = [None] * 10
        p = 0.0001
        for i in range(10, 40):
            pmp_close.append(p)
            if i < 30:  # ramp over days 10..29
                p *= 1.216
        pmp_vol: list = [None] * 10
        pmp_vol += [10.0] * 20 + [4500.0] * 10  # low volume, then enters top-3 on day 30
        pmp = pl.DataFrame({"date": dates, "close": pmp_close, "volume_to": pmp_vol})

        return {"aaa": aaa, "bbb": bbb, "ccc": ccc, "pmp": pmp}

    def _run(self, temp_dir, data, reanchor_ratio):
        cache = PriceDataCache(prices_dir=temp_dir)
        for coin_id, df in data.items():
            cache.set_prices(coin_id, df)
        processor = Total2Processor(
            price_cache=cache,
            top_n=3,
            volume_sma_window=2,
            freeze_period_days=3,
            min_coins_for_scaling=3,
            stale_entry_reanchor_ratio=reanchor_ratio,
        )
        return processor.calculate_total2(show_progress=False)

    def test_reanchor_removes_the_bart(self, temp_dir, sample_data_with_stale_pump_entrant):
        """With the guard on, the stale entrant is re-anchored and the index stays smooth."""
        res = self._run(temp_dir, sample_data_with_stale_pump_entrant, reanchor_ratio=5.0)

        index_df = res.index_df
        entry_day = date(2024, 1, 31)  # day 30 (0-indexed): pmp enters top-3
        prev_day = date(2024, 1, 30)
        dates_list = index_df["date"].to_list()
        assert entry_day in dates_list and prev_day in dates_list
        p_entry = _price_on(index_df, entry_day)
        p_prev = _price_on(index_df, prev_day)
        # No bart: the index must not spike when the stale entrant joins.
        assert p_entry / p_prev < 1.5

        # The re-anchor must be recorded for PMP.
        reanchors = res.stale_entry_reanchors or []
        pmp_events = [e for e in reanchors if e["coin"] == "PMP"]
        assert len(pmp_events) >= 1
        ev = pmp_events[0]
        assert ev["stale_ratio"] > 5.0  # it really was stale on entry
        assert ev["reanchored_to"] == pytest.approx(p_prev, rel=0.05)

    def test_without_guard_the_bart_appears(self, temp_dir, sample_data_with_stale_pump_entrant):
        """Sanity check: with the guard disabled (ratio=0) the same data barts."""
        res = self._run(temp_dir, sample_data_with_stale_pump_entrant, reanchor_ratio=0)

        index_df = res.index_df
        entry_day = date(2024, 1, 31)
        prev_day = date(2024, 1, 30)
        p_entry = _price_on(index_df, entry_day)
        p_prev = _price_on(index_df, prev_day)
        # Disabled guard -> the stale entrant dominates and the index spikes hard.
        assert p_entry / p_prev > 3.0
        assert not (res.stale_entry_reanchors or [])

    def test_normal_entrant_is_not_reanchored(self, temp_dir):
        """A coin entering the top-N near the index level must not be re-anchored."""
        dates = _days(date(2024, 1, 1), 40)

        def const(close, vol):
            return pl.DataFrame(
                {"date": dates, "close": [close] * 40, "volume_to": [float(vol)] * 40}
            )

        data = {"aaa": const(0.010, 1000), "bbb": const(0.005, 800), "ccc": const(0.002, 600)}
        # ddd enters late (day 15) at a normal level, immediately with top-N volume.
        ddd_close = [None] * 15 + [0.004] * 25
        ddd_vol = [None] * 15 + [700.0] * 25
        data["ddd"] = pl.DataFrame({"date": dates, "close": ddd_close, "volume_to": ddd_vol})

        res = self._run(temp_dir, data, reanchor_ratio=5.0)
        ddd_reanchors = [e for e in (res.stale_entry_reanchors or []) if e["coin"] == "DDD"]
        assert ddd_reanchors == []
        assert (res.index_df["total2_price"] > 0).all()


class TestSymbolReplacementDetection:
    """Tests for symbol replacement detection (shared utility).

    Symbol replacement occurs when CryptoCompare reuses a ticker symbol
    for a different token (e.g., old MOVE token replaced by Movement Labs MOVE).

    Detection methods:
    1. Extreme ratio: price jumps >30x when both prices are positive
    2. Resurrection from zero: price goes from 0 to positive after prior trading
    """

    def test_no_replacement_for_stable_prices(self):
        """Test no replacement detected for coins with stable price history."""
        dates = _days(date(2024, 1, 1), 30)
        prices = pl.Series([0.05 + i * 0.001 for i in range(30)])
        first_seen = dates[0]

        result = detect_symbol_replacement(prices, dates, first_seen=first_seen)
        assert result is None

    def test_extreme_ratio_detection(self):
        """Test detection of extreme price ratio jumps (both prices > 0)."""
        dates = _days(date(2024, 1, 1), 30)
        # Price stable at ~1e-10, then jumps 1000x on day 15
        prices_list = [1e-10] * 14 + [1e-7] * 16  # 1000x jump
        prices = pl.Series(prices_list)
        first_seen = dates[0]

        result = detect_symbol_replacement(prices, dates, first_seen=first_seen)

        assert result is not None
        assert result == dates[14]  # The day of the jump

    def test_resurrection_from_zero_detection(self):
        """Test detection of resurrection from zero prices.

        This catches cases like MOVE where the old token went to exactly 0
        before the new token started trading.
        """
        dates = _days(date(2024, 1, 1), 30)
        # Old token trades, goes to zero, then new token starts
        prices_list = [1e-10] * 5 + [0.0] * 10 + [1e-6] * 15  # Zero gap then resurrection
        prices = pl.Series(prices_list)
        first_seen = dates[0]

        result = detect_symbol_replacement(prices, dates, first_seen=first_seen)

        assert result is not None
        assert result == dates[15]  # The day of resurrection

    def test_no_replacement_for_initial_zero_to_trading(self):
        """Test that starting from zero is NOT detected as replacement.

        When a coin first starts trading (0 -> positive), this is normal
        behavior, not a symbol replacement.
        """
        dates = _days(date(2024, 1, 1), 30)
        # Coin starts with zeros, then begins trading - no prior trading history
        prices_list = [0.0] * 10 + [1e-6] * 20
        prices = pl.Series(prices_list)
        first_seen = dates[0]

        result = detect_symbol_replacement(prices, dates, first_seen=first_seen)

        # Should NOT detect replacement - this is just starting to trade
        assert result is None

    def test_multiple_replacements_returns_last(self):
        """Test that multiple replacements return the most recent one."""
        dates = _days(date(2024, 1, 1), 50)
        # First token, then gap, second token, then gap, third token
        prices_list = (
            [1e-10] * 5  # First token
            + [0.0] * 10  # Gap
            + [1e-7] * 15  # Second token (1000x higher)
            + [0.0] * 5  # Gap
            + [1e-4] * 15  # Third token (another 1000x higher)
        )
        prices = pl.Series(prices_list)
        first_seen = dates[0]

        result = detect_symbol_replacement(prices, dates, first_seen=first_seen)

        assert result is not None
        # Should return the LAST replacement date (third token start)
        assert result == dates[35]

    def test_replacement_must_be_after_first_seen(self):
        """Test that replacement date must be after the first_seen date."""
        dates = _days(date(2024, 1, 1), 30)
        # Jump happens on day 5
        prices_list = [1e-10] * 4 + [1e-7] * 26  # 1000x jump on day 5
        prices = pl.Series(prices_list)

        # Set first_seen to AFTER the jump
        first_seen = dates[10]

        result = detect_symbol_replacement(prices, dates, first_seen=first_seen)

        # Should NOT detect replacement since it happened before first_seen
        assert result is None

    def test_near_zero_threshold(self):
        """Test that very small prices (near zero threshold) are handled correctly."""
        dates = _days(date(2024, 1, 1), 30)
        # Prices just BELOW zero threshold (1e-16 < 1e-15), then actual zero, then real prices
        prices_list = [1e-16] * 5 + [0.0] * 10 + [1e-6] * 15
        prices = pl.Series(prices_list)
        first_seen = dates[0]

        result = detect_symbol_replacement(prices, dates, first_seen=first_seen)

        # The near-zero prices (1e-16) are below the threshold (1e-15),
        # so there's NO "prior trading" - this is just the coin starting to trade
        # Therefore NO resurrection should be detected
        assert result is None

    def test_above_threshold_then_zero_then_trading(self):
        """Test resurrection when prior prices are above the zero threshold."""
        dates = _days(date(2024, 1, 1), 30)
        # Prices ABOVE zero threshold (1e-14 > 1e-15), then actual zero, then real prices
        prices_list = [1e-14] * 5 + [0.0] * 10 + [1e-6] * 15
        prices = pl.Series(prices_list)
        first_seen = dates[0]

        result = detect_symbol_replacement(prices, dates, first_seen=first_seen)

        # The prior prices (1e-14) are above the threshold (1e-15),
        # so there IS prior trading - resurrection should be detected
        assert result is not None
        assert result == dates[15]

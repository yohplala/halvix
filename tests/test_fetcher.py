"""
Tests for data fetcher orchestration.

Tests cover:
- Fetching and filtering coins
- Price data fetching
- Integration with cache and filter
"""

import json
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from api.cryptocompare import Coin, CryptoCompareClient, CryptoCompareError
from data.cache import FileCache, PriceDataCache
from data.fetcher import DataFetcher, FetchResult


class TestFetchResult:
    """Tests for FetchResult dataclass."""

    def test_success_result(self):
        """Test creating a success result."""
        result = FetchResult(
            success=True,
            message="Success",
            coins_fetched=100,
            coins_filtered=20,
            coins_accepted=80,
        )

        assert result.success is True
        assert result.coins_fetched == 100
        assert result.coins_filtered == 20
        assert result.coins_accepted == 80

    def test_failure_result(self):
        """Test creating a failure result."""
        result = FetchResult(
            success=False,
            message="API error",
            errors=["Connection timeout"],
        )

        assert result.success is False
        assert result.errors is not None
        assert len(result.errors) == 1


class TestDataFetcherInit:
    """Tests for DataFetcher initialization."""

    def test_default_initialization(self):
        """Test fetcher initializes with defaults."""
        fetcher = DataFetcher()

        assert fetcher.client is not None
        assert fetcher.cache is not None
        assert fetcher.price_cache is not None
        assert fetcher.coin_filter is not None

    def test_custom_dependencies(self):
        """Test fetcher with custom dependencies."""
        mock_client = MagicMock(spec=CryptoCompareClient)
        mock_cache = MagicMock(spec=FileCache)

        fetcher = DataFetcher(client=mock_client, cache=mock_cache)

        assert fetcher.client is mock_client
        assert fetcher.cache is mock_cache


class TestDataFetcherTopCoins:
    """Tests for fetching top coins."""

    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories for cache."""
        with (
            tempfile.TemporaryDirectory() as cache_dir,
            tempfile.TemporaryDirectory() as prices_dir,
        ):
            yield Path(cache_dir), Path(prices_dir)

    @pytest.fixture
    def fetcher(self, temp_dirs):
        """Create a DataFetcher with temp directories."""
        cache_dir, prices_dir = temp_dirs

        mock_client = MagicMock(spec=CryptoCompareClient)
        cache = FileCache(cache_dir=cache_dir, expiry_seconds=3600)
        price_cache = PriceDataCache(prices_dir=prices_dir)

        fetcher = DataFetcher(
            client=mock_client,
            cache=cache,
            price_cache=price_cache,
        )
        return fetcher

    @pytest.fixture
    def sample_coins(self):
        """Sample coin list."""
        return [
            Coin(
                symbol="BTC",
                name="Bitcoin",
                market_cap=1e12,
                market_cap_rank=1,
                current_price=1.0,
                volume_24h=50000,
                circulating_supply=19e6,
            ),
            Coin(
                symbol="ETH",
                name="Ethereum",
                market_cap=400e9,
                market_cap_rank=2,
                current_price=0.05,
                volume_24h=30000,
                circulating_supply=120e6,
            ),
            Coin(
                symbol="WBTC",
                name="Wrapped Bitcoin",
                market_cap=10e9,
                market_cap_rank=15,
                current_price=0.99,
                volume_24h=5000,
                circulating_supply=150000,
            ),
            Coin(
                symbol="SOL",
                name="Solana",
                market_cap=80e9,
                market_cap_rank=5,
                current_price=0.003,
                volume_24h=10000,
                circulating_supply=400e6,
            ),
            Coin(
                symbol="USDT",
                name="Tether",
                market_cap=100e9,
                market_cap_rank=3,
                current_price=0.00001,
                volume_24h=80000,
                circulating_supply=100e9,
            ),
        ]

    def test_fetch_top_coins_returns_list(self, fetcher, sample_coins):
        """Test fetching top coins returns a list."""
        fetcher.client.get_top_coins_by_market_cap.return_value = sample_coins

        result = fetcher.fetch_top_coins(n=5, use_cache=False)

        assert isinstance(result, list)
        assert len(result) == 5
        assert result[0]["id"] == "btc"

    def test_fetch_top_coins_uses_cache(self, fetcher, sample_coins):
        """Test that cached data is used."""
        fetcher.client.get_top_coins_by_market_cap.return_value = sample_coins

        # First call - should hit API
        result1 = fetcher.fetch_top_coins(n=5, use_cache=True)

        # Second call - should use cache
        result2 = fetcher.fetch_top_coins(n=5, use_cache=True)

        # API should only be called once
        assert fetcher.client.get_top_coins_by_market_cap.call_count == 1
        assert result1 == result2

    def test_fetch_top_coins_bypasses_cache(self, fetcher, sample_coins):
        """Test that cache can be bypassed."""
        fetcher.client.get_top_coins_by_market_cap.return_value = sample_coins

        fetcher.fetch_top_coins(n=5, use_cache=False)
        fetcher.fetch_top_coins(n=5, use_cache=False)

        # API should be called twice
        assert fetcher.client.get_top_coins_by_market_cap.call_count == 2


class TestDataFetcherFilterCoins:
    """Tests for fetching and filtering coins."""

    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories."""
        with (
            tempfile.TemporaryDirectory() as cache_dir,
            tempfile.TemporaryDirectory() as prices_dir,
            tempfile.TemporaryDirectory() as processed_dir,
        ):
            yield Path(cache_dir), Path(prices_dir), Path(processed_dir)

    @pytest.fixture
    def sample_coins(self):
        """Sample coins with a mix of types."""
        return [
            Coin(
                symbol="BTC",
                name="Bitcoin",
                market_cap=1e12,
                market_cap_rank=1,
                current_price=1.0,
                volume_24h=50000,
                circulating_supply=19e6,
            ),
            Coin(
                symbol="ETH",
                name="Ethereum",
                market_cap=400e9,
                market_cap_rank=2,
                current_price=0.05,
                volume_24h=30000,
                circulating_supply=120e6,
            ),
            Coin(
                symbol="WBTC",
                name="Wrapped Bitcoin",
                market_cap=10e9,
                market_cap_rank=15,
                current_price=0.99,
                volume_24h=5000,
                circulating_supply=150000,
            ),
            Coin(
                symbol="STETH",
                name="Lido Staked Ether",
                market_cap=20e9,
                market_cap_rank=10,
                current_price=0.049,
                volume_24h=3000,
                circulating_supply=10e6,
            ),
            Coin(
                symbol="SOL",
                name="Solana",
                market_cap=80e9,
                market_cap_rank=5,
                current_price=0.003,
                volume_24h=10000,
                circulating_supply=400e6,
            ),
            Coin(
                symbol="SUI",
                name="Sui",
                market_cap=5e9,
                market_cap_rank=20,
                current_price=0.00005,
                volume_24h=2000,
                circulating_supply=10e9,
            ),
            Coin(
                symbol="USDT",
                name="Tether",
                market_cap=100e9,
                market_cap_rank=3,
                current_price=0.00001,
                volume_24h=80000,
                circulating_supply=100e9,
            ),
        ]

    def test_fetch_and_filter_excludes_wrapped_and_stablecoins(self, temp_dirs, sample_coins):
        """Test that wrapped/staked tokens and stablecoins are filtered."""
        cache_dir, prices_dir, processed_dir = temp_dirs

        mock_client = MagicMock(spec=CryptoCompareClient)
        # Return tuple (coins, coins_without_data) when track_no_data=True
        mock_client.get_top_coins_by_market_cap.return_value = (sample_coins, [])

        cache = FileCache(cache_dir=cache_dir)
        price_cache = PriceDataCache(prices_dir=prices_dir)

        fetcher = DataFetcher(
            client=mock_client,
            cache=cache,
            price_cache=price_cache,
        )

        # Patch the output paths and NO_USD_DATA_CSV
        with (
            patch("data.fetcher.COINS_TO_DOWNLOAD_JSON", processed_dir / "accepted.json"),
            patch("data.fetcher.PROCESSED_DIR", processed_dir),
            patch("data.fetcher.NO_USD_DATA_CSV", processed_dir / "no_usd_data.csv"),
        ):
            result = fetcher.fetch_and_filter_coins(
                n=7,
                use_cache=False,
                export_skipped=False,
            )

        assert result.success is True
        assert result.coins_fetched == 7
        assert result.coins_no_usd_data == 0
        # Should filter: BTC, WBTC, STETH, USDT
        # Accept: ETH, SOL, SUI
        summary = fetcher.get_filter_summary()
        reasons = summary["by_reason"]
        assert "Wrapped/Staked/Bridged token" in reasons
        assert "Stablecoin" in reasons

    def test_fetch_and_filter_handles_api_error(self, temp_dirs):
        """Test error handling for API failures."""
        cache_dir, prices_dir, _ = temp_dirs

        mock_client = MagicMock(spec=CryptoCompareClient)
        mock_client.get_top_coins_by_market_cap.side_effect = CryptoCompareError("API down")

        fetcher = DataFetcher(
            client=mock_client,
            cache=FileCache(cache_dir=cache_dir),
            price_cache=PriceDataCache(prices_dir=prices_dir),
        )

        result = fetcher.fetch_and_filter_coins(n=10, use_cache=False)

        assert result.success is False
        assert "API" in result.message


class TestDataFetcherPrices:
    """Tests for price data fetching."""

    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories."""
        with (
            tempfile.TemporaryDirectory() as cache_dir,
            tempfile.TemporaryDirectory() as prices_dir,
        ):
            yield Path(cache_dir), Path(prices_dir)

    @pytest.fixture
    def sample_price_df(self):
        """Sample price DataFrame as returned by CryptoCompare."""
        # Use dates ending at yesterday so cache is considered "up to date"
        # This prevents incremental fetching from triggering additional API calls
        yesterday = date.today() - timedelta(days=1)
        dates = [yesterday - timedelta(days=i) for i in range(2, -1, -1)]
        return pl.DataFrame(
            {
                "date": dates,
                "close": [1.0, 1.1, 1.2],
                "open": [0.9, 1.0, 1.1],
                "high": [1.1, 1.2, 1.3],
                "low": [0.8, 0.9, 1.0],
                "volume_from": [50000, 55000, 60000],
                "volume_to": [1000, 1100, 1200],
            }
        )

    def test_fetch_coin_prices(self, temp_dirs, sample_price_df):
        """Test fetching prices for a single coin using CryptoCompare."""
        cache_dir, prices_dir = temp_dirs

        mock_client = MagicMock(spec=CryptoCompareClient)
        mock_client.get_full_daily_history.return_value = sample_price_df

        fetcher = DataFetcher(
            client=mock_client,
            cache=FileCache(cache_dir=cache_dir),
            price_cache=PriceDataCache(prices_dir=prices_dir),
        )

        # Use ETH instead of BTC (BTC-BTC pair is skipped as nonsensical)
        df = fetcher.fetch_coin_prices("eth", symbol="ETH", use_cache=False)

        assert not df.is_empty()
        assert "close" in df.columns
        assert len(df) == 3

    def test_fetch_coin_prices_uses_cache(self, temp_dirs, sample_price_df):
        """Test that price cache is used."""
        cache_dir, prices_dir = temp_dirs

        mock_client = MagicMock(spec=CryptoCompareClient)
        mock_client.get_full_daily_history.return_value = sample_price_df

        fetcher = DataFetcher(
            client=mock_client,
            cache=FileCache(cache_dir=cache_dir),
            price_cache=PriceDataCache(prices_dir=prices_dir),
        )

        # Use ETH instead of BTC (BTC-BTC pair is skipped as nonsensical)
        # First call - hits API
        df1 = fetcher.fetch_coin_prices("eth", symbol="ETH", use_cache=True)

        # Second call - uses cache
        df2 = fetcher.fetch_coin_prices("eth", symbol="ETH", use_cache=True)

        # API should only be called once
        assert mock_client.get_full_daily_history.call_count == 1
        assert_frame_equal(df1, df2)


class TestDataFetcherGetFilterSummary:
    """Tests for filter summary."""

    def test_get_filter_summary_structure(self):
        """Test filter summary structure."""
        fetcher = DataFetcher()

        summary = fetcher.get_filter_summary()

        assert "skipped_count" in summary
        assert "by_reason" in summary
        assert "skipped_coins" in summary
        assert isinstance(summary["skipped_count"], int)
        assert isinstance(summary["by_reason"], dict)
        assert isinstance(summary["skipped_coins"], list)


class TestDetectSymbolReplacementsByName:
    """Tests for name-based symbol replacement detection."""

    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories."""
        with (
            tempfile.TemporaryDirectory() as cache_dir,
            tempfile.TemporaryDirectory() as prices_dir,
            tempfile.TemporaryDirectory() as processed_dir,
        ):
            yield Path(cache_dir), Path(prices_dir), Path(processed_dir)

    @pytest.fixture
    def fetcher(self, temp_dirs):
        """Create a DataFetcher with temp directories."""
        cache_dir, prices_dir, _ = temp_dirs
        mock_client = MagicMock(spec=CryptoCompareClient)
        cache = FileCache(cache_dir=cache_dir)
        price_cache = PriceDataCache(prices_dir=prices_dir)
        return DataFetcher(client=mock_client, cache=cache, price_cache=price_cache)

    def _write_old_coins(self, path, coins):
        """Write a coins_to_download.json file."""
        import json

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(coins, f)

    def _create_price_file(self, prices_dir, coin_id, vs_currency="btc"):
        """Create a dummy parquet price file."""
        dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(5)]
        df = pl.DataFrame(
            {"date": dates, "close": [1.0, 1.1, 1.2, 1.3, 1.4]},
        )
        prices_dir.mkdir(parents=True, exist_ok=True)
        filepath = prices_dir / f"{coin_id}-{vs_currency}.parquet"
        df.write_parquet(filepath)
        return filepath

    def test_no_previous_metadata(self, fetcher, temp_dirs):
        """No existing coins_to_download.json → no detections."""
        _, _, processed_dir = temp_dirs
        coins_path = processed_dir / "coins_to_download.json"

        new_coins = [{"id": "lit", "name": "Lighter"}]

        with patch("data.fetcher.COINS_TO_DOWNLOAD_JSON", coins_path):
            result = fetcher._detect_symbol_replacements_by_name(new_coins)

        assert result == []

    def test_name_unchanged(self, fetcher, temp_dirs):
        """Same name → no deletion."""
        _, prices_dir, processed_dir = temp_dirs
        coins_path = processed_dir / "coins_to_download.json"

        old_coins = [{"id": "eth", "name": "Ethereum"}]
        self._write_old_coins(coins_path, old_coins)
        price_file = self._create_price_file(prices_dir, "eth")

        new_coins = [{"id": "eth", "name": "Ethereum"}]

        with patch("data.fetcher.COINS_TO_DOWNLOAD_JSON", coins_path):
            result = fetcher._detect_symbol_replacements_by_name(new_coins)

        assert result == []
        assert price_file.exists()

    def test_name_changed_deletes_price_data(self, fetcher, temp_dirs):
        """Name changed → price files deleted, replacement returned."""
        _, prices_dir, processed_dir = temp_dirs
        coins_path = processed_dir / "coins_to_download.json"

        old_coins = [{"id": "lit", "name": "Litentry"}]
        self._write_old_coins(coins_path, old_coins)
        btc_file = self._create_price_file(prices_dir, "lit", "btc")
        usd_file = self._create_price_file(prices_dir, "lit", "usd")

        new_coins = [{"id": "lit", "name": "Lighter"}]

        with patch("data.fetcher.COINS_TO_DOWNLOAD_JSON", coins_path):
            result = fetcher._detect_symbol_replacements_by_name(new_coins)

        assert len(result) == 1
        assert result[0]["id"] == "lit"
        assert result[0]["old_name"] == "Litentry"
        assert result[0]["new_name"] == "Lighter"
        assert not btc_file.exists()
        assert not usd_file.exists()

    def test_new_coin_not_in_old_list(self, fetcher, temp_dirs):
        """New coin ID not in old metadata → no action."""
        _, _, processed_dir = temp_dirs
        coins_path = processed_dir / "coins_to_download.json"

        old_coins = [{"id": "eth", "name": "Ethereum"}]
        self._write_old_coins(coins_path, old_coins)

        new_coins = [
            {"id": "eth", "name": "Ethereum"},
            {"id": "lit", "name": "Lighter"},
        ]

        with patch("data.fetcher.COINS_TO_DOWNLOAD_JSON", coins_path):
            result = fetcher._detect_symbol_replacements_by_name(new_coins)

        assert result == []

    def test_coin_removed_from_new_list(self, fetcher, temp_dirs):
        """Coin in old list but not in new → no action (data preserved)."""
        _, prices_dir, processed_dir = temp_dirs
        coins_path = processed_dir / "coins_to_download.json"

        old_coins = [{"id": "lit", "name": "Litentry"}, {"id": "eth", "name": "Ethereum"}]
        self._write_old_coins(coins_path, old_coins)
        price_file = self._create_price_file(prices_dir, "lit")

        new_coins = [{"id": "eth", "name": "Ethereum"}]

        with patch("data.fetcher.COINS_TO_DOWNLOAD_JSON", coins_path):
            result = fetcher._detect_symbol_replacements_by_name(new_coins)

        assert result == []
        assert price_file.exists()

    def test_corrupted_old_metadata(self, fetcher, temp_dirs):
        """Corrupted JSON → graceful fallback, no crash."""
        _, _, processed_dir = temp_dirs
        coins_path = processed_dir / "coins_to_download.json"

        coins_path.parent.mkdir(parents=True, exist_ok=True)
        coins_path.write_text("not valid json{{{")

        new_coins = [{"id": "lit", "name": "Lighter"}]

        with patch("data.fetcher.COINS_TO_DOWNLOAD_JSON", coins_path):
            result = fetcher._detect_symbol_replacements_by_name(new_coins)

        assert result == []

    def test_bulk_rename_preserves_cache(self, fetcher, temp_dirs):
        """A wholesale re-label (e.g. provider switch) must NOT delete history."""
        _, prices_dir, processed_dir = temp_dirs
        coins_path = processed_dir / "coins_to_download.json"

        # 30 coins, all with a different name in the new list (> the bulk threshold)
        old_coins = [{"id": f"c{i}", "name": f"Old Name {i}"} for i in range(30)]
        new_coins = [{"id": f"c{i}", "name": f"New Name {i}"} for i in range(30)]
        self._write_old_coins(coins_path, old_coins)
        price_files = [self._create_price_file(prices_dir, f"c{i}") for i in range(30)]

        with patch("data.fetcher.COINS_TO_DOWNLOAD_JSON", coins_path):
            result = fetcher._detect_symbol_replacements_by_name(new_coins)

        assert result == []  # bulk guard skips deletion
        assert all(f.exists() for f in price_files)  # history preserved

    def test_few_renames_still_delete(self, fetcher, temp_dirs):
        """A handful of renames (below the guard) are still treated as real."""
        _, prices_dir, processed_dir = temp_dirs
        coins_path = processed_dir / "coins_to_download.json"

        # 30 coins, only 2 renamed → below threshold → real reassignments
        old_coins = [{"id": f"c{i}", "name": f"Name {i}"} for i in range(30)]
        new_coins = [{"id": f"c{i}", "name": f"Name {i}"} for i in range(30)]
        new_coins[0]["name"] = "Reassigned A"
        new_coins[1]["name"] = "Reassigned B"
        self._write_old_coins(coins_path, old_coins)
        kept = self._create_price_file(prices_dir, "c5")
        deleted = self._create_price_file(prices_dir, "c0")

        with patch("data.fetcher.COINS_TO_DOWNLOAD_JSON", coins_path):
            result = fetcher._detect_symbol_replacements_by_name(new_coins)

        assert {r["id"] for r in result} == {"c0", "c1"}
        assert not deleted.exists()  # renamed coin's cache deleted
        assert kept.exists()  # untouched coin preserved


class TestSpliceValidation:
    """Tests for the splice-time price-equivalence safeguard."""

    @pytest.fixture
    def fetcher(self):
        return DataFetcher(client=MagicMock(spec=CryptoCompareClient))

    @staticmethod
    def _df(start, closes):
        y, m, d = (int(p) for p in start.split("-"))
        dates = [date(y, m, d) + timedelta(days=i) for i in range(len(closes))]
        return pl.DataFrame({"date": dates, "close": closes})

    def test_same_asset_allows_splice(self, fetcher):
        # Same asset: provider tracks cached proportionally (~1x, tiny wobble).
        base = [1.0, 1.1, 1.05, 1.2, 1.15, 1.3, 1.25, 1.4]
        cached = self._df("2026-06-01", base)
        new = self._df("2026-06-01", [x * 1.01 for x in base])
        assert fetcher._splice_is_consistent("eth", "BTC", cached, new) is True
        assert fetcher.splice_mismatches == []

    def test_level_mismatch_blocks_splice(self, fetcher):
        cached = self._df("2026-06-01", [1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
        new = self._df("2026-06-01", [5.0, 5.0, 5.0, 5.0, 5.0, 5.0])  # 5x level
        assert fetcher._splice_is_consistent("foo", "BTC", cached, new) is False
        m = fetcher.splice_mismatches[0]
        assert m["id"] == "foo" and m["reason"] == "level"
        assert m["median_ratio"] == pytest.approx(5.0)

    def test_similar_level_but_untracked_blocks_splice(self, fetcher):
        # Different assets that momentarily share a price level (~1x) but whose
        # day-to-day moves are unrelated -> caught by the tracking signal.
        cached = self._df("2026-06-01", [1.0, 1.2, 0.9, 1.3, 0.8, 1.4, 1.0, 1.1])
        new = self._df("2026-06-01", [1.0, 0.8, 1.3, 0.9, 1.4, 0.85, 1.2, 0.95])
        assert fetcher._splice_is_consistent("syrup", "BTC", cached, new) is False
        assert fetcher.splice_mismatches[0]["reason"] == "tracking"

    def test_no_overlap_allows_splice(self, fetcher):
        cached = self._df("2026-06-01", [1.0, 1.0])  # ends 2026-06-02
        new = self._df("2026-06-05", [9.0, 9.0])  # no shared day
        assert fetcher._splice_is_consistent("eth", "BTC", cached, new) is True

    def test_incremental_skips_topup_on_mismatch(self):
        """A mismatched provider series must NOT be appended to history."""
        cached = self._df("2026-06-01", [1.0, 1.0, 1.0])  # cache to 2026-06-03
        topup = self._df("2026-06-03", [5.0, 5.1, 5.2])  # overlap 06-03 diverges 5x

        client = MagicMock(spec=CryptoCompareClient)
        client.get_full_daily_history.return_value = topup
        price_cache = MagicMock()
        price_cache.get_prices.return_value = cached
        fetcher = DataFetcher(client=client, price_cache=price_cache)
        # Pretend yesterday is well past the cache so a top-up is attempted.
        fetcher.history_end_date = date(2026, 6, 6)

        out = fetcher.fetch_coin_prices("foo", "FOO", "BTC", provider_id="foo-token")

        # Returned cache unchanged; nothing written; mismatch recorded.
        assert out["date"].max().isoformat() == "2026-06-03"
        price_cache.set_prices.assert_not_called()
        assert len(fetcher.splice_mismatches) == 1

    def test_incremental_skips_on_gap_or_no_overlap(self):
        """A truncated/non-contiguous provider window must not create a gap."""
        cached = self._df("2026-06-01", [1.0, 1.0, 1.0])  # cache to 2026-06-03
        # Provider returns only far-future days (no overlap, big gap) — e.g.
        # keyless CoinGecko truncation.
        topup = self._df("2026-06-20", [1.0, 1.0, 1.0])

        client = MagicMock(spec=CryptoCompareClient)
        client.get_full_daily_history.return_value = topup
        price_cache = MagicMock()
        price_cache.get_prices.return_value = cached
        fetcher = DataFetcher(client=client, price_cache=price_cache)
        fetcher.history_end_date = date(2026, 6, 25)

        out = fetcher.fetch_coin_prices("foo", "FOO", "BTC", provider_id="foo-token")

        assert out["date"].max().isoformat() == "2026-06-03"  # unchanged, no gap
        price_cache.set_prices.assert_not_called()
        assert fetcher.splice_mismatches[0]["reason"] in {"no_overlap", "gap"}

    def test_mismatch_summary_logs_without_error(self):
        """fetch_all_prices must summarise both mismatch shapes without crashing."""
        fetcher = DataFetcher(client=MagicMock(spec=CryptoCompareClient), price_cache=MagicMock())
        fetcher.splice_mismatches = [
            {  # price-equivalence failure shape
                "id": "foo",
                "vs_currency": "BTC",
                "reason": "level",
                "median_ratio": 5.0,
                "log_ratio_std": 0.1,
                "overlap_days": 30,
            },
            {  # contiguity failure shape
                "id": "bar",
                "vs_currency": "BTC",
                "reason": "no_overlap",
                "overlap_days": 0,
                "gap_days": 14,
            },
        ]
        # Empty coin list: exercises only the post-loop summary (the path that
        # previously raised KeyError on the new dict shape).
        fetcher.fetch_all_prices(coins=[], show_progress=True)


class _FakeProvider:
    """Minimal PriceProvider double returning canned per-symbol history."""

    name = "coingecko"

    def __init__(self, series_by_symbol: dict[str, pl.DataFrame]):
        self._series = series_by_symbol

    def get_full_daily_history(
        self,
        symbol: str,
        vs_currency: str = "BTC",
        start_date=None,
        end_date=None,
        show_progress: bool = False,
        provider_id: str | None = None,
    ) -> pl.DataFrame:
        df = self._series.get(symbol.upper())
        if df is None:
            return pl.DataFrame()
        out = df
        if start_date is not None:
            out = out.filter(pl.col("date") >= start_date)
        if end_date is not None:
            out = out.filter(pl.col("date") <= end_date)
        return out.clone()


class TestRegistryIntegration:
    """Cross-provider stem resolution wired through the fetch path."""

    @staticmethod
    def _series(start: str, closes: list[float]) -> pl.DataFrame:
        y, m, d = (int(p) for p in start.split("-"))
        dates = [date(y, m, d) + timedelta(days=i) for i in range(len(closes))]
        return pl.DataFrame({"date": dates, "close": closes, "volume_to": [100.0] * len(closes)})

    def _make(self, tmp_path, series_by_symbol, seed_files):
        """Build a fetcher over a tmp price dir + tmp registry, with seed parquets."""
        from data.coin_registry import CoinRegistry

        prices_dir = tmp_path / "prices"
        price_cache = PriceDataCache(prices_dir=prices_dir)
        for stem, df in seed_files.items():
            price_cache.set_prices(stem, df, "BTC")
        registry = CoinRegistry(path=tmp_path / "coin_registry.json")
        fetcher = DataFetcher(
            client=_FakeProvider(series_by_symbol),
            price_cache=price_cache,
            registry=registry,
        )
        fetcher.history_end_date = date(2026, 6, 25)
        return fetcher, price_cache, registry

    def test_same_asset_adopts_existing_stem(self, tmp_path):
        """A CoinGecko coin whose series matches cached history extends it in place."""
        ramp = [1.0 + 0.01 * i for i in range(40)]  # 2026-05-01 .. 2026-06-09
        cached = self._series("2026-05-01", ramp[:39])  # CryptoCompare era, ends 06-08
        full = self._series("2026-05-01", ramp + [1.4 + 0.01 * i for i in range(16)])  # to 06-25
        fetcher, price_cache, registry = self._make(tmp_path, {"ETH": full}, {"eth": cached})

        coin = {"id": "eth", "symbol": "ETH", "provider_id": "ethereum"}
        fetcher.fetch_all_prices(coins=[coin], vs_currencies=["BTC"], show_progress=False)

        updated = price_cache.get_prices("eth", "BTC")
        assert updated["date"].max() == date(2026, 6, 25)  # extended
        assert registry.get_stem("coingecko", "ethereum") == "eth"  # adopted, not forked
        assert not (tmp_path / "prices" / "eth-2-btc.parquet").exists()

    def test_symbol_collision_forks_to_new_stem(self, tmp_path):
        """A different asset sharing a symbol is stored separately as ``<sym>-2``."""
        cached = self._series("2026-05-01", [1.0] * 39)  # CryptoCompare BTCY, ends 06-08
        # CoinGecko BTCY is a different asset: 5x level over the overlap window.
        other = self._series("2026-05-09", [5.0] * 47)  # 05-09 .. 06-24-ish
        fetcher, price_cache, registry = self._make(tmp_path, {"BTCY": other}, {"btcy": cached})

        coin = {"id": "btcy", "symbol": "BTCY", "provider_id": "btc-yield"}
        fetcher.fetch_all_prices(coins=[coin], vs_currencies=["BTC"], show_progress=False)

        # Original CryptoCompare history untouched.
        original = price_cache.get_prices("btcy", "BTC")
        assert original["date"].max() == date(2026, 6, 8)
        assert float(original["close"][-1]) == 1.0
        # New asset stored under a forked stem and bound in the registry.
        forked = price_cache.get_prices("btcy-2", "BTC")
        assert forked is not None and float(forked["close"][-1]) == 5.0
        assert registry.get_stem("coingecko", "btc-yield") == "btcy-2"
        # Pre-migration file remains owned by CryptoCompare.
        assert registry.get_stem("cryptocompare", "BTCY") == "btcy"

    def test_known_identity_routes_directly(self, tmp_path):
        """An already-registered identity tops up its bound stem with no probe."""
        cached2 = self._series("2026-05-01", [5.0] * 39)  # btcy-2 history, ends 06-08
        full = self._series("2026-05-01", [5.0] * 55)  # extends to 06-24
        fetcher, price_cache, registry = self._make(tmp_path, {"BTCY": full}, {"btcy-2": cached2})
        registry.set_stem("coingecko", "btc-yield", "btcy-2")
        registry.save()

        coin = {"id": "btcy", "symbol": "BTCY", "provider_id": "btc-yield"}
        fetcher.fetch_all_prices(coins=[coin], vs_currencies=["BTC"], show_progress=False)

        updated = price_cache.get_prices("btcy-2", "BTC")
        assert updated["date"].max() == date(2026, 6, 24)
        assert not (tmp_path / "prices" / "btcy-btc.parquet").exists()  # bare stem untouched

    def test_bootstrap_seeds_cryptocompare_provenance(self, tmp_path):
        """Existing parquets are recorded as CryptoCompare-owned on first use."""
        cached = self._series("2026-06-01", [1.0, 1.0])
        fetcher, _price_cache, registry = self._make(tmp_path, {}, {"eth": cached, "sol": cached})

        fetcher._bootstrap_registry()

        assert registry.get_stem("cryptocompare", "ETH") == "eth"
        assert registry.get_stem("cryptocompare", "SOL") == "sol"

    def test_identity_seed_routes_rename_to_existing_stem(self, tmp_path):
        """A committed slug->stem seed continues history across a symbol rename."""
        ramp = [1.0 + 0.01 * i for i in range(40)]
        cached = self._series("2026-05-01", ramp[:39])  # 'mantle' history, ends 06-08
        full = self._series("2026-05-01", ramp + [1.4 + 0.01 * i for i in range(16)])
        fetcher, price_cache, registry = self._make(tmp_path, {"MNT": full}, {"mantle": cached})
        seed = tmp_path / "seed.json"
        seed.write_text(json.dumps({"coingecko": {"mantle": "mantle"}}))

        # CoinGecko now lists this asset under symbol MNT, slug 'mantle'.
        coin = {"id": "mnt", "symbol": "MNT", "provider_id": "mantle"}
        with patch("data.fetcher.COINGECKO_IDENTITY_SEED_JSON", seed):
            fetcher.fetch_all_prices(coins=[coin], vs_currencies=["BTC"], show_progress=False)

        updated = price_cache.get_prices("mantle", "BTC")
        assert updated["date"].max() == date(2026, 6, 25)  # mantle continued
        assert not (tmp_path / "prices" / "mnt-btc.parquet").exists()  # no fresh fork
        assert registry.get_stem("coingecko", "mantle") == "mantle"

    def test_registered_identity_bypasses_price_gate(self, tmp_path):
        """A registered binding appends even when prices diverge (micro-price case)."""
        cached = self._series("2026-05-01", [1.0] * 39)  # 'lunc' history, ends 06-08
        # Provider series diverges 5x over the overlap — would FORK if unregistered.
        full = self._series("2026-05-01", [5.0] * 56)  # spans through 2026-06-25
        fetcher, price_cache, registry = self._make(tmp_path, {"LUNC": full}, {"lunc": cached})
        registry.set_stem("coingecko", "terra-luna", "lunc")  # authoritative binding
        registry.save()

        coin = {"id": "lunc", "symbol": "LUNC", "provider_id": "terra-luna"}
        fetcher.fetch_all_prices(coins=[coin], vs_currencies=["BTC"], show_progress=False)

        updated = price_cache.get_prices("lunc", "BTC")
        assert updated["date"].max() == date(2026, 6, 25)  # appended, not skipped
        assert not (tmp_path / "prices" / "lunc-2-btc.parquet").exists()  # not forked
        assert fetcher.splice_mismatches == []

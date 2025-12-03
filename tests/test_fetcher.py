"""
Tests for data fetcher orchestration.

Tests cover:
- Fetching and filtering coins
- Price data fetching
- Integration with cache and filter
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
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
        assert fetcher.token_filter is not None

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
        with tempfile.TemporaryDirectory() as cache_dir, tempfile.TemporaryDirectory() as prices_dir:
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
        with tempfile.TemporaryDirectory() as cache_dir, tempfile.TemporaryDirectory() as prices_dir, tempfile.TemporaryDirectory() as processed_dir:
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

    def test_fetch_and_filter_excludes_wrapped(self, temp_dirs, sample_coins):
        """Test that wrapped/staked tokens are filtered."""
        cache_dir, prices_dir, processed_dir = temp_dirs

        mock_client = MagicMock(spec=CryptoCompareClient)
        mock_client.get_top_coins_by_market_cap.return_value = sample_coins

        cache = FileCache(cache_dir=cache_dir)
        price_cache = PriceDataCache(prices_dir=prices_dir)

        fetcher = DataFetcher(
            client=mock_client,
            cache=cache,
            price_cache=price_cache,
        )

        # Patch the output paths
        with patch("data.fetcher.ACCEPTED_COINS_JSON", processed_dir / "accepted.json"), patch(
            "data.fetcher.PROCESSED_DIR", processed_dir
        ):
            result = fetcher.fetch_and_filter_coins(
                n=7,
                for_total2=False,
                use_cache=False,
                export_filtered=False,
            )

        assert result.success is True
        assert result.coins_fetched == 7
        # Should filter: BTC, WBTC, STETH
        # Accept: ETH, SOL, SUI, USDT (stablecoin kept when not for_total2)
        assert result.coins_filtered >= 2  # At least wrapped and staked

    def test_fetch_and_filter_excludes_stablecoins_for_total2(self, temp_dirs, sample_coins):
        """Test that stablecoins are filtered for TOTAL2."""
        cache_dir, prices_dir, processed_dir = temp_dirs

        mock_client = MagicMock(spec=CryptoCompareClient)
        mock_client.get_top_coins_by_market_cap.return_value = sample_coins

        fetcher = DataFetcher(
            client=mock_client,
            cache=FileCache(cache_dir=cache_dir),
            price_cache=PriceDataCache(prices_dir=prices_dir),
        )

        with patch("data.fetcher.ACCEPTED_COINS_JSON", processed_dir / "accepted.json"), patch(
            "data.fetcher.PROCESSED_DIR", processed_dir
        ):
            result = fetcher.fetch_and_filter_coins(
                n=7,
                for_total2=True,  # Should also exclude stablecoins
                use_cache=False,
                export_filtered=False,
            )

        assert result.success is True
        # USDT should now also be filtered
        summary = fetcher.get_filter_summary()
        reasons = summary["by_reason"]
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
        with tempfile.TemporaryDirectory() as cache_dir, tempfile.TemporaryDirectory() as prices_dir:
            yield Path(cache_dir), Path(prices_dir)

    @pytest.fixture
    def sample_price_df(self):
        """Sample price DataFrame as returned by CryptoCompare."""
        from datetime import date, timedelta

        # Use dates ending at yesterday so cache is considered "up to date"
        # This prevents incremental fetching from triggering additional API calls
        yesterday = date.today() - timedelta(days=1)
        dates = pd.date_range(end=yesterday, periods=3, freq="D")
        return pd.DataFrame(
            {
                "price": [1.0, 1.1, 1.2],
                "open": [0.9, 1.0, 1.1],
                "high": [1.1, 1.2, 1.3],
                "low": [0.8, 0.9, 1.0],
                "volume_from": [50000, 55000, 60000],
                "volume_to": [1000, 1100, 1200],
            },
            index=dates,
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

        df = fetcher.fetch_coin_prices("btc", symbol="BTC", use_cache=False)

        assert not df.empty
        assert "price" in df.columns
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

        # First call - hits API
        df1 = fetcher.fetch_coin_prices("btc", symbol="BTC", use_cache=True)

        # Second call - uses cache
        df2 = fetcher.fetch_coin_prices("btc", symbol="BTC", use_cache=True)

        # API should only be called once
        assert mock_client.get_full_daily_history.call_count == 1
        # Check data equality (parquet may change index frequency metadata)
        pd.testing.assert_frame_equal(df1.reset_index(drop=True), df2.reset_index(drop=True))


class TestDataFetcherGetFilterSummary:
    """Tests for filter summary."""

    def test_get_filter_summary_structure(self):
        """Test filter summary structure."""
        fetcher = DataFetcher()

        summary = fetcher.get_filter_summary()

        assert "filtered_count" in summary
        assert "by_reason" in summary
        assert "filtered_tokens" in summary
        assert isinstance(summary["filtered_count"], int)
        assert isinstance(summary["by_reason"], dict)
        assert isinstance(summary["filtered_tokens"], list)

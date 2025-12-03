"""
Tests for data fetcher orchestration.

Tests cover:
- Fetching and filtering coins
- Price data fetching
- Integration with cache and filter
"""

import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from api.coingecko import Coin, CoinGeckoClient, CoinGeckoError
from api.cryptocompare import CryptoCompareClient
from data.cache import FileCache, PriceDataCache
from data.fetcher import DataFetcher, FetcherError, FetchResult


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
        mock_client = MagicMock(spec=CoinGeckoClient)
        mock_cache = MagicMock(spec=FileCache)
        
        fetcher = DataFetcher(client=mock_client, cache=mock_cache)
        
        assert fetcher.client is mock_client
        assert fetcher.cache is mock_cache


class TestDataFetcherTopCoins:
    """Tests for fetching top coins."""
    
    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories for cache."""
        with tempfile.TemporaryDirectory() as cache_dir, \
             tempfile.TemporaryDirectory() as prices_dir:
            yield Path(cache_dir), Path(prices_dir)
    
    @pytest.fixture
    def fetcher(self, temp_dirs):
        """Create a DataFetcher with temp directories."""
        cache_dir, prices_dir = temp_dirs
        
        mock_client = MagicMock(spec=CoinGeckoClient)
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
            Coin(id="bitcoin", symbol="btc", name="Bitcoin", market_cap_rank=1),
            Coin(id="ethereum", symbol="eth", name="Ethereum", market_cap_rank=2),
            Coin(id="wrapped-bitcoin", symbol="wbtc", name="Wrapped Bitcoin", market_cap_rank=15),
            Coin(id="solana", symbol="sol", name="Solana", market_cap_rank=5),
            Coin(id="tether", symbol="usdt", name="Tether", market_cap_rank=3),
        ]
    
    def test_fetch_top_coins_returns_list(self, fetcher, sample_coins):
        """Test fetching top coins returns a list."""
        fetcher.client.get_top_coins.return_value = sample_coins
        
        result = fetcher.fetch_top_coins(n=5, use_cache=False)
        
        assert isinstance(result, list)
        assert len(result) == 5
        assert result[0]["id"] == "bitcoin"
    
    def test_fetch_top_coins_uses_cache(self, fetcher, sample_coins):
        """Test that cached data is used."""
        fetcher.client.get_top_coins.return_value = sample_coins
        
        # First call - should hit API
        result1 = fetcher.fetch_top_coins(n=5, use_cache=True)
        
        # Second call - should use cache
        result2 = fetcher.fetch_top_coins(n=5, use_cache=True)
        
        # API should only be called once
        assert fetcher.client.get_top_coins.call_count == 1
        assert result1 == result2
    
    def test_fetch_top_coins_bypasses_cache(self, fetcher, sample_coins):
        """Test that cache can be bypassed."""
        fetcher.client.get_top_coins.return_value = sample_coins
        
        fetcher.fetch_top_coins(n=5, use_cache=False)
        fetcher.fetch_top_coins(n=5, use_cache=False)
        
        # API should be called twice
        assert fetcher.client.get_top_coins.call_count == 2


class TestDataFetcherFilterCoins:
    """Tests for fetching and filtering coins."""
    
    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories."""
        with tempfile.TemporaryDirectory() as cache_dir, \
             tempfile.TemporaryDirectory() as prices_dir, \
             tempfile.TemporaryDirectory() as processed_dir:
            yield Path(cache_dir), Path(prices_dir), Path(processed_dir)
    
    @pytest.fixture
    def sample_coins(self):
        """Sample coins with a mix of types."""
        return [
            Coin(id="bitcoin", symbol="btc", name="Bitcoin"),
            Coin(id="ethereum", symbol="eth", name="Ethereum"),
            Coin(id="wrapped-bitcoin", symbol="wbtc", name="Wrapped Bitcoin"),
            Coin(id="lido-staked-ether", symbol="steth", name="Lido Staked Ether"),
            Coin(id="solana", symbol="sol", name="Solana"),
            Coin(id="sui", symbol="sui", name="Sui"),
            Coin(id="tether", symbol="usdt", name="Tether"),
        ]
    
    def test_fetch_and_filter_excludes_wrapped(self, temp_dirs, sample_coins):
        """Test that wrapped/staked tokens are filtered."""
        cache_dir, prices_dir, processed_dir = temp_dirs
        
        mock_client = MagicMock(spec=CoinGeckoClient)
        mock_client.get_top_coins.return_value = sample_coins
        
        cache = FileCache(cache_dir=cache_dir)
        price_cache = PriceDataCache(prices_dir=prices_dir)
        
        fetcher = DataFetcher(
            client=mock_client,
            cache=cache,
            price_cache=price_cache,
        )
        
        # Patch the output paths
        with patch("data.fetcher.ACCEPTED_COINS_JSON", processed_dir / "accepted.json"), \
             patch("data.fetcher.PROCESSED_DIR", processed_dir):
            
            result = fetcher.fetch_and_filter_coins(
                n=7,
                for_total2=False,
                use_cache=False,
                export_filtered=False,
            )
        
        assert result.success is True
        assert result.coins_fetched == 7
        # Should filter: bitcoin, wrapped-bitcoin, lido-staked-ether
        # Accept: ethereum, solana, sui, tether (stablecoin kept when not for_total2)
        assert result.coins_filtered >= 2  # At least wrapped and staked
    
    def test_fetch_and_filter_excludes_stablecoins_for_total2(self, temp_dirs, sample_coins):
        """Test that stablecoins are filtered for TOTAL2."""
        cache_dir, prices_dir, processed_dir = temp_dirs
        
        mock_client = MagicMock(spec=CoinGeckoClient)
        mock_client.get_top_coins.return_value = sample_coins
        
        fetcher = DataFetcher(
            client=mock_client,
            cache=FileCache(cache_dir=cache_dir),
            price_cache=PriceDataCache(prices_dir=prices_dir),
        )
        
        with patch("data.fetcher.ACCEPTED_COINS_JSON", processed_dir / "accepted.json"), \
             patch("data.fetcher.PROCESSED_DIR", processed_dir):
            
            result = fetcher.fetch_and_filter_coins(
                n=7,
                for_total2=True,  # Should also exclude stablecoins
                use_cache=False,
                export_filtered=False,
            )
        
        assert result.success is True
        # Tether should now also be filtered
        summary = fetcher.get_filter_summary()
        reasons = summary["by_reason"]
        assert "Stablecoin" in reasons
    
    def test_fetch_and_filter_handles_api_error(self, temp_dirs):
        """Test error handling for API failures."""
        cache_dir, prices_dir, _ = temp_dirs
        
        mock_client = MagicMock(spec=CoinGeckoClient)
        mock_client.get_top_coins.side_effect = CoinGeckoError("API down")
        
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
        with tempfile.TemporaryDirectory() as cache_dir, \
             tempfile.TemporaryDirectory() as prices_dir:
            yield Path(cache_dir), Path(prices_dir)
    
    @pytest.fixture
    def sample_price_df(self):
        """Sample price DataFrame as returned by CryptoCompare."""
        from datetime import date, timedelta
        # Use dates ending at yesterday so cache is considered "up to date"
        # This prevents incremental fetching from triggering additional API calls
        yesterday = date.today() - timedelta(days=1)
        dates = pd.date_range(end=yesterday, periods=3, freq="D")
        return pd.DataFrame({
            "price": [1.0, 1.1, 1.2],
            "open": [0.9, 1.0, 1.1],
            "high": [1.1, 1.2, 1.3],
            "low": [0.8, 0.9, 1.0],
            "volume_from": [50000, 55000, 60000],
            "volume_to": [1000, 1100, 1200],
        }, index=dates)
    
    def test_fetch_coin_prices(self, temp_dirs, sample_price_df):
        """Test fetching prices for a single coin using CryptoCompare."""
        cache_dir, prices_dir = temp_dirs
        
        mock_cc_client = MagicMock(spec=CryptoCompareClient)
        mock_cc_client.get_full_daily_history.return_value = sample_price_df
        
        fetcher = DataFetcher(
            client=MagicMock(spec=CoinGeckoClient),
            cryptocompare_client=mock_cc_client,
            cache=FileCache(cache_dir=cache_dir),
            price_cache=PriceDataCache(prices_dir=prices_dir),
        )
        
        df = fetcher.fetch_coin_prices("bitcoin", symbol="BTC", use_cache=False)
        
        assert not df.empty
        assert "price" in df.columns
        assert len(df) == 3
    
    def test_fetch_coin_prices_uses_cache(self, temp_dirs, sample_price_df):
        """Test that price cache is used."""
        cache_dir, prices_dir = temp_dirs
        
        mock_cc_client = MagicMock(spec=CryptoCompareClient)
        mock_cc_client.get_full_daily_history.return_value = sample_price_df
        
        fetcher = DataFetcher(
            client=MagicMock(spec=CoinGeckoClient),
            cryptocompare_client=mock_cc_client,
            cache=FileCache(cache_dir=cache_dir),
            price_cache=PriceDataCache(prices_dir=prices_dir),
        )
        
        # First call - hits API
        df1 = fetcher.fetch_coin_prices("bitcoin", symbol="BTC", use_cache=True)
        
        # Second call - uses cache
        df2 = fetcher.fetch_coin_prices("bitcoin", symbol="BTC", use_cache=True)
        
        # API should only be called once
        assert mock_cc_client.get_full_daily_history.call_count == 1
        # Check data equality (parquet may change index frequency metadata)
        pd.testing.assert_frame_equal(
            df1.reset_index(drop=True), 
            df2.reset_index(drop=True)
        )


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


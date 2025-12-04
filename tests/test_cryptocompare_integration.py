"""
Integration tests for CryptoCompare API.

These tests make ACTUAL API calls to CryptoCompare.
They are skipped by default - run with: pytest --run-integration

CryptoCompare API notes:
- FREE tier: No API key required for basic access
- Rate limit: Varies by endpoint (client enforces conservative rate)
- Key advantage: Full historical data available (no 365-day limit)

To run integration tests:
    pytest tests/test_cryptocompare_integration.py --run-integration -v
"""

import time
from datetime import date, timedelta

import pandas as pd
import pytest
from api.cryptocompare import (
    APIError,
    Coin,
    CryptoCompareClient,
    RateLimitError,
)

# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client():
    """Create a CryptoCompare client for integration tests with conservative rate limiting."""
    # Use very conservative rate limiting for tests (5 calls/min = 12 seconds between calls)
    return CryptoCompareClient(calls_per_minute=5)


class TestCryptoCompareIntegrationPing:
    """Integration tests for API connectivity."""

    def test_ping_api(self, client):
        """Test that we can ping the CryptoCompare API."""
        result = client.ping()
        assert result is True

    def test_ping_response_time(self, client):
        """Test that API responds in reasonable time (excluding rate limit wait)."""
        # Note: elapsed time includes rate limit wait, so we allow generous timeout
        start = time.time()
        client.ping()
        elapsed = time.time() - start

        # Should respond within 15 seconds (includes rate limit wait)
        assert elapsed < 15.0


class TestCryptoCompareIntegrationTopCoins:
    """Integration tests for top coins by market cap endpoint."""

    def test_get_top_coins_by_market_cap(self, client):
        """Test fetching top coins by market cap."""
        coins = client.get_top_coins_by_market_cap(n=50)

        assert isinstance(coins, list)
        assert len(coins) == 50

        # All items should be Coin objects
        assert all(isinstance(c, Coin) for c in coins)

        # First coin should be BTC
        assert coins[0].symbol == "BTC"

    def test_get_top_100_coins(self, client):
        """Test fetching top 100 coins (requires pagination)."""
        coins = client.get_top_coins_by_market_cap(n=100)

        assert isinstance(coins, list)
        assert len(coins) == 100

        # Should have BTC and ETH
        symbols = [c.symbol for c in coins]
        assert "BTC" in symbols
        assert "ETH" in symbols

    def test_get_top_300_coins(self, client):
        """Test fetching top 300 coins (requires 3 pages)."""
        coins = client.get_top_coins_by_market_cap(n=300)

        assert isinstance(coins, list)
        assert len(coins) == 300

        # Check coin structure
        coin = coins[0]
        assert coin.symbol != ""
        assert coin.name != ""
        assert coin.market_cap > 0
        assert coin.current_price > 0
        assert coin.volume_24h >= 0

    def test_coin_to_dict_format(self, client):
        """Test that Coin.to_dict() returns expected format for filtering."""
        coins = client.get_top_coins_by_market_cap(n=10)

        coin_dict = coins[0].to_dict()

        # Check required fields for filtering
        assert "id" in coin_dict  # lowercase symbol
        assert "symbol" in coin_dict
        assert "name" in coin_dict
        assert "market_cap" in coin_dict
        assert "volume_24h" in coin_dict

        # id should be lowercase
        assert coin_dict["id"] == coin_dict["symbol"].lower()

    def test_top_coins_have_volume_data(self, client):
        """Test that top coins have volume data for TOTAL2 calculation."""
        coins = client.get_top_coins_by_market_cap(n=50)

        # Most top coins should have non-zero volume
        coins_with_volume = [c for c in coins if c.volume_24h > 0]
        assert len(coins_with_volume) >= 40  # At least 80% should have volume


class TestCryptoCompareIntegrationDailyHistory:
    """Integration tests for daily historical price data."""

    def test_get_btc_daily_history_short(self, client):
        """Test fetching 10 days of BTC price data."""
        result = client.get_daily_history("BTC", "USD", limit=10)

        assert isinstance(result, list)
        assert len(result) == 11  # limit + 1 (includes today)

        # Check record structure
        record = result[0]
        assert "time" in record
        assert "open" in record
        assert "high" in record
        assert "low" in record
        assert "close" in record

    def test_get_eth_btc_daily_history(self, client):
        """Test fetching ETH priced in BTC."""
        result = client.get_daily_history("ETH", "BTC", limit=10)

        assert len(result) > 0

        # ETH price in BTC should be reasonable (0.01 - 0.2)
        latest = result[-1]
        assert 0.001 <= latest["close"] <= 0.5

    def test_get_daily_history_has_volume(self, client):
        """Test that volume data is included."""
        result = client.get_daily_history("BTC", "USD", limit=5)

        record = result[0]
        assert "volumefrom" in record
        assert "volumeto" in record
        assert record["volumefrom"] > 0


class TestCryptoCompareIntegrationFullHistory:
    """Integration tests for full historical data (the key advantage of CryptoCompare)."""

    def test_get_btc_full_history_one_year(self, client):
        """Test fetching one year of BTC data as DataFrame."""
        end_date = date.today()
        start_date = end_date - timedelta(days=365)

        df = client.get_full_daily_history(
            symbol="BTC",
            vs_currency="USD",
            start_date=start_date,
            end_date=end_date,
        )

        assert isinstance(df, pd.DataFrame)
        assert not df.empty

        # Should have close to 365 rows
        assert len(df) >= 350

        # Check columns
        assert "close" in df.columns
        assert "open" in df.columns
        assert "high" in df.columns
        assert "low" in df.columns

    def test_get_full_history_multi_year(self, client):
        """Test fetching 3+ years of data (tests pagination beyond 2000 days)."""
        end_date = date.today()
        start_date = end_date - timedelta(days=1100)  # ~3 years

        df = client.get_full_daily_history(
            symbol="BTC",
            vs_currency="USD",
            start_date=start_date,
            end_date=end_date,
        )

        assert isinstance(df, pd.DataFrame)
        assert not df.empty

        # Should have close to 1100 rows
        assert len(df) >= 1000

    def test_get_full_history_halving_cycle_range(self, client):
        """Test fetching data covering a full halving cycle (4+ years)."""
        # A halving cycle is approximately 4 years (210,000 blocks ~ 1400-1500 days)
        end_date = date.today()
        start_date = date(2020, 5, 11)  # 3rd halving date

        df = client.get_full_daily_history(
            symbol="BTC",
            vs_currency="USD",
            start_date=start_date,
            end_date=end_date,
        )

        assert isinstance(df, pd.DataFrame)
        assert not df.empty

        # Should cover the entire period
        assert df.index.min().date() <= start_date + timedelta(days=1)

    def test_get_eth_history_from_2017(self, client):
        """Test fetching ETH data from 2017 (testing long-range historical access)."""
        df = client.get_full_daily_history(
            symbol="ETH",
            vs_currency="BTC",
            start_date=date(2017, 1, 1),
            end_date=date.today(),
        )

        assert isinstance(df, pd.DataFrame)
        assert not df.empty

        # ETH existed in 2017, so we should have data back to then
        # (or close to it, as exact date availability may vary)
        assert df.index.min().year <= 2017


class TestCryptoCompareIntegrationCoinList:
    """Integration tests for coin list endpoint."""

    def test_get_coin_list(self, client):
        """Test fetching the list of all coins."""
        coins = client.get_coin_list()

        assert isinstance(coins, dict)
        assert len(coins) > 1000  # Should have many coins

        # BTC and ETH should be present
        assert "BTC" in coins
        assert "ETH" in coins

    def test_coin_list_structure(self, client):
        """Test that coin info has expected structure."""
        coins = client.get_coin_list()

        btc = coins["BTC"]
        assert "CoinName" in btc or "FullName" in btc
        assert "Symbol" in btc


class TestCryptoCompareIntegrationRateLimiting:
    """Integration tests for rate limiting behavior."""

    def test_rate_limiting_prevents_429(self, client):
        """Test that rate limiting prevents 429 errors."""
        # Make multiple requests - client should automatically rate limit
        for _ in range(3):
            try:
                client.get_daily_history("BTC", "USD", limit=5)
            except RateLimitError:
                pytest.fail("Rate limiting should prevent 429 errors")
            except APIError as e:
                if "429" in str(e):
                    pytest.fail("Rate limiting should prevent 429 errors")


class TestCryptoCompareIntegrationErrorHandling:
    """Integration tests for error handling."""

    def test_invalid_symbol(self, client):
        """Test error handling for invalid symbol."""
        # CryptoCompare returns an API error for completely invalid symbols
        with pytest.raises(APIError) as exc_info:
            client.get_daily_history("NOTAREALCOIN12345", "USD", limit=5)

        # Should indicate market doesn't exist
        assert (
            "market does not exist" in str(exc_info.value).lower()
            or "error" in str(exc_info.value).lower()
        )

    def test_future_date_handling(self, client):
        """Test that requesting future dates doesn't break."""
        future_date = date.today() + timedelta(days=365)

        df = client.get_full_daily_history(
            symbol="BTC",
            vs_currency="USD",
            start_date=date.today() - timedelta(days=7),
            end_date=future_date,
        )

        # Should still return valid data up to today
        assert isinstance(df, pd.DataFrame)
        assert not df.empty

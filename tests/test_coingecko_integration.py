"""
Integration tests for CoinGecko API.

These tests make ACTUAL API calls to CoinGecko.
They are skipped by default - run with: pytest --run-integration

CoinGecko API notes:
- FREE tier: No API key required, public REST API
- Rate limit: 10-30 calls/minute (client enforces 6 second intervals)
- Pro tier: Requires API key (not used here)

To run integration tests:
    pytest tests/test_coingecko_integration.py --run-integration -v
"""

import time

import pytest
from api.coingecko import (
    APIError,
    Coin,
    CoinGeckoClient,
    RateLimitError,
)

# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client():
    """Create a CoinGecko client for integration tests with conservative rate limiting."""
    # Use very conservative rate limiting for tests (5 calls/min = 12 seconds between calls)
    # This helps avoid 429 errors during testing
    return CoinGeckoClient(calls_per_minute=5)


class TestCoinGeckoIntegrationPing:
    """Integration tests for API connectivity."""

    def test_ping_api(self, client):
        """Test that we can ping the CoinGecko API."""
        result = client.ping()
        assert result is True

    def test_ping_response_time(self, client):
        """Test that API responds in reasonable time (excluding rate limit wait)."""
        # Note: elapsed time includes rate limit wait, so we allow generous timeout
        # The actual API response should be <2s, but with rate limiting overhead
        # we allow up to 15 seconds total
        start = time.time()
        client.ping()
        elapsed = time.time() - start

        # Should respond within 15 seconds (includes rate limit wait)
        assert elapsed < 15.0


class TestCoinGeckoIntegrationCoins:
    """Integration tests for fetching coin data."""

    def test_get_top_10_coins(self, client):
        """Test fetching top 10 coins."""
        coins = client.get_top_coins(n=10)

        assert len(coins) == 10
        assert all(isinstance(c, Coin) for c in coins)

        # Bitcoin should be #1
        assert coins[0].id == "bitcoin"
        assert coins[0].market_cap_rank == 1

    def test_get_top_coins_has_required_fields(self, client):
        """Test that coins have all required fields."""
        coins = client.get_top_coins(n=5)

        for coin in coins:
            assert coin.id is not None
            assert coin.symbol is not None
            assert coin.name is not None
            assert coin.market_cap_rank is not None

    def test_get_top_coins_btc_price(self, client):
        """Test that BTC prices are reasonable."""
        coins = client.get_top_coins(n=10, vs_currency="btc")

        # Bitcoin priced in BTC should be ~1.0
        btc = next(c for c in coins if c.id == "bitcoin")
        assert 0.99 <= btc.current_price_btc <= 1.01

        # ETH should be some fraction of BTC
        eth = next((c for c in coins if c.id == "ethereum"), None)
        if eth:
            assert 0.01 <= eth.current_price_btc <= 0.2  # Reasonable range


class TestCoinGeckoIntegrationMarketChart:
    """Integration tests for market chart data."""

    def test_get_bitcoin_market_chart_7_days(self, client):
        """Test fetching 7 days of Bitcoin price data."""
        data = client.get_coin_market_chart("bitcoin", days=7)

        assert "prices" in data
        assert "market_caps" in data
        assert "total_volumes" in data

        # Should have data points
        assert len(data["prices"]) > 0

        # Each price point should be [timestamp, value]
        for point in data["prices"]:
            assert len(point) == 2
            assert isinstance(point[0], int | float)  # timestamp
            assert isinstance(point[1], int | float)  # price

    def test_get_ethereum_market_chart_btc(self, client):
        """Test fetching ETH price in BTC."""
        data = client.get_coin_market_chart(
            "ethereum",
            vs_currency="btc",
            days=7,
        )

        assert len(data["prices"]) > 0

        # ETH price in BTC should be between 0.01 and 0.2
        latest_price = data["prices"][-1][1]
        assert 0.01 <= latest_price <= 0.2

    def test_get_market_chart_365_days(self, client):
        """Test fetching maximum allowed data for free tier (365 days)."""
        # Free tier is limited to 365 days of historical data
        data = client.get_coin_market_chart(
            "bitcoin",
            vs_currency="btc",
            days=365,
        )

        # Should have close to 365 data points (daily data)
        assert len(data["prices"]) >= 300


class TestCoinGeckoIntegrationRateLimiting:
    """Integration tests for rate limiting behavior."""

    def test_rate_limiting_prevents_429(self, client):
        """Test that rate limiting prevents 429 errors."""
        # Make multiple quick requests
        # The client should automatically rate limit
        for _ in range(3):
            try:
                client.get_top_coins(n=5)
            except RateLimitError:
                pytest.fail("Rate limiting should prevent 429 errors")
            except APIError as e:
                if "429" in str(e):
                    pytest.fail("Rate limiting should prevent 429 errors")


class TestCoinGeckoIntegrationErrorHandling:
    """Integration tests for error handling."""

    def test_invalid_coin_id(self, client):
        """Test error handling for invalid coin ID."""
        with pytest.raises(APIError):
            client.get_coin_market_chart("this-coin-does-not-exist-12345")

    def test_invalid_vs_currency(self, client):
        """Test error handling for invalid currency."""
        # Some invalid currencies may work or fail gracefully
        # This test just ensures no crash
        try:
            client.get_coin_market_chart("bitcoin", vs_currency="xyz123")
        except APIError:
            pass  # Expected

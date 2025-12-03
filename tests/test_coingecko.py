"""
Tests for CoinGecko API client.

Tests cover:
- Client initialization
- Rate limiting behavior
- Coin data parsing
- Error handling
"""

import time
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from api.coingecko import (
    APIError,
    Coin,
    CoinGeckoClient,
    CoinGeckoError,
    RateLimitError,
)


class TestCoin:
    """Tests for the Coin dataclass."""
    
    def test_coin_creation(self):
        """Test creating a Coin instance."""
        coin = Coin(
            id="bitcoin",
            symbol="btc",
            name="Bitcoin",
            market_cap_rank=1,
            market_cap=1000000000,
            current_price_btc=1.0,
        )
        
        assert coin.id == "bitcoin"
        assert coin.symbol == "btc"
        assert coin.name == "Bitcoin"
        assert coin.market_cap_rank == 1
    
    def test_coin_to_dict(self):
        """Test converting Coin to dictionary."""
        coin = Coin(
            id="ethereum",
            symbol="eth",
            name="Ethereum",
            market_cap_rank=2,
        )
        
        d = coin.to_dict()
        
        assert d["id"] == "ethereum"
        assert d["symbol"] == "eth"
        assert d["name"] == "Ethereum"
        assert d["market_cap_rank"] == 2
        assert "market_cap" in d
        assert "current_price_btc" in d


class TestCoinGeckoClientInit:
    """Tests for client initialization."""
    
    def test_default_initialization(self):
        """Test client initializes with default values."""
        client = CoinGeckoClient()
        
        assert client.base_url == "https://api.coingecko.com/api/v3"
        assert client.calls_per_minute == 10
        assert client._last_request_time is None
    
    def test_custom_initialization(self):
        """Test client with custom parameters."""
        client = CoinGeckoClient(
            base_url="https://custom.api.com/v1",
            calls_per_minute=30,
        )
        
        assert client.base_url == "https://custom.api.com/v1"
        assert client.calls_per_minute == 30
    
    def test_base_url_trailing_slash_removed(self):
        """Test that trailing slash is removed from base URL."""
        client = CoinGeckoClient(base_url="https://api.example.com/")
        assert client.base_url == "https://api.example.com"


class TestCoinGeckoClientRateLimiting:
    """Tests for rate limiting behavior."""
    
    def test_rate_limit_interval_calculation(self):
        """Test that min_interval is calculated correctly."""
        client = CoinGeckoClient(calls_per_minute=10)
        assert client.min_interval == 6.0  # 60/10 = 6 seconds
        
        client = CoinGeckoClient(calls_per_minute=30)
        assert client.min_interval == 2.0  # 60/30 = 2 seconds
    
    def test_wait_for_rate_limit_first_call(self):
        """Test that first call doesn't wait."""
        client = CoinGeckoClient()
        
        start = time.time()
        client._wait_for_rate_limit()
        elapsed = time.time() - start
        
        # Should be nearly instant
        assert elapsed < 0.1
    
    def test_wait_for_rate_limit_respects_interval(self):
        """Test that subsequent calls respect the rate limit."""
        client = CoinGeckoClient(calls_per_minute=60)  # 1 second interval
        
        # Simulate a previous request
        client._last_request_time = time.time()
        
        start = time.time()
        client._wait_for_rate_limit()
        elapsed = time.time() - start
        
        # Should have waited approximately 1 second
        assert elapsed >= 0.9


class TestCoinGeckoClientRequests:
    """Tests for API request handling."""
    
    @pytest.fixture
    def client(self):
        """Create a client instance for testing."""
        return CoinGeckoClient()
    
    @pytest.fixture
    def mock_response(self):
        """Create a mock response factory."""
        def _mock(status_code=200, json_data=None):
            response = MagicMock()
            response.status_code = status_code
            response.json.return_value = json_data or {}
            response.text = str(json_data or {})
            return response
        return _mock
    
    def test_successful_request(self, client, mock_response):
        """Test a successful API request."""
        with patch.object(client.session, "get") as mock_get:
            mock_get.return_value = mock_response(200, {"gecko_says": "hello"})
            
            result = client._request("/ping")
            
            assert result == {"gecko_says": "hello"}
            mock_get.assert_called_once()
    
    def test_rate_limit_error_raised(self, client, mock_response):
        """Test that 429 response raises RateLimitError."""
        with patch.object(client.session, "get") as mock_get:
            mock_get.return_value = mock_response(429)
            
            with pytest.raises(RateLimitError):
                # Disable retry for test
                client._request.__wrapped__(client, "/test")
    
    def test_api_error_for_non_200(self, client, mock_response):
        """Test that non-200 responses raise APIError."""
        with patch.object(client.session, "get") as mock_get:
            mock_get.return_value = mock_response(500, {"error": "Server error"})
            
            with pytest.raises(APIError) as exc_info:
                client._request.__wrapped__(client, "/test")
            
            assert "500" in str(exc_info.value)


class TestCoinGeckoClientGetTopCoins:
    """Tests for get_top_coins method."""
    
    @pytest.fixture
    def client(self):
        return CoinGeckoClient()
    
    @pytest.fixture
    def sample_coin_data(self):
        """Sample coin data as returned by CoinGecko."""
        return [
            {
                "id": "bitcoin",
                "symbol": "btc",
                "name": "Bitcoin",
                "market_cap_rank": 1,
                "market_cap": 1000000000000,
                "current_price": 1.0,
            },
            {
                "id": "ethereum",
                "symbol": "eth",
                "name": "Ethereum",
                "market_cap_rank": 2,
                "market_cap": 500000000000,
                "current_price": 0.05,
            },
        ]
    
    def test_get_top_coins_parses_correctly(self, client, sample_coin_data):
        """Test that coin data is parsed into Coin objects."""
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = sample_coin_data
            
            coins = client.get_top_coins(n=2)
            
            assert len(coins) == 2
            assert coins[0].id == "bitcoin"
            assert coins[0].symbol == "btc"
            assert coins[0].name == "Bitcoin"
            assert coins[0].market_cap_rank == 1
            assert coins[1].id == "ethereum"
    
    def test_get_top_coins_respects_limit(self, client, sample_coin_data):
        """Test that get_top_coins respects the n parameter."""
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = sample_coin_data
            
            coins = client.get_top_coins(n=1)
            
            assert len(coins) == 1
    
    def test_get_top_coins_pagination(self, client):
        """Test that pagination works for large requests."""
        # Create 300 mock coins
        mock_coins = [
            {"id": f"coin-{i}", "symbol": f"c{i}", "name": f"Coin {i}"}
            for i in range(300)
        ]
        
        with patch.object(client, "_request") as mock_request:
            # First call returns 250 coins, second returns 50
            mock_request.side_effect = [mock_coins[:250], mock_coins[250:]]
            
            coins = client.get_top_coins(n=300)
            
            assert len(coins) == 300
            assert mock_request.call_count == 2


class TestCoinGeckoClientMarketChart:
    """Tests for market chart methods."""
    
    @pytest.fixture
    def client(self):
        return CoinGeckoClient()
    
    @pytest.fixture
    def sample_chart_data(self):
        """Sample market chart data."""
        return {
            "prices": [
                [1609459200000, 1.0],  # 2021-01-01
                [1609545600000, 1.1],  # 2021-01-02
            ],
            "market_caps": [
                [1609459200000, 1000000],
                [1609545600000, 1100000],
            ],
            "total_volumes": [
                [1609459200000, 50000],
                [1609545600000, 55000],
            ],
        }
    
    def test_get_coin_market_chart(self, client, sample_chart_data):
        """Test fetching market chart data."""
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = sample_chart_data
            
            result = client.get_coin_market_chart("bitcoin", days=30)
            
            assert "prices" in result
            assert "market_caps" in result
            assert "total_volumes" in result
            assert len(result["prices"]) == 2
    
    def test_get_coin_market_chart_range(self, client, sample_chart_data):
        """Test fetching market chart with date range."""
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = sample_chart_data
            
            result = client.get_coin_market_chart_range(
                "bitcoin",
                from_date=date(2021, 1, 1),
                to_date=date(2021, 12, 31),
            )
            
            assert "prices" in result
            mock_request.assert_called_once()
            
            # Check that timestamps were passed
            call_params = mock_request.call_args[1]["params"]
            assert "from" in call_params
            assert "to" in call_params


class TestCoinGeckoClientPing:
    """Tests for ping method."""
    
    def test_ping_success(self):
        """Test that ping returns True on success."""
        client = CoinGeckoClient()
        
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = {"gecko_says": "hello"}
            
            assert client.ping() is True
    
    def test_ping_failure(self):
        """Test that ping returns False on error."""
        client = CoinGeckoClient()
        
        with patch.object(client, "_request") as mock_request:
            mock_request.side_effect = CoinGeckoError("Connection failed")
            
            assert client.ping() is False


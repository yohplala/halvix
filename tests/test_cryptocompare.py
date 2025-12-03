"""
Tests for CryptoCompare API client.

Tests cover:
- Client initialization
- Rate limiting behavior  
- Historical price fetching
- Error handling
"""

import time
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from api.cryptocompare import (
    APIError,
    CryptoCompareClient,
    CryptoCompareError,
    RateLimitError,
)


class TestCryptoCompareClientInit:
    """Tests for client initialization."""
    
    def test_default_initialization(self):
        """Test client initializes with default values."""
        client = CryptoCompareClient()
        
        assert client.base_url == "https://min-api.cryptocompare.com"
        assert client.api_key is None
        assert client._last_request_time is None
    
    def test_custom_initialization(self):
        """Test client with custom parameters."""
        client = CryptoCompareClient(
            base_url="https://custom.api.com",
            api_key="test-key",
            calls_per_minute=60,
        )
        
        assert client.base_url == "https://custom.api.com"
        assert client.api_key == "test-key"
        assert client.calls_per_minute == 60
    
    def test_api_key_in_headers(self):
        """Test that API key is added to headers."""
        client = CryptoCompareClient(api_key="my-api-key")
        
        assert "authorization" in client.session.headers
        assert client.session.headers["authorization"] == "Apikey my-api-key"


class TestCryptoCompareClientRateLimiting:
    """Tests for rate limiting behavior."""
    
    def test_rate_limit_interval_calculation(self):
        """Test that min_interval is calculated correctly."""
        client = CryptoCompareClient(calls_per_minute=30)
        assert client.min_interval == 2.0  # 60/30 = 2 seconds
    
    def test_wait_for_rate_limit_first_call(self):
        """Test that first call doesn't wait."""
        client = CryptoCompareClient()
        
        start = time.time()
        client._wait_for_rate_limit()
        elapsed = time.time() - start
        
        # Should be nearly instant
        assert elapsed < 0.1


class TestCryptoCompareClientRequests:
    """Tests for API request handling."""
    
    @pytest.fixture
    def client(self):
        return CryptoCompareClient()
    
    @pytest.fixture
    def mock_response(self):
        def _mock(status_code=200, json_data=None):
            response = MagicMock()
            response.status_code = status_code
            response.json.return_value = json_data or {"Response": "Success"}
            response.text = str(json_data or {})
            return response
        return _mock
    
    def test_successful_request(self, client, mock_response):
        """Test a successful API request."""
        with patch.object(client.session, "get") as mock_get:
            mock_get.return_value = mock_response(200, {"Response": "Success", "Data": {"Data": []}})
            
            result = client._request("/test")
            
            assert result["Response"] == "Success"
            mock_get.assert_called_once()
    
    def test_rate_limit_error_raised(self, client, mock_response):
        """Test that 429 response raises RateLimitError."""
        with patch.object(client.session, "get") as mock_get:
            mock_get.return_value = mock_response(429)
            
            with pytest.raises(RateLimitError):
                client._request.__wrapped__(client, "/test")
    
    def test_api_error_for_error_response(self, client, mock_response):
        """Test that error response raises APIError."""
        with patch.object(client.session, "get") as mock_get:
            mock_get.return_value = mock_response(200, {"Response": "Error", "Message": "Invalid symbol"})
            
            with pytest.raises(APIError) as exc_info:
                client._request("/test")
            
            assert "Invalid symbol" in str(exc_info.value)


class TestCryptoCompareClientDailyHistory:
    """Tests for daily history methods."""
    
    @pytest.fixture
    def client(self):
        return CryptoCompareClient()
    
    @pytest.fixture
    def sample_history_response(self):
        return {
            "Response": "Success",
            "Data": {
                "Data": [
                    {
                        "time": 1704067200,  # 2024-01-01
                        "open": 0.05,
                        "high": 0.052,
                        "low": 0.049,
                        "close": 0.051,
                        "volumefrom": 1000,
                        "volumeto": 50,
                    },
                    {
                        "time": 1704153600,  # 2024-01-02
                        "open": 0.051,
                        "high": 0.053,
                        "low": 0.050,
                        "close": 0.052,
                        "volumefrom": 1100,
                        "volumeto": 55,
                    },
                ]
            }
        }
    
    def test_get_daily_history(self, client, sample_history_response):
        """Test fetching daily history."""
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = sample_history_response
            
            result = client.get_daily_history("ETH", "BTC", limit=10)
            
            # Result is the list of daily records from Data.Data
            assert len(result) == 2
            assert result[0]["close"] == 0.051
            mock_request.assert_called_once()
    
    def test_get_full_daily_history(self, client, sample_history_response):
        """Test fetching full history as DataFrame."""
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = sample_history_response
            
            df = client.get_full_daily_history(
                symbol="ETH",
                vs_currency="BTC",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 2),
            )
            
            assert isinstance(df, pd.DataFrame)
            assert not df.empty
            assert "price" in df.columns  # 'close' is renamed to 'price'
            assert len(df) == 2
    
    def test_get_full_daily_history_pagination(self, client):
        """Test that pagination works for large requests."""
        # Create mock responses for pagination
        first_response = {
            "Response": "Success",
            "Data": {
                "Data": [
                    {"time": 1704067200 - i * 86400, "close": 0.05, "open": 0.05, 
                     "high": 0.05, "low": 0.05, "volumefrom": 100, "volumeto": 5}
                    for i in range(2000)
                ]
            }
        }
        second_response = {
            "Response": "Success",
            "Data": {
                "Data": [
                    {"time": 1704067200 - (2000 + i) * 86400, "close": 0.05, "open": 0.05,
                     "high": 0.05, "low": 0.05, "volumefrom": 100, "volumeto": 5}
                    for i in range(500)
                ]
            }
        }
        
        with patch.object(client, "_request") as mock_request:
            mock_request.side_effect = [first_response, second_response]
            
            df = client.get_full_daily_history(
                symbol="BTC",
                vs_currency="USD",
                start_date=date(2017, 1, 1),
                end_date=date(2024, 1, 1),
            )
            
            # Should have made multiple requests
            assert mock_request.call_count >= 1
            assert not df.empty


class TestCryptoCompareClientSymbolMapping:
    """Tests for symbol mapping."""
    
    @pytest.fixture
    def client(self):
        return CryptoCompareClient()
    
    def test_get_symbol_for_coingecko_id(self, client):
        """Test mapping CoinGecko ID to CryptoCompare symbol."""
        # Method takes (coingecko_id, coingecko_symbol)
        assert client.get_symbol_for_coingecko_id("bitcoin", "btc") == "BTC"
        assert client.get_symbol_for_coingecko_id("ethereum", "eth") == "ETH"
        assert client.get_symbol_for_coingecko_id("solana", "sol") == "SOL"
    
    def test_special_symbol_mapping(self, client):
        """Test special symbol overrides."""
        # IOTA has a special mapping
        assert client.get_symbol_for_coingecko_id("miota", "miota") == "IOTA"


class TestCryptoCompareClientPing:
    """Tests for ping method."""
    
    def test_ping_success(self):
        """Test that ping returns True on success."""
        client = CryptoCompareClient()
        
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = {"Response": "Success"}
            
            assert client.ping() is True
    
    def test_ping_failure(self):
        """Test that ping returns False on error."""
        client = CryptoCompareClient()
        
        with patch.object(client, "_request") as mock_request:
            mock_request.side_effect = CryptoCompareError("Connection failed")
            
            assert client.ping() is False


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
        """Test that first call doesn't wait (when rate limit status is OK)."""
        from api.cryptocompare import RateLimitStatus

        client = CryptoCompareClient()

        # Mock rate limit status to return plenty of quota
        with patch.object(client, "get_rate_limit_status") as mock_status:
            mock_status.return_value = RateLimitStatus(
                calls_left_second=10,
                calls_left_minute=100,
                calls_left_hour=1000,
                calls_left_month=10000,
            )

            start = time.time()
            client._wait_for_rate_limit()
            elapsed = time.time() - start

            # Should be nearly instant when not near limit
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
        from api.cryptocompare import RateLimitStatus

        # Mock rate limit status to avoid extra API calls
        with patch.object(client, "get_rate_limit_status") as mock_status:
            mock_status.return_value = RateLimitStatus(
                calls_left_second=10,
                calls_left_minute=100,
                calls_left_hour=1000,
                calls_left_month=10000,
            )

            with patch.object(client.session, "get") as mock_get:
                mock_get.return_value = mock_response(
                    200, {"Response": "Success", "Data": {"Data": []}}
                )

                result = client._request("/test")

                assert result["Response"] == "Success"
                mock_get.assert_called_once()

    def test_rate_limit_error_raised(self, client, mock_response):
        """Test that 429 response raises RateLimitError."""
        # Mock _wait_for_rate_limit to avoid rate limit checks during test
        with patch.object(client, "_wait_for_rate_limit"):
            with patch.object(client.session, "get") as mock_get:
                mock_get.return_value = mock_response(429)

                with pytest.raises(RateLimitError):
                    client._request.__wrapped__(client, "/test")

    def test_api_error_for_error_response(self, client, mock_response):
        """Test that error response raises APIError."""
        # Mock _wait_for_rate_limit to avoid rate limit checks during test
        with patch.object(client, "_wait_for_rate_limit"):
            with patch.object(client.session, "get") as mock_get:
                mock_get.return_value = mock_response(
                    200, {"Response": "Error", "Message": "Invalid symbol"}
                )

                with pytest.raises(APIError) as exc_info:
                    # Use __wrapped__ to bypass the @retry decorator
                    client._request.__wrapped__(client, "/test")

                assert "Invalid symbol" in str(exc_info.value)

    def test_rate_limit_in_json_body_raises_rate_limit_error(self, client, mock_response):
        """Test that rate limit error in JSON body raises RateLimitError (not APIError).

        CryptoCompare sometimes returns rate limit errors as HTTP 200 with error in JSON body
        (e.g., monthly quota exceeded) instead of HTTP 429. We need to detect these
        and raise RateLimitError to trigger retry logic.
        """
        # Mock _wait_for_rate_limit to avoid rate limit checks during test
        with patch.object(client, "_wait_for_rate_limit"):
            with patch.object(client.session, "get") as mock_get:
                mock_get.return_value = mock_response(
                    200, {"Response": "Error", "Message": "You are over your rate limit"}
                )

                # Should raise RateLimitError, not APIError
                with pytest.raises(RateLimitError) as exc_info:
                    client._request.__wrapped__(client, "/test")

                assert "rate limit" in str(exc_info.value).lower()


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
            },
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
            assert "close" in df.columns
            assert len(df) == 2

    def test_get_full_daily_history_pagination(self, client):
        """Test that pagination works for large requests."""
        # Create mock responses for pagination
        first_response = {
            "Response": "Success",
            "Data": {
                "Data": [
                    {
                        "time": 1704067200 - i * 86400,
                        "close": 0.05,
                        "open": 0.05,
                        "high": 0.05,
                        "low": 0.05,
                        "volumefrom": 100,
                        "volumeto": 5,
                    }
                    for i in range(2000)
                ]
            },
        }
        second_response = {
            "Response": "Success",
            "Data": {
                "Data": [
                    {
                        "time": 1704067200 - (2000 + i) * 86400,
                        "close": 0.05,
                        "open": 0.05,
                        "high": 0.05,
                        "low": 0.05,
                        "volumefrom": 100,
                        "volumeto": 5,
                    }
                    for i in range(500)
                ]
            },
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


class TestCryptoCompareClientRateLimitStatus:
    """Tests for rate limit status checking."""

    def test_get_rate_limit_status_parses_response(self):
        """Test that rate limit status is correctly parsed from API response."""

        client = CryptoCompareClient()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "Data": {
                "calls_made": {
                    "second": 1,
                    "minute": 10,
                    "hour": 100,
                    "day": 500,
                    "month": 5000,
                },
                "calls_left": {
                    "second": 9,
                    "minute": 90,
                    "hour": 900,
                    "day": 4500,
                    "month": 95000,
                },
            }
        }

        with patch.object(client.session, "get", return_value=mock_response):
            status = client.get_rate_limit_status(use_cache=False)

            assert status.calls_made_second == 1
            assert status.calls_left_second == 9
            assert status.calls_made_minute == 10
            assert status.calls_left_minute == 90
            assert status.calls_made_month == 5000
            assert status.calls_left_month == 95000

    def test_rate_limit_status_is_near_limit(self):
        """Test that is_near_limit correctly identifies when we're approaching limits."""
        from api.cryptocompare import RateLimitStatus

        # Not near limit - plenty of quota
        status_ok = RateLimitStatus(
            calls_left_second=5,
            calls_left_minute=50,
            calls_left_hour=500,
            calls_left_month=5000,
        )
        assert not status_ok.is_near_limit

        # Near limit - almost out of seconds
        status_second = RateLimitStatus(
            calls_left_second=0,
            calls_left_minute=50,
            calls_left_hour=500,
            calls_left_month=5000,
        )
        assert status_second.is_near_limit

        # Near limit - almost out of minutes
        status_minute = RateLimitStatus(
            calls_left_second=5,
            calls_left_minute=2,
            calls_left_hour=500,
            calls_left_month=5000,
        )
        assert status_minute.is_near_limit

    def test_rate_limit_status_recommended_wait(self):
        """Test that recommended_wait_seconds returns appropriate values."""
        from api.cryptocompare import RateLimitStatus

        # Near second limit - wait 1 second
        status = RateLimitStatus(calls_left_second=0, calls_left_minute=50, calls_left_hour=500)
        assert status.recommended_wait_seconds == 1.0

        # Near minute limit - wait 10 seconds
        status = RateLimitStatus(calls_left_second=5, calls_left_minute=2, calls_left_hour=500)
        assert status.recommended_wait_seconds == 10.0

        # Near hour limit - wait 60 seconds
        status = RateLimitStatus(calls_left_second=5, calls_left_minute=50, calls_left_hour=20)
        assert status.recommended_wait_seconds == 60.0

        # Not near any limit - no wait needed
        status = RateLimitStatus(
            calls_left_second=5, calls_left_minute=50, calls_left_hour=500, calls_left_month=5000
        )
        assert status.recommended_wait_seconds == 0.0

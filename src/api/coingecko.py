"""
CoinGecko API client for Halvix.

Provides methods to:
- Fetch top N coins by market cap
- Fetch historical price data for a coin
- Handle rate limiting and retries
"""

import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import (
    API_CALLS_PER_MINUTE,
    API_MAX_RETRIES,
    API_MIN_INTERVAL,
    API_RETRY_MAX_WAIT,
    API_RETRY_MIN_WAIT,
    COINGECKO_BASE_URL,
    TOP_N_COINS,
)


class CoinGeckoError(Exception):
    """Base exception for CoinGecko API errors."""
    pass


class RateLimitError(CoinGeckoError):
    """Raised when API rate limit is exceeded."""
    pass


class APIError(CoinGeckoError):
    """Raised for general API errors."""
    pass


@dataclass
class Coin:
    """Represents a coin from CoinGecko."""
    id: str
    symbol: str
    name: str
    market_cap_rank: int | None = None
    market_cap: float | None = None
    current_price_btc: float | None = None
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for filtering and processing."""
        return {
            "id": self.id,
            "symbol": self.symbol,
            "name": self.name,
            "market_cap_rank": self.market_cap_rank,
            "market_cap": self.market_cap,
            "current_price_btc": self.current_price_btc,
        }


class CoinGeckoClient:
    """
    CoinGecko API client with rate limiting and retry logic.
    
    Usage:
        client = CoinGeckoClient()
        coins = client.get_top_coins(n=300)
        prices = client.get_coin_market_chart("bitcoin", days=365)
    """
    
    def __init__(
        self,
        base_url: str = COINGECKO_BASE_URL,
        calls_per_minute: int = API_CALLS_PER_MINUTE,
    ):
        """
        Initialize the CoinGecko client.
        
        Args:
            base_url: CoinGecko API base URL
            calls_per_minute: Maximum API calls per minute (rate limiting)
        """
        self.base_url = base_url.rstrip("/")
        self.calls_per_minute = calls_per_minute
        self.min_interval = 60.0 / calls_per_minute
        self._last_request_time: float | None = None
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "Halvix/0.1.0",
        })
    
    def _wait_for_rate_limit(self) -> None:
        """Wait if necessary to respect rate limits."""
        if self._last_request_time is not None:
            elapsed = time.time() - self._last_request_time
            if elapsed < self.min_interval:
                sleep_time = self.min_interval - elapsed
                time.sleep(sleep_time)
    
    @retry(
        retry=retry_if_exception_type(RateLimitError),
        stop=stop_after_attempt(API_MAX_RETRIES),
        wait=wait_exponential(
            multiplier=1,
            min=API_RETRY_MIN_WAIT,
            max=API_RETRY_MAX_WAIT,
        ),
    )
    def _request(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> dict | list:
        """
        Make a rate-limited request to the CoinGecko API.
        
        Args:
            endpoint: API endpoint (e.g., "/coins/markets")
            params: Query parameters
            
        Returns:
            Parsed JSON response
            
        Raises:
            RateLimitError: When rate limit is exceeded (will retry)
            APIError: For other API errors
        """
        self._wait_for_rate_limit()
        
        url = f"{self.base_url}{endpoint}"
        
        try:
            response = self.session.get(url, params=params, timeout=30)
            self._last_request_time = time.time()
            
            if response.status_code == 429:
                raise RateLimitError("Rate limit exceeded")
            
            if response.status_code != 200:
                raise APIError(
                    f"API error {response.status_code}: {response.text}"
                )
            
            return response.json()
            
        except requests.RequestException as e:
            raise APIError(f"Request failed: {e}") from e
    
    def get_top_coins(
        self,
        n: int = TOP_N_COINS,
        vs_currency: str = "btc",
    ) -> list[Coin]:
        """
        Fetch top N coins by market cap.
        
        Args:
            n: Number of coins to fetch (default: TOP_N_COINS from config)
            vs_currency: Quote currency for prices (default: "btc")
            
        Returns:
            List of Coin objects sorted by market cap rank
        """
        coins: list[Coin] = []
        per_page = 250  # CoinGecko max per page
        pages_needed = (n + per_page - 1) // per_page
        
        for page in range(1, pages_needed + 1):
            # Calculate how many to fetch on this page
            remaining = n - len(coins)
            page_size = min(per_page, remaining)
            
            data = self._request(
                "/coins/markets",
                params={
                    "vs_currency": vs_currency,
                    "order": "market_cap_desc",
                    "per_page": page_size,
                    "page": page,
                    "sparkline": "false",
                    "locale": "en",
                },
            )
            
            for coin_data in data:
                coins.append(Coin(
                    id=coin_data["id"],
                    symbol=coin_data["symbol"],
                    name=coin_data["name"],
                    market_cap_rank=coin_data.get("market_cap_rank"),
                    market_cap=coin_data.get("market_cap"),
                    current_price_btc=coin_data.get("current_price"),
                ))
            
            if len(coins) >= n:
                break
        
        return coins[:n]
    
    def get_coin_market_chart(
        self,
        coin_id: str,
        vs_currency: str = "btc",
        days: int | str = "max",
    ) -> dict[str, list]:
        """
        Fetch historical market data for a coin.
        
        Args:
            coin_id: CoinGecko coin ID (e.g., "bitcoin", "ethereum")
            vs_currency: Quote currency (default: "btc")
            days: Number of days of data, or "max" for all available
            
        Returns:
            Dictionary with 'prices', 'market_caps', 'total_volumes' keys
            Each value is a list of [timestamp_ms, value] pairs
        """
        data = self._request(
            f"/coins/{coin_id}/market_chart",
            params={
                "vs_currency": vs_currency,
                "days": str(days),
            },
        )
        
        return {
            "prices": data.get("prices", []),
            "market_caps": data.get("market_caps", []),
            "total_volumes": data.get("total_volumes", []),
        }
    
    def get_coin_market_chart_range(
        self,
        coin_id: str,
        vs_currency: str = "btc",
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> dict[str, list]:
        """
        Fetch historical market data for a coin within a date range.
        
        Args:
            coin_id: CoinGecko coin ID
            vs_currency: Quote currency (default: "btc")
            from_date: Start date (default: 2011-01-01)
            to_date: End date (default: today)
            
        Returns:
            Dictionary with 'prices', 'market_caps', 'total_volumes' keys
        """
        from_ts = int(datetime.combine(
            from_date or date(2011, 1, 1),
            datetime.min.time()
        ).timestamp())
        
        to_ts = int(datetime.combine(
            to_date or date.today(),
            datetime.max.time()
        ).timestamp())
        
        data = self._request(
            f"/coins/{coin_id}/market_chart/range",
            params={
                "vs_currency": vs_currency,
                "from": from_ts,
                "to": to_ts,
            },
        )
        
        return {
            "prices": data.get("prices", []),
            "market_caps": data.get("market_caps", []),
            "total_volumes": data.get("total_volumes", []),
        }
    
    def ping(self) -> bool:
        """
        Check if the API is reachable.
        
        Returns:
            True if API responds successfully
        """
        try:
            self._request("/ping")
            return True
        except CoinGeckoError:
            return False


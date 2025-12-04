"""
CryptoCompare API client for cryptocurrency data.

CryptoCompare offers free access to:
- Full historical data (2000+ days per request) for halving cycle analysis
- Top coins by market cap for coin discovery
- No symbol mapping needed - single source of truth

API Documentation: https://min-api.cryptocompare.com/documentation
"""

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from importlib.metadata import version
from typing import Any

import pandas as pd
import requests
from config import (
    CRYPTOCOMPARE_API_CALLS_PER_MINUTE,
    CRYPTOCOMPARE_BASE_URL,
)
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


def get_version() -> str:
    """Get package version for User-Agent."""
    try:
        return version("halvix")
    except Exception:
        return "dev"


class CryptoCompareError(Exception):
    """Base exception for CryptoCompare API errors."""

    pass


class RateLimitError(CryptoCompareError):
    """Raised when API rate limit is exceeded."""

    pass


class APIError(CryptoCompareError):
    """Raised for general API errors."""

    pass


@dataclass
class HistoricalPrice:
    """A single day's price data."""

    date: date
    open: float
    high: float
    low: float
    close: float
    volume_from: float
    volume_to: float


@dataclass
class Coin:
    """Represents a coin from CryptoCompare."""

    symbol: str
    name: str
    market_cap: float
    market_cap_rank: int
    current_price: float
    volume_24h: float
    circulating_supply: float

    def to_dict(self) -> dict:
        """Convert to dictionary for filtering and processing."""
        return {
            "id": self.symbol.lower(),  # Use lowercase symbol as ID
            "symbol": self.symbol,
            "name": self.name,
            "market_cap": self.market_cap,
            "market_cap_rank": self.market_cap_rank,
            "current_price": self.current_price,
            "volume_24h": self.volume_24h,
            "circulating_supply": self.circulating_supply,
        }


class CryptoCompareClient:
    """
    CryptoCompare API client for historical cryptocurrency prices.

    Free tier provides full historical data (no time limit).

    Usage:
        client = CryptoCompareClient()
        df = client.get_daily_history("BTC", "USD", days=5000)
    """

    def __init__(
        self,
        base_url: str = CRYPTOCOMPARE_BASE_URL,
        api_key: str | None = None,
        calls_per_minute: int = CRYPTOCOMPARE_API_CALLS_PER_MINUTE,
    ):
        """
        Initialize the CryptoCompare client.

        Args:
            base_url: API base URL
            api_key: Optional API key (not required for basic access)
            calls_per_minute: Rate limit
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.calls_per_minute = calls_per_minute
        self.min_interval = 60.0 / calls_per_minute
        self._last_request_time: float | None = None

        self.session = requests.Session()
        headers = {
            "Accept": "application/json",
            "User-Agent": f"Halvix/{get_version()}",
        }
        if api_key:
            headers["authorization"] = f"Apikey {api_key}"
        self.session.headers.update(headers)

    def _wait_for_rate_limit(self) -> None:
        """Wait if necessary to respect rate limits."""
        if self._last_request_time is not None:
            elapsed = time.time() - self._last_request_time
            if elapsed < self.min_interval:
                sleep_time = self.min_interval - elapsed
                time.sleep(sleep_time)

    @retry(
        retry=retry_if_exception_type(RateLimitError),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=60),
    )
    def _request(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> dict:
        """
        Make a rate-limited request to the CryptoCompare API.

        Args:
            endpoint: API endpoint
            params: Query parameters

        Returns:
            Parsed JSON response
        """
        self._wait_for_rate_limit()

        url = f"{self.base_url}{endpoint}"

        try:
            response = self.session.get(url, params=params, timeout=30)
            self._last_request_time = time.time()

            if response.status_code == 429:
                raise RateLimitError("Rate limit exceeded")

            if response.status_code != 200:
                raise APIError(f"API error {response.status_code}: {response.text}")

            data = response.json()

            # CryptoCompare returns Response: "Error" for errors
            if data.get("Response") == "Error":
                raise APIError(f"API error: {data.get('Message', 'Unknown error')}")

            return data

        except requests.RequestException as e:
            raise APIError(f"Request failed: {e}") from e

    def get_daily_history(
        self,
        symbol: str,
        vs_currency: str = "BTC",
        limit: int = 2000,
        to_timestamp: int | None = None,
    ) -> list[dict]:
        """
        Get daily historical prices for a cryptocurrency.

        Args:
            symbol: Coin symbol (e.g., "ETH", "SOL")
            vs_currency: Quote currency (default: "BTC")
            limit: Number of days (max 2000 per request)
            to_timestamp: End timestamp (default: now)

        Returns:
            List of daily price records
        """
        params = {
            "fsym": symbol.upper(),
            "tsym": vs_currency.upper(),
            "limit": min(limit, 2000),  # API max is 2000
        }

        if to_timestamp:
            params["toTs"] = to_timestamp

        data = self._request("/data/v2/histoday", params)

        return data.get("Data", {}).get("Data", [])

    def get_full_daily_history(
        self,
        symbol: str,
        vs_currency: str = "BTC",
        start_date: date | None = None,
        end_date: date | None = None,
        show_progress: bool = False,
    ) -> pd.DataFrame:
        """
        Get full daily historical prices, paginating if needed.

        This method handles fetching more than 2000 days by making
        multiple requests with different end timestamps.

        Args:
            symbol: Coin symbol (e.g., "ETH", "SOL")
            vs_currency: Quote currency (default: "BTC")
            start_date: Earliest date to fetch (default: 2010-01-01)
            end_date: Latest date to fetch (default: yesterday - today's data is incomplete)
            show_progress: Print progress messages

        Returns:
            DataFrame with date index and OHLCV columns
        """
        if start_date is None:
            start_date = date(2010, 1, 1)
        if end_date is None:
            # Use yesterday - today's data is incomplete (day hasn't ended)
            end_date = date.today() - timedelta(days=1)

        start_ts = int(datetime.combine(start_date, datetime.min.time()).timestamp())
        end_ts = int(datetime.combine(end_date, datetime.max.time()).timestamp())

        all_records = []
        current_to_ts = end_ts

        while True:
            if show_progress:
                current_date = datetime.fromtimestamp(current_to_ts).date()
                print(f"  Fetching {symbol} data up to {current_date}...")

            records = self.get_daily_history(
                symbol=symbol,
                vs_currency=vs_currency,
                limit=2000,
                to_timestamp=current_to_ts,
            )

            if not records:
                break

            # Filter out records before start_date
            valid_records = [r for r in records if r.get("time", 0) >= start_ts]

            all_records.extend(valid_records)

            # Check if we've reached the start date
            oldest_ts = min(r.get("time", float("inf")) for r in records)
            if oldest_ts <= start_ts:
                break

            # Move to earlier data (subtract 1 day to avoid duplicates)
            current_to_ts = oldest_ts - 86400

            # Safety check - if we got fewer than expected, we're done
            if len(records) < 2000:
                break

        if not all_records:
            return pd.DataFrame()

        # Remove duplicates and convert to DataFrame
        seen_times = set()
        unique_records = []
        for r in all_records:
            t = r.get("time")
            if t not in seen_times:
                seen_times.add(t)
                unique_records.append(r)

        df = pd.DataFrame(unique_records)

        # Convert timestamp to datetime
        df["date"] = pd.to_datetime(df["time"], unit="s")
        df = df.set_index("date").sort_index()

        # Rename columns to standard names
        df = df.rename(
            columns={
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "price",
                "volumefrom": "volume_from",
                "volumeto": "volume_to",
            }
        )

        # Select and order columns
        columns = ["price", "open", "high", "low", "volume_from", "volume_to"]
        available = [c for c in columns if c in df.columns]
        df = df[available]

        return df

    def get_coin_list(self) -> dict[str, dict]:
        """
        Get list of all coins available on CryptoCompare.

        Returns:
            Dictionary mapping symbol to coin info
        """
        data = self._request("/data/all/coinlist")
        return data.get("Data", {})

    def get_top_coins_by_market_cap(
        self,
        n: int = 300,
        vs_currency: str = "USD",
    ) -> list[Coin]:
        """
        Get top N coins by market capitalization.

        Uses pagination (100 coins per page) to fetch up to N coins.

        Args:
            n: Number of top coins to fetch (default: 300)
            vs_currency: Quote currency for prices (default: "USD")

        Returns:
            List of Coin objects sorted by market cap rank
        """
        coins: list[Coin] = []
        page = 0
        per_page = 100  # CryptoCompare returns 100 per page max

        while len(coins) < n:
            data = self._request(
                "/data/top/mktcapfull",
                params={
                    "limit": per_page,
                    "page": page,
                    "tsym": vs_currency.upper(),
                },
            )

            coin_data_list = data.get("Data", [])
            if not coin_data_list:
                break

            for coin_data in coin_data_list:
                coin_info = coin_data.get("CoinInfo", {})
                raw_data = coin_data.get("RAW", {}).get(vs_currency.upper(), {})

                if not raw_data:
                    continue

                coins.append(
                    Coin(
                        symbol=coin_info.get("Name", ""),
                        name=coin_info.get("FullName", ""),
                        market_cap=raw_data.get("MKTCAP", 0),
                        market_cap_rank=len(coins) + 1,
                        current_price=raw_data.get("PRICE", 0),
                        volume_24h=raw_data.get("VOLUME24HOUR", 0),
                        circulating_supply=raw_data.get("CIRCULATINGSUPPLY", 0),
                    )
                )

                if len(coins) >= n:
                    break

            page += 1

            # Safety check - API may not have more data
            if len(coin_data_list) < per_page:
                break

        return coins[:n]

    def ping(self) -> bool:
        """
        Check if the API is reachable.

        Returns:
            True if API responds successfully
        """
        try:
            # Use rate limit endpoint as a simple ping
            self._request("/data/v2/histoday", {"fsym": "BTC", "tsym": "USD", "limit": 1})
            return True
        except CryptoCompareError:
            return False

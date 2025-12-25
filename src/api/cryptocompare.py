"""
CryptoCompare API client for cryptocurrency data.

CryptoCompare (now part of CoinDesk) offers free access to:
- Full historical data (2000+ days per request) for halving cycle analysis
- Top coins by market cap for coin discovery
- No symbol mapping needed - single source of truth

API Documentation: https://developers.coindesk.com/documentation/

Endpoints used:
- /data/v2/histoday - Daily OHLCV prices (spot_v1_historical_days)
- /data/top/mktcapfull - Top coins by market cap (asset_v1_top_list)
- /stats/rate/limit - Rate limit status (admin_v2_rate_limit)
"""

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from importlib.metadata import version
from typing import Any

import pandas as pd
import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import (
    CRYPTOCOMPARE_API_CALLS_PER_MINUTE,
    CRYPTOCOMPARE_BASE_URL,
)
from utils.logging import get_logger

# Module logger for API debugging (uses halvix namespace for proper log propagation)
logger = get_logger(__name__)


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


@dataclass
class RateLimitStatus:
    """Current rate limit status from the API."""

    calls_made_second: int = 0
    calls_left_second: int = 0
    calls_made_minute: int = 0
    calls_left_minute: int = 0
    calls_made_hour: int = 0
    calls_left_hour: int = 0
    calls_made_day: int = 0
    calls_left_day: int = 0
    calls_made_month: int = 0
    calls_left_month: int = 0

    @property
    def is_near_limit(self) -> bool:
        """Check if we're approaching any rate limit."""
        # Consider "near limit" if less than 10% remaining on any tier
        return (
            self.calls_left_second < 1
            or self.calls_left_minute < 5
            or self.calls_left_hour < 50
            or self.calls_left_month < 100
        )

    @property
    def recommended_wait_seconds(self) -> float:
        """Calculate recommended wait time based on current limits."""
        if self.calls_left_second < 1:
            return 1.0  # Wait 1 second
        if self.calls_left_minute < 5:
            return 10.0  # Wait 10 seconds
        if self.calls_left_hour < 50:
            return 60.0  # Wait 1 minute
        return 0.0


class CryptoCompareClient:
    """
    CryptoCompare API client for historical cryptocurrency prices.

    Free tier provides full historical data (no time limit).

    Uses the rate limit status endpoint to dynamically manage request rate:
    https://developers.coindesk.com/documentation/data-api/admin_v2_rate_limit

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
        self._last_rate_check_time: float | None = None
        self._cached_rate_status: RateLimitStatus | None = None
        self._rate_check_interval = 30.0  # Check rate limit every 30 seconds

        self.session = requests.Session()
        headers = {
            "Accept": "application/json",
            "User-Agent": f"Halvix/{get_version()}",
        }
        if api_key:
            headers["authorization"] = f"Apikey {api_key}"
        self.session.headers.update(headers)

        # Rate limit logging: track calls for periodic status logging
        self._calls_since_last_log = 0
        self._calls_log_interval = 50  # Log rate limit status every N calls
        self._last_status_log_time: float | None = None
        self._status_log_interval = 60.0  # Or every N seconds
        self._dynamic_limits_available = False  # Track if API rate limit endpoint works

    def get_rate_limit_status(self, use_cache: bool = True) -> RateLimitStatus:
        """
        Get current rate limit status from the API.

        Uses the /stats/rate/limit endpoint to check remaining quota.
        See: https://developers.coindesk.com/documentation/data-api/admin_v2_rate_limit

        Args:
            use_cache: If True, return cached status if checked recently

        Returns:
            RateLimitStatus with current usage and remaining calls
        """
        # Return cached status if recent enough
        if use_cache and self._cached_rate_status is not None:
            if self._last_rate_check_time is not None:
                elapsed = time.time() - self._last_rate_check_time
                if elapsed < self._rate_check_interval:
                    return self._cached_rate_status

        try:
            url = f"{self.base_url}/stats/rate/limit"
            response = self.session.get(url, timeout=10)
            self._last_rate_check_time = time.time()

            if response.status_code != 200:
                logger.warning("Failed to get rate limit status: HTTP %d", response.status_code)
                self._dynamic_limits_available = False
                return RateLimitStatus()

            data = response.json()

            # Parse the response - structure varies by endpoint
            # The stats endpoint returns nested data by time period
            def extract_calls(period_data: dict) -> tuple[int, int]:
                """Extract calls_made and calls_left from period data."""
                calls_made = period_data.get("calls_made", {}).get("Histo", 0)
                calls_left = period_data.get("calls_left", {}).get("Histo", 0)
                return calls_made, calls_left

            status = RateLimitStatus()

            if "Data" in data:
                rate_data = data["Data"]

                if "calls_made" in rate_data and "calls_left" in rate_data:
                    # Flat structure
                    calls_made = rate_data.get("calls_made", {})
                    calls_left = rate_data.get("calls_left", {})

                    status.calls_made_second = calls_made.get("second", 0)
                    status.calls_left_second = calls_left.get("second", 0)
                    status.calls_made_minute = calls_made.get("minute", 0)
                    status.calls_left_minute = calls_left.get("minute", 0)
                    status.calls_made_hour = calls_made.get("hour", 0)
                    status.calls_left_hour = calls_left.get("hour", 0)
                    status.calls_made_day = calls_made.get("day", 0)
                    status.calls_left_day = calls_left.get("day", 0)
                    status.calls_made_month = calls_made.get("month", 0)
                    status.calls_left_month = calls_left.get("month", 0)

            self._cached_rate_status = status
            self._dynamic_limits_available = True  # Successfully got dynamic limits
            logger.debug(
                "Rate limit status: %d/%d second, %d/%d minute, %d/%d hour, %d/%d month",
                status.calls_made_second,
                status.calls_made_second + status.calls_left_second,
                status.calls_made_minute,
                status.calls_made_minute + status.calls_left_minute,
                status.calls_made_hour,
                status.calls_made_hour + status.calls_left_hour,
                status.calls_made_month,
                status.calls_made_month + status.calls_left_month,
            )
            return status

        except Exception as e:
            logger.warning("Error checking rate limit status: %s", e)
            self._dynamic_limits_available = False
            return RateLimitStatus()

    def _log_rate_limit_status_if_needed(self, status: RateLimitStatus) -> None:
        """Log rate limit status periodically (every N calls or N seconds)."""
        self._calls_since_last_log += 1

        should_log = False
        reason = ""

        # Log every N calls
        if self._calls_since_last_log >= self._calls_log_interval:
            should_log = True
            reason = f"every {self._calls_log_interval} calls"

        # Or log every N seconds
        elif self._last_status_log_time is not None:
            elapsed = time.time() - self._last_status_log_time
            if elapsed >= self._status_log_interval:
                should_log = True
                reason = f"every {self._status_log_interval:.0f}s"

        # Or log on first call (no previous log time)
        elif self._last_status_log_time is None:
            should_log = True
            reason = "initial status"

        if should_log:
            self._calls_since_last_log = 0
            self._last_status_log_time = time.time()

            # Calculate totals for clearer logging
            total_second = status.calls_made_second + status.calls_left_second
            total_minute = status.calls_made_minute + status.calls_left_minute
            total_hour = status.calls_made_hour + status.calls_left_hour
            total_month = status.calls_made_month + status.calls_left_month

            # Determine which rate limit is currently applied
            # Use dynamic API limits when available, fallback only when API unavailable
            fallback_rate = self.calls_per_minute  # calls/min

            if self._dynamic_limits_available and total_minute > 0:
                # Dynamic limits available - use API's per-minute rate
                api_rate_per_min = total_minute

                logger.info(
                    "Rate limits (%s): ACTIVE=API @ %d calls/min | "
                    "quota: %d/%d sec, %d/%d min, %d/%d hour, %d/%d month",
                    reason,
                    api_rate_per_min,
                    status.calls_made_second,
                    total_second,
                    status.calls_made_minute,
                    total_minute,
                    status.calls_made_hour,
                    total_hour,
                    status.calls_made_month,
                    total_month,
                )
            elif self._dynamic_limits_available:
                # API responded but returned 0 quota - use fallback
                logger.info(
                    "Rate limits (%s): ACTIVE=fallback @ %d calls/min | "
                    "API returned no quota data (0 calls/min)",
                    reason,
                    fallback_rate,
                )
            else:
                # API endpoint unavailable - use fallback
                logger.info(
                    "Rate limits (%s): ACTIVE=fallback @ %d calls/min | "
                    "API status endpoint unavailable",
                    reason,
                    fallback_rate,
                )

    def _wait_for_rate_limit(self) -> None:
        """
        Wait if necessary to respect rate limits.

        Uses dynamic API rate limits when available, falls back to configured rate otherwise.
        """
        # Check rate limit status periodically
        status = self.get_rate_limit_status(use_cache=True)

        # Log status periodically for visibility
        self._log_rate_limit_status_if_needed(status)

        # Determine the interval to use based on available rate limit info
        if self._dynamic_limits_available:
            # Use dynamic rate from API (per-minute limit converted to interval)
            total_minute = status.calls_made_minute + status.calls_left_minute
            if total_minute > 0:
                dynamic_interval = 60.0 / total_minute
            else:
                dynamic_interval = self.min_interval  # Fallback if no data
            effective_interval = dynamic_interval
        else:
            # Fallback to configured rate
            effective_interval = self.min_interval

        # Apply time-based throttling with the effective interval
        if self._last_request_time is not None:
            elapsed = time.time() - self._last_request_time
            if elapsed < effective_interval:
                sleep_time = effective_interval - elapsed
                time.sleep(sleep_time)

        # Additional wait if approaching limits (near exhaustion)
        if status.is_near_limit:
            wait_time = status.recommended_wait_seconds
            if wait_time > 0:
                logger.info(
                    "Approaching rate limit (second: %d left, minute: %d left, hour: %d left). "
                    "Waiting %.1f seconds...",
                    status.calls_left_second,
                    status.calls_left_minute,
                    status.calls_left_hour,
                    wait_time,
                )
                time.sleep(wait_time)
                # Invalidate cache after waiting
                self._last_rate_check_time = None

    def wait_for_rate_limit_reset(self, max_wait_seconds: float = 120.0) -> bool:
        """
        Wait until rate limit is no longer critical.

        Polls the rate limit status and waits until we have sufficient quota.

        Args:
            max_wait_seconds: Maximum time to wait before giving up

        Returns:
            True if rate limit is now OK, False if max wait exceeded
        """
        start_time = time.time()
        check_interval = 5.0  # Check every 5 seconds

        while True:
            elapsed = time.time() - start_time
            if elapsed >= max_wait_seconds:
                logger.warning(
                    "Max wait time exceeded (%.0fs), proceeding anyway", max_wait_seconds
                )
                return False

            # Force fresh check
            status = self.get_rate_limit_status(use_cache=False)

            if not status.is_near_limit:
                logger.info("Rate limit OK after %.1f seconds wait", elapsed)
                return True

            remaining_wait = max_wait_seconds - elapsed
            wait_time = min(status.recommended_wait_seconds, remaining_wait, check_interval)

            if wait_time <= 0:
                return False

            logger.info(
                "Rate limit still constrained (second: %d, minute: %d, hour: %d left). "
                "Waiting %.1f more seconds (%.0fs elapsed)...",
                status.calls_left_second,
                status.calls_left_minute,
                status.calls_left_hour,
                wait_time,
                elapsed,
            )
            time.sleep(wait_time)

    @retry(
        retry=retry_if_exception_type(RateLimitError),
        stop=stop_after_attempt(10),  # Increased from 5 to 10 attempts
        wait=wait_exponential(multiplier=2, min=2, max=120),  # More aggressive backoff
    )
    def _request(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> dict:
        """
        Make a rate-limited request to the CryptoCompare API.

        Uses dynamic rate limiting based on actual API quota status.

        Args:
            endpoint: API endpoint
            params: Query parameters

        Returns:
            Parsed JSON response
        """
        self._wait_for_rate_limit()

        url = f"{self.base_url}{endpoint}"

        # Build a readable description of what we're fetching for debug logging
        param_info = ""
        if params:
            if "fsym" in params and "tsym" in params:
                param_info = f" [{params['fsym']}/{params['tsym']}]"
            elif "page" in params:
                param_info = f" [page {params['page']}]"

        logger.debug("API request: %s%s", endpoint, param_info)

        try:
            response = self.session.get(url, params=params, timeout=30)
            self._last_request_time = time.time()

            if response.status_code == 429:
                logger.warning("Rate limit hit (HTTP 429): %s%s", endpoint, param_info)
                # Invalidate rate limit cache and mark dynamic limits as unreliable
                self._last_rate_check_time = None
                self._dynamic_limits_available = False
                raise RateLimitError("Rate limit exceeded (HTTP 429)")

            if response.status_code != 200:
                raise APIError(f"API error {response.status_code}: {response.text}")

            data = response.json()

            # CryptoCompare returns Response: "Error" for errors
            if data.get("Response") == "Error":
                error_msg = data.get("Message", "Unknown error")
                logger.debug("API error for %s%s: %s", endpoint, param_info, error_msg)

                # CryptoCompare sometimes returns rate limit errors in JSON body
                # instead of HTTP 429 (e.g., monthly/hourly quota exceeded)
                if "rate limit" in error_msg.lower():
                    logger.warning(
                        "Rate limit hit (JSON body): %s%s - %s", endpoint, param_info, error_msg
                    )
                    # Invalidate rate limit cache and mark dynamic limits as unreliable
                    self._last_rate_check_time = None
                    self._dynamic_limits_available = False
                    raise RateLimitError(f"Rate limit exceeded: {error_msg}")

                raise APIError(f"API error: {error_msg}")

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
                "volumefrom": "volume_from",
                "volumeto": "volume_to",
            }
        )

        # Select and order columns
        columns = ["open", "high", "low", "close", "volume_from", "volume_to"]
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
        track_no_data: bool = False,
    ) -> list[Coin] | tuple[list[Coin], list[dict]]:
        """
        Get top N coins by market capitalization.

        Uses pagination (100 coins per page) to fetch up to N coins.

        Args:
            n: Number of top coins to fetch (default: 300)
            vs_currency: Quote currency for prices (default: "USD")
            track_no_data: If True, also return coins without price data

        Returns:
            If track_no_data=False: List of Coin objects sorted by market cap rank
            If track_no_data=True: Tuple of (coins, coins_without_data)
        """
        coins: list[Coin] = []
        coins_without_data: list[dict] = []
        page = 0
        per_page = 100  # CryptoCompare returns 100 per page max
        total_seen = 0
        termination_reason = "unknown"

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
                termination_reason = f"API returned empty data on page {page}"
                break

            for coin_data in coin_data_list:
                total_seen += 1
                coin_info = coin_data.get("CoinInfo", {})
                raw_data = coin_data.get("RAW", {}).get(vs_currency.upper(), {})

                if not raw_data:
                    # Track coins without price data
                    if track_no_data:
                        coins_without_data.append(
                            {
                                "symbol": coin_info.get("Name", ""),
                                "name": coin_info.get("FullName", ""),
                                "rank": total_seen,
                            }
                        )
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
                    termination_reason = f"reached target of {n} coins with USD data"
                    break

            page += 1

            # Safety check - API may not have more data
            if len(coin_data_list) < per_page:
                termination_reason = (
                    f"API returned {len(coin_data_list)} coins on page {page - 1} "
                    f"(less than {per_page})"
                )
                break
        else:
            # Loop completed normally (len(coins) >= n)
            termination_reason = f"reached target of {n} coins with USD data"

        # Log summary for debugging
        logger.info(
            "Top coins fetch complete: %d pages, %d total coins seen, "
            "%d with USD data, %d without. Reason: %s",
            page,
            total_seen,
            len(coins),
            len(coins_without_data) if track_no_data else 0,
            termination_reason,
        )

        if track_no_data:
            return coins[:n], coins_without_data
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

    def check_histoday_availability(
        self,
        symbol: str,
        vs_currency: str = "BTC",
        wait_for_rate_limit: bool = True,
        max_wait_seconds: float = 120.0,
    ) -> dict[str, str]:
        """
        Check if historical daily data is available for a trading pair.

        Makes a minimal histoday request (limit=1) to verify the pair exists
        on CryptoCompare's CCCAGG aggregated exchange data.

        If rate limited, will wait and retry (if wait_for_rate_limit=True).

        Args:
            symbol: Coin symbol (e.g., "KET", "ETH")
            vs_currency: Quote currency (e.g., "BTC", "USD")
            wait_for_rate_limit: If True, wait for rate limit reset and retry
            max_wait_seconds: Maximum time to wait for rate limit reset

        Returns:
            Dictionary with:
                - 'available': True if histoday works, False otherwise
                - 'reason': Human-readable explanation of why it failed (if applicable)
        """
        max_attempts = 3 if wait_for_rate_limit else 1

        for attempt in range(max_attempts):
            try:
                # Use the standard _request method which has rate limiting and retry
                data = self._request(
                    "/data/v2/histoday",
                    {
                        "fsym": symbol.upper(),
                        "tsym": vs_currency.upper(),
                        "limit": 1,
                    },
                )

                if data.get("Response") == "Error":
                    # Return the actual API error message
                    message = data.get("Message", "Unknown error")
                    return {
                        "available": False,
                        "reason": message,
                    }

                # Check if we got valid data
                records = data.get("Data", {}).get("Data", [])
                if not records:
                    return {
                        "available": False,
                        "reason": f"No historical data returned for {symbol}/{vs_currency}",
                    }

                return {
                    "available": True,
                    "reason": f"{symbol}/{vs_currency} pair available",
                }

            except RateLimitError as e:
                if wait_for_rate_limit and attempt < max_attempts - 1:
                    logger.info(
                        "Rate limit hit checking %s/%s (attempt %d/%d). Waiting for reset...",
                        symbol,
                        vs_currency,
                        attempt + 1,
                        max_attempts,
                    )
                    # Wait for rate limit to reset
                    self.wait_for_rate_limit_reset(max_wait_seconds=max_wait_seconds)
                    continue
                else:
                    return {
                        "available": False,
                        "reason": f"Rate limit exceeded after {attempt + 1} attempts: {e}",
                    }
            except Exception as e:
                return {
                    "available": False,
                    "reason": f"Error checking pair: {e}",
                }

        return {
            "available": False,
            "reason": "Max attempts exceeded",
        }

"""
CoinGecko API client — the default Halvix price-data provider.

CoinGecko covers the full coin universe (not just exchange-listed pairs),
exposes native market-cap ranking for discovery, and serves recent price +
volume against any quote currency (BTC, USD). Halvix already caches full
history, so this client only tops up the most recent days each run.

Endpoints used:
- /coins/markets        Top coins by market cap (discovery + symbol→id map)
- /coins/{id}/market_chart  Recent price + volume series (resampled to daily)
- /ping                 Reachability check

A free Demo API key (https://www.coingecko.com/en/api) lifts rate limits; set
it via CRYPTOCOMPARE-style env (COINGECKO_API_KEY). Keyless access also works
but is throttled harder and may return HTTP 429 under load.
"""

import time
from datetime import UTC, date, datetime, timedelta
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

from api.base import Coin, PriceProviderError
from config import (
    COINGECKO_API_KEY,
    COINGECKO_BASE_URL,
    COINGECKO_CALLS_PER_MINUTE,
    COINGECKO_MARKETS_PER_PAGE,
    COINGECKO_MAX_DAYS_PER_REQUEST,
)
from utils.logging import get_logger

logger = get_logger(__name__)

# Standard OHLCV column order, shared with the CryptoCompare backend so cached
# parquet files stay schema-compatible across providers.
_OHLCV_COLUMNS = ["open", "high", "low", "close", "volume_from", "volume_to"]


def _get_version() -> str:
    """Package version for the User-Agent header."""
    try:
        return version("halvix")
    except Exception:
        return "dev"


class CoinGeckoError(PriceProviderError):
    """General CoinGecko API error."""


class CoinGeckoRateLimitError(CoinGeckoError):
    """Raised when CoinGecko returns HTTP 429 (rate limit exceeded)."""


class CoinGeckoClient:
    """
    CoinGecko price-data client implementing the ``PriceProvider`` protocol.

    Usage:
        client = CoinGeckoClient()
        coins = client.get_top_coins_by_market_cap(n=300)
        df = client.get_full_daily_history("ETH", "BTC", provider_id="ethereum")
    """

    def __init__(
        self,
        base_url: str = COINGECKO_BASE_URL,
        api_key: str | None = None,
        calls_per_minute: int = COINGECKO_CALLS_PER_MINUTE,
    ):
        """
        Initialize the CoinGecko client.

        Args:
            base_url: API base URL.
            api_key: Demo API key. Defaults to COINGECKO_API_KEY from the
                environment. Keyless access works but is throttled harder.
            calls_per_minute: Fallback request rate (min interval between calls).
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key if api_key is not None else COINGECKO_API_KEY
        self.calls_per_minute = max(1, calls_per_minute)
        self.min_interval = 60.0 / self.calls_per_minute
        self._last_request_time: float | None = None

        # symbol (lowercase) → CoinGecko id, populated during discovery so
        # same-process price fetches can resolve ids without a provider_id.
        self._symbol_to_id: dict[str, str] = {}

        self.session = requests.Session()
        headers = {
            "Accept": "application/json",
            "User-Agent": f"Halvix/{_get_version()}",
        }
        if self.api_key:
            headers["x-cg-demo-api-key"] = self.api_key
        else:
            logger.info(
                "No CoinGecko API key set — using the throttled keyless tier. "
                "Set COINGECKO_API_KEY for a higher rate limit "
                "(free Demo key: https://www.coingecko.com/en/api)."
            )
        self.session.headers.update(headers)

    # ------------------------------------------------------------------ #
    # Low-level request
    # ------------------------------------------------------------------ #

    def _wait_for_rate_limit(self) -> None:
        """Sleep just enough to respect the configured minimum interval."""
        if self._last_request_time is not None:
            elapsed = time.time() - self._last_request_time
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)

    @retry(
        retry=retry_if_exception_type(CoinGeckoRateLimitError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=5, min=5, max=60),
        reraise=True,  # surface the original CoinGeckoRateLimitError, not a RetryError wrapper
    )
    def _request(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """Make a rate-limited GET request and return the parsed JSON."""
        self._wait_for_rate_limit()
        url = f"{self.base_url}{endpoint}"
        try:
            response = self.session.get(url, params=params, timeout=30)
            self._last_request_time = time.time()

            if response.status_code == 429:
                logger.warning("CoinGecko rate limit hit (HTTP 429): %s", endpoint)
                raise CoinGeckoRateLimitError("Rate limit exceeded (HTTP 429)")
            if response.status_code in (401, 403):
                raise CoinGeckoError(
                    f"Authentication failed (HTTP {response.status_code}). Check "
                    "COINGECKO_API_KEY (free Demo key: https://www.coingecko.com/en/api)."
                )
            if response.status_code != 200:
                raise CoinGeckoError(f"API error {response.status_code}: {response.text[:200]}")

            return response.json()
        except requests.RequestException as e:
            raise CoinGeckoError(f"Request failed: {e}") from e

    # ------------------------------------------------------------------ #
    # Discovery
    # ------------------------------------------------------------------ #

    def get_top_coins_by_market_cap(
        self,
        n: int = 300,
        vs_currency: str = "USD",
        track_no_data: bool = False,
    ) -> list[Coin] | tuple[list[Coin], list[dict]]:
        """
        Get the top ``n`` coins by market capitalization.

        Paginates /coins/markets (250 per page) and keeps, for each symbol, the
        highest-market-cap coin (the markets list is sorted by market cap, so
        the first occurrence wins). Records every coin's CoinGecko id so price
        fetches can address it directly.

        CoinGecko only returns coins that have market data, so there is no
        "without USD data" bucket; when ``track_no_data`` is True the second
        tuple element is always empty (kept for interface symmetry).
        """
        coins: list[Coin] = []
        seen_symbols: set[str] = set()
        page = 1
        per_page = COINGECKO_MARKETS_PER_PAGE

        while len(coins) < n:
            data = self._request(
                "/coins/markets",
                params={
                    "vs_currency": vs_currency.lower(),
                    "order": "market_cap_desc",
                    "per_page": per_page,
                    "page": page,
                },
            )
            if not data:
                break

            for entry in data:
                symbol = (entry.get("symbol") or "").upper()
                coin_id = entry.get("id") or ""
                if not symbol or not coin_id:
                    continue
                if symbol.lower() in seen_symbols:
                    continue  # keep the higher-market-cap duplicate already seen
                seen_symbols.add(symbol.lower())
                self._symbol_to_id[symbol.lower()] = coin_id
                coins.append(
                    Coin(
                        symbol=symbol,
                        name=entry.get("name") or symbol,
                        market_cap=entry.get("market_cap") or 0,
                        market_cap_rank=len(coins) + 1,
                        current_price=entry.get("current_price") or 0,
                        volume_24h=entry.get("total_volume") or 0,
                        circulating_supply=entry.get("circulating_supply") or 0,
                        provider_id=coin_id,
                    )
                )
                if len(coins) >= n:
                    break

            if len(data) < per_page:
                break  # last page reached
            page += 1

        logger.info("CoinGecko discovery: %d coins by market cap", len(coins))
        if track_no_data:
            return coins[:n], []
        return coins[:n]

    # ------------------------------------------------------------------ #
    # Price history
    # ------------------------------------------------------------------ #

    def _resolve_id(self, symbol: str, provider_id: str | None) -> str:
        """Resolve a CoinGecko id from an explicit provider_id or the symbol map."""
        if provider_id:
            return provider_id
        resolved = self._symbol_to_id.get(symbol.lower())
        if resolved:
            return resolved
        raise CoinGeckoError(
            f"No CoinGecko id for {symbol!r}; run discovery (list-coins) first "
            "so coins carry a provider_id."
        )

    def get_full_daily_history(
        self,
        symbol: str,
        vs_currency: str = "BTC",
        start_date: date | None = None,
        end_date: date | None = None,
        show_progress: bool = False,
        provider_id: str | None = None,
    ) -> pd.DataFrame:
        """
        Get daily OHLCV history for a coin, resampled from CoinGecko's intraday
        market_chart series.

        CoinGecko serves hourly points for multi-day ranges and daily points for
        long ranges; these are aggregated into daily bars (open/high/low/close +
        quote-currency volume).

        Request sizing: an incremental top-up requests exactly its small window;
        a deep request (no start_date, or a span beyond the cap — e.g. a new coin
        with no cache) requests ``COINGECKO_MAX_DAYS_PER_REQUEST`` days. That cap
        is the Demo/keyless historical limit (~365 days); the API rejects larger
        ranges with HTTP 401, so a brand-new coin gets at most ~1 year of history
        until more accrues from daily updates (deeper history requires a paid
        CoinGecko plan).

        Returns:
            DataFrame indexed by date with the standard OHLCV columns
            (empty if no data).
        """
        coin_id = self._resolve_id(symbol, provider_id)
        today = datetime.now(UTC).date()
        if end_date is None:
            end_date = today - timedelta(days=1)

        # CoinGecko counts back from "now". Cap at the tier's historical limit;
        # larger ranges are rejected (HTTP 401) on the Demo/keyless tier.
        if start_date is None:
            span_days = COINGECKO_MAX_DAYS_PER_REQUEST
        else:
            span_days = (today - start_date).days + 1
        days = max(2, min(span_days, COINGECKO_MAX_DAYS_PER_REQUEST))

        if show_progress:
            logger.info("Fetching %s/%s from CoinGecko (days=%d)", symbol, vs_currency, days)

        data = self._request(
            f"/coins/{coin_id}/market_chart",
            params={"vs_currency": vs_currency.lower(), "days": days},
        )
        prices = data.get("prices") or []
        volumes = data.get("total_volumes") or []
        if not prices:
            return pd.DataFrame(columns=_OHLCV_COLUMNS)

        df = self._to_daily_ohlcv(prices, volumes)
        if df.empty:
            return df

        # Keep only complete days within the requested window.
        df = df[df.index.date < today]
        if start_date is not None:
            df = df[df.index.date >= start_date]
        if end_date is not None:
            df = df[df.index.date <= end_date]
        return df

    @staticmethod
    def _to_daily_ohlcv(prices: list[list], volumes: list[list]) -> pd.DataFrame:
        """
        Aggregate intraday [timestamp_ms, value] points into daily OHLCV bars.

        open/high/low/close come from the intraday prices; volume_to is the last
        24h-volume reading of the day (CoinGecko volume is denominated in the
        quote currency); volume_from is the implied base-asset volume.
        """
        price_series = pd.Series(
            [p[1] for p in prices],
            index=pd.to_datetime([p[0] for p in prices], unit="ms", utc=True),
        ).sort_index()
        vol_series = pd.Series(
            [v[1] for v in volumes],
            index=pd.to_datetime([v[0] for v in volumes], unit="ms", utc=True),
        ).sort_index()

        daily = price_series.resample("1D").agg(["first", "max", "min", "last"])
        daily.columns = ["open", "high", "low", "close"]
        daily["volume_to"] = vol_series.resample("1D").last()
        daily = daily.dropna(subset=["close"])
        daily["volume_to"] = daily["volume_to"].fillna(0.0)
        daily["volume_from"] = daily.apply(
            lambda r: r["volume_to"] / r["close"] if r["close"] else 0.0, axis=1
        )
        daily.index = daily.index.tz_localize(None)
        daily.index.name = "date"
        return daily[_OHLCV_COLUMNS]

    # ------------------------------------------------------------------ #
    # Health / availability
    # ------------------------------------------------------------------ #

    def ping(self) -> bool:
        """Return True if the CoinGecko API responds."""
        try:
            self._request("/ping")
            return True
        except PriceProviderError:
            return False

    def check_histoday_availability(
        self,
        symbol: str,
        vs_currency: str = "BTC",
        provider_id: str | None = None,
        **kwargs: object,
    ) -> dict[str, str]:
        """
        Check whether recent daily data is available for a coin/quote pair.

        Makes a minimal market_chart request and reports why it failed, if so.
        """
        try:
            coin_id = self._resolve_id(symbol, provider_id)
        except CoinGeckoError as e:
            return {"available": "", "reason": str(e)}

        try:
            data = self._request(
                f"/coins/{coin_id}/market_chart",
                params={"vs_currency": vs_currency.lower(), "days": 2},
            )
            if data.get("prices"):
                return {"available": "yes", "reason": f"{symbol}/{vs_currency} available"}
            return {
                "available": "",
                "reason": f"No recent data for {symbol}/{vs_currency}",
            }
        except CoinGeckoRateLimitError as e:
            return {"available": "", "reason": f"Rate limit exceeded: {e}"}
        except Exception as e:  # noqa: BLE001 - report any failure as the reason
            return {"available": "", "reason": f"Error checking pair: {e}"}

"""
Data fetching orchestration for Halvix.

Coordinates API calls, caching, and filtering to build the coin dataset.

Data source: CryptoCompare (single source of truth)
- Top coins by market cap for coin discovery
- Historical price data with full history
- Volume data for TOTAL2 calculation

Features:
- Incremental fetching: Only downloads new data since last cache
- Yesterday as end date: Avoids incomplete intraday data
"""

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from tqdm import tqdm

from analysis.filters import CoinFilter
from api.cryptocompare import CryptoCompareClient, CryptoCompareError
from config import (
    COINS_TO_DOWNLOAD_JSON,
    DAYS_AFTER_HALVING,
    DAYS_BEFORE_HALVING,
    HALVING_DATES,
    NO_USD_DATA_CSV,
    PROCESSED_DIR,
    QUOTE_CURRENCIES,
    TOP_N_BY_MARKETCAP_TO_FETCH,
    USE_YESTERDAY_AS_END_DATE,
)
from data.cache import FileCache, PriceDataCache
from utils.logging import get_logger

# Module logger
logger = get_logger(__name__)


class FetcherError(Exception):
    """Base exception for data fetcher errors."""

    pass


@dataclass
class FetchResult:
    """Result of a data fetch operation."""

    success: bool
    message: str
    coins_requested: int = 0  # How many coins we asked the API for (also the cap)
    coins_fetched: int = 0  # How many coins had USD data and were returned
    coins_no_usd_data: int = 0  # How many coins were missing USD data from API
    coins_no_usd_filtered: int = 0  # How many no-USD coins were filtered (stablecoins, etc.)
    coins_no_usd_accepted: int = 0  # How many no-USD coins were included (after cap)
    coins_no_usd_capped: int = 0  # How many no-USD coins were excluded due to cap
    coins_filtered: int = 0  # How many USD coins were filtered out (stablecoins, wrapped, etc.)
    coins_accepted: int = 0  # Total coins accepted for download (USD + no-USD, capped at requested)
    coins_symbol_replaced: int = 0  # How many coins had their symbol recycled (name changed)
    errors: list[str] | None = None


class DataFetcher:
    """
    Orchestrates data fetching from CryptoCompare API.

    Data source: CryptoCompare (single source)
    - Top coins by market cap
    - Historical price data (full history, no time limit)
    - Volume data for TOTAL2 calculation

    Workflow:
    1. Fetch top N coins by market cap
    2. Filter out wrapped/staked/bridged tokens
    3. Cache the filtered coin list
    4. Fetch historical prices for each coin
    """

    def __init__(
        self,
        client: CryptoCompareClient | None = None,
        cache: FileCache | None = None,
        price_cache: PriceDataCache | None = None,
        coin_filter: CoinFilter | None = None,
    ):
        """
        Initialize the data fetcher.

        Args:
            client: CryptoCompare API client (default: new instance)
            cache: File cache for API responses (default: new instance)
            price_cache: Price data cache (default: new instance)
            coin_filter: Coin filter (default: new instance)
        """
        self.client = client or CryptoCompareClient()
        self.cache = cache or FileCache()
        self.price_cache = price_cache or PriceDataCache()
        self.coin_filter = coin_filter or CoinFilter()
        self.no_usd_filter: CoinFilter | None = None  # Filter for coins without USD data

        # Calculate the date range needed for all halving cycles
        # First halving minus DAYS_BEFORE to last halving plus DAYS_AFTER
        self.history_start_date = HALVING_DATES[0] - timedelta(days=DAYS_BEFORE_HALVING)

        # End date: always yesterday (today's data is incomplete)
        # We fetch all available data; the analysis window limits apply later
        # during visualization, not during data fetching
        if USE_YESTERDAY_AS_END_DATE:
            self.history_end_date = date.today() - timedelta(days=1)
        else:
            # For testing: use analysis end date
            self.history_end_date = HALVING_DATES[-1] + timedelta(days=DAYS_AFTER_HALVING)

    def fetch_top_coins(
        self,
        n: int = TOP_N_BY_MARKETCAP_TO_FETCH,
        use_cache: bool = True,
        cache_key: str = "top_coins",
        track_no_data: bool = False,
    ) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], list[dict]]:
        """
        Fetch top N coins by market cap.

        Args:
            n: Number of coins to fetch
            use_cache: Whether to use cached data if available
            cache_key: Key for caching the coin list
            track_no_data: If True, also return coins without USD data

        Returns:
            If track_no_data=False: List of coin dictionaries
            If track_no_data=True: Tuple of (coin_dicts, coins_without_usd_data)
        """
        if use_cache and not track_no_data:
            cached = self.cache.get_json(f"{cache_key}_{n}")
            if cached is not None:
                return cached

        result = self.client.get_top_coins_by_market_cap(n=n, track_no_data=track_no_data)

        if track_no_data:
            coins, coins_without_data = result
            coin_dicts = [coin.to_dict() for coin in coins]
            self.cache.set_json(f"{cache_key}_{n}", coin_dicts)
            return coin_dicts, coins_without_data
        else:
            coins = result
            coin_dicts = [coin.to_dict() for coin in coins]
            self.cache.set_json(f"{cache_key}_{n}", coin_dicts)
            return coin_dicts

    def fetch_and_filter_coins(
        self,
        n: int = TOP_N_BY_MARKETCAP_TO_FETCH,
        use_cache: bool = True,
        export_skipped: bool = True,
    ) -> FetchResult:
        """
        Fetch top N coins and determine which should be downloaded.

        Two sources of coins:
        1. Coins WITH USD data: have market cap, can be ranked, filtered, and downloaded
        2. Coins WITHOUT USD data: no market cap from API, but may have BTC pairs available

        For both sources, applies the same filtering:
        - Skips: stablecoins, wrapped/staked/bridged, BTC derivatives
        - Downloads: BTC (USD pair) and all other coins (BTC pairs)

        Args:
            n: Number of coins to fetch
            use_cache: Whether to use cached data
            export_skipped: If True, export skipped coins to CSV

        Returns:
            FetchResult with statistics
        """
        try:
            # Reset filter to clear previous runs
            self.coin_filter.reset()

            # Fetch coins with tracking of coins without USD data
            result = self.fetch_top_coins(n=n, use_cache=use_cache, track_no_data=True)
            all_coins, coins_without_usd = result

            # --- Process coins WITH USD data ---
            coins_to_download = self.coin_filter.get_coins_to_download(
                all_coins,
                record_skipped=True,
            )
            usd_coins_filtered = len(self.coin_filter.skipped_coins)

            # Mark these coins as having USD data
            for coin in coins_to_download:
                coin["has_usd_data"] = True

            # --- Process coins WITHOUT USD data ---
            # Convert to same dict format and filter using same logic
            no_usd_coins_as_dicts = self._convert_no_usd_coins(coins_without_usd)

            # Use a fresh filter instance to get separate counts
            self.no_usd_filter = self.coin_filter.__class__()
            no_usd_accepted = self.no_usd_filter.get_coins_to_download(
                no_usd_coins_as_dicts,
                record_skipped=True,
            )
            no_usd_filtered = len(self.no_usd_filter.skipped_coins)

            # Mark these coins as NOT having USD data (will use BTC pairs only)
            for coin in no_usd_accepted:
                coin["has_usd_data"] = False

            # --- Cap total coins at n ---
            # USD coins have priority (they have actual market cap data)
            # No-USD coins fill remaining slots up to the requested limit
            remaining_slots = max(0, n - len(coins_to_download))
            no_usd_included = no_usd_accepted[:remaining_slots]
            no_usd_capped = len(no_usd_accepted) - len(no_usd_included)

            if no_usd_capped > 0:
                logger.info(
                    "Capped no-USD coins: %d included, %d excluded to meet limit of %d",
                    len(no_usd_included),
                    no_usd_capped,
                    n,
                )

            # Combine both lists (respecting the cap)
            all_coins_to_download = coins_to_download + no_usd_included

            # Export skipped coins for review (from USD coins only, main source)
            if export_skipped:
                self.coin_filter.export_skipped_coins_csv()

            # Detect symbol replacements by name change before overwriting metadata
            replacements = self._detect_symbol_replacements_by_name(all_coins_to_download)

            # Save combined coins to download list
            self._save_coins_to_download(all_coins_to_download)

            # Save coins without USD data for documentation (before filtering)
            self._save_coins_without_usd_data(coins_without_usd)

            return FetchResult(
                success=True,
                message=f"Successfully fetched and filtered {len(all_coins_to_download)} coins",
                coins_requested=n,
                coins_fetched=len(all_coins),
                coins_no_usd_data=len(coins_without_usd),
                coins_no_usd_filtered=no_usd_filtered,
                coins_no_usd_accepted=len(no_usd_included),  # Only those actually included
                coins_no_usd_capped=no_usd_capped,  # How many were excluded by cap
                coins_filtered=usd_coins_filtered,
                coins_accepted=len(all_coins_to_download),
                coins_symbol_replaced=len(replacements),
            )

        except CryptoCompareError as e:
            return FetchResult(
                success=False,
                message=f"API error: {e}",
                errors=[str(e)],
            )
        except Exception as e:
            return FetchResult(
                success=False,
                message=f"Unexpected error: {e}",
                errors=[str(e)],
            )

    def _convert_no_usd_coins(self, coins_without_usd: list[dict]) -> list[dict]:
        """
        Convert coins without USD data to the standard coin dict format.

        These coins came from the market cap API but had no USD price data.
        We convert them to match the format from Coin.to_dict() so they can
        be processed by the same filtering logic.

        Args:
            coins_without_usd: List of dicts with {symbol, name, rank}

        Returns:
            List of coin dicts in standard format with has_usd_data=False
        """
        return [
            {
                "id": coin["symbol"].lower(),
                "symbol": coin["symbol"],
                "name": coin["name"],
                "market_cap": 0,  # Unknown - no USD data
                "market_cap_rank": coin.get("rank", 0),
                "current_price": 0,
                "volume_24h": 0,
                "circulating_supply": 0,
            }
            for coin in coins_without_usd
        ]

    def _save_coins_to_download(self, coins: list[dict]) -> Path:
        """Save the coins to download list to JSON."""
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

        with open(COINS_TO_DOWNLOAD_JSON, "w", encoding="utf-8") as f:
            json.dump(coins, f, indent=2)

        return COINS_TO_DOWNLOAD_JSON

    def _save_coins_without_usd_data(self, coins: list[dict]) -> Path:
        """Save coins that were returned by API but without USD price data."""
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

        if coins:
            df = pd.DataFrame(coins)
            df.to_csv(NO_USD_DATA_CSV, index=False)
        else:
            # Write empty CSV with headers
            pd.DataFrame(columns=["symbol", "name", "rank"]).to_csv(NO_USD_DATA_CSV, index=False)

        return NO_USD_DATA_CSV

    def _detect_symbol_replacements_by_name(self, new_coins: list[dict]) -> list[dict]:
        """
        Detect symbol recycling by comparing coin names against previous metadata.

        CryptoCompare sometimes reassigns a symbol to a different project (e.g.,
        LIT changed from Litentry to Lighter). Price-ratio detection misses this
        when both tokens trade at similar price levels. Comparing names catches it.

        When a name change is detected, cached price data is deleted so that
        fetch-prices downloads only the new token's history.

        Args:
            new_coins: The new coins list about to be saved

        Returns:
            List of dicts with {id, old_name, new_name} for each replacement
        """
        if not COINS_TO_DOWNLOAD_JSON.exists():
            return []

        try:
            with open(COINS_TO_DOWNLOAD_JSON, encoding="utf-8") as f:
                old_coins = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

        old_names = {coin["id"]: coin.get("name", "") for coin in old_coins}
        replacements = []

        for coin in new_coins:
            coin_id = coin["id"]
            new_name = coin.get("name", "")
            old_name = old_names.get(coin_id)

            if old_name is not None and old_name != new_name:
                # Delete cached price data for all quote currencies
                for vs_currency in QUOTE_CURRENCIES:
                    self.price_cache.delete_prices(coin_id, vs_currency)

                replacements.append({"id": coin_id, "old_name": old_name, "new_name": new_name})
                logger.warning(
                    "Symbol replacement detected: %s renamed from '%s' to '%s'"
                    " — deleted cached price data",
                    coin_id.upper(),
                    old_name,
                    new_name,
                )

        return replacements

    def load_coins_to_download(self) -> list[dict]:
        """Load the previously saved coins to download list."""
        if not COINS_TO_DOWNLOAD_JSON.exists():
            raise FetcherError("No coins to download found. Run fetch_and_filter_coins first.")

        with open(COINS_TO_DOWNLOAD_JSON, encoding="utf-8") as f:
            return json.load(f)

    def fetch_coin_prices(
        self,
        coin_id: str,
        symbol: str,
        vs_currency: str = "BTC",
        use_cache: bool = True,
        incremental: bool = True,
    ) -> pd.DataFrame:
        """
        Fetch historical price data for a single coin-pair.

        Supports incremental fetching: if cached data exists, only fetch new data
        from the last cached date to yesterday.

        Files are stored as {coin_id}-{vs_currency}.parquet (e.g., eth-btc.parquet).

        Note: BTC-BTC pair is skipped as it doesn't make sense (BTC priced in BTC = 1.0).

        Args:
            coin_id: Coin ID (lowercase symbol)
            symbol: Coin symbol for CryptoCompare (e.g., "ETH")
            vs_currency: Quote currency (default: "BTC")
            use_cache: Whether to check cache first
            incremental: If True and cache exists, only fetch new data

        Returns:
            DataFrame with date index and OHLCV columns
        """
        # Use symbol for CryptoCompare (uppercase)
        symbol = symbol.upper()
        vs_currency = vs_currency.upper()

        # Skip BTC-BTC pair - it doesn't make sense (BTC priced in BTC = 1.0)
        if coin_id.lower() == "btc" and vs_currency == "BTC":
            logger.debug("Skipping BTC-BTC pair (doesn't make sense)")
            return pd.DataFrame()

        # Calculate end date (yesterday for complete data)
        yesterday = date.today() - timedelta(days=1)
        effective_end_date = min(self.history_end_date, yesterday)

        # Check cache for incremental update
        if use_cache and incremental:
            cached = self.price_cache.get_prices(coin_id, vs_currency)

            if cached is not None and not cached.empty:
                last_cached_date = cached.index.max().date()

                # If cache is up to date, return it
                if last_cached_date >= effective_end_date:
                    return cached

                # Incremental: only fetch new data since last cache
                fetch_start = last_cached_date + timedelta(days=1)

                if fetch_start > effective_end_date:
                    return cached

                try:
                    new_data = self.client.get_full_daily_history(
                        symbol=symbol,
                        vs_currency=vs_currency,
                        start_date=fetch_start,
                        end_date=effective_end_date,
                        show_progress=False,
                    )

                    if not new_data.empty:
                        # Merge with existing cache
                        combined = pd.concat([cached, new_data])
                        combined = combined[~combined.index.duplicated(keep="last")]
                        combined = combined.sort_index()
                        self.price_cache.set_prices(coin_id, combined, vs_currency)
                        return combined

                    return cached

                except CryptoCompareError:
                    # On error, return existing cache
                    return cached

        # No cache, non-incremental mode, or cache miss - fetch full history
        try:
            df = self.client.get_full_daily_history(
                symbol=symbol,
                vs_currency=vs_currency,
                start_date=self.history_start_date,
                end_date=effective_end_date,
                show_progress=False,
            )

            # Cache the result
            if not df.empty:
                self.price_cache.set_prices(coin_id, df, vs_currency)

            return df

        except CryptoCompareError:
            # Return empty DataFrame on error
            return pd.DataFrame()

    def fetch_all_prices(
        self,
        coins: list[dict] | None = None,
        vs_currencies: list[str] | None = None,
        use_cache: bool = True,
        incremental: bool = True,
        show_progress: bool = True,
    ) -> dict[str, dict[str, pd.DataFrame]]:
        """
        Fetch price data for all accepted coins against multiple quote currencies.

        Fetches full historical data needed for halving cycle analysis
        (from 550 days before first halving to 950 days after last halving).

        Supports incremental updates: if cached data exists, only fetches
        new data from the last cached date to yesterday.

        Files are stored as {coin_id}-{vs_currency}.parquet (e.g., eth-btc.parquet).

        Args:
            coins: List of coin dicts (default: load from accepted_coins.json)
            vs_currencies: List of quote currencies (default: QUOTE_CURRENCIES from config)
            use_cache: Whether to use cache
            incremental: If True, only fetch new data since last cache
            show_progress: Show progress bar

        Returns:
            Nested dictionary: {coin_id: {quote_currency: DataFrame}}
        """
        if coins is None:
            coins = self.load_coins_to_download()

        if vs_currencies is None:
            vs_currencies = QUOTE_CURRENCIES

        results: dict[str, dict[str, pd.DataFrame]] = {}
        errors = []

        # Calculate total iterations for progress bar
        total_iterations = len(coins) * len(vs_currencies)

        # Separate description based on mode
        desc = f"Fetching prices ({', '.join(vs_currencies)})"
        if incremental:
            desc += " (incremental)"

        if show_progress:
            pbar = tqdm(total=total_iterations, desc=desc)
        else:
            pbar = None

        for coin in coins:
            coin_id = coin["id"]
            symbol = coin.get("symbol", coin_id)
            results[coin_id] = {}

            for vs_currency in vs_currencies:
                try:
                    df = self.fetch_coin_prices(
                        coin_id=coin_id,
                        symbol=symbol,
                        vs_currency=vs_currency,
                        use_cache=use_cache,
                        incremental=incremental,
                    )

                    if not df.empty:
                        results[coin_id][vs_currency] = df

                except CryptoCompareError as e:
                    errors.append(f"{coin_id}-{vs_currency} ({symbol}): {e}")
                except Exception as e:
                    errors.append(f"{coin_id}-{vs_currency} ({symbol}): Unexpected error - {e}")

                if pbar:
                    pbar.update(1)

        if pbar:
            pbar.close()

        if show_progress and errors:
            logger.warning("%d errors occurred:", len(errors))
            for error in errors[:10]:
                logger.warning("  - %s", error)
            if len(errors) > 10:
                logger.warning("  ... and %d more", len(errors) - 10)

        return results

    def fetch_all_prices_single_currency(
        self,
        coins: list[dict] | None = None,
        vs_currency: str = "BTC",
        use_cache: bool = True,
        incremental: bool = True,
        show_progress: bool = True,
    ) -> dict[str, pd.DataFrame]:
        """
        Fetch price data for all accepted coins against a single quote currency.

        This is a convenience method that returns a flat dictionary.

        Args:
            coins: List of coin dicts (default: load from accepted_coins.json)
            vs_currency: Quote currency (default: "BTC")
            use_cache: Whether to use cache
            incremental: If True, only fetch new data since last cache
            show_progress: Show progress bar

        Returns:
            Dictionary mapping coin_id to price DataFrame
        """
        nested = self.fetch_all_prices(
            coins=coins,
            vs_currencies=[vs_currency],
            use_cache=use_cache,
            incremental=incremental,
            show_progress=show_progress,
        )

        # Flatten the nested dictionary
        return {
            coin_id: currency_data.get(vs_currency)
            for coin_id, currency_data in nested.items()
            if vs_currency in currency_data
        }

    def get_filter_summary(self) -> dict[str, Any]:
        """Get a summary of the last filtering operation (USD coins)."""
        return {
            "skipped_count": len(self.coin_filter.skipped_coins),
            "by_reason": self.coin_filter.get_skipped_summary(),
            "skipped_coins": [
                {
                    "id": c.coin_id,
                    "name": c.name,
                    "symbol": c.symbol,
                    "reason": c.reason,
                }
                for c in self.coin_filter.skipped_coins
            ],
        }

    def get_no_usd_filter_summary(self) -> dict[str, Any]:
        """Get a summary of the filtering operation for coins without USD data."""
        if self.no_usd_filter is None:
            return {"skipped_count": 0, "by_reason": {}, "skipped_coins": []}
        return {
            "skipped_count": len(self.no_usd_filter.skipped_coins),
            "by_reason": self.no_usd_filter.get_skipped_summary(),
            "skipped_coins": [
                {
                    "id": c.coin_id,
                    "name": c.name,
                    "symbol": c.symbol,
                    "reason": c.reason,
                }
                for c in self.no_usd_filter.skipped_coins
            ],
        }

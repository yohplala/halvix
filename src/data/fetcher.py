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
from analysis.filters import TokenFilter
from api.cryptocompare import CryptoCompareClient, CryptoCompareError
from config import (
    ACCEPTED_COINS_JSON,
    DAYS_AFTER_HALVING,
    DAYS_BEFORE_HALVING,
    HALVING_DATES,
    MIN_DATA_DATE,
    PROCESSED_DIR,
    TOP_N_COINS,
    USE_YESTERDAY_AS_END_DATE,
)
from tqdm import tqdm
from utils.logging import get_logger

from data.cache import FileCache, PriceDataCache

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
    coins_fetched: int = 0
    coins_filtered: int = 0
    coins_accepted: int = 0
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
        token_filter: TokenFilter | None = None,
    ):
        """
        Initialize the data fetcher.

        Args:
            client: CryptoCompare API client (default: new instance)
            cache: File cache for API responses (default: new instance)
            price_cache: Price data cache (default: new instance)
            token_filter: Token filter (default: new instance)
        """
        self.client = client or CryptoCompareClient()
        self.cache = cache or FileCache()
        self.price_cache = price_cache or PriceDataCache()
        self.token_filter = token_filter or TokenFilter()

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
        n: int = TOP_N_COINS,
        use_cache: bool = True,
        cache_key: str = "top_coins",
    ) -> list[dict[str, Any]]:
        """
        Fetch top N coins by market cap.

        Args:
            n: Number of coins to fetch
            use_cache: Whether to use cached data if available
            cache_key: Key for caching the coin list

        Returns:
            List of coin dictionaries
        """
        if use_cache:
            cached = self.cache.get_json(f"{cache_key}_{n}")
            if cached is not None:
                return cached

        coins = self.client.get_top_coins_by_market_cap(n=n)
        coin_dicts = [coin.to_dict() for coin in coins]

        self.cache.set_json(f"{cache_key}_{n}", coin_dicts)

        return coin_dicts

    def fetch_and_filter_coins(
        self,
        n: int = TOP_N_COINS,
        use_cache: bool = True,
        export_filtered: bool = True,
    ) -> FetchResult:
        """
        Fetch top N coins and apply filtering.

        Args:
            n: Number of coins to fetch
            use_cache: Whether to use cached data
            export_filtered: If True, export filtered tokens to CSV

        Returns:
            FetchResult with statistics
        """
        try:
            # Reset filter to clear previous runs
            self.token_filter.reset()

            # Fetch coins
            all_coins = self.fetch_top_coins(n=n, use_cache=use_cache)

            # Apply filtering
            filtered_coins = self.token_filter.filter_coins(
                all_coins,
                record_filtered=True,
            )

            # Export rejected coins for review
            if export_filtered:
                self.token_filter.export_rejected_coins_csv()

            # Save accepted coins list
            self._save_accepted_coins(filtered_coins)

            return FetchResult(
                success=True,
                message=f"Successfully fetched and filtered {len(filtered_coins)} coins",
                coins_fetched=len(all_coins),
                coins_filtered=len(self.token_filter.filtered_tokens),
                coins_accepted=len(filtered_coins),
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

    def _save_accepted_coins(self, coins: list[dict]) -> Path:
        """Save the accepted coin list to JSON."""
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

        with open(ACCEPTED_COINS_JSON, "w", encoding="utf-8") as f:
            json.dump(coins, f, indent=2)

        return ACCEPTED_COINS_JSON

    def load_accepted_coins(self) -> list[dict]:
        """Load the previously accepted coin list."""
        if not ACCEPTED_COINS_JSON.exists():
            raise FetcherError("No accepted coins found. Run fetch_and_filter_coins first.")

        with open(ACCEPTED_COINS_JSON, encoding="utf-8") as f:
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
        Fetch historical price data for a single coin.

        Supports incremental fetching: if cached data exists, only fetch new data
        from the last cached date to yesterday.

        Args:
            coin_id: Coin ID (lowercase symbol)
            symbol: Coin symbol for CryptoCompare (e.g., "ETH")
            vs_currency: Quote currency (default: "BTC")
            use_cache: Whether to check cache first
            incremental: If True and cache exists, only fetch new data

        Returns:
            DataFrame with date index and price/volume columns
        """
        # Use symbol for CryptoCompare (uppercase)
        symbol = symbol.upper()

        # Calculate end date (yesterday for complete data)
        yesterday = date.today() - timedelta(days=1)
        effective_end_date = min(self.history_end_date, yesterday)

        # Check cache for incremental update
        if use_cache and incremental:
            cached = self.price_cache.get_prices(coin_id)

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
                        vs_currency=vs_currency.upper(),
                        start_date=fetch_start,
                        end_date=effective_end_date,
                        show_progress=False,
                    )

                    if not new_data.empty:
                        # Merge with existing cache
                        combined = pd.concat([cached, new_data])
                        combined = combined[~combined.index.duplicated(keep="last")]
                        combined = combined.sort_index()
                        self.price_cache.set_prices(coin_id, combined)
                        return combined

                    return cached

                except CryptoCompareError:
                    # On error, return existing cache
                    return cached

        # No cache, non-incremental mode, or cache miss - fetch full history
        try:
            df = self.client.get_full_daily_history(
                symbol=symbol,
                vs_currency=vs_currency.upper(),
                start_date=self.history_start_date,
                end_date=effective_end_date,
                show_progress=False,
            )

            # Cache the result
            if not df.empty:
                self.price_cache.set_prices(coin_id, df)

            return df

        except CryptoCompareError:
            # Return empty DataFrame on error
            return pd.DataFrame()

    def fetch_all_prices(
        self,
        coins: list[dict] | None = None,
        vs_currency: str = "BTC",
        use_cache: bool = True,
        incremental: bool = True,
        show_progress: bool = True,
    ) -> dict[str, pd.DataFrame]:
        """
        Fetch price data for all accepted coins.

        Fetches full historical data needed for halving cycle analysis
        (from 550 days before first halving to 880 days after last halving).

        Supports incremental updates: if cached data exists, only fetches
        new data from the last cached date to yesterday.

        Args:
            coins: List of coin dicts (default: load from accepted_coins.json)
            vs_currency: Quote currency (default: "BTC")
            use_cache: Whether to use cache
            incremental: If True, only fetch new data since last cache
            show_progress: Show progress bar

        Returns:
            Dictionary mapping coin_id to price DataFrame
        """
        if coins is None:
            coins = self.load_accepted_coins()

        results = {}
        errors = []

        # Separate description based on mode
        desc = "Fetching prices"
        if incremental:
            desc += " (incremental)"

        iterator = tqdm(coins, desc=desc) if show_progress else coins

        for coin in iterator:
            coin_id = coin["id"]
            symbol = coin.get("symbol", coin_id)

            try:
                df = self.fetch_coin_prices(
                    coin_id=coin_id,
                    symbol=symbol,
                    vs_currency=vs_currency,
                    use_cache=use_cache,
                    incremental=incremental,
                )

                if not df.empty:
                    results[coin_id] = df

            except CryptoCompareError as e:
                errors.append(f"{coin_id} ({symbol}): {e}")
            except Exception as e:
                errors.append(f"{coin_id} ({symbol}): Unexpected error - {e}")

        if show_progress and errors:
            logger.warning("%d errors occurred:", len(errors))
            for error in errors[:10]:
                logger.warning("  - %s", error)
            if len(errors) > 10:
                logger.warning("  ... and %d more", len(errors) - 10)

        return results

    def get_coins_with_data_before(
        self,
        cutoff_date: date = MIN_DATA_DATE,
        coins: list[dict] | None = None,
    ) -> list[dict]:
        """
        Filter coins to only those with price data before a cutoff date.

        Args:
            cutoff_date: Only include coins with data before this date
            coins: List of coins to check (default: load accepted coins)

        Returns:
            Filtered list of coins with early data
        """
        if coins is None:
            coins = self.load_accepted_coins()

        valid_coins = []

        for coin in coins:
            coin_id = coin["id"]
            df = self.price_cache.get_prices(coin_id)

            if df is not None and not df.empty:
                first_date = df.index.min().date()
                if first_date < cutoff_date:
                    valid_coins.append(coin)

        return valid_coins

    def get_filter_summary(self) -> dict[str, Any]:
        """Get a summary of the last filtering operation."""
        return {
            "filtered_count": len(self.token_filter.filtered_tokens),
            "by_reason": self.token_filter.get_filtered_summary(),
            "filtered_tokens": [
                {
                    "id": t.coin_id,
                    "name": t.name,
                    "symbol": t.symbol,
                    "reason": t.reason,
                }
                for t in self.token_filter.filtered_tokens
            ],
        }

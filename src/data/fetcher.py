"""
Data fetching orchestration for Halvix.

Coordinates provider API calls, caching, and filtering to build the coin
dataset. The price provider (CoinGecko by default) is injected via the
``PriceProvider`` abstraction, so this module is provider-agnostic:
- Top coins by market cap for coin discovery
- Historical price data
- Volume data for TOTAL2 calculation

Features:
- Incremental fetching: Only downloads new data since last cache
- Yesterday as end date: Avoids incomplete intraday data
"""

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal, cast, overload

import numpy as np
import pandas as pd
from tqdm import tqdm

from analysis.filters import CoinFilter
from api import get_price_provider
from api.base import PriceProvider, PriceProviderError
from config import (
    COINGECKO_IDENTITY_SEED_JSON,
    COINS_TO_DOWNLOAD_JSON,
    DAYS_AFTER_HALVING,
    DAYS_BEFORE_HALVING,
    HALVING_DATES,
    NO_USD_DATA_CSV,
    PROCESSED_DIR,
    QUOTE_CURRENCIES,
    SPLICE_MAX_GAP_DAYS,
    SPLICE_MAX_LOG_RATIO_STD,
    SPLICE_MIN_OVERLAP_DAYS,
    SPLICE_OVERLAP_DAYS,
    SPLICE_PRICE_MAX_RATIO,
    TOP_N_BY_MARKETCAP_TO_FETCH,
    USE_YESTERDAY_AS_END_DATE,
)
from data.cache import FileCache, PriceDataCache
from data.coin_registry import CoinRegistry
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
    Orchestrates data fetching through the configured price provider.

    Data source: a ``PriceProvider`` (CoinGecko by default)
    - Top coins by market cap
    - Historical price data
    - Volume data for TOTAL2 calculation

    Workflow:
    1. Fetch top N coins by market cap
    2. Filter out wrapped/staked/bridged tokens
    3. Cache the filtered coin list
    4. Fetch historical prices for each coin
    """

    def __init__(
        self,
        client: PriceProvider | None = None,
        cache: FileCache | None = None,
        price_cache: PriceDataCache | None = None,
        coin_filter: CoinFilter | None = None,
        registry: CoinRegistry | None = None,
    ):
        """
        Initialize the data fetcher.

        Args:
            client: Price provider (default: the configured provider, CoinGecko)
            cache: File cache for API responses (default: new instance)
            price_cache: Price data cache (default: new instance)
            coin_filter: Coin filter (default: new instance)
            registry: Cross-provider coin-identity map (default: new instance)
        """
        self.client = client or get_price_provider()
        self.cache = cache or FileCache()
        self.price_cache = price_cache or PriceDataCache()
        self.coin_filter = coin_filter or CoinFilter()
        self.registry = registry or CoinRegistry()
        # Backend identifier used as the registry's top-level key. Read defensively
        # so a spec'd test double (whose ``.name`` is a mock) doesn't poison it.
        client_name = getattr(self.client, "name", None)
        self.provider_name = client_name if isinstance(client_name, str) else "unknown"
        self.no_usd_filter: CoinFilter | None = None  # Filter for coins without USD data
        # Coins whose incremental top-up was skipped because the provider's price
        # for the overlap day disagreed with cached history (suspected mismatch).
        self.splice_mismatches: list[dict] = []

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

    @overload
    def fetch_top_coins(
        self,
        n: int = ...,
        use_cache: bool = ...,
        cache_key: str = ...,
        track_no_data: Literal[False] = False,
    ) -> list[dict[str, Any]]: ...

    @overload
    def fetch_top_coins(
        self,
        n: int = ...,
        use_cache: bool = ...,
        cache_key: str = ...,
        *,
        track_no_data: Literal[True],
    ) -> tuple[list[dict[str, Any]], list[dict]]: ...

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
            coins, coins_without_data = cast("tuple[list[Any], list[dict]]", result)
            coin_dicts = [coin.to_dict() for coin in coins]
            self.cache.set_json(f"{cache_key}_{n}", coin_dicts)
            return coin_dicts, coins_without_data
        else:
            coins = cast("list[Any]", result)
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

        except PriceProviderError as e:
            return FetchResult(
                success=False,
                message=f"API error: {e}",
                errors=[str(e)],
            )
        except Exception as e:
            # Unexpected error — log the full traceback so it is diagnosable.
            logger.exception("Unexpected error in fetch_and_filter_coins")
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

    # If more than this fraction (or count) of coins change name in one run, it
    # is a bulk re-label (e.g. switching data provider, whose naming style
    # differs) rather than genuine symbol reassignments — so cached history is
    # preserved instead of deleted. A real reassignment touches only a handful.
    BULK_RENAME_MIN_COUNT = 20
    BULK_RENAME_FRACTION = 0.05

    def _detect_symbol_replacements_by_name(self, new_coins: list[dict]) -> list[dict]:
        """
        Detect symbol recycling by comparing coin names against previous metadata.

        Providers sometimes reassign a symbol to a different project (e.g.,
        LIT changed from Litentry to Lighter). Price-ratio detection misses this
        when both tokens trade at similar price levels. Comparing names catches it.

        When a genuine reassignment is detected, cached price data is deleted so
        that fetch-prices downloads only the new token's history. A *wholesale*
        rename (many coins at once — e.g. a data-source switch) is treated as a
        naming-style difference and the cache is preserved (see the bulk guard).

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

        old_names = {coin["id"]: (coin.get("name") or "").strip() for coin in old_coins}

        # Collect candidates first so we can distinguish a few real reassignments
        # from a wholesale re-label before touching the cache.
        candidates: list[dict] = []
        for coin in new_coins:
            coin_id = coin["id"]
            new_name = (coin.get("name") or "").strip()
            old_name = old_names.get(coin_id)
            if old_name is not None and old_name != new_name:
                candidates.append({"id": coin_id, "old_name": old_name, "new_name": new_name})

        bulk_threshold = max(
            self.BULK_RENAME_MIN_COUNT, int(len(new_coins) * self.BULK_RENAME_FRACTION)
        )
        if len(candidates) > bulk_threshold:
            logger.warning(
                "%d coins changed name (> %d) — treating as a bulk re-label "
                "(e.g. data-source/naming change); cached price history preserved.",
                len(candidates),
                bulk_threshold,
            )
            return []

        replacements = []
        for cand in candidates:
            coin_id = cand["id"]
            # Delete cached price data for all quote currencies
            for vs_currency in QUOTE_CURRENCIES:
                self.price_cache.delete_prices(coin_id, vs_currency)

            replacements.append(cand)
            logger.warning(
                "Symbol replacement detected: %s renamed from '%s' to '%s'"
                " — deleted cached price data",
                coin_id.upper(),
                cand["old_name"],
                cand["new_name"],
            )

        return replacements

    def load_coins_to_download(self) -> list[dict]:
        """Load the previously saved coins to download list."""
        if not COINS_TO_DOWNLOAD_JSON.exists():
            raise FetcherError("No coins to download found. Run fetch_and_filter_coins first.")

        with open(COINS_TO_DOWNLOAD_JSON, encoding="utf-8") as f:
            return json.load(f)

    def _splice_is_consistent(
        self,
        coin_id: str,
        vs_currency: str,
        cached: pd.DataFrame,
        new_data: pd.DataFrame,
    ) -> bool:
        """
        Verify the provider matches the cached history before splicing.

        Compares the two series over their overlapping days using two signals:
          - level:    median(provider / cached) within the configured ratio band
          - tracking: std(log(provider / cached)) small (the same asset moves
                      proportionally; a different asset drifts apart even if a
                      single day coincidentally matches)

        A failure means the symbol likely maps to a different asset now; the
        top-up is skipped and recorded in ``splice_mismatches`` so cached history
        is not corrupted. Returns True if it is safe to splice.
        """
        overlap = new_data.index.intersection(cached.index)
        if len(overlap) == 0:
            return True  # nothing to compare (provider lacks the overlap)

        old = pd.to_numeric(cached.loc[overlap, "close"], errors="coerce")
        new = pd.to_numeric(new_data.loc[overlap, "close"], errors="coerce")
        valid = (old > 0) & (new > 0) & old.notna() & new.notna()
        old, new = old[valid], new[valid]
        if old.empty:
            return True

        log_ratio = np.log(new.to_numpy() / old.to_numpy())
        median_ratio = float(np.exp(np.median(log_ratio)))
        ratio_std = float(np.std(log_ratio)) if len(log_ratio) >= SPLICE_MIN_OVERLAP_DAYS else 0.0

        level_bad = (
            median_ratio > SPLICE_PRICE_MAX_RATIO or median_ratio < 1.0 / SPLICE_PRICE_MAX_RATIO
        )
        tracking_bad = ratio_std > SPLICE_MAX_LOG_RATIO_STD

        if level_bad or tracking_bad:
            logger.warning(
                "Splice mismatch for %s/%s over %d overlap days: median ratio %.2fx, "
                "log-ratio std %.3f — skipping top-up (symbol may map to a different asset).",
                coin_id.upper(),
                vs_currency,
                len(old),
                median_ratio,
                ratio_std,
            )
            self.splice_mismatches.append(
                {
                    "id": coin_id,
                    "vs_currency": vs_currency,
                    "overlap_days": int(len(old)),
                    "median_ratio": median_ratio,
                    "log_ratio_std": ratio_std,
                    "reason": "level" if level_bad else "tracking",
                }
            )
            return False
        return True

    # ------------------------------------------------------------------ #
    # Cross-provider coin identity (registry)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _native_id(coin: dict) -> str:
        """
        Provider-native identifier used as the registry key for a coin.

        CoinGecko addresses coins by slug (carried as ``provider_id``);
        CryptoCompare addresses them by symbol. The slug, when present, is the
        unique id; otherwise fall back to the upper-cased symbol.
        """
        provider_id = coin.get("provider_id")
        if provider_id:
            return str(provider_id)
        return (coin.get("symbol") or coin["id"]).upper()

    def _bootstrap_registry(self) -> None:
        """
        Seed the registry from the existing cache the first time it is used.

        Every parquet on disk predates the provider migration, so it belongs to
        CryptoCompare (which keys by symbol → stem == lowercase symbol, native id
        == upper symbol). Seeding records that provenance and reserves those
        stems so a later collision allocates a fresh ``symbol-2`` rather than
        clobbering history. A no-op once the ``cryptocompare`` map exists.
        """
        try:
            stems = list(self.price_cache.list_cached_coins())
        except TypeError:  # e.g. a mocked price cache in tests
            return
        if not stems or not all(isinstance(s, str) for s in stems):
            return
        seed = {stem.upper(): stem for stem in stems}
        if self.registry.bootstrap_provider("cryptocompare", seed):
            self.registry.save()
            logger.info(
                "Bootstrapped coin registry from %d cached stems (CryptoCompare).", len(seed)
            )

    def _apply_identity_seed(self) -> None:
        """
        Apply the committed CoinGecko ``slug -> stem`` identity seed (if present).

        This one-time, version-controlled map binds the historical CryptoCompare
        base to stable CoinGecko slugs (renames, name-matches, curated forks). It
        is authoritative, so seeded slugs route deterministically; coins absent
        from it fall back to runtime symbol+price resolution.
        """
        if not COINGECKO_IDENTITY_SEED_JSON.exists():
            return
        try:
            data = json.loads(COINGECKO_IDENTITY_SEED_JSON.read_text(encoding="utf-8"))
            mapping = data.get("coingecko", {})
        except (json.JSONDecodeError, OSError, AttributeError):
            logger.warning("Could not read identity seed at %s.", COINGECKO_IDENTITY_SEED_JSON)
            return
        applied = 0
        for slug, stem in mapping.items():
            if self.registry.get_stem("coingecko", slug) != stem:
                self.registry.set_stem("coingecko", str(slug), str(stem))
                applied += 1
        if applied:
            self.registry.save()
            logger.info("Applied %d CoinGecko identity-seed bindings.", applied)

    def _register_identity(self, provider: str | None, native_id: str | None, stem: str) -> None:
        """Bind (provider, native_id) → stem and persist, if not already bound."""
        if not provider or not native_id:
            return
        if self.registry.get_stem(provider, native_id) == stem:
            return
        self.registry.set_stem(provider, native_id, stem)
        self.registry.save()

    def _fork_stem(
        self, provider: str | None, native_id: str | None, symbol: str, stem: str
    ) -> str | None:
        """
        Allocate a fresh stem when a symbol resolves to a *different* asset.

        Applies only when the tentative stem was the bare symbol (i.e. an asset
        owned by another provider already holds it) and this identity is not yet
        registered. A mismatch on an already-bound stem is a genuine mid-life
        asset swap, handled elsewhere — not a fork. Returns the new stem, or
        None when forking does not apply.
        """
        if not provider or not native_id:
            return None
        if self.registry.get_stem(provider, native_id) is not None:
            return None  # already owns this stem; a mismatch here is a real swap
        if stem != symbol.lower():
            return None  # not a bare-symbol collision
        reserved = set(self.price_cache.list_cached_coins()) | self.registry.all_stems()
        new_stem = self.registry.allocate_stem(symbol, reserved=reserved)
        self.registry.set_stem(provider, native_id, new_stem)
        self.registry.save()
        logger.warning(
            "Symbol collision: %s %r is a different asset than cached %r/* — "
            "storing separately as %r.",
            provider,
            native_id,
            stem,
            new_stem,
        )
        return new_stem

    def fetch_coin_prices(
        self,
        coin_id: str,
        symbol: str,
        vs_currency: str = "BTC",
        use_cache: bool = True,
        incremental: bool = True,
        provider_id: str | None = None,
        provider: str | None = None,
        native_id: str | None = None,
        stem: str | None = None,
    ) -> pd.DataFrame:
        """
        Fetch historical price data for a single coin-pair.

        Supports incremental fetching: if cached data exists, only fetch new data
        from the last cached date to yesterday.

        Files are stored as {stem}-{vs_currency}.parquet (e.g., eth-btc.parquet).
        The *stem* is the cross-provider cache key: usually the lowercase symbol,
        but a fresh ``symbol-2`` when this provider's coin is a different asset
        that happens to share a symbol with already-cached history. When
        ``provider``/``native_id`` are given, the resolved binding is recorded in
        the registry so later runs route to the right file without re-comparing.

        Note: BTC-BTC pair is skipped as it doesn't make sense (BTC priced in BTC = 1.0).

        Args:
            coin_id: Coin ID (lowercase symbol) — used for logging and the BTC skip
            symbol: Coin symbol (e.g., "ETH")
            vs_currency: Quote currency (default: "BTC")
            use_cache: Whether to check cache first
            incremental: If True and cache exists, only fetch new data
            provider_id: Provider-native coin id (e.g. CoinGecko slug)
            provider: Backend name for the registry binding (e.g. "coingecko")
            native_id: Provider-native registry key (slug or upper symbol)
            stem: On-disk cache key (default: ``coin_id``)

        Returns:
            DataFrame with date index and OHLCV columns
        """
        # Providers expect the uppercase symbol
        symbol = symbol.upper()
        vs_currency = vs_currency.upper()
        stem = (stem or coin_id).lower()

        # A registered identity is an authoritative (provider, native_id) -> stem
        # binding (from the committed seed or a prior verified splice). For these
        # we trust the binding and skip the price-equivalence gate, which exists
        # only to DISCOVER identity for unregistered tentative-symbol stems (and
        # would mis-fire on volatile micro-price coins, e.g. LUNC at ~3e-10).
        registered = bool(provider and native_id and self.registry.get_stem(provider, native_id))

        # Skip BTC-BTC pair - it doesn't make sense (BTC priced in BTC = 1.0)
        if coin_id.lower() == "btc" and vs_currency == "BTC":
            logger.debug("Skipping BTC-BTC pair (doesn't make sense)")
            return pd.DataFrame()

        # Calculate end date (yesterday for complete data)
        yesterday = date.today() - timedelta(days=1)
        effective_end_date = min(self.history_end_date, yesterday)

        # Check cache for incremental update
        if use_cache and incremental:
            cached = self.price_cache.get_prices(stem, vs_currency)

            if cached is not None and not cached.empty:
                last_cached_date = cached.index.max().date()

                # If cache is up to date, return it. (Identity is left
                # unregistered here: a fresh cache offers no overlap to confirm
                # this provider's coin is the same asset as the stem's history.)
                if last_cached_date >= effective_end_date:
                    return cached

                # Incremental: fetch starting an overlap window BEFORE the last
                # cached day so the splice can be validated against multiple
                # already-cached days before appending (one provider call covers
                # the whole window regardless of how far back it starts).
                fetch_start = max(
                    self.history_start_date,
                    last_cached_date - timedelta(days=SPLICE_OVERLAP_DAYS),
                )

                try:
                    new_data = self.client.get_full_daily_history(
                        symbol=symbol,
                        vs_currency=vs_currency,
                        start_date=fetch_start,
                        end_date=effective_end_date,
                        show_progress=False,
                        provider_id=provider_id,
                    )

                    if new_data.empty:
                        return cached

                    new_rows = new_data[new_data.index > cached.index.max()]
                    if new_rows.empty:
                        return cached

                    # Refuse to splice unless the new data both OVERLAPS the cache
                    # (so price equivalence can be verified) and CONNECTS to it
                    # without a gap. Truncated provider responses (common on the
                    # keyless tier) otherwise create gaps or unverified splices.
                    overlap = new_data.index.intersection(cached.index)
                    gap_days = (new_rows.index.min().date() - last_cached_date).days
                    if len(overlap) == 0 or gap_days > SPLICE_MAX_GAP_DAYS:
                        why = "no overlap to verify" if len(overlap) == 0 else f"{gap_days}-day gap"
                        logger.warning(
                            "Skipping %s/%s top-up (%s): provider window %s..%s does not safely "
                            "connect to cached history ending %s.",
                            coin_id.upper(),
                            vs_currency,
                            why,
                            new_data.index.min().date(),
                            new_data.index.max().date(),
                            last_cached_date,
                        )
                        self.splice_mismatches.append(
                            {
                                "id": stem,
                                "vs_currency": vs_currency,
                                "reason": "no_overlap" if len(overlap) == 0 else "gap",
                                "overlap_days": int(len(overlap)),
                                "gap_days": int(gap_days),
                            }
                        )
                        return cached

                    # Guard against splicing a DIFFERENT asset onto the history —
                    # but only for unregistered stems still being identified. A
                    # registered binding is authoritative and trusted as-is.
                    if not registered and not self._splice_is_consistent(
                        stem, vs_currency, cached, new_data
                    ):
                        # A price mismatch on a bare-symbol stem means this
                        # provider's coin is a DIFFERENT asset sharing the symbol.
                        # Fork to its own stem and fetch it there from scratch.
                        forked = self._fork_stem(provider, native_id, symbol, stem)
                        if forked is not None and forked != stem:
                            return self.fetch_coin_prices(
                                coin_id=coin_id,
                                symbol=symbol,
                                vs_currency=vs_currency,
                                use_cache=use_cache,
                                incremental=incremental,
                                provider_id=provider_id,
                                provider=provider,
                                native_id=native_id,
                                stem=forked,
                            )
                        return cached

                    # Append only strictly-newer rows; never overwrite cached
                    # historical values (keep="first" would, so we slice instead).
                    combined = pd.concat([cached, new_rows]).sort_index()
                    combined = combined[~combined.index.duplicated(keep="last")]
                    self.price_cache.set_prices(stem, combined, vs_currency)
                    # Verified same asset over the overlap → bind identity → stem.
                    self._register_identity(provider, native_id, stem)
                    return combined

                except PriceProviderError:
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
                provider_id=provider_id,
            )

            # Cache the result
            if not df.empty:
                self.price_cache.set_prices(stem, df, vs_currency)
                # We created (or own) this stem → bind the identity to it.
                self._register_identity(provider, native_id, stem)

            return df

        except PriceProviderError:
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

        Files are stored as {stem}-{vs_currency}.parquet (e.g., eth-btc.parquet),
        where the stem is resolved per coin through the cross-provider registry.

        Args:
            coins: List of coin dicts (default: load from coins_to_download.json)
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

        # Seed the registry from existing (CryptoCompare-era) parquets once, so
        # collisions fork instead of clobbering pre-migration history, then apply
        # the committed CoinGecko slug->stem identity seed (renames/forks).
        self._bootstrap_registry()
        self._apply_identity_seed()

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
            provider_id = coin.get("provider_id")
            native_id = self._native_id(coin)
            results[coin_id] = {}

            for vs_currency in vs_currencies:
                # Resolve the stem each pass: a known identity routes straight to
                # its file; an unknown one tentatively uses the symbol, and a
                # fork made during the BTC pass is picked up here for USD.
                stem = self.registry.get_stem(self.provider_name, native_id) or coin_id.lower()
                try:
                    df = self.fetch_coin_prices(
                        coin_id=coin_id,
                        symbol=symbol,
                        vs_currency=vs_currency,
                        use_cache=use_cache,
                        incremental=incremental,
                        provider_id=provider_id,
                        provider=self.provider_name,
                        native_id=native_id,
                        stem=stem,
                    )

                    if not df.empty:
                        results[coin_id][vs_currency] = df

                except PriceProviderError as e:
                    errors.append(f"{coin_id}-{vs_currency} ({symbol}): {e}")
                except Exception as e:
                    # Unexpected — log with traceback now (the loop keeps going).
                    logger.exception("Unexpected error fetching %s-%s", coin_id, vs_currency)
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

        if self.splice_mismatches:
            logger.warning(
                "%d coin(s) skipped on splice safety check (possible symbol→asset "
                "mismatch or truncated window); not appended to history:",
                len(self.splice_mismatches),
            )
            for m in self.splice_mismatches[:10]:
                if "median_ratio" in m:  # price-equivalence failure
                    detail = (
                        f"{m['reason']}: median {m['median_ratio']:.2f}x, "
                        f"log-std {m['log_ratio_std']:.3f} over {m['overlap_days']}d"
                    )
                else:  # contiguity/overlap failure
                    detail = (
                        f"{m['reason']}: overlap={m.get('overlap_days', 0)}d, "
                        f"gap={m.get('gap_days', 0)}d"
                    )
                logger.warning("  - %s/%s: %s", m["id"].upper(), m["vs_currency"], detail)

        return results

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

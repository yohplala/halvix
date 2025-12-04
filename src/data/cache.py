"""
File-based caching for API responses.

Caches coin lists and price data to reduce API calls and enable offline analysis.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from config import CACHE_DIR, CACHE_EXPIRY_SECONDS, PRICES_DIR


class CacheError(Exception):
    """Base exception for cache errors."""

    pass


class FileCache:
    """
    File-based cache for API responses and computed data.

    Supports:
    - JSON caching for coin lists and metadata
    - Parquet caching for time series data
    - Configurable expiry times
    """

    def __init__(
        self,
        cache_dir: Path = CACHE_DIR,
        expiry_seconds: int = CACHE_EXPIRY_SECONDS,
    ):
        """
        Initialize the file cache.

        Args:
            cache_dir: Directory for cache files
            expiry_seconds: Default cache expiry in seconds
        """
        self.cache_dir = cache_dir
        self.expiry_seconds = expiry_seconds
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_cache_path(self, key: str, extension: str = "json") -> Path:
        """Get the file path for a cache key."""
        # Use MD5 hash for long keys
        if len(key) > 100:
            key_hash = hashlib.md5(key.encode()).hexdigest()
            filename = f"{key_hash}.{extension}"
        else:
            # Sanitize the key for filesystem
            safe_key = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
            filename = f"{safe_key}.{extension}"

        return self.cache_dir / filename

    def _is_expired(self, filepath: Path, expiry_seconds: int | None = None) -> bool:
        """Check if a cached file has expired."""
        if not filepath.exists():
            return True

        expiry = expiry_seconds if expiry_seconds is not None else self.expiry_seconds

        # Never expire if expiry is 0 or negative
        if expiry <= 0:
            return False

        mtime = datetime.fromtimestamp(filepath.stat().st_mtime)
        age_seconds = (datetime.now() - mtime).total_seconds()

        return age_seconds > expiry

    def get_json(
        self,
        key: str,
        expiry_seconds: int | None = None,
    ) -> Any | None:
        """
        Get a cached JSON value.

        Args:
            key: Cache key
            expiry_seconds: Override default expiry

        Returns:
            Cached value or None if not found/expired
        """
        filepath = self._get_cache_path(key, "json")

        if self._is_expired(filepath, expiry_seconds):
            return None

        try:
            with open(filepath, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def set_json(self, key: str, value: Any) -> Path:
        """
        Cache a JSON-serializable value.

        Args:
            key: Cache key
            value: Value to cache (must be JSON-serializable)

        Returns:
            Path to the cache file
        """
        filepath = self._get_cache_path(key, "json")

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(value, f, indent=2, default=str)

        return filepath

    def get_parquet(
        self,
        key: str,
        expiry_seconds: int | None = None,
    ) -> pd.DataFrame | None:
        """
        Get a cached DataFrame from parquet.

        Args:
            key: Cache key
            expiry_seconds: Override default expiry

        Returns:
            Cached DataFrame or None if not found/expired
        """
        filepath = self._get_cache_path(key, "parquet")

        if self._is_expired(filepath, expiry_seconds):
            return None

        try:
            return pd.read_parquet(filepath)
        except Exception:
            return None

    def set_parquet(self, key: str, df: pd.DataFrame) -> Path:
        """
        Cache a DataFrame as parquet.

        Args:
            key: Cache key
            df: DataFrame to cache

        Returns:
            Path to the cache file
        """
        filepath = self._get_cache_path(key, "parquet")
        df.to_parquet(filepath, index=True)
        return filepath

    def invalidate(self, key: str) -> bool:
        """
        Remove a cached item.

        Args:
            key: Cache key

        Returns:
            True if item was removed, False if not found
        """
        for ext in ["json", "parquet"]:
            filepath = self._get_cache_path(key, ext)
            if filepath.exists():
                filepath.unlink()
                return True
        return False

    def clear(self) -> int:
        """
        Clear all cached items.

        Returns:
            Number of files removed
        """
        count = 0
        for filepath in self.cache_dir.glob("*"):
            if filepath.is_file():
                filepath.unlink()
                count += 1
        return count


class PriceDataCache:
    """
    Specialized cache for coin price data.

    Stores price data in individual parquet files per coin-pair.
    Files are named as {coin_id}-{quote_currency}.parquet (e.g., eth-btc.parquet).

    Supports both legacy format (coin_id only) and new pair format.
    """

    def __init__(self, prices_dir: Path = PRICES_DIR):
        """
        Initialize the price data cache.

        Args:
            prices_dir: Directory for price parquet files
        """
        self.prices_dir = prices_dir
        self.prices_dir.mkdir(parents=True, exist_ok=True)

    def _get_price_path(self, coin_id: str, quote_currency: str = "BTC") -> Path:
        """
        Get the file path for a coin-pair's price data.

        Args:
            coin_id: Coin ID (lowercase symbol, e.g., "eth")
            quote_currency: Quote currency (e.g., "BTC", "USD")

        Returns:
            Path like prices/eth-btc.parquet
        """
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in coin_id)
        quote = quote_currency.lower()
        return self.prices_dir / f"{safe_id}-{quote}.parquet"

    def _get_legacy_price_path(self, coin_id: str) -> Path:
        """Get the legacy file path (without quote currency)."""
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in coin_id)
        return self.prices_dir / f"{safe_id}.parquet"

    def has_prices(self, coin_id: str, quote_currency: str = "BTC") -> bool:
        """Check if price data exists for a coin-pair."""
        # Check new format first
        if self._get_price_path(coin_id, quote_currency).exists():
            return True
        # Fall back to legacy format for BTC
        if quote_currency.upper() == "BTC":
            return self._get_legacy_price_path(coin_id).exists()
        return False

    def get_prices(self, coin_id: str, quote_currency: str = "BTC") -> pd.DataFrame | None:
        """
        Get cached price data for a coin-pair.

        Returns a DataFrame with normalized DatetimeIndex at midnight.

        Args:
            coin_id: Coin ID (lowercase symbol)
            quote_currency: Quote currency (e.g., "BTC", "USD")

        Returns:
            DataFrame with DatetimeIndex and OHLCV columns, or None
        """
        # Try new format first
        filepath = self._get_price_path(coin_id, quote_currency)

        # Fall back to legacy format for BTC
        if not filepath.exists() and quote_currency.upper() == "BTC":
            filepath = self._get_legacy_price_path(coin_id)

        if not filepath.exists():
            return None

        try:
            df = pd.read_parquet(filepath)

            # Ensure normalized DatetimeIndex for consistent lookups
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            df.index = df.index.normalize()

            return df
        except Exception:
            return None

    def set_prices(self, coin_id: str, df: pd.DataFrame, quote_currency: str = "BTC") -> Path:
        """
        Cache price data for a coin-pair.

        Normalizes the DatetimeIndex to midnight UTC for consistent lookups.
        Trims leading rows where close is 0 (dates before coin existed).

        Args:
            coin_id: Coin ID (lowercase symbol)
            df: DataFrame with price data
            quote_currency: Quote currency (e.g., "BTC", "USD")

        Returns:
            Path to the cache file
        """
        filepath = self._get_price_path(coin_id, quote_currency)

        # Normalize index to DatetimeIndex at midnight for consistent lookups
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)
        df.index = df.index.normalize()

        # Trim leading rows where close is 0 (dates before coin existed)
        # CryptoCompare returns zeros for dates before a coin was listed
        if "close" in df.columns:
            first_valid_idx = (df["close"] > 0).idxmax()
            if first_valid_idx is not None:
                df = df.loc[first_valid_idx:]

        df.to_parquet(filepath, index=True)
        return filepath

    def get_last_date(self, coin_id: str, quote_currency: str = "BTC") -> pd.Timestamp | None:
        """
        Get the last date of cached price data for a coin-pair.

        Useful for incremental updates.

        Args:
            coin_id: Coin ID (lowercase symbol)
            quote_currency: Quote currency (e.g., "BTC", "USD")

        Returns:
            Last date in the cached data as pd.Timestamp, or None
        """
        df = self.get_prices(coin_id, quote_currency)
        if df is None or df.empty:
            return None

        return df.index.max()

    def list_cached_coins(self, quote_currency: str | None = None) -> list[str]:
        """
        List all coins with cached price data.

        Args:
            quote_currency: If provided, only list coins with this quote currency.
                          If None, lists all unique coin IDs.

        Returns:
            List of coin IDs
        """
        coins = set()
        for filepath in self.prices_dir.glob("*.parquet"):
            filename = filepath.stem
            # Check if it's the new format (contains hyphen for pair)
            if "-" in filename:
                parts = filename.rsplit("-", 1)
                if len(parts) == 2:
                    coin_id, quote = parts
                    if quote_currency is None or quote.upper() == quote_currency.upper():
                        coins.add(coin_id)
            else:
                # Legacy format - assume BTC quote
                if quote_currency is None or quote_currency.upper() == "BTC":
                    coins.add(filename)

        return sorted(coins)

    def list_cached_pairs(self) -> list[tuple[str, str]]:
        """
        List all cached coin-pairs.

        Returns:
            List of (coin_id, quote_currency) tuples
        """
        pairs = []
        for filepath in self.prices_dir.glob("*.parquet"):
            filename = filepath.stem
            if "-" in filename:
                parts = filename.rsplit("-", 1)
                if len(parts) == 2:
                    coin_id, quote = parts
                    pairs.append((coin_id, quote.upper()))
            else:
                # Legacy format - assume BTC quote
                pairs.append((filename, "BTC"))

        return sorted(pairs)

    def delete_prices(self, coin_id: str, quote_currency: str = "BTC") -> bool:
        """
        Delete cached price data for a coin-pair.

        Args:
            coin_id: Coin ID (lowercase symbol)
            quote_currency: Quote currency (e.g., "BTC", "USD")

        Returns:
            True if deleted, False if not found
        """
        filepath = self._get_price_path(coin_id, quote_currency)
        if filepath.exists():
            filepath.unlink()
            return True

        # Try legacy format for BTC
        if quote_currency.upper() == "BTC":
            legacy_path = self._get_legacy_price_path(coin_id)
            if legacy_path.exists():
                legacy_path.unlink()
                return True

        return False

    def clear(self) -> int:
        """
        Clear all cached price data.

        Returns:
            Number of files removed
        """
        count = 0
        for filepath in self.prices_dir.glob("*.parquet"):
            filepath.unlink()
            count += 1
        return count

    def migrate_to_pair_format(self) -> int:
        """
        Migrate legacy files to new pair format.

        Renames files like 'eth.parquet' to 'eth-btc.parquet'.

        Returns:
            Number of files migrated
        """
        migrated = 0
        for filepath in self.prices_dir.glob("*.parquet"):
            filename = filepath.stem
            # Skip if already in pair format
            if "-" in filename:
                continue

            # Rename to pair format
            new_path = self.prices_dir / f"{filename}-btc.parquet"
            if not new_path.exists():
                filepath.rename(new_path)
                migrated += 1

        return migrated

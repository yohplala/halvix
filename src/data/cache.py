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

    Stores price data in individual parquet files per coin for efficient access.
    """

    def __init__(self, prices_dir: Path = PRICES_DIR):
        """
        Initialize the price data cache.

        Args:
            prices_dir: Directory for price parquet files
        """
        self.prices_dir = prices_dir
        self.prices_dir.mkdir(parents=True, exist_ok=True)

    def _get_price_path(self, coin_id: str) -> Path:
        """Get the file path for a coin's price data."""
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in coin_id)
        return self.prices_dir / f"{safe_id}.parquet"

    def has_prices(self, coin_id: str) -> bool:
        """Check if price data exists for a coin."""
        return self._get_price_path(coin_id).exists()

    def get_prices(self, coin_id: str) -> pd.DataFrame | None:
        """
        Get cached price data for a coin.

        Returns a DataFrame with normalized DatetimeIndex at midnight.

        Args:
            coin_id: Coin ID (lowercase symbol)

        Returns:
            DataFrame with DatetimeIndex and price/volume columns, or None
        """
        filepath = self._get_price_path(coin_id)

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

    def set_prices(self, coin_id: str, df: pd.DataFrame) -> Path:
        """
        Cache price data for a coin.

        Normalizes the DatetimeIndex to midnight UTC for consistent lookups.
        Trims leading rows where price is 0 (dates before coin existed).

        Args:
            coin_id: Coin ID (lowercase symbol)
            df: DataFrame with price data

        Returns:
            Path to the cache file
        """
        filepath = self._get_price_path(coin_id)

        # Normalize index to DatetimeIndex at midnight for consistent lookups
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)
        df.index = df.index.normalize()

        # Trim leading rows where price is 0 (dates before coin existed)
        # CryptoCompare returns zeros for dates before a coin was listed
        if "price" in df.columns:
            first_valid_idx = (df["price"] > 0).idxmax()
            if first_valid_idx is not None:
                df = df.loc[first_valid_idx:]

        df.to_parquet(filepath, index=True)
        return filepath

    def get_last_date(self, coin_id: str) -> pd.Timestamp | None:
        """
        Get the last date of cached price data for a coin.

        Useful for incremental updates.

        Args:
            coin_id: Coin ID (lowercase symbol)

        Returns:
            Last date in the cached data as pd.Timestamp, or None
        """
        df = self.get_prices(coin_id)
        if df is None or df.empty:
            return None

        return df.index.max()

    def list_cached_coins(self) -> list[str]:
        """
        List all coins with cached price data.

        Returns:
            List of coin IDs
        """
        coins = []
        for filepath in self.prices_dir.glob("*.parquet"):
            coin_id = filepath.stem
            coins.append(coin_id)
        return sorted(coins)

    def delete_prices(self, coin_id: str) -> bool:
        """
        Delete cached price data for a coin.

        Args:
            coin_id: Coin ID (lowercase symbol)

        Returns:
            True if deleted, False if not found
        """
        filepath = self._get_price_path(coin_id)
        if filepath.exists():
            filepath.unlink()
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

"""
File-based caching for API responses and price data (polars-backed).

Caches coin lists (JSON) and per-coin price series (Parquet) to cut API calls
and enable offline analysis. Price frames carry an explicit ``date`` column
(polars has no index); every read normalizes it to ``pl.Date`` and sorts.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from config import CACHE_DIR, CACHE_EXPIRY_SECONDS, PRICES_DIR


class CacheError(Exception):
    """Base exception for cache errors."""


def _sanitize_stem(key: str) -> str:
    """Filesystem-safe form of a cache key / stem (keeps alnum, ``-``, ``_``)."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in key)


def _normalize_price_frame(df: pl.DataFrame) -> pl.DataFrame:
    """
    Return a price frame with a clean, sorted ``date`` column.

    Price parquets carry the date as a real column (older pandas-written files
    stored a named index under ``date``; a stray ``__index_level_0__`` from an
    unnamed index is coalesced defensively). ``date`` is cast to ``pl.Date``
    (daily bars) so comparisons against ``datetime.date`` work directly.
    """
    if "date" not in df.columns and "__index_level_0__" in df.columns:
        df = df.rename({"__index_level_0__": "date"})
    if "date" in df.columns:
        df = df.with_columns(pl.col("date").cast(pl.Date)).sort("date")
    return df


class FileCache:
    """File-based cache for API responses (JSON coin lists and metadata)."""

    def __init__(
        self,
        cache_dir: Path = CACHE_DIR,
        expiry_seconds: int = CACHE_EXPIRY_SECONDS,
    ):
        self.cache_dir = cache_dir
        self.expiry_seconds = expiry_seconds
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_cache_path(self, key: str, extension: str = "json") -> Path:
        """Get the file path for a cache key."""
        # Use MD5 hash for long keys, else a sanitized key.
        if len(key) > 100:
            filename = f"{hashlib.md5(key.encode()).hexdigest()}.{extension}"
        else:
            filename = f"{_sanitize_stem(key)}.{extension}"
        return self.cache_dir / filename

    def _is_expired(self, filepath: Path, expiry_seconds: int | None = None) -> bool:
        """Check if a cached file has expired."""
        if not filepath.exists():
            return True
        expiry = expiry_seconds if expiry_seconds is not None else self.expiry_seconds
        if expiry <= 0:  # Never expire when expiry is 0 or negative.
            return False
        mtime = datetime.fromtimestamp(filepath.stat().st_mtime)
        return (datetime.now() - mtime).total_seconds() > expiry

    def get_json(self, key: str, expiry_seconds: int | None = None) -> Any | None:
        """Get a cached JSON value (None if not found/expired/unreadable)."""
        filepath = self._get_cache_path(key, "json")
        if self._is_expired(filepath, expiry_seconds):
            return None
        try:
            with open(filepath, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError, OSError:
            return None

    def set_json(self, key: str, value: Any) -> Path:
        """Cache a JSON-serializable value."""
        filepath = self._get_cache_path(key, "json")
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(value, f, indent=2, default=str)
        except OSError as e:
            raise CacheError(f"Failed to write cache {filepath}: {e}") from e
        return filepath

    def clear(self) -> int:
        """Clear all cached items. Returns the number of files removed."""
        count = 0
        for filepath in self.cache_dir.glob("*"):
            if filepath.is_file():
                filepath.unlink()
                count += 1
        return count


class PriceDataCache:
    """
    Per-coin price cache, one Parquet file per coin-pair.

    Files are named ``{stem}-{quote}.parquet`` (e.g. ``eth-btc.parquet``) and
    hold OHLCV columns plus a ``date`` column (``pl.Date``).
    """

    def __init__(self, prices_dir: Path = PRICES_DIR):
        self.prices_dir = prices_dir
        self.prices_dir.mkdir(parents=True, exist_ok=True)

    def _get_price_path(self, coin_id: str, quote_currency: str = "BTC") -> Path:
        """Path like ``prices/eth-btc.parquet`` for a coin-pair."""
        return self.prices_dir / f"{_sanitize_stem(coin_id)}-{quote_currency.lower()}.parquet"

    def has_prices(self, coin_id: str, quote_currency: str = "BTC") -> bool:
        """Check if price data exists for a coin-pair."""
        return self._get_price_path(coin_id, quote_currency).exists()

    def get_prices(
        self,
        coin_id: str,
        quote_currency: str = "BTC",
        columns: list[str] | None = None,
    ) -> pl.DataFrame | None:
        """
        Get cached price data for a coin-pair, or None if absent/unreadable.

        Args:
            coin_id: Coin ID (parquet stem / lowercase symbol).
            quote_currency: Quote currency (e.g. "BTC", "USD").
            columns: Optional column projection (``date`` is always included).
                     For TOTAL2, use ["close", "volume_to"] to reduce memory.

        Returns:
            A polars DataFrame with a sorted ``date`` column, or None.
        """
        filepath = self._get_price_path(coin_id, quote_currency)
        if not filepath.exists():
            return None
        if columns is not None and "date" not in columns:
            columns = ["date", *columns]
        try:
            df = pl.read_parquet(filepath, columns=columns)
        except OSError, pl.exceptions.PolarsError:
            return None
        return _normalize_price_frame(df)

    def set_prices(self, coin_id: str, df: pl.DataFrame, quote_currency: str = "BTC") -> Path:
        """
        Cache price data for a coin-pair.

        Normalizes the ``date`` column and trims leading rows where close is 0
        (dates before the coin existed; some providers backfill zero closes).
        """
        filepath = self._get_price_path(coin_id, quote_currency)
        df = _normalize_price_frame(df)

        if "close" in df.columns:
            positive = df.filter(pl.col("close") > 0)
            if not positive.is_empty():
                first_valid = positive.select(pl.col("date").min()).item()
                df = df.filter(pl.col("date") >= first_valid)

        try:
            df.write_parquet(filepath)
        except (OSError, pl.exceptions.PolarsError) as e:
            raise CacheError(f"Failed to write prices {filepath}: {e}") from e
        return filepath

    def list_cached_coins(self, quote_currency: str | None = None) -> list[str]:
        """
        List all coins (stems) with cached price data.

        Args:
            quote_currency: If given, restrict to this quote currency.

        Returns:
            Sorted list of coin stems.
        """
        coins = set()
        for filepath in self.prices_dir.glob("*.parquet"):
            # rsplit on the last "-" so multi-part stems (``tag-2-btc``) parse.
            coin_id, _, quote = filepath.stem.rpartition("-")
            if not coin_id:
                continue
            if quote_currency is None or quote.upper() == quote_currency.upper():
                coins.add(coin_id)
        return sorted(coins)

    def delete_prices(self, coin_id: str, quote_currency: str = "BTC") -> bool:
        """Delete cached price data for a coin-pair. Returns True if removed."""
        filepath = self._get_price_path(coin_id, quote_currency)
        if filepath.exists():
            filepath.unlink()
            return True
        return False

    def clear(self) -> int:
        """Clear all cached price data. Returns the number of files removed."""
        count = 0
        for filepath in self.prices_dir.glob("*.parquet"):
            filepath.unlink()
            count += 1
        return count

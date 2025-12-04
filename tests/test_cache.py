"""
Tests for file-based caching.

Tests cover:
- JSON caching
- Parquet caching
- Cache expiry
- Price data cache
"""

import tempfile
import time
from pathlib import Path

import pandas as pd
import pytest

from data.cache import FileCache, PriceDataCache


class TestFileCache:
    """Tests for the FileCache class."""

    @pytest.fixture
    def temp_cache_dir(self):
        """Create a temporary cache directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def cache(self, temp_cache_dir):
        """Create a FileCache instance."""
        return FileCache(
            cache_dir=temp_cache_dir,
            expiry_seconds=3600,  # 1 hour
        )

    # =========================================================================
    # JSON Caching Tests
    # =========================================================================

    def test_set_and_get_json(self, cache):
        """Test basic JSON caching."""
        data = {"key": "value", "number": 42}

        cache.set_json("test_key", data)
        result = cache.get_json("test_key")

        assert result == data

    def test_get_json_returns_none_for_missing(self, cache):
        """Test that missing key returns None."""
        result = cache.get_json("nonexistent_key")
        assert result is None

    def test_json_cache_handles_complex_data(self, cache):
        """Test caching complex nested data."""
        data = {
            "list": [1, 2, 3],
            "nested": {"a": {"b": {"c": "deep"}}},
            "mixed": [{"x": 1}, {"y": 2}],
        }

        cache.set_json("complex", data)
        result = cache.get_json("complex")

        assert result == data

    def test_json_cache_expiry(self, temp_cache_dir):
        """Test that expired cache returns None."""
        cache = FileCache(
            cache_dir=temp_cache_dir,
            expiry_seconds=1,  # 1 second expiry
        )

        cache.set_json("expiring", {"data": "test"})

        # Should still be valid
        assert cache.get_json("expiring") is not None

        # Wait for expiry
        time.sleep(1.1)

        # Should be expired now
        assert cache.get_json("expiring") is None

    def test_json_cache_never_expires_with_zero(self, temp_cache_dir):
        """Test that expiry_seconds=0 means never expire."""
        cache = FileCache(
            cache_dir=temp_cache_dir,
            expiry_seconds=0,
        )

        cache.set_json("permanent", {"data": "test"})

        # Should never expire
        assert cache.get_json("permanent") is not None

    def test_set_json_returns_path(self, cache, temp_cache_dir):
        """Test that set_json returns the file path."""
        path = cache.set_json("test", {"a": 1})

        assert path.exists()
        assert path.parent == temp_cache_dir

    # =========================================================================
    # Parquet Caching Tests
    # =========================================================================

    def test_set_and_get_parquet(self, cache):
        """Test basic parquet caching."""
        df = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=5),
                "value": [1.0, 2.0, 3.0, 4.0, 5.0],
            }
        )
        df = df.set_index("date")

        cache.set_parquet("test_df", df)
        result = cache.get_parquet("test_df")

        assert result is not None
        pd.testing.assert_frame_equal(result, df)

    def test_get_parquet_returns_none_for_missing(self, cache):
        """Test that missing parquet returns None."""
        result = cache.get_parquet("nonexistent")
        assert result is None

    def test_parquet_cache_expiry(self, temp_cache_dir):
        """Test parquet cache expiry."""
        cache = FileCache(
            cache_dir=temp_cache_dir,
            expiry_seconds=1,
        )

        df = pd.DataFrame({"a": [1, 2, 3]})
        cache.set_parquet("expiring_df", df)

        assert cache.get_parquet("expiring_df") is not None

        time.sleep(1.1)

        assert cache.get_parquet("expiring_df") is None

    # =========================================================================
    # Cache Management Tests
    # =========================================================================

    def test_invalidate_removes_cache(self, cache):
        """Test that invalidate removes cached item."""
        cache.set_json("to_remove", {"data": "test"})

        assert cache.get_json("to_remove") is not None

        result = cache.invalidate("to_remove")

        assert result is True
        assert cache.get_json("to_remove") is None

    def test_invalidate_returns_false_for_missing(self, cache):
        """Test invalidate returns False for non-existent key."""
        result = cache.invalidate("nonexistent")
        assert result is False

    def test_clear_removes_all(self, cache):
        """Test that clear removes all cached items."""
        cache.set_json("key1", {"a": 1})
        cache.set_json("key2", {"b": 2})
        cache.set_parquet("df1", pd.DataFrame({"x": [1]}))

        count = cache.clear()

        assert count == 3
        assert cache.get_json("key1") is None
        assert cache.get_json("key2") is None
        assert cache.get_parquet("df1") is None

    def test_long_key_uses_hash(self, cache, temp_cache_dir):
        """Test that long keys are hashed."""
        long_key = "a" * 200

        cache.set_json(long_key, {"data": "test"})

        # Should create a file with MD5 hash name
        files = list(temp_cache_dir.glob("*.json"))
        assert len(files) == 1

        # Filename should be an MD5 hash (32 chars)
        filename = files[0].stem
        assert len(filename) == 32


class TestPriceDataCache:
    """Tests for the PriceDataCache class."""

    @pytest.fixture
    def temp_prices_dir(self):
        """Create a temporary prices directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def price_cache(self, temp_prices_dir):
        """Create a PriceDataCache instance."""
        return PriceDataCache(prices_dir=temp_prices_dir)

    @pytest.fixture
    def sample_price_df(self):
        """Create a sample price DataFrame."""
        return pd.DataFrame(
            {
                "close": [1.0, 1.1, 1.2, 1.3, 1.4],
                "volume": [1000, 1100, 1200, 1300, 1400],
            },
            index=pd.date_range("2024-01-01", periods=5, name="date"),
        )

    def test_set_and_get_prices(self, price_cache, sample_price_df):
        """Test basic price data caching."""
        price_cache.set_prices("bitcoin", sample_price_df)
        result = price_cache.get_prices("bitcoin")

        assert result is not None
        # Check data equality (parquet may change index frequency metadata)
        pd.testing.assert_frame_equal(
            result.reset_index(drop=True), sample_price_df.reset_index(drop=True)
        )
        # Check index values match
        assert list(result.index) == list(sample_price_df.index)

    def test_has_prices(self, price_cache, sample_price_df):
        """Test checking if prices exist."""
        assert price_cache.has_prices("bitcoin") is False

        price_cache.set_prices("bitcoin", sample_price_df)

        assert price_cache.has_prices("bitcoin") is True

    def test_get_last_date(self, price_cache, sample_price_df):
        """Test getting last date of price data."""
        price_cache.set_prices("bitcoin", sample_price_df)

        last_date = price_cache.get_last_date("bitcoin")

        assert last_date is not None
        # Should be the last date in the DataFrame
        expected = sample_price_df.index.max()
        assert last_date == expected

    def test_get_last_date_returns_none_for_missing(self, price_cache):
        """Test get_last_date returns None for missing coin."""
        assert price_cache.get_last_date("nonexistent") is None

    def test_list_cached_coins(self, price_cache, sample_price_df):
        """Test listing cached coins."""
        price_cache.set_prices("bitcoin", sample_price_df)
        price_cache.set_prices("ethereum", sample_price_df)
        price_cache.set_prices("solana", sample_price_df)

        coins = price_cache.list_cached_coins()

        assert len(coins) == 3
        assert "bitcoin" in coins
        assert "ethereum" in coins
        assert "solana" in coins
        # Should be sorted
        assert coins == sorted(coins)

    def test_delete_prices(self, price_cache, sample_price_df):
        """Test deleting price data."""
        price_cache.set_prices("bitcoin", sample_price_df)

        assert price_cache.has_prices("bitcoin")

        result = price_cache.delete_prices("bitcoin")

        assert result is True
        assert not price_cache.has_prices("bitcoin")

    def test_delete_prices_returns_false_for_missing(self, price_cache):
        """Test delete returns False for missing coin."""
        result = price_cache.delete_prices("nonexistent")
        assert result is False

    def test_clear_removes_all(self, price_cache, sample_price_df):
        """Test clearing all price data."""
        price_cache.set_prices("bitcoin", sample_price_df)
        price_cache.set_prices("ethereum", sample_price_df)

        count = price_cache.clear()

        assert count == 2
        assert len(price_cache.list_cached_coins()) == 0

    def test_coin_id_sanitization(self, price_cache, sample_price_df, temp_prices_dir):
        """Test that coin IDs are sanitized for filesystem."""
        # Special characters in ID
        price_cache.set_prices("coin/with:special", sample_price_df)

        # Should create a safe filename
        files = list(temp_prices_dir.glob("*.parquet"))
        assert len(files) == 1

        # And retrieve correctly
        result = price_cache.get_prices("coin/with:special")
        assert result is not None

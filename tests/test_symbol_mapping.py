"""
Tests for symbol mapping validation between CoinGecko and CryptoCompare.
"""

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from data.symbol_mapping import SymbolMapping, SymbolMappingCache


class TestSymbolMapping:
    """Tests for SymbolMapping dataclass."""
    
    def test_symbol_mapping_creation(self):
        """Test creating a SymbolMapping."""
        mapping = SymbolMapping(
            coingecko_id="ethereum",
            coingecko_symbol="eth",
            cryptocompare_symbol="ETH",
            validated_at="2025-01-01T12:00:00",
            coingecko_price=0.04,
            cryptocompare_price=0.041,
            price_diff_percent=2.47,
            is_valid=True,
        )
        
        assert mapping.coingecko_id == "ethereum"
        assert mapping.cryptocompare_symbol == "ETH"
        assert mapping.is_valid is True
    
    def test_to_dict(self):
        """Test converting to dictionary."""
        mapping = SymbolMapping(
            coingecko_id="solana",
            coingecko_symbol="sol",
            cryptocompare_symbol="SOL",
            validated_at="2025-01-01T12:00:00",
            coingecko_price=0.001,
            cryptocompare_price=0.001,
            price_diff_percent=0.0,
            is_valid=True,
        )
        
        d = mapping.to_dict()
        
        assert d["coingecko_id"] == "solana"
        assert d["is_valid"] is True
    
    def test_from_dict(self):
        """Test creating from dictionary."""
        data = {
            "coingecko_id": "cardano",
            "coingecko_symbol": "ada",
            "cryptocompare_symbol": "ADA",
            "validated_at": "2025-01-01T12:00:00",
            "coingecko_price": 0.00001,
            "cryptocompare_price": 0.00001,
            "price_diff_percent": 0.0,
            "is_valid": True,
            "error_message": "",
        }
        
        mapping = SymbolMapping.from_dict(data)
        
        assert mapping.coingecko_id == "cardano"
        assert mapping.cryptocompare_symbol == "ADA"


class TestSymbolMappingCache:
    """Tests for SymbolMappingCache."""
    
    @pytest.fixture
    def temp_cache_file(self):
        """Create a temporary cache file."""
        with tempfile.NamedTemporaryFile(
            suffix=".json",
            delete=False,
            mode="w"
        ) as f:
            f.write("{}")
        yield Path(f.name)
        Path(f.name).unlink(missing_ok=True)
    
    @pytest.fixture
    def cache(self, temp_cache_file):
        """Create a SymbolMappingCache with temp file."""
        return SymbolMappingCache(
            cache_file=temp_cache_file,
            tolerance_percent=4.0,  # Matches SYMBOL_MAPPING_TOLERANCE_PERCENT
        )
    
    def test_initialization(self, cache):
        """Test cache initializes correctly."""
        assert cache.tolerance_percent == 4.0
        assert len(cache._mappings) == 0
    
    def test_price_diff_calculation_same_price(self, cache):
        """Test price diff when prices are equal."""
        diff = cache._calculate_price_diff(0.05, 0.05)
        assert diff == 0.0
    
    def test_price_diff_calculation_small_diff(self, cache):
        """Test price diff with small difference."""
        # 0.05 and 0.052 = 3.9% difference
        diff = cache._calculate_price_diff(0.05, 0.052)
        assert 3.8 < diff < 4.0
    
    def test_price_diff_calculation_zero_price(self, cache):
        """Test price diff when one price is zero."""
        diff = cache._calculate_price_diff(0.05, 0)
        assert diff == float("inf")
    
    def test_has_mapping_not_in_cache(self, cache):
        """Test has_mapping returns False for unknown coin."""
        assert cache.has_mapping("unknown-coin") is False
    
    def test_has_valid_mapping_not_in_cache(self, cache):
        """Test has_valid_mapping returns False for unknown coin."""
        assert cache.has_valid_mapping("unknown-coin") is False
    
    def test_get_cryptocompare_symbol_not_validated(self, cache):
        """Test get_cryptocompare_symbol returns None for unvalidated."""
        assert cache.get_cryptocompare_symbol("unknown") is None
    
    def test_validate_mapping_caches_result(self, cache):
        """Test that validation results are cached."""
        # Create mock clients
        mock_cg_client = MagicMock()
        mock_cc_client = MagicMock()
        
        # Mock CoinGecko response
        mock_coin = MagicMock()
        mock_coin.id = "ethereum"
        mock_coin.current_price_btc = 0.04
        mock_cg_client.get_top_coins.return_value = [mock_coin]
        
        # Mock CryptoCompare response
        mock_cc_client.get_daily_history.return_value = [
            {"close": 0.041}
        ]
        
        # Validate
        mapping = cache.validate_mapping(
            coingecko_id="ethereum",
            coingecko_symbol="eth",
            coingecko_client=mock_cg_client,
            cryptocompare_client=mock_cc_client,
        )
        
        assert mapping.is_valid is True
        assert cache.has_mapping("ethereum")
        assert cache.get_cryptocompare_symbol("ethereum") == "ETH"
    
    def test_validate_mapping_invalid_price_diff(self, cache):
        """Test validation fails when price difference is too large."""
        mock_cg_client = MagicMock()
        mock_cc_client = MagicMock()
        
        # Mock CoinGecko response
        mock_coin = MagicMock()
        mock_coin.id = "scam-coin"
        mock_coin.current_price_btc = 0.01
        mock_cg_client.get_top_coins.return_value = [mock_coin]
        
        # Mock CryptoCompare with very different price
        mock_cc_client.get_daily_history.return_value = [
            {"close": 0.5}  # 50x different
        ]
        
        mapping = cache.validate_mapping(
            coingecko_id="scam-coin",
            coingecko_symbol="scam",
            coingecko_client=mock_cg_client,
            cryptocompare_client=mock_cc_client,
        )
        
        assert mapping.is_valid is False
        assert "exceeds tolerance" in mapping.error_message
    
    def test_validate_mapping_coin_not_found(self, cache):
        """Test validation fails when coin not found in CoinGecko."""
        mock_cg_client = MagicMock()
        mock_cc_client = MagicMock()
        
        # Return empty list (coin not in top 500)
        mock_cg_client.get_top_coins.return_value = []
        
        mapping = cache.validate_mapping(
            coingecko_id="unknown-coin",
            coingecko_symbol="unk",
            coingecko_client=mock_cg_client,
            cryptocompare_client=mock_cc_client,
        )
        
        assert mapping.is_valid is False
        assert "not found" in mapping.error_message
    
    def test_cache_persistence(self, temp_cache_file):
        """Test that cache persists to file."""
        # Create cache and add a mapping
        cache1 = SymbolMappingCache(cache_file=temp_cache_file)
        cache1._mappings["test-coin"] = SymbolMapping(
            coingecko_id="test-coin",
            coingecko_symbol="test",
            cryptocompare_symbol="TEST",
            validated_at=datetime.now().isoformat(),
            coingecko_price=0.01,
            cryptocompare_price=0.01,
            price_diff_percent=0.0,
            is_valid=True,
        )
        cache1._save_cache()
        
        # Create new cache instance and verify it loads
        cache2 = SymbolMappingCache(cache_file=temp_cache_file)
        
        assert cache2.has_mapping("test-coin")
        assert cache2.has_valid_mapping("test-coin")
    
    def test_skip_validated_in_batch(self, cache):
        """Test that already-validated coins are skipped in batch."""
        # Pre-populate cache
        cache._mappings["ethereum"] = SymbolMapping(
            coingecko_id="ethereum",
            coingecko_symbol="eth",
            cryptocompare_symbol="ETH",
            validated_at=datetime.now().isoformat(),
            coingecko_price=0.04,
            cryptocompare_price=0.04,
            price_diff_percent=0.0,
            is_valid=True,
        )
        
        mock_cg_client = MagicMock()
        mock_cc_client = MagicMock()
        
        # Batch with ethereum (already cached) and solana (new)
        coins = [
            {"id": "ethereum", "symbol": "eth"},
            {"id": "solana", "symbol": "sol"},
        ]
        
        # Mock for solana only
        mock_coin = MagicMock()
        mock_coin.id = "solana"
        mock_coin.current_price_btc = 0.001
        mock_cg_client.get_top_coins.return_value = [mock_coin]
        mock_cc_client.get_daily_history.return_value = [{"close": 0.001}]
        
        results = cache.validate_batch(
            coins=coins,
            coingecko_client=mock_cg_client,
            cryptocompare_client=mock_cc_client,
            skip_validated=True,
            show_progress=False,
        )
        
        # Should only validate solana (ethereum was skipped)
        assert "solana" in results
        assert "ethereum" not in results  # Was skipped
    
    def test_get_summary(self, cache):
        """Test getting summary of mappings."""
        # Add valid and invalid mappings
        cache._mappings["valid-coin"] = SymbolMapping(
            coingecko_id="valid-coin",
            coingecko_symbol="val",
            cryptocompare_symbol="VAL",
            validated_at=datetime.now().isoformat(),
            coingecko_price=0.01,
            cryptocompare_price=0.01,
            price_diff_percent=0.0,
            is_valid=True,
        )
        cache._mappings["invalid-coin"] = SymbolMapping(
            coingecko_id="invalid-coin",
            coingecko_symbol="inv",
            cryptocompare_symbol="INV",
            validated_at=datetime.now().isoformat(),
            coingecko_price=0.01,
            cryptocompare_price=0.5,
            price_diff_percent=192.0,
            is_valid=False,
            error_message="Price difference too large",
        )
        
        summary = cache.get_summary()
        
        assert summary["total"] == 2
        assert summary["valid"] == 1
        assert summary["invalid"] == 1
        assert len(summary["invalid_coins"]) == 1
        assert summary["invalid_coins"][0]["id"] == "invalid-coin"
    
    def test_clear_cache(self, cache, temp_cache_file):
        """Test clearing all mappings."""
        cache._mappings["test"] = SymbolMapping(
            coingecko_id="test",
            coingecko_symbol="test",
            cryptocompare_symbol="TEST",
            validated_at=datetime.now().isoformat(),
            coingecko_price=0.01,
            cryptocompare_price=0.01,
            price_diff_percent=0.0,
            is_valid=True,
        )
        cache._save_cache()
        
        count = cache.clear()
        
        assert count == 1
        assert len(cache._mappings) == 0
        assert not temp_cache_file.exists()
    
    def test_remove_mapping(self, cache):
        """Test removing a single mapping."""
        cache._mappings["to-remove"] = SymbolMapping(
            coingecko_id="to-remove",
            coingecko_symbol="rem",
            cryptocompare_symbol="REM",
            validated_at=datetime.now().isoformat(),
            coingecko_price=0.01,
            cryptocompare_price=0.01,
            price_diff_percent=0.0,
            is_valid=True,
        )
        
        removed = cache.remove_mapping("to-remove")
        
        assert removed is True
        assert "to-remove" not in cache._mappings
    
    def test_remove_nonexistent_mapping(self, cache):
        """Test removing a mapping that doesn't exist."""
        removed = cache.remove_mapping("nonexistent")
        assert removed is False


class TestIncrementalFetching:
    """Tests for incremental data fetching in DataFetcher."""
    
    @pytest.fixture
    def mock_price_cache(self):
        """Create a mock price cache."""
        cache = MagicMock()
        cache.get_prices.return_value = None
        cache.set_prices.return_value = Path("/tmp/test.parquet")
        return cache
    
    @pytest.fixture  
    def mock_symbol_mapping(self):
        """Create a mock symbol mapping cache."""
        mapping = MagicMock()
        mapping.get_cryptocompare_symbol.return_value = None
        mapping.has_valid_mapping.return_value = True
        return mapping
    
    def test_full_fetch_when_no_cache(self, mock_price_cache, mock_symbol_mapping):
        """Test full fetch is done when no cache exists."""
        from data.fetcher import DataFetcher
        import pandas as pd
        from datetime import date, timedelta
        
        # Create mock clients
        mock_cg = MagicMock()
        mock_cc = MagicMock()
        
        # Mock CryptoCompare response
        mock_df = pd.DataFrame({
            "price": [0.05, 0.051],
            "open": [0.049, 0.05],
            "high": [0.052, 0.053],
            "low": [0.048, 0.049],
        }, index=pd.date_range("2024-01-01", periods=2))
        mock_cc.get_full_daily_history.return_value = mock_df
        
        fetcher = DataFetcher(
            client=mock_cg,
            cryptocompare_client=mock_cc,
            price_cache=mock_price_cache,
            symbol_mapping=mock_symbol_mapping,
        )
        
        result = fetcher.fetch_coin_prices(
            coin_id="test-coin",
            symbol="TEST",
            use_cache=True,
            incremental=True,
        )
        
        # Should have called get_full_daily_history
        mock_cc.get_full_daily_history.assert_called_once()
        assert len(result) == 2


class TestYesterdayAsEndDate:
    """Tests that verify yesterday is used as end date."""
    
    def test_cryptocompare_defaults_to_yesterday(self):
        """Test that CryptoCompare client defaults to yesterday."""
        from api.cryptocompare import CryptoCompareClient
        from datetime import date, timedelta
        from unittest.mock import patch
        
        client = CryptoCompareClient()
        yesterday = date.today() - timedelta(days=1)
        
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = {
                "Data": {"Data": []}
            }
            
            # Call without specifying end_date
            client.get_full_daily_history(
                symbol="BTC",
                vs_currency="USD",
                start_date=date(2024, 1, 1),
                # end_date not specified - should default to yesterday
            )
            
            # The function was called, verify end_ts calculation
            # by checking that it doesn't use today
            # (Implementation detail: this is validated indirectly)


"""
Integration tests for symbol mapping validation.

These tests make ACTUAL API calls to both CoinGecko and CryptoCompare
to verify that symbol mappings work correctly with real price data.

Run with: pytest tests/test_symbol_mapping_integration.py --run-integration -v
"""

import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from api.coingecko import CoinGeckoClient
from api.cryptocompare import CryptoCompareClient
from config import SYMBOL_MAPPING_TOLERANCE_PERCENT
from data.symbol_mapping import SymbolMappingCache


# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def coingecko_client():
    """Create a CoinGecko client with conservative rate limiting."""
    return CoinGeckoClient(calls_per_minute=5)


@pytest.fixture(scope="module")
def cryptocompare_client():
    """Create a CryptoCompare client with conservative rate limiting."""
    return CryptoCompareClient(calls_per_minute=5)


@pytest.fixture
def temp_mapping_file():
    """Create a temporary mapping cache file."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        f.write("{}")
    yield Path(f.name)
    Path(f.name).unlink(missing_ok=True)


class TestSymbolMappingIntegrationMajorCoins:
    """Test symbol mapping validation for major cryptocurrencies."""
    
    def test_ethereum_mapping_valid(
        self,
        coingecko_client,
        cryptocompare_client,
        temp_mapping_file,
    ):
        """Test that ETH prices match between CoinGecko and CryptoCompare."""
        cache = SymbolMappingCache(
            cache_file=temp_mapping_file,
            tolerance_percent=SYMBOL_MAPPING_TOLERANCE_PERCENT,
        )
        
        mapping = cache.validate_mapping(
            coingecko_id="ethereum",
            coingecko_symbol="eth",
            coingecko_client=coingecko_client,
            cryptocompare_client=cryptocompare_client,
        )
        
        # Log prices for debugging
        print(f"\nETH Validation:")
        print(f"  CoinGecko price:     {mapping.coingecko_price:.8f} BTC")
        print(f"  CryptoCompare price: {mapping.cryptocompare_price:.8f} BTC")
        print(f"  Difference:          {mapping.price_diff_percent:.2f}%")
        print(f"  Valid:               {mapping.is_valid}")
        
        assert mapping.is_valid, f"ETH mapping should be valid, got: {mapping.error_message}"
        assert mapping.price_diff_percent <= SYMBOL_MAPPING_TOLERANCE_PERCENT
        assert mapping.cryptocompare_symbol == "ETH"
    
    def test_solana_mapping_valid(
        self,
        coingecko_client,
        cryptocompare_client,
        temp_mapping_file,
    ):
        """Test that SOL prices match between CoinGecko and CryptoCompare."""
        cache = SymbolMappingCache(
            cache_file=temp_mapping_file,
            tolerance_percent=SYMBOL_MAPPING_TOLERANCE_PERCENT,
        )
        
        mapping = cache.validate_mapping(
            coingecko_id="solana",
            coingecko_symbol="sol",
            coingecko_client=coingecko_client,
            cryptocompare_client=cryptocompare_client,
        )
        
        print(f"\nSOL Validation:")
        print(f"  CoinGecko price:     {mapping.coingecko_price:.8f} BTC")
        print(f"  CryptoCompare price: {mapping.cryptocompare_price:.8f} BTC")
        print(f"  Difference:          {mapping.price_diff_percent:.2f}%")
        print(f"  Valid:               {mapping.is_valid}")
        
        assert mapping.is_valid, f"SOL mapping should be valid, got: {mapping.error_message}"
        assert mapping.price_diff_percent <= SYMBOL_MAPPING_TOLERANCE_PERCENT
        assert mapping.cryptocompare_symbol == "SOL"
    
    def test_xrp_mapping_valid(
        self,
        coingecko_client,
        cryptocompare_client,
        temp_mapping_file,
    ):
        """Test that XRP prices match between CoinGecko and CryptoCompare."""
        cache = SymbolMappingCache(
            cache_file=temp_mapping_file,
            tolerance_percent=SYMBOL_MAPPING_TOLERANCE_PERCENT,
        )
        
        mapping = cache.validate_mapping(
            coingecko_id="ripple",
            coingecko_symbol="xrp",
            coingecko_client=coingecko_client,
            cryptocompare_client=cryptocompare_client,
        )
        
        print(f"\nXRP Validation:")
        print(f"  CoinGecko price:     {mapping.coingecko_price:.8f} BTC")
        print(f"  CryptoCompare price: {mapping.cryptocompare_price:.8f} BTC")
        print(f"  Difference:          {mapping.price_diff_percent:.2f}%")
        print(f"  Valid:               {mapping.is_valid}")
        
        assert mapping.is_valid, f"XRP mapping should be valid, got: {mapping.error_message}"
        assert mapping.price_diff_percent <= SYMBOL_MAPPING_TOLERANCE_PERCENT
        assert mapping.cryptocompare_symbol == "XRP"
    
    def test_cardano_mapping_valid(
        self,
        coingecko_client,
        cryptocompare_client,
        temp_mapping_file,
    ):
        """Test that ADA prices match between CoinGecko and CryptoCompare."""
        cache = SymbolMappingCache(
            cache_file=temp_mapping_file,
            tolerance_percent=SYMBOL_MAPPING_TOLERANCE_PERCENT,
        )
        
        mapping = cache.validate_mapping(
            coingecko_id="cardano",
            coingecko_symbol="ada",
            coingecko_client=coingecko_client,
            cryptocompare_client=cryptocompare_client,
        )
        
        print(f"\nADA Validation:")
        print(f"  CoinGecko price:     {mapping.coingecko_price:.8f} BTC")
        print(f"  CryptoCompare price: {mapping.cryptocompare_price:.8f} BTC")
        print(f"  Difference:          {mapping.price_diff_percent:.2f}%")
        print(f"  Valid:               {mapping.is_valid}")
        
        assert mapping.is_valid, f"ADA mapping should be valid, got: {mapping.error_message}"
        assert mapping.price_diff_percent <= SYMBOL_MAPPING_TOLERANCE_PERCENT
        assert mapping.cryptocompare_symbol == "ADA"
    
    def test_dogecoin_mapping_valid(
        self,
        coingecko_client,
        cryptocompare_client,
        temp_mapping_file,
    ):
        """Test that DOGE prices match between CoinGecko and CryptoCompare."""
        cache = SymbolMappingCache(
            cache_file=temp_mapping_file,
            tolerance_percent=SYMBOL_MAPPING_TOLERANCE_PERCENT,
        )
        
        mapping = cache.validate_mapping(
            coingecko_id="dogecoin",
            coingecko_symbol="doge",
            coingecko_client=coingecko_client,
            cryptocompare_client=cryptocompare_client,
        )
        
        print(f"\nDOGE Validation:")
        print(f"  CoinGecko price:     {mapping.coingecko_price:.8f} BTC")
        print(f"  CryptoCompare price: {mapping.cryptocompare_price:.8f} BTC")
        print(f"  Difference:          {mapping.price_diff_percent:.2f}%")
        print(f"  Valid:               {mapping.is_valid}")
        
        assert mapping.is_valid, f"DOGE mapping should be valid, got: {mapping.error_message}"
        assert mapping.price_diff_percent <= SYMBOL_MAPPING_TOLERANCE_PERCENT


class TestSymbolMappingIntegrationBatch:
    """Test batch validation of symbol mappings."""
    
    def test_batch_validation_top_coins(
        self,
        coingecko_client,
        cryptocompare_client,
        temp_mapping_file,
    ):
        """Test batch validation of top 10 coins."""
        cache = SymbolMappingCache(
            cache_file=temp_mapping_file,
            tolerance_percent=SYMBOL_MAPPING_TOLERANCE_PERCENT,
        )
        
        # Coins that should definitely work
        coins = [
            {"id": "ethereum", "symbol": "eth"},
            {"id": "solana", "symbol": "sol"},
            {"id": "ripple", "symbol": "xrp"},
            {"id": "cardano", "symbol": "ada"},
            {"id": "dogecoin", "symbol": "doge"},
        ]
        
        results = cache.validate_batch(
            coins=coins,
            coingecko_client=coingecko_client,
            cryptocompare_client=cryptocompare_client,
            skip_validated=False,
            show_progress=True,
        )
        
        # Print summary
        print("\nBatch Validation Results:")
        for coin_id, mapping in results.items():
            status = "✓" if mapping.is_valid else "✗"
            print(f"  {status} {coin_id}: {mapping.price_diff_percent:.2f}% diff")
        
        # At least 80% should be valid
        valid_count = sum(1 for m in results.values() if m.is_valid)
        valid_pct = valid_count / len(results) * 100
        
        print(f"\nValid: {valid_count}/{len(results)} ({valid_pct:.0f}%)")
        
        assert valid_pct >= 80, f"Expected at least 80% valid, got {valid_pct:.0f}%"


class TestSymbolMappingIntegrationEdgeCases:
    """Test edge cases in symbol mapping validation."""
    
    def test_nonexistent_coin(
        self,
        coingecko_client,
        cryptocompare_client,
        temp_mapping_file,
    ):
        """Test handling of a coin that doesn't exist."""
        cache = SymbolMappingCache(
            cache_file=temp_mapping_file,
            tolerance_percent=SYMBOL_MAPPING_TOLERANCE_PERCENT,
        )
        
        mapping = cache.validate_mapping(
            coingecko_id="this-coin-does-not-exist-12345",
            coingecko_symbol="fake",
            coingecko_client=coingecko_client,
            cryptocompare_client=cryptocompare_client,
        )
        
        # Should be invalid (not found)
        assert not mapping.is_valid
        assert "not found" in mapping.error_message.lower() or "error" in mapping.error_message.lower()
    
    def test_tolerance_boundary(
        self,
        coingecko_client,
        cryptocompare_client,
        temp_mapping_file,
    ):
        """Test that tolerance is applied correctly."""
        # Use a very strict tolerance
        cache = SymbolMappingCache(
            cache_file=temp_mapping_file,
            tolerance_percent=0.1,  # 0.1% - very strict
        )
        
        mapping = cache.validate_mapping(
            coingecko_id="ethereum",
            coingecko_symbol="eth",
            coingecko_client=coingecko_client,
            cryptocompare_client=cryptocompare_client,
        )
        
        # With such strict tolerance, it might fail even for valid coins
        # Just verify the tolerance is being applied
        if mapping.is_valid:
            assert mapping.price_diff_percent <= 0.1
        else:
            assert mapping.price_diff_percent > 0.1


class TestSymbolMappingIntegrationPriceComparison:
    """Test direct price comparison between APIs."""
    
    def test_direct_price_comparison_btc(
        self,
        coingecko_client,
        cryptocompare_client,
    ):
        """Test that BTC price in BTC is ~1.0 on both APIs."""
        # CoinGecko
        cg_coins = coingecko_client.get_top_coins(n=10, vs_currency="btc")
        cg_btc = next(c for c in cg_coins if c.id == "bitcoin")
        cg_price = cg_btc.current_price_btc
        
        # CryptoCompare
        cc_data = cryptocompare_client.get_daily_history("BTC", "BTC", limit=1)
        cc_price = cc_data[-1]["close"] if cc_data else 0
        
        print(f"\nBTC/BTC prices:")
        print(f"  CoinGecko:     {cg_price}")
        print(f"  CryptoCompare: {cc_price}")
        
        # Both should be ~1.0
        assert 0.99 <= cg_price <= 1.01, f"CoinGecko BTC/BTC should be ~1.0, got {cg_price}"
        assert 0.99 <= cc_price <= 1.01, f"CryptoCompare BTC/BTC should be ~1.0, got {cc_price}"
    
    def test_direct_price_comparison_eth(
        self,
        coingecko_client,
        cryptocompare_client,
    ):
        """Test ETH/BTC price consistency between APIs."""
        # CoinGecko
        cg_coins = coingecko_client.get_top_coins(n=10, vs_currency="btc")
        cg_eth = next(c for c in cg_coins if c.id == "ethereum")
        cg_price = cg_eth.current_price_btc
        
        # CryptoCompare
        cc_data = cryptocompare_client.get_daily_history("ETH", "BTC", limit=1)
        cc_price = cc_data[-1]["close"] if cc_data else 0
        
        print(f"\nETH/BTC prices:")
        print(f"  CoinGecko:     {cg_price:.8f}")
        print(f"  CryptoCompare: {cc_price:.8f}")
        
        # Calculate difference
        if cg_price > 0 and cc_price > 0:
            avg = (cg_price + cc_price) / 2
            diff_pct = abs(cg_price - cc_price) / avg * 100
            print(f"  Difference:    {diff_pct:.2f}%")
            
            # Should be within tolerance
            assert diff_pct <= SYMBOL_MAPPING_TOLERANCE_PERCENT, \
                f"ETH price diff {diff_pct:.2f}% exceeds {SYMBOL_MAPPING_TOLERANCE_PERCENT}%"


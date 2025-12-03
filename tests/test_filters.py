"""
Tests for token filtering in Halvix.

Tests that:
- Wrapped, staked, bridged tokens are correctly filtered out
- Legitimate tokens (SUI, SEI, STK, SAND, WIF) are NOT filtered
- Stablecoins are filtered for TOTAL2 calculation
- CSV export works correctly
"""

import tempfile
from pathlib import Path

import pytest

from analysis.filters import TokenFilter


class TestWrappedStakedTokenFiltering:
    """Tests for wrapped/staked/bridged token detection."""
    
    @pytest.fixture
    def token_filter(self):
        """Create a fresh TokenFilter instance."""
        return TokenFilter()
    
    # =========================================================================
    # Tokens that MUST be filtered out
    # =========================================================================
    
    @pytest.mark.parametrize("coin_id,name,symbol", [
        # Staked ETH variants
        ("lido-staked-ether", "Lido Staked Ether", "stETH"),
        ("staked-ether", "Staked Ether", "stETH"),
        ("wrapped-steth", "Wrapped stETH", "wstETH"),
        
        # Wrapped BTC variants
        ("wrapped-bitcoin", "Wrapped Bitcoin", "wBTC"),
        ("wbtc", "Wrapped Bitcoin", "WBTC"),
        
        # Wrapped ETH variants
        ("weth", "Wrapped Ether", "WETH"),
        ("wrapped-ether", "Wrapped Ether", "WETH"),
        ("wrapped-beacon-eth", "Wrapped Beacon ETH", "wbETH"),
        ("wrapped-eeth", "Wrapped eETH", "weETH"),
        
        # Liquid staking tokens
        ("jito-staked-sol", "Jito Staked SOL", "JitoSOL"),
        ("rocket-pool-eth", "Rocket Pool ETH", "rETH"),
        
        # Wrapped BNB
        ("wrapped-bnb", "Wrapped BNB", "wBNB"),
        ("wbnb", "Wrapped BNB", "WBNB"),
        
        # Binance staked
        ("bnsol", "Binance Staked SOL", "BNSOL"),
        
        # Kelp/Renzo restaked
        ("kelp-dao-restaked-eth", "Kelp DAO Restaked ETH", "rsETH"),
        
        # Various BTC derivatives
        ("fbtc", "Ignition FBTC", "FBTC"),
        ("lbtc", "Lombard Staked BTC", "LBTC"),
        ("solvbtc", "Solv BTC", "solvBTC"),
        ("lseth", "Liquid Staked ETH", "lsETH"),
        
        # KHYPE
        ("khype", "KHYPE", "KHYPE"),
        
        # Arbitrum bridged
        ("arbitrum-bridged-btc", "Arbitrum Bridged BTC", "BTC"),
        
        # Renzo
        ("renzo-restaked-eth", "Renzo Restaked ETH", "ezETH"),
        
        # Mantle staked
        ("mantle-staked-ether", "Mantle Staked Ether", "mETH"),
        
        # CLBTC
        ("clbtc", "CLBTC", "CLBTC"),
        
        # osETH
        ("oseth", "StakeWise Staked ETH", "osETH"),
        
        # L2 bridged WETH
        ("l2-standard-bridged-weth-base", "L2 Standard Bridged WETH", "WETH"),
        ("arbitrum-bridged-weth", "Arbitrum Bridged WETH", "WETH"),
        
        # tBTC
        ("tbtc", "tBTC", "tBTC"),
        ("threshold-btc", "Threshold BTC", "tBTC"),
        
        # Marinade
        ("marinade-staked-sol", "Marinade Staked SOL", "mSOL"),
        
        # Stader
        ("stader-ethx", "Stader ETHx", "ETHx"),
        
        # Ether.fi
        ("ether-fi-staked-eth", "Ether.fi Staked ETH", "eETH"),
        
        # Swell
        ("swell-staked-eth", "Swell Staked ETH", "swETH"),
        
        # sBTC
        ("sbtc", "Synth sBTC", "sBTC"),
        
        # cbETH
        ("coinbase-wrapped-staked-eth", "Coinbase Wrapped Staked ETH", "cbETH"),
        
        # enzoBTC
        ("enzobtc", "Enzo BTC", "enzoBTC"),
        ("enzo-btc", "Enzo BTC", "enzoBTC"),
        
        # Wrapped SOL
        ("wrapped-solana", "Wrapped SOL", "SOL"),
        ("wrapped-sol", "Wrapped SOL", "wSOL"),
        
        # syrupUSDC
        ("syrup-usdc", "Syrup USDC", "syrupUSDC"),
    ])
    def test_wrapped_staked_tokens_are_filtered(self, token_filter, coin_id, name, symbol):
        """Test that wrapped/staked/bridged tokens are correctly identified and filtered."""
        assert token_filter.is_wrapped_or_staked(coin_id, name), \
            f"Token {coin_id} ({name}) should be filtered as wrapped/staked"
    
    # =========================================================================
    # Tokens that MUST be accepted (not filtered)
    # =========================================================================
    
    @pytest.mark.parametrize("coin_id,name,symbol", [
        # Explicitly allowed tokens
        ("sui", "Sui", "SUI"),
        ("sei-network", "Sei", "SEI"),
        ("stk", "STK", "STK"),
        ("the-sandbox", "The Sandbox", "SAND"),
        ("dogwifhat", "dogwifhat", "WIF"),
        
        # Other legitimate tokens that might match patterns
        ("stellar", "Stellar", "XLM"),
        ("stacks", "Stacks", "STX"),
        ("starknet", "Starknet", "STRK"),
        ("storj", "Storj", "STORJ"),
        
        # Major coins
        ("ethereum", "Ethereum", "ETH"),
        ("solana", "Solana", "SOL"),
        ("cardano", "Cardano", "ADA"),
        ("polkadot", "Polkadot", "DOT"),
        ("avalanche-2", "Avalanche", "AVAX"),
        ("chainlink", "Chainlink", "LINK"),
    ])
    def test_legitimate_tokens_are_accepted(self, token_filter, coin_id, name, symbol):
        """Test that legitimate tokens are NOT filtered out."""
        assert not token_filter.is_wrapped_or_staked(coin_id, name), \
            f"Token {coin_id} ({name}) should NOT be filtered"
    
    # =========================================================================
    # Full filtering workflow tests
    # =========================================================================
    
    def test_filter_coins_excludes_wrapped_staked(self, token_filter):
        """Test that filter_coins correctly excludes wrapped/staked tokens."""
        coins = [
            {"id": "ethereum", "name": "Ethereum", "symbol": "ETH"},
            {"id": "wrapped-bitcoin", "name": "Wrapped Bitcoin", "symbol": "WBTC"},
            {"id": "solana", "name": "Solana", "symbol": "SOL"},
            {"id": "lido-staked-ether", "name": "Lido Staked Ether", "symbol": "stETH"},
            {"id": "sui", "name": "Sui", "symbol": "SUI"},
        ]
        
        filtered = token_filter.filter_coins(coins, for_total2=False)
        
        # Should have 3 coins: ETH, SOL, SUI
        assert len(filtered) == 3
        filtered_ids = {c["id"] for c in filtered}
        assert "ethereum" in filtered_ids
        assert "solana" in filtered_ids
        assert "sui" in filtered_ids
        assert "wrapped-bitcoin" not in filtered_ids
        assert "lido-staked-ether" not in filtered_ids
    
    def test_filter_coins_records_filtered_tokens(self, token_filter):
        """Test that filtered tokens are recorded for export."""
        coins = [
            {"id": "ethereum", "name": "Ethereum", "symbol": "ETH"},
            {"id": "wrapped-bitcoin", "name": "Wrapped Bitcoin", "symbol": "WBTC"},
            {"id": "lido-staked-ether", "name": "Lido Staked Ether", "symbol": "stETH"},
        ]
        
        token_filter.filter_coins(coins, for_total2=False, record_filtered=True)
        
        assert len(token_filter.filtered_tokens) == 2
        filtered_ids = {t.coin_id for t in token_filter.filtered_tokens}
        assert "wrapped-bitcoin" in filtered_ids
        assert "lido-staked-ether" in filtered_ids


class TestStablecoinFiltering:
    """Tests for stablecoin detection (for TOTAL2 calculation)."""
    
    @pytest.fixture
    def token_filter(self):
        return TokenFilter()
    
    @pytest.mark.parametrize("coin_id,name,symbol", [
        ("tether", "Tether", "USDT"),
        ("usd-coin", "USD Coin", "USDC"),
        ("dai", "Dai", "DAI"),
        ("usds", "USDS", "USDS"),
        ("ethena-usde", "Ethena USDe", "USDe"),
        ("paypal-usd", "PayPal USD", "PYUSD"),
        ("first-digital-usd", "First Digital USD", "FDUSD"),
        ("true-usd", "TrueUSD", "TUSD"),
        ("frax", "Frax", "FRAX"),
        ("gho", "GHO", "GHO"),
        ("usdd", "USDD", "USDD"),
    ])
    def test_stablecoins_are_detected(self, token_filter, coin_id, name, symbol):
        """Test that stablecoins are correctly identified."""
        assert token_filter.is_stablecoin(coin_id, name, symbol), \
            f"Token {coin_id} ({symbol}) should be identified as stablecoin"
    
    def test_filter_coins_excludes_stablecoins_for_total2(self, token_filter):
        """Test that stablecoins are excluded when for_total2=True."""
        coins = [
            {"id": "ethereum", "name": "Ethereum", "symbol": "ETH"},
            {"id": "tether", "name": "Tether", "symbol": "USDT"},
            {"id": "usd-coin", "name": "USD Coin", "symbol": "USDC"},
            {"id": "solana", "name": "Solana", "symbol": "SOL"},
        ]
        
        filtered = token_filter.filter_coins(coins, for_total2=True)
        
        assert len(filtered) == 2
        filtered_ids = {c["id"] for c in filtered}
        assert "ethereum" in filtered_ids
        assert "solana" in filtered_ids
        assert "tether" not in filtered_ids
        assert "usd-coin" not in filtered_ids
    
    def test_stablecoins_kept_when_not_for_total2(self, token_filter):
        """Test that stablecoins are NOT excluded when for_total2=False."""
        coins = [
            {"id": "ethereum", "name": "Ethereum", "symbol": "ETH"},
            {"id": "tether", "name": "Tether", "symbol": "USDT"},
        ]
        
        filtered = token_filter.filter_coins(coins, for_total2=False)
        
        # Stablecoins should be kept (only filtered for TOTAL2)
        assert len(filtered) == 2


class TestBTCDerivativeFiltering:
    """Tests for BTC derivative detection."""
    
    @pytest.fixture
    def token_filter(self):
        return TokenFilter()
    
    def test_bitcoin_is_not_filtered_as_derivative(self, token_filter):
        """Test that Bitcoin itself is not filtered as a derivative."""
        assert not token_filter.is_btc_derivative("bitcoin", "Bitcoin", "BTC")
    
    @pytest.mark.parametrize("coin_id,name,symbol", [
        ("wrapped-bitcoin", "Wrapped Bitcoin", "WBTC"),
        ("tbtc", "tBTC", "tBTC"),
        ("solvbtc", "Solv BTC", "solvBTC"),
        ("lbtc", "Lombard Staked BTC", "LBTC"),
        ("cbbtc", "Coinbase Wrapped BTC", "cbBTC"),
    ])
    def test_btc_derivatives_are_detected(self, token_filter, coin_id, name, symbol):
        """Test that BTC derivatives are correctly identified."""
        assert token_filter.is_btc_derivative(coin_id, name, symbol), \
            f"Token {coin_id} should be identified as BTC derivative"


class TestCSVExport:
    """Tests for CSV export functionality."""
    
    @pytest.fixture
    def token_filter(self):
        return TokenFilter()
    
    def test_export_rejected_coins_csv(self, token_filter):
        """Test that rejected coins can be exported to CSV."""
        coins = [
            {"id": "ethereum", "name": "Ethereum", "symbol": "ETH"},
            {"id": "wrapped-bitcoin", "name": "Wrapped Bitcoin", "symbol": "WBTC"},
            {"id": "lido-staked-ether", "name": "Lido Staked Ether", "symbol": "stETH"},
        ]
        
        token_filter.filter_coins(coins, for_total2=False, record_filtered=True)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "rejected.csv"
            result_path = token_filter.export_rejected_coins_csv(csv_path)
            
            assert result_path.exists()
            
            # Read and verify CSV content
            with open(result_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            assert "wrapped-bitcoin" in content
            assert "lido-staked-ether" in content
            assert "coingecko.com" in content
    
    def test_csv_uses_semicolon_delimiter(self, token_filter):
        """Test that CSV uses semicolon delimiter for Excel compatibility."""
        coins = [
            {"id": "wrapped-bitcoin", "name": "Wrapped Bitcoin", "symbol": "WBTC"},
        ]
        
        token_filter.filter_coins(coins, for_total2=False, record_filtered=True)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "rejected.csv"
            token_filter.export_rejected_coins_csv(csv_path)
            
            with open(csv_path, "r", encoding="utf-8") as f:
                first_line = f.readline()
            
            # Header should have semicolons
            assert ";" in first_line
            assert first_line.count(";") == 4  # 5 columns = 4 semicolons


class TestFilteredSummary:
    """Tests for filter summary functionality."""
    
    @pytest.fixture
    def token_filter(self):
        return TokenFilter()
    
    def test_get_filtered_summary(self, token_filter):
        """Test that summary counts are correct."""
        coins = [
            {"id": "ethereum", "name": "Ethereum", "symbol": "ETH"},
            {"id": "wrapped-bitcoin", "name": "Wrapped Bitcoin", "symbol": "WBTC"},
            {"id": "lido-staked-ether", "name": "Lido Staked Ether", "symbol": "stETH"},
            {"id": "tether", "name": "Tether", "symbol": "USDT"},
            {"id": "usd-coin", "name": "USD Coin", "symbol": "USDC"},
        ]
        
        token_filter.filter_coins(coins, for_total2=True, record_filtered=True)
        
        summary = token_filter.get_filtered_summary()
        
        assert "Wrapped/Staked/Bridged token" in summary
        assert summary["Wrapped/Staked/Bridged token"] == 2
        assert "Stablecoin" in summary
        assert summary["Stablecoin"] == 2


class TestAllowedTokensOverride:
    """Tests for allowed tokens override functionality."""
    
    @pytest.fixture
    def token_filter(self):
        return TokenFilter()
    
    @pytest.mark.parametrize("coin_id", [
        "sui",
        "sei-network",
        "sei",
        "stk",
        "the-sandbox",
        "sand",
        "dogwifhat",
        "wif",
        "stellar",
        "stacks",
        "starknet",
    ])
    def test_allowed_tokens_are_never_filtered(self, token_filter, coin_id):
        """Test that allowed tokens are never filtered regardless of patterns."""
        assert token_filter.is_allowed_token(coin_id), \
            f"Token {coin_id} should be in allowed list"
        
        assert not token_filter.is_wrapped_or_staked(coin_id, ""), \
            f"Token {coin_id} should not be filtered as wrapped/staked"
        
        assert not token_filter.is_stablecoin(coin_id, "", ""), \
            f"Token {coin_id} should not be filtered as stablecoin"

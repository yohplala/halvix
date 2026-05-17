"""
Tests for coin filtering in Halvix.

Tests that:
- Wrapped, staked, bridged tokens are correctly filtered out
- Legitimate tokens (SUI, SEI, STK, SAND, WIF) are NOT filtered
- Stablecoins are always filtered
- CSV export works correctly
"""

import tempfile
from pathlib import Path

import pytest

from analysis.filters import CoinFilter


class TestWrappedStakedTokenFiltering:
    """Tests for wrapped/staked/bridged token detection."""

    @pytest.fixture
    def coin_filter(self):
        """Create a fresh CoinFilter instance."""
        return CoinFilter()

    # =========================================================================
    # Tokens that MUST be filtered out (using lowercase symbols as IDs)
    # =========================================================================

    @pytest.mark.parametrize(
        "coin_id,name,symbol",
        [
            # Staked ETH variants
            ("steth", "Lido Staked Ether", "STETH"),
            ("wsteth", "Wrapped stETH", "WSTETH"),
            # Wrapped BTC variants
            ("wbtc", "Wrapped Bitcoin", "WBTC"),
            # Wrapped ETH variants
            ("weth", "Wrapped Ether", "WETH"),
            ("wbeth", "Wrapped Beacon ETH", "WBETH"),
            ("weeth", "Wrapped eETH", "WEETH"),
            # Liquid staking tokens
            ("jitosol", "Jito Staked SOL", "JITOSOL"),
            ("reth", "Rocket Pool ETH", "RETH"),
            # Wrapped BNB
            ("wbnb", "Wrapped BNB", "WBNB"),
            # Binance staked
            ("bnsol", "Binance Staked SOL", "BNSOL"),
            # Kelp/Renzo restaked
            ("rseth", "Kelp DAO Restaked ETH", "RSETH"),
            # Various BTC derivatives
            ("fbtc", "Ignition FBTC", "FBTC"),
            ("lbtc", "Lombard Staked BTC", "LBTC"),
            ("solvbtc", "Solv BTC", "SOLVBTC"),
            ("lseth", "Liquid Staked ETH", "LSETH"),
            # Renzo
            ("ezeth", "Renzo Restaked ETH", "EZETH"),
            # Mantle staked
            ("meth", "Mantle Staked Ether", "METH"),
            # osETH
            ("oseth", "StakeWise Staked ETH", "OSETH"),
            # tBTC
            ("tbtc", "tBTC", "TBTC"),
            # Marinade
            ("msol", "Marinade Staked SOL", "MSOL"),
            # ETHx
            ("ethx", "Stader ETHx", "ETHX"),
            # eETH
            ("eeth", "Ether.fi Staked ETH", "EETH"),
            # Swell
            ("sweth", "Swell Staked ETH", "SWETH"),
            # cbETH
            ("cbeth", "Coinbase Wrapped Staked ETH", "CBETH"),
        ],
    )
    def test_wrapped_staked_tokens_are_filtered(self, coin_filter, coin_id, name, symbol):
        """Test that wrapped/staked/bridged tokens are correctly identified and filtered."""
        assert coin_filter.is_wrapped_or_staked(
            coin_id, name, symbol
        ), f"Token {coin_id} ({name}) should be filtered as wrapped/staked"

    # =========================================================================
    # Tokens that MUST be accepted (not filtered)
    # =========================================================================

    @pytest.mark.parametrize(
        "coin_id,name,symbol",
        [
            # Explicitly allowed tokens
            ("sui", "Sui", "SUI"),
            ("sei", "Sei", "SEI"),
            ("stk", "STK", "STK"),
            ("sand", "The Sandbox", "SAND"),
            ("wif", "dogwifhat", "WIF"),
            # Other legitimate tokens that might match patterns
            ("xlm", "Stellar", "XLM"),
            ("stx", "Stacks", "STX"),
            ("strk", "Starknet", "STRK"),
            ("storj", "Storj", "STORJ"),
            # Major coins
            ("eth", "Ethereum", "ETH"),
            ("sol", "Solana", "SOL"),
            ("ada", "Cardano", "ADA"),
            ("dot", "Polkadot", "DOT"),
            ("avax", "Avalanche", "AVAX"),
            ("link", "Chainlink", "LINK"),
        ],
    )
    def test_legitimate_tokens_are_accepted(self, coin_filter, coin_id, name, symbol):
        """Test that legitimate tokens are NOT filtered out."""
        assert not coin_filter.is_wrapped_or_staked(
            coin_id, name, symbol
        ), f"Token {coin_id} ({name}) should NOT be filtered"

    # =========================================================================
    # Full filtering workflow tests
    # =========================================================================

    def test_filter_coins_for_download_excludes_wrapped_staked(self, coin_filter):
        """Test that filter_coins_for_download correctly excludes wrapped/staked tokens."""
        coins = [
            {"id": "eth", "name": "Ethereum", "symbol": "ETH"},
            {"id": "wbtc", "name": "Wrapped Bitcoin", "symbol": "WBTC"},
            {"id": "sol", "name": "Solana", "symbol": "SOL"},
            {"id": "steth", "name": "Lido Staked Ether", "symbol": "STETH"},
            {"id": "sui", "name": "Sui", "symbol": "SUI"},
            {"id": "btc", "name": "Bitcoin", "symbol": "BTC"},  # BTC should be included
        ]

        filtered = coin_filter.get_coins_to_download(coins)

        # Should have 4 coins: ETH, SOL, SUI, BTC (BTC is included for download)
        assert len(filtered) == 4
        filtered_ids = {c["id"] for c in filtered}
        assert "eth" in filtered_ids
        assert "sol" in filtered_ids
        assert "sui" in filtered_ids
        assert "btc" in filtered_ids  # BTC included for download
        assert "wbtc" not in filtered_ids
        assert "steth" not in filtered_ids

    def test_filter_coins_for_download_records_skipped_coins(self, coin_filter):
        """Test that skipped coins are recorded for export."""
        coins = [
            {"id": "eth", "name": "Ethereum", "symbol": "ETH"},
            {"id": "wbtc", "name": "Wrapped Bitcoin", "symbol": "WBTC"},
            {"id": "steth", "name": "Lido Staked Ether", "symbol": "STETH"},
        ]

        coin_filter.get_coins_to_download(coins, record_skipped=True)

        assert len(coin_filter.skipped_coins) == 2
        skipped_ids = {c.coin_id for c in coin_filter.skipped_coins}
        assert "wbtc" in skipped_ids
        assert "steth" in skipped_ids


class TestStablecoinFiltering:
    """Tests for stablecoin detection."""

    @pytest.fixture
    def coin_filter(self):
        return CoinFilter()

    @pytest.mark.parametrize(
        "coin_id,name,symbol",
        [
            ("usdt", "Tether", "USDT"),
            ("usdc", "USD Coin", "USDC"),
            ("dai", "Dai", "DAI"),
            ("usds", "USDS", "USDS"),
            ("usde", "Ethena USDe", "USDE"),
            ("pyusd", "PayPal USD", "PYUSD"),
            ("fdusd", "First Digital USD", "FDUSD"),
            ("tusd", "TrueUSD", "TUSD"),
            ("frax", "Frax", "FRAX"),
            ("gho", "GHO", "GHO"),
            ("usdd", "USDD", "USDD"),
            # Empirically-confirmed stablecoins (see config.EXCLUDED_STABLECOINS)
            ("usat", "USAT", "USAT"),
            ("usdcv", "USD CoinVertible", "USDCV"),
            ("usdr", "USDR", "USDR"),
            ("usdq", "USDQ", "USDQ"),
            ("aeur", "Anchored EUR", "AEUR"),
            ("europ", "EUROP", "EUROP"),
        ],
    )
    def test_stablecoins_are_detected(self, coin_filter, coin_id, name, symbol):
        """Test that stablecoins are correctly identified."""
        assert coin_filter.is_stablecoin(
            coin_id, name, symbol
        ), f"Token {coin_id} ({symbol}) should be identified as stablecoin"

    def test_filter_coins_for_download_excludes_stablecoins(self, coin_filter):
        """Test that stablecoins are always excluded from download."""
        coins = [
            {"id": "eth", "name": "Ethereum", "symbol": "ETH"},
            {"id": "usdt", "name": "Tether", "symbol": "USDT"},
            {"id": "usdc", "name": "USD Coin", "symbol": "USDC"},
            {"id": "sol", "name": "Solana", "symbol": "SOL"},
        ]

        filtered = coin_filter.get_coins_to_download(coins)

        assert len(filtered) == 2
        filtered_ids = {c["id"] for c in filtered}
        assert "eth" in filtered_ids
        assert "sol" in filtered_ids
        assert "usdt" not in filtered_ids
        assert "usdc" not in filtered_ids


class TestBTCDerivativeFiltering:
    """Tests for BTC derivative detection."""

    @pytest.fixture
    def coin_filter(self):
        return CoinFilter()

    def test_bitcoin_is_not_filtered_as_derivative(self, coin_filter):
        """Test that Bitcoin itself is not filtered as a derivative."""
        assert not coin_filter.is_btc_derivative("btc", "Bitcoin", "BTC")

    @pytest.mark.parametrize(
        "coin_id,name,symbol",
        [
            ("wbtc", "Wrapped Bitcoin", "WBTC"),
            ("tbtc", "tBTC", "TBTC"),
            ("solvbtc", "Solv BTC", "SOLVBTC"),
            ("lbtc", "Lombard Staked BTC", "LBTC"),
            ("cbbtc", "Coinbase Wrapped BTC", "CBBTC"),
        ],
    )
    def test_btc_derivatives_are_detected(self, coin_filter, coin_id, name, symbol):
        """Test that BTC derivatives are correctly identified."""
        assert coin_filter.is_btc_derivative(
            coin_id, name, symbol
        ), f"Token {coin_id} should be identified as BTC derivative"


class TestCSVExport:
    """Tests for CSV export functionality."""

    @pytest.fixture
    def coin_filter(self):
        return CoinFilter()

    def test_export_skipped_coins_csv(self, coin_filter):
        """Test that skipped coins can be exported to CSV."""
        coins = [
            {"id": "eth", "name": "Ethereum", "symbol": "ETH"},
            {"id": "wbtc", "name": "Wrapped Bitcoin", "symbol": "WBTC"},
            {"id": "steth", "name": "Lido Staked Ether", "symbol": "STETH"},
        ]

        coin_filter.get_coins_to_download(coins, record_skipped=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "skipped.csv"
            result_path = coin_filter.export_skipped_coins_csv(csv_path)

            assert result_path.exists()

            # Read and verify CSV content
            with open(result_path, encoding="utf-8") as f:
                content = f.read()

            assert "wbtc" in content
            assert "steth" in content
            assert "cryptocompare.com" in content

    def test_csv_uses_semicolon_delimiter(self, coin_filter):
        """Test that CSV uses semicolon delimiter for Excel compatibility."""
        coins = [
            {"id": "wbtc", "name": "Wrapped Bitcoin", "symbol": "WBTC"},
        ]

        coin_filter.get_coins_to_download(coins, record_skipped=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "skipped.csv"
            coin_filter.export_skipped_coins_csv(csv_path)

            with open(csv_path, encoding="utf-8") as f:
                first_line = f.readline()

            # Header should have semicolons
            assert ";" in first_line
            assert first_line.count(";") == 4  # 5 columns = 4 semicolons


class TestSkippedSummary:
    """Tests for skipped summary functionality."""

    @pytest.fixture
    def coin_filter(self):
        return CoinFilter()

    def test_get_skipped_summary(self, coin_filter):
        """Test that summary counts are correct."""
        coins = [
            {"id": "eth", "name": "Ethereum", "symbol": "ETH"},
            {"id": "wbtc", "name": "Wrapped Bitcoin", "symbol": "WBTC"},
            {"id": "steth", "name": "Lido Staked Ether", "symbol": "STETH"},
            {"id": "usdt", "name": "Tether", "symbol": "USDT"},
            {"id": "usdc", "name": "USD Coin", "symbol": "USDC"},
        ]

        coin_filter.get_coins_to_download(coins, record_skipped=True)

        summary = coin_filter.get_skipped_summary()

        assert "Wrapped/Staked/Bridged token" in summary
        assert summary["Wrapped/Staked/Bridged token"] == 2
        assert "Stablecoin" in summary
        assert summary["Stablecoin"] == 2


class TestAllowedTokensOverride:
    """Tests for allowed tokens override functionality."""

    @pytest.fixture
    def coin_filter(self):
        return CoinFilter()

    @pytest.mark.parametrize(
        "coin_id",
        [
            "sui",
            "sei",
            "stk",
            "sand",
            "wif",
            "xlm",
            "stx",
            "strk",
        ],
    )
    def test_allowed_tokens_are_never_filtered(self, coin_filter, coin_id):
        """Test that allowed tokens are never filtered regardless of patterns."""
        assert coin_filter.is_allowed_token(coin_id), f"Token {coin_id} should be in allowed list"

        assert not coin_filter.is_wrapped_or_staked(
            coin_id, "", coin_id.upper()
        ), f"Token {coin_id} should not be filtered as wrapped/staked"

        assert not coin_filter.is_stablecoin(
            coin_id, "", coin_id.upper()
        ), f"Token {coin_id} should not be filtered as stablecoin"

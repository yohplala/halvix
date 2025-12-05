"""
Token filtering module for Halvix.

Two-stage filtering:

1. DOWNLOAD FILTER (should_exclude_from_download):
   Excludes from price data download:
   - Stablecoins (stable vs fiat, not representative of crypto market trends)
   - Wrapped tokens (wBTC, wETH, AETHWETH, etc.)
   - Staked tokens (stETH, stSOL, etc.)
   - Bridged tokens
   - Liquid staking derivatives
   - BTC derivatives

   DOES NOT EXCLUDE: BTC (needed for BTC vs USD chart)

2. INDIVIDUAL ANALYSIS FILTER:
   After downloading, excludes from individual halving cycle analysis:
   - Bitcoin (base currency - we compare coins TO BTC)
   - Coins without sufficient historical data (before MIN_DATA_DATE)

   DOES NOT EXCLUDE from TOTAL2: recent coins (they ARE used for TOTAL2)
"""

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from config import (
    ALLOWED_TOKENS,
    CRYPTOCOMPARE_COIN_URL,
    DOWNLOAD_SKIPPED_CSV,
    EXCLUDED_PATTERNS,
    EXCLUDED_STABLECOINS,
    EXCLUDED_WRAPPED_STAKED_IDS,
)


@dataclass
class SkippedCoin:
    """Represents a coin that was skipped for download."""

    coin_id: str
    name: str
    symbol: str
    reason: str
    url: str


# Backwards compatibility alias
FilteredToken = SkippedCoin


class TokenFilter:
    """
    Filter tokens based on various exclusion criteria.

    Two filtering modes:

    1. For DOWNLOAD (should_skip_download):
       Skips: stablecoins, wrapped/staked/bridged tokens, BTC derivatives
       Downloads: BTC (needed for charting), all other coins

    2. For TOTAL2 (should_exclude_from_total2):
       Excludes: BTC, stablecoins, wrapped/staked/bridged tokens, BTC derivatives
       Includes: All coins including recent ones (for index immutability)

    3. For INDIVIDUAL ANALYSIS:
       Excludes: BTC, coins without data before MIN_DATA_DATE
       (handled in fetcher after price data is downloaded)

    Maintains a list of skipped coins for export and review.
    """

    def __init__(self):
        self.skipped_coins: list[SkippedCoin] = []
        self._compiled_patterns = [
            re.compile(pattern, re.IGNORECASE) for pattern in EXCLUDED_PATTERNS
        ]

    # Property for backwards compatibility
    @property
    def filtered_tokens(self) -> list[SkippedCoin]:
        """Backwards compatibility alias for skipped_coins."""
        return self.skipped_coins

    def reset(self):
        """Clear the skipped coins list."""
        self.skipped_coins = []

    def is_allowed_token(self, coin_id: str, symbol: str = "") -> bool:
        """
        Check if token is in the allowed list (override exclusions).

        Args:
            coin_id: The coin ID (lowercase symbol)
            symbol: The coin symbol (optional, for additional matching)

        Returns:
            True if token should never be filtered out
        """
        coin_id_lower = coin_id.lower()
        symbol_lower = symbol.lower() if symbol else ""
        return coin_id_lower in ALLOWED_TOKENS or symbol_lower in ALLOWED_TOKENS

    def is_stablecoin(self, coin_id: str, name: str = "", symbol: str = "") -> bool:
        """
        Check if token is a stablecoin.

        Args:
            coin_id: The coin ID (lowercase symbol)
            name: The coin name (optional)
            symbol: The coin symbol (optional)

        Returns:
            True if token is a stablecoin
        """
        # Check allowed list first
        if self.is_allowed_token(coin_id, symbol):
            return False

        coin_id_lower = coin_id.lower()

        # Check exact ID match
        if coin_id_lower in EXCLUDED_STABLECOINS:
            return True

        # Check if symbol matches common stablecoin symbols
        symbol_lower = symbol.lower() if symbol else ""
        stablecoin_symbols = {
            "usdt",
            "usdc",
            "dai",
            "usds",
            "usde",
            "pyusd",
            "tusd",
            "busd",
            "gusd",
            "usdp",
            "lusd",
            "frax",
            "mim",
            "gho",
            "fdusd",
            "usdd",
            "susd",
            "eurs",
            "eurt",
            "usdy",
            "usdg",
        }
        if symbol_lower in stablecoin_symbols:
            return True

        return False

    def is_wrapped_or_staked(self, coin_id: str, name: str = "", symbol: str = "") -> bool:
        """
        Check if token is a wrapped, staked, or bridged token.

        Args:
            coin_id: The coin ID (lowercase symbol)
            name: The coin name (optional)
            symbol: The coin symbol (optional)

        Returns:
            True if token is wrapped, staked, or bridged
        """
        # Check allowed list first
        if self.is_allowed_token(coin_id, symbol):
            return False

        coin_id_lower = coin_id.lower()
        name_lower = name.lower() if name else ""

        # Check exact ID match
        if coin_id_lower in EXCLUDED_WRAPPED_STAKED_IDS:
            return True

        # Check patterns against ID and name
        combined_text = f"{coin_id_lower} {name_lower}"

        for pattern in self._compiled_patterns:
            if pattern.search(combined_text):
                return True

        return False

    def is_btc_derivative(self, coin_id: str, name: str = "", symbol: str = "") -> bool:
        """
        Check if token is a BTC derivative (wrapped, staked, bridged BTC).

        Args:
            coin_id: The coin ID (lowercase symbol)
            name: The coin name (optional)
            symbol: The coin symbol (optional)

        Returns:
            True if token is a BTC derivative
        """
        # Check allowed list first
        if self.is_allowed_token(coin_id, symbol):
            return False

        coin_id_lower = coin_id.lower()
        name_lower = name.lower() if name else ""
        symbol_lower = symbol.lower() if symbol else ""

        # Check if it's the original Bitcoin (BTC) - not a derivative
        if coin_id_lower == "btc" or symbol_lower == "btc":
            return False

        combined = f"{coin_id_lower} {name_lower} {symbol_lower}"

        # BTC patterns - use regex for consistent matching
        btc_pattern = re.compile(r"btc|bitcoin", re.IGNORECASE)

        # Derivative patterns - use regex for consistent matching
        derivative_pattern = re.compile(
            r"wrapped|staked|bridged|liquid|synthetic|pegged|collateral|vault|yield", re.IGNORECASE
        )

        has_btc = btc_pattern.search(combined) is not None
        has_derivative = derivative_pattern.search(combined) is not None

        # If it has BTC in name AND derivative keyword, exclude it
        if has_btc and has_derivative:
            return True

        # Check specific BTC derivative symbols
        btc_derivative_symbols = {
            "wbtc",
            "tbtc",
            "hbtc",
            "renbtc",
            "sbtc",
            "fbtc",
            "lbtc",
            "solvbtc",
            "clbtc",
            "cbbtc",
            "enzobtc",
        }

        return coin_id_lower in btc_derivative_symbols or symbol_lower in btc_derivative_symbols

    def should_skip_download(
        self,
        coin_id: str,
        name: str = "",
        symbol: str = "",
    ) -> tuple[bool, str]:
        """
        Check if a coin should be skipped from price data download.

        Skips: stablecoins, wrapped/staked/bridged, BTC derivatives
        Downloads: BTC (needed for charting), all other coins

        Args:
            coin_id: The coin ID (lowercase symbol)
            name: The coin name
            symbol: The coin symbol

        Returns:
            Tuple of (should_skip, reason)
        """
        # Check allowed list first
        if self.is_allowed_token(coin_id, symbol):
            return (False, "")

        # BTC is NOT skipped - we need it for BTC vs USD chart
        # (it will be excluded from TOTAL2 separately)

        # Check stablecoins (always skipped - stable vs fiat)
        if self.is_stablecoin(coin_id, name, symbol):
            return (True, "Stablecoin")

        # Check wrapped/staked/bridged
        if self.is_wrapped_or_staked(coin_id, name, symbol):
            return (True, "Wrapped/Staked/Bridged token")

        # Check BTC derivatives
        if self.is_btc_derivative(coin_id, name, symbol):
            return (True, "BTC derivative")

        return (False, "")

    # Backwards compatibility alias
    def should_exclude_from_download(
        self,
        coin_id: str,
        name: str = "",
        symbol: str = "",
    ) -> tuple[bool, str]:
        """Backwards compatibility alias for should_skip_download."""
        return self.should_skip_download(coin_id, name, symbol)

    def should_exclude_from_total2(
        self,
        coin_id: str,
        name: str = "",
        symbol: str = "",
    ) -> tuple[bool, str]:
        """
        Check if a token should be excluded from TOTAL2 calculation.

        Excludes: BTC, stablecoins, wrapped/staked/bridged, BTC derivatives
        Includes: Recent coins (for index immutability)

        Args:
            coin_id: The coin ID (lowercase symbol)
            name: The coin name
            symbol: The coin symbol

        Returns:
            Tuple of (should_exclude, reason)
        """
        coin_id_lower = coin_id.lower()
        symbol_lower = symbol.lower() if symbol else ""

        # Check allowed list first
        if self.is_allowed_token(coin_id, symbol):
            return (False, "")

        # Check if it's Bitcoin itself - excluded from TOTAL2
        if coin_id_lower == "btc" or symbol_lower == "btc":
            return (True, "Bitcoin (base currency)")

        # Check stablecoins
        if self.is_stablecoin(coin_id, name, symbol):
            return (True, "Stablecoin")

        # Check wrapped/staked/bridged
        if self.is_wrapped_or_staked(coin_id, name, symbol):
            return (True, "Wrapped/Staked/Bridged token")

        # Check BTC derivatives
        if self.is_btc_derivative(coin_id, name, symbol):
            return (True, "BTC derivative")

        return (False, "")

    def get_coins_to_download(
        self,
        coins: list[dict],
        record_skipped: bool = True,
    ) -> list[dict]:
        """
        Get coins that should have price data downloaded.

        Skips: stablecoins, wrapped/staked/bridged, BTC derivatives
        Downloads: BTC and all other coins

        Args:
            coins: List of coin dictionaries with 'id', 'name', 'symbol' keys
            record_skipped: If True, record skipped coins for export

        Returns:
            List of coins to download (includes BTC)
        """
        to_download = []

        for coin in coins:
            coin_id = coin.get("id", "")
            name = coin.get("name", "")
            symbol = coin.get("symbol", "")

            should_skip, reason = self.should_skip_download(coin_id, name, symbol)

            if should_skip:
                if record_skipped:
                    self.skipped_coins.append(
                        SkippedCoin(
                            coin_id=coin_id,
                            name=name,
                            symbol=symbol,
                            reason=reason,
                            url=f"{CRYPTOCOMPARE_COIN_URL}/{symbol.upper()}/overview",
                        )
                    )
            else:
                to_download.append(coin)

        return to_download

    # Backwards compatibility alias
    def filter_coins_for_download(
        self,
        coins: list[dict],
        record_filtered: bool = True,
    ) -> list[dict]:
        """Backwards compatibility alias for get_coins_to_download."""
        return self.get_coins_to_download(coins, record_skipped=record_filtered)

    def filter_coins_for_total2(
        self,
        coins: list[dict],
    ) -> list[dict]:
        """
        Filter coins for TOTAL2 calculation.

        Excludes: BTC, stablecoins, wrapped/staked/bridged, BTC derivatives
        Includes: Recent coins (for index immutability)

        Does NOT record filtered tokens (use filter_coins_for_download for that).

        Args:
            coins: List of coin dictionaries with 'id', 'name', 'symbol' keys

        Returns:
            Filtered list of coins (excludes BTC)
        """
        filtered = []

        for coin in coins:
            coin_id = coin.get("id", "")
            name = coin.get("name", "")
            symbol = coin.get("symbol", "")

            should_exclude, _ = self.should_exclude_from_total2(coin_id, name, symbol)

            if not should_exclude:
                filtered.append(coin)

        return filtered

    def export_skipped_coins_csv(self, filepath: Path | None = None) -> Path:
        """
        Export skipped coins to CSV for review.

        Args:
            filepath: Optional custom path for CSV file

        Returns:
            Path to the created CSV file
        """
        filepath = filepath or DOWNLOAD_SKIPPED_CSV
        filepath.parent.mkdir(parents=True, exist_ok=True)

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter=";")  # Use semicolon for Excel compatibility
            writer.writerow(["Coin ID", "Name", "Symbol", "Reason", "URL"])

            for coin in sorted(self.skipped_coins, key=lambda c: c.coin_id):
                writer.writerow([coin.coin_id, coin.name, coin.symbol, coin.reason, coin.url])

        return filepath

    # Backwards compatibility alias
    def export_rejected_coins_csv(self, filepath: Path | None = None) -> Path:
        """Backwards compatibility alias for export_skipped_coins_csv."""
        return self.export_skipped_coins_csv(filepath)

    def get_skipped_summary(self) -> dict:
        """
        Get a summary of skipped coins by reason.

        Returns:
            Dictionary with counts by reason
        """
        summary = {}
        for coin in self.skipped_coins:
            reason = coin.reason
            summary[reason] = summary.get(reason, 0) + 1
        return summary

    # Backwards compatibility alias
    def get_filtered_summary(self) -> dict:
        """Backwards compatibility alias for get_skipped_summary."""
        return self.get_skipped_summary()

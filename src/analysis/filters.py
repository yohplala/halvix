"""
Token filtering module for Halvix.

Filters out from all analysis (halving cycles and TOTAL2):
- Bitcoin (base currency)
- Stablecoins (no price movement vs BTC)
- Wrapped tokens (wBTC, wETH, AETHWETH, etc.)
- Staked tokens (stETH, stSOL, etc.)
- Bridged tokens
- Liquid staking derivatives
- BTC derivatives
"""

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from config import (
    ALLOWED_TOKENS,
    CRYPTOCOMPARE_COIN_URL,
    EXCLUDED_PATTERNS,
    EXCLUDED_STABLECOINS,
    EXCLUDED_WRAPPED_STAKED_IDS,
    REJECTED_COINS_CSV,
)


@dataclass
class FilteredToken:
    """Represents a token that was filtered out."""

    coin_id: str
    name: str
    symbol: str
    reason: str
    url: str


class TokenFilter:
    """
    Filter tokens based on various exclusion criteria.

    Always excludes:
    - Bitcoin (base currency for analysis)
    - Stablecoins (no meaningful price movement vs BTC)
    - Wrapped tokens (wBTC, wETH, etc.)
    - Staked/Liquid staking tokens (stETH, JitoSOL, etc.)
    - Bridged tokens
    - BTC derivatives

    Maintains a list of filtered tokens for export and review.
    """

    def __init__(self):
        self.filtered_tokens: list[FilteredToken] = []
        self._compiled_patterns = [
            re.compile(pattern, re.IGNORECASE) for pattern in EXCLUDED_PATTERNS
        ]

    def reset(self):
        """Clear the filtered tokens list."""
        self.filtered_tokens = []

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

    def should_exclude(
        self, coin_id: str, name: str = "", symbol: str = "", for_total2: bool = False
    ) -> tuple[bool, str]:
        """
        Check if a token should be excluded.

        Args:
            coin_id: The coin ID (lowercase symbol)
            name: The coin name
            symbol: The coin symbol
            for_total2: Deprecated, kept for backwards compatibility (stablecoins always excluded)

        Returns:
            Tuple of (should_exclude, reason)
        """
        coin_id_lower = coin_id.lower()
        symbol_lower = symbol.lower() if symbol else ""

        # Check allowed list first
        if self.is_allowed_token(coin_id, symbol):
            return (False, "")

        # Check if it's Bitcoin itself (always exclude from non-BTC analysis)
        if coin_id_lower == "btc" or symbol_lower == "btc":
            return (True, "Bitcoin (base currency)")

        # Check stablecoins (always excluded - no price movement relative to BTC)
        if self.is_stablecoin(coin_id, name, symbol):
            return (True, "Stablecoin")

        # Check wrapped/staked/bridged
        if self.is_wrapped_or_staked(coin_id, name, symbol):
            return (True, "Wrapped/Staked/Bridged token")

        # Check BTC derivatives
        if self.is_btc_derivative(coin_id, name, symbol):
            return (True, "BTC derivative")

        return (False, "")

    def filter_coins(
        self, coins: list[dict], for_total2: bool = False, record_filtered: bool = True
    ) -> list[dict]:
        """
        Filter a list of coins based on exclusion criteria.

        Args:
            coins: List of coin dictionaries with 'id', 'name', 'symbol' keys
            for_total2: If True, also exclude stablecoins
            record_filtered: If True, record filtered tokens for export

        Returns:
            Filtered list of coins
        """
        filtered = []

        for coin in coins:
            coin_id = coin.get("id", "")
            name = coin.get("name", "")
            symbol = coin.get("symbol", "")

            should_exclude, reason = self.should_exclude(
                coin_id, name, symbol, for_total2=for_total2
            )

            if should_exclude:
                if record_filtered:
                    self.filtered_tokens.append(
                        FilteredToken(
                            coin_id=coin_id,
                            name=name,
                            symbol=symbol,
                            reason=reason,
                            url=f"{CRYPTOCOMPARE_COIN_URL}/{symbol.upper()}/overview",
                        )
                    )
            else:
                filtered.append(coin)

        return filtered

    def export_rejected_coins_csv(self, filepath: Path | None = None) -> Path:
        """
        Export rejected coins to CSV for review.

        Args:
            filepath: Optional custom path for CSV file

        Returns:
            Path to the created CSV file
        """
        filepath = filepath or REJECTED_COINS_CSV
        filepath.parent.mkdir(parents=True, exist_ok=True)

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter=";")  # Use semicolon for Excel compatibility
            writer.writerow(["Coin ID", "Name", "Symbol", "Reason", "URL"])

            for token in sorted(self.filtered_tokens, key=lambda t: t.coin_id):
                writer.writerow([token.coin_id, token.name, token.symbol, token.reason, token.url])

        return filepath

    def get_filtered_summary(self) -> dict:
        """
        Get a summary of filtered tokens by reason.

        Returns:
            Dictionary with counts by reason
        """
        summary = {}
        for token in self.filtered_tokens:
            reason = token.reason
            summary[reason] = summary.get(reason, 0) + 1
        return summary

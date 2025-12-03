"""
Symbol mapping and validation between CoinGecko and CryptoCompare.

Validates that CoinGecko coin IDs correctly map to CryptoCompare symbols
by cross-checking prices from both APIs.
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from config import (
    SYMBOL_MAPPING_FILE,
    SYMBOL_MAPPING_TOLERANCE_PERCENT,
)


@dataclass
class SymbolMapping:
    """
    Represents a validated mapping between CoinGecko and CryptoCompare.

    Attributes:
        coingecko_id: CoinGecko coin ID (e.g., "ethereum")
        coingecko_symbol: CoinGecko symbol (e.g., "eth")
        cryptocompare_symbol: CryptoCompare symbol (e.g., "ETH")
        validated_at: When the mapping was validated
        coingecko_price: Price from CoinGecko at validation time
        cryptocompare_price: Price from CryptoCompare at validation time
        price_diff_percent: Percentage difference between prices
        is_valid: Whether the mapping passed validation
        error_message: Error message if validation failed
    """

    coingecko_id: str
    coingecko_symbol: str
    cryptocompare_symbol: str
    validated_at: str  # ISO format datetime string
    coingecko_price: float
    cryptocompare_price: float
    price_diff_percent: float
    is_valid: bool
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SymbolMapping":
        """Create from dictionary."""
        return cls(**data)


class SymbolMappingCache:
    """
    Manages and validates mappings between CoinGecko IDs and CryptoCompare symbols.

    Validation process:
    1. Get recent price from CoinGecko (current or yesterday's close)
    2. Get recent price from CryptoCompare (yesterday's close)
    3. Compare prices (must be within tolerance %)
    4. Cache valid mappings for future use

    Usage:
        cache = SymbolMappingCache()

        # Check if already validated
        if cache.has_mapping("ethereum"):
            symbol = cache.get_cryptocompare_symbol("ethereum")
        else:
            # Validate with API clients
            mapping = cache.validate_mapping(
                coingecko_id="ethereum",
                coingecko_symbol="eth",
                coingecko_client=cg_client,
                cryptocompare_client=cc_client,
            )
    """

    def __init__(
        self,
        cache_file: Path = SYMBOL_MAPPING_FILE,
        tolerance_percent: float = SYMBOL_MAPPING_TOLERANCE_PERCENT,
    ):
        """
        Initialize the symbol mapping cache.

        Args:
            cache_file: Path to the JSON cache file
            tolerance_percent: Maximum allowed price difference (%)
        """
        self.cache_file = cache_file
        self.tolerance_percent = tolerance_percent
        self._mappings: dict[str, SymbolMapping] = {}
        self._coingecko_prices_cache: dict[str, float] = {}  # Cached prices by coin ID
        self._load_cache()

    def _load_cache(self) -> None:
        """Load mappings from cache file."""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, encoding="utf-8") as f:
                    data = json.load(f)
                    self._mappings = {k: SymbolMapping.from_dict(v) for k, v in data.items()}
            except (json.JSONDecodeError, KeyError, TypeError):
                # Invalid cache file, start fresh
                self._mappings = {}

    def _save_cache(self) -> None:
        """Save mappings to cache file."""
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)

        data = {k: v.to_dict() for k, v in self._mappings.items()}

        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _calculate_price_diff(self, price1: float, price2: float) -> float:
        """
        Calculate percentage difference between two prices.

        Uses the average as the base to avoid bias.

        Args:
            price1: First price
            price2: Second price

        Returns:
            Percentage difference (0-100 scale)
        """
        if price1 == 0 and price2 == 0:
            return 0.0
        if price1 == 0 or price2 == 0:
            return float("inf")

        avg = (price1 + price2) / 2
        return abs(price1 - price2) / avg * 100

    def has_mapping(self, coingecko_id: str) -> bool:
        """
        Check if a coin has been validated (whether valid or invalid).

        Args:
            coingecko_id: CoinGecko coin ID

        Returns:
            True if mapping exists in cache
        """
        return coingecko_id in self._mappings

    def has_valid_mapping(self, coingecko_id: str) -> bool:
        """
        Check if a coin has a valid mapping.

        Args:
            coingecko_id: CoinGecko coin ID

        Returns:
            True if mapping exists and is valid
        """
        mapping = self._mappings.get(coingecko_id)
        return mapping is not None and mapping.is_valid

    def get_mapping(self, coingecko_id: str) -> SymbolMapping | None:
        """
        Get the full mapping for a coin.

        Args:
            coingecko_id: CoinGecko coin ID

        Returns:
            SymbolMapping or None if not found
        """
        return self._mappings.get(coingecko_id)

    def get_cryptocompare_symbol(self, coingecko_id: str) -> str | None:
        """
        Get the validated CryptoCompare symbol for a CoinGecko ID.

        Args:
            coingecko_id: CoinGecko coin ID

        Returns:
            CryptoCompare symbol if valid, None otherwise
        """
        mapping = self._mappings.get(coingecko_id)
        if mapping and mapping.is_valid:
            return mapping.cryptocompare_symbol
        return None

    def _get_coingecko_price(self, coingecko_id: str) -> float:
        """
        Get CoinGecko price from cache.

        Args:
            coingecko_id: CoinGecko coin ID

        Returns:
            Price in BTC, or 0.0 if not found
        """
        return self._coingecko_prices_cache.get(coingecko_id, 0.0)

    def _prefetch_coingecko_prices(
        self,
        coingecko_client: Any,
        n: int = 500,
    ) -> None:
        """
        Prefetch CoinGecko prices for batch validation.

        Fetches top N coins once and caches their prices.

        Args:
            coingecko_client: CoinGecko API client
            n: Number of top coins to fetch
        """
        try:
            coins = coingecko_client.get_top_coins(n=n, vs_currency="btc")
            self._coingecko_prices_cache = {c.id: c.current_price_btc or 0.0 for c in coins}
        except Exception:
            # On error, keep existing cache (may be empty)
            pass

    def validate_mapping(
        self,
        coingecko_id: str,
        coingecko_symbol: str,
        coingecko_client: Any,  # CoinGeckoClient
        cryptocompare_client: Any,  # CryptoCompareClient
        force_revalidate: bool = False,
    ) -> SymbolMapping:
        """
        Validate that a CoinGecko ID maps to the correct CryptoCompare symbol.

        Compares prices from both APIs to ensure we're talking about the same coin.

        Note: For batch validation, call validate_batch() which prefetches
        CoinGecko prices once to avoid repeated API calls.

        Args:
            coingecko_id: CoinGecko coin ID (e.g., "ethereum")
            coingecko_symbol: CoinGecko symbol (e.g., "eth")
            coingecko_client: CoinGecko API client
            cryptocompare_client: CryptoCompare API client
            force_revalidate: If True, revalidate even if cached

        Returns:
            SymbolMapping with validation results
        """
        # Return cached if already validated (unless forced)
        if not force_revalidate and coingecko_id in self._mappings:
            return self._mappings[coingecko_id]

        # CryptoCompare uses uppercase symbols
        cryptocompare_symbol = coingecko_symbol.upper()

        # Initialize values
        cg_price = 0.0
        cc_price = 0.0
        error_message = ""

        # Try to get CoinGecko price from prefetched cache first
        cg_price = self._get_coingecko_price(coingecko_id)

        # If not in cache, fetch individually (fallback for single validations)
        if cg_price == 0.0:
            try:
                coins = coingecko_client.get_top_coins(n=500, vs_currency="btc")
                cg_coin = next((c for c in coins if c.id == coingecko_id), None)

                if cg_coin and cg_coin.current_price_btc:
                    cg_price = cg_coin.current_price_btc
                    # Cache for future use
                    self._coingecko_prices_cache[coingecko_id] = cg_price
                else:
                    error_message = f"Coin {coingecko_id} not found in CoinGecko top 500"
            except Exception as e:
                error_message = f"CoinGecko error: {e}"

        if cg_price > 0:
            try:
                # Get CryptoCompare yesterday's close
                cc_data = cryptocompare_client.get_daily_history(
                    symbol=cryptocompare_symbol,
                    vs_currency="BTC",
                    limit=1,
                )

                if cc_data:
                    cc_price = cc_data[-1].get("close", 0)
                else:
                    error_message = f"No CryptoCompare data for {cryptocompare_symbol}"
            except Exception as e:
                error_message = f"CryptoCompare error: {e}"
        elif not error_message:
            error_message = f"Coin {coingecko_id} not found in CoinGecko data"

        # Calculate price difference
        price_diff = self._calculate_price_diff(cg_price, cc_price)
        is_valid = price_diff <= self.tolerance_percent and cg_price > 0 and cc_price > 0

        if not is_valid and not error_message:
            error_message = (
                f"Price difference {price_diff:.2f}% exceeds tolerance {self.tolerance_percent}%"
            )

        # Create mapping
        mapping = SymbolMapping(
            coingecko_id=coingecko_id,
            coingecko_symbol=coingecko_symbol,
            cryptocompare_symbol=cryptocompare_symbol,
            validated_at=datetime.now().isoformat(),
            coingecko_price=cg_price,
            cryptocompare_price=cc_price,
            price_diff_percent=price_diff if price_diff != float("inf") else -1,
            is_valid=is_valid,
            error_message=error_message,
        )

        # Cache and save
        self._mappings[coingecko_id] = mapping
        self._save_cache()

        return mapping

    def validate_batch(
        self,
        coins: list[dict[str, str]],
        coingecko_client: Any,
        cryptocompare_client: Any,
        skip_validated: bool = True,
        show_progress: bool = True,
    ) -> dict[str, SymbolMapping]:
        """
        Validate mappings for a batch of coins.

        This method prefetches CoinGecko prices once to avoid making
        repeated API calls for each coin validation.

        Args:
            coins: List of dicts with 'id' and 'symbol' keys
            coingecko_client: CoinGecko API client
            cryptocompare_client: CryptoCompare API client
            skip_validated: Skip coins already in cache
            show_progress: Print progress messages

        Returns:
            Dictionary of coin_id -> SymbolMapping
        """
        results = {}

        # Filter to unvalidated coins if requested
        if skip_validated:
            coins_to_validate = [c for c in coins if c["id"] not in self._mappings]
        else:
            coins_to_validate = coins

        if not coins_to_validate:
            if show_progress:
                print("All coins already validated.")
            return results

        if show_progress:
            print("Prefetching CoinGecko prices...")

        # Prefetch all CoinGecko prices at once (single API call)
        self._prefetch_coingecko_prices(coingecko_client, n=500)

        if show_progress:
            print(f"Validating {len(coins_to_validate)} symbol mappings...")

        for i, coin in enumerate(coins_to_validate):
            coin_id = coin["id"]
            symbol = coin.get("symbol", "")

            if show_progress and (i + 1) % 10 == 0:
                print(f"  Validated {i + 1}/{len(coins_to_validate)}...")

            mapping = self.validate_mapping(
                coingecko_id=coin_id,
                coingecko_symbol=symbol,
                coingecko_client=coingecko_client,
                cryptocompare_client=cryptocompare_client,
            )

            results[coin_id] = mapping

        if show_progress:
            valid_count = sum(1 for m in results.values() if m.is_valid)
            print(f"  Done: {valid_count}/{len(results)} valid mappings")

        return results

    def get_summary(self) -> dict[str, Any]:
        """
        Get a summary of all mappings.

        Returns:
            Dictionary with counts and lists
        """
        valid = [m for m in self._mappings.values() if m.is_valid]
        invalid = [m for m in self._mappings.values() if not m.is_valid]

        return {
            "total": len(self._mappings),
            "valid": len(valid),
            "invalid": len(invalid),
            "invalid_coins": [
                {
                    "id": m.coingecko_id,
                    "symbol": m.coingecko_symbol,
                    "error": m.error_message,
                }
                for m in invalid
            ],
        }

    def clear(self) -> int:
        """
        Clear all cached mappings.

        Returns:
            Number of mappings cleared
        """
        count = len(self._mappings)
        self._mappings = {}

        if self.cache_file.exists():
            self.cache_file.unlink()

        return count

    def remove_mapping(self, coingecko_id: str) -> bool:
        """
        Remove a specific mapping from cache.

        Args:
            coingecko_id: CoinGecko coin ID

        Returns:
            True if removed, False if not found
        """
        if coingecko_id in self._mappings:
            del self._mappings[coingecko_id]
            self._save_cache()
            return True
        return False

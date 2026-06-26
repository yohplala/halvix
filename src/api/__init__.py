"""
API client package for Halvix price data.

Halvix sources prices through a provider abstraction (``PriceProvider``). The
backend is auto-selected from the available API keys (see ``get_price_provider``):
CoinGecko when a CoinGecko key is set, CryptoCompare when only its key is set,
and CoinGecko keyless otherwise. CoinGecko offers full coin coverage + native
market-cap ranking.
"""

from typing import Any

from config import COINGECKO_API_KEY, CRYPTOCOMPARE_API_KEY

from .base import Coin, PriceProvider, PriceProviderError
from .coingecko import CoinGeckoClient, CoinGeckoError
from .cryptocompare import (
    APIError,
    CryptoCompareClient,
    CryptoCompareError,
    RateLimitError,
)


def _default_provider() -> str:
    """
    Auto-select the provider from configured API keys.

    Prefers CoinGecko (a key unlocks reliable, full-coverage data); uses
    CryptoCompare only when just its key is set; otherwise CoinGecko keyless.
    """
    if COINGECKO_API_KEY:
        return "coingecko"
    if CRYPTOCOMPARE_API_KEY:
        return "cryptocompare"
    return "coingecko"


def get_price_provider(name: str | None = None, **kwargs: Any) -> PriceProvider:
    """
    Build the price provider.

    Args:
        name: Explicit backend ("coingecko" or "cryptocompare"). When omitted,
            the backend is auto-selected from the configured API keys.
        **kwargs: Forwarded to the concrete client constructor.

    Returns:
        A ready-to-use ``PriceProvider``.
    """
    provider = (name or _default_provider()).strip().lower()
    if provider == "cryptocompare":
        return CryptoCompareClient(**kwargs)
    if provider == "coingecko":
        return CoinGeckoClient(**kwargs)
    raise ValueError(
        f"Unknown price provider {provider!r}; expected 'coingecko' or 'cryptocompare'."
    )


__all__ = [
    "Coin",
    "PriceProvider",
    "PriceProviderError",
    "get_price_provider",
    "CoinGeckoClient",
    "CoinGeckoError",
    "CryptoCompareClient",
    "CryptoCompareError",
    "APIError",
    "RateLimitError",
]

"""
API client package for Halvix price data.

Halvix sources prices through a provider abstraction (``PriceProvider``).
CoinGecko is the default backend (full coin coverage + native market-cap
ranking); CryptoCompare is available as an alternative. Select one with
``get_price_provider`` (honours the PRICE_PROVIDER setting).
"""

from typing import Any

from config import PRICE_PROVIDER

from .base import Coin, PriceProvider, PriceProviderError
from .coingecko import CoinGeckoClient, CoinGeckoError
from .cryptocompare import (
    APIError,
    CryptoCompareClient,
    CryptoCompareError,
    RateLimitError,
)


def get_price_provider(name: str | None = None, **kwargs: Any) -> PriceProvider:
    """
    Build the configured price provider.

    Args:
        name: "coingecko" (default) or "cryptocompare". Falls back to the
            PRICE_PROVIDER setting when omitted.
        **kwargs: Forwarded to the concrete client constructor.

    Returns:
        A ready-to-use ``PriceProvider``.
    """
    provider = (name or PRICE_PROVIDER or "coingecko").strip().lower()
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

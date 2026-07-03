"""
Provider-agnostic interfaces shared by every price-data backend.

Halvix can source prices from more than one provider (CoinGecko by default,
CryptoCompare as an alternative). Each backend implements the ``PriceProvider``
protocol so the rest of the codebase — fetcher, CLI, processor — never depends
on a concrete provider. Pick one with ``api.get_price_provider``.
"""

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

import polars as pl


class PriceProviderError(Exception):
    """Base exception raised by any price-data provider."""


@dataclass
class Coin:
    """A coin as returned by a provider's market-cap ranking."""

    symbol: str
    name: str
    market_cap: float
    market_cap_rank: int
    current_price: float
    volume_24h: float
    circulating_supply: float
    provider_id: str | None = None  # provider-native id (e.g. a CoinGecko slug)

    def to_dict(self) -> dict:
        """Convert to the dict format consumed by filtering and fetching."""
        return {
            "id": self.symbol.lower(),  # lowercase symbol is the internal cache key
            "symbol": self.symbol,
            "name": self.name,
            "market_cap": self.market_cap,
            "market_cap_rank": self.market_cap_rank,
            "current_price": self.current_price,
            "volume_24h": self.volume_24h,
            "circulating_supply": self.circulating_supply,
            "provider_id": self.provider_id,
        }


@runtime_checkable
class PriceProvider(Protocol):
    """
    Minimal interface the data layer needs from a price-data source.

    The internal cache keys coins by lowercase symbol; ``provider_id`` carries
    the provider-native identifier (e.g. a CoinGecko slug) so a backend that
    addresses coins by id rather than symbol can resolve the right series.
    """

    # Stable backend identifier (e.g. "coingecko", "cryptocompare"). Used as the
    # top-level key of the cross-provider coin-identity registry.
    name: str

    def get_top_coins_by_market_cap(
        self,
        n: int = 300,
        vs_currency: str = "USD",
        track_no_data: bool = False,
    ) -> list[Coin] | tuple[list[Coin], list[dict]]:
        """Return the top ``n`` coins by market capitalization."""
        ...

    def get_full_daily_history(
        self,
        symbol: str,
        vs_currency: str = "BTC",
        start_date: date | None = None,
        end_date: date | None = None,
        show_progress: bool = False,
        provider_id: str | None = None,
    ) -> pl.DataFrame:
        """Return daily OHLCV history with a ``date`` column (may be empty)."""
        ...

    def ping(self) -> bool:
        """Return True if the provider API is reachable."""
        ...

    def check_histoday_availability(
        self,
        symbol: str,
        vs_currency: str = "BTC",
        provider_id: str | None = None,
    ) -> dict[str, str]:
        """Return {'available': truthy-string, 'reason': str} for a trading pair.

        Concrete providers may accept additional optional keyword arguments.
        """
        ...

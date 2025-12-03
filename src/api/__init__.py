"""
API client modules for external data sources.

Data source strategy:
- CoinGecko: Coin list, market cap rankings, metadata
- CryptoCompare: Historical price data (no time limit on free tier)
"""

from .coingecko import (
    APIError as CoinGeckoAPIError,
)
from .coingecko import (
    Coin,
    CoinGeckoClient,
    CoinGeckoError,
)
from .coingecko import (
    RateLimitError as CoinGeckoRateLimitError,
)
from .cryptocompare import (
    APIError as CryptoCompareAPIError,
)
from .cryptocompare import (
    CryptoCompareClient,
    CryptoCompareError,
)
from .cryptocompare import (
    RateLimitError as CryptoCompareRateLimitError,
)

__all__ = [
    # CoinGecko
    "Coin",
    "CoinGeckoClient",
    "CoinGeckoError",
    "CoinGeckoAPIError",
    "CoinGeckoRateLimitError",
    # CryptoCompare
    "CryptoCompareClient",
    "CryptoCompareError",
    "CryptoCompareAPIError",
    "CryptoCompareRateLimitError",
]

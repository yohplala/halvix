"""
API client module for CryptoCompare.

CryptoCompare is the single data source for Halvix:
- Top coins by market cap
- Historical price data (no time limit on free tier)
- Volume data for TOTAL2 calculation
"""

from .cryptocompare import (
    APIError,
    Coin,
    CryptoCompareClient,
    CryptoCompareError,
    RateLimitError,
)

__all__ = [
    "Coin",
    "CryptoCompareClient",
    "CryptoCompareError",
    "APIError",
    "RateLimitError",
]

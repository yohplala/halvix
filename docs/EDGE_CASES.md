# Edge Cases and Solutions

**[← Back to README](../README.md)**

---

This document analyzes edge cases identified in the data fetching pipeline and their implemented solutions.

> **Status**: All edge cases have been implemented ✅

## 1. BTC as Quote Currency

### Status: ✅ Already Implemented

CryptoCompare supports BTC as the quote currency (`tsym=BTC`). Our implementation defaults to this:

```python
# src/api/cryptocompare.py
def get_daily_history(
    self,
    symbol: str,
    vs_currency: str = "BTC",  # ✅ BTC is default
    ...
)
```

**No changes needed.**

---

## 2. Single Data Source (No Symbol Mapping)

### Status: ✅ Implemented

By using CryptoCompare as the single data source for both coin discovery and historical data:

- **No symbol mapping required** - we use CryptoCompare symbols throughout
- **No validation needed** - data comes from same source
- **Simpler architecture** - fewer moving parts

### Implementation

```python
# src/api/cryptocompare.py

def get_top_coins_by_market_cap(self, n: int = 300) -> list[Coin]:
    """
    Get top N coins by market capitalization.
    Returns Coin objects with symbol, name, market_cap, price, volume.
    """
    ...

def get_full_daily_history(self, symbol: str, ...) -> pd.DataFrame:
    """
    Use the same symbol from get_top_coins_by_market_cap directly.
    No mapping needed!
    """
    ...
```

---

## 3. Incremental Data Fetching

### Status: ✅ Implemented

Fetches only new data since last cache, avoiding unnecessary API calls.

```python
# In DataFetcher.fetch_coin_prices()

def fetch_coin_prices(
    self,
    coin_id: str,
    symbol: str,
    vs_currency: str = "BTC",
    use_cache: bool = True,
    incremental: bool = True,  # ✅ Incremental by default
) -> pd.DataFrame:
    """
    Fetch historical price data, with incremental updates.

    If incremental=True and cached data exists:
    - Only fetch from (last_cached_date + 1) to yesterday
    - Merge new data with existing cache
    """
    cached = self.price_cache.get_prices(coin_id)
    yesterday = date.today() - timedelta(days=1)

    if cached is not None and not cached.empty and incremental:
        last_cached = cached.index.max().date()

        if last_cached >= yesterday:
            # Cache is up-to-date
            return cached

        # Fetch only new data
        new_data = self.client.get_full_daily_history(
            symbol=symbol,
            vs_currency=vs_currency,
            start_date=last_cached + timedelta(days=1),
            end_date=yesterday,
        )

        if not new_data.empty:
            # Merge with existing cache
            combined = pd.concat([cached, new_data])
            combined = combined[~combined.index.duplicated(keep='last')]
            combined = combined.sort_index()
            self.price_cache.set_prices(coin_id, combined)
            return combined

        return cached

    # No cache or full refresh requested
    return self._fetch_full_history(coin_id, symbol, vs_currency, yesterday)
```

---

## 4. End Date Should Be Yesterday

### Status: ✅ Implemented

Today's price data is incomplete - CryptoCompare's `close` price for today is the **current price**, not the end-of-day price.

```python
# In multiple places
from datetime import date, timedelta

# Update get_full_daily_history default
def get_full_daily_history(
    self,
    symbol: str,
    vs_currency: str = "BTC",
    start_date: date | None = None,
    end_date: date | None = None,  # Default is yesterday, not today
    ...
):
    if end_date is None:
        end_date = date.today() - timedelta(days=1)  # Yesterday, not today
```

---

## 5. Volume-Weighted TOTAL2

### Status: ✅ Implemented

TOTAL2 uses 24h trading volume for both ranking and weighting:

```python
# src/data/processor.py

def _calculate_daily_total2(self, price_data, target_date):
    """
    Calculate volume-weighted TOTAL2 for a single day.

    Uses 24h trading volume (volumeto) for both:
    - Ranking coins (top N by volume)
    - Weighting the average price
    """
    daily_data = []

    for coin_id, df in price_data.items():
        row = df.loc[target_date]
        price = row["price"]
        volume = row["volume_to"]  # Volume in quote currency (BTC)

        if price > 0 and volume > 0:
            daily_data.append({
                "coin_id": coin_id,
                "price": price,
                "volume": volume,
            })

    # Sort by volume and take top N
    daily_data.sort(key=lambda x: x["volume"], reverse=True)
    top_n = daily_data[:self.top_n]

    # Calculate volume-weighted average
    total_volume = sum(c["volume"] for c in top_n)
    total2_price = sum(c["price"] * c["volume"] for c in top_n) / total_volume

    return total2_price
```

---

## 6. Configuration Settings

From `src/config.py`:

```python
# CryptoCompare API
CRYPTOCOMPARE_BASE_URL = "https://min-api.cryptocompare.com"
CRYPTOCOMPARE_API_CALLS_PER_MINUTE = 30
CRYPTOCOMPARE_MAX_DAYS_PER_REQUEST = 2000

# Data completeness
USE_YESTERDAY_AS_END_DATE = True  # Don't fetch incomplete today's data
```

---

## 7. Updated Data Flow

```
┌────────────────────────────────────────────────────────────────────┐
│                     Simplified Fetch Pipeline                       │
├────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  1. Fetch Top Coins (CryptoCompare)                                │
│     └── /data/top/mktcapfull                                       │
│         ├── Returns: symbol, name, market_cap, volume              │
│         └── Pagination: 100 coins per page                         │
│                                                                     │
│  2. Filter Coins Locally                                           │
│     ├── Remove wrapped/staked/bridged/stablecoins                  │
│     └── Export rejected to CSV                                     │
│                                                                     │
│  3. For each filtered coin:                                        │
│     ├── Has cached prices?                                         │
│     │   ├── Up to yesterday? → Skip (cache is current)            │
│     │   └── Older? → Fetch incrementally from last_date           │
│     └── No cache? → Fetch full history                            │
│                                                                     │
│  4. All fetches end at YESTERDAY (not today)                       │
│     └── Today's data is incomplete                                 │
│                                                                     │
│  5. Calculate Volume-Weighted TOTAL2                               │
│     ├── For each day: rank by volume, take top 50                  │
│     └── Weighted average: Σ(price × volume) / Σ(volume)           │
│                                                                     │
└────────────────────────────────────────────────────────────────────┘
```

---

## 8. Test Cases

### Token Filtering Tests

```python
class TestTokenFiltering:
    def test_filters_wrapped_tokens(self):
        """wBTC, wETH, etc. should be excluded."""

    def test_filters_staked_tokens(self):
        """stETH, JitoSOL, etc. should be excluded."""

    def test_allows_legitimate_tokens(self):
        """SUI, SEI, STX, etc. should pass filtering."""
```

### Incremental Fetching Tests

```python
class TestIncrementalFetching:
    def test_incremental_fetch_merges_correctly(self):
        """New data should merge with existing cache."""

    def test_no_fetch_when_cache_current(self):
        """Should skip fetch if cache is up to yesterday."""

    def test_full_fetch_when_no_cache(self):
        """Should fetch full history when no cache exists."""
```

### Volume-Weighted TOTAL2 Tests

```python
class TestVolumeWeightedTotal2:
    def test_ranks_by_volume(self):
        """Coins should be ranked by 24h volume, not market cap."""

    def test_weighted_average_calculation(self):
        """TOTAL2 should be volume-weighted average of prices."""

    def test_excludes_stablecoins(self):
        """Stablecoins should not be included in TOTAL2."""
```

---

*Document created: 2025-12-03*
*Updated: 2025-12-03 (simplified for single data source)*

---

**[← Back to README](../README.md)**

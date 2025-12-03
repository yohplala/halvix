# Data Sources and API Strategy

This document details the dual-API strategy used by Halvix for cryptocurrency data retrieval, including rate limits, data types, and implementation details.

## Overview

Halvix uses a **dual-API strategy** to work within free tier limitations while obtaining comprehensive historical data:

| API | Purpose | Key Advantage |
|-----|---------|---------------|
| **CoinGecko** | Coin lists, metadata, current market data | Comprehensive coin coverage, reliable market cap rankings |
| **CryptoCompare** | Historical price data (OHLCV) | **Unlimited historical depth** (5000+ days) |

This strategy was chosen because:
- CoinGecko's free tier limits historical data to **365 days**
- Halving cycle analysis requires **4000+ days** of data (covering multiple cycles)
- CryptoCompare's free tier provides **full historical access** with no time restrictions

---

## CoinGecko API

### Usage in Halvix

| Data Type | Endpoint | CLI Command |
|-----------|----------|-------------|
| Top N coins by market cap | `/coins/markets` | `python -m main list-coins` |
| Coin metadata (name, symbol, rank) | `/coins/markets` | `python -m main list-coins` |
| API connectivity check | `/ping` | `python -m main status` |

### Rate Limits

| Tier | Rate Limit | Notes |
|------|------------|-------|
| **Public (Free)** | 5-15 calls/minute | Variable based on server load |
| Demo (Free with key) | 30 calls/minute | Requires registration |
| Paid plans | 500-1000 calls/minute | Subscription required |

### Halvix Configuration

```python
# src/config.py
API_CALLS_PER_MINUTE = 10  # Conservative setting for free tier
API_MIN_INTERVAL = 6.0     # 6 seconds between calls
```

### Implementation Details

The `CoinGeckoClient` (`src/api/coingecko.py`) implements:

1. **Proactive Rate Limiting**: Waits between requests to stay under limits
   ```python
   def _wait_for_rate_limit(self) -> None:
       if self._last_request_time is not None:
           elapsed = time.time() - self._last_request_time
           if elapsed < self.min_interval:
               time.sleep(self.min_interval - elapsed)
   ```

2. **Automatic Retry with Exponential Backoff**: Uses `tenacity` library
   ```python
   @retry(
       retry=retry_if_exception_type(RateLimitError),
       stop=stop_after_attempt(5),
       wait=wait_exponential(multiplier=1, min=1, max=60),
   )
   ```

3. **Error Detection**: Catches HTTP 429 and raises `RateLimitError`

### Free Tier Limitations

| Limitation | Impact on Halvix |
|------------|------------------|
| 365-day historical data limit | ❌ Cannot use for halving analysis |
| Variable rate limits | ✅ Handled with conservative settings |
| No API key required | ✅ Simple setup |

---

## CryptoCompare API

### Usage in Halvix

| Data Type | Endpoint | CLI Command |
|-----------|----------|-------------|
| Daily OHLCV prices | `/data/v2/histoday` | `python -m main fetch-prices` |
| Full historical prices (with pagination) | `/data/v2/histoday` | `python -m main fetch-prices` |
| Coin symbol list | `/data/all/coinlist` | Internal mapping |
| API connectivity check | `/data/v2/histoday` | `python -m main status` |

### Rate Limits

| Tier | Rate Limit | Monthly Limit | Notes |
|------|------------|---------------|-------|
| **Free** | 10 calls/second | Unlimited | No API key required |
| Professional | 50 calls/second | 1,000,000 calls | Paid |
| Enterprise | Custom | Custom | Contact sales |

### Halvix Configuration

```python
# src/config.py
CRYPTOCOMPARE_API_CALLS_PER_MINUTE = 30  # Conservative (could go to 600)
CRYPTOCOMPARE_MAX_LIMIT = 2000           # Max days per request
```

### Implementation Details

The `CryptoCompareClient` (`src/api/cryptocompare.py`) implements:

1. **Proactive Rate Limiting**: Same pattern as CoinGecko
   ```python
   self.min_interval = 60.0 / calls_per_minute  # 2 seconds at 30 calls/min
   ```

2. **Automatic Pagination**: For requests exceeding 2000 days
   ```python
   def get_full_daily_history(self, symbol, vs_currency, start_date, end_date):
       # Automatically fetches in 2000-day chunks
       # Handles deduplication and chronological sorting
   ```

3. **Symbol Mapping**: Converts CoinGecko IDs to CryptoCompare symbols
   ```python
   def get_symbol_for_coingecko_id(self, coingecko_id, coingecko_symbol):
       return coingecko_symbol.upper()  # Most coins: ETH -> ETH
   ```

### Free Tier Advantages

| Feature | Benefit for Halvix |
|---------|-------------------|
| **No time limit on historical data** | ✅ Can fetch 5000+ days for halving analysis |
| High rate limit (10/second) | ✅ Fast data retrieval |
| No API key required | ✅ Simple setup |
| 2000 days per request | ✅ Efficient pagination |

---

## Data Flow Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Halvix Pipeline                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Step 1: Discover Coins (CoinGecko)                            │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ GET /coins/markets?per_page=300                          │  │
│  │ Returns: id, symbol, name, market_cap, market_cap_rank   │  │
│  │ Output: data/processed/accepted_coins.json               │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              │                                  │
│                              ▼                                  │
│  Step 2: Filter Tokens (Local)                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Remove: wrapped, staked, bridged, stablecoins, BTC       │  │
│  │ Output: data/processed/rejected_coins.csv                │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              │                                  │
│                              ▼                                  │
│  Step 3: Fetch Historical Prices (CryptoCompare)               │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ GET /data/v2/histoday?fsym=ETH&tsym=BTC&limit=2000      │  │
│  │ Pagination: Multiple requests for 4000+ days            │  │
│  │ Returns: date, open, high, low, close, volume           │  │
│  │ Output: data/prices/{coin_id}.parquet                   │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              │                                  │
│                              ▼                                  │
│  Step 4: Calculate TOTAL2 Index (Local)                        │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Daily selection of top 50 altcoins by market cap         │  │
│  │ Market-cap weighted average price                        │  │
│  │ Output: data/processed/total2_index.parquet             │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Retrieved

### From CoinGecko

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique coin identifier (e.g., "ethereum") |
| `symbol` | string | Trading symbol (e.g., "eth") |
| `name` | string | Full name (e.g., "Ethereum") |
| `market_cap` | float | Current market capitalization in USD |
| `market_cap_rank` | int | Rank by market cap (1 = largest) |
| `current_price` | float | Current price in requested currency |

### From CryptoCompare

| Field | Type | Description |
|-------|------|-------------|
| `time` | int | Unix timestamp (start of day, UTC) |
| `open` | float | Opening price for the day |
| `high` | float | Highest price during the day |
| `low` | float | Lowest price during the day |
| `close` | float | Closing price (stored as `price`) |
| `volumefrom` | float | Volume in base currency |
| `volumeto` | float | Volume in quote currency |

---

## Symbol Mapping

CoinGecko uses human-readable IDs (`bitcoin`, `ethereum`) while CryptoCompare uses uppercase trading symbols (`BTC`, `ETH`).

### Mapping Strategy

```python
# Most coins: uppercase the CoinGecko symbol
coingecko_symbol = "eth"  → cryptocompare_symbol = "ETH"

# Special cases handled via overrides
coingecko_id = "miota"    → cryptocompare_symbol = "IOTA"
```

### Potential Issues

Some coins may have:
- Different symbols on different exchanges
- No CryptoCompare listing (newer coins)
- Name/symbol conflicts

These are handled by the filtering process—coins without valid CryptoCompare data are excluded from analysis.

---

## Rate Limit Comparison

| Feature | CoinGecko (Free) | CryptoCompare (Free) |
|---------|-----------------|---------------------|
| **Calls/minute** | 5-15 (variable) | 600 (10/second) |
| **Historical limit** | 365 days | **Unlimited** |
| **API key required** | No | No |
| **Data freshness** | Real-time | ~10 min delay |
| **Retry on 429** | Yes (with backoff) | Yes (with backoff) |

### Why This Matters

For halving cycle analysis spanning 4 cycles (2012-2024):
- Required: ~4,500 days of data
- CoinGecko: ❌ Max 365 days
- CryptoCompare: ✅ Full history available

---

## Error Handling

Both clients implement:

1. **HTTP 429 Detection**: Catches rate limit responses
2. **Automatic Retry**: Up to 5 attempts with exponential backoff
3. **Graceful Degradation**: Returns empty data rather than crashing
4. **Logging**: Errors are logged for debugging

```python
# Retry configuration (both clients)
@retry(
    retry=retry_if_exception_type(RateLimitError),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=60),
)
```

---

## Testing

### Unit Tests (Mocked)

```bash
# Run all unit tests (no API calls)
poetry run pytest tests/test_coingecko.py tests/test_cryptocompare.py -v
```

### Integration Tests (Real API)

```bash
# Run integration tests (makes real API calls)
poetry run pytest tests/test_coingecko_integration.py --run-integration -v
poetry run pytest tests/test_cryptocompare_integration.py --run-integration -v
```

⚠️ **Note**: Integration tests use conservative rate limits (5 calls/minute) to avoid triggering rate limits during testing.

---

## Configuration Reference

All API settings are in `src/config.py`:

```python
# CoinGecko
COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
API_CALLS_PER_MINUTE = 10      # Conservative for free tier
API_MAX_RETRIES = 5
API_RETRY_MIN_WAIT = 1         # seconds
API_RETRY_MAX_WAIT = 60        # seconds

# CryptoCompare
CRYPTOCOMPARE_BASE_URL = "https://min-api.cryptocompare.com"
CRYPTOCOMPARE_API_CALLS_PER_MINUTE = 30  # Very conservative
CRYPTOCOMPARE_MAX_LIMIT = 2000           # Days per request
```

---

## Why Not Asyncio?

The current implementation uses **synchronous requests** with rate limiting. Here's why asyncio wasn't implemented:

### Rate Limiting Constraints

| API | Free Tier Limit | Effective Throughput |
|-----|-----------------|---------------------|
| CoinGecko | 5-15 calls/min | ~1 call every 6-12 seconds |
| CryptoCompare | 600 calls/min | ~1 call every 0.1 seconds |

**Analysis:**

1. **CoinGecko is the bottleneck**: With only 5-15 calls/minute, we can't benefit from parallel requests - we'd hit rate limits immediately.

2. **Sequential is actually faster for CoinGecko**: The rate limiter forces us to wait between calls anyway. Asyncio would add complexity without speed benefits.

3. **CryptoCompare could benefit from asyncio**, but:
   - Full history for one coin requires ~3 requests (6000 days / 2000 per request)
   - We typically fetch ~200 coins → ~600 requests
   - At 10 req/sec, this takes ~60 seconds
   - With incremental fetching, we often fetch 0 requests (cache is current)

4. **Complexity vs. Benefit**: Asyncio would require:
   - Converting all API clients to async
   - Managing concurrent rate limiters
   - Error handling across parallel tasks
   - Session management with aiohttp

**Conclusion:** The rate limiting overhead dominates execution time. Asyncio would add significant complexity for marginal improvement. The current synchronous implementation with incremental caching is the pragmatic choice.

### When to Reconsider

Consider asyncio if:
- Moving to paid API tiers with higher rate limits
- Adding more data sources that can be queried in parallel
- Building a real-time dashboard that needs concurrent updates

---

## Troubleshooting

### "Rate limit exceeded" errors

1. Increase interval between calls in `config.py`
2. Wait a few minutes before retrying
3. Check if another process is using the same API

### "Market does not exist" errors (CryptoCompare)

- The coin symbol may not be listed on CryptoCompare
- Check symbol mapping in `get_symbol_for_coingecko_id()`
- Coin will be excluded from analysis automatically

### Empty historical data

- Coin may be too new (created after requested start date)
- Symbol mapping may be incorrect
- Check CryptoCompare directly: `https://min-api.cryptocompare.com/data/v2/histoday?fsym=ETH&tsym=BTC&limit=10`

---

*Last updated: 2025-12-03*

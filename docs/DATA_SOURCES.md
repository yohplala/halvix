# Data Sources and API Strategy

This document details the API strategy used by Halvix for cryptocurrency data retrieval, including rate limits, data types, and implementation details.

## Overview

Halvix uses **CryptoCompare** as its single data source:

| Feature | Details |
|---------|---------|
| **Top coins by market cap** | `/data/top/mktcapfull` endpoint |
| **Historical prices** | `/data/v2/histoday` endpoint |
| **Volume data** | Included in historical data for TOTAL2 weighting |
| **Rate limit** | 10 calls/second (free tier) |
| **Historical depth** | **Unlimited** - full history available |

This single-source approach provides:
- No symbol mapping issues between different APIs
- Consistent data quality
- Simpler architecture
- Full historical data needed for halving cycle analysis (5000+ days)

---

## CryptoCompare API

### Endpoints Used

| Data Type | Endpoint | CLI Command |
|-----------|----------|-------------|
| Top N coins by market cap | `/data/top/mktcapfull` | `python -m main list-coins` |
| Daily OHLCV prices | `/data/v2/histoday` | `python -m main fetch-prices` |
| Full historical prices (with pagination) | `/data/v2/histoday` | `python -m main fetch-prices` |
| API connectivity check | `/data/v2/histoday` | `python -m main status` |

### Rate Limits

| Tier | Rate Limit | Notes |
|------|------------|-------|
| **Free** | 10 calls/second | No API key required |
| Professional | 50 calls/second | Paid |
| Enterprise | Custom | Contact sales |

### Halvix Configuration

```python
# src/config.py
CRYPTOCOMPARE_API_CALLS_PER_MINUTE = 30  # Conservative (could go to 600)
CRYPTOCOMPARE_MAX_DAYS_PER_REQUEST = 2000  # Max days per request
```

### Implementation Details

The `CryptoCompareClient` (`src/api/cryptocompare.py`) implements:

1. **Proactive Rate Limiting**: Waits between requests to stay under limits
   ```python
   self.min_interval = 60.0 / calls_per_minute  # 2 seconds at 30 calls/min
   ```

2. **Automatic Retry with Exponential Backoff**: Uses `tenacity` library
   ```python
   @retry(
       retry=retry_if_exception_type(RateLimitError),
       stop=stop_after_attempt(5),
       wait=wait_exponential(multiplier=1, min=1, max=60),
   )
   ```

3. **Automatic Pagination**: For requests exceeding 2000 days
   ```python
   def get_full_daily_history(self, symbol, vs_currency, start_date, end_date):
       # Automatically fetches in 2000-day chunks
       # Handles deduplication and chronological sorting
   ```

4. **Top Coins by Market Cap**: Fetches current rankings with pagination
   ```python
   def get_top_coins_by_market_cap(self, n: int = 300):
       # Fetches coins in pages of 100
       # Returns Coin objects with market cap, price, volume
   ```

### Free Tier Advantages

| Feature | Benefit for Halvix |
|---------|-------------------|
| **No time limit on historical data** | ✅ Can fetch 5000+ days for halving analysis |
| High rate limit (10/second) | ✅ Fast data retrieval |
| No API key required | ✅ Simple setup |
| 2000 days per request | ✅ Efficient pagination |
| Market cap rankings | ✅ Top coins discovery |
| Volume data | ✅ Volume-weighted TOTAL2 calculation |

---

## Data Flow Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Halvix Pipeline                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Step 1: Discover Coins (CryptoCompare)                        │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ GET /data/top/mktcapfull?limit=100&page=0..2             │  │
│  │ Returns: symbol, name, market_cap, price, volume         │  │
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
│  │ Output: data/raw/prices/{symbol}.parquet                │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              │                                  │
│                              ▼                                  │
│  Step 4: Calculate Volume-Weighted TOTAL2 (Local)              │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Daily selection of top 50 altcoins by volume             │  │
│  │ Volume-weighted average price                            │  │
│  │ Output: data/processed/total2_index.parquet             │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Caching and Incremental Updates

### Price Data Caching

Price data is stored in parquet format, one file per coin-pair:

```
data/raw/prices/
├── eth-btc.parquet    # ETH priced in BTC
├── eth-usd.parquet    # ETH priced in USD
├── xrp-btc.parquet
├── xrp-usd.parquet
├── bnb-btc.parquet
├── bnb-usd.parquet
└── ... (one file per coin per quote currency)
```

Files are named as `{coin_id}-{quote_currency}.parquet` for clarity.

### Incremental Update Behavior

When running `fetch-prices` in incremental mode (default):

1. **Load existing cache**: Read the existing parquet file for the coin
2. **Determine new data range**: Find the last cached date, fetch from `last_date + 1` to yesterday
3. **Merge with pandas**: `pd.concat([cached_data, new_data])`
4. **Deduplicate**: Remove any duplicate dates, keeping the newest values
5. **Overwrite file**: Write the combined DataFrame back to the same parquet file

```python
# Simplified logic from src/data/fetcher.py
if not new_data.empty:
    combined = pd.concat([cached, new_data])
    combined = combined[~combined.index.duplicated(keep="last")]
    combined = combined.sort_index()
    price_cache.set_prices(coin_id, combined)  # Overwrites the file
```

**Why overwrite instead of append?**
- Dataset is small (~5000 rows per coin, a few tens of KB)
- Daily updates add only a few rows
- Simpler than managing append-only storage
- Parquet compression is efficient on full rewrite
- Ensures data consistency (no orphaned append files)

### Cache Expiry

| Cache Type | Expiry | Purpose |
|------------|--------|---------|
| **API response cache** | 24 hours | Coin list from `/data/top/mktcapfull` |
| **Price data cache** | Never expires | Parquet files in `data/raw/prices/` |

Price data never expires because historical data doesn't change. Incremental mode only fetches new data since the last cached date.

---

## Data Retrieved

### From /data/top/mktcapfull

| Field | Type | Description |
|-------|------|-------------|
| `CoinInfo.Name` | string | Trading symbol (e.g., "ETH") |
| `CoinInfo.FullName` | string | Full name (e.g., "Ethereum") |
| `RAW.MKTCAP` | float | Current market capitalization |
| `RAW.PRICE` | float | Current price in quote currency |
| `RAW.VOLUME24HOUR` | float | 24h trading volume |
| `RAW.CIRCULATINGSUPPLY` | float | Circulating supply |

### From /data/v2/histoday

| Field | Type | Description |
|-------|------|-------------|
| `time` | int | Unix timestamp (start of day, UTC) |
| `open` | float | Opening price for the day |
| `high` | float | Highest price during the day |
| `low` | float | Lowest price during the day |
| `close` | float | Closing price |
| `volumefrom` | float | Volume in base currency |
| `volumeto` | float | Volume in quote currency |

---

## Error Handling

The client implements:

1. **HTTP 429 Detection**: Catches rate limit responses
2. **Automatic Retry**: Up to 5 attempts with exponential backoff
3. **Graceful Degradation**: Returns empty data rather than crashing
4. **Logging**: Errors are logged for debugging

```python
# Retry configuration
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
poetry run pytest tests/test_cryptocompare.py -v
```

### Integration Tests (Real API)

```bash
# Run integration tests (makes real API calls)
poetry run pytest tests/test_cryptocompare_integration.py --run-integration -v
```

⚠️ **Note**: Integration tests use conservative rate limits to avoid triggering rate limits during testing.

---

## Configuration Reference

All API settings are in `src/config.py`:

```python
# CryptoCompare
CRYPTOCOMPARE_BASE_URL = "https://min-api.cryptocompare.com"
CRYPTOCOMPARE_COIN_URL = "https://www.cryptocompare.com/coins"
CRYPTOCOMPARE_API_CALLS_PER_MINUTE = 30  # Very conservative
CRYPTOCOMPARE_MAX_DAYS_PER_REQUEST = 2000  # Days per request

# Retry configuration
API_MAX_RETRIES = 5
API_RETRY_MIN_WAIT = 1         # seconds
API_RETRY_MAX_WAIT = 60        # seconds
```

---

## Troubleshooting

### "Rate limit exceeded" errors

1. Increase interval between calls in `config.py`
2. Wait a few minutes before retrying
3. Check if another process is using the same API

### "Market does not exist" errors

- The coin symbol may not be listed on CryptoCompare
- Coin will be excluded from analysis automatically

### Empty historical data

- Coin may be too new (created after requested start date)
- Check CryptoCompare directly: `https://min-api.cryptocompare.com/data/v2/histoday?fsym=ETH&tsym=BTC&limit=10`

---

*Last updated: 2025-12-03*

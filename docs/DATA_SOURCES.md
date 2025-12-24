# Data Sources and API Strategy

**[← Back to README](../README.md)**

---

This document details the API strategy used by Halvix for cryptocurrency data retrieval, including rate limits, data types, and implementation details.

## Overview

Halvix uses **CryptoCompare** (now part of CoinDesk) as its single data source:

| Feature | Details |
|---------|---------|
| **Top coins by market cap** | `/data/top/mktcapfull` endpoint |
| **Historical prices** | `/data/v2/histoday` endpoint |
| **Volume data** | Included in historical data for TOTAL2 weighting |
| **Rate limit** | Dynamic - adapts to API quota status |
| **Historical depth** | **Unlimited** - full history available |

This single-source approach provides:
- No symbol mapping issues between different APIs
- Consistent data quality
- Simpler architecture
- Full historical data needed for halving cycle analysis (5000+ days)

---

## CryptoCompare API

**API Documentation**: https://developers.coindesk.com/documentation/

### Endpoints Used

| Data Type | Endpoint | Documentation | CLI Command |
|-----------|----------|---------------|-------------|
| Top N coins by market cap | `/data/top/mktcapfull` | [asset_v1_top_list](https://developers.coindesk.com/documentation/data-api/asset_v1_top_list) | `python -m main list-coins` |
| Daily OHLCV prices | `/data/v2/histoday` | [spot_v1_historical_days](https://developers.coindesk.com/documentation/data-api/spot_v1_historical_days) | `python -m main fetch-prices` |
| Rate limit status | `/stats/rate/limit` | [admin_v2_rate_limit](https://developers.coindesk.com/documentation/data-api/admin_v2_rate_limit) | (internal) |

**Note**: We use `/data/v2/histoday` for individual coin historical prices, not the index endpoint (`index_cc_v1_historical_days`) which is for market cap indices.

### Rate Limiting - Dynamic Adaptive

Halvix uses **dynamic rate limiting** that adapts to actual API quota usage:

| Feature | Description |
|---------|-------------|
| **Status Checking** | Calls `/stats/rate/limit` to check remaining quota |
| **Adaptive Waiting** | Automatically waits when approaching limits |
| **Multi-tier Awareness** | Monitors second, minute, hour, and month quotas |
| **Fallback Throttling** | Minimum interval between requests as safety net |

```python
# The client periodically checks quota and adapts:
status = client.get_rate_limit_status()
# status.calls_left_second, calls_left_minute, calls_left_hour, calls_left_month
```

### Halvix Configuration

```python
# src/config.py

# Fallback minimum interval between requests (used if rate limit status unavailable)
# The client dynamically checks /stats/rate/limit and adapts accordingly
CRYPTOCOMPARE_API_CALLS_PER_MINUTE = 12  # Fallback: 5 seconds between requests

CRYPTOCOMPARE_MAX_DAYS_PER_REQUEST = 2000  # Max days per API request
```

The `CRYPTOCOMPARE_API_CALLS_PER_MINUTE` constant serves as a **conservative fallback**. The client's primary rate limiting is dynamic, checking the actual API quota status. When quota is available, it can make requests faster; when the status endpoint is unavailable, it falls back to the safe 5-second interval.

### Implementation Details

The `CryptoCompareClient` (`src/api/cryptocompare.py`) implements:

1. **Dynamic Rate Limit Status Checking**: Polls `/stats/rate/limit` endpoint
   ```python
   status = self.get_rate_limit_status()
   if status.is_near_limit:
       time.sleep(status.recommended_wait_seconds)
   ```

2. **Fallback Time-Based Throttling**: Conservative minimum interval between requests
   ```python
   self.min_interval = 60.0 / calls_per_minute  # 5 seconds at 12 calls/min (fallback)
   ```

3. **Automatic Retry with Exponential Backoff**: Uses `tenacity` library
   ```python
   @retry(
       retry=retry_if_exception_type(RateLimitError),
       stop=stop_after_attempt(10),  # 10 attempts
       wait=wait_exponential(multiplier=2, min=2, max=120),  # Up to 2 min wait
   )
   ```

4. **Wait for Rate Limit Reset**: Polls status until quota available
   ```python
   def wait_for_rate_limit_reset(self, max_wait_seconds=120):
       # Waits and polls until rate limit is no longer critical
   ```

5. **Automatic Pagination**: For requests exceeding 2000 days
   ```python
   def get_full_daily_history(self, symbol, vs_currency, start_date, end_date):
       # Automatically fetches in 2000-day chunks
       # Handles deduplication and chronological sorting
   ```

6. **Top Coins by Market Cap**: Fetches current rankings with pagination
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
| Rate limit status endpoint | ✅ Dynamic adaptive throttling |

---

## Data Flow Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                         Halvix Pipeline                        │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  Step 1: Discover Coins (CryptoCompare)                        │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ GET /data/top/mktcapfull?limit=100&page=0..11            │  │
│  │ Requests: TOP_N_BY_MARKETCAP_TO_FETCH = 1200 coins       │  │
│  │                                                          │  │
│  │ Two sources of coins:                                    │  │
│  │ • WITH USD data: ~886 coins (have market cap, price)     │  │
│  │ • WITHOUT USD data: ~490 coins (no market cap from API)  │  │
│  │                                                          │  │
│  │ Output: data/processed/coins_to_download.json            │  │
│  │         data/processed/no_usd_data.csv                   │  │
│  │         data/processed/fetch_metadata.json               │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              │                                 │
│                              ▼                                 │
│  Step 2: Filter Coins (Local) - applies to BOTH sources        │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Remove: wrapped, staked, bridged, stablecoins            │  │
│  │ Keep: BTC (for BTC/USD chart)                            │  │
│  │ Mark coins: has_usd_data=true/false                      │  │
│  │ Output: data/processed/download_skipped.csv              │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              │                                 │
│                              ▼                                 │
│  Step 3: Fetch Historical Prices (CryptoCompare)               │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Altcoins (USD coins): GET ...?fsym=ETH&tsym=BTC          │  │
│  │ Altcoins (no-USD):    GET ...?fsym=RYO&tsym=BTC          │  │
│  │ BTC:                  GET ...?fsym=BTC&tsym=USD          │  │
│  │ Pagination: Multiple requests for 4000+ days             │  │
│  │ Output: data/raw/prices/{coin}-{quote}.parquet           │  │
│  │         data/processed/download_failed.csv (if any)      │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              │                                 │
│                              ▼                                 │
│  Step 4: Calculate Volume-Weighted TOTAL2 (Local)              │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Daily selection of top 30 altcoins by volume             │  │
│  │ Volume-weighted average price                            │  │
│  │ Output: data/processed/total2_index.parquet              │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

---

## Data Status Page

The pipeline generates a `data_status.html` page (at `site/data_status.html`) that provides a comprehensive view of the data:

### Statistics Cards

| Card | Description |
|------|-------------|
| **Coins Requested** | Number requested from API (1200), also the cap on total accepted coins |
| **Coins Accepted** | Total coins accepted for download (≤ requested), with sublabel showing how many from no-USD source |
| **Coins Downloaded** | Coins that have downloaded price data in cache |
| **Skipped / Failed** | Filtered coins (stablecoins, wrapped) + failed downloads (no BTC pair) + capped no-USD coins |
| **Total Pairs** | Sum of all quote pairs (BTC + USD) across all coins |

### Downloaded Coins Table

Lists all coins with price data, including:
- Symbol and name (linked to CryptoCompare)
- **Source** column: "USD" for coins with market cap data, "BTC-only" for coins discovered without USD data
- **Quote(s)** column: Shows available pairs (BTC, USD, or both)
- Market cap (shows "N/A" for BTC-only coins)
- Date range and days of data

Coins are sorted: USD coins first (by market cap descending), then BTC-only coins.

### Skipped / Failed Table

Lists all excluded coins with reasons:
- **Stablecoin**: USDT, USDC, DAI, etc.
- **Wrapped/Staked/Bridged token**: WBTC, stETH, etc.
- **No BTC pair**: Coins like KET that have no direct BTC trading pair on CryptoCompare

---

## Output Files

### Processed Data Files

| File | Description |
|------|-------------|
| `coins_to_download.json` | Coins accepted for price fetching. Each coin has `has_usd_data` field (true/false) |
| `download_skipped.csv` | Coins filtered out (stablecoins, wrapped, etc.) from USD coins |
| `download_failed.csv` | Coins that failed to download (no BTC pair on CryptoCompare) |
| `no_usd_data.csv` | Coins returned by API without USD price data (before filtering) |
| `fetch_metadata.json` | Metadata: coins_requested, coins_fetched, coins_no_usd_data, coins_accepted, etc. |
| `total2_index.parquet` | Calculated TOTAL2 index |

### Price Data Files

```
data/raw/prices/
├── eth-btc.parquet    # ETH priced in BTC
├── btc-usd.parquet    # BTC priced in USD (special case)
├── xrp-btc.parquet
└── ... (one file per coin-pair)
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

The client implements robust rate limit handling using the CryptoCompare rate limit status endpoint:
https://developers.coindesk.com/documentation/data-api/admin_v2_rate_limit

### Rate Limit Detection

1. **HTTP 429 Detection**: Catches rate limit responses from HTTP status
2. **JSON Body Rate Limit Detection**: Detects rate limit errors returned as HTTP 200 with error message in JSON body (e.g., "You are over your rate limit please upgrade your account!")
3. **Dynamic Rate Limit Status**: Periodically checks `/stats/rate/limit` endpoint to monitor quota usage

### Rate Limit Response

```python
# Rate limit status checking
status = client.get_rate_limit_status()
print(f"Calls left this second: {status.calls_left_second}")
print(f"Calls left this minute: {status.calls_left_minute}")
print(f"Calls left this hour: {status.calls_left_hour}")
print(f"Calls left this month: {status.calls_left_month}")
```

### Automatic Retry and Wait

- **Retry Configuration**: Up to 10 attempts with aggressive exponential backoff (2s, 4s, 8s, 16s... up to 120s)
- **Smart Waiting**: When approaching rate limits, automatically waits before making more requests
- **Wait for Reset**: `wait_for_rate_limit_reset()` method polls status and waits until quota is available

```python
# Retry configuration
@retry(
    retry=retry_if_exception_type(RateLimitError),
    stop=stop_after_attempt(10),  # 10 attempts
    wait=wait_exponential(multiplier=2, min=2, max=120),  # Aggressive backoff
)
```

### Graceful Degradation

- Returns empty data rather than crashing
- Logs all errors for debugging
- Skips remaining failed coin checks once rate limit is hit

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
# Free tier allows 10/sec (600/min), we use conservative 2/sec (120/min)
CRYPTOCOMPARE_API_CALLS_PER_MINUTE = 120
CRYPTOCOMPARE_MAX_DAYS_PER_REQUEST = 2000  # Days per request

# Coin fetching
TOP_N_BY_MARKETCAP_TO_FETCH = 1200  # Request top 1200 coins by market cap

# Retry configuration
API_MAX_RETRIES = 5
API_RETRY_MIN_WAIT = 1         # seconds
API_RETRY_MAX_WAIT = 60        # seconds

# Data completeness - this is a fixed constant ensuring only complete daily data is fetched
USE_YESTERDAY_AS_END_DATE = True

# Output files
COINS_TO_DOWNLOAD_JSON = PROCESSED_DIR / "coins_to_download.json"
DOWNLOAD_SKIPPED_CSV = PROCESSED_DIR / "download_skipped.csv"
DOWNLOAD_FAILED_CSV = PROCESSED_DIR / "download_failed.csv"
NO_USD_DATA_CSV = PROCESSED_DIR / "no_usd_data.csv"
FETCH_METADATA_JSON = PROCESSED_DIR / "fetch_metadata.json"
```

---

## Troubleshooting

### "Rate limit exceeded" errors

CryptoCompare has multiple types of rate limits:
- **Per-second limit**: Free tier allows ~10 requests/second (we use conservative 2/sec)
- **Monthly/hourly quota**: Free tier has account-level quotas that may be exceeded

If you see rate limit errors:

1. **Wait before retrying**: The free tier may have hourly/daily quotas
2. **Reduce fetch frequency**: Increase `CRYPTOCOMPARE_API_CALLS_PER_MINUTE` in `config.py`
3. **Use incremental fetching**: The `--incremental` flag only fetches new data since last cache
4. **Consider API key**: Register for a free API key for higher limits
5. **Check monthly usage**: CryptoCompare free tier has a monthly call limit (~100k calls)

The client automatically:
- Detects rate limits in both HTTP 429 responses AND JSON error messages
- Retries up to 5 times with exponential backoff (1s, 2s, 4s, 8s, 16s)
- Skips remaining failed coin checks once rate limit is hit

### "CCCAGG market does not exist for this coin pair" errors

This error occurs when a coin doesn't have a direct trading pair on CryptoCompare's aggregated exchanges. For example, KET has no direct KET/BTC pair.

- These coins are logged during `fetch-prices` and saved to `download_failed.csv`
- They appear in the "Skipped / Failed" section of `data_status.html`
- The coin may still have a USD pair (current price), but no historical BTC data

### Empty historical data

- Coin may be too new (created after requested start date)
- Check CryptoCompare directly: `https://min-api.cryptocompare.com/data/v2/histoday?fsym=ETH&tsym=BTC&limit=10`

### Requested vs. returned coins

The CryptoCompare market cap API returns coins in two categories:

1. **Coins WITH USD data** (~886 of 1200): Have market cap, price, and volume data
2. **Coins WITHOUT USD data** (~490): Returned by API but missing USD price data

Lower-ranked coins (smaller market cap) are more likely to lack USD data on CryptoCompare. These coins often still have BTC trading pairs available via the `histoday` endpoint.

**Halvix processes both categories with a total cap:**
- USD coins have priority (they have actual market cap data for ranking)
- No-USD coins fill remaining slots up to the requested limit
- Total accepted coins will never exceed `coins_requested`
- Both are filtered (removes stablecoins, wrapped, etc.)
- Marks each coin with `has_usd_data: true/false` in `coins_to_download.json`
- No-USD coins are sorted by their API position (CryptoCompare's internal ranking)

The `fetch_metadata.json` file records the full breakdown:
```json
{
  "coins_requested": 1200,
  "coins_fetched": 886,
  "coins_no_usd_data": 490,
  "coins_no_usd_filtered": 7,
  "coins_no_usd_accepted": 283,
  "coins_no_usd_capped": 200,
  "coins_filtered": 31,
  "coins_accepted": 1138
}
```

In this example:
- 886 coins had USD data, 31 were filtered → 855 USD coins accepted
- 490 coins had no USD data, 7 were filtered → 483 passed filtering
- But only 283 no-USD coins were included (200 capped to meet the 1200 limit)
- Total: 855 + 283 = 1138 ≤ 1200

---

*Last updated: 2025-12-24*

---

**[← Back to README](../README.md)**

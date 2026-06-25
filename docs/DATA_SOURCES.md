# Data Sources and Provider Strategy

**[← Back to README](../README.md)**

---

This document describes how Halvix retrieves cryptocurrency data: the provider
abstraction, the default CoinGecko backend, the CryptoCompare alternative, and
the caching/incremental-update strategy.

## Overview

Halvix sources price data through a small **provider abstraction**
(`api.base.PriceProvider`) so the rest of the codebase never depends on a single
vendor. Two backends ship today:

| Provider | Default | Key | Coverage | Notes |
|----------|:------:|-----|----------|-------|
| **CoinGecko** (`api/coingecko.py`) | ✅ | optional free Demo key | full coin universe | native market-cap ranking, recent price+volume vs BTC/USD |
| **CryptoCompare** (`api/cryptocompare.py`) | | required API key | full | legacy backend; exchange-aggregated history |

Select a backend with the `PRICE_PROVIDER` environment variable
(`coingecko` — default — or `cryptocompare`) and build it via the factory:

```python
from api import get_price_provider

provider = get_price_provider()          # honours PRICE_PROVIDER
coins = provider.get_top_coins_by_market_cap(n=300)
df = provider.get_full_daily_history("ETH", "BTC", provider_id="ethereum")
```

Halvix caches full history on the `raw-data` branch, so day-to-day the pipeline
only **tops up the most recent days** for the top coins by market cap — keeping
the call volume small.

---

## CoinGecko (default provider)

**API Documentation**: https://docs.coingecko.com/

### Endpoints used

| Data | Endpoint | CLI command |
|------|----------|-------------|
| Top coins by market cap | `/coins/markets` | `python -m main list-coins` |
| Recent price + volume | `/coins/{id}/market_chart` | `python -m main fetch-prices` |
| Reachability | `/ping` | (internal) |

- **Discovery** paginates `/coins/markets` (250 per page, sorted by market cap),
  keeping the highest-market-cap coin per symbol. Each coin's CoinGecko id
  (slug) is stored as `provider_id` in `coins_to_download.json` so price fetches
  can address the right series without a separate symbol→id map.
- **Price history** uses `/coins/{id}/market_chart`, which returns intraday
  points (hourly for multi-day ranges on the free tier). Halvix resamples these
  into **daily OHLCV** bars (open/high/low/close + BTC-denominated volume) and
  drops the incomplete current day.

### Authentication and rate limits

A free **Demo API key** (https://www.coingecko.com/en/api) raises the rate
limit. Provide it via `COINGECKO_API_KEY` (a CI secret); it is sent as the
`x-cg-demo-api-key` header. Keyless access also works but is throttled harder
and may return HTTP 429 under load — the client retries with exponential backoff
and enforces a configurable minimum interval between calls
(`COINGECKO_CALLS_PER_MINUTE`).

Because full history is already cached, the daily job is scoped to the top
~300 coins by market cap (`fetch-prices --limit 300`), which comfortably fits
the free monthly quota.

```python
# src/config.py
COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY") or None
COINGECKO_CALLS_PER_MINUTE = 25
COINGECKO_MARKETS_PER_PAGE = 250
COINGECKO_MAX_DAYS_PER_REQUEST = 360   # free-tier history cap (recent top-up only)
```

---

## CryptoCompare (alternative provider)

Selectable with `PRICE_PROVIDER=cryptocompare`. The CryptoCompare / CoinDesk
Data API **requires an API key** (free key at
https://developers.coindesk.com/); keyless requests return HTTP 401. Provide it
via `CRYPTOCOMPARE_API_KEY`.

| Data | Endpoint |
|------|----------|
| Top coins by market cap | `/data/top/mktcapfull` |
| Daily OHLCV prices | `/data/v2/histoday` |
| Rate-limit status | `/stats/rate/limit` |

The `CryptoCompareClient` adds **dynamic rate limiting**: it periodically polls
`/stats/rate/limit` and throttles when remaining quota drops below the
`RATE_LIMIT_HOURLY_THRESHOLD` / `RATE_LIMIT_MONTHLY_THRESHOLD` bounds, falling
back to `CRYPTOCOMPARE_API_CALLS_PER_MINUTE` when the status endpoint is
unavailable. It paginates `histoday` in 2000-day chunks for full-history
backfill and retries rate-limit errors with exponential backoff.

---

## Data Flow Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                         Halvix Pipeline                          │
├────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Step 1: Discover Coins (provider /coins/markets)                │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ Top coins by market cap (paginated)                        │ │
│  │ Output: data/processed/coins_to_download.json              │ │
│  │         data/processed/fetch_metadata.json                 │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              │                                   │
│                              ▼                                   │
│  Step 2: Filter Coins (local)                                    │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ Remove: wrapped, staked, bridged, stablecoins              │ │
│  │ Keep: BTC (for the BTC/USD chart)                          │ │
│  │ Output: data/processed/download_skipped.csv                │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              │                                   │
│                              ▼                                   │
│  Step 3: Fetch / top up prices (provider market_chart)           │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ Altcoins: {coin}/BTC   •   BTC: BTC/USD                    │ │
│  │ Incremental: only days since the last cached date          │ │
│  │ Output: data/raw/prices/{coin}-{quote}.parquet             │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              │                                   │
│                              ▼                                   │
│  Step 4: Calculate Volume-Weighted TOTAL2 (local)                │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ Daily top 30 altcoins by smoothed volume                   │ │
│  │ Output: data/processed/total2_index.parquet                │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                  │
└────────────────────────────────────────────────────────────────┘
```

---

## Output Files

| File | Description |
|------|-------------|
| `coins_to_download.json` | Coins accepted for price fetching (includes `provider_id`, `has_usd_data`) |
| `download_skipped.csv` | Coins filtered out (stablecoins, wrapped, etc.) |
| `download_failed.csv` | Coins that returned no data from the provider |
| `no_usd_data.csv` | Coins returned without USD price data (provider-dependent; empty for CoinGecko) |
| `fetch_metadata.json` | Counts and timestamp of the last discovery run |
| `total2_index.parquet` | Calculated TOTAL2 index |

### Price Data Files

Price data is stored as parquet, one file per coin-pair, named
`{coin_id}-{quote_currency}.parquet`:

```
data/raw/prices/
├── eth-btc.parquet    # ETH priced in BTC
├── btc-usd.parquet    # BTC priced in USD (special case)
├── xrp-btc.parquet
└── ... (one file per coin-pair)
```

---

## Caching and Incremental Updates

When running `fetch-prices` in incremental mode (default):

1. **Load existing cache** for the coin-pair.
2. **Determine new range**: from `last_cached_date + 1` to yesterday.
3. **Merge** with `pd.concat([cached, new_data])`.
4. **Deduplicate** by date (keep newest).
5. **Overwrite** the parquet file with the combined frame.

```python
# Simplified from src/data/fetcher.py
if not new_data.empty:
    combined = pd.concat([cached, new_data])
    combined = combined[~combined.index.duplicated(keep="last")]
    combined = combined.sort_index()
    price_cache.set_prices(coin_id, combined)
```

Overwriting (rather than appending) keeps things simple: the dataset is small,
daily updates add only a few rows, and a full rewrite avoids orphaned append
files. Price data never expires (historical data doesn't change); the coin-list
API response cache expires after 24 hours.

---

## Output schema

Every backend returns daily history with the same columns so cached parquet
files stay provider-compatible:

| Column | Description |
|--------|-------------|
| `open` / `high` / `low` / `close` | Daily prices in the quote currency |
| `volume_from` | Volume in the base currency |
| `volume_to` | Volume in the quote currency (used for TOTAL2 weighting) |

For CoinGecko, `market_chart` provides price and quote-currency volume directly;
open/high/low are derived from the intraday points and `volume_from` is the
implied base-asset volume.

---

## Troubleshooting

### Rate-limit (HTTP 429) errors

- **CoinGecko**: add a free Demo key (`COINGECKO_API_KEY`) for a higher limit,
  and keep using incremental mode. The client already retries with backoff.
- **CryptoCompare**: the client throttles dynamically against `/stats/rate/limit`;
  a valid `CRYPTOCOMPARE_API_KEY` is required at all.

### Authentication (HTTP 401/403) errors

The provider rejected the credentials. Set the appropriate key
(`COINGECKO_API_KEY` or `CRYPTOCOMPARE_API_KEY`) for the active `PRICE_PROVIDER`.

### Empty historical data

- The coin may be too new, or not tracked by the provider.
- Failed coins are recorded in `download_failed.csv` and shown in the
  "Skipped / Failed" section of `data_status.html`.

---

## Testing

```bash
# Provider unit tests (mocked, no network)
poetry run pytest tests/test_coingecko.py tests/test_cryptocompare.py -v

# CryptoCompare integration tests (real API; require a key)
poetry run pytest tests/test_cryptocompare_integration.py --run-integration -v
```

---

**[← Back to README](../README.md)**

# TOTAL2 Index Calculation

> **TOTAL2** is a volume-weighted market index representing the cryptocurrency market excluding Bitcoin. This document describes how Halvix calculates the TOTAL2 index.

## Overview

TOTAL2 provides a benchmark to compare individual coin performance against the overall altcoin market. Unlike a simple average, TOTAL2 is **volume-weighted**, meaning coins with higher trading volume have proportionally more influence on the index.

**Key features:**
- Volume smoothing using Simple Moving Average (SMA) to reduce daily volatility
- Vectorized calculation for efficient processing
- Support for both BTC and USD denominated prices

## Configuration

The TOTAL2 calculation uses these key variables from `src/config.py`:

```python
# Number of top coins to use for TOTAL2 calculation
TOP_N_FOR_TOTAL2 = 50

# Volume smoothing window for TOTAL2 calculation (days)
# Uses Simple Moving Average to smooth out daily volume spikes
VOLUME_SMA_WINDOW = 14

# Quote currencies for price data
QUOTE_CURRENCIES = ["BTC", "USD"]

# Default quote currency for analysis
DEFAULT_QUOTE_CURRENCY = "BTC"
```

These values can be modified to adjust the index calculation.

## Calculation Algorithm

### Daily TOTAL2 Value

For **each day** in the analysis window, TOTAL2 is calculated as follows:

```
TOTAL2(day) = Σ(price[i] × smoothed_volume[i]) / Σ(smoothed_volume[i])
              for i in top N coins by smoothed volume on that day
```

Where:
- `price[i]` = Close price of coin i on that day
- `smoothed_volume[i]` = 14-day SMA of 24h trading volume
- `N` = `TOP_N_FOR_TOTAL2` (default: 50)

### Volume Smoothing

Volume can change dramatically from one day to the next. To provide a more stable ranking, we apply a **14-day Simple Moving Average (SMA)** to the volume data:

```
smoothed_volume[day] = average(volume[day-13], volume[day-12], ..., volume[day])
```

**Important:** The first 13 days of each coin's data will have NaN values (warmup period) and are excluded from the calculation.

### Vectorized Implementation

The calculation uses a highly efficient vectorized approach:

```python
# 1. Build aligned DataFrames (coins as columns, dates as rows)
close_df = DataFrame(...)      # Shape: (num_days, num_coins)
volume_df = DataFrame(...)     # Shape: (num_days, num_coins)

# 2. Apply SMA to volume
smoothed_volume_df = volume_df.rolling(window=VOLUME_SMA_WINDOW).mean()

# 3. Rank by smoothed volume (highest = rank 1)
rank_df = smoothed_volume_df.rank(axis=1, ascending=False)

# 4. Create mask for top N coins
mask_df = rank_df <= TOP_N_FOR_TOTAL2

# 5. Calculate weighted average
masked_close = close_df.where(mask_df)
masked_volume = smoothed_volume_df.where(mask_df)
numerator = (masked_close * masked_volume).sum(axis=1)
denominator = masked_volume.sum(axis=1)
total2 = numerator / denominator
```

### Step-by-Step Process

```
1. LOAD all price data into aligned DataFrames
   - Rows: all dates from earliest to latest
   - Columns: coin IDs

2. APPLY SMA smoothing to volume data
   - Window: VOLUME_SMA_WINDOW (default: 14 days)
   - First 13 days per coin become NaN (warmup)

3. FILTER OUT (before ranking):
   - Bitcoin (BTC) - base currency
   - Wrapped tokens (wBTC, wETH, etc.)
   - Staked tokens (stETH, JitoSOL, etc.)
   - Bridged tokens
   - Stablecoins (USDT, USDC, DAI, etc.)

4. RANK coins by smoothed volume (per day, vectorized)

5. SELECT top N coins per day using rank mask

6. CALCULATE volume-weighted average price (vectorized)

7. BUILD composition records (which coins made top N each day)
```

### Example Calculation

For a given day with these top 3 coins (simplified example):

| Coin | Price (BTC) | 24h Volume (BTC) |
|------|-------------|------------------|
| ETH  | 0.050       | 50,000           |
| SOL  | 0.003       | 30,000           |
| XRP  | 0.00002     | 20,000           |

```
Total Volume = 50,000 + 30,000 + 20,000 = 100,000 BTC

TOTAL2 = (0.050 × 50,000 + 0.003 × 30,000 + 0.00002 × 20,000) / 100,000
       = (2,500 + 90 + 0.4) / 100,000
       = 2,590.4 / 100,000
       = 0.02590 BTC
```

## Dynamic Composition

**Important:** The coins included in TOTAL2 change day by day based on trading volume rankings.

- A coin might be #45 one day and #55 the next (dropping out of TOTAL2)
- New coins can enter the index as they gain trading activity
- This reflects the actual market dynamics over time

### Composition Tracking

Halvix saves the daily composition to `data/processed/total2_daily_composition.parquet`:

| date       | rank | coin_id    | volume        | weight   | price_btc |
|------------|------|------------|---------------|----------|-----------|
| 2024-01-01 | 1    | eth        | 50000         | 0.50     | 0.050     |
| 2024-01-01 | 2    | sol        | 30000         | 0.30     | 0.003     |
| 2024-01-01 | 3    | xrp        | 20000         | 0.20     | 0.00002   |
| 2024-01-02 | 1    | eth        | 52000         | 0.48     | 0.051     |
| ...        | ...  | ...        | ...           | ...      | ...       |

## Exclusions

### Always Excluded (from all analysis)

These are excluded because they don't represent independent price action:

- **Wrapped tokens**: wBTC, wETH, wSOL, wBNB
- **Staked tokens**: stETH, JitoSOL, mSOL, cbETH
- **Bridged tokens**: Arbitrum bridged, L2 bridged
- **Liquid staking derivatives**: Lido, Rocket Pool, Renzo, etc.

### Also Excluded for TOTAL2

These are additionally excluded from TOTAL2:

- **Stablecoins**: USDT, USDC, DAI, FRAX, GHO, etc.

Stablecoins are excluded because they don't track the crypto market - they're pegged to fiat currencies.

### NOT Excluded from TOTAL2: Recent Coins

**Important:** Recent coins (those without data before `MIN_DATA_DATE`) are **included** in TOTAL2 calculation. The `MIN_DATA_DATE` filter only applies to individual coin halving cycle analysis, not to TOTAL2.

#### Why Include Recent Coins?

TOTAL2 is designed to capture the cryptocurrency market trend, and its value for any given day **must remain immutable** once calculated. This immutability requirement is why we include all coins that qualify by volume, regardless of how recently they appeared.

**The problem with excluding recent coins:**

Consider a coin that launched in 2024 and quickly reached top 50 by trading volume. If we excluded it because it's "recent":

1. Today, calculating TOTAL2 for day D would exclude this coin
2. One year from now, this coin is no longer "recent" (it now has sufficient history)
3. Recalculating TOTAL2 for the same day D would now include this coin
4. **The TOTAL2 value for day D would change** - this breaks our immutability requirement

**Our intended behavior:**

- TOTAL2 for any day D should reflect the **actual market composition on that day**
- The value should be calculated once and remain stable forever
- The index must include all coins that were in the top 50 by 24h trading volume on that specific day
- No retroactive changes should occur when recalculating historical values

By including recent coins, we ensure that TOTAL2 accurately represents the full cryptocurrency market (restricted to top 50 by volume) on each day, and that this representation is permanent and reproducible.

### Never Excluded (Allowed List)

Some tokens with pattern-matching names are explicitly allowed:

- **SUI**, **SEI** - Layer 1 blockchains (not "staked" tokens)
- **STX** (Stacks), **STRK** (Starknet) - Have "st" prefix but aren't staked tokens
- **SAND** (The Sandbox), **WIF** (dogwifhat) - Legitimate tokens

## Output Files

| File | Format | Description |
|------|--------|-------------|
| `data/processed/total2_index.parquet` | Parquet | Daily TOTAL2 values |
| `data/processed/total2_daily_composition.parquet` | Parquet | Which coins were in top N each day |

### TOTAL2 Index Schema

```
date: datetime (index)
total2_price: float      # Volume-weighted average price in BTC
total_volume: float      # Sum of volumes of top N coins
coin_count: int          # Number of coins included (may be < N if not enough data)
```

### Daily Composition Schema

```
date: datetime
rank: int               # 1 to N
coin_id: str            # Coin symbol (lowercase)
volume: float           # 24h volume in BTC on that day
weight: float           # Proportion of total volume (0-1)
price_btc: float        # Price in BTC on that day
```

## Usage in Analysis

Once calculated, TOTAL2 is used as:

1. **Benchmark overlay** - Displayed as a grey line on individual coin charts
2. **Backfilling reference** - For coins without early data, their history is estimated using TOTAL2
3. **Performance comparison** - Coins are compared against TOTAL2 to identify outperformers

## Why Volume-Weighted?

Volume-weighted TOTAL2 has advantages over market-cap-weighted:

1. **Reflects actual market activity** - High volume means active trading
2. **Available historically** - Volume data is part of daily OHLCV
3. **Filters out dormant coins** - Low volume coins don't distort the index
4. **Single data source** - No need for separate market cap data

## Price Data Storage

Price data is stored in pair-based parquet files:

```
data/raw/prices/
├── eth-btc.parquet    # ETH priced in BTC
├── eth-usd.parquet    # ETH priced in USD
├── sol-btc.parquet    # SOL priced in BTC
├── sol-usd.parquet    # SOL priced in USD
└── ...
```

Each file contains OHLCV data:
- `open`, `high`, `low`, `close` - Price data
- `volume_from` - Volume in base currency (e.g., ETH)
- `volume_to` - Volume in quote currency (e.g., BTC or USD)

## Command Line Usage

```bash
# Calculate TOTAL2 with defaults
python -m main calculate-total2

# Custom parameters
python -m main calculate-total2 --top-n 100 --volume-sma 7 --quote-currency USD

# Generate visualizations (after calculating TOTAL2)
python -m main generate-charts
```

This generates:
- `output/charts/total2_halving_cycles.html` - TOTAL2 across 4 halving cycles
- `output/charts/total2_composition.html` - Interactive date picker to view TOTAL2 composition

## Related Configuration

From `src/config.py`:

```python
# TOTAL2 calculation
TOP_N_FOR_TOTAL2 = 50              # Number of coins in index
VOLUME_SMA_WINDOW = 14             # Days for volume SMA smoothing

# Quote currencies
QUOTE_CURRENCIES = ["BTC", "USD"]
DEFAULT_QUOTE_CURRENCY = "BTC"

# Output paths
TOTAL2_INDEX_FILE = PROCESSED_DIR / "total2_index.parquet"
TOTAL2_COMPOSITION_FILE = PROCESSED_DIR / "total2_daily_composition.parquet"
```

---

*See also: [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md) for full project specification*

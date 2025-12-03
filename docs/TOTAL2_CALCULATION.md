# TOTAL2 Index Calculation

> **TOTAL2** is a volume-weighted market index representing the cryptocurrency market excluding Bitcoin. This document describes how Halvix calculates the TOTAL2 index.

## Overview

TOTAL2 provides a benchmark to compare individual coin performance against the overall altcoin market. Unlike a simple average, TOTAL2 is **volume-weighted**, meaning coins with higher trading volume have proportionally more influence on the index.

## Configuration

The TOTAL2 calculation uses this key variable from `src/config.py`:

```python
# Number of top coins to use for TOTAL2 calculation
TOP_N_FOR_TOTAL2 = 50
```

This value can be modified to include more or fewer coins in the index.

## Calculation Algorithm

### Daily TOTAL2 Value

For **each day** in the analysis window, TOTAL2 is calculated as follows:

```
TOTAL2(day) = Σ(price_btc[i] × volume[i]) / Σ(volume[i])
              for i in top N coins by volume on that day
```

Where:
- `price_btc[i]` = Price of coin i in BTC on that day
- `volume[i]` = 24h trading volume of coin i in BTC on that day
- `N` = `TOP_N_FOR_TOTAL2` (default: 50)

### Step-by-Step Process

```
For each day in the analysis window:

    1. COLLECT 24h volume for all coins on that day

    2. FILTER OUT:
       - Bitcoin (BTC) - base currency
       - Wrapped tokens (wBTC, wETH, etc.)
       - Staked tokens (stETH, JitoSOL, etc.)
       - Bridged tokens (Arbitrum bridged, L2 bridged, etc.)
       - Liquid staking derivatives
       - Stablecoins (USDT, USDC, DAI, etc.)

    3. SORT remaining coins by 24h volume descending

    4. SELECT top N coins (default: 50)

    5. CALCULATE volume-weighted average:
       total_volume = sum(volume[i] for i in top_N)
       total2 = sum(price_btc[i] * volume[i] for i in top_N) / total_volume

    6. RECORD:
       - TOTAL2 value for that day
       - List of coins that made the top N that day (composition)
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

## Related Configuration

From `src/config.py`:

```python
# TOTAL2 calculation
TOP_N_FOR_TOTAL2 = 50              # Number of coins in index

# Output paths
TOTAL2_INDEX_FILE = PROCESSED_DIR / "total2_index.parquet"
TOTAL2_COMPOSITION_FILE = PROCESSED_DIR / "total2_daily_composition.parquet"
```

---

*See also: [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md) for full project specification*

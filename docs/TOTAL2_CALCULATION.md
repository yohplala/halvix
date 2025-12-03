# TOTAL2 Index Calculation

> **TOTAL2** is a weighted market index representing the cryptocurrency market excluding Bitcoin. This document describes how Halvix calculates the TOTAL2 index.

## Overview

TOTAL2 provides a benchmark to compare individual coin performance against the overall altcoin market. Unlike a simple average, TOTAL2 is **market-cap weighted**, meaning larger coins have proportionally more influence on the index.

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
TOTAL2(day) = Σ(price_btc[i] × market_cap[i]) / Σ(market_cap[i])
              for i in top N coins on that day
```

Where:
- `price_btc[i]` = Price of coin i in BTC on that day
- `market_cap[i]` = Market capitalization of coin i on that day
- `N` = `TOP_N_FOR_TOTAL2` (default: 50)

### Step-by-Step Process

```
For each day in the analysis window:

    1. COLLECT market_cap for all coins on that day
    
    2. FILTER OUT:
       - Bitcoin (BTC) - base currency
       - Wrapped tokens (wBTC, wETH, etc.)
       - Staked tokens (stETH, JitoSOL, etc.)
       - Bridged tokens (Arbitrum bridged, L2 bridged, etc.)
       - Liquid staking derivatives
       - Stablecoins (USDT, USDC, DAI, etc.)
    
    3. SORT remaining coins by market_cap descending
    
    4. SELECT top N coins (default: 50)
    
    5. CALCULATE weighted average:
       total_mcap = sum(market_cap[i] for i in top_N)
       total2 = sum(price_btc[i] * market_cap[i] for i in top_N) / total_mcap
    
    6. RECORD:
       - TOTAL2 value for that day
       - List of coins that made the top N that day (composition)
```

### Example Calculation

For a given day with these top 3 coins (simplified example):

| Coin | Price (BTC) | Market Cap |
|------|-------------|------------|
| ETH  | 0.050       | $400B      |
| SOL  | 0.003       | $80B       |
| XRP  | 0.00002     | $60B       |

```
Total Market Cap = 400B + 80B + 60B = 540B

TOTAL2 = (0.050 × 400B + 0.003 × 80B + 0.00002 × 60B) / 540B
       = (20B + 0.24B + 0.0012B) / 540B
       = 20.2412B / 540B
       = 0.0375 BTC
```

## Dynamic Composition

**Important:** The coins included in TOTAL2 change day by day based on market cap rankings.

- A coin might be #45 one day and #55 the next (dropping out of TOTAL2)
- New coins can enter the index as they grow in market cap
- This reflects the actual market dynamics over time

### Composition Tracking

Halvix saves the daily composition to `data/processed/total2_daily_composition.parquet`:

| date       | rank | coin_id    | market_cap    | weight   |
|------------|------|------------|---------------|----------|
| 2024-01-01 | 1    | ethereum   | 400000000000  | 0.741    |
| 2024-01-01 | 2    | solana     | 80000000000   | 0.148    |
| 2024-01-01 | 3    | ripple     | 60000000000   | 0.111    |
| 2024-01-02 | 1    | ethereum   | 410000000000  | 0.745    |
| ...        | ...  | ...        | ...           | ...      |

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
total2_price: float      # Weighted average price in BTC
total_market_cap: float  # Sum of market caps of top N coins
coin_count: int          # Number of coins included (may be < N if not enough data)
```

### Daily Composition Schema

```
date: datetime
rank: int               # 1 to N
coin_id: str            # CoinGecko coin ID
market_cap: float       # Market cap on that day
weight: float           # Proportion of total market cap (0-1)
price_btc: float        # Price in BTC on that day
```

## Usage in Analysis

Once calculated, TOTAL2 is used as:

1. **Benchmark overlay** - Displayed as a grey line on individual coin charts
2. **Backfilling reference** - For coins without early data, their history is estimated using TOTAL2
3. **Performance comparison** - Coins are compared against TOTAL2 to identify outperformers

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


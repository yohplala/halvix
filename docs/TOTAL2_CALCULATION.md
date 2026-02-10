# TOTAL2b Index Calculation

**[← Back to README](../README.md)**

---

> **TOTAL2b** is a volume-weighted market index representing the cryptocurrency market excluding Bitcoin. This document describes how Halvix calculates the TOTAL2b index.

## Overview

TOTAL2b provides a benchmark to compare individual coin performance against the overall altcoin market. Unlike a simple average, TOTAL2b is **volume-weighted**, meaning coins with higher trading volume have proportionally more influence on the index.

**Key features:**
- Volume smoothing using Simple Moving Average (SMA) to reduce daily volatility
- Vectorized calculation for efficient processing
- Support for both BTC and USD denominated prices
- Freeze period + price scaling for smooth new coin integration
- Symbol replacement detection (>30x price jump resets first_seen)

## Configuration

The TOTAL2b calculation uses these key variables from `src/config.py`:

```python
# Number of top coins to use for TOTAL2b calculation
TOP_N_BY_VOLUME_FOR_TOTAL2 = 30

# Minimum coins required to calculate index for a day
# If fewer coins have valid data, that day's index value is NaN
TOTAL2_MIN_COINS_FOR_INDEX = 3

# Volume smoothing window for TOTAL2b calculation (days)
# Uses Simple Moving Average to smooth out daily volume spikes
# 120 days (~4 months) provides stable ranking and reduces max weight change
VOLUME_SMA_WINDOW = 120

# Quote currencies for price data
QUOTE_CURRENCIES = ["BTC", "USD"]

# Default quote currency for analysis
DEFAULT_QUOTE_CURRENCY = "BTC"
```

Additional configuration for **TOTAL2b** in `src/config.py`:

```python
# TOTAL2b new coin entry settings (pre-entry freeze + scaling)
TOTAL2B_ENTRY_FREEZE_PERIOD_DAYS = 21   # Days to wait before coin can join (3 weeks)
TOTAL2B_MIN_COINS_FOR_SCALING = 30      # Only apply scaling after index has this many coins

# Symbol Replacement Detection: CryptoCompare sometimes reuses symbols for different
# tokens (e.g., old worthless "HYPE" replaced by Hyperliquid "HYPE" in Dec 2024,
# or old "OMG" replaced by OmiseGO in July 2017 with a 633x jump).
# When a coin's price jumps by more than this factor in a single day, we treat it
# as a symbol replacement and reset the first_seen date to after the jump.
TOTAL2B_SYMBOL_REPLACEMENT_THRESHOLD = 30  # 30x price change indicates symbol swap
```

## Calculation Algorithm

### Daily TOTAL2b Value

For **each day** in the analysis window, TOTAL2b is calculated as follows:

```
TOTAL2b(day) = Σ(price[i] × smoothed_volume[i]) / Σ(smoothed_volume[i])
               for i in top N coins by smoothed volume on that day
```

Where:
- `price[i]` = Close price of coin i on that day
- `smoothed_volume[i]` = 120-day SMA of 24h trading volume
- `N` = `TOP_N_BY_VOLUME_FOR_TOTAL2` (default: 30)

### Volume Smoothing

Volume can change dramatically from one day to the next. To provide a more stable ranking, we apply a **120-day Simple Moving Average (SMA)** to the volume data:

```
smoothed_volume[day] = average(volume[day-119], volume[day-118], ..., volume[day])
```

### Zero-Padding of 24h Volume

**Problem:** When a coin starts trading (first day of data), it could immediately have high volume and jump into the TOP30 with significant weight.

**Solution - Zero-Padding:** Instead of excluding the first 119 days of data, we prepend zeros:

1. For each coin, all days **before its first trading data** are filled with 0 volume
2. On a coin's first day with trading volume, its smoothed volume = `actual_volume / 120`
3. The weight gradually increases over the 120-day warmup period

**Implementation detail (`zero_pad=True`):**
- When `zero_pad=True` (default), the SMA treats missing values as zeros, causing new coins to enter gradually
- When `zero_pad=False`, the SMA only uses available data, which could allow immediate high weight entries
- The TOTAL2b calculation always uses `zero_pad=True` to ensure gradual entry

**Daily Weight Recalculation:**
Weights are recalculated daily based on the smoothed volume for that day. A coin's weight = `smoothed_volume[coin] / sum(smoothed_volume[top_N_coins])`. This means a coin's influence on the index changes as its relative volume changes over time.

### Volume Outlier Detection

CryptoCompare occasionally has bad data points with impossible volume spikes. These are automatically detected and corrected.

**Detection criteria:**
- Volume is **> 20x** the rolling median of past 7 days
- Volume is significant (**> 5,000 BTC**)
- Past median is **> 0**

**Correction method:**
1. Cap the outlier value at `20 × past_median`
2. Compute capped average: `(previous_day + cap) / 2`

---

## TOTAL2b Algorithm

TOTAL2b uses a price scaling mechanism which makes it resistant to distortion because of coin entry with large prices:

### 1. Freeze Period

When a coin first appears in CryptoCompare data, it must wait **21 days** before it can be included in the index. This:
- Ensures stable price data before inclusion
- Avoids launch-day volatility spikes
- Provides time for accurate volume SMA calculation

### 2. Symbol Replacement Detection

CryptoCompare sometimes reuses a symbol for a different token (e.g., old worthless "HYPE" replaced by Hyperliquid "HYPE" in Dec 2024, or old "OMG" replaced by OmiseGO in July 2017). When detected, the `first_seen` date is reset, and a new freeze period and price scaling apply to the new token.

**Detection criteria:**
- Price jumps by **>30x** (or **<1/30x**) in a single day
- Both the pre-jump and post-jump prices must be positive (not a coin starting to trade)
- Takes the **last** detected jump (handles multiple replacements)

**Effect:**
- The `first_seen` date is reset to the replacement date
- The freeze period restarts from this new date
- Any old scaling factor is discarded
- Price scaling is applied fresh when the "new" token enters the index

**Example - HYPE symbol replacement:**
- Original first-seen: 2018-05-15 (old worthless token)
- Symbol replacement detected: 2024-12-02 (price jump from ~0 to $30+)
- New first-seen: 2024-12-02
- Freeze period: 2024-12-02 to 2024-12-23
- Entry eligible: 2024-12-23 (with fresh price scaling)

### 3. Price Scaling

When a coin enters TOTAL2b (after passing the freeze period and reaching TOP30 by volume):

```python
scaling_factor = TOTAL2b_d-1 / COIN_PRICE_d
scaled_price = raw_price * scaling_factor
```

Where:
- `TOTAL2b_d-1` is the index value from the previous day
- `COIN_PRICE_d` is the coin's raw price on the day of entry
- `scaling_factor` is stored and applied to all future prices for this coin

**Why scaling works:**
- Preserves the coin's **day-over-day price change factor**
- Cancels any large absolute offsets in the index because of high coin price
- Applies only when index already has 30+ coins (established baseline)
- Persistent: once applied, the scaling factor remains for all subsequent days

### Algorithm Summary

```python
for each day:
    1. Calculate first-seen dates for all coins
       - Detect symbol replacements (>30x price jumps)
       - Reset first_seen date if replacement detected
    2. Filter eligible coins (passed freeze period + valid data)
    3. Detect new entries (coins entering TOP30 today)
    4. For new entries: apply price scaling (if index has 30+ coins)
       - scaling_factor = prev_total2b / entry_day_price
       - Apply to all future prices for this coin
    5. Calculate volume-weighted average of TOP30 (using scaled prices)
    6. Record composition
```

---

## Max Weight Change Tracking

**Purpose:** Ensure that TOTAL2b curve variations reflect actual price movements rather than sudden changes in coin weights.

**Implementation:**
- Calculate daily weight change for each coin in TOTAL2b
- Track the maximum absolute change
- Only track after **2016-07-04** when the index first has 30 coins
- Log a warning if max change exceeds 0.5%

---

## Dynamic Composition

The coins included in TOTAL2b change day by day based on trading volume rankings:
- A coin might be #25 one day and #35 the next (dropping out of TOTAL2b)
- New coins can enter the index as they gain trading activity
- This reflects actual market dynamics

### Composition Tracking

Halvix saves the daily composition to `data/processed/total2_daily_composition.parquet`:

| date       | rank | coin_id    | volume        | weight   | price_btc |
|------------|------|------------|---------------|----------|-----------|
| 2024-01-01 | 1    | eth        | 50000         | 0.50     | 0.050     |
| 2024-01-01 | 2    | sol        | 30000         | 0.30     | 0.003     |
| 2024-01-01 | 3    | xrp        | 20000         | 0.20     | 0.00002   |

---

## Exclusions

### Excluded from TOTAL2b

#### Bitcoin (BTC)
- **BTC** is excluded as the base currency (TOTAL2b represents the altcoin market)

#### Derivatives (no independent price action)
- **Wrapped tokens**: wBTC, wETH, wSOL, wBNB
- **Staked tokens**: stETH, JitoSOL, mSOL, cbETH
- **Bridged tokens**: Arbitrum bridged, L2 bridged

#### Stablecoins (pegged to fiat)
- **USD stablecoins**: USDT, USDC, DAI, FRAX, GHO, etc.
- **EUR stablecoins**: EURS, EURC, EURT, AGEUR

### Never Excluded (Allowed List)

Some tokens with pattern-matching names are explicitly allowed:
- **SUI**, **SEI** - Layer 1 blockchains (not "staked" tokens)
- **STX** (Stacks), **STRK** (Starknet) - Have "st" prefix but aren't staked tokens
- **SAND** (The Sandbox), **WIF** (dogwifhat) - Legitimate tokens

---

## Output Files

| File | Format | Description |
|------|--------|-------------|
| `data/processed/total2_index.parquet` | Parquet | Daily TOTAL2b values |
| `data/processed/total2_daily_composition.parquet` | Parquet | Which coins were in top N each day |
| `data/processed/total2_max_weight_change.json` | JSON | Statistics and outlier corrections |

### TOTAL2b Index Schema

```
date: datetime (index)
total2_price: float      # Volume-weighted average price in BTC
total_volume: float      # Sum of volumes of top N coins
coin_count: int          # Number of coins included
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

---

## Command Line Usage

```bash
# Calculate TOTAL2b (default, recommended)
poetry run python -m main calculate-total2

# Custom parameters
poetry run python -m main calculate-total2 --top-n 100 --volume-sma 7 --quote-currency USD

# Generate visualizations (after calculating)
poetry run python -m main generate-cycle-charts
```

### CLI Options

| Option | Default | Description |
|--------|---------|-------------|
| `--top-n` | 30 | Number of coins in index |
| `--volume-sma` | 120 | Days for volume SMA smoothing |
| `--quote-currency` | BTC | Quote currency for prices |
| `--dry-run` | false | Calculate but don't save results |

---

## Code Architecture

The processor code is organized into modules:

```
src/data/
├── processor.py           # Re-exports and factory function
├── processor_base.py      # BaseTotal2Processor (shared algorithms)
├── processor_total2b.py   # Total2bProcessor
└── price_filters.py       # Common filtering tools (shared with pattern analysis)
```

### Common Filtering Tools

The volume outlier detection and SMA smoothing algorithms are implemented as standalone functions in `src/data/price_filters.py`. These are used by:

- **TOTAL2b calculation**: via `processor_base.py`
- **Pattern analysis**: via `cycle_patterns.py`

This ensures consistent data quality across all analysis modules.

**Available functions:**

| Function | Description |
|----------|-------------|
| `apply_volume_corrections_to_dataframe()` | Apply corrections to a DataFrame of multiple coins |
| `apply_volume_sma_smoothing_to_dataframe()` | Apply smoothing to a DataFrame of multiple coins |

### Using the Processor

```python
from data.processor import get_processor, Total2bProcessor

# Use factory function (recommended)
processor = get_processor()
result = processor.calculate_total2()

# Direct instantiation
processor = Total2bProcessor(
    top_n=30,
    volume_sma_window=120,
    freeze_period_days=21,
)
```

### Total2Result

The processor returns a `Total2Result` dataclass:

```python
@dataclass
class Total2Result:
    index_df: pd.DataFrame           # Daily index values
    composition_df: pd.DataFrame     # Daily composition
    coins_processed: int             # Total coins processed
    date_range: tuple[date, date]    # Start and end dates
    avg_coins_per_day: float         # Average coins per day
    max_weight_change: float | None  # Max daily weight change
    max_weight_change_coin: str | None
    max_weight_change_date: date | None
    volume_outliers_corrected: list[dict] | None
    price_outliers_corrected: list[dict] | None  # Scaling events
    index_type: str                  # "total2b"
```

---

*See also: [CLAUDE.md](../CLAUDE.md) for full project specification*

---

**[← Back to README](../README.md)**

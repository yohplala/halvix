# TOTAL2 Index Calculation

**[← Back to README](../README.md)**

---

> **TOTAL2** is a volume-weighted market index representing the cryptocurrency market excluding Bitcoin. This document describes how Halvix calculates the TOTAL2 index, including both the legacy (TOTAL2) and new (TOTAL2b) methodologies.

## Overview

TOTAL2 provides a benchmark to compare individual coin performance against the overall altcoin market. Unlike a simple average, TOTAL2 is **volume-weighted**, meaning coins with higher trading volume have proportionally more influence on the index.

**Key features:**
- Volume smoothing using Simple Moving Average (SMA) to reduce daily volatility
- Vectorized calculation for efficient processing
- Support for both BTC and USD denominated prices
- **Two calculation methodologies**: Legacy (TOTAL2) and New (TOTAL2b)

## TOTAL2 vs TOTAL2b

Halvix supports two methodologies for calculating the index:

| Feature | TOTAL2 (Legacy) | TOTAL2b (New, Default) |
|---------|-----------------|------------------------|
| **Volume smoothing** | 120-day SMA with zero-padding | 120-day SMA with zero-padding |
| **Volume outlier correction** | ✓ Yes | ✓ Yes |
| **New coin integration** | 21-day entry warmup (post-entry) | 21-day freeze period (pre-entry) |
| **Price adjustment (post-entry)** | Cap price changes during warmup | Scale by TOTAL2b_d-1/COIN_PRICE_d |
| **Symbol replacement detection** | ✗ No | ✓ Yes (>100x price jump resets first_seen) |
| **TOTAL2 series smoothing** | ✓ Yes (caps extreme movements) | ✗ No |

### Entry Timing Comparison

Both methodologies use a **21-day period** but apply it differently:

| Aspect | TOTAL2 (Legacy) | TOTAL2b (New) |
|--------|-----------------|---------------|
| **When** | After coin enters TOP30 | After first appearance on CryptoCompare (or after symbol replacement) |
| **Mechanism** | Entry warmup: cap price changes | Freeze period: coin not eligible for inclusion, cannot enter TOP30 |
| **Duration** | `TOTAL2_ENTRY_WARMUP_PERIOD_DAYS` (21 days) | `TOTAL2B_ENTRY_FREEZE_PERIOD_DAYS` (21 days) |
| **Effect** | Prevent index spikes from extreme prices | Ensure stable data before inclusion |
| **Symbol replacement** | Not handled | Resets first_seen date, restarts freeze period |

### When to Use Each

- **TOTAL2b (default)**: Recommended for new analyses, as this index is no longer distorted by coin entries with high absolute price values.
- **TOTAL2 (legacy)**: For backward compatibility with existing analyses.

## Configuration

The TOTAL2 calculation uses these key variables from `src/config.py`:

```python
# Number of top coins to use for TOTAL2 calculation
TOP_N_BY_VOLUME_FOR_TOTAL2 = 30

# Volume smoothing window for TOTAL2 calculation (days)
# Uses Simple Moving Average to smooth out daily volume spikes
# 120 days (~4 months) provides stable ranking and reduces max weight change
VOLUME_SMA_WINDOW = 120

# Quote currencies for price data
QUOTE_CURRENCIES = ["BTC", "USD"]

# Default quote currency for analysis
DEFAULT_QUOTE_CURRENCY = "BTC"
```

Additional configuration for **TOTAL2 (legacy)** in `src/config.py`:

```python
# TOTAL2 entry warmup settings (post-entry monitoring)
TOTAL2_ENTRY_MAX_INCREASE = 1.7    # Threshold for tracking large increases (70% gain)
TOTAL2_ENTRY_MAX_DECREASE = 0.5    # Threshold for tracking large decreases (50% loss)
TOTAL2_ENTRY_WARMUP_PERIOD_DAYS = 21  # Monitor for first 21 days after entry
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
TOTAL2B_SYMBOL_REPLACEMENT_THRESHOLD = 100  # 100x price change indicates symbol swap
```

## Calculation Algorithm

### Daily TOTAL2 Value

For **each day** in the analysis window, TOTAL2 is calculated as follows:

```
TOTAL2(day) = Σ(price[i] × smoothed_volume[i]) / Σ(smoothed_volume[i])
              for i in top N coins by smoothed volume on that day
```

Where:
- `price[i]` = Close price of coin i on that day
- `smoothed_volume[i]` = 120-day SMA of 24h trading volume
- `N` = `TOP_N_BY_VOLUME_FOR_TOTAL2` (default: 30)

### Volume Smoothing (Shared)

Volume can change dramatically from one day to the next. To provide a more stable ranking, we apply a **120-day Simple Moving Average (SMA)** to the volume data:

```
smoothed_volume[day] = average(volume[day-119], volume[day-118], ..., volume[day])
```

### Zero-Padding of 24h Volume (Shared)

**Problem:** When a coin starts trading (first day of data), it could immediately have high volume and jump into the TOP30 with significant weight.

**Solution - Zero-Padding:** Instead of excluding the first 119 days of data, we prepend zeros:

1. For each coin, all days **before its first trading data** are filled with 0 volume
2. On a coin's first day with trading volume, its smoothed volume = `actual_volume / 120`
3. The weight gradually increases over the 120-day warmup period

### Volume Outlier Detection (Shared)

CryptoCompare occasionally has bad data points with impossible volume spikes. These are automatically detected and corrected.

**Detection criteria:**
- Volume is **> 20x** the rolling median of past 7 days
- Volume is significant (**> 5,000 BTC**)
- Past median is **> 0**

**Correction method:**
1. Cap the outlier value at `20 × past_median`
2. Compute capped average: `(previous_day + cap) / 2`

---

## TOTAL2b Algorithm (New, Default)

TOTAL2b uses a price scaling mechanism which makes it resistant to distortion because of coin entry with large prices:

### 1. Freeze Period

When a coin first appears in CryptoCompare data, it must wait **21 days** before it can be included in the index. This:
- Ensures stable price data before inclusion
- Avoids launch-day volatility spikes
- Provides time for accurate volume SMA calculation

### 2. Symbol Replacement Detection

CryptoCompare sometimes reuses a symbol for a different token (e.g., old worthless "HYPE" replaced by Hyperliquid "HYPE" in Dec 2024, or old "OMG" replaced by OmiseGO in July 2017). When detected, the `first_seen` date is reset, and a new freeze period and price scaling apply to the new token.

**Detection criteria:**
- Price jumps by **>100x** (or **<0.01x**) in a single day
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

### 4. No TOTAL2 Series Smoothing

TOTAL2b does **not** apply TOTAL2 series smoothing (capping extreme index movements). The freeze period, symbol replacement detection, and price scaling provide sufficient protection against entry spikes, making additional smoothing unnecessary.

### Algorithm Summary

```python
for each day:
    1. Calculate first-seen dates for all coins
       - Detect symbol replacements (>100x price jumps)
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

## TOTAL2 Algorithm (Legacy)

TOTAL2 (legacy) uses entry warmup price capping and TOTAL2 series smoothing:

### 1. Entry Warmup (Price Capping)

When a new coin enters the TOP30, its price is **capped** during a **21-day warmup period** to prevent artificial spikes in the index.

**Mechanism:**
1. On entry day: Baseline is set to the **TOTAL2 value from the previous day** (market level)
2. Each subsequent day: Cap price changes relative to the previous day's **capped price**
3. The coin's price contribution gradually converges to its actual price over the warmup period

| Parameter | Value | Description |
|-----------|-------|-------------|
| **Max Increase** | 1.7x (70% gain) | Cap price increases at this factor |
| **Max Decrease** | 0.5x (50% loss) | Cap price decreases at this factor |
| **Duration** | 21 days | Warmup capping period |

**Example - ZEC entering on 2016-10-28:**
- Day 0 (before): Baseline = TOTAL2 value (~0.01 BTC)
- Day 1: Actual 27.8 BTC → **Capped at** 1.7x = 0.017 BTC
- Day 2: Actual 2.79 BTC → **Capped at** 1.7x = 0.029 BTC
- ... converges to actual price (~1-2 BTC) in ~9 days

### 2. TOTAL2 Series Smoothing

Extreme day-over-day movements in the **aggregate TOTAL2 index** are capped:
- **Increases > 200%**: Capped at 3x the previous day's value
- **Decreases > 65%**: Floored at 0.35x the previous day's value

This prevents the index from having extreme jumps when coins with unusual prices enter or exit.

### 3. Two-Pass Algorithm

```python
# Pass 1: Calculate Raw TOTAL2 (to get baseline for entry capping)
raw_total2 = calculate_weighted_average(close_df, smoothed_volume_df, mask_df)

# Apply entry warmup: CAP prices for coins during warmup period
capped_close_df, warmup_events = apply_entry_warmup_capping(close_df, raw_total2, mask_df)

# Pass 2: Recalculate TOTAL2 with capped prices
total2_series = calculate_weighted_average(capped_close_df, smoothed_volume_df, mask_df)

# Apply TOTAL2 series smoothing (cap extreme day-over-day movements in aggregate)
smoothed_total2 = apply_series_smoothing(total2_series)
```

---

## Max Weight Change Tracking

**Purpose:** Ensure that TOTAL2 curve variations reflect actual price movements rather than sudden changes in coin weights.

**Implementation:**
- Calculate daily weight change for each coin in TOTAL2
- Track the maximum absolute change
- Only track after **2016-07-04** when TOTAL2 first has 30 coins
- Log a warning if max change exceeds 0.5%

---

## Dynamic Composition

The coins included in TOTAL2 change day by day based on trading volume rankings:
- A coin might be #25 one day and #35 the next (dropping out of TOTAL2)
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

### Excluded from TOTAL2 (Both Versions)

#### Bitcoin (BTC)
- **BTC** is excluded as the base currency (TOTAL2 represents the altcoin market)

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
| `data/processed/total2_index.parquet` | Parquet | Daily TOTAL2 values |
| `data/processed/total2_daily_composition.parquet` | Parquet | Which coins were in top N each day |
| `data/processed/total2_max_weight_change.json` | JSON | Statistics and outlier corrections |

### TOTAL2 Index Schema

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
python -m main calculate-total2

# Calculate legacy TOTAL2
python -m main calculate-total2 --index-type total2

# Custom parameters
python -m main calculate-total2 --top-n 100 --volume-sma 7 --quote-currency USD

# Generate visualizations (after calculating)
python -m main generate-charts
```

### CLI Options

| Option | Default | Description |
|--------|---------|-------------|
| `--index-type` | `total2b` | Index type: `total2` (legacy) or `total2b` (new) |
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
├── processor_total2.py    # Total2Processor (legacy)
└── processor_total2b.py   # Total2bProcessor (new)
```

### Using the Processors

```python
from data.processor import get_processor, Total2Processor, Total2bProcessor

# Use factory function (recommended)
processor = get_processor("total2b")  # or "total2"
result = processor.calculate_total2()

# Direct instantiation
processor = Total2bProcessor(
    top_n=30,
    volume_sma_window=120,
    freeze_period_days=21,
)
```

### Total2Result

Both processors return a `Total2Result` dataclass:

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
    price_outliers_corrected: list[dict] | None  # Or scaling events for TOTAL2b
    index_type: str                  # "total2" or "total2b"
```

---

*See also: [AGENTS.md](../AGENTS.md) for full project specification*

---

**[← Back to README](../README.md)**

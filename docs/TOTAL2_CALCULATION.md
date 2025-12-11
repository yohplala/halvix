# TOTAL2 Index Calculation

**[← Back to README](../README.md)**

---

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
TOP_N_FOR_TOTAL2 = 30

# Volume smoothing window for TOTAL2 calculation (days)
# Uses Simple Moving Average to smooth out daily volume spikes
# 120 days (~4 months) provides stable ranking and reduces max weight change
VOLUME_SMA_WINDOW = 120

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
- `smoothed_volume[i]` = 120-day SMA of 24h trading volume
- `N` = `TOP_N_FOR_TOTAL2` (default: 30)

### Volume Smoothing

Volume can change dramatically from one day to the next. To provide a more stable ranking, we apply a **120-day Simple Moving Average (SMA)** to the volume data:

```
smoothed_volume[day] = average(volume[day-119], volume[day-118], ..., volume[day])
```

### Zero-Padding of 24h Volume

**Problem:** When a coin starts trading (first day of data), it could immediately have high volume and jump into the TOP30 with significant weight. This creates a sudden change in TOTAL2 composition that doesn't reflect actual market price movements.

**Example:** YFI appeared on 2020-09-19 with >2.5% weight, causing a sudden leg up in the TOTAL2 curve unrelated to actual market performance.

**Solution - Zero-Padding:** Instead of excluding the first 119 days of data (120-day SMA warmup period), we prepend zeros:

1. For each coin, all days **before its first trading data** are filled with 0 volume
2. On a coin's first trading day, its smoothed volume = `actual_volume / 120`
3. The weight gradually increases over the 120-day warmup period as more actual data enters the SMA

**Result:** When a coin first has trade data and potentially enters the TOP30, it does so gradually. Its weight starts at ~0.83% of what it would be without smoothing (1/120) and increases linearly over the SMA window.

### Max Weight Change Tracking

**Purpose:** Ensure that TOTAL2 curve variations reflect actual price movements rather than sudden changes in coin weights within the index.

**Why it matters:** If a coin's weight suddenly changes by 2%, even if its price stays the same, it can move the TOTAL2 value significantly. We want the index to track market performance, not composition shuffling.

**Implementation:**
- Calculate daily weight change for each coin in TOTAL2
- Track the maximum absolute change (positive or negative)
- Only track after **2016-07-04** when TOTAL2 first has 30 coins (avoids early noise)
- Log a warning if max change exceeds 0.5%

**Tuning:** If the max weight change is too high, consider increasing `VOLUME_SMA_WINDOW` to smooth more aggressively. The goal is to keep max daily weight changes below 0.5-0.6%.

### Automatic Outlier Detection and Correction

CryptoCompare occasionally has bad data points with impossible volume spikes. Additionally, coins launching with extreme prices (like ZEC at 27.8 BTC) can distort TOTAL2. Both issues are automatically detected and corrected.

**IMPORTANT: Past-Only Principle**

All outlier detection and correction uses **only past data** (data available at time t, not future data). This ensures:
1. The algorithm can run iteratively as new data appears
2. Past TOTAL2 values are never recalculated
3. Index immutability is maintained

Corrections rely on past data only:
- **Volume outliers:** detected using rolling median of past 7 days
- **Price outliers (day-over-day):** detected using previous day's price
- **Price warmup capping:** uses corrected TOTAL2 values (market level from past) as baseline

#### Volume Outlier Detection

Volume spikes can severely distort the TOTAL2 calculation by inflating a coin's weight artificially.

**How Outliers Are Detected:**

The system uses rolling statistics on **past data only** to detect outliers:

```python
# Calculate rolling median using ONLY past data (not centered)
rolling_median = volume_df.rolling(window=7, min_periods=3).median()

# Shift to exclude current day from median calculation
past_median = rolling_median.shift(1)

# Calculate ratio and identify outliers
ratio_df = volume_df / past_median
is_outlier = (
    (ratio_df > OUTLIER_THRESHOLD)
    & (volume_df > MIN_VOLUME_FOR_OUTLIER_CHECK)
    & (past_median > 0)  # Don't flag new coins as outliers
)
```

Detection criteria:
- Volume is **> 20x** the rolling median of past 7 days
- Volume is significant (**> 5,000 BTC**) to focus on spikes that impact TOTAL2
- Past median is **> 0** (new coins with no valid past data are not flagged)

**How Outliers Are Corrected (Capped Average):**

Detected outliers are replaced using a **capped average** approach that only uses past data:

1. Skip if `previous_day <= 0` or `past_median <= 0` (cannot correct without valid past data)
2. Cap the outlier value at `OUTLIER_THRESHOLD × past_median`
3. Compute capped average: `(previous_day + cap) / 2`

This approach:
- Smooths out spikes while preserving trend direction
- Never uses future data for correction
- Skips correction for new coins (e.g., UNI on launch day) that have no valid past data

**Known Bad Data Examples:**

| Coin | Date | Original | Corrected | Ratio | Impact |
|------|------|----------|-----------|-------|--------|
| PPC | 2017-12-10 | 90,215,196 BTC | 56 BTC | 730,724x | Would dominate TOTAL2 for 120 days |
| BCH | 2018-06-20 | 756,204 BTC | 19,415 BTC | 37x | Caused -5.4% weight drop when exiting SMA |
| BCH | 2022-12-11 | 7,058 BTC | 47 BTC | 121x | Volume spike artifact |
| BTT | 2019-02-04 | 33,415 BTC | 4,989 BTC | 1,208x | Launch day anomaly |

**Why This Matters:**

Without correction, a single bad data point can:
1. Inflate a coin's weight in TOTAL2 for the entire SMA window (120 days)
2. Cause a sudden weight drop when the bad data exits the SMA window
3. Create artificial jumps in the TOTAL2 curve unrelated to actual market movements

**Example - BCH 2018-06-20:**
- Bad volume: 756,204 BTC (normal: ~20,000 BTC)
- This inflated BCH's smoothed volume for 120 days
- On 2018-08-19, this bad data point exited the SMA window
- Result: BCH weight suddenly dropped by 5.4% in one day
- Without correction, this creates artificial TOTAL2 curve movements

**Configuration:**

The outlier detection parameters are defined in `processor.py`:
```python
VOLUME_OUTLIER_THRESHOLD = 20  # 20x median - catches BCH (37x), PPC (730,724x)
MIN_VOLUME_FOR_OUTLIER_CHECK = 5000  # Only significant spikes (BTC)
OUTLIER_WINDOW_DAYS = 7  # Rolling window of past 7 days for median calculation
```

**Design Decisions:**

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Threshold | 20x | Low enough to catch BCH (37x), high enough to avoid false positives |
| Min Volume | 5,000 BTC | Only correct spikes that would impact TOP30 rankings significantly |
| Window | 7 days | Wide enough for stable median, narrow enough to catch isolated spikes |

The high minimum volume ensures we only correct spikes that would significantly impact TOTAL2 rankings. Small coins with natural volatility (e.g., 100 BTC → 2,000 BTC) are left unchanged.

**Reporting:**

All volume corrections are:
1. Logged during TOTAL2 calculation (top 20 by ratio shown)
2. Saved to `data/processed/total2_max_weight_change.json`
3. Displayed on the "TOTAL2 Statistics" page (`site/total2_statistics.html`)

#### Price Outlier Detection

Extreme price moves can also distort TOTAL2, especially launch day spikes.

**TOTAL2 Entry Warmup (Price Capping):**

When a coin first enters TOTAL2 (TOP30 by volume), its price may cause artificial spikes in the index. Halvix applies iterative price capping to prevent this:

| Parameter | Value | Description |
|-----------|-------|-------------|
| **Trigger** | First day in TOTAL2 | Applied when coin enters TOP30 |
| **Max Increase** | 1.7x (70% gain) | Price can't increase more than 70% per day |
| **Max Decrease** | 0.5x (50% loss) | Price can't decrease more than 50% per day |
| **Duration** | 21 days | Warmup capping lasts 3 weeks |
| **Baseline** | Corrected TOTAL2 | Market level from day before entry |

**How Capping Works:**

We use iterative capping (not SMA smoothing):
1. Day 0 (before entry): Use corrected TOTAL2 as baseline (market level)
2. Day 1+: Cap price at MAX_INCREASE × previous day's capped price
3. If actual price is below cap, use actual price (converged)
4. For crashes: floor at MAX_DECREASE × previous day's capped price

**CASE 1 - ZEC (2016-10-28): Listed AND entered TOTAL2 on the same day**
- Day 0: baseline = ~0.01 BTC (corrected TOTAL2)
- Day 1: Actual 27.8 BTC → Cap at 1.7x = **0.017 BTC**
- Day 2: Actual 2.79 BTC → Cap at 1.7x = **0.029 BTC**
- Day 3: Actual 0.77 BTC → Cap at 1.7x = **0.049 BTC**
- ... price increases 70% each day until cap ≥ actual
- **Converges in ~10 days** when cap catches up to ZEC's crashed price (~1-2 BTC)

**CASE 2 - YFI (2020-09-14): Entered TOTAL2 45 days after listing**
- Day 0: baseline = ~0.012 BTC (corrected TOTAL2)
- Day 1: Actual 3.73 BTC → Cap at 1.7x = **0.020 BTC**
- Day 2: Actual 3.27 BTC → Cap at 1.7x = **0.034 BTC**
- ... price grows 70% each day until cap ≥ actual
- **Converges in ~11 days** when cap catches up to YFI's price (~3.7 BTC)

Both cases gradually ramp up from market level, preventing artificial TOTAL2 spikes.

**Two-Pass Algorithm:**

1. **Pass 1: Calculate Raw TOTAL2**
   - Build aligned DataFrames (apply volume and day-over-day price corrections)
   - Apply volume SMA smoothing (zero-padded, 120-day window)
   - Calculate raw TOTAL2 (may have spikes from coin entries)
   - Apply TOTAL2 outlier detection to the raw series

2. **Pass 2: Apply TOTAL2 Entry Warmup**
   - For each coin entering TOTAL2: fill pre-entry prices with corrected TOTAL2 values
   - Apply iterative price capping for first 21 days after entry
   - Recalculate final TOTAL2 with capped prices

**Why TOTAL2-Based Filling (Not Zero-Padding):**

Using corrected TOTAL2 values as the fill value:
- Provides a sensible "market level" baseline
- Smooths transition from market level to actual price
- Prevents both artificial spikes AND crashes in TOTAL2

```python
# Two-pass calculation
# Pass 1: Raw TOTAL2
close_df_raw, volume_df, volume_outliers, price_outliers = build_aligned_dataframes(price_data)
raw_total2 = calculate_raw_total2(close_df_raw, volume_df)

# Apply TOTAL2 outlier detection
corrected_total2 = apply_total2_outlier_detection(raw_total2)

# Pass 2: TOTAL2 entry warmup (iterative capping for 21 days after entering TOTAL2)
close_df_smoothed = apply_total2_entry_warmup(close_df_raw, mask_df, corrected_total2)

# Final TOTAL2 with capped prices
final_total2 = calculate_total2(close_df_smoothed, volume_df)
```

**Day-Over-Day Price Outlier Correction (Past-Only):**

For price spikes (>5x increase):
```python
max_allowed = previous_price * PRICE_OUTLIER_THRESHOLD
corrected = (previous_price + max_allowed) / 2
```

For price crashes (>80% drop):
```python
min_allowed = previous_price / PRICE_OUTLIER_THRESHOLD
corrected = (previous_price + min_allowed) / 2
```

**Edge case handling:**
- Skip if `previous_price <= 0` (cannot correct without valid past data)
- Skip if `original_price <= 0` (coin not yet listed, no correction needed)
- Require both current AND previous price > `MIN_PRICE_FOR_OUTLIER_CHECK`

**Configuration:**

```python
# Day-over-day price outlier detection
PRICE_OUTLIER_THRESHOLD = 5  # >5x or <0.2x triggers correction
MIN_PRICE_FOR_OUTLIER_CHECK = 0.001  # Only check meaningful prices (BTC)

# TOTAL2 series outlier detection
TOTAL2_OUTLIER_THRESHOLD = 2  # >2x or <0.5x triggers TOTAL2 series correction

# TOTAL2 entry warmup (price capping for coins entering TOTAL2)
TOTAL2_ENTRY_MAX_INCREASE = 1.7  # Max 1.7x (70% gain) per day
TOTAL2_ENTRY_MAX_DECREASE = 0.5  # Min 0.5x (50% loss) per day
TOTAL2_ENTRY_WARMUP_DAYS = 21  # Apply capping for first 21 days after entry
```

#### Iterative Correction

Both volume and price outlier corrections are applied **iteratively** to handle consecutive outliers:

```python
for iteration in range(max_iterations):
    # Detect outliers using corrected data from previous iteration
    is_outlier = detect_outliers(corrected_df)

    if no_outliers_found:
        break

    # Apply corrections using only past (already corrected) data
    for outlier in outliers:
        corrected_df[outlier] = compute_correction(corrected_df)
```

**Why iterative correction is necessary:**

Consider two consecutive days with outliers:
- Day t: Volume spike 100x median → corrected
- Day t+1: Original volume might be 50x the UNCORRECTED day t value

Without iteration, day t+1's correction would be based on the wrong reference.
With iteration, after correcting day t, we re-check day t+1 against the corrected value.

**Iteration limits:**
- Maximum 10 iterations to prevent infinite loops
- In practice, convergence typically occurs in 1-3 iterations

### Vectorized Implementation

The calculation uses a two-pass vectorized approach:

```python
# 1. Filter coin IDs before loading (excludes BTC, derivatives, stablecoins)
eligible_coins = filter_coins_for_total2(all_cached_coins)

# 2. Load price data, build aligned DataFrames, apply outlier corrections
close_df_raw, volume_df, volume_outliers, price_outliers = build_aligned_dataframes(price_data)

# 3. Apply SMA to volume (zero-padded for gradual entry)
smoothed_volume_df = volume_df.rolling(window=VOLUME_SMA_WINDOW).mean()

# 4. Rank by smoothed volume (highest = rank 1)
rank_df = smoothed_volume_df.rank(axis=1, ascending=False)
mask_df = rank_df <= TOP_N_FOR_TOTAL2

# 5. PASS 1: Calculate raw TOTAL2
raw_total2 = (close_df_raw.where(mask_df) * smoothed_volume_df.where(mask_df)).sum(axis=1) / \
             smoothed_volume_df.where(mask_df).sum(axis=1)

# 6. Apply TOTAL2 outlier detection
corrected_total2 = apply_total2_outlier_detection(raw_total2)

# 7. PASS 2: Apply TOTAL2 entry warmup (handles ZEC and YFI cases)
# For each coin: fill pre-entry prices with market level, apply iterative capping for 21 days
close_df_smoothed = apply_total2_entry_warmup(close_df_raw, mask_df, corrected_total2)

# 8. Calculate final TOTAL2 with capped prices
masked_close = close_df_smoothed.where(mask_df)
masked_volume = smoothed_volume_df.where(mask_df)
total2 = (masked_close * masked_volume).sum(axis=1) / masked_volume.sum(axis=1)
```

### Step-by-Step Process

```
1. GET all cached coin IDs from price data directory

2. FILTER coin IDs (before loading any price data):
   - Bitcoin (BTC) - excluded as base currency
   - Wrapped tokens (wBTC, wETH, etc.)
   - Staked tokens (stETH, JitoSOL, etc.)
   - Bridged tokens
   - Stablecoins (USDT, USDC, DAI, etc.)
   → Excluded coins are NEVER loaded

3. LOAD price data for eligible coins only
   - Build aligned DataFrames (coins as columns, dates as rows)
   - Apply volume outlier detection and correction
   - Apply day-over-day price outlier correction

4. PASS 1: Calculate raw TOTAL2
   - Apply SMA smoothing to volume data (zero-padded, 120-day window)
   - Rank coins by smoothed volume, create TOP30 mask
   - Calculate raw TOTAL2 (may have spikes from coin entries)
   - Apply TOTAL2 outlier detection to the raw series

5. PASS 2: Apply TOTAL2 entry warmup
   - For each coin entering TOTAL2: identify first entry date
   - Fill pre-entry prices with corrected TOTAL2 values (market level)
   - Apply iterative price capping for first 21 days after entry
   - Handles both ZEC-type (entry on listing day) and YFI-type (entry weeks later)

6. CALCULATE final TOTAL2 with capped prices (vectorized)

7. BUILD composition records (which coins made top N each day)
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

### Excluded from TOTAL2

The following coins are **filtered out before loading** price data. They are never included in the TOTAL2 calculation:

#### Bitcoin (BTC)
- **BTC** is excluded as the base currency (TOTAL2 represents the altcoin market)

#### Derivatives (no independent price action)
- **Wrapped tokens**: wBTC, wETH, wSOL, wBNB
- **Staked tokens**: stETH, JitoSOL, mSOL, cbETH
- **Bridged tokens**: Arbitrum bridged, L2 bridged
- **Liquid staking derivatives**: Lido, Rocket Pool, Renzo, etc.

#### Stablecoins (pegged to fiat)
- **USD stablecoins**: USDT, USDC, DAI, FRAX, GHO, etc.
- **EUR stablecoins**: EURS, EURC, EURT, AGEUR
- **Algorithmic stablecoins**: UST, USTC (TerraUSD)

Stablecoins are excluded because they don't track the crypto market - they're pegged to fiat currencies. This includes algorithmic stablecoins like TerraUSD (UST/USTC) that were designed to maintain a USD peg, even though USTC depegged in May 2022. Note: LUNA/LUNC are NOT excluded - they were the mechanism tokens, not stablecoins themselves.

### NOT Excluded from TOTAL2: Recent Coins

**Important:** Recent coins (those without data before `MIN_DATA_DATE`) are **included** in TOTAL2 calculation. The `MIN_DATA_DATE` filter only applies to individual coin halving cycle analysis, not to TOTAL2. This ensures TOTAL2 accurately represents the full cryptocurrency market on each day.

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
- `site/charts/total2_charts.html` - TOTAL2 across 3 halving cycles (2016, 2020, 2024)
- `site/charts/total2_composition.html` - Interactive date picker to view TOTAL2 composition
- `site/total2_statistics.html` - Coin statistics and outlier corrections

## Related Configuration

From `src/config.py`:

```python
# TOTAL2 calculation
TOP_N_FOR_TOTAL2 = 30              # Number of coins in index
VOLUME_SMA_WINDOW = 120            # Days for volume SMA smoothing (~4 months)

# TOTAL2 entry warmup (price capping for coins entering TOTAL2)
TOTAL2_ENTRY_MAX_INCREASE = 1.7    # Max 1.7x (70% gain) per day
TOTAL2_ENTRY_MAX_DECREASE = 0.5    # Min 0.5x (50% loss) per day
TOTAL2_ENTRY_WARMUP_DAYS = 21      # Apply capping for first 21 days after entry

# Quote currencies
QUOTE_CURRENCIES = ["BTC", "USD"]
DEFAULT_QUOTE_CURRENCY = "BTC"

# Output paths
TOTAL2_INDEX_FILE = PROCESSED_DIR / "total2_index.parquet"
TOTAL2_COMPOSITION_FILE = PROCESSED_DIR / "total2_daily_composition.parquet"
```

---

*See also: [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) for full project specification*

---

**[← Back to README](../README.md)**

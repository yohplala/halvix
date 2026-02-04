# Cycle Pattern Analysis

This document explains the cycle pattern analysis feature in Halvix, which identifies min/max points within Bitcoin halving cycles and projects price targets for the next cycle.

## Overview

The pattern analysis identifies characteristic points within each halving cycle and uses three methods to project price targets:

1. **Log-Linear Trendline Regression** - Fits regression lines through cycle peaks and troughs
2. **Fibonacci Extension (127.2%)** - Projects targets based on previous cycle moves
3. **Diminishing Returns Model** - Accounts for decreasing cycle-over-cycle gains

A **composite score** (equal-weight average) ranks altcoins by expected return.

**IMPORTANT**: Returns are calculated as percentage gain from the **current price** to the projected target.

## Coin Selection

The pattern analyzer selects coins that have been in TOTAL2 at any point within the **past 2 years**. This expanded selection:

- Allows analysis of coins even if they temporarily dropped out of the TOTAL2 top 30
- Includes coins that have historical TOTAL2 presence (validated by volume)
- Provides more comprehensive market coverage

**Important**: Only coins that were in TOTAL2 within the past 2 years are analyzed. Coins that have never been in TOTAL2 or were last in TOTAL2 more than 2 years ago are excluded.

## Data Approach: Full Price History with TOTAL2 Filtering

For each selected coin, the pattern analyzer uses **full price history** (not just dates when in TOTAL2). This ensures:

- Accurate detection of true cycle min/max points, even when a coin temporarily drops out of TOTAL2
- Better identification of extreme prices that may occur outside the TOTAL2 index period
- More complete cycle pattern analysis

**TOTAL2-Style Filtering Tools Applied:**

To ensure data quality consistent with TOTAL2 calculation, the following filters are applied to the full price data:

| Filter | Description | Parameters |
|--------|-------------|------------|
| **Volume Outlier Detection** | Detects and corrects impossible volume spikes | 20x rolling median, min 5000 BTC, 7-day window |
| **Volume SMA Smoothing** | Applies simple moving average to volume data | 120-day window with zero padding |

These are the same filtering tools used by the TOTAL2 calculation, ensuring consistent data quality across all analysis modules. The filters are implemented as common helpers in `src/data/price_filters.py`.

**Note**: Unlike the TOTAL2 calculation which filters prices to only TOTAL2 dates, the pattern analyzer intentionally uses full price history to capture true extremes that may occur when a coin is outside the index.

## Cycle Points

For each completed halving cycle, the analyzer identifies **4 characteristic points**:

| Point | Window | Description |
|-------|--------|-------------|
| **min1** | [halving - 550 days, halving] | Lowest price in pre-halving window |
| **max1** | [min1 date, halving] | Highest price between min1 and halving |
| **min2** | [halving, max2 date] | Lowest price between halving and max2 |
| **max2** | [halving, halving + 950 days] | Highest price in post-halving window |

### Cycle 5 Point (Current Cycle)

For cycle 5 (the current cycle), the analyzer adds:

| Point | Window | Description |
|-------|--------|-------------|
| **min1** | [October 6, 2025, current date] | Lowest price since the cycle 4 BTC peak |

This uses the last BTC peak (October 2025) as the starting point, not the halving date. This represents the bottom after the cycle 4 peak, which is the typical cycle pattern.

This gives:
- **4 points per completed cycle** (cycles 2, 3, 4)
- **1 point for current cycle** (cycle 5)
- Up to **13 points for coins** present since cycle 2 (2016 halving)

## Analysis Methods

### 1. Log-Linear Trendline Regression

Fits separate linear regression lines (on log-transformed prices) through:
- **Upper trendline**: Through max1 and max2 points across cycles
- **Lower trendline**: Through min1 and min2 points across cycles

**Requirements**:
- At least 2 peaks and 2 troughs
- Minimum 180-day span between earliest and latest points
- No zero or negative prices

The pattern is classified based on slope relationships:
- **Falling Wedge**: Upper slope < lower slope (diminishing returns pattern)
- **Rising Wedge**: Upper slope > lower slope (accelerating returns)
- **Channel**: Slopes approximately parallel

Target is projected by extending the upper trendline to the expected cycle 5 peak date (~September 2029 = 2028 halving + 550 days).

### 2. Fibonacci Extension (127.2%)

Uses the standard Fibonacci extension formula:

```
Target = C + (B - A) * 1.272
```

Where:
- **A** = Previous cycle minimum (min1)
- **B** = Previous cycle maximum (max2)
- **C** = Current cycle minimum (min1, including cycle 5 min1)

This projects where price might reach if it extends 127.2% of the previous cycle's move from the current cycle's low.

### 3. Diminishing Returns Model

Calculates how much the gain ratio diminishes from cycle to cycle.

**Step 1: Calculate cycle gain ratio (cycle_n_gain)**

For each cycle, the gain ratio is calculated as:

```
cycle_n_gain = max_price / min_price
```

Where:
- `max_price` = Highest price among all max points (max1, max2) in cycle n
- `min_price` = Lowest price among all min points (min1, min2) in cycle n

**Example:**
- Cycle 3 min1 = 0.001 BTC, max2 = 0.015 BTC
- cycle_3_gain = 0.015 / 0.001 = 15x

**Step 2: Calculate diminishing factor**

The diminishing factor measures how much gains decrease between cycles:

```
diminishing_factor = cycle_n_gain / cycle_(n-1)_gain
```

If multiple cycles are available, the average of all diminishing factors is used.

**Example:**
- Cycle 2 gain: 20x
- Cycle 3 gain: 15x
- Cycle 4 gain: 8x
- Diminishing factors: 15/20 = 0.75, 8/15 = 0.53
- Average diminishing factor: (0.75 + 0.53) / 2 = 0.64

**Step 3: Project next cycle target**

```
next_cycle_gain = last_cycle_gain * diminishing_factor
target = latest_min_price * next_cycle_gain
```

**Example:**
- Last cycle gain: 8x
- Diminishing factor: 0.64
- Next cycle gain: 8 × 0.64 = 5.12x
- Latest min price: 0.0005 BTC
- **Target: 0.0005 × 5.12 = 0.00256 BTC**

**Single Cycle Fallback:**
For coins with only 1 cycle of data, a conservative **50% diminishing factor** is assumed:
```
next_cycle_gain = single_cycle_gain * 0.5
```

## Return Calculation

All returns are calculated as:

```
return_pct = (target_price / current_price - 1) * 100
```

Where `current_price` is the last available price in the TOTAL2-filtered data for that coin.

## Confidence Levels

Coins are assigned confidence levels based on data availability:

| Level | Cycles | Description |
|-------|--------|-------------|
| **HIGH** | 3+ | Full historical data (2016+ halving) |
| **MEDIUM** | 2 | Two complete cycles (2020+ halving) |
| **LOW** | 1 | Single cycle only (limited statistical confidence) |

## Usage

### CLI Command

```bash
# Run pattern analysis (default: top 9 altcoins)
poetry run python -m main analyze-patterns

# Specify number of top coins
poetry run python -m main analyze-patterns --top-n 15

# Custom output directory
poetry run python -m main analyze-patterns --output-dir ./output
```

### Output Files

| File | Description |
|------|-------------|
| `site/pattern_analysis.html` | Main page with ranking table |
| `site/charts/pattern_btc.html` | BTC/USD pattern chart |
| `site/charts/pattern_{coin}.html` | Individual altcoin charts |
| `data/processed/pattern_targets.json` | JSON with all computed targets |

### JSON Output Fields

The `pattern_targets.json` includes for each coin:

```json
{
  "points": [...],
  "num_cycles": 2,
  "confidence": "medium",
  "first_in_total2": "2020-06-15",
  "last_in_total2": "2025-05-22",
  "days_in_total2": 1803,
  "current_price": 0.00012345,
  "current_date": "2025-05-22",
  "trendline_target_pct": 245.5,
  "fib_target_pct": 180.3,
  "dim_return_target_pct": -15.2,
  "composite_target_pct": 136.9
}
```

## Data Requirements

Pattern analysis requires:

1. **BTC-USD price data** - For Bitcoin pattern analysis
2. **Altcoin-BTC price data** - For altcoin vs BTC analysis
3. **TOTAL2 composition** - For filtering to coins that have been in TOTAL2

Run these commands first if data is missing:

```bash
poetry run python -m main list-coins
poetry run python -m main fetch-prices
poetry run python -m main calculate-total2
```

## Interpretation

### Reading the Charts

Each chart shows:
- **Light grey line**: Full price history (background context)
- **Colored solid lines**: Cycle segments connecting min/max points
- **Colored markers**: Individual min/max points (color-coded by type)
- **Star markers**: Projected targets for cycle 5

### Point Colors

| Color | Point Type |
|-------|------------|
| Red | min1 (pre-halving minimum) |
| Orange | max1 (pre-halving maximum) |
| Purple | min2 (post-halving minimum) |
| Green | max2 (post-halving maximum) |

### Target Colors

| Color | Method |
|-------|--------|
| Blue | Trendline projection |
| Orange | Fibonacci 127.2% extension |
| Purple | Diminishing returns |

### Limitations

- **Single-cycle coins** have low statistical confidence
- **Projections are not financial advice** - they represent mathematical extrapolations
- **Market conditions change** - historical patterns may not repeat
- **Alt/BTC ratios** can diverge significantly from projections during market regime changes
- **Coins must have been in TOTAL2** - coins that were never in TOTAL2, or were last in TOTAL2 more than 2 years ago, are not analyzed
- **Full price history** - uses complete price data, not just TOTAL2 dates, which may include volatile periods

## Algorithm Details

### Coin Selection

The analyzer selects coins based on TOTAL2 membership within a 2-year lookback window:

1. Load TOTAL2 composition data
2. Filter to coins with at least one appearance in the past 2 years
3. Use this set as the candidate pool for analysis

### Price Data Filtering

For each selected coin, full price history is loaded and filtered using TOTAL2-style tools:

1. **Volume Outlier Detection**: Iteratively detects and corrects volume spikes > 20x rolling median
2. **Volume SMA Smoothing**: Applies 120-day SMA with zero padding for consistent weighting

These filters are implemented in `src/data/price_filters.py` and shared between TOTAL2 calculation and pattern analysis.

### Min/Max Detection

The analyzer uses absolute min/max within windows rather than local extrema detection, as cycle extremes typically represent global min/max values rather than intermediate swings.

### Reference Date

All trendline calculations use the 2016 halving (cycle 2) as the reference date for x-axis values. This provides a consistent baseline across all halvings.

### Projected Cycle 5

- **5th Halving Date**: March 15, 2028 (projected)
- **Target Peak Date**: ~550 days after halving (September 2029)

These projections assume cycle timing remains consistent with historical patterns.

## Halving Cycle Windows

| Cycle | Halving Date | Pre-Window Start | Post-Window End |
|-------|--------------|------------------|-----------------|
| 2 | July 9, 2016 | Dec 2, 2014 | Feb 14, 2019 |
| 3 | May 11, 2020 | Nov 8, 2018 | Dec 17, 2022 |
| 4 | April 19, 2024 | Oct 16, 2022 | Nov 25, 2026 |
| 5 | March 15, 2028 (proj.) | Sept 12, 2026 | Dec 21, 2030 |

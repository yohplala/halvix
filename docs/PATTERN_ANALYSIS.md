# Cycle Pattern Analysis

This document explains the cycle pattern analysis feature in Halvix, which identifies min/max points within Bitcoin halving cycles and projects price targets for the next cycle.

## Overview

The pattern analysis identifies characteristic points within each halving cycle and uses three methods to project price targets:

1. **Log-Linear Trendline Regression** - Fits regression lines through cycle peaks and troughs
2. **Fibonacci Extension (127.2%)** - Projects targets based on previous cycle moves
3. **Diminishing Returns Model** - Accounts for decreasing cycle-over-cycle gains

A **composite score** (equal-weight average) ranks altcoins by expected return.

**IMPORTANT**: Returns are calculated as percentage gain from the **current price** to the projected target.

## Data Source: TOTAL2 Consistency

For altcoins, the pattern analyzer **only uses price data from dates when the coin was actually in TOTAL2**. This ensures:

- Consistency with TOTAL2 calculation methodology
- Only "validated" price data is used (coins that passed TOTAL2 entry criteria)
- No unverified early listing data that might contain outliers
- Alignment with the 21-day freeze period and other TOTAL2 filters

This means a coin like 1000SATS (which entered TOTAL2 on March 25, 2024) will only have analysis based on price data from that date forward, not from its raw listing date.

## Cycle Points

For each completed halving cycle, the analyzer identifies **4 characteristic points**:

| Point | Window | Description |
|-------|--------|-------------|
| **min1** | [halving - 550 days, halving] | Lowest price in pre-halving window |
| **max1** | [min1 date, halving] | Highest price between min1 and halving |
| **min2** | [halving, max2 date] | Lowest price between halving and max2 |
| **max2** | [halving, halving + 950 days] | Highest price in post-halving window |

### Cycle 5 Point (Current Cycle)

For cycle 5 (starting April 19, 2024, which is ongoing), the analyzer adds:

| Point | Window | Description |
|-------|--------|-------------|
| **min1** | [April 19, 2024, current date] | Lowest price since the 4th halving |

If no data is available after the halving (coin exited TOTAL2), the last available price before halving is used.

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

Calculates how much the gain ratio (max/min within cycle) diminishes from cycle to cycle:

```python
diminishing_factor = cycle_n_gain / cycle_n-1_gain
next_cycle_gain = current_cycle_gain * diminishing_factor
target = latest_min * next_cycle_gain
```

For coins with only 1 cycle of data, a conservative 50% diminishing factor is assumed.

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
- **Coins must be in TOTAL2** - coins that never entered TOTAL2 are not analyzed

## Algorithm Details

### TOTAL2 Date Filtering

The analyzer loads the TOTAL2 composition file and creates a set of dates for each coin when it was in the index. Only price data from these dates is used for analysis.

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

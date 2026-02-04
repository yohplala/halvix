# Cycle Pattern Analysis

This document explains the cycle pattern analysis feature in Halvix, which identifies min/max points within Bitcoin halving cycles and projects price targets for the next cycle.

## Overview

The pattern analysis identifies characteristic points within each halving cycle and uses three methods to project price targets:

1. **Log-Linear Trendline Regression** - Fits regression lines through cycle peaks and troughs
2. **Fibonacci Extension (127.2%)** - Projects targets based on previous cycle moves
3. **Diminishing Returns Model** - Accounts for decreasing cycle-over-cycle gains

A **composite score** (equal-weight average) ranks altcoins by expected return.

## Cycle Points

For each halving cycle, the analyzer identifies **4 characteristic points**:

| Point | Window | Description |
|-------|--------|-------------|
| **min1** | [halving - 550 days, halving] | Lowest price in pre-halving window |
| **max1** | [min1 date, halving] | Highest price between min1 and halving |
| **min2** | [halving, max2 date] | Lowest price between halving and max2 |
| **max2** | [halving, halving + 950 days] | Highest price in post-halving window |

This gives up to **8 points for 2 cycles** (e.g., coins present since 2020 halving) or **12 points for 3 cycles**.

## Analysis Methods

### 1. Log-Linear Trendline Regression

Fits separate linear regression lines (on log-transformed prices) through:
- **Upper trendline**: Through max1 and max2 points across cycles
- **Lower trendline**: Through min1 and min2 points across cycles

The pattern is classified based on slope relationships:
- **Falling Wedge**: Upper slope < lower slope (diminishing returns pattern)
- **Rising Wedge**: Upper slope > lower slope (accelerating returns)
- **Channel**: Slopes approximately parallel

Target is projected by extending the upper trendline to the expected cycle 5 peak date (2028 halving + 550 days).

### 2. Fibonacci Extension (127.2%)

Uses the standard Fibonacci extension formula:

```
Target = C + (B - A) * 1.272
```

Where:
- **A** = Previous cycle minimum (min1)
- **B** = Previous cycle maximum (max2)
- **C** = Current cycle minimum (min1)

This projects where price might reach if it extends 127.2% of the previous cycle's move from the current cycle's low.

### 3. Diminishing Returns Model

Calculates how much the gain ratio (max/min within cycle) diminishes from cycle to cycle:

```python
diminishing_factor = cycle_n_gain / cycle_n-1_gain
next_cycle_gain = current_cycle_gain * diminishing_factor
target = latest_min * next_cycle_gain
```

For coins with only 1 cycle of data, a conservative 50% diminishing factor is assumed.

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

## Algorithm Details

### Min/Max Detection

The analyzer uses absolute min/max within windows rather than local extrema detection, as cycle extremes typically represent global min/max values rather than intermediate swings.

### Reference Date

All calculations use the 2016 halving (cycle 2) as the reference date for x-axis values when fitting trendlines. This provides a consistent baseline across all halvings.

### Projected Cycle 5

- **5th Halving Date**: March 15, 2028 (projected)
- **Target Peak Date**: ~550 days after halving (September 2029)

These projections assume cycle timing remains consistent with historical patterns.

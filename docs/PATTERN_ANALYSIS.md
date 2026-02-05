# Cycle Pattern Analysis

**[← Back to README](../README.md)**

---

This document explains the cycle pattern analysis feature in Halvix, which identifies min/max points within Bitcoin halving cycles and projects price targets for the next cycle.

## Overview

The pattern analysis identifies characteristic points within each halving cycle and uses three methods to project price targets:

1. **Log-Linear Trendline Regression** - Fits regression lines through cycle peaks and troughs
2. **Fibonacci Extension (127.2%)** - Projects targets based on previous cycle moves
3. **Diminishing Returns Model** - Accounts for decreasing cycle-over-cycle gains

A **composite score** (equal-weight average) ranks altcoins by expected return.

**IMPORTANT**: Returns are calculated as percentage gain from the **current price** to the projected target.

## Coin Selection

The pattern analyzer selects coins that have been in TOTAL2 at any point within the **past 3 years**. This expanded selection:

- Allows analysis of coins even if they temporarily dropped out of the TOTAL2 top 30
- Includes coins that have historical TOTAL2 presence (validated by volume)
- Provides more comprehensive market coverage

**Important**: Only coins that were in TOTAL2 within the past 3 years are analyzed. Coins that have never been in TOTAL2 or were last in TOTAL2 more than 3 years ago are excluded.

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
| **max2** | [halving, min(halving + 950, next_pre_start)] | Highest price in post-halving window* |

*The max2 window is capped at the start of the next cycle's pre-halving window to prevent overlap between cycles.

### Cycle 5 (Current Cycle)

For cycle 5, the analyzer detects min1 differently since the cycle is ongoing:

| Attribute | Value | Notes |
|-----------|-------|-------|
| **Price** | Lowest since cycle 4 BTC peak | Actual minimum from `BTC_CYCLE_PEAKS[-1]` to current date |
| **Date (display)** | Actual date of minimum | Used in charts and output |
| **Date (regression)** | 5th halving − 520 days | ~Sept 27, 2026; used only for trendline x-coordinate |

The approximated regression date:
- Places min1 within the typical window `[halving-550, halving]`
- Provides a stable x-coordinate since the true bottom may not have occurred yet
- Only affects trendline regression; Fibonacci and Diminishing Returns use the actual price only

This gives:
- **4 points per completed cycle** (cycles 2, 3, 4)
- **1 point for current cycle** (cycle 5)
- Up to **13 points for coins** present since cycle 2 (2016 halving)

## Analysis Methods

### 1. Log-Linear Trendline Regression

Fits separate linear regression lines (on log-transformed prices) through:
- **Upper trendline**: Through max1 and max2 points across cycles
- **Lower trendline**: Through min1 and min2 points across cycles

> **Note**: Cycle 5 min1 uses an approximated date for regression (see [Cycle 5](#cycle-5-current-cycle) above).

**Weighted Regression**:

The regression uses weighted least squares to prioritize "major" cycle extremes over "minor" intermediate points:

| Point Type | Classification | Weight | Rationale |
|------------|---------------|--------|-----------|
| **min1** | Major | 67% | True cycle bottom (pre-halving low) |
| **max1** | Minor | 33% | Intermediate high before halving |
| **min2** | Minor | 33% | Intermediate dip after halving |
| **max2** | Major | 67% | True cycle peak (post-halving high) |

This weighting ensures the trendlines fit more closely to the definitive cycle extremes (min1 and max2) rather than the intermediate points (max1 and min2) which are less representative of long-term trends.

**Note**: With only 2 points per category, weights have no effect since a line through 2 points is uniquely determined. Weights only affect the regression when 3 or more points are available.

**Requirements (Major Extrema Approach)**:

To calculate a trendline, the analyzer requires at least **2 major extrema of the same type** (either min1 or max2):

| Condition | Upper Trendline | Lower Trendline | Result |
|-----------|-----------------|-----------------|--------|
| 2+ min1 AND 2+ max2 | Fit through max points | Fit through min points | Independent slopes |
| 2+ min1 only | Use trough slope | Fit through min1 points | Parallel channel |
| 2+ max2 only | Fit through max2 points | Use peak slope | Parallel channel |
| <2 of both | None | None | No trendline |

**Parallel Channel Assumption**: When only one side (peaks or troughs) has enough major extrema, the slope from that side is used for both trendlines. The intercept for the other side is calculated to pass through the available major point(s).

**Additional Requirements**:
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

## Ranking and Filtering

### Ranking Criterion

Coins are **ranked by composite target percentage** (descending). The composite score is an equal-weight average of all available projection methods (trendline, Fibonacci, diminishing returns), providing a balanced view of expected returns.

### Filtering Rules

Coins are filtered to exclude assets expected to underperform BTC:

**1. Trendline Prediction Filter:**

| Trendline Value | Included? | Reason |
|-----------------|-----------|--------|
| **Positive** | Yes | Expected to outperform BTC |
| **None/Missing** | Yes | Insufficient data for trendline, but other methods may apply |
| **Negative** | No | Expected to underperform BTC |

**2. Floor Appreciation Filter:**

Coins with declining floors (min points getting lower over cycles) are excluded. The lower trendline slope must indicate at least **10% annual floor appreciation** (`MIN_LOWER_SLOPE_ANNUAL_PCT` in config).

| Floor Trend | Included? | Example |
|-------------|-----------|---------|
| **Appreciating (≥10%/year)** | Yes | Healthy floor growth |
| **Stagnant (<10%/year)** | No | Floor not keeping pace |
| **Declining (negative)** | No | Bottoms getting lower (e.g., CTXC) |

This filter catches coins like CTXC where the upper trendline may show gains but the floor is eroding - a sign of structural weakness.

**Additional Requirements:**
- Coins must have a valid **composite score** (at least one projection method must succeed)

### Rank Display

- **BTC** is always shown with rank **0** (baseline asset)
- **Altcoins** are ranked **1, 2, 3...** based on their composite target
- Each chart title includes the rank prefix (e.g., "#1 - ETH/BTC - Cycle Pattern Analysis")
- The ranking table shows the rank in the first column

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

- **Very new coins** (e.g., HYPE) may have insufficient cycle data for any projections - they need at least one complete cycle with min+max points
- **Single-cycle coins** have low statistical confidence
- **Projections are not financial advice** - they represent mathematical extrapolations
- **Market conditions change** - historical patterns may not repeat
- **Alt/BTC ratios** can diverge significantly from projections during market regime changes
- **Coins must have been in TOTAL2** within the past 3 years to be analyzed
- **Full price history** - uses complete price data, not just TOTAL2 dates, which may include volatile periods

## Algorithm Details

### Min/Max Detection

The analyzer uses absolute min/max within windows rather than local extrema detection, as cycle extremes typically represent global min/max values rather than intermediate swings.

### Reference Date

All trendline calculations use the 2016 halving (cycle 2) as the reference date for x-axis values. This provides a consistent baseline across all halvings.

### Config Constants

Key parameters in [`src/config.py`](../src/config.py):

| Constant | Value | Used For |
|----------|-------|----------|
| `TRENDLINE_MAJOR_POINT_WEIGHT` | 0.67 | Weight for min1, max2 in regression |
| `TRENDLINE_MINOR_POINT_WEIGHT` | 0.33 | Weight for max1, min2 in regression |
| `CYCLE5_MIN1_APPROX_DAYS_BEFORE_HALVING` | 520 | Approximated min1 date for trendline |
| `MIN_LOWER_SLOPE_ANNUAL_PCT` | 10 | Minimum annual floor appreciation (%) |
| `DEFAULT_FIBONACCI_LEVEL` | 1.272 | Fibonacci extension level |
| `DEFAULT_DIMINISHING_FACTOR` | 0.20 | Fallback for single-cycle coins |

## Halving Cycle Windows

> **Note**: Halving dates are defined in [`src/config.py`](../src/config.py) (`HALVING_DATES` and `PROJECTED_5TH_HALVING`). Window calculations use `DAYS_BEFORE_HALVING` (550) and `DAYS_AFTER_HALVING` (950).

| Cycle | Halving Date | Pre-Window Start | Post-Window End |
|-------|--------------|------------------|-----------------|
| 2 | July 9, 2016 | Dec 2, 2014 | Feb 14, 2019 |
| 3 | May 11, 2020 | Nov 8, 2018 | Dec 17, 2022 |
| 4 | April 19, 2024 | Oct 16, 2022 | Nov 25, 2026 |
| 5 | March 31, 2028 (proj.) | Sept 28, 2026 | Nov 6, 2030 |

---

*Last updated: 2026-02-04*

---

**[← Back to README](../README.md)**

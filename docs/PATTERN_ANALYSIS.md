# Cycle Pattern Analysis

**[← Back to README](../README.md)**

---

This document explains the cycle pattern analysis feature in Halvix, which identifies min/max points within Bitcoin halving cycles and projects price targets for the next cycle.

## Overview

The pattern analysis identifies characteristic points within each halving cycle and uses four methods to project price targets:

1. **Log-Linear Trendline Regression** - Fits regression lines through cycle peaks and troughs
2. **Fibonacci Extension (100%)** - Projects targets based on previous cycle moves
3. **Diminishing Returns Model** - Accounts for decreasing cycle-over-cycle gains
4. **Historical Peak** - Uses historical cycle peaks as a price reference

A **composite score** (weighted average of available methods) ranks altcoins by expected return. Each confidence level has its own **weight profile** that determines method weights and a scale factor (see [Confidence-Based Weight Profiles](#confidence-based-weight-profiles)).

**IMPORTANT**: Returns are calculated as percentage gain from the **current price** to the projected target:

```
return_pct = (target_price / current_price - 1) * 100
```

Where `current_price` is the last available price in the full price history for that coin.

## Coin Selection

The pattern analyzer selects coins that have been in TOTAL2 at any point within the **past 3 years**. This expanded selection:

- Allows analysis of coins even if they temporarily dropped out of the TOTAL2 top 30
- Includes coins that have historical TOTAL2 presence (validated by volume)
- Provides more comprehensive market coverage

**Important**: Only coins that were in TOTAL2 within the past 3 years are analyzed. Coins that have never been in TOTAL2 or were last in TOTAL2 more than 3 years ago are excluded.

## Data Approach: Full Price History

For each selected coin, the pattern analyzer uses **full price history** (not just dates when in TOTAL2). This ensures:

- Accurate detection of true cycle min/max points, even when a coin temporarily drops out of TOTAL2
- Better identification of extreme prices that may occur outside the TOTAL2 index period
- More complete cycle pattern analysis

**Symbol Replacement Detection**: CryptoCompare sometimes reuses ticker symbols for different tokens (e.g., old "MOVE" token replaced by Movement Labs "MOVE"). The analyzer detects these replacements and uses only post-replacement data. See `detect_symbol_replacement` in `src/data/price_filters.py`.

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

**Partial Cycles**: Coins launched mid-cycle (without pre-halving data) will have partial cycle points. For example, a coin launched in June 2022 would have cycle 3 post-halving points (min2, max2) but no pre-halving points (min1, max1). This allows analysis of newer coins while still capturing their cycle extremes.

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

**Recency Decay**:

In addition to point-type weights, a **recency decay factor** (`TRENDLINE_RECENCY_DECAY = 0.7`) is applied so that more recent cycles have greater influence on the trendline. The final weight for each point is:

```
final_weight = type_weight × recency_decay ^ (max_cycle - point_cycle)
```

| Cycle Age | Recency Multiplier | Effect |
|-----------|-------------------|--------|
| Most recent | 1.0 | Full weight |
| One cycle back | 0.7 | 70% of type weight |
| Two cycles back | 0.49 | 49% of type weight |

This prevents early high-growth cycles from making projections overly optimistic, especially for BTC where cycle-over-cycle returns are diminishing.

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

### 2. Fibonacci Extension (100%)

Uses Fibonacci extension in **log-space** to respect the multiplicative nature of price movements:

```
Target = 10^(log10(C) + (log10(B) - log10(A)) * 1.272)
```

Where:
- **A** = Previous cycle minimum (prefer min1, fallback to min2)
- **B** = Previous cycle maximum (max2 only - true cycle peak)
- **C** = Current cycle minimum (min1 only - true cycle start)

Using log-space ensures proportional consistency: a 10x move from $1→$10 projects the same proportional extension as $100→$1000. This is more appropriate for crypto assets where price movements are multiplicative rather than additive.

**Fallback Logic**: Only the previous cycle minimum has a fallback (min1 → min2). This allows coins with partial pre-halving data to get Fib projections while maintaining chronological order (min → max → min). No fallback is allowed for B (max) or C (current min) to preserve the correct sequence of extrema.

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
next_cycle_gain = max(last_cycle_gain * diminishing_factor, DIM_RETURN_MIN_GAIN_RATIO)
target = latest_min_price * next_cycle_gain
```

**Gain Floor**: The projected gain is clamped to at least `DIM_RETURN_MIN_GAIN_RATIO` (0.1x = minimum 10% gain, i.e., 1.1x from trough). This allows the model to express pessimism when diminishing factors point to very low cycle gains, while preventing absurdly small projections. Without any floor, coins with enormous first-cycle gains (e.g., SOL launching from near-zero) produce tiny diminishing factors that project negligible returns.

**Example:**
- Last cycle gain: 8x
- Diminishing factor: 0.64
- Next cycle gain: 8 × 0.64 = 5.12x
- Latest min price: 0.0005 BTC
- **Target: 0.0005 × 5.12 = 0.00256 BTC**

**Single Cycle:**
For coins with only 1 cycle of data, a conservative **20% diminishing factor** (`DEFAULT_DIMINISHING_FACTOR`) is applied via the diminishing returns model. The Fibonacci extension method returns `None` for single-cycle coins (insufficient data: requires a prior cycle's move to project from the current low).

### 4. Historical Peak

Uses historical cycle peaks to establish a price target reference.

**Logic:**

1. If the **previous cycle's max2** is the **absolute maximum** across all historical cycles → use that value as the target
2. Otherwise → calculate a **weighted average** of all historical peaks

**Weighted Average Formula:**

When the previous cycle max2 is NOT the absolute maximum:

```
target = (sum(max2_prices) × 0.67 + sum(max1_prices) × 0.33) /
         (count(max2) × 0.67 + count(max1) × 0.33)
```

The weighting uses the same scheme as trendline regression:
- **max2 points** (true cycle peaks): 67% weight
- **max1 points** (pre-halving highs): 33% weight

**Example:**

For a coin with 3 cycles of data:
- Cycle 2: max1 = 0.008 BTC, max2 = 0.012 BTC
- Cycle 3: max1 = 0.006 BTC, max2 = 0.015 BTC (previous cycle)
- Cycle 4: max1 = 0.004 BTC, max2 = 0.010 BTC

Since max2 of cycle 3 (0.015) is the absolute maximum:
- **Target = 0.015 BTC** (previous cycle max2 used directly)

If cycle 4 max2 were 0.020 BTC instead (making it the absolute max):
- Weighted sum = (0.012 + 0.015 + 0.020) × 0.67 + (0.008 + 0.006 + 0.004) × 0.33
- Weighted sum = 0.047 × 0.67 + 0.018 × 0.33 = 0.03149 + 0.00594 = 0.03743
- Weight total = 3 × 0.67 + 3 × 0.33 = 2.01 + 0.99 = 3.0
- **Target = 0.03743 / 3.0 = 0.01248 BTC**

**Rationale:**

The historical peak method provides an anchor based on actual achieved prices:
- If the previous cycle set an all-time high, that peak represents proven market valuation
- If not, the weighted average of peaks gives a balanced view of historical highs
- This complements the projection-based methods (trendline, Fibonacci, diminishing returns)

## Confidence Levels

Coins are assigned confidence levels based on the number of cycles where they have **pre-halving data** (min1 point). A cycle only counts if the coin existed before that halving.

| Level | Cycles with min1 | Description |
|-------|------------------|-------------|
| **HIGH** | 3+ | Full historical data (2016+ halving) |
| **MEDIUM** | 2 | Two complete cycles (2020+ halving) |
| **LOW** | 1 | Single cycle only (limited statistical confidence) |

**Note**: Coins launched after a halving (with only post-halving data like min2/max2) do not count that cycle toward confidence. For example, a coin launched in June 2024 (after the 4th halving) only has cycle 5 min1, resulting in LOW confidence.

### Confidence-Based Weight Profiles

Instead of separate code paths for different confidence levels, a **single weight profile** per confidence level controls both method weights and the overall scale factor. This is defined in `COMPOSITE_WEIGHT_PROFILES` in `config.py`.

| Confidence | Trendline | Fibonacci | Historical | Diminishing | Scale | Notes |
|------------|-----------|-----------|------------|-------------|-------|-------|
| **HIGH** (3+ cycles) | 40% | 25% | 20% | 15% | 1.0 | Full weights, no penalty |
| **MEDIUM** (2 cycles) | 40% | 25% | 20% | 15% | **0.9** | 10% penalty for limited data |
| **LOW** (1 cycle) | **0.2%** | **2%** | 20% | **2%** | **0.1** | Historical peak dominates, 90% penalty |

**Method weight rationale (high/medium):**
- **Trendline (40%)**: Captures structural multi-cycle trend direction
- **Fibonacci (25%)**: Technical projection based on previous cycle move
- **Historical Peak (20%)**: Reality anchor based on achieved valuations
- **Diminishing Returns (15%)**: Most volatile; sensitive to outlier launch cycles

**Low confidence rationale:**
- **Trendline weight ~0**: A 2-point trendline (one min1 + one max point) is statistically unreliable; small variations lead to wildly different extrapolations.
- **Fibonacci weight ~0**: Log-space Fibonacci with a `min2` fallback for point A can produce extreme projections when the previous cycle's range is very large.
- **Diminishing weight ~0**: With only one cycle of gain data, the diminishing returns factor is unreliable.
- **Historical Peak (20%)**: The only method that doesn't extrapolate — it uses actually achieved prices, making it the most trustworthy signal for single-cycle coins.
- **Scale = 0.1**: A 90% penalty reflects the very high uncertainty of projections based on a single cycle.

**Result**: Low-confidence composite ≈ historical_peak × 0.1

When a method is unavailable (returns None), its weight is excluded and the remaining weights are renormalized before applying the scale factor.

## Ranking and Filtering

### Ranking Criterion

Coins are **ranked by composite target percentage** (descending). The composite score is computed using the confidence-based weight profile (see [Confidence-Based Weight Profiles](#confidence-based-weight-profiles)).

### Filtering Rules

Coins are filtered to exclude assets expected to underperform BTC or with insufficient data quality:

**1. Trendline Prediction Filter:**

| Trendline Value | Included? | Reason |
|-----------------|-----------|--------|
| **Positive** | Yes | Expected to outperform BTC |
| **None/Missing** | Yes | Insufficient data for trendline, but other methods may apply |
| **Negative** | No | Expected to underperform BTC |

**2. Floor Appreciation Filter:**

Coins with declining floors (min points getting lower over cycles) are excluded. The lower trendline slope must indicate at least **8% annual floor appreciation** (`MIN_LOWER_SLOPE_ANNUAL_PCT` in config).

| Floor Trend | Included? | Example |
|-------------|-----------|---------|
| **Appreciating (≥8%/year)** | Yes | Healthy floor growth |
| **Stagnant (<8%/year)** | No | Floor not keeping pace |
| **Declining (negative)** | No | Bottoms getting lower (e.g., CTXC) |

This filter catches coins like CTXC where the upper trendline may show gains but the floor is eroding - a sign of structural weakness.

**3. Fibonacci Retracement Filter:**

Coins that have **retraced too deeply** from their last cycle peak are filtered out. This uses the standard Fibonacci retracement framework with three structural points:

```
A = previous cycle min (min1 preferred, min2 fallback)
B = previous cycle max2 (peak)
C = current cycle min1 (new trough)

log_retracement = log10(B / C) / log10(B / A)
```

Coins with retracement > `MAX_RETRACEMENT_LEVEL` (88.6%) are excluded. Beyond this level, the "higher low" structure is broken — the coin has given back so much of its cycle gain that the pattern is structurally unhealthy. This complements the floor slope filter: both catch declining coins, but the retracement filter works even with a single completed cycle.

| Retracement | Included? | Example |
|-------------|-----------|---------|
| **≤ 88.6%** | Yes | Healthy correction (e.g., VIRTUAL at ~38%) |
| **> 88.6%** | No | Structural breakdown (e.g., COOKIE at ~95%) |
| **None** | Yes | Insufficient data (no previous cycle peak) |

**Continuous Retracement Penalty:**

Coins that pass the 88.6% hard filter but have retraced beyond the golden ratio level (61.8%) receive a **linear penalty** on their composite score. This provides a gradual signal degradation rather than an all-or-nothing cutoff:

```
If retracement > 0.618 and ≤ 0.886:
    t = (retracement - 0.618) / (0.886 - 0.618)
    penalty = 1.0 - t × (1.0 - RETRACEMENT_PENALTY_AT_MAX)
    composite_target_pct *= penalty
```

| Retracement | Penalty | Effect |
|-------------|---------|--------|
| ≤ 61.8% | 1.0 (none) | Healthy — no adjustment |
| 75.2% (midpoint) | 0.75 | 25% reduction |
| 88.6% (max) | 0.5 | 50% reduction (just before exclusion) |

**4. Coin Age Filter:**

Coins must have at least **1 year of price history** (`MIN_COIN_AGE_DAYS` = 365 days). This filters out very new coins (e.g., ZORA) with insufficient data for reliable projections.

| Coin Age | Included? | Reason |
|----------|-----------|--------|
| **≥ 1 year** | Yes | Sufficient price history |
| **< 1 year** | No | Too new for reliable analysis |

**5. Price Liquidity Filter:**

Coins must have at least **30 distinct price values** (`MIN_UNIQUE_PRICES` = 30) over their price history. This filters out illiquid coins with "staircase" patterns (e.g., ZBCN, HTX) where price stays constant for extended periods, indicating very low trading activity.

| Unique Prices | Included? | Example |
|---------------|-----------|---------|
| **≥ 30** | Yes | Normal trading activity |
| **< 30** | No | Staircase pattern, illiquid (e.g., ZBCN with only 4 price levels) |

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
# Run pattern analysis (default: top 14 altcoins)
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
  "hist_peak_target_pct": 120.8,
  "hist_peak_is_absolute": false,
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
- **Dashed grey lines**: Upper and lower trendlines (when available)
- **Colored solid lines**: Cycle segments connecting min/max points
- **Colored markers**: Individual min/max points (color-coded by type)
- **Star markers**: Projected targets for cycle 5
- **Dotted horizontal line**: Historical peak level (for Hist. Peak method)

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
| Orange | Fibonacci 100% extension |
| Purple | Diminishing returns |
| Green | Historical peak |

### Limitations

- **Very new coins** (e.g., HYPE) may have insufficient cycle data for any projections - they need at least one complete cycle with min+max points
- **Single-cycle coins** have low statistical confidence (trendline excluded from composite)
- **Projections are not financial advice** - they represent mathematical extrapolations
- **Market conditions change** - historical patterns may not repeat
- **Alt/BTC ratios** can diverge significantly from projections during market regime changes
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
| `MAJOR_POINT_WEIGHT` | 0.67 | Weight for min1, max2 in regression and historical peak averaging |
| `MINOR_POINT_WEIGHT` | 0.33 | Weight for max1, min2 in regression and historical peak averaging |
| `CURRENT_CYCLE_MIN1_APPROX_DAYS_BEFORE_HALVING` | 520 | Approximated min1 date for trendline |
| `MIN_LOWER_SLOPE_ANNUAL_PCT` | 8 | Minimum annual floor appreciation (%) |
| `MIN_COIN_AGE_DAYS` | 365 | Minimum coin age in days (1 year) |
| `MIN_UNIQUE_PRICES` | 30 | Minimum distinct prices for liquidity |
| `COMPOSITE_WEIGHT_PROFILES` | dict | Weight profiles per confidence level (see above) |
| `MAX_RETRACEMENT_LEVEL` | 0.886 | Fibonacci retracement filter (88.6% = √0.786) |
| `GOLDEN_RETRACEMENT_LEVEL` | 0.618 | Retracement penalty starts at this level |
| `RETRACEMENT_PENALTY_AT_MAX` | 0.5 | Composite multiplier at MAX_RETRACEMENT_LEVEL |
| `DEFAULT_FIBONACCI_LEVEL` | 1.0 | Fibonacci extension level |
| `DIM_RETURN_MIN_GAIN_RATIO` | 0.1 | Minimum projected gain ratio (0.1x = 10% gain from trough) |
| `DEFAULT_DIMINISHING_FACTOR` | 0.20 | Conservative fallback for single-cycle coins (assumes 80% gain reduction vs prior cycle, more pessimistic than observed ~0.65 average) |

## Halving Cycle Windows

> **Note**: Halving dates are defined in [`src/config.py`](../src/config.py) (`HALVING_DATES`, including the projected 5th halving). Window calculations use `DAYS_BEFORE_HALVING` (550) and `DAYS_AFTER_HALVING` (950).

| Cycle | Halving Date | Pre-Window Start | Post-Window End |
|-------|--------------|------------------|-----------------|
| 2 | July 9, 2016 | Dec 2, 2014 | Feb 14, 2019 |
| 3 | May 11, 2020 | Nov 8, 2018 | Dec 17, 2022 |
| 4 | April 19, 2024 | Oct 16, 2022 | Nov 25, 2026 |
| 5 | March 31, 2028 (proj.) | Sept 28, 2026 | Nov 6, 2030 |

---

**[← Back to README](../README.md)**

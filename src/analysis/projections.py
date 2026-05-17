"""
Projection methods for cycle pattern analysis.

The four projection models (log-linear trendline, Fibonacci extension,
diminishing returns, historical peak) plus the composite/retracement
helpers live here as pure module-level functions: they take cycle points
(and pre-built indexes) as explicit arguments and return numeric targets
or ``None``.

``CyclePatternAnalyzer`` exposes thin staticmethod wrappers under the
``_calculate_*`` / ``_classify_*`` / ``_fit_log_trendlines`` /
``_project_trendline_target`` names so the analyzer's own internal calls
and the existing test surface (``analyzer._foo(...)`` /
``CyclePatternAnalyzer._foo(...)``) keep working.
"""

import math
from datetime import date, timedelta

import numpy as np

from analysis.cycle_points import (
    Confidence,
    CyclePoint,
    PointType,
    fib_retracement_ratio,
)
from analysis.point_detection import find_latest_min_point
from config import (
    COMPOSITE_WEIGHT_PROFILES,
    CURRENT_CYCLE_MIN1_APPROX_DAYS_BEFORE_HALVING,
    DEFAULT_DIMINISHING_FACTOR,
    DEFAULT_FIBONACCI_LEVEL,
    DIM_RETURN_MIN_GAIN_RATIO,
    HALVING_DATES,
    MAJOR_POINT_WEIGHT,
    MINOR_POINT_WEIGHT,
    SLOPE_DIFF_CHANNEL_THRESHOLD,
    TRENDLINE_LOG_PRICE_LIMIT,
    TRENDLINE_RECENCY_DECAY,
)
from utils.logging import get_logger

logger = get_logger(__name__)


def get_regression_date(point: CyclePoint) -> date:
    """
    Get the date to use for trendline regression for a given point.

    For actual (non-projected) points, returns the detected date.
    For projected min1, returns the approximated date (520 days before
    the last halving), matching the chart display position. This ensures
    the trendline visually passes through the displayed point.

    Maintainer note:
        The projected-min1 x-coordinate is anchored to ``HALVING_DATES[-1]``
        (currently 2028-03-31, a static config value — not auto-updated by
        any CI workflow). Bumping ``HALVING_DATES[-1]`` to a different
        projected date will shift the regression x-coord for every coin
        with a projected min1, subtly re-positioning every trendline and
        its derived target. If/when block-time projections move the date,
        expect target percentages to drift; consider re-baselining or
        switching to a more stable anchor (e.g. "today + remaining days
        to the next halving") if drift becomes material.

    Args:
        point: The cycle point

    Returns:
        Date to use for regression x-coordinate
    """
    if point.projected and point.point_type == "min1":
        return HALVING_DATES[-1] - timedelta(days=CURRENT_CYCLE_MIN1_APPROX_DAYS_BEFORE_HALVING)
    return point.date


def fit_log_trendlines(
    points: list[CyclePoint],
) -> tuple[float | None, float | None, float | None, float | None]:
    """
    Fit log-linear trendlines through cycle min and max points.

    Uses weighted least squares regression where:
    - Major points (min1, max2) get higher weight (true cycle extremes)
    - Minor points (max1, min2) get lower weight (intermediate points)

    With only 2 points per category, weights have no effect since a line
    through 2 points is uniquely determined. With 3+ points, weights
    affect which points the regression line fits more closely.

    Note: Actual points use their detected dates for regression.
    Projected min1 uses an approximated date (520 days before halving),
    matching its chart display position. This ensures trendlines visually
    pass through displayed points.

    Returns:
        Tuple of (upper_slope, upper_intercept, lower_slope, lower_intercept)
        or (None, None, None, None) if insufficient data
    """
    # Separate peaks and troughs, filtering out zero/negative prices
    # Include projected min1: its price is approximate (23.6% retracement) but
    # provides a useful second trough for coins with limited history (e.g., PIPPIN,
    # HYPE). get_regression_date() already provides a stable x-coordinate for it.
    peaks = [p for p in points if "max" in p.point_type and p.price > 0 and not p.projected]
    troughs = [
        p
        for p in points
        if "min" in p.point_type and p.price > 0 and (not p.projected or p.point_type == "min1")
    ]

    if not peaks or not troughs:
        return None, None, None, None

    # Count major extrema: max2 (true peaks) and min1 (true bottoms)
    major_peaks = [p for p in peaks if p.point_type == "max2"]
    major_troughs = [p for p in troughs if p.point_type == "min1"]

    has_enough_peaks = len(major_peaks) >= 2
    has_enough_troughs = len(major_troughs) >= 2

    # Fallback: check if we have 2+ total points on either side (any min or max type)
    has_enough_total_troughs = len(troughs) >= 2
    has_enough_total_peaks = len(peaks) >= 2

    # Need at least one side with 2+ major extrema, OR 2+ total points as fallback
    if not has_enough_peaks and not has_enough_troughs:
        if not has_enough_total_troughs and not has_enough_total_peaks:
            logger.debug(
                "Insufficient extrema for trendline: %d max2, %d min1, %d total peaks, %d total troughs",
                len(major_peaks),
                len(major_troughs),
                len(peaks),
                len(troughs),
            )
            return None, None, None, None

    # Convert to arrays with days as x-axis (days from first halving date)
    # Use HALVING_DATES[1] (2016) as reference
    # Note: Projected min1 uses approximated date via get_regression_date()
    reference_date = HALVING_DATES[1]

    peak_x = np.array([(get_regression_date(p) - reference_date).days for p in peaks]).reshape(
        -1, 1
    )
    peak_y = np.log10([p.price for p in peaks])

    trough_x = np.array([(get_regression_date(p) - reference_date).days for p in troughs]).reshape(
        -1, 1
    )
    trough_y = np.log10([p.price for p in troughs])

    # Assign weights based on point type AND cycle recency:
    # - max2 (true peak) gets major weight, max1 (intermediate) gets minor weight
    # - min1 (true bottom) gets major weight, min2 (intermediate) gets minor weight
    # - Recent cycles get higher weight via TRENDLINE_RECENCY_DECAY
    #   (e.g., 0.7: most recent=1.0, one back=0.7, two back=0.49)
    max_cycle = max(p.cycle_num for p in peaks + troughs)

    def _recency_weight(cycle_num: int) -> float:
        return TRENDLINE_RECENCY_DECAY ** (max_cycle - cycle_num)

    peak_weights = np.array(
        [
            (MAJOR_POINT_WEIGHT if p.point_type == "max2" else MINOR_POINT_WEIGHT)
            * _recency_weight(p.cycle_num)
            for p in peaks
        ]
    )
    trough_weights = np.array(
        [
            (MAJOR_POINT_WEIGHT if p.point_type == "min1" else MINOR_POINT_WEIGHT)
            * _recency_weight(p.cycle_num)
            for p in troughs
        ]
    )

    try:
        if has_enough_peaks and has_enough_troughs:
            # Both sides have enough data - fit independently
            upper_fit = np.polyfit(peak_x.flatten(), peak_y, 1, w=peak_weights)
            lower_fit = np.polyfit(trough_x.flatten(), trough_y, 1, w=trough_weights)
            return (
                float(upper_fit[0]),
                float(upper_fit[1]),
                float(lower_fit[0]),
                float(lower_fit[1]),
            )

        elif has_enough_troughs:
            # Only troughs have enough major points - fit troughs, use same slope for peaks
            lower_fit = np.polyfit(trough_x.flatten(), trough_y, 1, w=trough_weights)
            slope = lower_fit[0]
            # Calculate intercept for upper line passing through the max2 point (or average if multiple)
            if major_peaks:
                # Use the major peak(s) to set the upper intercept
                major_peak_x = np.mean(
                    [(get_regression_date(p) - reference_date).days for p in major_peaks]
                )
                major_peak_y = np.mean([np.log10(p.price) for p in major_peaks])
            else:
                # Fallback to highest peak
                highest_peak = max(peaks, key=lambda p: p.price)
                major_peak_x = (get_regression_date(highest_peak) - reference_date).days
                major_peak_y = np.log10(highest_peak.price)
            upper_intercept = major_peak_y - slope * major_peak_x
            return (
                float(slope),
                float(upper_intercept),
                float(lower_fit[0]),
                float(lower_fit[1]),
            )

        elif has_enough_peaks:
            # Only peaks have enough major points - fit peaks, use same slope for troughs
            upper_fit = np.polyfit(peak_x.flatten(), peak_y, 1, w=peak_weights)
            slope = upper_fit[0]
            # Calculate intercept for lower line passing through the min1 point (or average if multiple)
            if major_troughs:
                major_trough_x = np.mean(
                    [(get_regression_date(p) - reference_date).days for p in major_troughs]
                )
                major_trough_y = np.mean([np.log10(p.price) for p in major_troughs])
            else:
                # Fallback to lowest trough
                lowest_trough = min(troughs, key=lambda p: p.price)
                major_trough_x = (get_regression_date(lowest_trough) - reference_date).days
                major_trough_y = np.log10(lowest_trough.price)
            lower_intercept = major_trough_y - slope * major_trough_x
            return (
                float(upper_fit[0]),
                float(upper_fit[1]),
                float(slope),
                float(lower_intercept),
            )

        elif has_enough_total_troughs and has_enough_total_peaks:
            # Both sides have 2+ total points (but not 2+ major) - fit independently
            upper_fit = np.polyfit(peak_x.flatten(), peak_y, 1, w=peak_weights)
            lower_fit = np.polyfit(trough_x.flatten(), trough_y, 1, w=trough_weights)
            return (
                float(upper_fit[0]),
                float(upper_fit[1]),
                float(lower_fit[0]),
                float(lower_fit[1]),
            )

        elif has_enough_total_troughs:
            # Fallback: 2+ total troughs only - fit troughs, use same slope for peaks
            lower_fit = np.polyfit(trough_x.flatten(), trough_y, 1, w=trough_weights)
            slope = lower_fit[0]
            # Calculate intercept for upper line passing through the highest peak
            highest_peak = max(peaks, key=lambda p: p.price)
            peak_x_val = (get_regression_date(highest_peak) - reference_date).days
            peak_y_val = np.log10(highest_peak.price)
            upper_intercept = peak_y_val - slope * peak_x_val
            return (
                float(slope),
                float(upper_intercept),
                float(lower_fit[0]),
                float(lower_fit[1]),
            )

        else:
            # Fallback: 2+ total peaks only - fit peaks, use same slope for troughs
            upper_fit = np.polyfit(peak_x.flatten(), peak_y, 1, w=peak_weights)
            slope = upper_fit[0]
            # Calculate intercept for lower line passing through the lowest trough
            lowest_trough = min(troughs, key=lambda p: p.price)
            trough_x_val = (get_regression_date(lowest_trough) - reference_date).days
            trough_y_val = np.log10(lowest_trough.price)
            lower_intercept = trough_y_val - slope * trough_x_val
            return (
                float(upper_fit[0]),
                float(upper_fit[1]),
                float(slope),
                float(lower_intercept),
            )

    except (np.linalg.LinAlgError, ValueError, TypeError) as e:
        logger.debug("Trendline fitting failed: %s", e)
        return None, None, None, None


def project_trendline_target(
    upper_slope: float,
    upper_intercept: float,
    target_date: date,
) -> float | None:
    """
    Project target price using upper trendline (log scale).

    Args:
        upper_slope: Slope of upper trendline (log scale)
        upper_intercept: Y-intercept of upper trendline (log scale)
        target_date: Date to project to

    Returns:
        Projected price at target date, or None if projection overflows
    """
    reference_date = HALVING_DATES[1]
    days = (target_date - reference_date).days
    log_price = upper_slope * days + upper_intercept

    # Guard against overflow - log_price > 308 would overflow float64
    # This happens with very steep slopes (short data spans or outliers)
    if log_price > TRENDLINE_LOG_PRICE_LIMIT or log_price < -TRENDLINE_LOG_PRICE_LIMIT:
        logger.debug("Trendline projection overflow: log_price=%.2f", log_price)
        return None

    return 10**log_price


def calculate_fib_extension(
    points: list[CyclePoint],
    idx: dict[tuple[int, PointType], list[CyclePoint]],
    level: float = DEFAULT_FIBONACCI_LEVEL,
) -> float | None:
    """
    Calculate Fibonacci extension target.

    Uses the most recent complete cycle:
    A = previous cycle min (prefer min1, fallback to min2)
    B = previous cycle max (max2 only - true cycle peak)
    C = current cycle min (min1 only - true cycle start)

    Extension (log-space): 10^(log10(C) + (log10(B) - log10(A)) * level)

    Using log-space respects the multiplicative nature of price movements:
    a 10x move from $1->$10 projects the same proportional extension as
    $100->$1000.

    The fallback for A (min1 -> min2) allows coins with partial pre-halving
    data to still get Fib projections, while maintaining chronological order
    (min -> max -> min).

    Args:
        points: All cycle points
        idx: Pre-built points index from build_points_index()
        level: Fibonacci level (default 100%)

    Returns:
        Projected price or None if insufficient data
    """
    # Need at least 2 cycles for Fibonacci
    cycles = sorted({p.cycle_num for p in points})

    if len(cycles) < 2:
        # Single cycle: insufficient data for meaningful Fibonacci extension.
        # Requires a prior cycle's move (A->B) to project from current low (C).
        return None

    # Use last complete cycle
    latest_cycle = max(cycles)
    prev_cycle = max(c for c in cycles if c < latest_cycle)

    # Get max2 from previous cycle (no fallback - must be true cycle peak)
    prev_max2_list = idx.get((prev_cycle, "max2"), [])
    prev_max2 = prev_max2_list[0] if prev_max2_list else None

    # Get min from previous cycle (prefer min1, fallback to min2)
    # This allows coins with partial pre-halving data to still get Fib projections
    prev_min1_list = idx.get((prev_cycle, "min1"), [])
    prev_min = prev_min1_list[0] if prev_min1_list else None
    if prev_min is None:
        prev_min2_list = idx.get((prev_cycle, "min2"), [])
        prev_min = prev_min2_list[0] if prev_min2_list else None

    # Get min1 from latest cycle (no fallback - must be true cycle start)
    latest_min1_list = idx.get((latest_cycle, "min1"), [])
    latest_min1 = latest_min1_list[0] if latest_min1_list else None

    if prev_min and prev_max2 and latest_min1:
        a = prev_min.price
        b = prev_max2.price
        c = latest_min1.price

        # Guard against non-positive prices (log undefined)
        if a <= 0 or b <= 0 or c <= 0:
            return None

        # Log-space extension: respects multiplicative nature of price moves
        log_a, log_b, log_c = math.log10(a), math.log10(b), math.log10(c)
        log_move = log_b - log_a
        return 10 ** (log_c + log_move * level)

    return None


def calculate_diminishing_return(
    points: list[CyclePoint],
    idx: dict[tuple[int, PointType], list[CyclePoint]],
) -> tuple[float | None, float | None]:
    """
    Calculate diminishing returns factor and projected target.

    Compares the % gain from min to max across cycles to estimate
    how much the returns diminish each cycle.

    Returns:
        Tuple of (projected_target, diminishing_factor)
    """
    cycles = sorted({p.cycle_num for p in points})

    if len(cycles) < 1:
        return None, None

    # Calculate gain ratios for each cycle
    gains = []
    for cycle in cycles:
        # Prefer major types (min1, max2); fallback to minor (min2, max1)
        min_prices = [p.price for p in idx.get((cycle, "min1"), [])]
        if not min_prices:
            min_prices = [p.price for p in idx.get((cycle, "min2"), [])]
        max_prices = [p.price for p in idx.get((cycle, "max2"), [])]
        if not max_prices:
            max_prices = [p.price for p in idx.get((cycle, "max1"), [])]

        if min_prices and max_prices:
            min_price = min(min_prices)
            max_price = max(max_prices)
            gain_ratio = max_price / min_price if min_price > 0 else 0
            gains.append((cycle, gain_ratio))

    if not gains:
        return None, None

    # If only one cycle, use default diminishing factor (conservative)
    if len(gains) == 1:
        last_gain_ratio = gains[0][1]
        dim_factor = DEFAULT_DIMINISHING_FACTOR
        next_gain_ratio = last_gain_ratio * dim_factor
        # Floor: projected gain can't be below DIM_RETURN_MIN_GAIN_RATIO
        # (the "diminishing returns" concept implies decreasing but still positive gains)
        next_gain_ratio = max(next_gain_ratio, DIM_RETURN_MIN_GAIN_RATIO)

        latest_min = find_latest_min_point(idx)

        if latest_min:
            target = latest_min.price * next_gain_ratio
            return target, dim_factor

        return None, dim_factor

    # Calculate diminishing factor from historical cycles
    # Factor = ratio of consecutive cycle gains
    dim_factors = []
    for i in range(1, len(gains)):
        if gains[i - 1][1] > 0:
            factor = gains[i][1] / gains[i - 1][1]
            dim_factors.append(factor)

    if dim_factors:
        if len(dim_factors) >= 3 and all(f > 0 for f in dim_factors):
            # Geometric mean for multiplicative ratios
            avg_dim_factor = float(np.exp(np.mean(np.log(dim_factors))))
        else:
            avg_dim_factor = float(np.mean(dim_factors))
        last_gain_ratio = gains[-1][1]
        next_gain_ratio = last_gain_ratio * avg_dim_factor
        # Floor: projected gain can't be below DIM_RETURN_MIN_GAIN_RATIO
        # (the "diminishing returns" concept implies decreasing but still positive gains)
        next_gain_ratio = max(next_gain_ratio, DIM_RETURN_MIN_GAIN_RATIO)

        latest_min = find_latest_min_point(idx)

        if latest_min:
            target = latest_min.price * next_gain_ratio
            return target, float(avg_dim_factor)

    return None, None


def calculate_historical_peak(
    points: list[CyclePoint],
    idx: dict[tuple[int, PointType], list[CyclePoint]],
) -> tuple[float | None, bool | None]:
    """
    Calculate historical peak target.

    Logic:
    - If previous cycle max2 is the absolute max across all cycles -> use that value
    - Otherwise -> weighted average of peaks at or above last max2 (67% max2, 33% max1)

    Returns:
        Tuple of (target_price, is_absolute_max)
    """
    # Get all max points using index
    max2_points = [p for key, pts in idx.items() if key[1] == "max2" for p in pts if p.price > 0]
    max1_points = [p for key, pts in idx.items() if key[1] == "max1" for p in pts if p.price > 0]

    if not max2_points:
        return None, None

    # Find the most recent cycle with max2 (previous cycle)
    latest_max2 = max(max2_points, key=lambda p: p.cycle_num)

    # Find absolute max across all max2 points
    absolute_max2 = max(max2_points, key=lambda p: p.price)

    # Case A: Previous cycle max2 is the absolute maximum
    if latest_max2.price >= absolute_max2.price:
        return latest_max2.price, True

    # Case B: Previous cycle max2 is NOT the absolute max
    # Weighted average of historical peaks at or above last max2
    threshold = latest_max2.price
    filtered_max2 = [p for p in max2_points if p.price >= threshold]
    filtered_max1 = [p for p in max1_points if p.price >= threshold]

    all_peaks = filtered_max2 + filtered_max1
    if not all_peaks:
        return latest_max2.price, False

    # Weighted sum: max2 gets 67%, max1 gets 33%
    weighted_sum = 0.0
    weight_total = 0.0

    for p in filtered_max2:
        weighted_sum += p.price * MAJOR_POINT_WEIGHT
        weight_total += MAJOR_POINT_WEIGHT

    for p in filtered_max1:
        weighted_sum += p.price * MINOR_POINT_WEIGHT
        weight_total += MINOR_POINT_WEIGHT

    if weight_total == 0:
        return latest_max2.price, False

    weighted_avg = weighted_sum / weight_total
    return weighted_avg, False


def calculate_weighted_composite(
    trendline_pct: float | None,
    fib_pct: float | None,
    dim_return_pct: float | None,
    hist_peak_pct: float | None,
    confidence: Confidence = "high",
) -> float | None:
    """
    Calculate weighted composite target percentage.

    Uses confidence-based weight profiles from COMPOSITE_WEIGHT_PROFILES.
    Each confidence level defines method weights and a scale factor,
    providing a single code path for all coins regardless of confidence.

    For high confidence: all 4 methods are weighted, scale = 1.0.
    For medium confidence: all 4 methods are weighted, scale = 0.9.
    For low confidence: only historical peak has meaningful weight;
    trendline, fibonacci, and diminishing are near-zero. Scale = 0.1
    (90% penalty for single-cycle uncertainty).

    When a method is unavailable (None), its weight is excluded and the
    remaining weights are renormalized.

    Args:
        trendline_pct: Trendline projection percentage
        fib_pct: Fibonacci extension percentage
        dim_return_pct: Diminishing returns percentage
        hist_peak_pct: Historical peak percentage
        confidence: Confidence level ("high", "medium", or "low")

    Returns:
        Weighted composite percentage, or None if no methods available
    """
    profile = COMPOSITE_WEIGHT_PROFILES[confidence]

    # Build list of (value, weight) pairs for available methods
    components: list[tuple[float, float]] = []

    if trendline_pct is not None and profile["trendline"] > 0:
        components.append((trendline_pct, profile["trendline"]))
    if fib_pct is not None and profile["fibonacci"] > 0:
        components.append((fib_pct, profile["fibonacci"]))
    if dim_return_pct is not None and profile["diminishing"] > 0:
        components.append((dim_return_pct, profile["diminishing"]))
    if hist_peak_pct is not None and profile["historical"] > 0:
        components.append((hist_peak_pct, profile["historical"]))

    if not components:
        return None

    # Weighted average with renormalization, then apply confidence scale
    total_weight = sum(w for _, w in components)
    weighted_sum = sum(v * w for v, w in components)
    return (weighted_sum / total_weight) * profile["scale"]


def calculate_retracement_ratio(
    points: list[CyclePoint],
    idx: dict[tuple[int, PointType], list[CyclePoint]],
) -> float | None:
    """
    Calculate Fibonacci retracement ratio of the last cycle move.

    Uses three structural points (standard Fibonacci retracement setup):
      A = previous cycle's min (min1 preferred, min2 fallback)
      B = previous cycle's max2 (peak)
      C = next cycle's min1 (new trough / current cycle start)

    The retracement ratio in log-space:
      log_retracement = log10(B / C) / log10(B / A)
      0.0 = C at peak (no retracement)
      1.0 = C at previous trough (full retracement)

    Coins that retrace beyond MAX_RETRACEMENT_LEVEL (88.6%) are considered
    structurally broken — the "higher low" pattern has failed.

    Args:
        points: List of cycle points

    Returns:
        Retracement ratio (0.0-1.0+), or None if insufficient data.
        Values > 1.0 mean C dropped below A (worse than full retracement).
    """
    if not points:
        return None

    # Find the last cycle that has a max2 (completed peak)
    max2_points = [p for key, pts in idx.items() if key[1] == "max2" for p in pts if p.price > 0]
    if not max2_points:
        return None

    last_max2 = max(max2_points, key=lambda p: p.cycle_num)
    peak_cycle = last_max2.cycle_num
    peak_price = last_max2.price  # B

    # A: Find min from the same cycle as peak (min1 preferred, min2 fallback)
    cycle_min1s = [p for p in idx.get((peak_cycle, "min1"), []) if p.price > 0]
    cycle_min2s = [p for p in idx.get((peak_cycle, "min2"), []) if p.price > 0]
    cycle_mins = cycle_min1s + cycle_min2s
    if not cycle_mins:
        return None

    prev_trough = min(cycle_min1s if cycle_min1s else cycle_mins, key=lambda p: p.price)
    prev_trough_price = prev_trough.price  # A

    # C: Find next cycle's min1 (the new trough after the peak)
    next_min1s = [
        p
        for key, pts in idx.items()
        if key[1] == "min1" and key[0] > peak_cycle
        for p in pts
        if p.price > 0
    ]
    if not next_min1s:
        return None

    new_trough = min(next_min1s, key=lambda p: p.cycle_num)
    new_trough_price = new_trough.price  # C

    # Use extracted Fibonacci kernel
    try:
        return fib_retracement_ratio(prev_trough_price, peak_price, new_trough_price)
    except ValueError:
        return None


def classify_pattern(
    upper_slope: float | None,
    lower_slope: float | None,
) -> str:
    """
    Classify the pattern based on trendline slopes.

    Args:
        upper_slope: Slope of upper trendline
        lower_slope: Slope of lower trendline

    Returns:
        Pattern type string
    """
    if upper_slope is None or lower_slope is None:
        return "unknown"

    slope_diff = abs(upper_slope - lower_slope)

    if slope_diff < SLOPE_DIFF_CHANNEL_THRESHOLD:
        return "channel"
    elif upper_slope < lower_slope:
        return "falling_wedge"
    else:
        return "rising_wedge"

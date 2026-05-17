"""
Cycle Pattern Analysis Module for Halvix.

Identifies min/max points within halving cycle windows and applies four
analysis methods to project price targets for the next cycle:

1. Log-Linear Trendline Regression
2. Fibonacci Extensions (100% level)
3. Diminishing Returns Model
4. Historical Peak

COIN SELECTION:
- Analyzes all coins that have been in TOTAL2 at any point in the past 3 years
- This expanded selection allows analysis of coins even if they temporarily
  dropped out of the TOTAL2 top 30

DATA APPROACH:
- Uses FULL price history for each coin (not just dates when in TOTAL2)
- Detects symbol replacements (e.g., old MOVE token replaced by Movement Labs MOVE)
- This allows min/max points to be detected even when a coin is temporarily
  outside the TOTAL2 index

Returns are calculated as percentage gain from CURRENT PRICE to projected target.

Usage:
    from analysis.cycle_patterns import CyclePatternAnalyzer

    analyzer = CyclePatternAnalyzer()
    results = analyzer.analyze_all_coins()
    top_coins = analyzer.get_top_coins(n=14)
"""

import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from analysis import point_detection
from analysis.cycle_points import (
    CoinPatternResult,
    Confidence,
    CyclePoint,
    PointType,
    _to_date,
    fib_retracement_ratio,
)
from config import (
    COMPOSITE_WEIGHT_PROFILES,
    CURRENT_CYCLE_MIN1_APPROX_DAYS_BEFORE_HALVING,
    DAYS_BEFORE_HALVING,
    DEFAULT_DIMINISHING_FACTOR,
    DEFAULT_FIBONACCI_LEVEL,
    DIM_RETURN_MIN_GAIN_RATIO,
    GOLDEN_RETRACEMENT_LEVEL,
    HALVING_DATES,
    MAJOR_POINT_WEIGHT,
    MAX_RETRACEMENT_LEVEL,
    MIN_COIN_AGE_DAYS,
    MIN_LOWER_SLOPE,
    MIN_UNIQUE_PRICES,
    MIN_UPPER_TRENDLINE_TARGET_PCT,
    MINOR_POINT_WEIGHT,
    PROCESSED_DIR,
    RETRACEMENT_PENALTY_AT_MAX,
    SLOPE_DIFF_CHANNEL_THRESHOLD,
    TOTAL2_COMPOSITION_FILE,
    TOTAL2_LOOKBACK_YEARS,
    TRENDLINE_LOG_PRICE_LIMIT,
    TRENDLINE_RECENCY_DECAY,
    UNIQUE_PRICES_WINDOW_DAYS,
)
from data.cache import PriceDataCache
from data.price_filters import detect_round_trips, detect_symbol_replacement
from utils.logging import get_logger

logger = get_logger(__name__)


class CyclePatternAnalyzer:
    """
    Analyzes cycle patterns for BTC and altcoins.

    Uses segment-based detection between consecutive halvings.
    Within each segment [H[n-1], H[n]], identifies up to 4 points:

    - max2(n-1): max price in segment (structural, always exists)
    - min2(n-1): min in [H[n-1], max2 date] (optional, 23.6% significance)
    - min1(n): min in [max2 date, H[n]] (structural for completed cycles)
    - max1(n): max in [min1 date, H[n]] (optional, 23.6% significance)

    Points are validated using Fibonacci retracement thresholds (MIN_RETRACEMENT_LEVEL).
    Optional points (min2, max1) must show >= 23.6% retracement to be significant.
    Alternation rule: if a segment ends with min (no max1), next has no min2.

    COIN SELECTION:
    - Analyzes all coins that have been in TOTAL2 at any point in the past 3 years
    - Coins must have been in TOTAL2 within the TOTAL2_LOOKBACK_YEARS period

    DATA APPROACH:
    - Uses FULL price history for each coin (not just TOTAL2 dates)
    - Detects symbol replacements (e.g., old MOVE replaced by Movement Labs MOVE)
    - This allows min/max points to be detected even when outside TOTAL2 index

    Then applies 4 projection methods and ranks by composite target.
    """

    def __init__(
        self,
        price_cache: PriceDataCache | None = None,
        min_cycles: int = 1,
    ):
        """
        Initialize the analyzer.

        Args:
            price_cache: Optional price cache instance
            min_cycles: Minimum number of cycles required for analysis (default: 1)
        """
        self.price_cache = price_cache or PriceDataCache()
        self.min_cycles = min_cycles

        # Use cycles 2-5 (skip cycle 1 — too little altcoin data)
        # Cycles 2-4 are completed halvings, cycle 5 is projected (2028)
        self.all_halvings = HALVING_DATES[1:]
        self.current_cycle_num = len(HALVING_DATES)
        self.projected_halving = HALVING_DATES[-1]

        # Load TOTAL2 composition for filtering
        self._total2_composition: pd.DataFrame | None = None
        self._total2_coins: set[str] | None = None

        # Early-pipeline counts populated by analyze_all_coins() and consumed
        # by get_top_coins() when it prints the unified filter table. They
        # remain None when get_top_coins() is called without analyze_all_coins()
        # first (e.g. from a direct caller passing in custom results).
        self._pipeline_cached_coins: int | None = None
        self._pipeline_total2_coins: int | None = None

    def _load_total2_composition(self) -> pd.DataFrame | None:
        """Load TOTAL2 composition data."""
        if self._total2_composition is not None:
            return self._total2_composition

        if TOTAL2_COMPOSITION_FILE.exists():
            try:
                self._total2_composition = pd.read_parquet(TOTAL2_COMPOSITION_FILE)
                logger.info(
                    "Loaded TOTAL2 composition: %d records",
                    len(self._total2_composition),
                )
            except Exception as e:
                logger.warning("Could not load TOTAL2 composition: %s", e)

        return self._total2_composition

    def _get_total2_coins(self) -> set[str]:
        """
        Get set of coins that have been in TOTAL2 within the past TOTAL2_LOOKBACK_YEARS.

        This expanded selection allows analysis of coins even if they temporarily
        dropped out of the TOTAL2 top 30.

        Returns:
            Set of coin IDs (lowercase) that were in TOTAL2 within the lookback period
        """
        if self._total2_coins is not None:
            return self._total2_coins

        self._total2_coins = set()

        comp_df = self._load_total2_composition()
        if comp_df is not None:
            # Filter to coins that were in TOTAL2 within the lookback period
            lookback_cutoff = date.today() - timedelta(days=TOTAL2_LOOKBACK_YEARS * 365)

            # Convert date column if needed
            if "date" in comp_df.columns:
                comp_df_dates = pd.to_datetime(comp_df["date"]).dt.date

                recent_mask = comp_df_dates >= lookback_cutoff
                recent_coins = comp_df[recent_mask]["coin_id"].str.lower().unique()
                self._total2_coins = set(recent_coins)

                logger.info(
                    "Found %d coins in TOTAL2 within past %d years (from %s)",
                    len(self._total2_coins),
                    TOTAL2_LOOKBACK_YEARS,
                    lookback_cutoff.isoformat(),
                )
            else:
                self._total2_coins = set(comp_df["coin_id"].str.lower().unique())
                logger.info(
                    "Found %d coins in TOTAL2 history (no date filtering)", len(self._total2_coins)
                )

        return self._total2_coins

    def _get_coin_total2_dates(self, coin_id: str) -> set[date]:
        """
        Get the dates when a coin was in TOTAL2.

        Args:
            coin_id: Lowercase coin ID

        Returns:
            Set of dates when the coin was in TOTAL2
        """
        comp_df = self._load_total2_composition()
        if comp_df is None:
            return set()

        coin_data = comp_df[comp_df["coin_id"] == coin_id]
        if coin_data.empty:
            return set()

        # Convert to set of dates
        return {_to_date(ts) for ts in coin_data["date"]}

    # ── Identification kernel ─────────────────────────────────────────
    # The kernel itself lives in ``analysis.point_detection``. The thin
    # wrappers below forward to module-level functions there so callers
    # (and tests) that use ``analyzer._foo(...)`` /
    # ``CyclePatternAnalyzer._foo(...)`` continue to work unchanged.
    # ────────────────────────────────────────────────────────────────

    def _identify_cycle_points(self, df: pd.DataFrame) -> list[CyclePoint]:
        """Detect cycle min/max points across all halving-delimited segments."""
        return point_detection.identify_cycle_points(df, self.all_halvings)

    _build_segments = staticmethod(point_detection.build_segments)
    _pass1_find_max2 = staticmethod(point_detection.pass1_find_max2)
    _pass2_find_min2_candidates = staticmethod(point_detection.pass2_find_min2_candidates)
    _merge_adjacent_maxes = staticmethod(point_detection.merge_adjacent_maxes)
    _pass3_validate_and_detect = staticmethod(point_detection.pass3_validate_and_detect)
    _process_segment = staticmethod(point_detection.process_segment)
    _extend_min2_search = staticmethod(point_detection.extend_min2_search)
    _adjust_launch_min2 = staticmethod(point_detection.adjust_launch_min2)
    _find_max1_before_min2 = staticmethod(point_detection.find_max1_before_min2)
    _check_min2_retracement = staticmethod(point_detection.check_min2_retracement)
    _validate_min2 = staticmethod(point_detection.validate_min2)
    _replace_min1_if_lower = staticmethod(point_detection.replace_min1_if_lower)
    _find_min1 = staticmethod(point_detection.find_min1)
    _find_max1 = staticmethod(point_detection.find_max1)
    _correct_min1_with_max1 = staticmethod(point_detection.correct_min1_with_max1)
    _detect_post_halving_points = staticmethod(point_detection.detect_post_halving_points)
    _find_latest_min_point = staticmethod(point_detection.find_latest_min_point)
    _build_points_index = staticmethod(point_detection.build_points_index)
    _count_min1_cycles = staticmethod(point_detection.count_min1_cycles)

    @staticmethod
    def _get_regression_date(point: CyclePoint) -> date:
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

    def _fit_log_trendlines(
        self,
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
        # HYPE). _get_regression_date() already provides a stable x-coordinate for it.
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
        # Note: Projected min1 uses approximated date via _get_regression_date()
        reference_date = HALVING_DATES[1]

        peak_x = np.array(
            [(self._get_regression_date(p) - reference_date).days for p in peaks]
        ).reshape(-1, 1)
        peak_y = np.log10([p.price for p in peaks])

        trough_x = np.array(
            [(self._get_regression_date(p) - reference_date).days for p in troughs]
        ).reshape(-1, 1)
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
                        [(self._get_regression_date(p) - reference_date).days for p in major_peaks]
                    )
                    major_peak_y = np.mean([np.log10(p.price) for p in major_peaks])
                else:
                    # Fallback to highest peak
                    highest_peak = max(peaks, key=lambda p: p.price)
                    major_peak_x = (self._get_regression_date(highest_peak) - reference_date).days
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
                        [
                            (self._get_regression_date(p) - reference_date).days
                            for p in major_troughs
                        ]
                    )
                    major_trough_y = np.mean([np.log10(p.price) for p in major_troughs])
                else:
                    # Fallback to lowest trough
                    lowest_trough = min(troughs, key=lambda p: p.price)
                    major_trough_x = (
                        self._get_regression_date(lowest_trough) - reference_date
                    ).days
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
                peak_x_val = (self._get_regression_date(highest_peak) - reference_date).days
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
                trough_x_val = (self._get_regression_date(lowest_trough) - reference_date).days
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

    def _project_trendline_target(
        self,
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

    def _calculate_fib_extension(
        self,
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
            idx: Pre-built points index from _build_points_index()
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

    def _calculate_diminishing_return(
        self,
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

            latest_min = self._find_latest_min_point(idx)

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

            latest_min = self._find_latest_min_point(idx)

            if latest_min:
                target = latest_min.price * next_gain_ratio
                return target, float(avg_dim_factor)

        return None, None

    def _calculate_historical_peak(
        self,
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
        max2_points = [
            p for key, pts in idx.items() if key[1] == "max2" for p in pts if p.price > 0
        ]
        max1_points = [
            p for key, pts in idx.items() if key[1] == "max1" for p in pts if p.price > 0
        ]

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

    @staticmethod
    def _calculate_weighted_composite(
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

    @staticmethod
    def _calculate_retracement_ratio(
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
        max2_points = [
            p for key, pts in idx.items() if key[1] == "max2" for p in pts if p.price > 0
        ]
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

    def _classify_pattern(
        self,
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

    def _run_projections(self, result: CoinPatternResult) -> None:
        """Run all projection methods and set results in-place.

        Shared pipeline for both BTC and altcoin analysis: sets confidence
        from cycle count, fits trendlines, runs all 4 projection methods,
        computes the composite score, and applies the retracement penalty.
        """
        # Set confidence from cycle count (same logic for BTC and altcoins)
        if result.num_cycles >= 3:
            result.confidence = "high"
        elif result.num_cycles >= 2:
            result.confidence = "medium"
        else:
            result.confidence = "low"

        # Build points index once for all projection methods
        idx = self._build_points_index(result.points)

        # Fit trendlines
        upper_slope, upper_int, lower_slope, lower_int = self._fit_log_trendlines(result.points)

        if upper_slope is not None:
            result.upper_slope = upper_slope
            result.lower_slope = lower_slope
            result.upper_intercept = upper_int
            result.lower_intercept = lower_int
            result.pattern_type = self._classify_pattern(upper_slope, lower_slope)

            # Expected peak ≈ halving + 550 days (same offset as DAYS_BEFORE_HALVING)
            target_date = self.projected_halving + timedelta(days=DAYS_BEFORE_HALVING)
            target = self._project_trendline_target(upper_slope, upper_int, target_date)
            if target is not None:
                result.trendline_target = target
                result.trendline_target_pct = (target / result.current_price - 1) * 100

        # Fibonacci extension
        fib_target = self._calculate_fib_extension(result.points, idx)
        if fib_target:
            result.fib_target = fib_target
            result.fib_target_pct = (fib_target / result.current_price - 1) * 100

        # Diminishing returns
        dim_target, dim_factor = self._calculate_diminishing_return(result.points, idx)
        if dim_target:
            result.dim_return_target = dim_target
            result.dim_return_target_pct = (dim_target / result.current_price - 1) * 100
            result.dim_return_factor = dim_factor

        # Historical peak
        hist_peak_target, hist_peak_is_absolute = self._calculate_historical_peak(
            result.points, idx
        )
        if hist_peak_target:
            result.hist_peak_target = hist_peak_target
            result.hist_peak_target_pct = (hist_peak_target / result.current_price - 1) * 100
            result.hist_peak_is_absolute = hist_peak_is_absolute

        # Composite target (weighted average using confidence-based weight profile)
        result.composite_target_pct = self._calculate_weighted_composite(
            trendline_pct=result.trendline_target_pct,
            fib_pct=result.fib_target_pct,
            dim_return_pct=result.dim_return_target_pct,
            hist_peak_pct=result.hist_peak_target_pct,
            confidence=result.confidence,
        )

        # Retracement ratio + continuous penalty
        result.retracement_ratio = self._calculate_retracement_ratio(result.points, idx)
        if (
            result.retracement_ratio is not None
            and result.composite_target_pct is not None
            and result.retracement_ratio > GOLDEN_RETRACEMENT_LEVEL
            and result.retracement_ratio <= MAX_RETRACEMENT_LEVEL
        ):
            t = (result.retracement_ratio - GOLDEN_RETRACEMENT_LEVEL) / (
                MAX_RETRACEMENT_LEVEL - GOLDEN_RETRACEMENT_LEVEL
            )
            penalty = 1.0 - t * (1.0 - RETRACEMENT_PENALTY_AT_MAX)
            result.composite_target_pct *= penalty

    @staticmethod
    def _smooth_round_trips(df: pd.DataFrame, label: str) -> pd.DataFrame:
        """
        Smooth single-day spike-and-revert glitches in df['close'].

        Cycle min/max detection (idxmax/idxmin over halving segments) and the
        log-linear trendline regression both read close prices directly, so a
        one-day pump-dump (e.g. SIREN 2026-04-16 at 2.49x reverting next day)
        can produce a false max1/max2 or skew the trendline. Mirrors the
        round-trip correction applied in the TOTAL2b processor, keeping the
        close-series guards in sync between the two pipelines.

        Returns the input df with df['close'] mutated on spike days
        (set to the prior day's close). df itself is copied to avoid
        mutating the cache layer.
        """
        if df.empty or "close" not in df.columns:
            return df
        events = detect_round_trips(df["close"])
        if not events:
            return df
        df = df.copy()
        for ev in events:
            for dt in ev["smoothed_dates"]:
                df.at[dt, "close"] = ev["pre_price"]
            span_str = (
                f"{ev['smoothed_dates'][0].date()}"
                if len(ev["smoothed_dates"]) == 1
                else f"{ev['smoothed_dates'][0].date()}..{ev['smoothed_dates'][-1].date()}"
            )
            logger.info(
                "%s: round-trip glitch on %s smoothed: peak %.3e → %.3e (jump %.2fx, "
                "revert %.2fx after %dd, %s)",
                label,
                span_str,
                ev["jump_price"],
                ev["pre_price"],
                ev["jump_ratio"],
                ev["revert_ratio"],
                ev["days_to_revert"],
                ev["direction"],
            )
        return df

    def analyze_btc(self) -> CoinPatternResult | None:
        """
        Analyze BTC/USD pattern using the same cycle point detection as altcoins.

        Returns:
            CoinPatternResult or None if data unavailable
        """
        btc_df = self.price_cache.get_prices("btc", "USD")

        if btc_df is None or btc_df.empty:
            logger.warning("BTC-USD data not available")
            return None

        btc_df = self._smooth_round_trips(btc_df, "BTC")

        result = CoinPatternResult(coin_id="btc")
        result.points = self._identify_cycle_points(btc_df)

        if not result.points:
            logger.warning("No BTC cycle points found")
            return None

        result.num_cycles = self._count_min1_cycles(result.points)
        result.current_price = float(btc_df["close"].iloc[-1])
        result.current_date = btc_df.index[-1].date()

        if result.current_price <= 0:
            logger.warning(
                "BTC: current_price is %.4g — skipping projections to avoid divide-by-zero",
                result.current_price,
            )
            return None

        self._run_projections(result)
        return result

    def analyze_coin(self, coin_id: str, force: bool = False) -> CoinPatternResult | None:
        """
        Analyze pattern for a single altcoin vs BTC.

        Uses FULL price history to detect cycle min/max points (not just TOTAL2 dates).
        This ensures accurate detection of true extremes even when a coin temporarily
        drops out of the TOTAL2 index.

        Args:
            coin_id: Lowercase coin ID (e.g., "eth")
            force: If True, skip TOTAL2 membership and minimum cycle checks

        Returns:
            CoinPatternResult or None if insufficient data
        """
        # Load coin price data (vs BTC)
        df = self.price_cache.get_prices(coin_id, "BTC")

        if df is None or df.empty:
            logger.debug("%s: No BTC price data available", coin_id.upper())
            return None

        # Detect symbol replacement (e.g., old MOVE token replaced by Movement Labs MOVE)
        # If detected, filter price data to only include the new token's data
        if "close" in df.columns:
            replacement_date = detect_symbol_replacement(df["close"])
            if replacement_date is not None:
                logger.info(
                    "%s: Symbol replacement detected on %s, filtering to post-replacement data",
                    coin_id.upper(),
                    replacement_date.date(),
                )
                df = df[df.index >= replacement_date]

                if df.empty:
                    logger.debug("%s: No data after symbol replacement date", coin_id.upper())
                    return None

        # Smooth single-day spike-and-revert glitches on the close series so
        # they cannot become false max1/max2/min1/min2 points or distort the
        # log-linear trendline.
        df = self._smooth_round_trips(df, coin_id.upper())

        # Get TOTAL2 membership info (for reference, not filtering)
        total2_dates = self._get_coin_total2_dates(coin_id)
        first_total2 = min(total2_dates) if total2_dates else None
        last_total2 = max(total2_dates) if total2_dates else None

        if not force:
            if first_total2 is None:
                logger.debug("No TOTAL2 data for %s", coin_id)
                return None

            # Check that coin was in TOTAL2 within the lookback period
            # This is now handled by _get_total2_coins, but double-check here
            if last_total2 is not None:
                lookback_cutoff = date.today() - timedelta(days=TOTAL2_LOOKBACK_YEARS * 365)
                if last_total2 < lookback_cutoff:
                    logger.debug(
                        "%s: Last in TOTAL2 on %s, before lookback cutoff %s, skipping",
                        coin_id,
                        last_total2.isoformat(),
                        lookback_cutoff.isoformat(),
                    )
                    return None

        result = CoinPatternResult(coin_id=coin_id)
        result.first_in_total2 = first_total2
        result.last_in_total2 = last_total2
        result.days_in_total2 = len(total2_dates)

        # Find points using segment-based detection across all halvings
        result.points = self._identify_cycle_points(df)

        if not result.points:
            logger.debug("%s: No cycle points found", coin_id.upper())
            return None

        # Count cycles where coin has min1 (pre-halving data proves coin existed before halving)
        # Post-halving-only data (min2/max2) doesn't count as experiencing a full cycle
        result.num_cycles = self._count_min1_cycles(result.points)

        # Check minimum cycles requirement
        if not force and result.num_cycles < self.min_cycles:
            logger.debug(
                "%s: Insufficient cycles (%d < %d required)",
                coin_id.upper(),
                result.num_cycles,
                self.min_cycles,
            )
            return None

        # Get current price and price quality info
        result.current_price = float(df["close"].iloc[-1])
        result.current_date = df.index[-1].date()
        result.first_price_date = df.index[0].date()

        # Guard against a zero (or negative) latest close. This would only
        # happen for a delisted coin or a feed gap that survived earlier
        # filters; projections like (target / current_price - 1) would
        # otherwise hit ZeroDivisionError. Skip the coin entirely.
        if result.current_price <= 0:
            logger.info(
                "%s: current_price is %.4g — skipping projections to avoid divide-by-zero",
                coin_id.upper(),
                result.current_price,
            )
            return None

        unique_window_start = result.current_date - timedelta(days=UNIQUE_PRICES_WINDOW_DAYS)
        recent_prices = df[df.index.date >= unique_window_start]
        result.unique_price_count = (
            recent_prices["close"].nunique() if not recent_prices.empty else 0
        )

        self._run_projections(result)
        return result

    def analyze_all_coins(
        self,
        filter_total2: bool = True,
        include: set[str] | None = None,
        show_progress: bool = True,
    ) -> dict[str, CoinPatternResult]:
        """
        Analyze all available altcoins.

        When filter_total2=True (default), only analyzes coins that have been
        in TOTAL2 within the past TOTAL2_LOOKBACK_YEARS (default: 3 years).
        This expanded selection allows analysis of coins even if they temporarily
        dropped out of the TOTAL2 top 30.

        Uses FULL price history for each coin, allowing accurate min/max
        detection even outside TOTAL2 dates.

        Args:
            filter_total2: If True, only analyze coins in TOTAL2 within past 3 years
            include: Coin IDs to always include regardless of TOTAL2 filter
            show_progress: If True, show progress bar

        Returns:
            Dictionary mapping coin_id to CoinPatternResult
        """
        # Get list of coins to analyze
        cached_coins = self.price_cache.list_cached_coins("BTC")
        cached_set = set(cached_coins)

        if filter_total2:
            # Get coins in TOTAL2 within past TOTAL2_LOOKBACK_YEARS
            total2_coins = self._get_total2_coins()
            coins_to_analyze = [c for c in cached_coins if c in total2_coins]
            # Add force-included coins that exist in cache
            if include:
                forced = [c for c in include if c in cached_set and c not in total2_coins]
                if forced:
                    coins_to_analyze.extend(forced)
                    logger.info("Force-included %d coins: %s", len(forced), ", ".join(forced))
            logger.info(
                "Analyzing %d coins (in TOTAL2 within past %d years, from %d cached)",
                len(coins_to_analyze),
                TOTAL2_LOOKBACK_YEARS,
                len(cached_coins),
            )
        else:
            coins_to_analyze = cached_coins
            logger.info("Analyzing %d coins", len(coins_to_analyze))

        # Store early pipeline counts for the unified filter table in get_top_coins()
        self._pipeline_cached_coins = len(cached_coins)
        self._pipeline_total2_coins = len(coins_to_analyze)

        results = {}

        if show_progress:
            try:
                from tqdm import tqdm

                coins_iter = tqdm(coins_to_analyze, desc="Analyzing patterns")
            except ImportError:
                coins_iter = coins_to_analyze
        else:
            coins_iter = coins_to_analyze

        include_set = include or set()
        for coin_id in coins_iter:
            result = self.analyze_coin(coin_id, force=coin_id in include_set)
            if result and result.composite_target_pct is not None:
                results[coin_id] = result

        logger.info("Successfully analyzed %d coins with valid projections", len(results))
        return results

    def get_top_coins(
        self,
        results: dict[str, CoinPatternResult],
        n: int = 9,
        include: set[str] | None = None,
    ) -> list[CoinPatternResult]:
        """
        Get top N coins by composite target percentage.

        Filtering rules:
        - Coins must have at least one intermediate extrema (max1 or min2) beyond max2 + min1
        - Coins must have at least 3 actual (non-projected) extrema
        - Coins with declining floor (lower_slope < MIN_LOWER_SLOPE) are excluded
        - Coins with excessive Fibonacci retracement (> MAX_RETRACEMENT_LEVEL) are excluded
        - Coins must be at least MIN_COIN_AGE_DAYS old (1 year)
        - Coins must have at least MIN_UNIQUE_PRICES distinct price values (filters illiquid/staircase)

        Force-included coins (via ``include``) bypass all quality filters.

        Args:
            results: Dictionary of coin results
            n: Number of top coins to return
            include: Coin IDs that bypass filters and are always included

        Returns:
            List of top N CoinPatternResult sorted by composite_target_pct (descending)
        """
        today = date.today()
        min_first_price_date = today - timedelta(days=MIN_COIN_AGE_DAYS)

        # Separate force-included coins — they bypass all quality filters
        include_set = include or set()
        forced_results = {cid: r for cid, r in results.items() if cid in include_set}

        # Apply filters successively and track counts for logging
        # Note: results from analyze_all_coins() already have composite_target_pct != None,
        # so no need to re-filter for that here.
        candidates = list(results.values())
        total_start = len(candidates)

        # Filter 1: Must have at least one intermediate extrema (max1 or min2) beyond
        # the structural pair (max2 + min1). This ensures enough cycle structure for
        # meaningful pattern analysis.
        candidates = [
            r for r in candidates if any(p.point_type in ("max1", "min2") for p in r.points)
        ]
        after_extrema = len(candidates)

        # Filter 2: Must have at least 3 actual (non-projected) extrema.
        # Coins with only 2 real points (e.g., PIPPIN with min2 + max2) have too little
        # data for reliable predictions, even if a projected min1 enables trendline fitting.
        candidates = [r for r in candidates if sum(1 for p in r.points if not p.projected) >= 3]
        after_actual = len(candidates)

        # Filter 3: Declining floor (lower_slope below MIN_LOWER_SLOPE)
        candidates = [
            r for r in candidates if r.lower_slope is None or r.lower_slope >= MIN_LOWER_SLOPE
        ]
        after_floor = len(candidates)

        # Filter 4: Trendline projection too negative (below MIN_UPPER_TRENDLINE_TARGET_PCT)
        candidates = [
            r
            for r in candidates
            if r.trendline_target_pct is None
            or r.trendline_target_pct >= MIN_UPPER_TRENDLINE_TARGET_PCT
        ]
        after_trendline = len(candidates)

        # Filter 5: Excessive Fibonacci retracement (> MAX_RETRACEMENT_LEVEL)
        candidates = [
            r
            for r in candidates
            if r.retracement_ratio is None or r.retracement_ratio <= MAX_RETRACEMENT_LEVEL
        ]
        after_retracement = len(candidates)

        # Filter 6: Too new (first_price_date < MIN_COIN_AGE_DAYS ago)
        candidates = [
            r
            for r in candidates
            if r.first_price_date is None or r.first_price_date <= min_first_price_date
        ]
        after_age = len(candidates)

        # Filter 7: Too few unique prices (staircase/illiquid patterns)
        candidates = [r for r in candidates if r.unique_price_count >= MIN_UNIQUE_PRICES]
        after_unique = len(candidates)

        # Build unified filter summary table including early pipeline stages.
        # These counts are populated by analyze_all_coins(); when get_top_coins()
        # is called standalone (custom results dict) they stay at None and the
        # table simply omits those leading rows.
        cached = self._pipeline_cached_coins
        total2 = self._pipeline_total2_coins

        lines = ["Coin selection & filter summary:"]
        lines.append(f"  {'Step':<44s}  {'Remaining'}")

        def _start(label: str, count: int) -> str:
            return f"  {label:<44s}  {count}"

        def _step(label: str, count: int, removed: int) -> str:
            return f"  {label:<44s}  {count}  (-{removed})"

        if cached is not None:
            lines.append(_start("Cached altcoin prices", cached))
        if total2 is not None:
            prev = cached if cached is not None else total2
            lines.append(
                _step(f"In TOTAL2 within past {TOTAL2_LOOKBACK_YEARS} years", total2, prev - total2)
            )
            lines.append(
                _step(
                    "Enough cycle data for projections",
                    total_start,
                    total2 - total_start,
                )
            )
        else:
            lines.append(_start("With cycle projections", total_start))

        lines.append(
            _step(
                "Has intermediate extrema (max1/min2)", after_extrema, total_start - after_extrema
            )
        )
        lines.append(_step("Actual extrema >= 3", after_actual, after_extrema - after_actual))
        lines.append(
            _step("Floor not declining (slope >= min)", after_floor, after_actual - after_floor)
        )
        lines.append(
            _step(
                f"Trendline projection >= {MIN_UPPER_TRENDLINE_TARGET_PCT}%",
                after_trendline,
                after_floor - after_trendline,
            )
        )
        lines.append(
            _step(
                f"Retracement <= {MAX_RETRACEMENT_LEVEL * 100:.1f}%",
                after_retracement,
                after_trendline - after_retracement,
            )
        )
        lines.append(
            _step(f"Coin age >= {MIN_COIN_AGE_DAYS} days", after_age, after_retracement - after_age)
        )
        lines.append(
            _step(
                f"Unique prices >= {MIN_UNIQUE_PRICES} (last {UNIQUE_PRICES_WINDOW_DAYS}d)",
                after_unique,
                after_age - after_unique,
            )
        )

        if forced_results:
            lines.append(f"  {'Force-included coins':<44s}  {len(forced_results)}")

        logger.info("\n".join(lines))

        # Sort by composite target (descending) - primary ranking criterion
        sorted_results = sorted(candidates, key=lambda x: x.composite_target_pct or 0, reverse=True)

        top = sorted_results[:n]

        # Append force-included coins that aren't already in top-N,
        # sorted among themselves by composite target (descending)
        if forced_results:
            top_ids = {r.coin_id for r in top}
            extras = [r for r in forced_results.values() if r.coin_id not in top_ids]
            extras.sort(key=lambda x: x.composite_target_pct or 0, reverse=True)
            top.extend(extras)

        return top

    def save_results(
        self,
        btc_result: CoinPatternResult | None,
        coin_results: dict[str, CoinPatternResult],
        output_path: Path | None = None,
    ) -> Path:
        """
        Save analysis results to JSON.

        Args:
            btc_result: BTC analysis result
            coin_results: Dictionary of altcoin results
            output_path: Path to save JSON (default: data/processed/pattern_targets.json)

        Returns:
            Path to saved file
        """
        if output_path is None:
            output_path = PROCESSED_DIR / "pattern_targets.json"

        output_path.parent.mkdir(parents=True, exist_ok=True)

        def point_to_dict(p: CyclePoint) -> dict:
            d = {
                "date": p.date.isoformat(),
                "price": p.price,
                "cycle_num": p.cycle_num,
                "point_type": p.point_type,
                "days_from_halving": p.days_from_halving,
            }
            if p.projected:
                d["projected"] = True
            return d

        def result_to_dict(r: CoinPatternResult) -> dict:
            return {
                "points": [point_to_dict(p) for p in r.points],
                "num_cycles": r.num_cycles,
                "current_price": r.current_price,
                "current_date": r.current_date.isoformat() if r.current_date else None,
                "pattern_type": r.pattern_type,
                "trendline_target": r.trendline_target,
                "trendline_target_pct": r.trendline_target_pct,
                "fib_target": r.fib_target,
                "fib_target_pct": r.fib_target_pct,
                "dim_return_target": r.dim_return_target,
                "dim_return_target_pct": r.dim_return_target_pct,
                "hist_peak_target": r.hist_peak_target,
                "hist_peak_target_pct": r.hist_peak_target_pct,
                "hist_peak_is_absolute": r.hist_peak_is_absolute,
                "composite_target_pct": r.composite_target_pct,
            }

        data = {
            "generated_at": pd.Timestamp.now().isoformat(),
            "note": "Returns are calculated as % gain from current_price to target",
            "btc": None,
            "altcoins": {},
        }

        if btc_result:
            data["btc"] = result_to_dict(btc_result)

        for coin_id, result in coin_results.items():
            d = result_to_dict(result)
            d.update(
                {
                    "confidence": result.confidence,
                    "first_in_total2": (
                        result.first_in_total2.isoformat() if result.first_in_total2 else None
                    ),
                    "last_in_total2": (
                        result.last_in_total2.isoformat() if result.last_in_total2 else None
                    ),
                    "days_in_total2": result.days_in_total2,
                    "dim_return_factor": result.dim_return_factor,
                    "retracement_ratio": result.retracement_ratio,
                }
            )
            data["altcoins"][coin_id] = d

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        logger.info("Saved pattern analysis results to %s", output_path)
        return output_path

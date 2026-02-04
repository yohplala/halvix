"""
Cycle Pattern Analysis Module for Halvix.

Identifies min/max points within halving cycle windows and applies three
analysis methods to project price targets for the next cycle:

1. Log-Linear Trendline Regression
2. Fibonacci Extensions (127.2% level)
3. Diminishing Returns Model

IMPORTANT: This module uses TOTAL2 composition data to filter prices.
Only price data from dates when a coin was actually in TOTAL2 is used,
ensuring consistency with the TOTAL2 calculation methodology.

Returns are calculated as percentage gain from CURRENT PRICE to projected target.

Usage:
    from analysis.cycle_patterns import CyclePatternAnalyzer

    analyzer = CyclePatternAnalyzer()
    results = analyzer.analyze_all_coins()
    top_coins = analyzer.get_top_coins(n=9)
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import argrelextrema

from config import (
    BTC_CYCLE_BOTTOMS,
    BTC_CYCLE_PEAKS,
    DAYS_AFTER_HALVING,
    DAYS_BEFORE_HALVING,
    HALVING_DATES,
    PROCESSED_DIR,
    PROJECTED_5TH_HALVING,
    TOTAL2_COMPOSITION_FILE,
)
from data.cache import PriceDataCache

logger = logging.getLogger(__name__)


@dataclass
class CyclePoint:
    """A single min or max point within a cycle."""

    date: date
    price: float
    cycle_num: int
    point_type: str  # "min1", "max1", "min2", "max2"
    days_from_halving: int


@dataclass
class CoinPatternResult:
    """Analysis result for a single coin."""

    coin_id: str
    points: list[CyclePoint] = field(default_factory=list)
    num_cycles: int = 0

    # Method 1: Trendline projection
    trendline_target: float | None = None
    trendline_target_pct: float | None = None
    upper_slope: float | None = None
    lower_slope: float | None = None

    # Method 2: Fibonacci extension (127.2%)
    fib_target: float | None = None
    fib_target_pct: float | None = None

    # Method 3: Diminishing returns
    dim_return_target: float | None = None
    dim_return_target_pct: float | None = None
    dim_return_factor: float | None = None

    # Composite score (equal weight average of 3 methods)
    composite_target_pct: float | None = None

    # Current price for reference (returns are calculated vs this price)
    current_price: float | None = None
    current_date: date | None = None

    # Pattern classification
    pattern_type: str | None = None  # "falling_wedge", "rising_wedge", "channel"

    # Data quality
    confidence: str = "low"  # "low" (1 cycle), "medium" (2 cycles), "high" (3+ cycles)

    # TOTAL2 membership info
    first_in_total2: date | None = None
    last_in_total2: date | None = None
    days_in_total2: int = 0


@dataclass
class BTCPatternResult:
    """Analysis result for BTC (vs USD)."""

    points: list[CyclePoint] = field(default_factory=list)
    num_cycles: int = 0

    # Targets
    trendline_target: float | None = None
    trendline_target_pct: float | None = None
    fib_target: float | None = None
    fib_target_pct: float | None = None
    dim_return_target: float | None = None
    dim_return_target_pct: float | None = None
    composite_target_pct: float | None = None

    # Current price (returns are calculated vs this price)
    current_price: float | None = None
    current_date: date | None = None

    # Pattern
    pattern_type: str | None = None


class CyclePatternAnalyzer:
    """
    Analyzes cycle patterns for BTC and altcoins.

    For each halving cycle, identifies 4 points:
    - min1: minimum in pre-halving window [halving-550; halving]
    - max1: maximum in window [min1 date; halving]
    - max2: maximum in post-halving window [halving; halving+950]
    - min2: minimum in window [halving; max2 date]

    For cycle 5 (current, starting April 19, 2024), adds:
    - min1: minimum from halving to current date (or last available price if before halving)

    IMPORTANT: For altcoins, only price data from dates when the coin was
    actually in TOTAL2 is used. This ensures consistency with the TOTAL2
    calculation methodology and avoids using unverified price data.

    Then applies 3 projection methods and ranks by composite target.
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

        # Use cycles 2, 3, 4, and 5 (indices 1-4)
        # Cycle 5 is the current cycle starting April 19, 2024
        self.all_halvings = HALVING_DATES[1:]  # 2016, 2020, 2024 halvings

        # Load TOTAL2 composition for filtering
        self._total2_composition: pd.DataFrame | None = None
        self._total2_coins: set[str] | None = None

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
        """Get set of coins that have been in TOTAL2."""
        if self._total2_coins is not None:
            return self._total2_coins

        self._total2_coins = set()

        comp_df = self._load_total2_composition()
        if comp_df is not None:
            self._total2_coins = set(comp_df["coin_id"].str.lower().unique())
            logger.info("Found %d coins in TOTAL2 history", len(self._total2_coins))

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
        dates = set()
        for ts in coin_data["date"]:
            if hasattr(ts, "date"):
                dates.add(ts.date())
            else:
                dates.add(ts)

        return dates

    def _filter_to_total2_dates(
        self, df: pd.DataFrame, coin_id: str
    ) -> tuple[pd.DataFrame, date | None, date | None]:
        """
        Filter price DataFrame to only include dates when coin was in TOTAL2.

        Args:
            df: Price DataFrame with DatetimeIndex
            coin_id: Lowercase coin ID

        Returns:
            Tuple of (filtered DataFrame, first_date, last_date)
        """
        total2_dates = self._get_coin_total2_dates(coin_id)

        if not total2_dates:
            # No TOTAL2 data - return empty
            return df.iloc[:0], None, None

        # Filter to TOTAL2 dates
        # Note: df.index.date returns numpy array, so use np.isin
        mask = np.isin(df.index.date, list(total2_dates))
        filtered = df[mask]

        if filtered.empty:
            return filtered, None, None

        first_date = min(total2_dates)
        last_date = max(total2_dates)

        return filtered, first_date, last_date

    def _find_local_extrema(
        self,
        prices: pd.Series,
        order: int = 5,
    ) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
        """
        Find local minima and maxima in a price series.

        Args:
            prices: Price series with DatetimeIndex
            order: Number of points on each side to compare

        Returns:
            Tuple of (minima_dates, maxima_dates)
        """
        if len(prices) < order * 2 + 1:
            return pd.DatetimeIndex([]), pd.DatetimeIndex([])

        values = prices.values

        # Find indices of local extrema
        min_idx = argrelextrema(values, np.less_equal, order=order)[0]
        max_idx = argrelextrema(values, np.greater_equal, order=order)[0]

        minima_dates = prices.index[min_idx]
        maxima_dates = prices.index[max_idx]

        return minima_dates, maxima_dates

    def _find_cycle_points(
        self,
        df: pd.DataFrame,
        halving_date: date,
        cycle_num: int,
        is_current_cycle: bool = False,
    ) -> list[CyclePoint]:
        """
        Find the 4 characteristic points for a cycle.

        Points:
        - min1: minimum in [halving-550; halving]
        - max1: maximum in [min1 date; halving]
        - max2: maximum in [halving; halving+950]
        - min2: minimum in [halving; max2 date]

        For current cycle (cycle 5), only min1 is available:
        - min1: minimum from halving to current date

        Args:
            df: Price DataFrame with DatetimeIndex and 'close' column
            halving_date: The halving date for this cycle
            cycle_num: Cycle number (2, 3, 4, 5)
            is_current_cycle: If True, this is cycle 5 (in progress)

        Returns:
            List of CyclePoint objects (0-4 points depending on data availability)
        """
        points = []

        if df.empty:
            return points

        # Define windows
        pre_start = halving_date - timedelta(days=DAYS_BEFORE_HALVING)
        post_end = halving_date + timedelta(days=DAYS_AFTER_HALVING)

        # For current cycle, we only look for min1 since the last BTC peak
        if is_current_cycle:
            # Cycle 5: Find min1 from last BTC peak (Oct 2025) onwards
            # This is the bottom after the cycle 4 peak, not since halving
            last_btc_peak = BTC_CYCLE_PEAKS[-1] if BTC_CYCLE_PEAKS else halving_date
            post_peak_mask = df.index.date >= last_btc_peak
            post_peak_data = df[post_peak_mask]

            if not post_peak_data.empty:
                # min1: minimum since last BTC peak
                min1_idx = post_peak_data["close"].idxmin()
                min1_price = post_peak_data.loc[min1_idx, "close"]
                min1_date = min1_idx.date() if hasattr(min1_idx, "date") else min1_idx

                points.append(
                    CyclePoint(
                        date=min1_date,
                        price=float(min1_price),
                        cycle_num=cycle_num,
                        point_type="min1",
                        days_from_halving=(min1_date - halving_date).days,
                    )
                )
            else:
                # No data after last peak - use last available price
                if not df.empty:
                    last_idx = df.index[-1]
                    last_price = df.loc[last_idx, "close"]
                    last_date = last_idx.date() if hasattr(last_idx, "date") else last_idx

                    points.append(
                        CyclePoint(
                            date=last_date,
                            price=float(last_price),
                            cycle_num=cycle_num,
                            point_type="min1",
                            days_from_halving=(last_date - halving_date).days,
                        )
                    )

            return points

        # Regular cycle (completed or mostly complete)
        # Pre-halving window: find min1
        pre_mask = (df.index.date >= pre_start) & (df.index.date < halving_date)
        pre_data = df[pre_mask]

        if pre_data.empty:
            return points

        # min1: absolute minimum in pre-halving window
        min1_idx = pre_data["close"].idxmin()
        min1_price = pre_data.loc[min1_idx, "close"]
        min1_date = min1_idx.date() if hasattr(min1_idx, "date") else min1_idx

        points.append(
            CyclePoint(
                date=min1_date,
                price=float(min1_price),
                cycle_num=cycle_num,
                point_type="min1",
                days_from_halving=(min1_date - halving_date).days,
            )
        )

        # max1: maximum between min1 and halving
        max1_mask = (df.index >= min1_idx) & (df.index.date < halving_date)
        max1_data = df[max1_mask]

        if not max1_data.empty:
            max1_idx = max1_data["close"].idxmax()
            max1_price = max1_data.loc[max1_idx, "close"]
            max1_date = max1_idx.date() if hasattr(max1_idx, "date") else max1_idx

            points.append(
                CyclePoint(
                    date=max1_date,
                    price=float(max1_price),
                    cycle_num=cycle_num,
                    point_type="max1",
                    days_from_halving=(max1_date - halving_date).days,
                )
            )

        # Post-halving window: find max2 first
        post_mask = (df.index.date >= halving_date) & (df.index.date <= post_end)
        post_data = df[post_mask]

        if post_data.empty:
            return points

        # max2: absolute maximum in post-halving window
        max2_idx = post_data["close"].idxmax()
        max2_price = post_data.loc[max2_idx, "close"]
        max2_date = max2_idx.date() if hasattr(max2_idx, "date") else max2_idx

        # min2: minimum between halving and max2
        min2_mask = (df.index.date >= halving_date) & (df.index <= max2_idx)
        min2_data = df[min2_mask]

        if not min2_data.empty:
            min2_idx = min2_data["close"].idxmin()
            min2_price = min2_data.loc[min2_idx, "close"]
            min2_date = min2_idx.date() if hasattr(min2_idx, "date") else min2_idx

            points.append(
                CyclePoint(
                    date=min2_date,
                    price=float(min2_price),
                    cycle_num=cycle_num,
                    point_type="min2",
                    days_from_halving=(min2_date - halving_date).days,
                )
            )

        points.append(
            CyclePoint(
                date=max2_date,
                price=float(max2_price),
                cycle_num=cycle_num,
                point_type="max2",
                days_from_halving=(max2_date - halving_date).days,
            )
        )

        return points

    def _fit_log_trendlines(
        self,
        points: list[CyclePoint],
    ) -> tuple[float | None, float | None, float | None, float | None]:
        """
        Fit log-linear trendlines through cycle min and max points.

        Returns:
            Tuple of (upper_slope, upper_intercept, lower_slope, lower_intercept)
            or (None, None, None, None) if insufficient data
        """
        # Separate peaks (max1, max2) and troughs (min1, min2)
        peaks = [p for p in points if "max" in p.point_type]
        troughs = [p for p in points if "min" in p.point_type]

        if len(peaks) < 2 or len(troughs) < 2:
            return None, None, None, None

        # Filter out any points with zero or negative prices
        peaks = [p for p in peaks if p.price > 0]
        troughs = [p for p in troughs if p.price > 0]

        if len(peaks) < 2 or len(troughs) < 2:
            return None, None, None, None

        # Require minimum data span for BOTH peaks and troughs individually
        # This prevents fitting a line on just 2 points and extrapolating years ahead
        # Require at least 1200 days (~3.3 years) span for each trendline
        # This ensures we're capturing multi-cycle patterns, not just within-cycle moves
        MIN_TRENDLINE_SPAN_DAYS = 1200
        peak_dates = [p.date for p in peaks]
        trough_dates = [p.date for p in troughs]
        peak_span = (max(peak_dates) - min(peak_dates)).days
        trough_span = (max(trough_dates) - min(trough_dates)).days

        if peak_span < MIN_TRENDLINE_SPAN_DAYS:
            logger.debug(
                "Peak data span too short for trendline: %d days (need %d)",
                peak_span,
                MIN_TRENDLINE_SPAN_DAYS,
            )
            return None, None, None, None

        if trough_span < MIN_TRENDLINE_SPAN_DAYS:
            logger.debug(
                "Trough data span too short for trendline: %d days (need %d)",
                trough_span,
                MIN_TRENDLINE_SPAN_DAYS,
            )
            return None, None, None, None

        # Convert to arrays with days as x-axis (days from first halving date)
        # Use HALVING_DATES[1] (2016) as reference
        reference_date = HALVING_DATES[1]

        peak_x = np.array([(p.date - reference_date).days for p in peaks]).reshape(-1, 1)
        peak_y = np.log10([p.price for p in peaks])

        trough_x = np.array([(p.date - reference_date).days for p in troughs]).reshape(-1, 1)
        trough_y = np.log10([p.price for p in troughs])

        try:
            # Simple linear regression on log-transformed prices
            upper_slope = np.polyfit(peak_x.flatten(), peak_y, 1)[0]
            upper_fit = np.polyfit(peak_x.flatten(), peak_y, 1)

            lower_slope = np.polyfit(trough_x.flatten(), trough_y, 1)[0]
            lower_fit = np.polyfit(trough_x.flatten(), trough_y, 1)

            return float(upper_slope), float(upper_fit[1]), float(lower_slope), float(lower_fit[1])
        except Exception as e:
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
        if log_price > 300 or log_price < -300:
            logger.debug("Trendline projection overflow: log_price=%.2f", log_price)
            return None

        return 10**log_price

    def _calculate_fib_extension(
        self,
        points: list[CyclePoint],
        level: float = 1.272,
    ) -> float | None:
        """
        Calculate Fibonacci extension target.

        Uses the most recent complete cycle:
        A = cycle min (min1 or min2)
        B = cycle max (max2)
        C = next cycle min (min1)

        Extension = C + (B - A) * level

        Args:
            points: All cycle points
            level: Fibonacci level (default 127.2%)

        Returns:
            Projected price or None if insufficient data
        """
        # Need at least 2 cycles for Fibonacci
        cycles = sorted({p.cycle_num for p in points})

        if len(cycles) < 2:
            # Single cycle: use move from min1 to max2, project from max2
            cycle_points = [p for p in points if p.cycle_num == cycles[0]]
            min_points = [p for p in cycle_points if "min" in p.point_type]
            max_points = [p for p in cycle_points if "max" in p.point_type]

            if not min_points or not max_points:
                return None

            # Get earliest min and highest max
            a = min(min_points, key=lambda p: p.date).price
            b = max(max_points, key=lambda p: p.price).price
            c = min(min_points, key=lambda p: p.price).price  # Use lowest min as C

            move = b - a
            return c + move * level

        # Use last complete cycle
        latest_cycle = max(cycles)
        prev_cycle = max(c for c in cycles if c < latest_cycle)

        # Get max2 from previous cycle
        prev_max2 = None
        for p in points:
            if p.cycle_num == prev_cycle and p.point_type == "max2":
                prev_max2 = p
                break

        # Get min1 from previous cycle
        prev_min1 = None
        for p in points:
            if p.cycle_num == prev_cycle and p.point_type == "min1":
                prev_min1 = p
                break

        # Get min1 from latest cycle (retracement point)
        latest_min1 = None
        for p in points:
            if p.cycle_num == latest_cycle and p.point_type == "min1":
                latest_min1 = p
                break

        if prev_min1 and prev_max2 and latest_min1:
            a = prev_min1.price
            b = prev_max2.price
            c = latest_min1.price
            move = b - a
            return c + move * level

        return None

    def _calculate_diminishing_return(
        self,
        points: list[CyclePoint],
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
            cycle_points = [p for p in points if p.cycle_num == cycle]
            min_points = [p for p in cycle_points if "min" in p.point_type]
            max_points = [p for p in cycle_points if "max" in p.point_type]

            if min_points and max_points:
                min_price = min(p.price for p in min_points)
                max_price = max(p.price for p in max_points)
                gain_ratio = max_price / min_price if min_price > 0 else 0
                gains.append((cycle, gain_ratio))

        if not gains:
            return None, None

        # If only one cycle, assume 50% diminishing (conservative)
        if len(gains) == 1:
            last_gain_ratio = gains[0][1]
            dim_factor = 0.5
            next_gain_ratio = last_gain_ratio * dim_factor

            # Get latest min point
            latest_min = None
            for p in sorted(points, key=lambda x: x.date, reverse=True):
                if "min" in p.point_type:
                    latest_min = p
                    break

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
            avg_dim_factor = np.mean(dim_factors)
            last_gain_ratio = gains[-1][1]
            next_gain_ratio = last_gain_ratio * avg_dim_factor

            # Get latest min point
            latest_min = None
            for p in sorted(points, key=lambda x: x.date, reverse=True):
                if "min" in p.point_type:
                    latest_min = p
                    break

            if latest_min:
                target = latest_min.price * next_gain_ratio
                return target, float(avg_dim_factor)

        return None, None

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

        if slope_diff < 0.00001:
            return "channel"
        elif upper_slope < lower_slope:
            return "falling_wedge"
        else:
            return "rising_wedge"

    def analyze_btc(self) -> BTCPatternResult | None:
        """
        Analyze BTC/USD pattern using predefined peaks and bottoms.

        Returns:
            BTCPatternResult or None if data unavailable
        """
        # Load BTC-USD data
        btc_df = self.price_cache.get_prices("btc", "USD")

        if btc_df is None or btc_df.empty:
            logger.warning("BTC-USD data not available")
            return None

        result = BTCPatternResult()

        # Use hardcoded BTC peaks and bottoms (verified data)
        # Map to CyclePoints using cycles 2, 3, 4
        # Get prices at known peak/bottom dates
        for i, (halving_date, peak_date) in enumerate(
            zip(HALVING_DATES[1:], BTC_CYCLE_PEAKS, strict=False)
        ):
            cycle_num = i + 2  # Cycles 2, 3, 4

            # Find the bottom that precedes this halving
            for bottom_date in BTC_CYCLE_BOTTOMS:
                if bottom_date < halving_date:
                    # Check if this bottom is in the pre-halving window
                    window_start = halving_date - timedelta(days=DAYS_BEFORE_HALVING)
                    if window_start <= bottom_date < halving_date:
                        # Get price at bottom date
                        try:
                            bottom_ts = pd.Timestamp(bottom_date)
                            if bottom_ts in btc_df.index:
                                price = float(btc_df.loc[bottom_ts, "close"])
                            else:
                                # Find nearest date
                                nearest = btc_df.index[btc_df.index.get_indexer([bottom_ts], method="nearest")[0]]
                                price = float(btc_df.loc[nearest, "close"])

                            result.points.append(
                                CyclePoint(
                                    date=bottom_date,
                                    price=price,
                                    cycle_num=cycle_num,
                                    point_type="min1",
                                    days_from_halving=(bottom_date - halving_date).days,
                                )
                            )
                        except Exception as e:
                            logger.debug("Could not get price for %s: %s", bottom_date, e)

            # Get peak price
            try:
                peak_ts = pd.Timestamp(peak_date)
                if peak_ts in btc_df.index:
                    price = float(btc_df.loc[peak_ts, "close"])
                else:
                    nearest = btc_df.index[btc_df.index.get_indexer([peak_ts], method="nearest")[0]]
                    price = float(btc_df.loc[nearest, "close"])

                result.points.append(
                    CyclePoint(
                        date=peak_date,
                        price=price,
                        cycle_num=cycle_num,
                        point_type="max2",
                        days_from_halving=(peak_date - halving_date).days,
                    )
                )
            except Exception as e:
                logger.debug("Could not get price for %s: %s", peak_date, e)

        # Add cycle 5 first point (min1 since last BTC peak - October 2025)
        halving_5 = HALVING_DATES[3]  # 2024 halving
        last_btc_peak = BTC_CYCLE_PEAKS[-1] if BTC_CYCLE_PEAKS else halving_5
        post_peak_mask = btc_df.index.date >= last_btc_peak
        post_peak_data = btc_df[post_peak_mask]

        if not post_peak_data.empty:
            min5_idx = post_peak_data["close"].idxmin()
            min5_price = post_peak_data.loc[min5_idx, "close"]
            min5_date = min5_idx.date() if hasattr(min5_idx, "date") else min5_idx

            result.points.append(
                CyclePoint(
                    date=min5_date,
                    price=float(min5_price),
                    cycle_num=5,
                    point_type="min1",
                    days_from_halving=(min5_date - halving_5).days,
                )
            )

        if not result.points:
            logger.warning("No BTC cycle points found")
            return None

        result.num_cycles = len({p.cycle_num for p in result.points})

        # Get current price (returns are calculated vs this price)
        result.current_price = float(btc_df["close"].iloc[-1])
        result.current_date = btc_df.index[-1].date()

        # Fit trendlines
        upper_slope, upper_int, lower_slope, lower_int = self._fit_log_trendlines(result.points)

        if upper_slope is not None:
            result.upper_slope = upper_slope
            result.lower_slope = lower_slope
            result.pattern_type = self._classify_pattern(upper_slope, lower_slope)

            # Project to expected peak of cycle 5
            # Approximate: halving + 550 days (typical peak timing)
            target_date = PROJECTED_5TH_HALVING + timedelta(days=550)
            target = self._project_trendline_target(upper_slope, upper_int, target_date)
            if target is not None:
                result.trendline_target = target
                result.trendline_target_pct = (target / result.current_price - 1) * 100

        # Fibonacci extension
        fib_target = self._calculate_fib_extension(result.points)
        if fib_target:
            result.fib_target = fib_target
            result.fib_target_pct = (fib_target / result.current_price - 1) * 100

        # Diminishing returns
        dim_target, dim_factor = self._calculate_diminishing_return(result.points)
        if dim_target:
            result.dim_return_target = dim_target
            result.dim_return_target_pct = (dim_target / result.current_price - 1) * 100
            result.dim_return_factor = dim_factor

        # Composite target (equal weight)
        pcts = [
            p
            for p in [
                result.trendline_target_pct,
                result.fib_target_pct,
                result.dim_return_target_pct,
            ]
            if p is not None
        ]
        if pcts:
            result.composite_target_pct = np.mean(pcts)

        return result

    def analyze_coin(self, coin_id: str) -> CoinPatternResult | None:
        """
        Analyze pattern for a single altcoin vs BTC.

        IMPORTANT: Only uses price data from dates when the coin was
        actually in TOTAL2. This ensures consistency with TOTAL2 methodology.

        Args:
            coin_id: Lowercase coin ID (e.g., "eth")

        Returns:
            CoinPatternResult or None if insufficient data
        """
        # Load coin price data (vs BTC)
        df = self.price_cache.get_prices(coin_id, "BTC")

        if df is None or df.empty:
            return None

        # Filter to only TOTAL2 dates
        filtered_df, first_total2, last_total2 = self._filter_to_total2_dates(df, coin_id)

        if filtered_df.empty:
            logger.debug("No TOTAL2 data for %s", coin_id)
            return None

        # Require recent TOTAL2 data for meaningful projections
        # Coins must be currently in TOTAL2 (within past week) to have valid current price
        if last_total2 is not None:
            days_since_total2 = (date.today() - last_total2).days
            if days_since_total2 > 7:
                logger.debug(
                    "%s: Last in TOTAL2 was %d days ago (threshold: 7), skipping",
                    coin_id,
                    days_since_total2,
                )
                return None

        result = CoinPatternResult(coin_id=coin_id)
        result.first_in_total2 = first_total2
        result.last_in_total2 = last_total2
        result.days_in_total2 = len(self._get_coin_total2_dates(coin_id))

        # Find points for each halving cycle
        # Cycle 2 = 2016, Cycle 3 = 2020, Cycle 4 = 2024, Cycle 5 = current
        for halving_date in self.all_halvings:
            cycle_num = HALVING_DATES.index(halving_date) + 1
            is_current = halving_date == HALVING_DATES[3]  # 2024 halving = cycle 5

            cycle_points = self._find_cycle_points(
                filtered_df, halving_date, cycle_num, is_current_cycle=is_current
            )
            result.points.extend(cycle_points)

        if not result.points:
            return None

        result.num_cycles = len({p.cycle_num for p in result.points})

        # Check minimum cycles requirement
        if result.num_cycles < self.min_cycles:
            return None

        # Set confidence level
        if result.num_cycles >= 3:
            result.confidence = "high"
        elif result.num_cycles >= 2:
            result.confidence = "medium"
        else:
            result.confidence = "low"

        # Get current price (returns are calculated vs this price)
        # Use last price in TOTAL2-filtered data
        result.current_price = float(filtered_df["close"].iloc[-1])
        result.current_date = filtered_df.index[-1].date()

        # Fit trendlines (need at least 2 points each for min/max)
        upper_slope, upper_int, lower_slope, lower_int = self._fit_log_trendlines(result.points)

        if upper_slope is not None:
            result.upper_slope = upper_slope
            result.lower_slope = lower_slope
            result.pattern_type = self._classify_pattern(upper_slope, lower_slope)

            # Project to cycle 5 peak (halving + 550 days)
            target_date = PROJECTED_5TH_HALVING + timedelta(days=550)
            target = self._project_trendline_target(upper_slope, upper_int, target_date)
            if target is not None:
                result.trendline_target = target
                result.trendline_target_pct = (target / result.current_price - 1) * 100

        # Fibonacci extension
        fib_target = self._calculate_fib_extension(result.points)
        if fib_target:
            result.fib_target = fib_target
            result.fib_target_pct = (fib_target / result.current_price - 1) * 100

        # Diminishing returns
        dim_target, dim_factor = self._calculate_diminishing_return(result.points)
        if dim_target:
            result.dim_return_target = dim_target
            result.dim_return_target_pct = (dim_target / result.current_price - 1) * 100
            result.dim_return_factor = dim_factor

        # Composite target (equal weight of available methods)
        # No cap - show raw projections to expose any issues
        pcts = [
            p
            for p in [result.trendline_target_pct, result.fib_target_pct, result.dim_return_target_pct]
            if p is not None
        ]

        if pcts:
            result.composite_target_pct = np.mean(pcts)
        else:
            result.composite_target_pct = None

        return result

    def analyze_all_coins(
        self,
        filter_total2: bool = True,
        show_progress: bool = True,
    ) -> dict[str, CoinPatternResult]:
        """
        Analyze all available altcoins.

        Args:
            filter_total2: If True, only analyze coins that have been in TOTAL2
            show_progress: If True, show progress bar

        Returns:
            Dictionary mapping coin_id to CoinPatternResult
        """
        # Get list of coins to analyze
        cached_coins = self.price_cache.list_cached_coins("BTC")

        if filter_total2:
            total2_coins = self._get_total2_coins()
            coins_to_analyze = [c for c in cached_coins if c in total2_coins]
            logger.info(
                "Analyzing %d coins (filtered to TOTAL2 from %d cached)",
                len(coins_to_analyze),
                len(cached_coins),
            )
        else:
            coins_to_analyze = cached_coins
            logger.info("Analyzing %d coins", len(coins_to_analyze))

        results = {}

        if show_progress:
            try:
                from tqdm import tqdm

                coins_iter = tqdm(coins_to_analyze, desc="Analyzing patterns")
            except ImportError:
                coins_iter = coins_to_analyze
        else:
            coins_iter = coins_to_analyze

        for coin_id in coins_iter:
            result = self.analyze_coin(coin_id)
            if result and result.composite_target_pct is not None:
                results[coin_id] = result

        logger.info("Successfully analyzed %d coins with valid projections", len(results))
        return results

    def get_top_coins(
        self,
        results: dict[str, CoinPatternResult],
        n: int = 9,
    ) -> list[CoinPatternResult]:
        """
        Get top N coins by composite target percentage.

        Args:
            results: Dictionary of coin results
            n: Number of top coins to return

        Returns:
            List of top N CoinPatternResult sorted by composite_target_pct
        """
        # Filter to coins with valid composite target
        valid = [r for r in results.values() if r.composite_target_pct is not None]

        # Sort by composite target (descending)
        sorted_results = sorted(valid, key=lambda x: x.composite_target_pct or 0, reverse=True)

        return sorted_results[:n]

    def save_results(
        self,
        btc_result: BTCPatternResult | None,
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
            return {
                "date": p.date.isoformat(),
                "price": p.price,
                "cycle_num": p.cycle_num,
                "point_type": p.point_type,
                "days_from_halving": p.days_from_halving,
            }

        data = {
            "generated_at": pd.Timestamp.now().isoformat(),
            "note": "Returns are calculated as % gain from current_price to target",
            "btc": None,
            "altcoins": {},
        }

        if btc_result:
            data["btc"] = {
                "points": [point_to_dict(p) for p in btc_result.points],
                "num_cycles": btc_result.num_cycles,
                "current_price": btc_result.current_price,
                "current_date": btc_result.current_date.isoformat() if btc_result.current_date else None,
                "pattern_type": btc_result.pattern_type,
                "trendline_target": btc_result.trendline_target,
                "trendline_target_pct": btc_result.trendline_target_pct,
                "fib_target": btc_result.fib_target,
                "fib_target_pct": btc_result.fib_target_pct,
                "dim_return_target": btc_result.dim_return_target,
                "dim_return_target_pct": btc_result.dim_return_target_pct,
                "composite_target_pct": btc_result.composite_target_pct,
            }

        for coin_id, result in coin_results.items():
            data["altcoins"][coin_id] = {
                "points": [point_to_dict(p) for p in result.points],
                "num_cycles": result.num_cycles,
                "confidence": result.confidence,
                "first_in_total2": result.first_in_total2.isoformat() if result.first_in_total2 else None,
                "last_in_total2": result.last_in_total2.isoformat() if result.last_in_total2 else None,
                "days_in_total2": result.days_in_total2,
                "current_price": result.current_price,
                "current_date": result.current_date.isoformat() if result.current_date else None,
                "pattern_type": result.pattern_type,
                "trendline_target": result.trendline_target,
                "trendline_target_pct": result.trendline_target_pct,
                "fib_target": result.fib_target,
                "fib_target_pct": result.fib_target_pct,
                "dim_return_target": result.dim_return_target,
                "dim_return_target_pct": result.dim_return_target_pct,
                "dim_return_factor": result.dim_return_factor,
                "composite_target_pct": result.composite_target_pct,
            }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        logger.info("Saved pattern analysis results to %s", output_path)
        return output_path

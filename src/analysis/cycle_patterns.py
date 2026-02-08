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
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from config import (
    COMPOSITE_WEIGHT_PROFILES,
    CURRENT_CYCLE_MIN1_APPROX_DAYS_BEFORE_HALVING,
    DEFAULT_DIMINISHING_FACTOR,
    DEFAULT_FIBONACCI_LEVEL,
    DIM_RETURN_MIN_GAIN_RATIO,
    EXPECTED_PEAK_DAYS_AFTER_HALVING,
    GOLDEN_RETRACEMENT_LEVEL,
    HALVING_DATES,
    MAJOR_POINT_WEIGHT,
    MAX2_PRE_HALVING_BUFFER_DAYS,
    MAX_RETRACEMENT_LEVEL,
    MIN_COIN_AGE_DAYS,
    MIN_LOWER_SLOPE,
    MIN_RETRACEMENT_LEVEL,
    MIN_UNIQUE_PRICES,
    MINOR_POINT_WEIGHT,
    PROCESSED_DIR,
    RETRACEMENT_PENALTY_AT_MAX,
    TOTAL2_COMPOSITION_FILE,
    TOTAL2_LOOKBACK_YEARS,
    TRENDLINE_LOG_PRICE_LIMIT,
    TRENDLINE_RECENCY_DECAY,
    UNIQUE_PRICES_WINDOW_DAYS,
)
from data.cache import PriceDataCache
from data.price_filters import detect_symbol_replacement
from utils.logging import get_logger

logger = get_logger(__name__)

PointType = Literal["min1", "max1", "min2", "max2"]
Confidence = Literal["low", "medium", "high"]


def _to_date(dt: date) -> date:
    """Convert a pandas Timestamp or datetime to a plain date object."""
    return dt.date() if hasattr(dt, "date") else dt


def fib_retracement_ratio(a: float, b: float, c: float) -> float | None:
    """
    Log-space Fibonacci retracement ratio.

    Measures how much of the move from A to B has been retraced to C.

    Args:
        a: Reference low (must be positive, below b)
        b: Peak (must be positive, above a)
        c: Retracement point (must be positive)

    Returns:
        Retracement ratio in log-space:
        - 0.0 = C at peak (no retracement)
        - 1.0 = C at reference low (full retracement)
        - >1.0 = C below reference low
        None if inputs are invalid (non-positive or b <= a).
    """
    if a <= 0 or b <= 0 or c <= 0 or b <= a:
        return None
    return math.log10(b / c) / math.log10(b / a)


@dataclass
class CyclePoint:
    """A single min or max point within a cycle."""

    date: date
    price: float
    cycle_num: int
    point_type: PointType
    days_from_halving: int


@dataclass
class CoinPatternResult:
    """
    Analysis result for a single coin.

    Note on mutability: The `points` field uses `field(default_factory=list)` to avoid
    the mutable default argument pitfall in Python dataclasses. This ensures each
    instance gets its own empty list instead of sharing a single list across all instances.
    """

    coin_id: str
    points: list[CyclePoint] = field(default_factory=list)
    num_cycles: int = 0

    # Method 1: Trendline projection
    trendline_target: float | None = None
    trendline_target_pct: float | None = None
    upper_slope: float | None = None
    lower_slope: float | None = None
    upper_intercept: float | None = None  # For trendline visualization
    lower_intercept: float | None = None  # For trendline visualization

    # Method 2: Fibonacci extension (100%)
    fib_target: float | None = None
    fib_target_pct: float | None = None

    # Method 3: Diminishing returns
    dim_return_target: float | None = None
    dim_return_target_pct: float | None = None
    dim_return_factor: float | None = None

    # Method 4: Historical peak
    hist_peak_target: float | None = None
    hist_peak_target_pct: float | None = None
    hist_peak_is_absolute: bool | None = None  # True if prev cycle max2 was absolute max

    # Composite score (weighted average of available methods)
    composite_target_pct: float | None = None

    # Retracement: how much of the last cycle gain has been given back (log-space, 0-1)
    retracement_ratio: float | None = None

    # Current price for reference (returns are calculated vs this price)
    current_price: float | None = None
    current_date: date | None = None

    # Pattern classification
    pattern_type: str | None = None  # "falling_wedge", "rising_wedge", "channel"

    # Data quality
    confidence: Confidence = "low"

    # TOTAL2 membership info
    first_in_total2: date | None = None
    last_in_total2: date | None = None
    days_in_total2: int = 0

    # Price data info
    first_price_date: date | None = None  # First date with price data (for age filtering)
    unique_price_count: int = 0  # Number of unique price values (filters staircase patterns)

    # Rank in trendline prediction ranking (set after sorting)
    rank: int | None = None


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

    def _filter_to_total2_dates(
        self, df: pd.DataFrame, coin_id: str
    ) -> tuple[pd.DataFrame, date | None, date | None]:
        """
        Get TOTAL2 membership dates for a coin.

        Note: This method is kept for compatibility but the pattern analyzer
        now uses full price data with filtering tools applied.

        Args:
            df: Price DataFrame with DatetimeIndex
            coin_id: Lowercase coin ID

        Returns:
            Tuple of (original DataFrame, first_total2_date, last_total2_date)
        """
        total2_dates = self._get_coin_total2_dates(coin_id)

        if not total2_dates:
            # No TOTAL2 data - return empty
            return df.iloc[:0], None, None

        first_date = min(total2_dates)
        last_date = max(total2_dates)

        # Return full DataFrame (not filtered to TOTAL2 dates)
        # Pattern analysis uses full price history to find true min/max
        return df, first_date, last_date

    def _find_all_cycle_points(self, df: pd.DataFrame) -> list[CyclePoint]:
        """
        Find cycle points using segment-based detection across all halvings.

        Processes segments between consecutive halvings. Within each segment
        [H[n-1], H[n]], identifies up to 4 points:

        - max2(n-1): max price in segment (always exists) — cycle n-1
        - min2(n-1): min price in [H[n-1], max2 date] (optional) — cycle n-1
        - min1(n): min price in [max2 date, H[n]] (structural) — cycle n
        - max1(n): max price in [min1 date, H[n]] (optional) — cycle n

        Uses a 3-pass algorithm:
          Pass 1: Find max2 for all segments
          Pass 2: Find min2 candidates for all segments
          Pass 3: Validate min2/min1/max1 sequentially, apply merging

        Args:
            df: Price DataFrame with DatetimeIndex and 'close' column

        Returns:
            List of CyclePoint objects with correct cycle_num and days_from_halving.
        """
        if df.empty:
            return []

        halvings = self.all_halvings
        last_price_date = _to_date(df.index[-1])

        # Build segment metadata
        segments: list[dict | None] = []
        for s in range(len(halvings) - 1):
            seg_start = halvings[s]
            seg_end = halvings[s + 1]
            is_last = s == len(halvings) - 2
            effective_end = min(seg_end, last_price_date) if is_last else seg_end

            # Cycle numbers: max2/min2 belong to prev_cycle, min1/max1 to curr_cycle
            # halvings[0] = HALVING_DATES[1] = 2nd halving = cycle 2
            prev_cycle = s + 2  # cycle of seg_start halving
            curr_cycle = s + 3  # cycle of seg_end halving

            seg_mask = (df.index.date >= seg_start) & (df.index.date <= effective_end)
            seg_data = df[seg_mask]
            valid_seg = seg_data[seg_data["close"] > 0] if not seg_data.empty else seg_data

            if valid_seg.empty:
                segments.append(None)
                continue

            segments.append(
                {
                    "seg_start": seg_start,
                    "seg_end": seg_end,
                    "effective_end": effective_end,
                    "prev_cycle": prev_cycle,
                    "curr_cycle": curr_cycle,
                    "data": seg_data,
                    "valid_data": valid_seg,
                    "is_last": is_last,
                }
            )

        # --- Pass 1: Find max2 for each segment ---
        # Buffer excludes the pre-halving rally zone from max2 search.
        # This prevents the pre-halving pump (structurally max1) from being
        # picked as the cycle peak when it exceeds the actual cycle top.
        buffer = timedelta(days=MAX2_PRE_HALVING_BUFFER_DAYS)
        for seg in segments:
            if seg is None:
                continue
            max2_search_end = min(seg["effective_end"], seg["seg_end"] - buffer)
            if max2_search_end <= seg["seg_start"]:
                # Buffer consumed entire segment — fall back to full range
                max2_search_end = seg["effective_end"]
            max2_mask = seg["valid_data"].index.date <= max2_search_end
            max2_data = seg["valid_data"][max2_mask]
            if max2_data.empty:
                max2_data = seg["valid_data"]  # fallback: use all data
            max2_idx = max2_data["close"].idxmax()
            seg["max2_date"] = _to_date(max2_idx)
            seg["max2_price"] = float(max2_data.loc[max2_idx, "close"])
            seg["max2_idx"] = max2_idx

        # --- Pass 2: Find min2 candidates (min in [seg_start, max2_date]) ---
        for seg in segments:
            if seg is None or "max2_idx" not in seg:
                continue
            min2_mask = (seg["valid_data"].index.date >= seg["seg_start"]) & (
                seg["valid_data"].index <= seg["max2_idx"]
            )
            min2_data = seg["valid_data"][min2_mask]
            if not min2_data.empty:
                min2_idx = min2_data["close"].idxmin()
                seg["min2_date"] = _to_date(min2_idx)
                seg["min2_price"] = float(min2_data.loc[min2_idx, "close"])
            else:
                seg["min2_date"] = None
                seg["min2_price"] = None

        # --- Pass 3: Sequential validation and min1/max1 detection ---
        points: list[CyclePoint] = []
        prev_min1_price: float | None = None
        prev_min1_point: CyclePoint | None = None
        prev_had_max1 = True  # assume alternation OK for first segment
        prev_max1_date: date | None = None

        for s_idx, seg in enumerate(segments):
            if seg is None:
                continue

            prev_cycle = seg["prev_cycle"]
            curr_cycle = seg["curr_cycle"]
            seg_start_halving = halvings[s_idx]  # halving for prev_cycle
            seg_end_halving = halvings[s_idx + 1]  # halving for curr_cycle

            # -- max2 always exists --
            points.append(
                CyclePoint(
                    date=seg["max2_date"],
                    price=seg["max2_price"],
                    cycle_num=prev_cycle,
                    point_type="max2",
                    days_from_halving=(seg["max2_date"] - seg_start_halving).days,
                )
            )

            # -- Extend min2 search to prev max1 when applicable --
            # The dip between max1 and max2 can cross the halving boundary
            # (e.g., COVID crash on 2020-03-18 is before H3 but is the true
            # structural min2 for cycle 3). Search the original df directly.
            if prev_had_max1 and prev_max1_date is not None:
                ext_mask = (
                    (df.index.date >= prev_max1_date)
                    & (df.index.date <= seg["max2_date"])
                    & (df["close"] > 0)
                )
                ext_data = df[ext_mask]
                if not ext_data.empty:
                    ext_min_idx = ext_data["close"].idxmin()
                    ext_min_price = float(ext_data.loc[ext_min_idx, "close"])
                    ext_min_date = _to_date(ext_min_idx)
                    if seg["min2_price"] is None or ext_min_price < seg["min2_price"]:
                        seg["min2_date"] = ext_min_date
                        seg["min2_price"] = ext_min_price

            # -- Validate min2 --
            min2_valid = False
            if seg["min2_price"] is not None:
                if not prev_had_max1 and s_idx > 0:
                    # Alternation rule: prev segment ended with min (no max1),
                    # so this segment should start with max, not min.
                    min2_valid = False
                elif prev_min1_price is not None and s_idx > 0:
                    ratio = fib_retracement_ratio(
                        prev_min1_price, seg["max2_price"], seg["min2_price"]
                    )
                    min2_valid = ratio is not None and ratio >= MIN_RETRACEMENT_LEVEL
                else:
                    # First segment or no prior context.
                    # Only accept if min2 is NOT at the very start of
                    # available data — otherwise it's just the token's
                    # launch price, not a structural dip.
                    first_available = _to_date(df.index[0])
                    min2_valid = (seg["min2_date"] - first_available).days > 7

            if min2_valid:
                points.append(
                    CyclePoint(
                        date=seg["min2_date"],
                        price=seg["min2_price"],
                        cycle_num=prev_cycle,
                        point_type="min2",
                        days_from_halving=(seg["min2_date"] - seg_start_halving).days,
                    )
                )

            # -- Merge adjacent maxes when no min2 separates them --
            # When prev segment had max1 and current has no valid min2,
            # the two peaks are one formation. Keep the higher as max2.
            if not min2_valid and prev_had_max1 and prev_max1_date is not None:
                prev_max1_pts = [
                    p for p in points if p.point_type == "max1" and p.date == prev_max1_date
                ]
                if prev_max1_pts:
                    prev_max1_pt = prev_max1_pts[0]
                    # Remove both old max1 and old max2 from points
                    points = [
                        p
                        for p in points
                        if not (p.cycle_num == prev_cycle and p.point_type in ("max1", "max2"))
                    ]
                    if prev_max1_pt.price > seg["max2_price"]:
                        # max1 is higher — use its date/price for merged max2
                        points.append(
                            CyclePoint(
                                date=prev_max1_pt.date,
                                price=prev_max1_pt.price,
                                cycle_num=prev_cycle,
                                point_type="max2",
                                days_from_halving=(prev_max1_pt.date - seg_start_halving).days,
                            )
                        )
                        seg["max2_price"] = prev_max1_pt.price
                        # Keep seg["max2_date"] at the later date so min1
                        # search starts after the entire peak formation
                    else:
                        # max2 is higher — re-add it as-is
                        points.append(
                            CyclePoint(
                                date=seg["max2_date"],
                                price=seg["max2_price"],
                                cycle_num=prev_cycle,
                                point_type="max2",
                                days_from_halving=(seg["max2_date"] - seg_start_halving).days,
                            )
                        )

            # -- Replace prev min1 if price went lower before max2 --
            # When no min2 separates min1 from max2, the bear may have
            # continued past min1.  The true cycle bottom is the lowest
            # point in (prev_min1, max2].
            if not min2_valid and prev_min1_point is not None:
                low_mask = (
                    (df.index.date > prev_min1_point.date)
                    & (df.index.date <= seg["max2_date"])
                    & (df["close"] > 0)
                )
                low_data = df[low_mask]
                if not low_data.empty:
                    low_idx = low_data["close"].idxmin()
                    low_price = float(low_data.loc[low_idx, "close"])
                    if low_price < prev_min1_point.price:
                        low_date = _to_date(low_idx)
                        # Remove old min1 and add replacement
                        points = [
                            p
                            for p in points
                            if not (
                                p.cycle_num == prev_min1_point.cycle_num and p.point_type == "min1"
                            )
                        ]
                        new_min1 = CyclePoint(
                            date=low_date,
                            price=low_price,
                            cycle_num=prev_min1_point.cycle_num,
                            point_type="min1",
                            days_from_halving=(low_date - seg_start_halving).days,
                        )
                        points.append(new_min1)
                        prev_min1_price = low_price
                        prev_min1_point = new_min1

            # -- Find min1: min in (max2_date, effective_end] --
            min1_mask = (seg["valid_data"].index.date > seg["max2_date"]) & (
                seg["valid_data"].index.date <= seg["effective_end"]
            )
            min1_data = seg["valid_data"][min1_mask]

            min1_point: CyclePoint | None = None
            if not min1_data.empty:
                min1_idx = min1_data["close"].idxmin()
                min1_date = _to_date(min1_idx)
                min1_price = float(min1_data.loc[min1_idx, "close"])

                # Validate retracement depth (structural for completed cycles,
                # gates current cycle — has the bear started?)
                ref_price = (
                    seg["min2_price"] if min2_valid and seg["min2_price"] else prev_min1_price
                )
                if ref_price is not None:
                    ratio = fib_retracement_ratio(ref_price, seg["max2_price"], min1_price)
                    if ratio is not None and ratio >= MIN_RETRACEMENT_LEVEL:
                        min1_point = CyclePoint(
                            date=min1_date,
                            price=min1_price,
                            cycle_num=curr_cycle,
                            point_type="min1",
                            days_from_halving=(min1_date - seg_end_halving).days,
                        )
                else:
                    # No reference price (first segment, no min2).
                    # Still require min1 below max2 (must be a retracement).
                    if min1_price < seg["max2_price"]:
                        min1_point = CyclePoint(
                            date=min1_date,
                            price=min1_price,
                            cycle_num=curr_cycle,
                            point_type="min1",
                            days_from_halving=(min1_date - seg_end_halving).days,
                        )

            # -- Find max1: max in [min1_date, seg_end], extended to next min2 --
            max1_point: CyclePoint | None = None
            if min1_point is not None:
                # Determine extended search end
                max1_search_end = seg["effective_end"]
                if s_idx + 1 < len(segments) and segments[s_idx + 1] is not None:
                    next_seg = segments[s_idx + 1]
                    if next_seg.get("min2_date") is not None:
                        max1_search_end = max(max1_search_end, next_seg["min2_date"])

                max1_mask = (seg["valid_data"].index.date >= min1_point.date) & (
                    seg["valid_data"].index.date <= max1_search_end
                )
                # Also include next segment data if extended
                if max1_search_end > seg["effective_end"] and s_idx + 1 < len(segments):
                    next_seg = segments[s_idx + 1]
                    if next_seg is not None:
                        ext_mask = (next_seg["valid_data"].index.date > seg["effective_end"]) & (
                            next_seg["valid_data"].index.date <= max1_search_end
                        )
                        ext_data = next_seg["valid_data"][ext_mask]
                        max1_data = pd.concat(
                            [
                                seg["valid_data"][max1_mask],
                                ext_data,
                            ]
                        )
                    else:
                        max1_data = seg["valid_data"][max1_mask]
                else:
                    max1_data = seg["valid_data"][max1_mask]

                if not max1_data.empty:
                    max1_idx = max1_data["close"].idxmax()
                    max1_date = _to_date(max1_idx)
                    max1_price = float(max1_data.loc[max1_idx, "close"])

                    # Validate bounce significance:
                    # bounce_fraction = 1 - fib_retracement_ratio(min1, max2, max1)
                    ratio = fib_retracement_ratio(min1_point.price, seg["max2_price"], max1_price)
                    if ratio is not None and (1.0 - ratio) >= MIN_RETRACEMENT_LEVEL:
                        max1_point = CyclePoint(
                            date=max1_date,
                            price=max1_price,
                            cycle_num=curr_cycle,
                            point_type="max1",
                            days_from_halving=(max1_date - seg_end_halving).days,
                        )

            # -- Correct min1 using max1 as boundary --
            # The initial min1 search is bounded by the segment end (halving
            # date).  The true bottom may occur a few days past the halving
            # (e.g. BNB's low on 2020-05-14, 3 days after H3).  When max1
            # exists, rescan [min1, max1) in the full df for a lower point.
            if min1_point is not None and max1_point is not None:
                corr_mask = (
                    (df.index.date >= min1_point.date)
                    & (df.index.date < max1_point.date)
                    & (df["close"] > 0)
                )
                corr_data = df[corr_mask]
                if not corr_data.empty:
                    corr_idx = corr_data["close"].idxmin()
                    corr_price = float(corr_data.loc[corr_idx, "close"])
                    if corr_price < min1_point.price:
                        min1_point = CyclePoint(
                            date=_to_date(corr_idx),
                            price=corr_price,
                            cycle_num=curr_cycle,
                            point_type="min1",
                            days_from_halving=(_to_date(corr_idx) - seg_end_halving).days,
                        )

            # -- Merge logic: if no max1 AND prev had no max1, merge min1/min2 --
            # When prev segment had max1, min2 is structurally distinct
            # (dip between max1 and max2) and must not be merged away.
            if min1_point is not None and max1_point is None and not prev_had_max1:
                # Check if min2 exists and has lower price
                if (
                    min2_valid
                    and seg["min2_price"] is not None
                    and seg["min2_price"] < min1_point.price
                ):
                    # min2 has lower price — relabel as min1 for curr_cycle
                    min1_point = CyclePoint(
                        date=seg["min2_date"],
                        price=seg["min2_price"],
                        cycle_num=curr_cycle,
                        point_type="min1",
                        days_from_halving=(seg["min2_date"] - seg_end_halving).days,
                    )
                    # Remove the min2 we already added to points
                    points = [
                        p
                        for p in points
                        if not (p.cycle_num == prev_cycle and p.point_type == "min2")
                    ]
                # else: keep min1 as-is, just no max1

            # Add min1 and max1 to points
            if min1_point is not None:
                points.append(min1_point)
            if max1_point is not None:
                points.append(max1_point)

            # Update state for next iteration
            if min1_point is not None:
                prev_min1_price = min1_point.price
                prev_min1_point = min1_point
            prev_had_max1 = max1_point is not None
            prev_max1_date = max1_point.date if max1_point is not None else None

        # --- Handle last/current segment beyond last halving ---
        # If there's price data after the last halving, find points there
        last_halving = halvings[-1]
        if last_price_date > last_halving:
            post_mask = (df.index.date > last_halving) & (df["close"] > 0)
            post_data = df[post_mask]
            if not post_data.empty:
                last_cycle = len(HALVING_DATES)
                # max2 for the last cycle
                max2_idx = post_data["close"].idxmax()
                max2_date = _to_date(max2_idx)
                max2_price = float(post_data.loc[max2_idx, "close"])

                points.append(
                    CyclePoint(
                        date=max2_date,
                        price=max2_price,
                        cycle_num=last_cycle,
                        point_type="max2",
                        days_from_halving=(max2_date - last_halving).days,
                    )
                )

                # min2: dip between prev max1 and max2 (extends before halving)
                last_min2_valid = False
                if prev_had_max1 and prev_max1_date is not None:
                    min2_ext_mask = (
                        (df.index.date >= prev_max1_date)
                        & (df.index.date <= max2_date)
                        & (df["close"] > 0)
                    )
                    min2_ext = df[min2_ext_mask]
                    if not min2_ext.empty:
                        min2_idx = min2_ext["close"].idxmin()
                        min2_date = _to_date(min2_idx)
                        min2_price = float(min2_ext.loc[min2_idx, "close"])
                        # Validate significance
                        if prev_min1_price is not None:
                            ratio = fib_retracement_ratio(prev_min1_price, max2_price, min2_price)
                            last_min2_valid = ratio is not None and ratio >= MIN_RETRACEMENT_LEVEL
                        else:
                            last_min2_valid = True
                        if last_min2_valid:
                            points.append(
                                CyclePoint(
                                    date=min2_date,
                                    price=min2_price,
                                    cycle_num=last_cycle,
                                    point_type="min2",
                                    days_from_halving=(min2_date - last_halving).days,
                                )
                            )

                # Merge adjacent maxes when no min2 separates them
                if not last_min2_valid and prev_had_max1 and prev_max1_date is not None:
                    prev_max1_pts = [
                        p for p in points if p.point_type == "max1" and p.date == prev_max1_date
                    ]
                    if prev_max1_pts:
                        prev_max1_pt = prev_max1_pts[0]
                        # Remove both old max1 and old max2
                        points = [
                            p
                            for p in points
                            if not (p.cycle_num == last_cycle and p.point_type == "max2")
                            and not (p.point_type == "max1" and p.date == prev_max1_date)
                        ]
                        merged_date = (
                            prev_max1_pt.date if prev_max1_pt.price > max2_price else max2_date
                        )
                        merged_price = max(prev_max1_pt.price, max2_price)
                        points.append(
                            CyclePoint(
                                date=merged_date,
                                price=merged_price,
                                cycle_num=last_cycle,
                                point_type="max2",
                                days_from_halving=(merged_date - last_halving).days,
                            )
                        )
                        max2_price = merged_price
                        # Keep max2_date at the later date for min1 search

                # min1 for the next cycle (if bear has started)
                min1_after = post_data[post_data.index.date > max2_date]
                if not min1_after.empty:
                    min1_idx = min1_after["close"].idxmin()
                    min1_date = _to_date(min1_idx)
                    min1_price = float(min1_after.loc[min1_idx, "close"])

                    # Check 23.6% retracement
                    ref = prev_min1_price
                    if ref is not None:
                        ratio = fib_retracement_ratio(ref, max2_price, min1_price)
                        if ratio is not None and ratio >= MIN_RETRACEMENT_LEVEL:
                            points.append(
                                CyclePoint(
                                    date=min1_date,
                                    price=min1_price,
                                    cycle_num=last_cycle + 1,
                                    point_type="min1",
                                    days_from_halving=0,  # No next halving yet
                                )
                            )

        return points

    @staticmethod
    def _find_latest_min_point(points: list[CyclePoint]) -> CyclePoint | None:
        """Find the most recent min point (min1 or min2) by date."""
        for p in sorted(points, key=lambda x: x.date, reverse=True):
            if "min" in p.point_type:
                return p
        return None

    @staticmethod
    def _build_points_index(
        points: list[CyclePoint],
    ) -> dict[tuple[int, str], list[CyclePoint]]:
        """Build index of points by (cycle_num, point_type) for O(1) lookup."""
        index: dict[tuple[int, str], list[CyclePoint]] = {}
        for p in points:
            key = (p.cycle_num, p.point_type)
            if key not in index:
                index[key] = []
            index[key].append(p)
        return index

    def _get_regression_date(self, point: CyclePoint) -> date:
        """
        Get the date to use for trendline regression for a given point.

        For current cycle min1, uses an approximated date
        (projected_halving - CURRENT_CYCLE_MIN1_APPROX_DAYS_BEFORE_HALVING days)
        instead of the actual detected date. This provides a stable reference point
        for regression calculations since the current cycle is ongoing and the actual
        bottom may not have occurred yet.

        For all other points, returns the actual date.

        Args:
            point: The cycle point

        Returns:
            Date to use for regression x-coordinate
        """
        if point.cycle_num == self.current_cycle_num and point.point_type == "min1":
            # Use approximated date for current cycle min1
            return self.projected_halving - timedelta(
                days=CURRENT_CYCLE_MIN1_APPROX_DAYS_BEFORE_HALVING
            )
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

        Note: For cycle 5 min1, an approximated date is used (520 days before
        the projected 5th halving) instead of the actual detected date. This
        provides a stable reference for regression since cycle 5 is ongoing.

        Returns:
            Tuple of (upper_slope, upper_intercept, lower_slope, lower_intercept)
            or (None, None, None, None) if insufficient data
        """
        # Separate peaks and troughs, filtering out zero/negative prices
        peaks = [p for p in points if "max" in p.point_type and p.price > 0]
        troughs = [p for p in points if "min" in p.point_type and p.price > 0]

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
        # Note: Cycle 5 min1 uses approximated date via _get_regression_date()
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

            elif has_enough_total_troughs:
                # Fallback: 2+ total troughs but not enough major - fit all troughs, use same slope for peaks
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
                # Fallback: 2+ total peaks but not enough major - fit all peaks, use same slope for troughs
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
            # Collect all min/max points for this cycle from the index
            min_prices = []
            max_prices = []
            for pt in ("min1", "min2"):
                for p in idx.get((cycle, pt), []):
                    min_prices.append(p.price)
            for pt in ("max1", "max2"):
                for p in idx.get((cycle, pt), []):
                    max_prices.append(p.price)

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

            latest_min = self._find_latest_min_point(points)

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

            latest_min = self._find_latest_min_point(points)

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
        - Otherwise -> weighted average of all historical peaks (67% max2, 33% max1)

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
        # Calculate weighted average of all historical peaks
        all_peaks = max2_points + max1_points
        if not all_peaks:
            return None, None

        # Weighted sum: max2 gets 67%, max1 gets 33%
        weighted_sum = 0.0
        weight_total = 0.0

        for p in max2_points:
            weighted_sum += p.price * MAJOR_POINT_WEIGHT
            weight_total += MAJOR_POINT_WEIGHT

        for p in max1_points:
            weighted_sum += p.price * MINOR_POINT_WEIGHT
            weight_total += MINOR_POINT_WEIGHT

        if weight_total == 0:
            return None, None

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
        return fib_retracement_ratio(prev_trough_price, peak_price, new_trough_price)

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

            target_date = self.projected_halving + timedelta(days=EXPECTED_PEAK_DAYS_AFTER_HALVING)
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

        result = CoinPatternResult(coin_id="btc")
        result.points = self._find_all_cycle_points(btc_df)

        if not result.points:
            logger.warning("No BTC cycle points found")
            return None

        result.num_cycles = len({p.cycle_num for p in result.points if p.point_type == "min1"})
        result.current_price = float(btc_df["close"].iloc[-1])
        result.current_date = btc_df.index[-1].date()

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

        # Get TOTAL2 membership info (for reference, not filtering)
        _, first_total2, last_total2 = self._filter_to_total2_dates(df, coin_id)

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
        result.days_in_total2 = len(self._get_coin_total2_dates(coin_id))

        # Find points using segment-based detection across all halvings
        result.points = self._find_all_cycle_points(df)

        if not result.points:
            logger.debug("%s: No cycle points found", coin_id.upper())
            return None

        # Count cycles where coin has min1 (pre-halving data proves coin existed before halving)
        # Post-halving-only data (min2/max2) doesn't count as experiencing a full cycle
        result.num_cycles = len({p.cycle_num for p in result.points if p.point_type == "min1"})

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

        self._pipeline_with_projections = len(results)
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
        - Coins must have a positive trendline prediction (missing or negative = excluded)
        - Coins with declining floor (lower_slope < MIN_LOWER_SLOPE) are excluded
        - Coins with excessive Fibonacci retracement (> MAX_RETRACEMENT_LEVEL) are excluded
        - Coins must have a valid composite score
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

        # Filter 1: Must have positive trendline (no trendline = filtered out)
        candidates = [
            r
            for r in candidates
            if r.trendline_target_pct is not None and r.trendline_target_pct > 0
        ]
        after_trendline = len(candidates)

        # Filter 3: Declining floor (lower_slope below MIN_LOWER_SLOPE)
        candidates = [
            r for r in candidates if r.lower_slope is None or r.lower_slope >= MIN_LOWER_SLOPE
        ]
        after_floor = len(candidates)

        # Filter 4: Excessive Fibonacci retracement (> MAX_RETRACEMENT_LEVEL)
        candidates = [
            r
            for r in candidates
            if r.retracement_ratio is None or r.retracement_ratio <= MAX_RETRACEMENT_LEVEL
        ]
        after_retracement = len(candidates)

        # Filter 5: Too new (first_price_date < MIN_COIN_AGE_DAYS ago)
        candidates = [
            r
            for r in candidates
            if r.first_price_date is None or r.first_price_date <= min_first_price_date
        ]
        after_age = len(candidates)

        # Filter 6: Too few unique prices (staircase/illiquid patterns)
        candidates = [r for r in candidates if r.unique_price_count >= MIN_UNIQUE_PRICES]
        after_unique = len(candidates)

        # Build unified filter summary table including early pipeline stages
        cached = getattr(self, "_pipeline_cached_coins", None)
        total2 = getattr(self, "_pipeline_total2_coins", None)

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

        lines.append(_step("Positive trendline", after_trendline, total_start - after_trendline))
        lines.append(
            _step("Floor not declining (slope >= min)", after_floor, after_trendline - after_floor)
        )
        lines.append(
            _step(
                f"Retracement <= {MAX_RETRACEMENT_LEVEL * 100:.1f}%",
                after_retracement,
                after_floor - after_retracement,
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
            return {
                "date": p.date.isoformat(),
                "price": p.price,
                "cycle_num": p.cycle_num,
                "point_type": p.point_type,
                "days_from_halving": p.days_from_halving,
            }

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

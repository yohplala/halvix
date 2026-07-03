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
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
from tqdm import tqdm

from analysis import point_detection, projections
from analysis.cycle_points import (
    CoinPatternResult,
    CyclePoint,
    _to_date,
    build_points_index,
    count_min1_cycles,
    count_peak_cycles,
)
from config import (
    DAYS_BEFORE_HALVING,
    GOLDEN_RETRACEMENT_LEVEL,
    HALVING_DATES,
    MAX_FLAT_RUN_DAYS,
    MAX_RETRACEMENT_LEVEL,
    MAX_ZERO_CHANGE_FRACTION,
    MIN_COIN_AGE_DAYS,
    MIN_LOWER_SLOPE,
    MIN_UNIQUE_PRICES,
    MIN_UPPER_TRENDLINE_TARGET_PCT,
    PROCESSED_DIR,
    RETRACEMENT_PENALTY_AT_MAX,
    TOTAL2_COMPOSITION_FILE,
    TOTAL2_LOOKBACK_YEARS,
    UNIQUE_PRICES_WINDOW_DAYS,
    YOUNG_COIN_TREND_LOG_SCALE,
)
from data.cache import PriceDataCache
from data.price_filters import apply_round_trip_smoothing, filter_to_post_replacement
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
        self.projected_halving = HALVING_DATES[-1]

        # Load TOTAL2 composition for filtering
        self._total2_composition: pl.DataFrame | None = None
        self._total2_coins: set[str] | None = None

        # Early-pipeline counts populated by analyze_all_coins() and consumed
        # by get_top_coins() when it prints the unified filter table. They
        # remain None when get_top_coins() is called without analyze_all_coins()
        # first (e.g. from a direct caller passing in custom results).
        self._pipeline_cached_coins: int | None = None
        self._pipeline_total2_coins: int | None = None

    def _load_total2_composition(self) -> pl.DataFrame | None:
        """Load TOTAL2 composition data (``date`` cast to ``pl.Date``)."""
        if self._total2_composition is not None:
            return self._total2_composition

        if TOTAL2_COMPOSITION_FILE.exists():
            try:
                comp = pl.read_parquet(TOTAL2_COMPOSITION_FILE)
                self._total2_composition = comp.with_columns(pl.col("date").cast(pl.Date))
                logger.info(
                    "Loaded TOTAL2 composition: %d records",
                    self._total2_composition.height,
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
            if "date" in comp_df.columns:
                # Filter to coins that were in TOTAL2 within the lookback period
                lookback_cutoff = date.today() - timedelta(days=TOTAL2_LOOKBACK_YEARS * 365)
                recent = comp_df.filter(pl.col("date") >= lookback_cutoff)
                self._total2_coins = set(recent["coin_id"].str.to_lowercase().unique().to_list())

                logger.info(
                    "Found %d coins in TOTAL2 within past %d years (from %s)",
                    len(self._total2_coins),
                    TOTAL2_LOOKBACK_YEARS,
                    lookback_cutoff.isoformat(),
                )
            else:
                self._total2_coins = set(comp_df["coin_id"].str.to_lowercase().unique().to_list())
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

        coin_data = comp_df.filter(pl.col("coin_id") == coin_id)
        if coin_data.is_empty():
            return set()

        # Convert to set of dates
        return {_to_date(ts) for ts in coin_data["date"]}

    # ── Kernel + projection wrappers ──────────────────────────────────
    # The identification kernel lives in ``analysis.point_detection`` and
    # the four projection models in ``analysis.projections``. The thin
    # wrappers below forward to those module-level functions so callers
    # (production code here + tests using ``analyzer._foo(...)`` /
    # ``CyclePatternAnalyzer._foo(...)``) keep their original surface.
    # Only wrappers actually referenced externally are kept.
    # ────────────────────────────────────────────────────────────────

    def _identify_cycle_points(self, df: pl.DataFrame) -> list[CyclePoint]:
        """Detect cycle min/max points across all halving-delimited segments."""
        return point_detection.identify_cycle_points(df, self.all_halvings)

    _build_segments = staticmethod(point_detection.build_segments)
    # Pure point-list helpers live in ``analysis.cycle_points`` — they
    # operate on already-detected CyclePoint lists, not on the
    # segment-scan kernel. The 17 other wrappers that previously forwarded
    # to ``point_detection.*`` were dropped in commit 7160f70 (unreferenced
    # by tests and production callers).
    _build_points_index = staticmethod(build_points_index)
    _count_min1_cycles = staticmethod(count_min1_cycles)
    _count_peak_cycles = staticmethod(count_peak_cycles)

    _fit_log_trendlines = staticmethod(projections.fit_log_trendlines)
    _project_trendline_target = staticmethod(projections.project_trendline_target)
    _last_peak_days = staticmethod(projections.last_peak_days)
    _floor_damped_trendline = staticmethod(projections.floor_damped_trendline)
    _calculate_fib_extension = staticmethod(projections.calculate_fib_extension)
    _calculate_diminishing_return = staticmethod(projections.calculate_diminishing_return)
    _calculate_historical_peak = staticmethod(projections.calculate_historical_peak)
    _calculate_weighted_composite = staticmethod(projections.calculate_weighted_composite)
    _calculate_retracement_ratio = staticmethod(projections.calculate_retracement_ratio)
    _classify_pattern = staticmethod(projections.classify_pattern)

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

        # Projections are relative to the current price; without it there is
        # nothing to compute against.
        current_price = result.current_price
        if current_price is None or current_price <= 0:
            return

        # Fit trendlines
        upper_slope, upper_int, lower_slope, lower_int = self._fit_log_trendlines(result.points)

        if upper_slope is not None and upper_int is not None:
            result.upper_slope = upper_slope
            result.lower_slope = lower_slope
            result.upper_intercept = upper_int
            result.lower_intercept = lower_int
            result.pattern_type = self._classify_pattern(upper_slope, lower_slope)

            # Expected peak ≈ halving + 550 days (same offset as DAYS_BEFORE_HALVING).
            # Floor-aware damping: bend the peak line toward the floor for the
            # forward extrapolation so a widening/parabolic channel is projected
            # at the rate its floor can support (see TRENDLINE_FLOOR_DAMPING).
            target_date = self.projected_halving + timedelta(days=DAYS_BEFORE_HALVING)
            anchor_days = self._last_peak_days(result.points)
            if lower_slope is not None and anchor_days is not None:
                proj_slope, proj_int = self._floor_damped_trendline(
                    upper_slope, upper_int, lower_slope, anchor_days
                )
            else:
                proj_slope, proj_int = upper_slope, upper_int
            result.trend_proj_slope = proj_slope
            result.trend_proj_intercept = proj_int
            target = self._project_trendline_target(proj_slope, proj_int, target_date)
            if target is not None:
                result.trendline_target = target
                result.trendline_target_pct = (target / current_price - 1) * 100

        # Maturity: has the coin lived through a COMPLETED prior halving cycle,
        # i.e. does it own a realized peak (max2) dated before the current cycle's
        # halving? The rebound-based methods (Fibonacci, diminishing returns,
        # historical peak) all assume a full-cycle rebound and need a past cycle to
        # anchor to. A coin with only in-progress-cycle structure (SYRUP, SIREN,
        # HYPE) has no such anchor and would over-extrapolate wildly, so for it the
        # composite is built from the demonstrated trendline only, then capped.
        last_halving = max(h for h in HALVING_DATES if h <= date.today())
        mature = any(
            p.point_type == "max2" and not p.projected and p.date < last_halving
            for p in result.points
        )

        if mature:
            # Fibonacci extension
            fib_target = self._calculate_fib_extension(result.points, idx)
            if fib_target:
                result.fib_target = fib_target
                result.fib_target_pct = (fib_target / current_price - 1) * 100

            # Diminishing returns
            dim_target, dim_factor = self._calculate_diminishing_return(result.points, idx)
            if dim_target:
                result.dim_return_target = dim_target
                result.dim_return_target_pct = (dim_target / current_price - 1) * 100
                result.dim_return_factor = dim_factor

            # Historical peak
            hist_peak_target, hist_peak_is_absolute = self._calculate_historical_peak(
                result.points, idx
            )
            if hist_peak_target:
                result.hist_peak_target = hist_peak_target
                result.hist_peak_target_pct = (hist_peak_target / current_price - 1) * 100
                result.hist_peak_is_absolute = hist_peak_is_absolute

        # Composite target.
        if mature:
            # Weighted average using the confidence-based weight profile.
            result.composite_target_pct = self._calculate_weighted_composite(
                trendline_pct=result.trendline_target_pct,
                fib_pct=result.fib_target_pct,
                dim_return_pct=result.dim_return_target_pct,
                hist_peak_pct=result.hist_peak_target_pct,
                confidence=result.confidence,
            )
        elif result.trendline_target_pct is not None:
            # Young coin: trendline-only, log-compressed (no hard cap). A single
            # explosive cycle extrapolated forward can yield a wild trendline; a
            # linear factor can't tame that, so keep a fraction of the projected
            # LOG-growth instead — this crushes the tail while staying gentle on
            # modest young coins. Floor damping and the age/liquidity display
            # filters still run first. See YOUNG_COIN_TREND_LOG_SCALE in config.
            m = 1 + result.trendline_target_pct / 100
            result.composite_target_pct = (
                YOUNG_COIN_TREND_LOG_SCALE * 100 * math.log(m)
                if m > 0
                else result.trendline_target_pct
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
    def _smooth_round_trips(df: pl.DataFrame, label: str) -> pl.DataFrame:
        """
        Smooth spike-and-revert glitches (single-day or multi-day) in df['close'].

        Cycle min/max detection (arg_max/arg_min over halving segments) and the
        log-linear trendline regression both read close prices directly, so a
        transient pump-dump (e.g. SIREN 2026-04-16 at 2.49x reverting next day)
        can produce a false max1/max2 or skew the trendline. Delegates to the
        shared ``apply_round_trip_smoothing`` used by the TOTAL2 processor and
        the chart path, keeping the close-series guards in sync.
        """
        return apply_round_trip_smoothing(df, log_label=label)

    def analyze_btc(self) -> CoinPatternResult | None:
        """
        Analyze BTC/USD pattern using the same cycle point detection as altcoins.

        Returns:
            CoinPatternResult or None if data unavailable
        """
        btc_df = self.price_cache.get_prices("btc", "USD")

        if btc_df is None or btc_df.is_empty():
            logger.warning("BTC-USD data not available")
            return None

        btc_df = self._smooth_round_trips(btc_df, "BTC")

        result = CoinPatternResult(coin_id="btc")
        result.points = self._identify_cycle_points(btc_df)

        if not result.points:
            logger.warning("No BTC cycle points found")
            return None

        result.num_cycles = self._count_min1_cycles(result.points)
        result.current_price = float(btc_df["close"][-1])
        result.current_date = btc_df["date"][-1]

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

        if df is None or df.is_empty():
            logger.debug("%s: No BTC price data available", coin_id.upper())
            return None

        # Detect symbol replacement (e.g., old MOVE token replaced by Movement
        # Labs MOVE) and, if found, drop the old token's pre-replacement history.
        df = filter_to_post_replacement(df, log_label=coin_id.upper())
        if df.is_empty():
            logger.debug("%s: No data after symbol replacement date", coin_id.upper())
            return None

        # Smooth spike-and-revert glitches (single-day or multi-day) on the
        # close series so they cannot become false max1/max2/min1/min2 points
        # or distort the log-linear trendline.
        df = self._smooth_round_trips(df, coin_id.upper())

        # Get TOTAL2 membership info (for reference, not filtering)
        total2_dates = self._get_coin_total2_dates(coin_id)
        first_total2 = min(total2_dates) if total2_dates else None
        last_total2 = max(total2_dates) if total2_dates else None

        if not force:
            if first_total2 is None:
                logger.debug("No TOTAL2 data for %s", coin_id)
                return None

            # Check that coin was in TOTAL2 within the lookback period.
            # _get_total2_coins() already enforces this when filter_total2 is
            # True, but analyze_coin can be called standalone (e.g. from
            # tests / CLI inspection) so we re-check defensively.
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

        # "Cycles" / maturity = number of halving cycles in which the coin printed
        # a realized peak (max2). This counts cycle tops the coin actually reached
        # (so TRX, at a fresh high, reads 3 like SOL) rather than bear bottoms.
        result.num_cycles = self._count_peak_cycles(result.points)

        # Check minimum cycles requirement (>=1 realized peak). Admits trending
        # young coins (e.g. HYPE) that only have an in-progress-cycle peak; their
        # projections are governed by the young-coin path in _run_projections.
        if not force and result.num_cycles < self.min_cycles:
            logger.debug(
                "%s: Insufficient cycles (%d < %d required)",
                coin_id.upper(),
                result.num_cycles,
                self.min_cycles,
            )
            return None

        # Get current price and price quality info
        result.current_price = float(df["close"][-1])
        result.current_date = df["date"][-1]
        result.first_price_date = df["date"][0]

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
        recent_prices = df.filter(pl.col("date") >= unique_window_start)
        recent_close = recent_prices["close"] if not recent_prices.is_empty() else None
        result.unique_price_count = recent_close.n_unique() if recent_close is not None else 0
        result.max_flat_run, result.zero_change_fraction = self._staircase_metrics(recent_close)

        self._run_projections(result)
        return result

    @staticmethod
    def _staircase_metrics(recent_close: pl.Series | None) -> tuple[int, float]:
        """Longest run of identical consecutive closes + fraction of zero-change days.

        Robust low-liquidity signals over the recent window: a liquid coin moves
        almost every day (short flat runs, near-zero zero-change fraction), while a
        staircase coin (BANANAS31) holds a price flat for many days at a time.
        """
        if recent_close is None or len(recent_close) < 2:
            return 0, 0.0
        vals = recent_close.to_numpy()
        longest = run = 1
        for i in range(1, len(vals)):
            run = run + 1 if vals[i] == vals[i - 1] else 1
            longest = max(longest, run)
        zero_change_fraction = float((vals[1:] == vals[:-1]).mean())
        return longest, zero_change_fraction

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

        coins_iter = (
            tqdm(coins_to_analyze, desc="Analyzing patterns") if show_progress else coins_to_analyze
        )

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

        # Filter 8: Staircase / illiquid — long flat plateaus or many zero-change
        # days (robust where the unique-count sits right at its threshold, e.g.
        # BANANAS31: 26 unique but ~week-long flat runs).
        candidates = [
            r
            for r in candidates
            if r.max_flat_run <= MAX_FLAT_RUN_DAYS
            and r.zero_change_fraction <= MAX_ZERO_CHANGE_FRACTION
        ]
        after_staircase = len(candidates)

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
        lines.append(
            _step(
                f"Not staircase (flat run <= {MAX_FLAT_RUN_DAYS}d)",
                after_staircase,
                after_unique - after_staircase,
            )
        )

        if forced_results:
            lines.append(f"  {'Force-included coins':<44s}  {len(forced_results)}")

        logger.info("\n".join(lines))

        # Sort by composite target (descending) - primary ranking criterion.
        # Maturity/confidence is shown via the badge, NOT used to reorder the
        # ranking (the composite cap on young coins already keeps their figures
        # plausible without editorialising the order).
        #
        # Force-included coins join the ranked pool even if they failed a filter,
        # so a flagship (e.g. ETH, filtered by retracement; XRP near the trendline
        # floor) sorts into its composite position rather than being tacked on at
        # the end of the table/charts.
        pool = {r.coin_id: r for r in candidates}
        pool.update(forced_results)
        ranked = sorted(pool.values(), key=lambda x: x.composite_target_pct or 0, reverse=True)

        top = ranked[:n]

        # Guarantee every force-included coin appears even if it ranks beyond n,
        # still ordered by composite among the appended extras.
        if forced_results:
            top_ids = {r.coin_id for r in top}
            extras = sorted(
                (r for cid, r in forced_results.items() if cid not in top_ids),
                key=lambda x: x.composite_target_pct or 0,
                reverse=True,
            )
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

        data: dict[str, Any] = {
            "generated_at": datetime.now().isoformat(),
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

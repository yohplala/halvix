"""
Dataclasses and helper functions for cycle pattern analysis.

Shared building blocks consumed by ``point_detection``, ``projections``
and ``cycle_patterns``. Three groups:

- **Type aliases**: ``PointType``, ``Confidence``.
- **Dataclasses**: ``CyclePoint`` (a single min/max in a cycle),
  ``CoinPatternResult`` (full per-coin analysis), ``SegmentData`` and
  ``_SegmentIterState`` (internal state for the multi-pass segment scan).
- **Functions**: ``_to_date``, ``fib_retracement_ratio``, ``_make_point``,
  ``_project_min1`` — pure helpers with no analyzer state, plus
  ``build_points_index``, ``find_latest_min_point``,
  ``count_min1_cycles`` — pure operations on already-detected
  ``CyclePoint`` lists.
"""

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

import polars as pl

from config import MIN_RETRACEMENT_LEVEL

PointType = Literal["min1", "max1", "min2", "max2"]
Confidence = Literal["low", "medium", "high"]


def _to_date(dt: date) -> date:
    """Convert a pandas Timestamp or datetime to a plain date object."""
    return dt.date() if hasattr(dt, "date") else dt


def fib_retracement_ratio(a: float, b: float, c: float) -> float:
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

    Raises:
        ValueError: If inputs are non-positive or b <= a.
    """
    if a <= 0 or b <= 0 or c <= 0:
        raise ValueError(f"All inputs must be positive: a={a}, b={b}, c={c}")
    if b <= a:
        raise ValueError(f"Peak must exceed low: a={a}, b={b}")
    return math.log10(b / c) / math.log10(b / a)


@dataclass
class CyclePoint:
    """A single min or max point within a cycle."""

    date: date
    price: float
    cycle_num: int
    point_type: PointType
    days_from_halving: int
    projected: bool = False  # True when price is assumed (e.g., 23.6% retracement)


@dataclass
class CoinPatternResult:
    """
    Analysis result for a single coin.

    Note on mutability: The `points` field uses `field(default_factory=list)` to avoid
    the mutable default argument pitfall in Python dataclasses. This ensures each
    instance gets its own empty list instead of sharing a single list across all instances.
    """

    coin_id: str  # on-disk parquet stem (cache key), e.g. "tag-2"
    points: list[CyclePoint] = field(default_factory=list)
    num_cycles: int = 0

    # Display identity, resolved from the stem via CoinMetadataResolver. Left
    # None for standalone/test callers; renderers then fall back to the stem.
    display_ticker: str | None = None  # e.g. "TAG" (not the "TAG-2" stem)
    display_url: str | None = None  # CoinGecko coin page for the ticker

    # Method 1: Trendline projection
    trendline_target: float | None = None
    trendline_target_pct: float | None = None
    upper_slope: float | None = None
    lower_slope: float | None = None
    upper_intercept: float | None = None  # For trendline visualization
    lower_intercept: float | None = None  # For trendline visualization
    # Floor-damped forward projection: the peak line bent toward the floor beyond
    # the last realized peak. Drives the trendline target and the chart's ★ — the
    # upper trendline is drawn straight at this slope through to the target.
    trend_proj_slope: float | None = None
    trend_proj_intercept: float | None = None

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
    max_flat_run: int = 0  # Longest run of identical consecutive closes (staircase/illiquidity)
    zero_change_fraction: float = 0.0  # Share of window days with no price change (illiquidity)

    # Rank in trendline prediction ranking (set after sorting)
    rank: int | None = None


@dataclass
class SegmentData:
    """Metadata for a single segment between two consecutive halvings."""

    seg_start: date  # halving at start of segment
    seg_end: date  # halving at end of segment
    effective_end: date  # min(seg_end, last_price_date) for last segment
    prev_cycle: int  # cycle number of seg_start halving
    curr_cycle: int  # cycle number of seg_end halving
    data: pl.DataFrame  # price data for this segment (may include zeros)
    valid_data: pl.DataFrame  # price data with close > 0
    is_last: bool  # whether this is the last halving-delimited segment
    # Populated by Pass 1:
    max2_date: date | None = None
    max2_price: float | None = None
    # Populated by Pass 2:
    min2_date: date | None = None
    min2_price: float | None = None


@dataclass
class _SegmentIterState:
    """Mutable state carried between segments during Pass 3 validation."""

    min1_price: float | None = None
    min1_point: CyclePoint | None = None
    had_max1: bool = True
    max1_date: date | None = None


def _make_point(
    pt_date: date,
    price: float,
    cycle_num: int,
    point_type: PointType,
    halving_ref: date,
    projected: bool = False,
) -> CyclePoint:
    """Create a CyclePoint with days_from_halving computed from halving_ref."""
    return CyclePoint(
        date=pt_date,
        price=price,
        cycle_num=cycle_num,
        point_type=point_type,
        days_from_halving=(pt_date - halving_ref).days,
        projected=projected,
    )


def _project_min1(
    min1_date: date,
    max2_price: float,
    ref_price: float,
    cycle_num: int,
    halving_ref: date,
) -> CyclePoint | None:
    """Project min1 at MIN_RETRACEMENT_LEVEL when actual retracement is insufficient.

    Returns a CyclePoint with projected=True, or None if the price calculation
    fails (e.g., non-positive inputs to log10).
    """
    try:
        projected_price = 10 ** (
            (1 - MIN_RETRACEMENT_LEVEL) * math.log10(max2_price)
            + MIN_RETRACEMENT_LEVEL * math.log10(ref_price)
        )
        return _make_point(
            min1_date, projected_price, cycle_num, "min1", halving_ref, projected=True
        )
    except ValueError:
        return None


# ── Operations on already-detected cycle points ──────────────────────
# These three helpers consume CyclePoint lists / the by-(cycle, type)
# index and are independent of the segment-detection kernel — both
# ``point_detection`` and ``projections`` use them. They live here (the
# dataclass + helper module) rather than in ``point_detection`` to keep
# the dependency graph one-directional: ``point_detection`` and
# ``projections`` each depend on ``cycle_points``, not on each other.
# ────────────────────────────────────────────────────────────────────


def build_points_index(
    points: list[CyclePoint],
) -> dict[tuple[int, PointType], list[CyclePoint]]:
    """Build index of points by (cycle_num, point_type) for O(1) lookup."""
    index: dict[tuple[int, PointType], list[CyclePoint]] = {}
    for p in points:
        key = (p.cycle_num, p.point_type)
        if key not in index:
            index[key] = []
        index[key].append(p)
    return index


def find_latest_min_point(
    idx: dict[tuple[int, PointType], list[CyclePoint]],
) -> CyclePoint | None:
    """Find the most recent min point (min1 or min2) by date using the index."""
    latest: CyclePoint | None = None
    for (_, pt), pts in idx.items():
        if "min" in pt:
            for p in pts:
                if latest is None or p.date > latest.date:
                    latest = p
    return latest


def count_min1_cycles(points: list[CyclePoint]) -> int:
    """Count distinct cycles that have an *actual* (non-projected) min1.

    A projected min1 represents a 23.6%-retracement assumption for the
    in-progress cycle when the bear hasn't unfolded. It is not evidence
    that the coin has lived through a completed cycle, so it must not
    bump the cycle count (and therefore the confidence level).
    """
    return len({p.cycle_num for p in points if p.point_type == "min1" and not p.projected})


def count_peak_cycles(points: list[CyclePoint]) -> int:
    """Count distinct halving cycles in which the coin printed a realized peak (max2).

    This is the maturity / "cycles lived" metric used for the displayed cycle
    count and the confidence tier. It counts cycle *tops* the coin actually
    reached, which — unlike ``count_min1_cycles`` (bear-market *bottoms*) —

    - does NOT under-count an old coin currently making a fresh high with no
      recent bottom yet (e.g. TRX: peaks in cycles 2/3/4 -> 3, matching SOL),
    - does NOT over-count a young coin whose only ``min1`` sits in the
      in-progress cycle.

    ``max2`` points are structural and always realized (never projected).
    """
    return len({p.cycle_num for p in points if p.point_type == "max2"})

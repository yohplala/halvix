"""
Cycle min/max identification kernel.

The multi-pass segment scan lives here as pure module-level functions: they
take prices, halvings and segment metadata as explicit arguments and return
data structures (``CyclePoint`` lists / ``SegmentData`` lists) — no analyzer
state is read or mutated.

The kernel detects four structural points per halving-delimited segment
(``max2``, ``min2``, ``min1``, ``max1``) using a three-pass algorithm; see
``identify_cycle_points`` for the high-level orchestration and
``docs/IDENTIFICATION_KERNEL.md`` for the algorithm rationale.

Price frames carry a ``date`` column (``pl.Date``) and a ``close`` column;
segment scans filter on ``date`` and pick extrema with ``arg_max``/``arg_min``
(first-occurrence on ties, matching the previous ``idxmax``/``idxmin``).

``CyclePatternAnalyzer`` exposes a handful of staticmethod wrappers
(``_build_segments``, ``_build_points_index``, ``_count_min1_cycles``) that
forward to functions here, preserving the surface a few external test
helpers rely on.
"""

from datetime import date, timedelta

import polars as pl

from analysis.cycle_points import (
    CyclePoint,
    SegmentData,
    _make_point,
    _project_min1,
    _SegmentIterState,
    fib_retracement_ratio,
)
from config import (
    HALVING_DATES,
    LAUNCH_DATE_BUFFER_DAYS,
    MAX2_PRE_HALVING_BUFFER_DAYS,
    MIN_RETRACEMENT_LEVEL,
)


def _argmax_row(frame: pl.DataFrame) -> tuple[date, float]:
    """(date, close) at the highest close — first occurrence on ties."""
    pos = frame["close"].arg_max()
    return frame["date"][pos], float(frame["close"][pos])


def _argmin_row(frame: pl.DataFrame) -> tuple[date, float]:
    """(date, close) at the lowest close — first occurrence on ties."""
    pos = frame["close"].arg_min()
    return frame["date"][pos], float(frame["close"][pos])


def identify_cycle_points(df: pl.DataFrame, halvings: list[date]) -> list[CyclePoint]:
    """Identify cycle min/max points using segment-based detection.

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
        df: Price DataFrame with a ``date`` column and a ``close`` column.
        halvings: List of halving dates delimiting the segments.

    Returns:
        List of CyclePoint objects with correct cycle_num and days_from_halving.
    """
    if df.is_empty():
        return []

    last_price_date = df["date"][-1]

    segments = build_segments(df, halvings, last_price_date)
    pass1_find_max2(segments)
    pass2_find_min2_candidates(segments)
    points, state = pass3_validate_and_detect(df, segments, halvings)
    detect_post_halving_points(df, points, halvings, last_price_date, segments, state)
    return points


def build_segments(
    df: pl.DataFrame,
    halvings: list[date],
    last_price_date: date,
) -> list[SegmentData | None]:
    """Build segment metadata between consecutive halvings.

    Segment intervals follow the half-open convention ``[seg_start, seg_end)``
    on halving boundaries: a price exactly on halving ``H[n]`` belongs to
    segment ``[H[n], H[n+1])`` only, never to ``[H[n-1], H[n])``. This
    prevents the halving-date sample from being scanned by two segments
    at once. ``effective_end`` keeps its prior semantics of an *inclusive*
    upper bound used by downstream passes, so for non-clipped segments
    we set it to ``seg_end - 1 day`` (the latest day strictly before the
    next halving). The post-halving detector
    (``detect_post_halving_points``) uses ``>= halvings[-1]`` to take
    ownership of the projected-halving day under this same convention.
    """
    segments: list[SegmentData | None] = []
    one_day = timedelta(days=1)
    for s in range(len(halvings) - 1):
        seg_start = halvings[s]
        seg_end = halvings[s + 1]
        is_last = s == len(halvings) - 2
        # effective_end is INCLUSIVE; choose the latest day belonging to
        # this segment under the [seg_start, seg_end) convention.
        if is_last and last_price_date < seg_end:
            effective_end = last_price_date
        else:
            effective_end = seg_end - one_day
        prev_cycle = s + 2
        curr_cycle = s + 3

        seg_data = df.filter((pl.col("date") >= seg_start) & (pl.col("date") <= effective_end))
        valid_seg = seg_data.filter(pl.col("close") > 0)

        if valid_seg.is_empty():
            segments.append(None)
            continue

        segments.append(
            SegmentData(
                seg_start=seg_start,
                seg_end=seg_end,
                effective_end=effective_end,
                prev_cycle=prev_cycle,
                curr_cycle=curr_cycle,
                data=seg_data,
                valid_data=valid_seg,
                is_last=is_last,
            )
        )
    return segments


def pass1_find_max2(segments: list[SegmentData | None]) -> None:
    """Pass 1: Find max2 for each segment.

    Buffer excludes the pre-halving rally zone from max2 search,
    preventing the pre-halving pump (structurally max1) from being
    picked as the cycle peak when it exceeds the actual cycle top.
    """
    buffer = timedelta(days=MAX2_PRE_HALVING_BUFFER_DAYS)
    for seg in segments:
        if seg is None:
            continue
        max2_search_end = min(seg.effective_end, seg.seg_end - buffer)
        if max2_search_end <= seg.seg_start:
            max2_search_end = seg.effective_end
        max2_data = seg.valid_data.filter(pl.col("date") <= max2_search_end)
        if max2_data.is_empty():
            max2_data = seg.valid_data
        seg.max2_date, seg.max2_price = _argmax_row(max2_data)


def pass2_find_min2_candidates(segments: list[SegmentData | None]) -> None:
    """Pass 2: Find min2 candidates (min in [seg_start, max2_date])."""
    for seg in segments:
        if seg is None or seg.max2_date is None:
            continue
        min2_data = seg.valid_data.filter(
            (pl.col("date") >= seg.seg_start) & (pl.col("date") <= seg.max2_date)
        )
        if not min2_data.is_empty():
            seg.min2_date, seg.min2_price = _argmin_row(min2_data)


def merge_adjacent_maxes(
    points: list[CyclePoint],
    prev_max1_date: date,
    new_max2_date: date,
    new_max2_price: float,
    cycle_num: int,
    halving_ref: date,
) -> tuple[list[CyclePoint], float]:
    """Merge adjacent max1 and max2 when no min2 separates them.

    When a previous segment's max1 and the current segment's max2 are
    not separated by a valid min2, they form one peak formation. Keep
    the higher of the two as the merged max2.

    Returns:
        (updated points list, merged max2 price)
    """
    prev_max1_pts = [p for p in points if p.point_type == "max1" and p.date == prev_max1_date]
    if not prev_max1_pts:
        return points, new_max2_price

    prev_max1_pt = prev_max1_pts[0]
    # Remove old max1 and old max2 for this cycle (mutate in-place)
    points[:] = [
        p
        for p in points
        if not (p.cycle_num == cycle_num and p.point_type in ("max1", "max2"))
        and not (p.point_type == "max1" and p.date == prev_max1_date)
    ]
    if prev_max1_pt.price > new_max2_price:
        merged_date = prev_max1_pt.date
        merged_price = prev_max1_pt.price
    else:
        merged_date = new_max2_date
        merged_price = new_max2_price
    points.append(_make_point(merged_date, merged_price, cycle_num, "max2", halving_ref))
    return points, merged_price


def pass3_validate_and_detect(
    df: pl.DataFrame,
    segments: list[SegmentData | None],
    halvings: list[date],
) -> tuple[list[CyclePoint], _SegmentIterState]:
    """Pass 3: Sequential validation of min2/min1/max1 with merge logic."""
    points: list[CyclePoint] = []
    state = _SegmentIterState()

    for s_idx, seg in enumerate(segments):
        if seg is None:
            continue
        process_segment(df, seg, segments, s_idx, halvings, points, state)

    return points, state


def process_segment(
    df: pl.DataFrame,
    seg: SegmentData,
    segments: list[SegmentData | None],
    s_idx: int,
    halvings: list[date],
    points: list[CyclePoint],
    state: _SegmentIterState,
) -> None:
    """Process a single segment: detect, validate, and merge points.

    Mutates *points* (appends detected cycle points) and *state*
    (updates inter-segment tracking for the next iteration).
    """
    prev_cycle = seg.prev_cycle
    curr_cycle = seg.curr_cycle
    seg_start_halving = halvings[s_idx]
    seg_end_halving = halvings[s_idx + 1]

    # max2 always exists (Pass 1 populates it before Pass 3 runs)
    assert seg.max2_date is not None and seg.max2_price is not None
    points.append(_make_point(seg.max2_date, seg.max2_price, prev_cycle, "max2", seg_start_halving))

    # Extend min2 search to prev max1 when applicable
    extend_min2_search(df, seg, state.had_max1, state.max1_date)

    # Validate min2
    min2_valid = validate_min2(df, seg, s_idx, state.had_max1, state.min1_price)
    if min2_valid:
        # validate_min2 sets min2_date/price when it returns True
        assert seg.min2_date is not None and seg.min2_price is not None
        points.append(
            _make_point(seg.min2_date, seg.min2_price, prev_cycle, "min2", seg_start_halving)
        )

    # max1 before min2 (short-history: no prior max1 to precede this min2)
    max1_before = find_max1_before_min2(
        df, seg, state.max1_date, min2_valid, prev_cycle, seg_start_halving
    )
    if max1_before is not None:
        points.append(max1_before)

    # Merge adjacent maxes when no min2 separates them
    if not min2_valid and state.had_max1 and state.max1_date is not None:
        points, seg.max2_price = merge_adjacent_maxes(
            points,
            state.max1_date,
            seg.max2_date,
            seg.max2_price,
            prev_cycle,
            seg_start_halving,
        )

    # Replace prev min1 if price went lower before max2
    if not min2_valid and state.min1_point is not None:
        state.min1_point, state.min1_price = replace_min1_if_lower(
            df, points, seg, state.min1_point, seg_start_halving
        )

    # Find min1 and max1
    min1_point = find_min1(seg, min2_valid, state.min1_price, curr_cycle, seg_end_halving)
    # Skip max1 search when min1 is projected — the assumed price
    # hasn't been reached, so a recovery bounce is not meaningful.
    max1_point = None
    if min1_point is not None and not min1_point.projected:
        max1_point = find_max1(df, seg, segments, s_idx, min1_point, curr_cycle, seg_end_halving)

    # Correct min1 using max1 as boundary
    if min1_point is not None and max1_point is not None:
        min1_point = correct_min1_with_max1(df, min1_point, max1_point, curr_cycle, seg_end_halving)

    if min1_point is not None:
        points.append(min1_point)
    if max1_point is not None:
        points.append(max1_point)

    # Update state for next iteration
    if min1_point is not None:
        state.min1_price = min1_point.price
        state.min1_point = min1_point
    state.had_max1 = max1_point is not None
    state.max1_date = max1_point.date if max1_point is not None else None


def extend_min2_search(
    df: pl.DataFrame,
    seg: SegmentData,
    prev_had_max1: bool,
    prev_max1_date: date | None,
) -> None:
    """Extend min2 search to prev max1 date when applicable.

    The dip between max1 and max2 can cross the halving boundary
    (e.g., COVID crash is before H3 but is the true structural min2
    for cycle 3).
    """
    if not (prev_had_max1 and prev_max1_date is not None):
        return
    ext_data = df.filter(
        (pl.col("date") >= prev_max1_date)
        & (pl.col("date") <= seg.max2_date)
        & (pl.col("close") > 0)
    )
    if not ext_data.is_empty():
        ext_min_date, ext_min_price = _argmin_row(ext_data)
        if seg.min2_price is None or ext_min_price < seg.min2_price:
            seg.min2_date = ext_min_date
            seg.min2_price = ext_min_price


def adjust_launch_min2(
    df: pl.DataFrame,
    min2_date: date,
    min2_price: float,
    max2_date: date,
) -> tuple[date, float] | None:
    """Adjust min2 when it falls in the launch-price zone.

    If the candidate is within LAUNCH_DATE_BUFFER_DAYS of first data,
    searches for an alternative beyond the buffer. The alternative must
    be a genuine dip (price was higher at some point before it), not
    just launch-price continuation on an ascending slope.

    Returns (date, price) of the valid candidate, or None.
    """
    first_available = df["date"][0]
    if (min2_date - first_available).days > LAUNCH_DATE_BUFFER_DAYS:
        return (min2_date, min2_price)
    # Search beyond launch zone
    buffer_cutoff = first_available + timedelta(days=LAUNCH_DATE_BUFFER_DAYS)
    alt_data = df.filter(
        (pl.col("date") > buffer_cutoff) & (pl.col("date") <= max2_date) & (pl.col("close") > 0)
    )
    if alt_data.is_empty():
        return None
    alt_min_date, alt_min_price = _argmin_row(alt_data)
    # Verify genuine dip: price must have been higher before the alt min2
    pre_dip = df.filter(
        (pl.col("date") > buffer_cutoff)
        & (pl.col("date") < alt_min_date)
        & (pl.col("close") > alt_min_price)
    )
    if pre_dip.is_empty():
        return None
    return (alt_min_date, alt_min_price)


def find_max1_before_min2(
    df: pl.DataFrame,
    seg: SegmentData,
    prev_max1_date: date | None,
    min2_valid: bool,
    prev_cycle: int,
    seg_start_halving: date,
) -> CyclePoint | None:
    """Find max1 before min2 for short-history tokens with no prior max1."""
    if not min2_valid or prev_max1_date is not None:
        return None
    if seg.min2_date is None or seg.min2_price is None or seg.max2_price is None:
        return None
    first_available = df["date"][0]
    max1_search_start = first_available + timedelta(days=LAUNCH_DATE_BUFFER_DAYS)
    max1_data = df.filter(
        (pl.col("date") >= max1_search_start)
        & (pl.col("date") < seg.min2_date)
        & (pl.col("close") > 0)
    )
    if max1_data.is_empty():
        return None
    max1_date, max1_price = _argmax_row(max1_data)
    try:
        ratio = fib_retracement_ratio(seg.min2_price, seg.max2_price, max1_price)
    except ValueError:
        return None
    if (1.0 - ratio) >= MIN_RETRACEMENT_LEVEL:
        return _make_point(max1_date, max1_price, prev_cycle, "max1", seg_start_halving)
    return None


def check_min2_retracement(
    df: pl.DataFrame,
    prev_min1_price: float | None,
    max2_price: float,
    min2_date: date,
    min2_price: float,
    max2_date: date,
    *,
    has_prior_context: bool,
) -> tuple[date, float] | None:
    """Validate min2 via fib retracement, with launch-price fallback.

    If prev_min1_price is available, checks against MIN_RETRACEMENT_LEVEL.
    If has_prior_context is True (e.g. prior max1 exists), accepts the min2.
    Otherwise, delegates to adjust_launch_min2 to suppress launch-price artifacts.

    Returns (date, price) of the valid candidate, or None.
    """
    if prev_min1_price is not None:
        try:
            ratio = fib_retracement_ratio(prev_min1_price, max2_price, min2_price)
            if ratio >= MIN_RETRACEMENT_LEVEL:
                return (min2_date, min2_price)
        except ValueError:
            pass
        return None
    if has_prior_context:
        return (min2_date, min2_price)
    return adjust_launch_min2(df, min2_date, min2_price, max2_date)


def validate_min2(
    df: pl.DataFrame,
    seg: SegmentData,
    s_idx: int,
    prev_had_max1: bool,
    prev_min1_price: float | None,
) -> bool:
    """Validate whether the min2 candidate is structurally significant."""
    if (
        seg.min2_price is None
        or seg.min2_date is None
        or seg.max2_price is None
        or seg.max2_date is None
    ):
        return False
    if not prev_had_max1 and s_idx > 0:
        # Alternation rule: prev segment ended with min (no max1)
        return False
    result = check_min2_retracement(
        df,
        prev_min1_price if s_idx > 0 else None,
        seg.max2_price,
        seg.min2_date,
        seg.min2_price,
        seg.max2_date,
        has_prior_context=False,
    )
    if result is None:
        return False
    seg.min2_date, seg.min2_price = result
    return True


def replace_min1_if_lower(
    df: pl.DataFrame,
    points: list[CyclePoint],
    seg: SegmentData,
    prev_min1_point: CyclePoint,
    halving_ref: date,
) -> tuple[CyclePoint, float]:
    """Replace prev min1 if price went lower before max2.

    When no min2 separates min1 from max2, the bear may have continued.
    Returns (updated min1 point, updated min1 price).
    """
    low_data = df.filter(
        (pl.col("date") > prev_min1_point.date)
        & (pl.col("date") <= seg.max2_date)
        & (pl.col("close") > 0)
    )
    if not low_data.is_empty():
        low_date, low_price = _argmin_row(low_data)
        if low_price < prev_min1_point.price:
            # Remove old min1
            points[:] = [
                p
                for p in points
                if not (p.cycle_num == prev_min1_point.cycle_num and p.point_type == "min1")
            ]
            new_min1 = _make_point(
                low_date, low_price, prev_min1_point.cycle_num, "min1", halving_ref
            )
            points.append(new_min1)
            return new_min1, low_price
    return prev_min1_point, prev_min1_point.price


def find_min1(
    seg: SegmentData,
    min2_valid: bool,
    prev_min1_price: float | None,
    curr_cycle: int,
    seg_end_halving: date,
) -> CyclePoint | None:
    """Find min1: minimum price in (max2_date, effective_end]."""
    if seg.max2_date is None or seg.max2_price is None:
        return None
    min1_data = seg.valid_data.filter(
        (pl.col("date") > seg.max2_date) & (pl.col("date") <= seg.effective_end)
    )
    if min1_data.is_empty():
        return None

    min1_date, min1_price = _argmin_row(min1_data)

    ref_price = seg.min2_price if min2_valid and seg.min2_price else prev_min1_price
    if ref_price is not None:
        try:
            ratio = fib_retracement_ratio(ref_price, seg.max2_price, min1_price)
        except ValueError:
            return None
        if ratio >= MIN_RETRACEMENT_LEVEL:
            return _make_point(min1_date, min1_price, curr_cycle, "min1", seg_end_halving)
        # For the last segment (in-progress cycle), project min1 at 23.6%
        if seg.is_last:
            return _project_min1(min1_date, seg.max2_price, ref_price, curr_cycle, seg_end_halving)
        return None
    # No reference price — still require min1 below max2
    if min1_price < seg.max2_price:
        return _make_point(min1_date, min1_price, curr_cycle, "min1", seg_end_halving)
    return None


def find_max1(
    df: pl.DataFrame,
    seg: SegmentData,
    segments: list[SegmentData | None],
    s_idx: int,
    min1_point: CyclePoint | None,
    curr_cycle: int,
    seg_end_halving: date,
) -> CyclePoint | None:
    """Find max1: max in [min1_date, seg_end], extended to next min2."""
    if min1_point is None or seg.max2_price is None:
        return None

    max1_search_end = seg.effective_end
    if s_idx + 1 < len(segments) and segments[s_idx + 1] is not None:
        next_seg = segments[s_idx + 1]
        if next_seg is not None and next_seg.min2_date is not None:
            max1_search_end = max(max1_search_end, next_seg.min2_date)

    max1_data = seg.valid_data.filter(
        (pl.col("date") >= min1_point.date) & (pl.col("date") <= max1_search_end)
    )
    if max1_search_end > seg.effective_end and s_idx + 1 < len(segments):
        next_seg = segments[s_idx + 1]
        if next_seg is not None:
            ext_data = next_seg.valid_data.filter(
                (pl.col("date") > seg.effective_end) & (pl.col("date") <= max1_search_end)
            )
            max1_data = pl.concat([max1_data, ext_data])

    if max1_data.is_empty():
        return None

    max1_date, max1_price = _argmax_row(max1_data)

    try:
        ratio = fib_retracement_ratio(min1_point.price, seg.max2_price, max1_price)
    except ValueError:
        return None
    if (1.0 - ratio) >= MIN_RETRACEMENT_LEVEL:
        return _make_point(max1_date, max1_price, curr_cycle, "max1", seg_end_halving)
    return None


def correct_min1_with_max1(
    df: pl.DataFrame,
    min1_point: CyclePoint,
    max1_point: CyclePoint,
    curr_cycle: int,
    seg_end_halving: date,
) -> CyclePoint:
    """Correct min1 using max1 as boundary.

    The initial min1 search is bounded by the segment end. The true bottom
    may occur a few days past the halving. Rescan [min1, max1) for a lower.
    """
    corr_data = df.filter(
        (pl.col("date") >= min1_point.date)
        & (pl.col("date") < max1_point.date)
        & (pl.col("close") > 0)
    )
    if not corr_data.is_empty():
        corr_date, corr_price = _argmin_row(corr_data)
        if corr_price < min1_point.price:
            return _make_point(corr_date, corr_price, curr_cycle, "min1", seg_end_halving)
    return min1_point


def detect_post_halving_points(
    df: pl.DataFrame,
    points: list[CyclePoint],
    halvings: list[date],
    last_price_date: date,
    segments: list[SegmentData | None],
    state: _SegmentIterState,
) -> None:
    """Handle last/current segment beyond the final halving.

    Detects max2, min2, and min1 in post-halving data (current cycle).

    Uses an inclusive ``>= last_halving`` bound so the halving date itself
    is owned by the post-halving segment — consistent with the half-open
    ``[seg_start, seg_end)`` convention used by ``build_segments`` (which
    excludes ``halvings[-1]`` from the prior segment).
    """
    last_halving = halvings[-1]
    if last_price_date < last_halving:
        return

    post_data = df.filter((pl.col("date") >= last_halving) & (pl.col("close") > 0))
    if post_data.is_empty():
        return

    last_cycle = len(HALVING_DATES)

    # max2 for the current cycle
    max2_date, max2_price = _argmax_row(post_data)
    points.append(_make_point(max2_date, max2_price, last_cycle, "max2", last_halving))

    # min2: dip between prev max1 (or first available date) and max2
    last_min2_valid = False
    last_min2_price: float | None = None
    if state.had_max1:
        if state.max1_date is not None:
            min2_search_start = state.max1_date
        else:
            # No prior segments — fall back to first available data date
            min2_search_start = df["date"][0]
        min2_ext = df.filter(
            (pl.col("date") >= min2_search_start)
            & (pl.col("date") <= max2_date)
            & (pl.col("close") > 0)
        )
        if not min2_ext.is_empty():
            min2_date, min2_price = _argmin_row(min2_ext)
            result = check_min2_retracement(
                df,
                state.min1_price,
                max2_price,
                min2_date,
                min2_price,
                max2_date,
                has_prior_context=state.max1_date is not None,
            )
            if result is not None:
                min2_date, min2_price = result
                last_min2_valid = True
                last_min2_price = min2_price
                points.append(_make_point(min2_date, min2_price, last_cycle, "min2", last_halving))

    # Merge adjacent maxes when no min2 separates them
    if not last_min2_valid and state.had_max1 and state.max1_date is not None:
        points, max2_price = merge_adjacent_maxes(
            points, state.max1_date, max2_date, max2_price, last_cycle, last_halving
        )

    # min1 for the next cycle (if bear has started)
    min1_after = post_data.filter(pl.col("date") > max2_date)
    if not min1_after.is_empty():
        min1_date, min1_price = _argmin_row(min1_after)

        # Use min2 as reference if detected, otherwise fall back to prev min1
        ref = last_min2_price if last_min2_price is not None else state.min1_price
        if ref is not None:
            try:
                ratio = fib_retracement_ratio(ref, max2_price, min1_price)
            except ValueError:
                ratio = None
            if ratio is not None and ratio >= MIN_RETRACEMENT_LEVEL:
                # Actual min1: retracement is deep enough
                points.append(
                    _make_point(min1_date, min1_price, last_cycle + 1, "min1", last_halving)
                )
            else:
                projected = _project_min1(min1_date, max2_price, ref, last_cycle + 1, last_halving)
                if projected:
                    points.append(projected)
        elif min1_price < max2_price:
            # No reference price — still require min1 below max2
            points.append(_make_point(min1_date, min1_price, last_cycle + 1, "min1", last_halving))

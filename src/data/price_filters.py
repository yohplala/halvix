"""
Common price data filtering tools for Halvix (polars-backed).

These filters are shared between:
- TOTAL2 calculation (processor.py)
- Pattern analysis (cycle_patterns.py)

Provides:
- Volume outlier detection and correction
- Volume SMA smoothing with zero padding
- Symbol replacement detection
- Round-trip spike-and-revert detection

Wide frames (dates × coins) carry a ``date`` column plus one column per coin.
Single-series helpers take the value column and a parallel ``dates`` sequence,
since a polars Series has no index to label dates with.
"""

import math
from collections.abc import Sequence
from datetime import date
from typing import Any

import numpy as np
import polars as pl

from config import (
    PRICE_ROUND_TRIP_JUMP_THRESHOLD,
    PRICE_ROUND_TRIP_REVERT_THRESHOLD,
    PRICE_ROUND_TRIP_WINDOW_DAYS,
    SYMBOL_REPLACEMENT_DECREASE_THRESHOLD,
    SYMBOL_REPLACEMENT_INCREASE_THRESHOLD,
)
from utils.logging import get_logger

logger = get_logger(__name__)

# =============================================================================
# Volume Outlier Detection Parameters
# =============================================================================

# Default parameters - can be overridden when calling functions
DEFAULT_VOLUME_OUTLIER_THRESHOLD = 20  # 20x median
DEFAULT_MIN_VOLUME_FOR_OUTLIER_CHECK = 5000  # BTC
DEFAULT_OUTLIER_WINDOW_DAYS = 7
DEFAULT_VOLUME_SMA_WINDOW = 120


def _coin_columns(df: pl.DataFrame) -> list[str]:
    """Column names of a wide (dates × coins) frame, excluding ``date``."""
    return [c for c in df.columns if c != "date"]


def _wide_from_matrix(dates: Sequence, coin_cols: Sequence[str], vals: np.ndarray) -> pl.DataFrame:
    """
    Rebuild a wide frame from a ``date`` column and a days×coins matrix.

    numpy uses NaN for absent cells; polars distinguishes NaN from null and
    (unlike pandas) does NOT skip NaN in ``is_not_null`` / rolling aggregations.
    Convert NaN → null so missing days behave like pandas' skipped-NaN semantics.
    """
    data: dict[str, Any] = {"date": list(dates)}
    for j, col in enumerate(coin_cols):
        data[col] = vals[:, j]
    return pl.DataFrame(data).with_columns(pl.col(c).fill_nan(None) for c in coin_cols)


def apply_volume_corrections_to_dataframe(
    volume_df: pl.DataFrame,
    threshold: float = DEFAULT_VOLUME_OUTLIER_THRESHOLD,
    min_volume: float = DEFAULT_MIN_VOLUME_FOR_OUTLIER_CHECK,
    window_days: int = DEFAULT_OUTLIER_WINDOW_DAYS,
    max_iterations: int = 10,
    show_progress: bool = False,
) -> tuple[pl.DataFrame, list[dict]]:
    """
    Apply volume outlier corrections to a wide (dates × coins) frame.

    A cell whose volume exceeds ``threshold``× its trailing rolling median (and
    is above ``min_volume``) is capped and blended with the prior day, iterating
    until no outliers remain.

    Args:
        volume_df: Wide frame with a ``date`` column and one column per coin.
        threshold: Multiple of median that triggers outlier detection.
        min_volume: Minimum volume to consider for outlier check.
        window_days: Rolling window size for median calculation.
        max_iterations: Maximum correction iterations.
        show_progress: Whether to log correction messages.

    Returns:
        Tuple of (corrected_df, all_corrections).
    """
    coin_cols = _coin_columns(volume_df)
    dates = volume_df["date"].to_list()
    # days × coins matrix; nulls become NaN (matches the previous pandas path).
    vals = volume_df.select(coin_cols).to_numpy().astype(float)
    all_corrections: list[dict[str, Any]] = []

    for iteration in range(max_iterations):
        corrections_made: list[dict[str, Any]] = []

        # Trailing rolling median (min 3 obs), shifted one day so a spike does
        # not median-mask itself. NaN → null first so absent days are skipped
        # (matching pandas' NaN-skipping rolling median).
        cur = pl.DataFrame({col: vals[:, j] for j, col in enumerate(coin_cols)}).with_columns(
            pl.col(col).fill_nan(None) for col in coin_cols
        )
        past_median = cur.select(
            pl.col(col).rolling_median(window_size=window_days, min_samples=3).shift(1)
            for col in coin_cols
        ).to_numpy()

        # Only form the ratio where both operands are finite and the median is
        # positive; elsewhere it is meaningless (missing days / warm-up). Dividing
        # only on those cells avoids divide-by-zero and NaN warnings at the source.
        safe = np.isfinite(vals) & np.isfinite(past_median) & (past_median > 0)
        ratio = np.zeros_like(vals)
        np.divide(vals, past_median, out=ratio, where=safe)

        is_outlier = (ratio > threshold) & (vals > min_volume) & safe
        rows, cols = np.where(is_outlier)
        if len(rows) == 0:
            break

        for idx, col_idx in zip(rows, cols, strict=True):
            original_vol = vals[idx, col_idx]
            ratio_val = ratio[idx, col_idx]
            median_val = past_median[idx, col_idx]
            prev_vol = vals[idx - 1, col_idx] if idx > 0 else np.nan

            if not np.isfinite(prev_vol) or prev_vol <= 0:
                continue
            if not np.isfinite(median_val) or median_val <= 0:
                continue

            capped_value = median_val * threshold
            interpolated = (prev_vol + min(original_vol, capped_value)) / 2
            if interpolated <= 0:
                continue

            vals[idx, col_idx] = interpolated
            corrections_made.append(
                {
                    "coin": coin_cols[col_idx].upper(),
                    "date": str(dates[idx]),
                    "original": float(original_vol),
                    "corrected": float(interpolated),
                    "ratio": float(ratio_val) if np.isfinite(ratio_val) else 0.0,
                    "iteration": iteration + 1,
                }
            )

        all_corrections.extend(corrections_made)

        if show_progress and corrections_made:
            logger.info(
                "Volume outlier iteration %d: %d corrections",
                iteration + 1,
                len(corrections_made),
            )

    all_corrections = sorted(all_corrections, key=lambda x: x["ratio"], reverse=True)

    if all_corrections and show_progress:
        logger.info("Volume outlier corrections (%d total):", len(all_corrections))
        for c in all_corrections[:20]:
            iter_str = f" (iter {c['iteration']})" if c.get("iteration", 1) > 1 else ""
            original_str = f"{c['original']:>15,.2f}"
            corrected_str = f"{c['corrected']:>12,.2f}"
            ratio_str = f"{c['ratio']:,.0f}"
            logger.info(
                "  %6s %s: %s → %s (%sx median)%s",
                c["coin"],
                c["date"],
                original_str,
                corrected_str,
                ratio_str,
                iter_str,
            )
        if len(all_corrections) > 20:
            logger.info("  ... and %d more", len(all_corrections) - 20)

    return _wide_from_matrix(dates, coin_cols, vals), all_corrections


def apply_volume_sma_smoothing_to_dataframe(
    volume_df: pl.DataFrame,
    window: int = DEFAULT_VOLUME_SMA_WINDOW,
    zero_pad: bool = True,
) -> pl.DataFrame:
    """
    Apply SMA smoothing to a wide volume frame, optionally with zero padding.

    Zero-padding sets every day before a coin's first observation to 0 so new
    coins enter indices gradually over the SMA window (no sudden weight jumps).

    Args:
        volume_df: Wide frame with a ``date`` column and one column per coin.
        window: SMA window size in days.
        zero_pad: If True, pad with zeros before each coin's first valid value.

    Returns:
        Smoothed wide volume frame (``date`` column preserved).
    """
    coin_cols = _coin_columns(volume_df)
    work = volume_df

    if zero_pad:
        pad_exprs = []
        for col in coin_cols:
            first_valid = work.filter(pl.col(col).is_not_null()).select(pl.col("date").min()).item()
            if first_valid is not None:
                pad_exprs.append(
                    pl.when(pl.col("date") < first_valid)
                    .then(0.0)
                    .otherwise(pl.col(col))
                    .alias(col)
                )
        if pad_exprs:
            work = work.with_columns(pad_exprs)

    smoothed = work.with_columns(pl.col(col).rolling_mean(window_size=window) for col in coin_cols)
    return smoothed


# =============================================================================
# Symbol Replacement Detection
# =============================================================================

# Default threshold for numerical zero (avoids division by zero issues)
DEFAULT_ZERO_THRESHOLD = 1e-15


def detect_symbol_replacement(
    close: pl.Series,
    dates: Sequence,
    increase_threshold: float = SYMBOL_REPLACEMENT_INCREASE_THRESHOLD,
    decrease_threshold: float = SYMBOL_REPLACEMENT_DECREASE_THRESHOLD,
    first_seen: date | None = None,
) -> date | None:
    """
    Detect if a coin's symbol was replaced by a different token.

    Providers sometimes reuse symbols for different tokens (e.g.,
    old worthless "HYPE" replaced by Hyperliquid "HYPE" in Dec 2024,
    or LIT changed from Litentry to Lighter in Jan 2026).

    Detection methods:
    1. **Extreme ratio jump**: Price ratio exceeds asymmetric thresholds between
       consecutive days where both prices are positive. Increases use a lower
       threshold (4.42x) since legitimate daily gains that large are virtually
       impossible on BTC pairs. Decreases use a higher bar (ratio < 0.101)
       to avoid flagging legitimate crashes like OM/MANTRA.
    2. **Resurrection from zero**: Price transitions from zero to positive after
       a period of zero prices, when there was trading before the zero period.
       This catches cases like MOVE where the old token went to exactly 0.

    Args:
        close: Series of close prices for a coin.
        dates: Parallel sequence of dates (same length/order as ``close``).
        increase_threshold: Ratio above which an increase flags replacement.
        decrease_threshold: Ratio below which a decrease flags replacement.
        first_seen: If provided, only returns replacement dates after it.

    Returns:
        The date of the last symbol replacement, or None if none detected.
    """
    values = close.to_numpy().astype(float)
    if values.size == 0:
        return None

    prev = np.empty_like(values)
    prev[0] = np.nan
    prev[1:] = values[:-1]

    # Form the day-over-day ratio only between two genuinely positive closes;
    # restricting the division to those cells (prev > 1e-15) keeps micro-price
    # coins (e.g. LUNC at ~3e-10) from overflowing float64 and avoids
    # divide-by-zero warnings without suppressing them.
    valid_ratio = (values > DEFAULT_ZERO_THRESHOLD) & (prev > DEFAULT_ZERO_THRESHOLD)
    ratio = np.ones_like(values)
    np.divide(values, prev, out=ratio, where=valid_ratio)

    # Method 1: extreme jump/crash between consecutive positive closes.
    extreme_jumps = ((ratio > increase_threshold) | (ratio < decrease_threshold)) & valid_ratio

    # Method 2: resurrection from zero, but only if the coin traded BEFORE the
    # zero period (else it is simply a coin starting to trade).
    resurrection = (values > DEFAULT_ZERO_THRESHOLD) & (prev <= DEFAULT_ZERO_THRESHOLD)
    positive = (values > DEFAULT_ZERO_THRESHOLD).astype(np.int64)
    had_positive_before = np.empty(values.shape, dtype=bool)
    had_positive_before[0] = False
    had_positive_before[1:] = np.cumsum(positive)[:-1] > 0
    valid_resurrection = resurrection & had_positive_before

    combined = extreme_jumps | valid_resurrection
    positions = np.flatnonzero(combined)
    if positions.size == 0:
        return None

    last_jump_date = dates[int(positions[-1])]
    if first_seen is not None and last_jump_date <= first_seen:
        return None
    return last_jump_date


# =============================================================================
# Round-Trip Detection (single-day or multi-day spike-and-revert)
# =============================================================================


def detect_round_trips(
    close: pl.Series,
    dates: Sequence,
    jump_threshold: float = PRICE_ROUND_TRIP_JUMP_THRESHOLD,
    revert_threshold: float = PRICE_ROUND_TRIP_REVERT_THRESHOLD,
    window_days: int = PRICE_ROUND_TRIP_WINDOW_DAYS,
) -> list[dict]:
    """
    Detect spike-and-revert patterns (single-day or multi-day) in a close series.

    Distinct from symbol-replacement detection: symbol replacement assumes the
    new price is permanent and resets first_seen (ejecting the coin). Round-trip
    detection assumes the spike is transient (low-liquidity pump-and-dump or a
    glitchy daily close) and the proper remedy is to smooth every elevated day
    in the pattern back to the pre-spike baseline, keeping the coin in the index.

    At each position i, the detector scans the forward window [i, i+window_days]
    for an extremum:
      - up-candidate:   max(close in window) / close[i-1] > jump_threshold
      - down-candidate: min(close in window) / close[i-1] < 1/jump_threshold
    If a candidate is found AND the price returns to baseline within the same
    window AFTER the extremum (revert_ratio inside [1/revert_threshold,
    revert_threshold]), an event is recorded covering every day from i through
    the extremum (the elevated/depressed span). The revert day itself is NOT
    smoothed.

    Args:
        close: Series of close prices for a coin.
        dates: Parallel sequence of dates (same length/order as ``close``).
        jump_threshold: Window-extremum ratio that triggers a candidate (>1).
        revert_threshold: Tolerance band around the pre-spike price for
            declaring a revert (>1; e.g. 1.5 means within ±50% of pre-spike).
        window_days: Forward window size in days.

    Returns:
        List of event dictionaries with keys: date (first elevated day),
        direction, days_to_revert (revert_idx - i), pre_price, jump_price (the
        extremum value), revert_price, jump_ratio, revert_ratio, smoothed_dates
        (every date in the elevated span — empty for none).
    """
    events: list[dict] = []

    values = close.to_numpy().astype(float)
    n = values.size
    if n < 3:
        return events

    if jump_threshold <= 1 or revert_threshold <= 1:
        raise ValueError("jump_threshold and revert_threshold must be > 1")

    inv_jump = 1.0 / jump_threshold
    inv_revert = 1.0 / revert_threshold

    # Track indices that already belong to a recorded event's span (from spike
    # start through the revert day). Re-flagging a day inside that range as a
    # fresh event would smooth a legitimate baseline back to the spike value.
    skip_until: int = -1

    for i in range(1, n - 1):
        if i <= skip_until:
            continue
        p_pre = values[i - 1]
        if not (p_pre > DEFAULT_ZERO_THRESHOLD):
            continue

        end = min(i + window_days, n - 1)
        if end <= i:
            continue

        # Scan window for both extrema in one pass.
        max_idx, max_val = -1, -1.0
        min_idx, min_val = -1, math.inf
        for j in range(i, end + 1):
            v = values[j]
            if v <= DEFAULT_ZERO_THRESHOLD:
                continue
            if v > max_val:
                max_idx, max_val = j, v
            if v < min_val:
                min_idx, min_val = j, v

        if max_idx < 0:
            continue  # no valid prices in window

        up_candidate = max_val / p_pre > jump_threshold
        down_candidate = min_val < math.inf and min_val / p_pre < inv_jump

        # If both fire (a pump that crashes — see RAVE), prefer the one whose
        # extremum comes first in time: that's where the round-trip *starts*.
        if up_candidate and (not down_candidate or max_idx <= min_idx):
            direction, extremum_idx, extremum_val = "up", max_idx, max_val
        elif down_candidate:
            direction, extremum_idx, extremum_val = "down", min_idx, min_val
        else:
            continue

        # Look for revert AFTER the extremum, within the same window.
        revert_k_idx = -1
        revert_val = 0.0
        for k_idx in range(extremum_idx + 1, end + 1):
            v = values[k_idx]
            if v <= DEFAULT_ZERO_THRESHOLD:
                continue
            revert_ratio = v / p_pre
            reverted = (direction == "up" and revert_ratio < revert_threshold) or (
                direction == "down" and revert_ratio > inv_revert
            )
            if reverted:
                revert_k_idx = k_idx
                revert_val = v
                break
        if revert_k_idx < 0:
            continue

        # Locate the actual spike start: walk backwards from extremum_idx and
        # include a CONTIGUOUS run of days that are strictly on the spike side
        # of p_pre (above for "up", below for "down"). Stop at the first day
        # that is NOT on the spike side — those days are baseline (or worse,
        # noise on the opposite side) and must not be smoothed.
        #
        # Without this guard, e.g. [10, 12, 11, 9, 14, 50, 8, ...] iterated
        # from i=1 would pick spike_start=1 (12 > 10 satisfies the bare
        # v > p_pre check) and smooth idx 1..5 — including idx 3 (9, BELOW
        # p_pre, clearly not part of an upward spike).
        spike_start_idx = extremum_idx
        for j in range(extremum_idx - 1, i - 1, -1):
            v = values[j]
            if v <= DEFAULT_ZERO_THRESHOLD:
                break  # opaque gap; treat as baseline
            if direction == "up" and v > p_pre:
                spike_start_idx = j
                continue
            if direction == "down" and v < p_pre:
                spike_start_idx = j
                continue
            # v is at or on the non-spike side of p_pre: spike ends here.
            break

        events.append(
            {
                "date": dates[spike_start_idx],
                "direction": direction,
                "days_to_revert": revert_k_idx - spike_start_idx,
                "pre_price": float(p_pre),
                "jump_price": float(extremum_val),
                "revert_price": float(revert_val),
                "jump_ratio": float(extremum_val / p_pre),
                "revert_ratio": float(revert_val / p_pre),
                # Smooth every elevated day from spike start through the day
                # before revert. The revert day itself is the genuine return
                # to baseline and is NOT smoothed.
                "smoothed_dates": [dates[j] for j in range(spike_start_idx, revert_k_idx)],
            }
        )
        # Skip the entire span (start through revert) so an interior day can't
        # be re-flagged as a fresh event of the opposite direction (e.g. the
        # revert day looking like a down-spike of the prior peak).
        skip_until = revert_k_idx

    return events


def smooth_round_trips_on_series(
    df: pl.DataFrame,
    value_col: str = "close",
    jump_threshold: float = PRICE_ROUND_TRIP_JUMP_THRESHOLD,
    revert_threshold: float = PRICE_ROUND_TRIP_REVERT_THRESHOLD,
    window_days: int = PRICE_ROUND_TRIP_WINDOW_DAYS,
) -> tuple[pl.DataFrame, list[dict]]:
    """
    Smooth round-trip spike spans on a single-coin price frame.

    Each detected event replaces every day in the elevated/depressed span with
    the pre-spike baseline; the revert day itself is left untouched. The input
    frame is not mutated — the same frame is returned when no events fire,
    otherwise a new frame with ``value_col`` smoothed.

    Args:
        df: Frame with ``date`` and ``value_col`` columns (date-sorted).
        value_col: Name of the price column to smooth.
        jump_threshold: See detect_round_trips.
        revert_threshold: See detect_round_trips.
        window_days: See detect_round_trips.

    Returns:
        Tuple of (corrected_df, events). ``events`` is the raw list from
        ``detect_round_trips`` so callers can format their own log messages.
    """
    if df.is_empty():
        return df, []
    dates = df["date"].to_list()
    events = detect_round_trips(
        df[value_col],
        dates,
        jump_threshold=jump_threshold,
        revert_threshold=revert_threshold,
        window_days=window_days,
    )
    if not events:
        return df, events

    date_pos = {d: i for i, d in enumerate(dates)}
    vals = df[value_col].to_numpy().astype(float).copy()
    for ev in events:
        for dt in ev["smoothed_dates"]:
            vals[date_pos[dt]] = ev["pre_price"]
    corrected = df.with_columns(pl.Series(value_col, vals))
    return corrected, events


def apply_round_trip_smoothing(
    df: pl.DataFrame,
    value_col: str = "close",
    log_label: str | None = None,
) -> pl.DataFrame:
    """
    Detect and smooth round-trip glitches on a single-coin frame.

    Shared entry point for the analysis and visualization paths so the two
    pipelines stay in sync. When ``log_label`` is given, one line is logged per
    event (the chart path passes ``None`` because the analyzer already logged).

    Args:
        df: Frame with ``date`` and ``value_col`` columns (date-sorted).
        value_col: Price column to smooth.
        log_label: Coin label for per-event logging, or None to stay silent.

    Returns:
        The frame with ``value_col`` smoothed (same frame if nothing fired).
    """
    corrected, events = smooth_round_trips_on_series(df, value_col=value_col)
    if not events:
        return df
    if log_label is not None:
        for ev in events:
            smoothed = ev["smoothed_dates"]
            span = f"{smoothed[0]}" if len(smoothed) == 1 else f"{smoothed[0]}..{smoothed[-1]}"
            logger.info(
                "%s: round-trip glitch on %s smoothed: peak %.3e → %.3e (jump %.2fx, "
                "revert %.2fx after %dd, %s)",
                log_label,
                span,
                ev["jump_price"],
                ev["pre_price"],
                ev["jump_ratio"],
                ev["revert_ratio"],
                ev["days_to_revert"],
                ev["direction"],
            )
    return corrected


def filter_to_post_replacement(
    df: pl.DataFrame,
    value_col: str = "close",
    log_label: str | None = None,
) -> pl.DataFrame:
    """
    Drop pre-symbol-replacement history from a single-coin frame.

    Shared by the analysis and visualization paths. When a symbol was recycled
    for a different token, keep only rows on/after the replacement date so the
    old token's history cannot contaminate detection.

    Args:
        df: Frame with ``date`` and ``value_col`` columns (date-sorted).
        value_col: Price column used for detection.
        log_label: Coin label for logging, or None to stay silent.

    Returns:
        The filtered frame (unchanged if no replacement was detected).
    """
    if df.is_empty() or value_col not in df.columns:
        return df
    replacement_date = detect_symbol_replacement(df[value_col], df["date"].to_list())
    if replacement_date is None:
        return df
    if log_label is not None:
        logger.info(
            "%s: Symbol replacement detected on %s, filtering to post-replacement data",
            log_label,
            replacement_date,
        )
    return df.filter(pl.col("date") >= replacement_date)


def apply_round_trip_corrections_to_dataframe(
    close_df: pl.DataFrame,
    jump_threshold: float = PRICE_ROUND_TRIP_JUMP_THRESHOLD,
    revert_threshold: float = PRICE_ROUND_TRIP_REVERT_THRESHOLD,
    window_days: int = PRICE_ROUND_TRIP_WINDOW_DAYS,
    show_progress: bool = False,
) -> tuple[pl.DataFrame, list[dict]]:
    """
    Smooth round-trip spike spans across a wide (dates × coins) close frame.

    For each detected event, every day in the elevated/depressed span (from
    spike start through the extremum) is replaced with the pre-spike baseline.
    The revert day itself is left untouched. This neutralises the glitch for
    TOTAL2 calculation while keeping the coin in the index (unlike symbol-
    replacement detection, which ejects the coin for 21 days).

    Args:
        close_df: Wide frame with a ``date`` column and one column per coin.
        jump_threshold: See detect_round_trips.
        revert_threshold: See detect_round_trips.
        window_days: See detect_round_trips.
        show_progress: Log a summary of corrections applied.

    Returns:
        Tuple of (corrected_df, all_corrections). One correction record per
        smoothed day, carrying the spike-pattern metadata.
    """
    coin_cols = _coin_columns(close_df)
    dates = close_df["date"].to_list()
    date_pos = {d: i for i, d in enumerate(dates)}
    vals = close_df.select(coin_cols).to_numpy().astype(float)
    all_corrections: list[dict] = []

    for j, coin_id in enumerate(coin_cols):
        events = detect_round_trips(
            close_df[coin_id],
            dates,
            jump_threshold=jump_threshold,
            revert_threshold=revert_threshold,
            window_days=window_days,
        )
        for ev in events:
            corrected = ev["pre_price"]
            coin_label = coin_id.upper()
            for dt in ev["smoothed_dates"]:
                idx = date_pos[dt]
                original = float(vals[idx, j])
                vals[idx, j] = corrected
                all_corrections.append(
                    {
                        "coin": coin_label,
                        "date": str(dt),
                        "original": original,
                        "corrected": float(corrected),
                        "jump_ratio": ev["jump_ratio"],
                        "revert_ratio": ev["revert_ratio"],
                        "days_to_revert": ev["days_to_revert"],
                        "direction": ev["direction"],
                    }
                )

    all_corrections.sort(
        key=lambda c: abs(c["jump_ratio"] - 1.0) if c["jump_ratio"] >= 1 else 1.0 / c["jump_ratio"],
        reverse=True,
    )

    if show_progress and all_corrections:
        logger.info("Round-trip price corrections (%d total):", len(all_corrections))
        for c in all_corrections[:20]:
            original_str = f"{c['original']:.2e}"
            corrected_str = f"{c['corrected']:.2e}"
            logger.info(
                "  %6s %s: %s → %s (jump %.2fx, revert %.2fx after %dd, %s)",
                c["coin"],
                c["date"],
                original_str,
                corrected_str,
                c["jump_ratio"],
                c["revert_ratio"],
                c["days_to_revert"],
                c["direction"],
            )
        if len(all_corrections) > 20:
            logger.info("  ... and %d more", len(all_corrections) - 20)

    return _wide_from_matrix(dates, coin_cols, vals), all_corrections

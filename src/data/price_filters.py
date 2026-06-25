"""
Common price data filtering tools for Halvix.

These filters are shared between:
- TOTAL2 calculation (processor.py)
- Pattern analysis (cycle_patterns.py)

Provides:
- Volume outlier detection and correction
- Volume SMA smoothing with zero padding
- Symbol replacement detection
- Round-trip spike-and-revert detection
"""

import math
from typing import Any

import numpy as np
import pandas as pd

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


def apply_volume_corrections_to_dataframe(
    volume_df: pd.DataFrame,
    threshold: float = DEFAULT_VOLUME_OUTLIER_THRESHOLD,
    min_volume: float = DEFAULT_MIN_VOLUME_FOR_OUTLIER_CHECK,
    window_days: int = DEFAULT_OUTLIER_WINDOW_DAYS,
    max_iterations: int = 10,
    show_progress: bool = False,
) -> tuple[pd.DataFrame, list[dict]]:
    """
    Apply volume outlier corrections to a DataFrame of multiple coins.

    This is a convenience function that applies correct_volume_outliers
    to each column (coin) in the DataFrame.

    Args:
        volume_df: DataFrame with volume data (dates × coins)
        threshold: Multiple of median that triggers outlier detection
        min_volume: Minimum volume to consider for outlier check
        window_days: Rolling window size for median calculation
        max_iterations: Maximum correction iterations
        show_progress: Whether to print correction messages

    Returns:
        Tuple of (corrected_df, all_corrections)
    """
    corrected_df = volume_df.copy()
    all_corrections: list[dict[str, Any]] = []

    for iteration in range(max_iterations):
        corrections_made: list[dict[str, Any]] = []

        # Vectorized outlier detection across all columns
        rolling_median = corrected_df.rolling(window=window_days, min_periods=3).median()
        past_median = rolling_median.shift(1)
        ratio_df = corrected_df / past_median

        is_outlier = (ratio_df > threshold) & (corrected_df > min_volume) & (past_median > 0)

        outlier_locations = np.where(is_outlier)

        if len(outlier_locations[0]) == 0:
            break

        for idx, col_idx in zip(outlier_locations[0], outlier_locations[1], strict=True):
            dt = corrected_df.index[idx]
            coin_id = corrected_df.columns[col_idx]
            original_vol = corrected_df.iloc[idx, col_idx]
            ratio = ratio_df.iloc[idx, col_idx]
            median_val = past_median.iloc[idx, col_idx]

            prev_vol = corrected_df.iloc[idx - 1, col_idx] if idx > 0 else np.nan

            if not pd.notna(prev_vol) or prev_vol <= 0:
                continue
            if not pd.notna(median_val) or median_val <= 0:
                continue

            capped_value = median_val * threshold
            interpolated = (prev_vol + min(original_vol, capped_value)) / 2

            if interpolated <= 0:
                continue

            corrected_df.iloc[idx, col_idx] = interpolated

            corrections_made.append(
                {
                    "coin": coin_id.upper() if isinstance(coin_id, str) else str(coin_id),
                    "date": str(dt.date()) if hasattr(dt, "date") else str(dt),
                    "original": float(original_vol),
                    "corrected": float(interpolated),
                    "ratio": float(ratio) if np.isfinite(ratio) else 0.0,
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
            # Pre-format numbers with thousand separators (% formatting doesn't support ,)
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

    return corrected_df, all_corrections


def apply_volume_sma_smoothing_to_dataframe(
    volume_df: pd.DataFrame,
    window: int = DEFAULT_VOLUME_SMA_WINDOW,
    zero_pad: bool = True,
) -> pd.DataFrame:
    """
    Apply SMA smoothing to a DataFrame of volume data with zero padding.

    Zero-padding ensures new coins enter indices gradually over the
    SMA window period, preventing sudden weight jumps.

    Args:
        volume_df: DataFrame with volume data (dates × coins)
        window: SMA window size in days
        zero_pad: If True, pad with zeros before first valid value for each coin

    Returns:
        Smoothed volume DataFrame
    """
    if zero_pad:
        padded_df = volume_df.copy()

        for coin_id in padded_df.columns:
            first_valid_idx = volume_df[coin_id].first_valid_index()
            if first_valid_idx is not None:
                mask = padded_df.index < first_valid_idx
                padded_df.loc[mask, coin_id] = 0.0

        smoothed_df = padded_df.rolling(window=window).mean()
    else:
        smoothed_df = volume_df.rolling(window=window).mean()

    return smoothed_df


# =============================================================================
# Symbol Replacement Detection
# =============================================================================

# Default threshold for numerical zero (avoids division by zero issues)
DEFAULT_ZERO_THRESHOLD = 1e-15


def detect_symbol_replacement(
    price_series: pd.Series,
    increase_threshold: float = SYMBOL_REPLACEMENT_INCREASE_THRESHOLD,
    decrease_threshold: float = SYMBOL_REPLACEMENT_DECREASE_THRESHOLD,
    first_seen: pd.Timestamp | None = None,
) -> pd.Timestamp | None:
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
        price_series: Series of close prices for a coin with DatetimeIndex
        increase_threshold: Ratio above which an increase flags replacement (default: 4.42x)
        decrease_threshold: Ratio below which a decrease flags replacement (default: 0.101x)
        first_seen: Optional first-seen date; if provided, only returns
                   replacement dates that occur after this date

    Returns:
        The date of the last symbol replacement, or None if no replacement detected
    """
    if price_series.empty:
        return None

    # Get previous day's price
    prev_price = price_series.shift(1)

    # Method 1: Extreme ratio detection
    # Calculate daily price change ratios (only where both prices are positive)
    valid_ratio_mask = (price_series > DEFAULT_ZERO_THRESHOLD) & (
        prev_price > DEFAULT_ZERO_THRESHOLD
    )
    price_ratio = price_series / prev_price

    # Find dates with extreme price jumps (asymmetric thresholds)
    extreme_jumps = (
        (price_ratio > increase_threshold) | (price_ratio < decrease_threshold)
    ) & valid_ratio_mask

    # Method 2: Resurrection from zero detection
    # Find dates where price goes from zero to positive
    resurrection_mask = (price_series > DEFAULT_ZERO_THRESHOLD) & (
        prev_price <= DEFAULT_ZERO_THRESHOLD
    )

    # For resurrection to be a symbol replacement (not just coin starting to trade),
    # there must have been trading BEFORE the zero period.
    # Vectorized: cumulative flag tracks whether any prior price was positive.
    had_positive_before = (price_series > DEFAULT_ZERO_THRESHOLD).cumsum().shift(
        1, fill_value=0
    ) > 0
    valid_resurrection_mask = resurrection_mask & had_positive_before

    # Combine both detection methods
    combined_mask = extreme_jumps | valid_resurrection_mask

    all_replacement_dates = price_series.index[combined_mask]

    if all_replacement_dates.empty:
        return None

    # Return the date of the LAST replacement (most recent)
    # This handles cases where a symbol might be replaced multiple times
    last_jump_date = all_replacement_dates[-1]

    # Only consider it a replacement if it happened after the first_seen date
    if first_seen is not None and last_jump_date <= first_seen:
        return None

    return last_jump_date


# =============================================================================
# Round-Trip Detection (single-day or multi-day spike-and-revert)
# =============================================================================


def detect_round_trips(
    price_series: pd.Series,
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

    Examples this catches:
      - Single-day spike: [10, 25, 10] — peak at i, revert at i+1.
      - Multi-day pump-and-dump (RAVE 2026-04-15..18): [10, 11, 17, 22, 3] —
        cumulative 2.2x climb that no single day-over-day reaches, then a 0.13x
        crash. With a wide enough window, the extremum-vs-baseline test fires.

    Args:
        price_series: Series of close prices for a coin with DatetimeIndex
        jump_threshold: Window-extremum ratio that triggers a candidate (>1)
        revert_threshold: Tolerance band around the pre-spike price for
            declaring a revert (>1; e.g. 1.5 means within ±50% of pre-spike)
        window_days: Forward window size in days

    Returns:
        List of event dictionaries with keys: date (first elevated day),
        direction, days_to_revert (revert_idx - i), pre_price, jump_price (the
        extremum value), revert_price, jump_ratio, revert_ratio, smoothed_dates
        (every date in the elevated span — empty for none).
    """
    events: list[dict] = []

    if price_series.empty or len(price_series) < 3:
        return events

    if jump_threshold <= 1 or revert_threshold <= 1:
        raise ValueError("jump_threshold and revert_threshold must be > 1")

    inv_jump = 1.0 / jump_threshold
    inv_revert = 1.0 / revert_threshold

    n = len(price_series)
    values = price_series.to_numpy()
    index = price_series.index

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
                "date": index[spike_start_idx],
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
                "smoothed_dates": [index[j] for j in range(spike_start_idx, revert_k_idx)],
            }
        )
        # Skip the entire span (start through revert) so an interior day can't
        # be re-flagged as a fresh event of the opposite direction (e.g. the
        # revert day looking like a down-spike of the prior peak).
        skip_until = revert_k_idx

    return events


def smooth_round_trips_on_series(
    close_series: pd.Series,
    jump_threshold: float = PRICE_ROUND_TRIP_JUMP_THRESHOLD,
    revert_threshold: float = PRICE_ROUND_TRIP_REVERT_THRESHOLD,
    window_days: int = PRICE_ROUND_TRIP_WINDOW_DAYS,
) -> tuple[pd.Series, list[dict]]:
    """
    Smooth round-trip spike spans on a single close-price Series.

    Series-level counterpart of ``apply_round_trip_corrections_to_dataframe``
    used by the per-coin analysis and visualization paths. Each detected event
    replaces every day in the elevated/depressed span with the pre-spike
    baseline; the revert day itself is left untouched. The input Series is
    not mutated — a copy is returned only when at least one event fires.

    Args:
        close_series: Series of close prices (DatetimeIndex)
        jump_threshold: See detect_round_trips
        revert_threshold: See detect_round_trips
        window_days: See detect_round_trips

    Returns:
        Tuple of (corrected_series, events). ``events`` is the raw list from
        ``detect_round_trips`` (with ``smoothed_dates``, ``pre_price``,
        ``jump_ratio``, etc.) so callers can format their own log messages
        without needing the dict-of-corrections form used by the matrix
        version. ``corrected_series is close_series`` when no events fire,
        otherwise it is a freshly-copied series.
    """
    if close_series.empty:
        return close_series, []
    events = detect_round_trips(
        close_series,
        jump_threshold=jump_threshold,
        revert_threshold=revert_threshold,
        window_days=window_days,
    )
    if not events:
        return close_series, events
    corrected = close_series.copy()
    for ev in events:
        for dt in ev["smoothed_dates"]:
            corrected.at[dt] = ev["pre_price"]
    return corrected, events


def apply_round_trip_corrections_to_dataframe(
    close_df: pd.DataFrame,
    jump_threshold: float = PRICE_ROUND_TRIP_JUMP_THRESHOLD,
    revert_threshold: float = PRICE_ROUND_TRIP_REVERT_THRESHOLD,
    window_days: int = PRICE_ROUND_TRIP_WINDOW_DAYS,
    show_progress: bool = False,
) -> tuple[pd.DataFrame, list[dict]]:
    """
    Smooth round-trip spike spans across a multi-coin close-price DataFrame.

    For each detected event, every day in the elevated/depressed span (from
    spike start through the extremum) is replaced with the pre-spike baseline.
    The revert day itself is left untouched. This neutralises the glitch for
    TOTAL2 calculation while keeping the coin in the index (unlike symbol-
    replacement detection, which ejects the coin for 21 days).

    Args:
        close_df: DataFrame of close prices (dates × coins)
        jump_threshold: See detect_round_trips
        revert_threshold: See detect_round_trips
        window_days: See detect_round_trips
        show_progress: Log a summary of corrections applied

    Returns:
        Tuple of (corrected_df, all_corrections). One correction record per
        smoothed day, carrying the spike-pattern metadata (jump_ratio,
        revert_ratio, days_to_revert, direction). A multi-day event produces
        one record per smoothed day, all sharing the same metadata.
    """
    corrected_df = close_df.copy()
    all_corrections: list[dict] = []

    for coin_id in corrected_df.columns:
        events = detect_round_trips(
            corrected_df[coin_id],
            jump_threshold=jump_threshold,
            revert_threshold=revert_threshold,
            window_days=window_days,
        )
        for ev in events:
            corrected = ev["pre_price"]
            coin_label = coin_id.upper() if isinstance(coin_id, str) else str(coin_id)
            for dt in ev["smoothed_dates"]:
                original = float(corrected_df.at[dt, coin_id])
                corrected_df.at[dt, coin_id] = corrected
                all_corrections.append(
                    {
                        "coin": coin_label,
                        "date": str(dt.date()) if hasattr(dt, "date") else str(dt),
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

    return corrected_df, all_corrections

"""
Common price data filtering tools for Halvix.

These filters are shared between:
- TOTAL2/TOTAL2b calculation (processor_base.py, processor_total2b.py)
- Pattern analysis (cycle_patterns.py)

Provides:
- Volume outlier detection and correction
- Volume SMA smoothing with zero padding
- Price outlier detection

Using these common helpers ensures consistent data quality across
all analysis modules.
"""

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
    all_corrections = []

    for iteration in range(max_iterations):
        corrections_made = []

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

    CryptoCompare sometimes reuses symbols for different tokens (e.g.,
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
# Round-Trip Detection (single-day spike-and-revert)
# =============================================================================


def detect_round_trips(
    price_series: pd.Series,
    jump_threshold: float = PRICE_ROUND_TRIP_JUMP_THRESHOLD,
    revert_threshold: float = PRICE_ROUND_TRIP_REVERT_THRESHOLD,
    window_days: int = PRICE_ROUND_TRIP_WINDOW_DAYS,
) -> list[dict]:
    """
    Detect single-day price spikes that revert within a short window.

    Distinct from symbol-replacement detection: symbol replacement assumes the
    new price is permanent and resets first_seen (ejecting the coin). Round-trip
    detection assumes the spike is transient (low-liquidity pump-and-dump or a
    glitchy daily close) and the proper remedy is to smooth the spike day, not
    to eject the coin from the index.

    A day D is flagged when BOTH:
    1. The single-day ratio price(D)/price(D-1) exceeds jump_threshold (up-spike)
       OR falls below 1/jump_threshold (down-spike).
    2. The price returns close to its pre-jump value within `window_days`:
       price(D+k)/price(D-1) is back inside [1/revert_threshold, revert_threshold]
       for some k in [1, window_days].

    Args:
        price_series: Series of close prices for a coin with DatetimeIndex
        jump_threshold: Single-day ratio that triggers a candidate jump (>1)
        revert_threshold: Tolerance band around the pre-jump price for declaring
            a revert (>1; e.g. 1.5 means within ±50% of pre-jump)
        window_days: How many days after the jump to look for a revert

    Returns:
        List of event dictionaries with keys: date, direction, days_to_revert,
        pre_price, jump_price, revert_price, jump_ratio, revert_ratio.
    """
    events: list[dict] = []

    if price_series.empty or len(price_series) < 3:
        return events

    if jump_threshold <= 1 or revert_threshold <= 1:
        raise ValueError("jump_threshold and revert_threshold must be > 1")

    inv_jump = 1.0 / jump_threshold
    inv_revert = 1.0 / revert_threshold

    # Iterate by integer position so we can look ahead `window_days` safely.
    n = len(price_series)
    values = price_series.to_numpy()
    index = price_series.index

    for i in range(1, n - 1):
        p_pre = values[i - 1]
        p_jump = values[i]
        if not (p_pre > DEFAULT_ZERO_THRESHOLD and p_jump > DEFAULT_ZERO_THRESHOLD):
            continue

        ratio = p_jump / p_pre
        if ratio > jump_threshold:
            direction = "up"
        elif ratio < inv_jump:
            direction = "down"
        else:
            continue

        # Look ahead for revert
        max_k = min(window_days, n - 1 - i)
        for k in range(1, max_k + 1):
            p_after = values[i + k]
            if not (p_after > DEFAULT_ZERO_THRESHOLD):
                continue
            revert_ratio = p_after / p_pre
            reverted = (direction == "up" and revert_ratio < revert_threshold) or (
                direction == "down" and revert_ratio > inv_revert
            )
            if reverted:
                events.append(
                    {
                        "date": index[i],
                        "direction": direction,
                        "days_to_revert": k,
                        "pre_price": float(p_pre),
                        "jump_price": float(p_jump),
                        "revert_price": float(p_after),
                        "jump_ratio": float(ratio),
                        "revert_ratio": float(revert_ratio),
                    }
                )
                break

    return events


def apply_round_trip_corrections_to_dataframe(
    close_df: pd.DataFrame,
    jump_threshold: float = PRICE_ROUND_TRIP_JUMP_THRESHOLD,
    revert_threshold: float = PRICE_ROUND_TRIP_REVERT_THRESHOLD,
    window_days: int = PRICE_ROUND_TRIP_WINDOW_DAYS,
    show_progress: bool = False,
) -> tuple[pd.DataFrame, list[dict]]:
    """
    Smooth round-trip spike days across a multi-coin close-price DataFrame.

    For each detected event, the spike day's close is replaced with the prior
    day's close. This neutralises the glitch for TOTAL2 calculation while
    keeping the coin in the index (unlike symbol-replacement detection, which
    ejects the coin for 21 days).

    Args:
        close_df: DataFrame of close prices (dates × coins)
        jump_threshold: See detect_round_trips
        revert_threshold: See detect_round_trips
        window_days: See detect_round_trips
        show_progress: Log a summary of corrections applied

    Returns:
        Tuple of (corrected_df, all_corrections). Each correction is a dict
        with coin, date, original, corrected, jump_ratio, revert_ratio,
        days_to_revert, direction.
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
            dt = ev["date"]
            original = ev["jump_price"]
            corrected = ev["pre_price"]
            corrected_df.at[dt, coin_id] = corrected
            all_corrections.append(
                {
                    "coin": coin_id.upper() if isinstance(coin_id, str) else str(coin_id),
                    "date": str(dt.date()) if hasattr(dt, "date") else str(dt),
                    "original": float(original),
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

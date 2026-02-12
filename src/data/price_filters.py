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

from config import SYMBOL_REPLACEMENT_DECREASE_THRESHOLD, SYMBOL_REPLACEMENT_INCREASE_THRESHOLD
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

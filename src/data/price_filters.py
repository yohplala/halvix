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


def detect_volume_outliers(
    volume_series: pd.Series,
    threshold: float = DEFAULT_VOLUME_OUTLIER_THRESHOLD,
    min_volume: float = DEFAULT_MIN_VOLUME_FOR_OUTLIER_CHECK,
    window_days: int = DEFAULT_OUTLIER_WINDOW_DAYS,
) -> pd.Series:
    """
    Detect volume outliers in a price series.

    An outlier is defined as a volume that:
    - Is > threshold times the rolling median of past window_days
    - Exceeds min_volume (to avoid flagging small volumes)
    - Has a positive past median (to ensure valid comparison)

    Args:
        volume_series: Series of volume data with DatetimeIndex
        threshold: Multiple of median that triggers outlier detection
        min_volume: Minimum volume to consider for outlier check
        window_days: Rolling window size for median calculation

    Returns:
        Boolean Series where True indicates an outlier
    """
    if volume_series.empty:
        return pd.Series(dtype=bool)

    # Calculate rolling median of past values (shift to exclude current day)
    rolling_median = volume_series.rolling(window=window_days, min_periods=3).median()
    past_median = rolling_median.shift(1)

    # Calculate ratio to past median
    ratio = volume_series / past_median

    # Identify outliers
    is_outlier = (ratio > threshold) & (volume_series > min_volume) & (past_median > 0)

    return is_outlier


def correct_volume_outliers(
    volume_series: pd.Series,
    threshold: float = DEFAULT_VOLUME_OUTLIER_THRESHOLD,
    min_volume: float = DEFAULT_MIN_VOLUME_FOR_OUTLIER_CHECK,
    window_days: int = DEFAULT_OUTLIER_WINDOW_DAYS,
    max_iterations: int = 10,
) -> tuple[pd.Series, list[dict]]:
    """
    Detect and correct volume outliers iteratively.

    CryptoCompare occasionally has bad data points with impossible volume spikes.
    This function detects outliers and replaces them with interpolated values.

    Correction method:
    1. Cap the outlier at threshold × past_median
    2. Interpolate: (previous_day + capped_value) / 2

    Args:
        volume_series: Series of volume data with DatetimeIndex
        threshold: Multiple of median that triggers outlier detection
        min_volume: Minimum volume to consider for outlier check
        window_days: Rolling window size for median calculation
        max_iterations: Maximum correction iterations

    Returns:
        Tuple of (corrected_series, list_of_corrections)
    """
    corrected = volume_series.copy()
    all_corrections = []

    for iteration in range(max_iterations):
        corrections_made = []

        # Calculate rolling median and ratio
        rolling_median = corrected.rolling(window=window_days, min_periods=3).median()
        past_median = rolling_median.shift(1)
        ratio = corrected / past_median

        # Find outliers
        is_outlier = (ratio > threshold) & (corrected > min_volume) & (past_median > 0)

        outlier_indices = corrected.index[is_outlier]

        if len(outlier_indices) == 0:
            break

        for idx in outlier_indices:
            original_vol = corrected.loc[idx]
            median_val = past_median.loc[idx]

            # Get previous value
            idx_pos = corrected.index.get_loc(idx)
            if idx_pos == 0:
                continue

            prev_idx = corrected.index[idx_pos - 1]
            prev_vol = corrected.loc[prev_idx]

            if not pd.notna(prev_vol) or prev_vol <= 0:
                continue
            if not pd.notna(median_val) or median_val <= 0:
                continue

            # Cap and interpolate
            capped_value = median_val * threshold
            interpolated = (prev_vol + min(original_vol, capped_value)) / 2

            if interpolated <= 0:
                continue

            corrected.loc[idx] = interpolated

            corrections_made.append(
                {
                    "date": str(idx.date()) if hasattr(idx, "date") else str(idx),
                    "original": float(original_vol),
                    "corrected": float(interpolated),
                    "ratio": float(ratio.loc[idx]) if np.isfinite(ratio.loc[idx]) else 0.0,
                    "iteration": iteration + 1,
                }
            )

        all_corrections.extend(corrections_made)

        if not corrections_made:
            break

    return corrected, sorted(all_corrections, key=lambda x: x["ratio"], reverse=True)


def apply_volume_sma_smoothing(
    volume_series: pd.Series,
    window: int = DEFAULT_VOLUME_SMA_WINDOW,
    zero_pad: bool = True,
) -> pd.Series:
    """
    Apply SMA smoothing to volume data with optional zero padding.

    Zero-padding ensures new coins enter indices gradually over the
    SMA window period, preventing sudden weight jumps.

    Args:
        volume_series: Series of volume data with DatetimeIndex
        window: SMA window size in days
        zero_pad: If True, pad with zeros before first valid value

    Returns:
        Smoothed volume Series
    """
    if volume_series.empty:
        return volume_series.copy()

    if zero_pad:
        # Create copy and fill NaN before first valid with 0
        padded = volume_series.copy()
        first_valid_idx = volume_series.first_valid_index()

        if first_valid_idx is not None:
            mask = padded.index < first_valid_idx
            padded.loc[mask] = 0.0

        smoothed = padded.rolling(window=window).mean()
    else:
        smoothed = volume_series.rolling(window=window).mean()

    return smoothed


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
            logger.info(
                "  %6s %s: %15,.2f → %12,.2f (%,.0fx median)%s",
                c["coin"],
                c["date"],
                c["original"],
                c["corrected"],
                c["ratio"],
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

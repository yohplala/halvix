"""
Data processor for TOTAL2 index calculation.

Calculates the volume-weighted TOTAL2 index:
- For each day, identifies top N coins by smoothed 24h trading volume
- Uses 28-day SMA for volume smoothing (configurable via VOLUME_SMA_WINDOW)
- Excludes BTC, derivatives, and stablecoins
- Computes volume-weighted average price in BTC
- Tracks daily composition (which coins were in the index)

Vectorized implementation for efficient computation across all dates.
"""

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

from analysis.filters import TokenFilter
from config import (
    DEFAULT_QUOTE_CURRENCY,
    PROCESSED_DIR,
    TOP_N_FOR_TOTAL2,
    TOTAL2_COMPOSITION_FILE,
    TOTAL2_INDEX_FILE,
    TOTAL2_MAX_WEIGHT_CHANGE_FILE,
    VOLUME_SMA_WINDOW,
)
from data.cache import PriceDataCache


class ProcessorError(Exception):
    """Base exception for processor errors."""

    pass


# Volume outlier detection parameters
# A data point is considered an outlier if:
# - Its volume is > OUTLIER_THRESHOLD times the median of surrounding days
# - AND the volume is significant (> MIN_VOLUME_FOR_OUTLIER_CHECK BTC)
# We use a high minimum volume to focus on spikes that impact TOTAL2 significantly
# Examples: PPC 2017-12-10 (90M BTC), BCH 2018-06-20 (756K BTC)
VOLUME_OUTLIER_THRESHOLD = 20  # 20x median catches BCH case (39x) with margin
MIN_VOLUME_FOR_OUTLIER_CHECK = 5000  # Only check significant volume spikes (BTC)
OUTLIER_WINDOW_DAYS = 7  # Days before and after to calculate median

# Price outlier detection parameters
# Detects extreme price moves that could distort TOTAL2
# Examples: ZEC launch 2016-10-28 (27.8 BTC, then crashed 90%)
PRICE_OUTLIER_THRESHOLD = 5  # >5x or <0.2x day-over-day change
MIN_PRICE_FOR_OUTLIER_CHECK = 0.001  # Only check coins with meaningful price (BTC)


@dataclass
class Total2Result:
    """Result of TOTAL2 calculation."""

    index_df: pd.DataFrame
    composition_df: pd.DataFrame
    coins_processed: int
    date_range: tuple[date, date]
    avg_coins_per_day: float
    max_weight_change: float | None = None
    max_weight_change_coin: str | None = None
    max_weight_change_date: date | None = None
    volume_outliers_corrected: list[dict] | None = None
    price_outliers_corrected: list[dict] | None = None


class Total2Processor:
    """
    Processor for calculating the volume-weighted TOTAL2 market index.

    TOTAL2 is a volume-weighted index of top N altcoins,
    excluding BTC, derivatives, and stablecoins.

    The composition changes daily based on smoothed 24h trading volume rankings.

    Algorithm (vectorized):
    1. Get cached coin IDs, filter out BTC/derivatives/stablecoins (before loading)
    2. Load price data for eligible coins only into aligned DataFrames
    3. Apply SMA smoothing to volume data (VOLUME_SMA_WINDOW days, default: 28)
    4. Rank coins by smoothed volume for each day
    5. Create mask for top N coins
    6. Calculate: TOTAL2 = Σ(price × smoothed_volume) / Σ(smoothed_volume)

    Important: TOTAL2 uses ALL cached price data, including recent coins.
    The MIN_DATA_DATE filter only applies to individual coin halving cycle
    analysis, not to TOTAL2 calculation. This ensures the index is immutable:
    the value for any day D should not change when recalculated in the future.
    Including recent coins ensures stable, reproducible historical values.

    Usage:
        processor = Total2Processor()
        result = processor.calculate_total2()
        result.index_df  # Daily TOTAL2 values
        result.composition_df  # Daily composition
    """

    def __init__(
        self,
        price_cache: PriceDataCache | None = None,
        token_filter: TokenFilter | None = None,
        top_n: int = TOP_N_FOR_TOTAL2,
        volume_sma_window: int = VOLUME_SMA_WINDOW,
        quote_currency: str = DEFAULT_QUOTE_CURRENCY,
    ):
        """
        Initialize the TOTAL2 processor.

        Args:
            price_cache: Cache for price data (default: new instance)
            token_filter: Token filter for exclusions (default: new instance)
            top_n: Number of coins to include in TOTAL2 (default: TOP_N_FOR_TOTAL2)
            volume_sma_window: SMA window for volume smoothing (default: VOLUME_SMA_WINDOW)
            quote_currency: Quote currency for prices (default: DEFAULT_QUOTE_CURRENCY)
        """
        self.price_cache = price_cache or PriceDataCache()
        self.token_filter = token_filter or TokenFilter()
        self.top_n = top_n
        self.volume_sma_window = volume_sma_window
        self.quote_currency = quote_currency

    def load_all_price_data(
        self,
        coin_ids: list[str] | None = None,
        show_progress: bool = True,
    ) -> dict[str, pd.DataFrame]:
        """
        Load price data for all cached coins.

        Args:
            coin_ids: Optional list of coin IDs to load (default: all cached)
            show_progress: Show progress bar

        Returns:
            Dictionary mapping coin_id to price DataFrame
        """
        if coin_ids is None:
            coin_ids = self.price_cache.list_cached_coins(self.quote_currency)

        data = {}
        iterator = tqdm(coin_ids, desc="Loading price data") if show_progress else coin_ids

        for coin_id in iterator:
            df = self.price_cache.get_prices(coin_id, self.quote_currency)
            if df is not None and not df.empty:
                data[coin_id] = df

        return data

    def build_aligned_dataframes(
        self,
        price_data: dict[str, pd.DataFrame],
        show_progress: bool = True,
    ) -> tuple[pd.DataFrame, pd.DataFrame, list[dict], list[dict]]:
        """
        Build aligned price and volume DataFrames for vectorized calculation.

        Creates two DataFrames with:
        - Rows: all dates from earliest to latest across all coins
        - Columns: coin IDs

        Also detects and corrects volume and price outliers from bad data.

        Args:
            price_data: Dictionary of price DataFrames per coin
            show_progress: Whether to print progress messages

        Returns:
            Tuple of (close_df, volume_df, volume_outliers, price_outliers)
        """
        # Find global date range
        all_dates = set()
        for df in price_data.values():
            all_dates.update(df.index)

        if not all_dates:
            raise ProcessorError("No dates found in price data")

        # Create complete date index
        min_date = min(all_dates)
        max_date = max(all_dates)
        date_index = pd.date_range(start=min_date, end=max_date, freq="D")

        # Build price and volume DataFrames
        close_data = {}
        volume_data = {}

        for coin_id, df in price_data.items():
            # Reindex to common dates (NaN where no data)
            close_data[coin_id] = df["close"].reindex(date_index)
            volume_data[coin_id] = df["volume_to"].reindex(date_index)

        close_df = pd.DataFrame(close_data, index=date_index)
        volume_df = pd.DataFrame(volume_data, index=date_index)

        # Apply volume data corrections for outliers
        volume_df, volume_outliers = self._apply_volume_corrections(
            volume_df, show_progress=show_progress
        )

        # Apply price data corrections for outliers (launch spikes, etc.)
        close_df, price_outliers = self._apply_price_corrections(
            close_df, show_progress=show_progress
        )

        return close_df, volume_df, volume_outliers, price_outliers

    def _apply_volume_corrections(
        self,
        volume_df: pd.DataFrame,
        show_progress: bool = True,
    ) -> tuple[pd.DataFrame, list[dict]]:
        """
        Detect and correct volume outliers using only PAST data.

        CryptoCompare occasionally has bad data points with impossible volume spikes.
        This function automatically detects outliers where volume is > VOLUME_OUTLIER_THRESHOLD
        times the rolling median of PAST days, and replaces them with capped values.

        IMPORTANT: This method only uses past data for detection and correction,
        ensuring the algorithm can run iteratively as new data appears without
        recalculating past values.

        The correction uses a capped average approach:
        - Cap the outlier to VOLUME_OUTLIER_THRESHOLD times the past median
        - Use average of previous day and capped value for smoothing

        Examples of known bad data:
        - PPC 2017-12-10: 90M BTC (should be ~50 BTC)
        - BCH 2018-06-20: 756K BTC (should be ~20K BTC)

        Args:
            volume_df: DataFrame with volume data (dates × coins)
            show_progress: Whether to print correction messages

        Returns:
            Tuple of (corrected_volume_df, list_of_corrections)
        """
        corrected_df = volume_df.copy()
        all_corrections = []
        max_iterations = 10  # Prevent infinite loops

        for iteration in range(max_iterations):
            corrections_made = []

            # Calculate rolling median using ONLY PAST data (not centered)
            # This ensures we only use information available at time t
            rolling_median = corrected_df.rolling(
                window=OUTLIER_WINDOW_DAYS, min_periods=3
            ).median()

            # Shift by 1 to exclude current day from median calculation
            # (we want median of days t-1, t-2, ..., t-OUTLIER_WINDOW_DAYS)
            past_median = rolling_median.shift(1)

            # Calculate ratio of actual volume to past median
            ratio_df = corrected_df / past_median

            # Find outliers: volume > threshold AND volume > minimum
            # Also require past_median > 0 to avoid detecting new coins as outliers
            is_outlier = (
                (ratio_df > VOLUME_OUTLIER_THRESHOLD)
                & (corrected_df > MIN_VOLUME_FOR_OUTLIER_CHECK)
                & (past_median > 0)  # Don't flag as outlier if no valid past data
            )

            # Get list of outliers
            outlier_locations = np.where(is_outlier)

            if len(outlier_locations[0]) == 0:
                break  # No more outliers found

            for idx, col_idx in zip(outlier_locations[0], outlier_locations[1], strict=True):
                dt = corrected_df.index[idx]
                coin_id = corrected_df.columns[col_idx]
                original_vol = corrected_df.iloc[idx, col_idx]
                ratio = ratio_df.iloc[idx, col_idx]
                median_val = past_median.iloc[idx, col_idx]

                # Use only PAST data for correction:
                # 1. Get previous day's value (already corrected if it was an outlier)
                prev_vol = corrected_df.iloc[idx - 1, col_idx] if idx > 0 else np.nan

                # Skip if previous volume is 0 or invalid (coin just started)
                if not pd.notna(prev_vol) or prev_vol <= 0:
                    continue
                if not pd.notna(median_val) or median_val <= 0:
                    continue

                # Cap at VOLUME_OUTLIER_THRESHOLD times the median
                capped_value = median_val * VOLUME_OUTLIER_THRESHOLD
                # Use capped average: average of previous day and capped value
                interpolated = (prev_vol + min(original_vol, capped_value)) / 2

                # Ensure corrected value is positive
                if interpolated <= 0:
                    continue

                # Apply correction
                corrected_df.iloc[idx, col_idx] = interpolated

                corrections_made.append(
                    {
                        "coin": coin_id.upper(),
                        "date": str(dt.date()),
                        "original": float(original_vol),
                        "corrected": float(interpolated),
                        "ratio": float(ratio) if np.isfinite(ratio) else 0.0,
                        "iteration": iteration + 1,
                    }
                )

            all_corrections.extend(corrections_made)

            if show_progress and corrections_made:
                print(
                    f"  Volume outlier iteration {iteration + 1}: {len(corrections_made)} corrections"
                )

        # Sort by ratio descending
        all_corrections = sorted(all_corrections, key=lambda x: x["ratio"], reverse=True)

        # Report corrections
        if all_corrections and show_progress:
            print(f"  Volume outlier corrections ({len(all_corrections)} total):")
            for c in all_corrections[:20]:  # Show top 20
                iter_str = f" (iter {c['iteration']})" if c.get("iteration", 1) > 1 else ""
                print(
                    f"    {c['coin']:6s} {c['date']}: "
                    f"{c['original']:>15,.2f} → {c['corrected']:>12,.2f} "
                    f"({c['ratio']:.0f}x median){iter_str}"
                )
            if len(all_corrections) > 20:
                print(f"    ... and {len(all_corrections) - 20} more")

        return corrected_df, all_corrections

    def _apply_price_corrections(
        self,
        close_df: pd.DataFrame,
        show_progress: bool = True,
    ) -> tuple[pd.DataFrame, list[dict]]:
        """
        Detect and correct extreme price moves using only PAST data.

        Two types of corrections:
        1. Warmup smoothing: For the first PRICE_SMA_WARMUP_DAYS of each coin,
           apply zero-padded SMA to smooth out launch day spikes
        2. Day-over-day outliers: >5x spike or >80% crash using only past data

        IMPORTANT: This method only uses past data for both detection and correction,
        ensuring the algorithm can run iteratively as new data appears without
        recalculating past values.

        The correction uses a capped approach:
        - For spikes: cap at PRICE_OUTLIER_THRESHOLD times previous day's price
        - For crashes: use a capped average between previous price and outlier

        Args:
            close_df: DataFrame with close price data (dates × coins)
            show_progress: Whether to print correction messages

        Returns:
            Tuple of (corrected_close_df, list_of_corrections)
        """
        from config import PRICE_SMA_WARMUP_DAYS, PRICE_SMA_WARMUP_WINDOW

        corrected_df = close_df.copy()
        all_corrections = []
        max_iterations = 10  # Prevent infinite loops

        # === Part 1: Warmup smoothing for first days (zero-padded SMA, no forward-looking) ===
        # Uses the same approach as volume smoothing:
        # 1. Fill NaN values before first valid price with 0 (zero-padding)
        # 2. Apply rolling SMA with min_periods=1
        # 3. Only apply corrections during warmup period (first PRICE_SMA_WARMUP_DAYS)
        #
        # For N-period SMA with zero-padding:
        #   Day 1: (0 + ... + 0 + price) / N = price / N  (N-1 zeros)
        #   Day 2: (0 + ... + prev + price) / N
        #   Day N+: regular N-day SMA

        # Create zero-padded price DataFrame for SMA calculation
        padded_close_df = corrected_df.copy()
        for coin_id in padded_close_df.columns:
            first_valid_idx = corrected_df[coin_id].first_valid_index()
            if first_valid_idx is not None:
                # Fill all NaN values before the first valid data point with 0
                mask = padded_close_df.index < first_valid_idx
                padded_close_df.loc[mask, coin_id] = 0.0

        # Apply SMA with min_periods=1 (same as volume smoothing)
        smoothed_close_df = padded_close_df.rolling(
            window=PRICE_SMA_WARMUP_WINDOW, min_periods=1
        ).mean()

        # Apply corrections only during warmup period for each coin
        for coin_id in corrected_df.columns:
            first_valid_idx = corrected_df[coin_id].first_valid_index()
            if first_valid_idx is None:
                continue

            first_pos = corrected_df.index.get_loc(first_valid_idx)
            warmup_end_pos = min(first_pos + PRICE_SMA_WARMUP_DAYS, len(corrected_df))

            for pos in range(first_pos, warmup_end_pos):
                original_price = corrected_df.iloc[pos][coin_id]
                smoothed = smoothed_close_df.iloc[pos][coin_id]

                if pd.isna(original_price) or original_price <= 0:
                    continue
                if pd.isna(smoothed) or smoothed <= 0:
                    continue

                # Only apply correction if it significantly changes the price (>threshold)
                if original_price > MIN_PRICE_FOR_OUTLIER_CHECK:
                    ratio = original_price / smoothed
                    if ratio > PRICE_OUTLIER_THRESHOLD:
                        corrected_df.iloc[pos, corrected_df.columns.get_loc(coin_id)] = smoothed
                        all_corrections.append(
                            {
                                "coin": coin_id.upper(),
                                "date": str(corrected_df.index[pos].date()),
                                "original": float(original_price),
                                "corrected": float(smoothed),
                                "ratio": float(ratio),
                                "type": "warmup-sma",
                            }
                        )

        # === Part 2: Day-over-day outliers (iterative, using only past data) ===
        for iteration in range(max_iterations):
            corrections_made = []

            # Calculate day-over-day price ratio using CORRECTED prices
            price_ratio = corrected_df / corrected_df.shift(1)

            # Find outliers: price changed by more than PRICE_OUTLIER_THRESHOLD
            # Either >5x increase or <0.2x (80% drop)
            # Also require both current and previous price to be meaningful (> MIN_PRICE)
            is_spike = (
                (price_ratio > PRICE_OUTLIER_THRESHOLD)
                & (corrected_df > MIN_PRICE_FOR_OUTLIER_CHECK)
                & (corrected_df.shift(1) > MIN_PRICE_FOR_OUTLIER_CHECK)
            )
            is_crash = (
                (price_ratio < (1 / PRICE_OUTLIER_THRESHOLD))
                & (corrected_df.shift(1) > MIN_PRICE_FOR_OUTLIER_CHECK)
                & (corrected_df > 0)  # Current price must be > 0 to correct
            )
            is_outlier = is_spike | is_crash

            # Get list of outliers
            outlier_locations = np.where(is_outlier)

            if len(outlier_locations[0]) == 0:
                break  # No more outliers found

            for idx, col_idx in zip(outlier_locations[0], outlier_locations[1], strict=True):
                dt = corrected_df.index[idx]
                coin_id = corrected_df.columns[col_idx]
                original_price = corrected_df.iloc[idx, col_idx]
                prev_price = corrected_df.iloc[idx - 1, col_idx] if idx > 0 else np.nan
                ratio = price_ratio.iloc[idx, col_idx]

                # Skip if already corrected as warmup-sma
                coin_first_valid = corrected_df[coin_id].first_valid_index()
                if coin_first_valid is not None:
                    first_pos = corrected_df.index.get_loc(coin_first_valid)
                    if idx < first_pos + PRICE_SMA_WARMUP_DAYS:
                        continue  # Already handled in Part 1

                if not pd.notna(prev_price) or prev_price <= 0:
                    continue  # Cannot correct without valid past data

                if original_price <= 0:
                    continue  # Cannot correct 0 prices meaningfully

                # Use only PAST data for correction:
                if ratio > 1:
                    # Spike: cap at PRICE_OUTLIER_THRESHOLD times previous price
                    max_allowed = prev_price * PRICE_OUTLIER_THRESHOLD
                    # Use capped average: average of previous price and capped value
                    interpolated = (prev_price + min(original_price, max_allowed)) / 2
                    change_type = "spike"
                else:
                    # Crash: use capped average between previous and crashed value
                    # Don't allow more than 80% drop in one day
                    min_allowed = prev_price / PRICE_OUTLIER_THRESHOLD
                    interpolated = (prev_price + max(original_price, min_allowed)) / 2
                    change_type = "crash"

                # Ensure corrected value is positive
                if interpolated <= 0:
                    continue

                # Apply correction
                corrected_df.iloc[idx, col_idx] = interpolated

                corrections_made.append(
                    {
                        "coin": coin_id.upper(),
                        "date": str(dt.date()),
                        "original": float(original_price),
                        "corrected": float(interpolated),
                        "ratio": float(ratio) if np.isfinite(ratio) else 0.0,
                        "type": change_type,
                        "iteration": iteration + 1,
                    }
                )

            all_corrections.extend(corrections_made)

            if show_progress and corrections_made:
                print(
                    f"  Price outlier iteration {iteration + 1}: {len(corrections_made)} corrections"
                )

        # Sort by ratio (most extreme first), handle zero/inf cases
        def sort_key(x):
            r = x["ratio"]
            if r == 0 or not np.isfinite(r):
                return float("inf")
            return max(r, 1 / r)

        all_corrections = sorted(all_corrections, key=sort_key, reverse=True)

        # Report corrections
        if all_corrections and show_progress:
            print(f"  Price outlier corrections ({len(all_corrections)} total):")
            for c in all_corrections[:20]:  # Show top 20
                ratio_str = f"{c['ratio']:.1f}x" if c["ratio"] > 1 else f"{c['ratio']:.2f}x"
                iter_str = f" (iter {c['iteration']})" if c.get("iteration", 0) > 1 else ""
                print(
                    f"    {c['coin']:6s} {c['date']}: "
                    f"{c['original']:>12.6f} → {c['corrected']:>12.6f} BTC "
                    f"({ratio_str} {c['type']}){iter_str}"
                )
            if len(all_corrections) > 20:
                print(f"    ... and {len(all_corrections) - 20} more")

        return corrected_df, all_corrections

    def filter_coins_for_total2(
        self,
        coin_ids: list[str],
    ) -> list[str]:
        """
        Filter coin IDs to exclude BTC, derivatives, and stablecoins.

        Args:
            coin_ids: List of coin IDs to filter

        Returns:
            Filtered list of coin IDs eligible for TOTAL2
        """
        eligible = []

        for coin_id in coin_ids:
            # Check if should be excluded
            # coin_id is lowercase symbol (e.g., "eth")
            should_exclude, _ = self.token_filter.should_exclude_from_total2(
                coin_id=coin_id,
                name="",  # We only have ID from cache
                symbol=coin_id.upper(),
            )

            if not should_exclude:
                eligible.append(coin_id)

        return eligible

    def get_common_date_range(
        self,
        price_data: dict[str, pd.DataFrame],
    ) -> tuple[date, date]:
        """
        Find the common date range across all price data.

        Args:
            price_data: Dictionary of price DataFrames

        Returns:
            Tuple of (start_date, end_date)
        """
        if not price_data:
            raise ProcessorError("No price data available")

        # Get the union of all dates (we need at least some coins each day)
        all_dates = set()
        for df in price_data.values():
            all_dates.update(df.index.date if hasattr(df.index, "date") else df.index)

        if not all_dates:
            raise ProcessorError("No dates found in price data")

        min_date = min(all_dates)
        max_date = max(all_dates)

        return (min_date, max_date)

    def calculate_total2(
        self,
        coin_ids: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        show_progress: bool = True,
    ) -> Total2Result:
        """
        Calculate the volume-weighted TOTAL2 index using vectorized operations.

        Uses SMA-smoothed volume for ranking and weighting. The first
        (volume_sma_window - 1) days will have NaN values due to warmup.

        Args:
            coin_ids: Optional list of coin IDs (default: all cached, filtered)
            start_date: Optional start date (default: earliest available)
            end_date: Optional end date (default: latest available)
            show_progress: Show progress bar

        Returns:
            Total2Result with index and composition DataFrames
        """
        # Load all price data from cache
        # Note: This includes ALL cached coins (including recent ones).
        # The MIN_DATA_DATE filter does not apply to TOTAL2 calculation.
        # This ensures index immutability: the value for any day D won't change
        # when recalculated in the future (no retroactive changes).
        all_cached = self.price_cache.list_cached_coins(self.quote_currency)

        if not all_cached:
            raise ProcessorError(
                "No cached price data found. Run 'python -m main fetch-prices' first."
            )

        # Filter for TOTAL2 eligibility
        if coin_ids is None:
            eligible_coins = self.filter_coins_for_total2(all_cached)
        else:
            eligible_coins = self.filter_coins_for_total2(coin_ids)

        if not eligible_coins:
            raise ProcessorError("No eligible coins found for TOTAL2 calculation")

        # Load price data for eligible coins
        price_data = self.load_all_price_data(eligible_coins, show_progress=show_progress)

        if not price_data:
            raise ProcessorError("Failed to load price data for eligible coins")

        if show_progress:
            print(f"Building aligned DataFrames for {len(price_data)} coins...")

        # Build aligned DataFrames (includes outlier detection)
        close_df, volume_df, volume_outliers, price_outliers = self.build_aligned_dataframes(
            price_data, show_progress
        )

        if show_progress:
            print(f"Applying {self.volume_sma_window}-day SMA to volume (zero-padded)...")

        # Apply SMA to volume with zero-padding for warmup
        # Instead of removing the first VOLUME_SMA_WINDOW-1 points, we pad with zeros
        # This ensures coins entering the TOP50 do so gradually, preventing sudden jumps
        #
        # How it works: for each coin, we fill NaN values before its first data point with 0
        # This way, when a coin appears (e.g., YFI on 2020-09-19), its smoothed volume is:
        #   Day 1: volume / SMA_WINDOW (averaged with SMA_WINDOW-1 zeros)
        #   Day 2: (vol1 + vol2) / SMA_WINDOW
        #   ...
        #   Day SMA_WINDOW: actual SMA
        # This prevents sudden jumps when a coin enters the TOP50 with significant volume

        padded_volume_df = volume_df.copy()
        for coin_id in padded_volume_df.columns:
            first_valid_idx = volume_df[coin_id].first_valid_index()
            if first_valid_idx is not None:
                # Fill all NaN values before the first valid data point with 0
                # This creates the zero-padding effect for the SMA warmup
                mask = padded_volume_df.index < first_valid_idx
                padded_volume_df.loc[mask, coin_id] = 0.0

        # Apply SMA with min_periods=1 so we get values from day 1
        # The zero-padding ensures gradual weight increase for new coins
        smoothed_volume_df = padded_volume_df.rolling(
            window=self.volume_sma_window, min_periods=1
        ).mean()

        if show_progress:
            print("Calculating daily rankings and TOTAL2...")

        # Rank by smoothed volume (highest = 1)
        rank_df = smoothed_volume_df.rank(axis=1, ascending=False, method="first")

        # Create mask for top N
        mask_df = rank_df <= self.top_n

        # Apply mask
        masked_close = close_df.where(mask_df)
        masked_volume = smoothed_volume_df.where(mask_df)

        # Calculate TOTAL2 = Σ(price × volume) / Σ(volume)
        numerator = (masked_close * masked_volume).sum(axis=1)
        denominator = masked_volume.sum(axis=1)
        total2_series = numerator / denominator

        # Count coins included per day
        coin_count_series = mask_df.sum(axis=1)

        # Filter date range
        if start_date is not None:
            total2_series = total2_series[total2_series.index >= pd.Timestamp(start_date)]
            denominator = denominator[denominator.index >= pd.Timestamp(start_date)]
            coin_count_series = coin_count_series[
                coin_count_series.index >= pd.Timestamp(start_date)
            ]

        if end_date is not None:
            total2_series = total2_series[total2_series.index <= pd.Timestamp(end_date)]
            denominator = denominator[denominator.index <= pd.Timestamp(end_date)]
            coin_count_series = coin_count_series[coin_count_series.index <= pd.Timestamp(end_date)]

        # Drop NaN values (warmup period and days with insufficient data)
        valid_mask = total2_series.notna() & (coin_count_series >= 3)
        total2_series = total2_series[valid_mask]
        denominator = denominator[valid_mask]
        coin_count_series = coin_count_series[valid_mask]

        if total2_series.empty:
            raise ProcessorError("Could not calculate TOTAL2 for any date")

        # Build index DataFrame
        index_df = pd.DataFrame(
            {
                "total2_price": total2_series,
                "total_volume": denominator,
                "coin_count": coin_count_series.astype(int),
            }
        )
        index_df.index.name = "date"

        # Build composition DataFrame
        if show_progress:
            print("Building composition records...")

        composition_records = self._build_composition_records(
            close_df, smoothed_volume_df, rank_df, mask_df, total2_series.index
        )

        composition_df = pd.DataFrame(composition_records)
        if not composition_df.empty:
            composition_df["date"] = pd.to_datetime(composition_df["date"])
            composition_df = composition_df.sort_values(["date", "rank"])

        # Calculate stats
        data_start = total2_series.index.min().date()
        data_end = total2_series.index.max().date()
        avg_coins = coin_count_series.mean()

        # Calculate max daily weight change for coins in TOTAL2
        # This helps identify sudden composition changes that may cause price jumps
        max_weight_change = None
        max_weight_change_coin = None
        max_weight_change_date = None

        if not composition_df.empty and show_progress:
            print("Calculating max daily weight change...")

            max_weight_change, max_weight_change_coin, max_weight_change_date = (
                self._calculate_max_weight_change(composition_df)
            )

            if max_weight_change is not None:
                print(
                    f"  Max weight change: {max_weight_change:.4f}% "
                    f"for {max_weight_change_coin} on {max_weight_change_date}"
                )

        return Total2Result(
            index_df=index_df,
            composition_df=composition_df,
            coins_processed=len(price_data),
            date_range=(data_start, data_end),
            avg_coins_per_day=avg_coins,
            max_weight_change=max_weight_change,
            max_weight_change_coin=max_weight_change_coin,
            max_weight_change_date=max_weight_change_date,
            volume_outliers_corrected=volume_outliers,
            price_outliers_corrected=price_outliers,
        )

    def _build_composition_records(
        self,
        close_df: pd.DataFrame,
        volume_df: pd.DataFrame,
        rank_df: pd.DataFrame,
        mask_df: pd.DataFrame,
        valid_dates: pd.DatetimeIndex,
    ) -> list[dict]:
        """
        Build composition records for each day.

        Args:
            close_df: DataFrame of close prices
            volume_df: DataFrame of smoothed volumes
            rank_df: DataFrame of volume ranks
            mask_df: DataFrame of inclusion mask
            valid_dates: DatetimeIndex of dates with valid TOTAL2 values

        Returns:
            List of composition record dictionaries
        """
        records = []

        for dt in valid_dates:
            # Get data for this date
            mask_row = mask_df.loc[dt]
            included_coins = mask_row[mask_row].index.tolist()

            if not included_coins:
                continue

            volume_row = volume_df.loc[dt]
            close_row = close_df.loc[dt]
            rank_row = rank_df.loc[dt]

            total_vol = volume_row[included_coins].sum()

            for coin_id in included_coins:
                vol = volume_row[coin_id]
                price = close_row[coin_id]
                rank = int(rank_row[coin_id])

                if pd.notna(vol) and pd.notna(price) and total_vol > 0:
                    records.append(
                        {
                            "date": dt.date(),
                            "rank": rank,
                            "coin_id": coin_id,
                            "volume": vol,
                            "weight": vol / total_vol,
                            "price_btc": price,
                        }
                    )

        return records

    def _calculate_max_weight_change(
        self,
        composition_df: pd.DataFrame,
        min_date: date | None = None,
    ) -> tuple[float | None, str | None, date | None]:
        """
        Calculate the maximum daily weight change for any coin in TOTAL2.

        This metric helps ensure that TOTAL2 curve variations are due to actual
        price changes rather than sudden changes in coin weights. A high max
        weight change indicates a coin's weight changed abruptly, which could
        cause artificial jumps in the TOTAL2 value unrelated to market movements.

        We only track this after TOTAL2 has 50 coins (default: after 2017-11-01)
        to avoid noise from the early period when the index was still being
        populated and weight variations were naturally high.

        Args:
            composition_df: DataFrame with columns: date, rank, coin_id, weight, ...
            min_date: Only consider dates >= this date (default: 2017-11-01)

        Returns:
            Tuple of (max_change_pct, coin_id, date) or (None, None, None) if insufficient data
        """
        if composition_df.empty:
            return None, None, None

        # Default to 2017-11-01 when TOTAL2 typically has 50 coins
        if min_date is None:
            min_date = date(2017, 11, 1)

        # Filter to only dates after min_date
        filtered_df = composition_df[composition_df["date"] >= pd.Timestamp(min_date)]
        if filtered_df.empty:
            return None, None, None

        # Pivot to get weight by date and coin
        # Convert weight from fraction to percentage for display
        weight_pivot = filtered_df.pivot_table(
            index="date", columns="coin_id", values="weight", aggfunc="first"
        )

        # Fill missing values with 0 (coin not in TOTAL2 that day)
        weight_pivot = weight_pivot.fillna(0) * 100  # Convert to percentage

        # Calculate daily change (diff between consecutive days)
        weight_diff = weight_pivot.diff()

        # Drop first row (no previous day to compare)
        weight_diff = weight_diff.iloc[1:]

        if weight_diff.empty:
            return None, None, None

        # Find maximum absolute change
        # We look at absolute values because both entering (positive) and leaving (negative)
        # can cause jumps in the TOTAL2 value
        abs_diff = weight_diff.abs()

        # Get the maximum value and its location
        max_change = abs_diff.max().max()
        if pd.isna(max_change):
            return None, None, None

        # Find which coin and date had this max change
        for coin_id in abs_diff.columns:
            for dt in abs_diff.index:
                if abs_diff.loc[dt, coin_id] == max_change:
                    # Return the actual change (can be negative)
                    actual_change = weight_diff.loc[dt, coin_id]
                    change_date = dt.date() if hasattr(dt, "date") else dt
                    return float(actual_change), coin_id, change_date

        return None, None, None

    def _calculate_daily_total2(
        self,
        price_data: dict[str, pd.DataFrame],
        target_date: datetime,
    ) -> tuple[dict, list[dict]] | None:
        """
        Calculate volume-weighted TOTAL2 for a single day (legacy method).

        Kept for backward compatibility with tests.

        Args:
            price_data: Dictionary of price DataFrames (with normalized DatetimeIndex)
            target_date: The date to calculate for

        Returns:
            Tuple of (index_record, composition_records) or None if not enough data
        """
        # Collect data for this date
        daily_data = []
        target_date_normalized = pd.Timestamp(target_date).normalize()

        for coin_id, df in price_data.items():
            try:
                # Use normalized timestamp for lookup (index should be DatetimeIndex)
                if target_date_normalized not in df.index:
                    continue

                row = df.loc[target_date_normalized]

                # Extract values from the Series
                price = row["close"] if "close" in row.index else None
                # Use volume_to (volume in quote currency, i.e., BTC)
                volume = row["volume_to"] if "volume_to" in row.index else None

                if pd.notna(price) and pd.notna(volume) and volume > 0 and price > 0:
                    daily_data.append(
                        {
                            "coin_id": coin_id,
                            "price": float(price),
                            "volume": float(volume),
                        }
                    )
            except (KeyError, IndexError, TypeError):
                continue

        if len(daily_data) < 3:  # Need at least 3 coins for meaningful index
            return None

        # Sort by volume and take top N
        daily_data.sort(key=lambda x: x["volume"], reverse=True)
        top_n = daily_data[: self.top_n]

        # Calculate volume-weighted average price
        total_volume = sum(c["volume"] for c in top_n)
        weighted_sum = sum(c["price"] * c["volume"] for c in top_n)
        total2_price = weighted_sum / total_volume if total_volume > 0 else 0

        # Build index record
        index_record = {
            "date": target_date.date() if hasattr(target_date, "date") else target_date,
            "total2_price": total2_price,
            "total_volume": total_volume,
            "coin_count": len(top_n),
        }

        # Build composition records
        composition_records = []
        for rank, coin in enumerate(top_n, start=1):
            composition_records.append(
                {
                    "date": target_date.date() if hasattr(target_date, "date") else target_date,
                    "rank": rank,
                    "coin_id": coin["coin_id"],
                    "volume": coin["volume"],
                    "weight": coin["volume"] / total_volume if total_volume > 0 else 0,
                    "price_btc": coin["price"],
                }
            )

        return index_record, composition_records

    def save_results(
        self,
        result: Total2Result,
        index_path: Path | None = None,
        composition_path: Path | None = None,
    ) -> tuple[Path, Path]:
        """
        Save TOTAL2 results to parquet files and max weight change to JSON.

        Args:
            result: Total2Result from calculate_total2
            index_path: Path for index file (default: from config)
            composition_path: Path for composition file (default: from config)

        Returns:
            Tuple of (index_path, composition_path)
        """
        import json

        index_path = index_path or TOTAL2_INDEX_FILE
        composition_path = composition_path or TOTAL2_COMPOSITION_FILE

        # Ensure directory exists
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

        # Save index
        result.index_df.to_parquet(index_path)

        # Save composition
        if not result.composition_df.empty:
            result.composition_df.to_parquet(composition_path, index=False)

        # Save max weight change info and outliers to JSON (for index.html display)
        max_weight_info = {
            "max_weight_change": result.max_weight_change,
            "coin": (
                result.max_weight_change_coin.upper() if result.max_weight_change_coin else None
            ),
            "date": str(result.max_weight_change_date) if result.max_weight_change_date else None,
            "volume_outliers_corrected": result.volume_outliers_corrected or [],
            "price_outliers_corrected": result.price_outliers_corrected or [],
        }
        with open(TOTAL2_MAX_WEIGHT_CHANGE_FILE, "w", encoding="utf-8") as f:
            json.dump(max_weight_info, f, indent=2)

        return index_path, composition_path

    def load_total2_index(self, path: Path | None = None) -> pd.DataFrame:
        """
        Load previously calculated TOTAL2 index.

        Args:
            path: Path to index file (default: from config)

        Returns:
            DataFrame with TOTAL2 index
        """
        path = path or TOTAL2_INDEX_FILE

        if not path.exists():
            raise ProcessorError("TOTAL2 index not found. Run calculate_total2 first.")

        return pd.read_parquet(path)

    def load_total2_composition(self, path: Path | None = None) -> pd.DataFrame:
        """
        Load previously calculated TOTAL2 daily composition.

        Args:
            path: Path to composition file (default: from config)

        Returns:
            DataFrame with daily composition
        """
        path = path or TOTAL2_COMPOSITION_FILE

        if not path.exists():
            raise ProcessorError("TOTAL2 composition not found. Run calculate_total2 first.")

        return pd.read_parquet(path)

    def get_composition_for_date(
        self,
        target_date: date,
        composition_df: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """
        Get the TOTAL2 composition for a specific date.

        Args:
            target_date: The date to query
            composition_df: Optional pre-loaded composition (default: load from file)

        Returns:
            DataFrame with coins in TOTAL2 for that date
        """
        if composition_df is None:
            composition_df = self.load_total2_composition()

        # Filter to target date
        mask = composition_df["date"].dt.date == target_date
        return composition_df[mask].sort_values("rank")

    def get_coin_total2_history(
        self,
        coin_id: str,
        composition_df: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """
        Get the history of a coin's inclusion in TOTAL2.

        Args:
            coin_id: Coin ID (lowercase symbol)
            composition_df: Optional pre-loaded composition (default: load from file)

        Returns:
            DataFrame with dates when coin was in TOTAL2
        """
        if composition_df is None:
            composition_df = self.load_total2_composition()

        mask = composition_df["coin_id"] == coin_id
        return composition_df[mask].sort_values("date")

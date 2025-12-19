"""
TOTAL2 (legacy) processor implementation.

Extends BaseTotal2Processor with:
- Entry warmup: actual price capping for new coins entering TOTAL2
- TOTAL2 series smoothing: caps extreme day-over-day index movements
- Two-pass algorithm: raw calculation then apply capped prices
"""

from datetime import date

import pandas as pd

from analysis.filters import TokenFilter
from config import (
    DEFAULT_QUOTE_CURRENCY,
    TOP_N_FOR_TOTAL2,
    TOTAL2_ENTRY_MAX_DECREASE,
    TOTAL2_ENTRY_MAX_INCREASE,
    TOTAL2_ENTRY_WARMUP_PERIOD_DAYS,
    VOLUME_SMA_WINDOW,
)
from data.cache import PriceDataCache
from data.processor_base import (
    BaseTotal2Processor,
    ProcessorError,
    Total2Result,
)

# TOTAL2 series smoothing parameters
# Caps extreme day-over-day movements in the aggregate index
MAX_DOD_INCREASE = 3.0  # Cap TOTAL2 increase at 3x per day
MAX_DOD_DECREASE = 0.35  # Cap TOTAL2 decrease at 0.35x per day


class Total2Processor(BaseTotal2Processor):
    """
    Processor for legacy TOTAL2 index calculation.

    Features:
    - Entry warmup: new coins have their prices CAPPED during warmup period
    - TOTAL2 series smoothing: extreme day-over-day index movements are capped
    - Two-pass algorithm: raw TOTAL2, then recalculate with capped prices
    """

    INDEX_TYPE = "total2"

    def __init__(
        self,
        price_cache: PriceDataCache | None = None,
        token_filter: TokenFilter | None = None,
        top_n: int = TOP_N_FOR_TOTAL2,
        volume_sma_window: int = VOLUME_SMA_WINDOW,
        quote_currency: str = DEFAULT_QUOTE_CURRENCY,
        entry_max_increase: float = TOTAL2_ENTRY_MAX_INCREASE,
        entry_max_decrease: float = TOTAL2_ENTRY_MAX_DECREASE,
        entry_warmup_days: int = TOTAL2_ENTRY_WARMUP_PERIOD_DAYS,
    ):
        """
        Initialize the TOTAL2 processor.

        Args:
            price_cache: Cache for price data
            token_filter: Token filter for exclusions
            top_n: Number of coins to include
            volume_sma_window: SMA window for volume smoothing
            quote_currency: Quote currency for prices
            entry_max_increase: Max price increase factor during entry warmup
            entry_max_decrease: Max price decrease factor during entry warmup
            entry_warmup_days: Number of days for entry warmup period
        """
        super().__init__(
            price_cache=price_cache,
            token_filter=token_filter,
            top_n=top_n,
            volume_sma_window=volume_sma_window,
            quote_currency=quote_currency,
        )
        self.entry_max_increase = entry_max_increase
        self.entry_max_decrease = entry_max_decrease
        self.entry_warmup_days = entry_warmup_days

    def calculate_total2(
        self,
        coin_ids: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        show_progress: bool = True,
    ) -> Total2Result:
        """
        Calculate the volume-weighted TOTAL2 index with entry warmup price capping.

        Two-pass algorithm:
        1. First pass: Calculate raw TOTAL2 series (to get baseline for entry capping)
        2. Apply entry warmup: cap prices for coins during their warmup period
        3. Second pass: Recalculate TOTAL2 with capped prices

        Args:
            coin_ids: Optional list of coin IDs (default: all cached)
            start_date: Optional start date for index (inclusive)
            end_date: Optional end date for index (inclusive)
            show_progress: Show progress information

        Returns:
            Total2Result with index DataFrame, composition DataFrame, and metadata
        """
        # Load and filter price data
        if show_progress:
            print("Loading price data...")

        price_data = self.load_all_price_data(coin_ids, show_progress=show_progress)

        if not price_data:
            raise ProcessorError("No price data available")

        # Filter for TOTAL2 eligibility
        eligible_ids = self.filter_coins_for_total2(list(price_data.keys()))
        if show_progress:
            print(f"Filtered to {len(eligible_ids)} eligible coins")

        price_data = {cid: df for cid, df in price_data.items() if cid in eligible_ids}

        if not price_data:
            raise ProcessorError("No eligible coins for TOTAL2")

        # Build aligned DataFrames
        if show_progress:
            print("Building aligned DataFrames...")

        close_df, volume_df, volume_outliers = self.build_aligned_dataframes(
            price_data, show_progress=show_progress
        )

        # Apply volume SMA smoothing
        if show_progress:
            print("Applying volume SMA smoothing...")

        smoothed_volume_df = self.apply_volume_sma_smoothing(volume_df)

        # Calculate rank and mask
        rank_df, mask_df = self.calculate_rank_and_mask(smoothed_volume_df)

        # First pass: Calculate raw TOTAL2 (needed as baseline for entry capping)
        if show_progress:
            print("First pass: Calculating raw TOTAL2...")

        raw_total2, _, _ = self.calculate_weighted_average(
            close_df, smoothed_volume_df, mask_df
        )

        # Apply entry warmup price capping
        if show_progress:
            print("Applying entry warmup price capping...")

        capped_close_df, warmup_events = self._apply_entry_warmup_capping(
            close_df.copy(),
            raw_total2,
            mask_df,
            show_progress=show_progress,
        )

        # Second pass: Recalculate TOTAL2 with capped prices
        if show_progress:
            print("Second pass: Recalculating TOTAL2 with capped prices...")

        total2_series, volume_sum, coin_count = self.calculate_weighted_average(
            capped_close_df, smoothed_volume_df, mask_df
        )

        # Apply TOTAL2 series smoothing (cap extreme aggregate movements)
        total2_corrected, smoothing_events = self._apply_total2_series_smoothing(
            total2_series, show_progress=show_progress
        )

        # Combine all events
        all_events = warmup_events + smoothing_events

        # Create index DataFrame
        index_df = pd.DataFrame(
            {
                "total2_price": total2_corrected,
                "total_volume": volume_sum,
                "coin_count": coin_count,
            }
        )

        # Filter to date range
        if start_date:
            index_df = index_df[index_df.index >= pd.Timestamp(start_date)]
        if end_date:
            index_df = index_df[index_df.index <= pd.Timestamp(end_date)]

        # Remove NaN rows
        index_df = index_df.dropna(subset=["total2_price"])

        if index_df.empty:
            raise ProcessorError("No valid index values after filtering")

        # Build composition records (using capped prices)
        if show_progress:
            print("Building composition records...")

        composition_records = self.build_composition_records(
            capped_close_df, smoothed_volume_df, rank_df, mask_df, index_df.index
        )

        composition_df = pd.DataFrame(composition_records)
        if not composition_df.empty:
            composition_df["date"] = pd.to_datetime(composition_df["date"])

        # Calculate max weight change
        max_change, max_coin, max_date = self.calculate_max_weight_change(composition_df)

        # Create result
        date_range = (index_df.index.min().date(), index_df.index.max().date())

        result = Total2Result(
            index_df=index_df,
            composition_df=composition_df,
            coins_processed=len(price_data),
            date_range=date_range,
            avg_coins_per_day=coin_count.mean() if not coin_count.empty else 0,
            max_weight_change=max_change,
            max_weight_change_coin=max_coin,
            max_weight_change_date=max_date,
            volume_outliers_corrected=volume_outliers,
            price_outliers_corrected=all_events,
            index_type="total2",
        )

        return result

    def _apply_entry_warmup_capping(
        self,
        close_df: pd.DataFrame,
        raw_total2: pd.Series,
        mask_df: pd.DataFrame,
        show_progress: bool = True,
    ) -> tuple[pd.DataFrame, list[dict]]:
        """
        Apply entry warmup price capping for coins entering the index.

        When a coin first enters TOP30:
        1. Its baseline price is set to the TOTAL2 value from the previous day
        2. During warmup period, its price is capped at MAX_INCREASE/MAX_DECREASE per day
        3. Each day uses the previous day's CAPPED price as reference

        This prevents coins with extreme prices (like ZEC at 27.8 BTC) from
        causing artificial spikes in the index.

        Args:
            close_df: DataFrame of close prices (will be modified with capped values)
            raw_total2: Raw TOTAL2 series (used as baseline for entry)
            mask_df: DataFrame of inclusion mask (True = coin in TOP30)
            show_progress: Whether to print progress

        Returns:
            Tuple of (capped_close_df, list_of_capping_events)
        """
        events = []
        capped_df = close_df.copy()

        # Find first entry date for each coin
        coin_entry_dates = {}
        for coin_id in mask_df.columns:
            coin_mask = mask_df[coin_id]
            true_indices = coin_mask[coin_mask].index
            if len(true_indices) > 0:
                coin_entry_dates[coin_id] = true_indices[0]

        # Track capped prices for each coin during warmup
        # Key: coin_id, Value: dict of {date: capped_price}
        coin_capped_prices: dict[str, dict] = {}

        # Process each date in order
        for i, dt in enumerate(close_df.index):
            for coin_id, entry_date in coin_entry_dates.items():
                # Skip if coin not in index on this date
                if coin_id not in mask_df.columns or not mask_df.loc[dt, coin_id]:
                    continue

                days_since_entry = (dt - entry_date).days

                # Skip if outside warmup period
                if days_since_entry < 0 or days_since_entry >= self.entry_warmup_days:
                    continue

                # Get actual price
                actual_price = close_df.loc[dt, coin_id]
                if pd.isna(actual_price) or actual_price <= 0:
                    continue

                # Initialize tracking dict for this coin
                if coin_id not in coin_capped_prices:
                    coin_capped_prices[coin_id] = {}

                # Determine baseline/reference price
                if days_since_entry == 0:
                    # Entry day: use TOTAL2 value from previous day as baseline
                    if i > 0:
                        prev_dt = close_df.index[i - 1]
                        baseline = raw_total2.loc[prev_dt]
                        if pd.isna(baseline) or baseline <= 0:
                            baseline = actual_price  # Fallback
                    else:
                        baseline = actual_price  # First day, no baseline available
                    reference_price = baseline
                else:
                    # Subsequent days: use previous day's CAPPED price
                    prev_dt = close_df.index[i - 1]
                    if prev_dt in coin_capped_prices[coin_id]:
                        reference_price = coin_capped_prices[coin_id][prev_dt]
                    else:
                        # Fallback to previous day's actual price
                        reference_price = close_df.loc[prev_dt, coin_id]
                        if pd.isna(reference_price) or reference_price <= 0:
                            reference_price = actual_price

                # Calculate price change ratio
                price_change = actual_price / reference_price if reference_price > 0 else 1.0

                # Apply capping
                capped_price = actual_price
                capping_applied = False

                if price_change > self.entry_max_increase:
                    capped_price = reference_price * self.entry_max_increase
                    capping_applied = True
                    events.append(
                        {
                            "date": str(dt.date()),
                            "type": "entry_warmup_cap_up",
                            "coin": coin_id.upper(),
                            "original": float(actual_price),
                            "corrected": float(capped_price),
                            "change_factor": float(price_change),
                            "days_since_entry": days_since_entry,
                        }
                    )
                elif price_change < self.entry_max_decrease:
                    capped_price = reference_price * self.entry_max_decrease
                    capping_applied = True
                    events.append(
                        {
                            "date": str(dt.date()),
                            "type": "entry_warmup_cap_down",
                            "coin": coin_id.upper(),
                            "original": float(actual_price),
                            "corrected": float(capped_price),
                            "change_factor": float(price_change),
                            "days_since_entry": days_since_entry,
                        }
                    )

                # Store capped price (even if no capping was applied, for reference)
                coin_capped_prices[coin_id][dt] = capped_price

                # Update the DataFrame with capped price
                if capping_applied:
                    capped_df.loc[dt, coin_id] = capped_price

        if events and show_progress:
            print(f"  Entry warmup capping: {len(events)} price caps applied")
            for e in events[:5]:
                print(
                    f"    {e['coin']} {e['date']}: "
                    f"{e['original']:.6f} → {e['corrected']:.6f} "
                    f"(day {e['days_since_entry']})"
                )
            if len(events) > 5:
                print(f"    ... and {len(events) - 5} more")

        return capped_df, events

    def _apply_total2_series_smoothing(
        self,
        total2_series: pd.Series,
        show_progress: bool = True,
    ) -> tuple[pd.Series, list[dict]]:
        """
        Apply smoothing to the TOTAL2 series itself.

        Caps extreme day-over-day movements in the aggregate index.
        This is a safety net for cases not caught by entry warmup capping.

        Args:
            total2_series: TOTAL2 series to smooth
            show_progress: Whether to print progress

        Returns:
            Tuple of (smoothed_series, list_of_events)
        """
        events = []
        max_iterations = 50
        working_series = total2_series.copy()

        for iteration in range(max_iterations):
            pct_change = working_series.pct_change()

            # Find extreme increases (>200%) or decreases (>65%)
            outlier_up = pct_change > (MAX_DOD_INCREASE - 1)
            outlier_down = pct_change < -(1 - MAX_DOD_DECREASE)

            outliers = outlier_up | outlier_down

            if not outliers.any():
                break

            corrections_made = []

            for dt in working_series.index[outliers]:
                idx = working_series.index.get_loc(dt)
                if idx == 0:
                    continue

                prev_val = working_series.iloc[idx - 1]
                current_val = working_series.iloc[idx]

                if pd.isna(prev_val) or pd.isna(current_val):
                    continue

                change = current_val / prev_val

                if change > MAX_DOD_INCREASE:
                    capped_val = prev_val * MAX_DOD_INCREASE
                    working_series.iloc[idx] = capped_val
                    corrections_made.append(
                        {
                            "date": str(dt.date()),
                            "type": "total2_smoothing_up",
                            "coin": "TOTAL2",
                            "original": float(current_val),
                            "corrected": float(capped_val),
                            "change_factor": float(change),
                            "iteration": iteration + 1,
                        }
                    )
                elif change < MAX_DOD_DECREASE:
                    capped_val = prev_val * MAX_DOD_DECREASE
                    working_series.iloc[idx] = capped_val
                    corrections_made.append(
                        {
                            "date": str(dt.date()),
                            "type": "total2_smoothing_down",
                            "coin": "TOTAL2",
                            "original": float(current_val),
                            "corrected": float(capped_val),
                            "change_factor": float(change),
                            "iteration": iteration + 1,
                        }
                    )

            events.extend(corrections_made)

            if show_progress and corrections_made:
                print(
                    f"  TOTAL2 smoothing iteration {iteration + 1}: "
                    f"{len(corrections_made)} corrections"
                )

        return working_series, events

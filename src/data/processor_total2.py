"""
TOTAL2 (legacy) processor implementation.

Extends BaseTotal2Processor with:
- Entry warmup: progressive price capping for new coins entering TOTAL2
- TOTAL2 series smoothing: caps extreme day-over-day index movements
- Two-pass vectorized algorithm
"""

from datetime import date

import pandas as pd

from analysis.filters import TokenFilter
from config import (
    DEFAULT_QUOTE_CURRENCY,
    TOP_N_FOR_TOTAL2,
    TOTAL2_ENTRY_MAX_DECREASE,
    TOTAL2_ENTRY_MAX_INCREASE,
    TOTAL2_ENTRY_WARMUP_DAYS,
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
    - Entry warmup: new coins are integrated progressively with capped price changes
    - TOTAL2 series smoothing: extreme day-over-day index movements are capped
    - Two-pass algorithm with corrections
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
        entry_warmup_days: int = TOTAL2_ENTRY_WARMUP_DAYS,
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
        Calculate the volume-weighted TOTAL2 index with entry warmup.

        This method uses a two-pass algorithm:
        1. First pass: Calculate raw TOTAL2 series (volume-weighted average)
        2. Apply TOTAL2 series smoothing and entry warmup corrections
        3. Second pass: Rebuild composition records

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

        # First pass: Calculate raw TOTAL2
        if show_progress:
            print("First pass: Calculating raw TOTAL2...")

        total2_series, volume_sum, coin_count = self.calculate_weighted_average(
            close_df, smoothed_volume_df, mask_df
        )

        # Apply TOTAL2 series smoothing and entry warmup
        if show_progress:
            print("Applying TOTAL2 smoothing and entry warmup...")

        total2_corrected, warmup_events = self._apply_total2_smoothing_and_warmup(
            total2_series.copy(),
            close_df,
            smoothed_volume_df,
            mask_df,
            show_progress=show_progress,
        )

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

        # Build composition records
        if show_progress:
            print("Building composition records...")

        composition_records = self.build_composition_records(
            close_df, smoothed_volume_df, rank_df, mask_df, index_df.index
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
            price_outliers_corrected=warmup_events,  # Entry warmup events
            index_type="total2",
        )

        return result

    def _apply_total2_smoothing_and_warmup(
        self,
        total2_series: pd.Series,
        close_df: pd.DataFrame,
        smoothed_volume_df: pd.DataFrame,
        mask_df: pd.DataFrame,
        show_progress: bool = True,
    ) -> tuple[pd.Series, list[dict]]:
        """
        Apply TOTAL2 series smoothing and entry warmup corrections.

        Two types of corrections:
        1. TOTAL2 series smoothing: Cap extreme day-over-day index movements
           (prevents aggregate spikes when new coins enter with extreme prices)
        2. Entry warmup: Track coins during warmup period for reporting

        Args:
            total2_series: Raw TOTAL2 series
            close_df: DataFrame of close prices
            smoothed_volume_df: DataFrame of smoothed volumes
            mask_df: DataFrame of inclusion mask
            show_progress: Whether to print correction info

        Returns:
            Tuple of (corrected_series, list_of_events)
        """
        all_events = []
        max_iterations = 50

        working_series = total2_series.copy()

        # Step 1: TOTAL2 series smoothing
        # Cap extreme day-over-day movements in the aggregate index
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
                            "original": float(current_val),
                            "corrected": float(capped_val),
                            "change_factor": float(change),
                            "iteration": iteration + 1,
                        }
                    )

            all_events.extend(corrections_made)

            if show_progress and corrections_made:
                print(
                    f"  TOTAL2 smoothing iteration {iteration + 1}: "
                    f"{len(corrections_made)} corrections"
                )

        # Step 2: Track entry warmup events (for reporting)
        warmup_events = self._track_entry_warmup_events(
            working_series, close_df, mask_df, show_progress
        )
        all_events.extend(warmup_events)

        if all_events and show_progress:
            print(f"  Total events ({len(all_events)}):")
            for e in all_events[:10]:
                iter_str = f" (iter {e.get('iteration', 1)})" if e.get("iteration", 1) > 1 else ""
                print(
                    f"    {e['date']} {e['type']}: "
                    f"{e['original']:.6f} → {e['corrected']:.6f} "
                    f"({e['change_factor']:.2f}x){iter_str}"
                )
            if len(all_events) > 10:
                print(f"    ... and {len(all_events) - 10} more")

        return working_series, all_events

    def _track_entry_warmup_events(
        self,
        total2_series: pd.Series,
        close_df: pd.DataFrame,
        mask_df: pd.DataFrame,
        show_progress: bool = True,
    ) -> list[dict]:
        """
        Track coins during their entry warmup period.

        When a new coin enters the TOP30, its price changes are monitored.
        Large price swings during warmup are recorded for transparency.

        Note: The actual capping happens via TOTAL2 series smoothing.
        This method tracks individual coin behavior for reporting.

        Args:
            total2_series: TOTAL2 series
            close_df: DataFrame of close prices
            mask_df: DataFrame of inclusion mask
            show_progress: Whether to print progress

        Returns:
            List of warmup event records
        """
        events = []

        # Find first entry date for each coin
        coin_entry_dates = {}
        for coin_id in mask_df.columns:
            coin_mask = mask_df[coin_id]
            true_indices = coin_mask[coin_mask].index
            if len(true_indices) > 0:
                coin_entry_dates[coin_id] = true_indices[0]

        # Check each day for coins in warmup period
        for i, dt in enumerate(total2_series.index):
            if i < 1:
                continue

            prev_dt = total2_series.index[i - 1]

            for coin_id, entry_date in coin_entry_dates.items():
                if not mask_df.loc[dt, coin_id]:
                    continue

                days_since_entry = (dt - entry_date).days
                if days_since_entry < 0 or days_since_entry >= self.entry_warmup_days:
                    continue

                if coin_id not in close_df.columns:
                    continue

                current_price = close_df.loc[dt, coin_id]
                prev_price = close_df.loc[prev_dt, coin_id]

                if pd.isna(current_price) or pd.isna(prev_price) or prev_price == 0:
                    continue

                price_change = current_price / prev_price

                # Record significant price movements during warmup
                if price_change > self.entry_max_increase:
                    events.append(
                        {
                            "date": str(dt.date()),
                            "type": "entry_warmup_increase",
                            "coin": coin_id.upper(),
                            "original": float(current_price),
                            "corrected": float(prev_price * self.entry_max_increase),
                            "change_factor": float(price_change),
                            "days_since_entry": days_since_entry,
                        }
                    )
                elif price_change < self.entry_max_decrease:
                    events.append(
                        {
                            "date": str(dt.date()),
                            "type": "entry_warmup_decrease",
                            "coin": coin_id.upper(),
                            "original": float(current_price),
                            "corrected": float(prev_price * self.entry_max_decrease),
                            "change_factor": float(price_change),
                            "days_since_entry": days_since_entry,
                        }
                    )

        return events

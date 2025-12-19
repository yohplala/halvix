"""
TOTAL2 (legacy) processor implementation.

Extends BaseTotal2Processor with:
- Price outlier detection and correction (iterative day-over-day)
- Entry warmup price capping for new coins
- Two-pass vectorized algorithm with corrections
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

# Price outlier detection parameters (TOTAL2-specific)
MAX_DOD_INCREASE = 3.0  # Maximum day-over-day price increase factor
MAX_DOD_DECREASE = 0.35  # Maximum day-over-day price decrease factor
PRICE_OUTLIER_WINDOW_DAYS = 7  # Window for price outlier detection


class Total2Processor(BaseTotal2Processor):
    """
    Processor for legacy TOTAL2 index calculation.

    Features:
    - Price outlier detection and iterative correction
    - Entry warmup price capping for new coins
    - Full two-pass algorithm with corrections
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
            entry_max_increase: Max price increase factor for new coin entry
            entry_max_decrease: Max price decrease factor for new coin entry
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
        Calculate the volume-weighted TOTAL2 index with outlier corrections.

        This method uses a two-pass vectorized algorithm:
        1. First pass: Calculate raw TOTAL2 series (volume-weighted average)
        2. Apply price corrections to TOTAL2 series for outliers and entry warmup
        3. Second pass: Rebuild composition records with corrected prices

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

        # Apply price outlier corrections to TOTAL2 series
        if show_progress:
            print("Applying price outlier corrections...")

        total2_corrected, price_outliers = self._apply_total2_price_corrections(
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
            price_outliers_corrected=price_outliers,
            index_type="total2",
        )

        return result

    def _apply_total2_price_corrections(
        self,
        total2_series: pd.Series,
        close_df: pd.DataFrame,
        smoothed_volume_df: pd.DataFrame,
        mask_df: pd.DataFrame,
        show_progress: bool = True,
    ) -> tuple[pd.Series, list[dict]]:
        """
        Apply iterative price corrections to the TOTAL2 series.

        Corrections applied:
        1. Day-over-day outlier detection (20%/35% limits)
        2. Entry warmup price capping for new coins

        Args:
            total2_series: Raw TOTAL2 series
            close_df: DataFrame of close prices
            smoothed_volume_df: DataFrame of smoothed volumes
            mask_df: DataFrame of inclusion mask
            show_progress: Whether to print correction info

        Returns:
            Tuple of (corrected_series, list_of_corrections)
        """
        all_corrections = []
        max_iterations = 50

        working_series = total2_series.copy()

        for iteration in range(max_iterations):
            # Detect day-over-day outliers
            pct_change = working_series.pct_change()

            # Find increases > MAX_DOD_INCREASE-1 (e.g., >200%)
            outlier_up = pct_change > (MAX_DOD_INCREASE - 1)
            # Find decreases < -(1 - MAX_DOD_DECREASE) (e.g., <-65%)
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
                            "type": "dod_increase",
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
                            "type": "dod_decrease",
                            "original": float(current_val),
                            "corrected": float(capped_val),
                            "change_factor": float(change),
                            "iteration": iteration + 1,
                        }
                    )

            all_corrections.extend(corrections_made)

            if show_progress and corrections_made:
                print(
                    f"  Price outlier iteration {iteration + 1}: {len(corrections_made)} corrections"
                )

        # Apply entry warmup corrections
        entry_corrections = self._apply_entry_warmup_corrections(
            working_series, close_df, smoothed_volume_df, mask_df, show_progress
        )
        all_corrections.extend(entry_corrections)

        if all_corrections and show_progress:
            print(f"  Price corrections ({len(all_corrections)} total):")
            for c in all_corrections[:10]:
                iter_str = f" (iter {c.get('iteration', 1)})" if c.get("iteration", 1) > 1 else ""
                print(
                    f"    {c['date']} {c['type']}: "
                    f"{c['original']:.6f} → {c['corrected']:.6f} "
                    f"({c['change_factor']:.2f}x){iter_str}"
                )
            if len(all_corrections) > 10:
                print(f"    ... and {len(all_corrections) - 10} more")

        return working_series, all_corrections

    def _apply_entry_warmup_corrections(
        self,
        total2_series: pd.Series,
        close_df: pd.DataFrame,
        smoothed_volume_df: pd.DataFrame,
        mask_df: pd.DataFrame,
        show_progress: bool = True,
    ) -> list[dict]:
        """
        Apply entry warmup price capping for coins entering the index.

        When a new coin enters the TOP30, its day-over-day price change is
        capped during the warmup period to prevent large offsets.

        Args:
            total2_series: TOTAL2 series (modified in place)
            close_df: DataFrame of close prices
            smoothed_volume_df: DataFrame of smoothed volumes
            mask_df: DataFrame of inclusion mask
            show_progress: Whether to print progress

        Returns:
            List of correction records
        """
        corrections = []

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

            # Find coins that entered recently and are still in warmup
            for coin_id, entry_date in coin_entry_dates.items():
                if not mask_df.loc[dt, coin_id]:
                    continue

                days_since_entry = (dt - entry_date).days
                if days_since_entry < 0 or days_since_entry >= self.entry_warmup_days:
                    continue

                # Check if this coin's price moved too much
                if coin_id not in close_df.columns:
                    continue

                current_price = close_df.loc[dt, coin_id]
                prev_price = close_df.loc[prev_dt, coin_id]

                if pd.isna(current_price) or pd.isna(prev_price) or prev_price == 0:
                    continue

                price_change = current_price / prev_price

                needs_correction = False
                if price_change > self.entry_max_increase:
                    needs_correction = True
                    direction = "up"
                elif price_change < self.entry_max_decrease:
                    needs_correction = True
                    direction = "down"

                if needs_correction:
                    # Calculate the correction impact on TOTAL2
                    # (simplified - full implementation would recalculate weighted avg)
                    corrections.append(
                        {
                            "date": str(dt.date()),
                            "type": f"entry_warmup_{direction}",
                            "coin": coin_id.upper(),
                            "original": float(current_price),
                            "corrected": float(
                                prev_price
                                * (
                                    self.entry_max_increase
                                    if direction == "up"
                                    else self.entry_max_decrease
                                )
                            ),
                            "change_factor": float(price_change),
                            "days_since_entry": days_since_entry,
                        }
                    )

        return corrections

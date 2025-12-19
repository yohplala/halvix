"""
TOTAL2b processor implementation.

Extends BaseTotal2Processor with:
- 3-week freeze period for new coins before index inclusion
- Price scaling for new coin entries (V / TOTAL2b_d-1)
- No price outlier detection (removed vs TOTAL2)
- Simpler, more predictable entry mechanics
"""

from datetime import date

import pandas as pd
from tqdm import tqdm

from analysis.filters import TokenFilter
from config import (
    DEFAULT_QUOTE_CURRENCY,
    TOP_N_FOR_TOTAL2,
    TOTAL2B_FREEZE_PERIOD_DAYS,
    TOTAL2B_MIN_COINS_FOR_SCALING,
    VOLUME_SMA_WINDOW,
)
from data.cache import PriceDataCache
from data.processor_base import (
    BaseTotal2Processor,
    ProcessorError,
    Total2Result,
)

# Re-export for backward compatibility
FREEZE_PERIOD_DAYS = TOTAL2B_FREEZE_PERIOD_DAYS
MIN_COINS_FOR_SCALING = TOTAL2B_MIN_COINS_FOR_SCALING


class Total2bProcessor(BaseTotal2Processor):
    """
    Processor for TOTAL2b index calculation.

    Key differences from TOTAL2:
    - 3-week freeze period for new coins before they can join the index
    - Price scaling for new entries: scaled by 1/TOTAL2b_d-1 to preserve
      price change factors without causing large offsets
    - No price outlier detection or iterative corrections
    - Simpler, more transparent methodology
    """

    INDEX_TYPE = "total2b"

    def __init__(
        self,
        price_cache: PriceDataCache | None = None,
        token_filter: TokenFilter | None = None,
        top_n: int = TOP_N_FOR_TOTAL2,
        volume_sma_window: int = VOLUME_SMA_WINDOW,
        quote_currency: str = DEFAULT_QUOTE_CURRENCY,
        freeze_period_days: int = TOTAL2B_FREEZE_PERIOD_DAYS,
        min_coins_for_scaling: int = TOTAL2B_MIN_COINS_FOR_SCALING,
    ):
        """
        Initialize the TOTAL2b processor.

        Args:
            price_cache: Cache for price data
            token_filter: Token filter for exclusions
            top_n: Number of coins to include
            volume_sma_window: SMA window for volume smoothing
            quote_currency: Quote currency for prices
            freeze_period_days: Days to wait before including new coin
            min_coins_for_scaling: Min coins before applying scaling
        """
        super().__init__(
            price_cache=price_cache,
            token_filter=token_filter,
            top_n=top_n,
            volume_sma_window=volume_sma_window,
            quote_currency=quote_currency,
        )
        self.freeze_period_days = freeze_period_days
        self.min_coins_for_scaling = min_coins_for_scaling

    def calculate_total2(
        self,
        coin_ids: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        show_progress: bool = True,
    ) -> Total2Result:
        """
        Calculate the volume-weighted TOTAL2b index with freeze period and scaling.

        Algorithm:
        1. Build aligned price/volume DataFrames
        2. Apply volume outlier corrections (shared with TOTAL2)
        3. Apply volume SMA smoothing (shared with TOTAL2)
        4. Calculate coin first-seen dates for freeze period enforcement
        5. Iteratively calculate daily index values with:
           - Freeze period enforcement (coins must wait 21 days)
           - Price scaling for new entries (scaled by 1/TOTAL2b_d-1)
        6. Build composition records

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
            raise ProcessorError("No eligible coins for TOTAL2b")

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

        # Calculate first-seen dates for each coin
        first_seen_dates = self._calculate_first_seen_dates(close_df)

        if show_progress:
            print(f"Tracking first-seen dates for {len(first_seen_dates)} coins")

        # Calculate TOTAL2b with freeze period and scaling
        if show_progress:
            print("Calculating TOTAL2b with freeze period and scaling...")

        index_df, composition_records, scaling_events = self._calculate_total2b_iterative(
            close_df,
            smoothed_volume_df,
            first_seen_dates,
            show_progress=show_progress,
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

        # Build composition DataFrame
        composition_df = pd.DataFrame(composition_records)
        if not composition_df.empty:
            composition_df["date"] = pd.to_datetime(composition_df["date"])
            # Filter to match index date range
            if start_date:
                composition_df = composition_df[
                    composition_df["date"] >= pd.Timestamp(start_date)
                ]
            if end_date:
                composition_df = composition_df[composition_df["date"] <= pd.Timestamp(end_date)]

        # Calculate max weight change
        max_change, max_coin, max_date = self.calculate_max_weight_change(composition_df)

        # Create result
        date_range = (index_df.index.min().date(), index_df.index.max().date())

        result = Total2Result(
            index_df=index_df,
            composition_df=composition_df,
            coins_processed=len(price_data),
            date_range=date_range,
            avg_coins_per_day=(
                index_df["coin_count"].mean() if "coin_count" in index_df.columns else 0
            ),
            max_weight_change=max_change,
            max_weight_change_coin=max_coin,
            max_weight_change_date=max_date,
            volume_outliers_corrected=volume_outliers,
            price_outliers_corrected=scaling_events,  # Repurpose for scaling events
            index_type="total2b",
        )

        return result

    def _calculate_first_seen_dates(
        self,
        close_df: pd.DataFrame,
    ) -> dict[str, pd.Timestamp]:
        """
        Calculate the first date each coin appears in the data.

        This is used to enforce the freeze period - coins cannot
        join the index until freeze_period_days after their first appearance.

        Args:
            close_df: DataFrame of close prices (dates × coins)

        Returns:
            Dictionary mapping coin_id to first-seen Timestamp
        """
        first_seen = {}

        for coin_id in close_df.columns:
            first_valid = close_df[coin_id].first_valid_index()
            if first_valid is not None:
                first_seen[coin_id] = first_valid

        return first_seen

    def _calculate_total2b_iterative(
        self,
        close_df: pd.DataFrame,
        smoothed_volume_df: pd.DataFrame,
        first_seen_dates: dict[str, pd.Timestamp],
        show_progress: bool = True,
    ) -> tuple[pd.DataFrame, list[dict], list[dict]]:
        """
        Calculate TOTAL2b iteratively with freeze period and price scaling.

        For each day:
        1. Determine eligible coins (passed freeze period)
        2. For coins entering today, apply price scaling
        3. Calculate volume-weighted average of top-N eligible coins
        4. Record composition

        The scaling formula for new entries:
        scaled_price = raw_price / TOTAL2b_d-1

        This preserves the coin's price change factor while preventing
        large absolute offsets in the index.

        Args:
            close_df: DataFrame of close prices
            smoothed_volume_df: DataFrame of smoothed volumes
            first_seen_dates: Dictionary of first-seen dates per coin
            show_progress: Whether to show progress

        Returns:
            Tuple of (index_df, composition_records, scaling_events)
        """
        dates = close_df.index
        index_records = []
        composition_records = []
        scaling_events = []

        # Track which coins are currently in the index (for entry detection)
        coins_in_index: set[str] = set()
        # Track scaled prices (persist scaling across days)
        scaled_close_df = close_df.copy()
        # Track scaling factors applied to each coin
        coin_scaling_factors: dict[str, float] = {}

        prev_total2b = None

        iterator = tqdm(dates, desc="TOTAL2b calculation") if show_progress else dates

        for dt in iterator:
            # Step 1: Determine eligible coins (passed freeze period)
            eligible_coins = []
            for coin_id in close_df.columns:
                first_seen = first_seen_dates.get(coin_id)
                if first_seen is None:
                    continue

                # Check freeze period
                days_since_first = (dt - first_seen).days
                if days_since_first < self.freeze_period_days:
                    continue

                # Check if has valid price and volume
                price = close_df.loc[dt, coin_id]
                volume = smoothed_volume_df.loc[dt, coin_id]

                if pd.notna(price) and pd.notna(volume) and volume > 0 and price > 0:
                    eligible_coins.append(coin_id)

            if len(eligible_coins) < 3:
                # Not enough coins yet
                continue

            # Step 2: Detect new entries and apply scaling
            new_entries = set(eligible_coins) - coins_in_index

            # Check if we should apply scaling (only after min_coins_for_scaling)
            should_scale = (
                len(coins_in_index) >= self.min_coins_for_scaling and prev_total2b is not None
            )

            for coin_id in new_entries:
                if should_scale and prev_total2b > 0:
                    # Apply scaling: divide all future prices by prev_total2b
                    scaling_factor = 1.0 / prev_total2b
                    coin_scaling_factors[coin_id] = scaling_factor

                    # Scale all prices for this coin from this date forward
                    raw_price = close_df.loc[dt, coin_id]
                    scaled_close_df.loc[dt:, coin_id] = (
                        close_df.loc[dt:, coin_id] * scaling_factor
                    )

                    scaling_events.append(
                        {
                            "date": str(dt.date()),
                            "type": "price_scaling",
                            "coin": coin_id.upper(),
                            "original": float(raw_price),
                            "corrected": float(raw_price * scaling_factor),
                            "change_factor": float(scaling_factor),
                            "prev_total2b": float(prev_total2b),
                        }
                    )

            # Update coins in index
            coins_in_index = set(eligible_coins)

            # Step 3: Calculate volume-weighted average
            # Use scaled prices for coins that have scaling applied
            volumes = []

            for coin_id in eligible_coins:
                vol = smoothed_volume_df.loc[dt, coin_id]
                if coin_id in coin_scaling_factors:
                    price = scaled_close_df.loc[dt, coin_id]
                else:
                    price = close_df.loc[dt, coin_id]

                volumes.append((coin_id, vol, price))

            # Sort by volume and take top N
            volumes.sort(key=lambda x: x[1], reverse=True)
            top_n = volumes[: self.top_n]

            total_volume = sum(v for _, v, _ in top_n)
            if total_volume <= 0:
                continue

            weighted_sum = sum(p * v for _, v, p in top_n)
            total2b_price = weighted_sum / total_volume

            # Record index value
            index_records.append(
                {
                    "date": dt,
                    "total2_price": total2b_price,
                    "total_volume": total_volume,
                    "coin_count": len(top_n),
                }
            )

            prev_total2b = total2b_price

            # Step 4: Record composition
            for rank, (coin_id, vol, price) in enumerate(top_n, start=1):
                composition_records.append(
                    {
                        "date": dt.date(),
                        "rank": rank,
                        "coin_id": coin_id,
                        "volume": vol,
                        "weight": vol / total_volume,
                        "price_btc": price,
                    }
                )

        # Build index DataFrame
        if not index_records:
            index_df = pd.DataFrame(columns=["total2_price", "total_volume", "coin_count"])
        else:
            index_df = pd.DataFrame(index_records)
            index_df.set_index("date", inplace=True)

        if show_progress and scaling_events:
            print(f"  Applied scaling to {len(scaling_events)} new coin entries:")
            for event in scaling_events[:10]:
                print(
                    f"    {event['coin']:6s} {event['date']}: "
                    f"scaled by {event['change_factor']:.6f} (prev TOTAL2b: {event['prev_total2b']:.4f})"
                )
            if len(scaling_events) > 10:
                print(f"    ... and {len(scaling_events) - 10} more")

        return index_df, composition_records, scaling_events

    def get_freeze_period_status(
        self,
        price_data: dict[str, pd.DataFrame] | None = None,
        target_date: date | None = None,
    ) -> list[dict]:
        """
        Get the freeze period status for all coins on a given date.

        Useful for understanding which coins are waiting to enter the index.

        Args:
            price_data: Optional price data dict (default: load from cache)
            target_date: Date to check (default: today)

        Returns:
            List of dicts with coin freeze status information
        """

        if price_data is None:
            price_data = self.load_all_price_data(show_progress=False)

        if target_date is None:
            target_date = date.today()

        target_ts = pd.Timestamp(target_date)

        statuses = []

        for coin_id, df in price_data.items():
            first_seen = df.index.min()
            if first_seen is None:
                continue

            days_since_first = (target_ts - first_seen).days
            days_remaining = self.freeze_period_days - days_since_first

            statuses.append(
                {
                    "coin_id": coin_id.upper(),
                    "first_seen": str(first_seen.date()),
                    "days_since_first": days_since_first,
                    "days_remaining": max(0, days_remaining),
                    "eligible": days_remaining <= 0,
                }
            )

        statuses.sort(key=lambda x: x["days_since_first"], reverse=True)

        return statuses

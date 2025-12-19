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

from analysis.filters import CoinFilter
from config import (
    DEFAULT_QUOTE_CURRENCY,
    TOP_N_BY_VOLUME_FOR_TOTAL2,
    TOTAL2B_ENTRY_FREEZE_PERIOD_DAYS,
    TOTAL2B_MIN_COINS_FOR_SCALING,
    TOTAL2B_SYMBOL_REPLACEMENT_THRESHOLD,
    VOLUME_SMA_WINDOW,
)
from data.cache import PriceDataCache
from data.processor_base import (
    BaseTotal2Processor,
    ProcessorError,
    Total2Result,
)


class Total2bProcessor(BaseTotal2Processor):
    """
    Processor for TOTAL2b index calculation.

    Key differences from TOTAL2:
    - 3-week freeze period for new coins before they can join the index
    - Price scaling for new entries: scaled by TOTAL2b_d-1/COIN_PRICE_d to preserve
      price change factors without causing large offsets
    - No price outlier detection or iterative corrections
    - Simpler, more transparent methodology
    """

    INDEX_TYPE = "total2b"

    def __init__(
        self,
        price_cache: PriceDataCache | None = None,
        coin_filter: CoinFilter | None = None,
        top_n: int = TOP_N_BY_VOLUME_FOR_TOTAL2,
        volume_sma_window: int = VOLUME_SMA_WINDOW,
        quote_currency: str = DEFAULT_QUOTE_CURRENCY,
        freeze_period_days: int = TOTAL2B_ENTRY_FREEZE_PERIOD_DAYS,
        min_coins_for_scaling: int = TOTAL2B_MIN_COINS_FOR_SCALING,
        symbol_replacement_threshold: float = TOTAL2B_SYMBOL_REPLACEMENT_THRESHOLD,
    ):
        """
        Initialize the TOTAL2b processor.

        Args:
            price_cache: Cache for price data
            coin_filter: Coin filter for exclusions
            top_n: Number of coins to include
            volume_sma_window: SMA window for volume smoothing
            quote_currency: Quote currency for prices
            freeze_period_days: Days to wait before including new coin
            min_coins_for_scaling: Min coins before applying scaling
            symbol_replacement_threshold: Price change factor that indicates symbol swap
        """
        super().__init__(
            price_cache=price_cache,
            coin_filter=coin_filter,
            top_n=top_n,
            volume_sma_window=volume_sma_window,
            quote_currency=quote_currency,
        )
        self.freeze_period_days = freeze_period_days
        self.min_coins_for_scaling = min_coins_for_scaling
        self.symbol_replacement_threshold = symbol_replacement_threshold

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
           - Price scaling for new entries (scaled by TOTAL2b_d-1/COIN_PRICE_d)
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

        # Calculate first-seen dates for each coin (requires both price and volume)
        # Also detects symbol replacements and resets first_seen accordingly
        first_seen_dates, symbol_replacements = self._calculate_first_seen_dates(
            close_df, volume_df, show_progress=show_progress
        )

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
                composition_df = composition_df[composition_df["date"] >= pd.Timestamp(start_date)]
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
        volume_df: pd.DataFrame,
        show_progress: bool = True,
    ) -> tuple[dict[str, pd.Timestamp], list[dict]]:
        """
        Calculate the first date each coin appears in CryptoCompare data.

        A coin's first appearance is defined as the first day with BOTH:
        - Non-null and positive close price
        - Non-null and positive volume

        This method also detects symbol replacements (when CryptoCompare reuses
        a symbol for a different token) by looking for extreme price jumps.
        If detected, the first_seen date is reset to after the jump.

        This is used to enforce the freeze period - coins cannot
        join the index until freeze_period_days after their first appearance.

        Args:
            close_df: DataFrame of close prices (dates × coins)
            volume_df: DataFrame of raw volumes (dates × coins)
            show_progress: Whether to print symbol replacement detections

        Returns:
            Tuple of (first_seen dict, symbol_replacement_events list)
        """
        first_seen = {}
        symbol_replacements = []

        for coin_id in close_df.columns:
            if coin_id not in volume_df.columns:
                continue

            # Find first date where both price > 0 and volume > 0
            price_valid = (close_df[coin_id] > 0) & close_df[coin_id].notna()
            volume_valid = (volume_df[coin_id] > 0) & volume_df[coin_id].notna()
            both_valid = price_valid & volume_valid

            if not both_valid.any():
                continue

            initial_first_seen = both_valid.idxmax()

            # Check for symbol replacement (extreme price jumps)
            replacement_date = self._detect_symbol_replacement(
                close_df[coin_id], initial_first_seen
            )

            if replacement_date is not None:
                # Symbol was replaced - use post-replacement date
                symbol_replacements.append(
                    {
                        "coin": coin_id.upper(),
                        "original_first_seen": str(initial_first_seen.date()),
                        "replacement_date": str(replacement_date.date()),
                        "price_before": float(
                            close_df.loc[replacement_date - pd.Timedelta(days=1), coin_id]
                        ),
                        "price_after": float(close_df.loc[replacement_date, coin_id]),
                    }
                )
                first_seen[coin_id] = replacement_date
            else:
                first_seen[coin_id] = initial_first_seen

        if show_progress and symbol_replacements:
            print(f"  Detected {len(symbol_replacements)} symbol replacement(s):")
            for event in symbol_replacements:
                print(
                    f"    {event['coin']:6s}: {event['original_first_seen']} → "
                    f"{event['replacement_date']} "
                    f"(price {event['price_before']:.2e} → {event['price_after']:.2e})"
                )

        return first_seen, symbol_replacements

    def _detect_symbol_replacement(
        self,
        price_series: pd.Series,
        first_seen: pd.Timestamp,
    ) -> pd.Timestamp | None:
        """
        Detect if a coin's symbol was replaced by a different token.

        CryptoCompare sometimes reuses symbols for different tokens (e.g.,
        old worthless "HYPE" replaced by Hyperliquid "HYPE" in Dec 2024).
        This is detected by looking for extreme price jumps (>1000x or <0.001x).

        A true symbol replacement requires:
        - Non-zero price BEFORE the jump (old token was trading)
        - Non-zero price AFTER the jump (new token is trading)
        - Extreme price ratio between them

        A jump from 0 to non-zero is just the coin starting to trade, not a replacement.

        Args:
            price_series: Series of close prices for a coin
            first_seen: The initial first-seen date

        Returns:
            The date of the last symbol replacement, or None if no replacement detected
        """
        # Get previous day's price
        prev_price = price_series.shift(1)

        # Calculate daily price change ratios (only where both prices are positive)
        # This avoids division by zero and filters out "coin starts trading" events
        valid_ratio_mask = (price_series > 0) & (prev_price > 0)
        price_ratio = price_series / prev_price

        # Find dates with extreme price jumps (either direction)
        # Both current and previous price must be > 0 (not just starting to trade)
        extreme_jumps = (
            (price_ratio > self.symbol_replacement_threshold)
            | (price_ratio < 1 / self.symbol_replacement_threshold)
        ) & valid_ratio_mask

        if not extreme_jumps.any():
            return None

        # Return the date of the LAST extreme jump (most recent replacement)
        # This handles cases where a symbol might be replaced multiple times
        last_jump_date = price_series.index[extreme_jumps][-1]

        # Only consider it a replacement if it happened after the first_seen date
        if last_jump_date > first_seen:
            return last_jump_date

        return None

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
        scaled_price = raw_price * TOTAL2b_d-1 / COIN_PRICE_d

        Where COIN_PRICE_d is the coin's raw price on entry day.
        This ensures entry-day scaled price equals TOTAL2b_d-1, and
        preserves day-over-day price change factors thereafter.

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
                    # Apply scaling: multiply by TOTAL2b_d-1 / COIN_PRICE_d
                    # This ensures entry-day scaled price = prev_total2b
                    raw_price_at_entry = close_df.loc[dt, coin_id]
                    if raw_price_at_entry > 0:
                        scaling_factor = prev_total2b / raw_price_at_entry
                        coin_scaling_factors[coin_id] = scaling_factor

                        # Scale all prices for this coin from this date forward
                        scaled_close_df.loc[dt:, coin_id] = (
                            close_df.loc[dt:, coin_id] * scaling_factor
                        )

                        scaling_events.append(
                            {
                                "date": str(dt.date()),
                                "type": "price_scaling",
                                "coin": coin_id.upper(),
                                "original": float(raw_price_at_entry),
                                "corrected": float(raw_price_at_entry * scaling_factor),
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
            # Find first date with both valid price > 0 and volume > 0
            if "close" not in df.columns or "volume_to" not in df.columns:
                continue

            valid_mask = (
                (df["close"] > 0)
                & df["close"].notna()
                & (df["volume_to"] > 0)
                & df["volume_to"].notna()
            )

            if not valid_mask.any():
                continue

            first_seen = df.index[valid_mask].min()

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

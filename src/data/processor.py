"""
TOTAL2 index processor — public entrypoint for the data layer.

Calculates a volume-weighted altcoin index (BTC-denominated) using:

- Volume outlier correction (>20x past median) and 120-day SMA smoothing with
  zero-padding for warm-up.
- Round-trip price-spike smoothing (single-day or multi-day windows) — see
  ``data.price_filters.detect_round_trips``.
- Symbol-replacement detection that resets a coin's freeze period when its
  ticker is reassigned to a different token.
- A 21-day freeze period before a coin can enter the top-30 by volume.
- Entry-day price scaling: when a coin joins the index, its price is scaled
  by ``prev_total2 / raw_price_at_entry`` so it lines up with the prior day's
  index value; the scaling factor persists for all future days.

The on-disk metadata still labels itself ``total2b`` (the algorithm variant
name from earlier versions) — preserved so saved parquet/JSON consumers don't
break.

Module exports:

- ``get_processor``: factory function (used by the CLI).
- ``Total2Processor``: the concrete processor class.
- ``Total2Result``: the result dataclass.
- ``ProcessorError`` / ``NoDataError``: exceptions raised by the processor.
"""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
from tqdm import tqdm

from analysis.filters import CoinFilter
from config import (
    DEFAULT_QUOTE_CURRENCY,
    PROCESSED_DIR,
    SYMBOL_REPLACEMENT_DECREASE_THRESHOLD,
    SYMBOL_REPLACEMENT_INCREASE_THRESHOLD,
    TOP_N_BY_VOLUME_FOR_TOTAL2,
    TOTAL2_COMPOSITION_FILE,
    TOTAL2_INDEX_FILE,
    TOTAL2_MAX_WEIGHT_CHANGE_FILE,
    TOTAL2_MIN_COINS_FOR_INDEX,
    TOTAL2B_ENTRY_FREEZE_PERIOD_DAYS,
    TOTAL2B_MIN_COINS_FOR_SCALING,
    VOLUME_SMA_WINDOW,
    coin_url,
)
from data.cache import PriceDataCache
from data.price_filters import (
    DEFAULT_MIN_VOLUME_FOR_OUTLIER_CHECK,
    DEFAULT_OUTLIER_WINDOW_DAYS,
    DEFAULT_VOLUME_OUTLIER_THRESHOLD,
    apply_round_trip_corrections_to_dataframe,
    apply_volume_corrections_to_dataframe,
    apply_volume_sma_smoothing_to_dataframe,
    detect_symbol_replacement,
)
from utils.logging import get_logger

logger = get_logger(__name__)


class ProcessorError(Exception):
    """Base exception for processor errors."""


class NoDataError(ProcessorError):
    """Raised when required data is missing or unavailable."""


@dataclass
class Total2Result:
    """Complete result of a TOTAL2 calculation."""

    index_df: pd.DataFrame  # Daily index values (date, total2_price, total_volume, coin_count)
    composition_df: (
        pd.DataFrame
    )  # Daily composition (date, rank, coin_id, volume, weight, price_btc)
    coins_processed: int
    date_range: tuple[date, date]
    avg_coins_per_day: float
    index_type: str = "total2b"  # Algorithm variant marker, preserved for on-disk compatibility
    max_weight_change: float | None = None
    max_weight_change_coin: str | None = None
    max_weight_change_date: date | None = None
    volume_outliers_corrected: list[dict] | None = None
    scaling_events: list[dict] | None = None
    round_trip_corrections: list[dict] | None = None
    symbol_replacements: list[dict] | None = None


class Total2Processor:
    """
    TOTAL2 index processor with freeze period + entry-day price scaling.

    Pipeline (one pass over the price+volume matrices, per call to
    ``calculate_total2``):

    1. Load and filter cached price data.
    2. Build aligned close/volume DataFrames, apply volume outlier corrections.
    3. Smooth round-trip price spikes (single-day or multi-day windows).
    4. Apply 120-day SMA to volume (with zero-padding so new coins enter
       gradually).
    5. Compute per-coin first-seen dates, detecting symbol replacements that
       reset the freeze clock.
    6. Iterate dates: each day, build the top-N eligible coins, scale new
       entrants by ``prev_total2 / raw_price``, and compute the volume-weighted
       average of scaled prices.
    7. Save index, composition, and metadata (corrections, scaling events,
       coin statistics).
    """

    def __init__(
        self,
        price_cache: PriceDataCache | None = None,
        coin_filter: CoinFilter | None = None,
        top_n: int = TOP_N_BY_VOLUME_FOR_TOTAL2,
        volume_sma_window: int = VOLUME_SMA_WINDOW,
        quote_currency: str = DEFAULT_QUOTE_CURRENCY,
        freeze_period_days: int = TOTAL2B_ENTRY_FREEZE_PERIOD_DAYS,
        min_coins_for_scaling: int = TOTAL2B_MIN_COINS_FOR_SCALING,
        symbol_replacement_increase_threshold: float = SYMBOL_REPLACEMENT_INCREASE_THRESHOLD,
        symbol_replacement_decrease_threshold: float = SYMBOL_REPLACEMENT_DECREASE_THRESHOLD,
    ):
        self.price_cache = price_cache or PriceDataCache()
        self.coin_filter = coin_filter or CoinFilter()
        self.top_n = top_n
        self.volume_sma_window = volume_sma_window
        self.quote_currency = quote_currency
        self.freeze_period_days = freeze_period_days
        self.min_coins_for_scaling = min_coins_for_scaling
        self.symbol_replacement_increase_threshold = symbol_replacement_increase_threshold
        self.symbol_replacement_decrease_threshold = symbol_replacement_decrease_threshold

    # =========================================================================
    # Data loading + alignment
    # =========================================================================

    def load_all_price_data(
        self,
        coin_ids: list[str] | None = None,
        show_progress: bool = True,
        columns: list[str] | None = None,
    ) -> dict[str, pd.DataFrame]:
        """
        Load price data for all cached coins.

        Args:
            coin_ids: Optional list of coin IDs to load (default: all cached)
            show_progress: Show progress bar
            columns: Optional list of columns to load (memory optimization).
                     Default: ["close", "volume_to"] for TOTAL2 calculation.

        Returns:
            Dictionary mapping coin_id to price DataFrame
        """
        if coin_ids is None:
            coin_ids = self.price_cache.list_cached_coins(self.quote_currency)

        if columns is None:
            columns = ["close", "volume_to"]

        data = {}
        skipped_coins = []
        iterator = tqdm(coin_ids, desc="Loading price data") if show_progress else coin_ids

        for coin_id in iterator:
            df = self.price_cache.get_prices(coin_id, self.quote_currency, columns=columns)
            if df is not None and not df.empty:
                data[coin_id] = df
            else:
                skipped_coins.append(coin_id)

        if skipped_coins:
            logger.debug(
                "Skipped %d coins with no/empty price data: %s",
                len(skipped_coins),
                ", ".join(skipped_coins[:10]) + ("..." if len(skipped_coins) > 10 else ""),
            )

        return data

    def filter_coins_for_total2(self, coin_ids: list[str]) -> list[str]:
        """Filter coin IDs to exclude BTC, derivatives, and stablecoins."""
        eligible = []
        for coin_id in coin_ids:
            should_exclude, _ = self.coin_filter.should_exclude_from_total2(
                coin_id=coin_id,
                name="",
                symbol=coin_id.upper(),
            )
            if not should_exclude:
                eligible.append(coin_id)
        return eligible

    def build_aligned_dataframes(
        self,
        price_data: dict[str, pd.DataFrame],
        show_progress: bool = True,
    ) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
        """
        Build aligned price and volume DataFrames for vectorized calculation.

        Also detects and corrects volume outliers from bad data.

        Returns:
            Tuple of (close_df, volume_df, volume_outliers)
        """
        all_dates = set()
        for df in price_data.values():
            all_dates.update(df.index)
        if not all_dates:
            raise NoDataError("No dates found in price data")

        date_index = pd.date_range(start=min(all_dates), end=max(all_dates), freq="D")

        close_data = {}
        volume_data = {}
        for coin_id, df in price_data.items():
            close_data[coin_id] = df["close"].reindex(date_index)
            volume_data[coin_id] = df["volume_to"].reindex(date_index)

        close_df = pd.DataFrame(close_data, index=date_index)
        volume_df = pd.DataFrame(volume_data, index=date_index)

        volume_df, volume_outliers = self._apply_volume_corrections(
            volume_df, show_progress=show_progress
        )
        return close_df, volume_df, volume_outliers

    def _apply_volume_corrections(
        self,
        volume_df: pd.DataFrame,
        show_progress: bool = True,
    ) -> tuple[pd.DataFrame, list[dict]]:
        """Detect and cap volume outliers using past-only rolling medians."""
        return apply_volume_corrections_to_dataframe(
            volume_df,
            threshold=DEFAULT_VOLUME_OUTLIER_THRESHOLD,
            min_volume=DEFAULT_MIN_VOLUME_FOR_OUTLIER_CHECK,
            window_days=DEFAULT_OUTLIER_WINDOW_DAYS,
            max_iterations=10,
            show_progress=show_progress,
        )

    def apply_volume_sma_smoothing(self, volume_df: pd.DataFrame) -> pd.DataFrame:
        """
        Apply SMA smoothing to volume with zero padding for warmup.

        Zero-padding ensures new coins enter the index gradually over the SMA
        window period, preventing sudden weight jumps.
        """
        return apply_volume_sma_smoothing_to_dataframe(
            volume_df,
            window=self.volume_sma_window,
            zero_pad=True,
        )

    # =========================================================================
    # Eligibility, first-seen, and the iterative index calculation
    # =========================================================================

    def calculate_total2(
        self,
        coin_ids: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        show_progress: bool = True,
    ) -> Total2Result:
        """
        Calculate the volume-weighted TOTAL2 index with freeze period + scaling.

        Args:
            coin_ids: Optional list of coin IDs (default: all cached)
            start_date: Optional start date for index (inclusive)
            end_date: Optional end date for index (inclusive)
            show_progress: Show progress information

        Returns:
            Total2Result with index DataFrame, composition DataFrame, and metadata
        """
        if show_progress:
            logger.info("Loading price data...")
        price_data = self.load_all_price_data(coin_ids, show_progress=show_progress)
        if not price_data:
            raise ProcessorError("No price data available")

        eligible_ids = self.filter_coins_for_total2(list(price_data.keys()))
        if show_progress:
            logger.info("Filtered to %d eligible coins", len(eligible_ids))
        price_data = {cid: df for cid, df in price_data.items() if cid in eligible_ids}
        if not price_data:
            raise ProcessorError("No eligible coins for TOTAL2")

        if show_progress:
            logger.info("Building aligned DataFrames...")
        close_df, volume_df, volume_outliers = self.build_aligned_dataframes(
            price_data, show_progress=show_progress
        )

        if show_progress:
            logger.info("Applying round-trip price corrections...")
        close_df, round_trip_corrections = apply_round_trip_corrections_to_dataframe(
            close_df, show_progress=show_progress
        )

        if show_progress:
            logger.info("Applying volume SMA smoothing...")
        smoothed_volume_df = self.apply_volume_sma_smoothing(volume_df)

        first_seen_dates, symbol_replacements = self._calculate_first_seen_dates(
            close_df, volume_df, show_progress=show_progress
        )
        if show_progress:
            logger.info("Tracking first-seen dates for %d coins", len(first_seen_dates))

        if show_progress:
            logger.info("Calculating TOTAL2 with freeze period and scaling...")
        index_df, composition_records, scaling_events = self._calculate_total2_iterative(
            close_df,
            smoothed_volume_df,
            first_seen_dates,
            show_progress=show_progress,
        )

        if start_date:
            index_df = index_df[index_df.index >= pd.Timestamp(start_date)]
        if end_date:
            index_df = index_df[index_df.index <= pd.Timestamp(end_date)]
        index_df = index_df.dropna(subset=["total2_price"])
        if index_df.empty:
            raise ProcessorError("No valid index values after filtering")

        composition_df = pd.DataFrame(composition_records)
        if not composition_df.empty:
            composition_df["date"] = pd.to_datetime(composition_df["date"])
            if start_date:
                composition_df = composition_df[composition_df["date"] >= pd.Timestamp(start_date)]
            if end_date:
                composition_df = composition_df[composition_df["date"] <= pd.Timestamp(end_date)]

        max_change, max_coin, max_date = self.calculate_max_weight_change(composition_df)
        date_range = (index_df.index.min().date(), index_df.index.max().date())

        return Total2Result(
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
            scaling_events=scaling_events,
            round_trip_corrections=round_trip_corrections,
            symbol_replacements=symbol_replacements,
            index_type="total2b",
        )

    def _calculate_first_seen_dates(
        self,
        close_df: pd.DataFrame,
        volume_df: pd.DataFrame,
        show_progress: bool = True,
    ) -> tuple[dict[str, pd.Timestamp], list[dict]]:
        """
        First date each coin appears with both close > 0 and volume > 0.

        Also runs the symbol-replacement detector and resets first_seen to the
        post-replacement date when a provider reassigns a ticker (HYPE 2024-12,
        MOVE 2024, LIT 2026-01-08 are known examples).

        Returns the first-seen map and the list of detected replacement events
        (also logged when show_progress=True).
        """
        first_seen: dict[str, pd.Timestamp] = {}
        symbol_replacements: list[dict] = []

        for coin_id in close_df.columns:
            if coin_id not in volume_df.columns:
                continue
            price_valid = (close_df[coin_id] > 0) & close_df[coin_id].notna()
            volume_valid = (volume_df[coin_id] > 0) & volume_df[coin_id].notna()
            both_valid = price_valid & volume_valid
            if not both_valid.any():
                continue
            initial_first_seen = both_valid.idxmax()
            replacement_date = detect_symbol_replacement(
                close_df[coin_id],
                increase_threshold=self.symbol_replacement_increase_threshold,
                decrease_threshold=self.symbol_replacement_decrease_threshold,
                first_seen=initial_first_seen,
            )
            if replacement_date is not None:
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
            logger.info("  Detected %d symbol replacement(s):", len(symbol_replacements))
            for event in symbol_replacements:
                logger.info(
                    "    %6s: %s → %s (price %.2e → %.2e)",
                    event["coin"],
                    event["original_first_seen"],
                    event["replacement_date"],
                    event["price_before"],
                    event["price_after"],
                )

        return first_seen, symbol_replacements

    def _build_eligibility_mask(
        self,
        close_df: pd.DataFrame,
        smoothed_volume_df: pd.DataFrame,
        first_seen_dates: dict[str, pd.Timestamp],
    ) -> pd.DataFrame:
        """
        Pre-compute (dates × coins) boolean mask: True where a coin is eligible.

        Eligibility = freeze period passed AND close > 0 AND smoothed volume > 0.
        Vectorising this once at the start beats a per-date inner loop.
        """
        price_valid = (close_df > 0) & close_df.notna()
        volume_valid = (smoothed_volume_df > 0) & smoothed_volume_df.notna()
        base_eligible = price_valid & volume_valid

        freeze_mask = pd.DataFrame(False, index=close_df.index, columns=close_df.columns)
        for coin_id in close_df.columns:
            first_seen = first_seen_dates.get(coin_id)
            if first_seen is None:
                continue
            eligibility_date = first_seen + pd.Timedelta(days=self.freeze_period_days)
            freeze_mask[coin_id] = close_df.index >= eligibility_date

        return base_eligible & freeze_mask

    def _calculate_total2_iterative(
        self,
        close_df: pd.DataFrame,
        smoothed_volume_df: pd.DataFrame,
        first_seen_dates: dict[str, pd.Timestamp],
        show_progress: bool = True,
    ) -> tuple[pd.DataFrame, list[dict], list[dict]]:
        """
        Per-day pass over the eligibility-masked close/volume matrices.

        Scaling formula for new entrants:
            scaled_price = raw_price * prev_total2 / COIN_PRICE_d
        so the entry-day scaled price equals prev_total2 (continuity) and the
        coin carries the same scaling factor for every subsequent day
        (preserves day-over-day return factors).

        Returns:
            (index_df, composition_records, scaling_events)
        """
        eligibility_mask = self._build_eligibility_mask(
            close_df, smoothed_volume_df, first_seen_dates
        )

        dates = close_df.index
        index_records: list[dict] = []
        composition_records: list[dict] = []
        scaling_events: list[dict] = []

        coins_in_index: set[str] = set()
        coin_scaling_factors: dict[str, float] = {}
        prev_total2: float | None = None

        close_values = close_df.values
        volume_values = smoothed_volume_df.values
        eligibility_values = eligibility_mask.values
        coin_ids = close_df.columns.tolist()
        coin_to_idx = {coin: i for i, coin in enumerate(coin_ids)}

        iterator = (
            tqdm(range(len(dates)), desc="TOTAL2 calculation")
            if show_progress
            else range(len(dates))
        )

        for date_idx in iterator:
            dt = dates[date_idx]
            eligible_mask_row = eligibility_values[date_idx]
            eligible_coins = [coin_ids[i] for i in range(len(coin_ids)) if eligible_mask_row[i]]
            if len(eligible_coins) < TOTAL2_MIN_COINS_FOR_INDEX:
                continue

            new_entries = set(eligible_coins) - coins_in_index
            should_scale = (
                len(coins_in_index) >= self.min_coins_for_scaling and prev_total2 is not None
            )
            for coin_id in new_entries:
                if should_scale and prev_total2 is not None and prev_total2 > 0:
                    idx = coin_to_idx[coin_id]
                    raw_price_at_entry = close_values[date_idx, idx]
                    if raw_price_at_entry > 0:
                        scaling_factor = prev_total2 / raw_price_at_entry
                        coin_scaling_factors[coin_id] = scaling_factor
                        scaling_events.append(
                            {
                                "date": str(dt.date()),
                                "type": "price_scaling",
                                "coin": coin_id.upper(),
                                "original": float(raw_price_at_entry),
                                "corrected": float(raw_price_at_entry * scaling_factor),
                                "change_factor": float(scaling_factor),
                                "prev_total2b": float(prev_total2),
                            }
                        )

            coins_in_index = set(eligible_coins)

            volumes: list[tuple[str, float, float]] = []
            for coin_id in eligible_coins:
                idx = coin_to_idx[coin_id]
                vol = volume_values[date_idx, idx]
                raw_price = close_values[date_idx, idx]
                price = (
                    raw_price * coin_scaling_factors[coin_id]
                    if coin_id in coin_scaling_factors
                    else raw_price
                )
                volumes.append((coin_id, vol, price))

            volumes.sort(key=lambda x: x[1], reverse=True)
            top_n = volumes[: self.top_n]
            total_volume = sum(v for _, v, _ in top_n)
            if total_volume <= 0:
                continue

            weighted_sum = sum(p * v for _, v, p in top_n)
            total2_price = weighted_sum / total_volume

            index_records.append(
                {
                    "date": dt,
                    "total2_price": total2_price,
                    "total_volume": total_volume,
                    "coin_count": len(top_n),
                }
            )
            prev_total2 = total2_price

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

        if not index_records:
            index_df = pd.DataFrame(columns=["total2_price", "total_volume", "coin_count"])
        else:
            index_df = pd.DataFrame(index_records).set_index("date")

        if show_progress and scaling_events:
            logger.info("  Applied scaling to %d new coin entries:", len(scaling_events))
            for event in scaling_events[:10]:
                logger.info(
                    "    %6s %s: scaled by %.6f (prev TOTAL2: %.4f)",
                    event["coin"],
                    event["date"],
                    event["change_factor"],
                    event["prev_total2b"],
                )
            if len(scaling_events) > 10:
                logger.info("    ... and %d more", len(scaling_events) - 10)

        return index_df, composition_records, scaling_events

    def get_freeze_period_status(
        self,
        price_data: dict[str, pd.DataFrame] | None = None,
        target_date: date | None = None,
    ) -> list[dict]:
        """
        Freeze-period status for all coins on a given date (default: today).

        Useful for understanding which coins are waiting to enter the index.
        """
        if price_data is None:
            price_data = self.load_all_price_data(show_progress=False)
        if target_date is None:
            target_date = date.today()
        target_ts = pd.Timestamp(target_date)

        statuses: list[dict] = []
        for coin_id, df in price_data.items():
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

    # =========================================================================
    # Stats + IO
    # =========================================================================

    def calculate_max_weight_change(
        self,
        composition_df: pd.DataFrame,
        min_date: date | None = None,
    ) -> tuple[float | None, str | None, date | None]:
        """
        Largest day-over-day weight change for any coin in the index.

        Used to flag dates where index variation reflects composition churn
        rather than actual price moves. Default min_date = 2016-07-04 (the day
        the index first had 30 coins; weights are noisy before that).
        """
        if composition_df.empty:
            return None, None, None
        if min_date is None:
            min_date = date(2016, 7, 4)

        filtered_df = composition_df[composition_df["date"] >= pd.Timestamp(min_date)]
        if filtered_df.empty:
            return None, None, None

        weight_pivot = filtered_df.pivot_table(
            index="date", columns="coin_id", values="weight", aggfunc="first"
        )
        weight_pivot = weight_pivot.fillna(0) * 100
        weight_diff = weight_pivot.diff().iloc[1:]
        if weight_diff.empty:
            return None, None, None

        abs_diff = weight_diff.abs()
        max_change = abs_diff.max().max()
        if pd.isna(max_change):
            return None, None, None

        abs_stacked = abs_diff.stack()
        dt, coin_id = abs_stacked.idxmax()
        actual_change = weight_diff.loc[dt, coin_id]
        change_date = dt.date() if hasattr(dt, "date") else dt
        return float(actual_change), coin_id, change_date

    def calculate_coin_statistics(self, composition_df: pd.DataFrame) -> list[dict]:
        """
        Per-coin participation stats, ranked by days_in_total2 descending.

        One record per coin that ever appeared in the index, with first/last
        date, first/last price+weight, min/max price+weight, total days, and
        whether the coin is still in the index on the latest date.
        """
        if composition_df.empty:
            return []

        latest_date = composition_df["date"].max()
        latest_coins = set(
            composition_df[composition_df["date"] == latest_date]["coin_id"].tolist()
        )

        sorted_df = composition_df.sort_values("date")
        grouped = sorted_df.groupby("coin_id")
        agg = grouped.agg(
            first_date=("date", "first"),
            first_price=("price_btc", "first"),
            first_weight=("weight", "first"),
            last_date=("date", "last"),
            last_price=("price_btc", "last"),
            last_weight=("weight", "last"),
            min_price=("price_btc", "min"),
            max_price=("price_btc", "max"),
            min_weight=("weight", "min"),
            max_weight=("weight", "max"),
            days_in_total2=("date", "count"),
        )
        agg = agg.sort_values("days_in_total2", ascending=False)
        agg["rank"] = range(1, len(agg) + 1)

        coin_stats: list[dict] = []
        for coin_id, row in agg.iterrows():
            fd = row["first_date"]
            ld = row["last_date"]
            coin_stats.append(
                {
                    "coin_id": coin_id.upper(),
                    "url": coin_url(coin_id),
                    "days_in_total2": int(row["days_in_total2"]),
                    "still_present": coin_id in latest_coins,
                    "first_date": str(fd.date() if hasattr(fd, "date") else fd),
                    "first_price": float(row["first_price"]),
                    "first_weight": float(row["first_weight"]) * 100,
                    "last_date": str(ld.date() if hasattr(ld, "date") else ld),
                    "last_price": float(row["last_price"]),
                    "last_weight": float(row["last_weight"]) * 100,
                    "min_price": float(row["min_price"]),
                    "max_price": float(row["max_price"]),
                    "min_weight": float(row["min_weight"]) * 100,
                    "max_weight": float(row["max_weight"]) * 100,
                    "rank": int(row["rank"]),
                }
            )
        return coin_stats

    def save_results(
        self,
        result: Total2Result,
        index_path: Path | None = None,
        composition_path: Path | None = None,
    ) -> tuple[Path, Path]:
        """Save index, composition, and stats JSON to disk."""
        index_path = index_path or TOTAL2_INDEX_FILE
        composition_path = composition_path or TOTAL2_COMPOSITION_FILE

        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        result.index_df.to_parquet(index_path)
        if not result.composition_df.empty:
            result.composition_df.to_parquet(composition_path, index=False)

        coin_statistics = self.calculate_coin_statistics(result.composition_df)
        max_weight_info = {
            "max_weight_change": result.max_weight_change,
            "coin": (
                result.max_weight_change_coin.upper() if result.max_weight_change_coin else None
            ),
            "date": str(result.max_weight_change_date) if result.max_weight_change_date else None,
            "volume_outliers_corrected": result.volume_outliers_corrected or [],
            "scaling_events": result.scaling_events or [],
            "round_trip_corrections": result.round_trip_corrections or [],
            "symbol_replacements": result.symbol_replacements or [],
            "coin_statistics": coin_statistics,
            "index_type": result.index_type,
        }
        TOTAL2_MAX_WEIGHT_CHANGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(TOTAL2_MAX_WEIGHT_CHANGE_FILE, "w", encoding="utf-8") as f:
            json.dump(max_weight_info, f, indent=2)

        return index_path, composition_path


def get_processor(**kwargs) -> Total2Processor:
    """Factory function to create a Total2Processor."""
    return Total2Processor(**kwargs)


__all__ = [
    "NoDataError",
    "ProcessorError",
    "Total2Processor",
    "Total2Result",
    "get_processor",
]

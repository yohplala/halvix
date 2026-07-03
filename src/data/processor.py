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
from datetime import date, timedelta
from pathlib import Path
from typing import cast

import numpy as np
import polars as pl
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
    TOTAL2_STALE_ENTRY_REANCHOR_RATIO,
    TOTAL2B_ENTRY_FREEZE_PERIOD_DAYS,
    TOTAL2B_MIN_COINS_FOR_SCALING,
    VOLUME_SMA_WINDOW,
)
from data.cache import PriceDataCache
from data.coin_metadata import CoinMetadataResolver
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

    index_df: pl.DataFrame  # Daily index values (date, total2_price, total_volume, coin_count)
    composition_df: (
        pl.DataFrame
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
    # Coins whose stale first-eligibility multiplier was re-anchored to the index
    # level on their first top-N entry (bart fix): scaled price had drifted above
    # TOTAL2_STALE_ENTRY_REANCHOR_RATIO x the index before the coin joined.
    stale_entry_reanchors: list[dict] | None = None


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
    6. Iterate dates: each day, scale new freeze-eligible entrants by
       ``prev_total2 / raw_price``, pick the top-N by volume, re-anchor any coin
       that enters the composition carrying a stale multiplier (scaled price
       above ``stale_entry_reanchor_ratio`` x the index — the bart fix), and
       compute the volume-weighted average of scaled prices.
    7. Save index, composition, and metadata (corrections, scaling events,
       stale-entry re-anchors, coin statistics).
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
        stale_entry_reanchor_ratio: float = TOTAL2_STALE_ENTRY_REANCHOR_RATIO,
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
        # Bart safety net: a coin entering the top-N with a scaled price above
        # this multiple of the index has a stale multiplier and is re-anchored to
        # the index level (0/None disables).
        self.stale_entry_reanchor_ratio = stale_entry_reanchor_ratio

    # =========================================================================
    # Data loading + alignment
    # =========================================================================

    def load_all_price_data(
        self,
        coin_ids: list[str] | None = None,
        show_progress: bool = True,
        columns: list[str] | None = None,
    ) -> dict[str, pl.DataFrame]:
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
            if df is not None and not df.is_empty():
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
        price_data: dict[str, pl.DataFrame],
        show_progress: bool = True,
    ) -> tuple[pl.DataFrame, pl.DataFrame, list[dict]]:
        """
        Build aligned wide (dates × coins) close/volume frames for calculation.

        Each coin's series is left-joined onto a contiguous daily ``date`` spine
        (missing days become null). Also detects and corrects volume outliers.

        Returns:
            Tuple of (close_df, volume_df, volume_outliers). Both wide frames
            carry a leading ``date`` column then one column per coin.
        """
        min_date: date | None = None
        max_date: date | None = None
        for df in price_data.values():
            lo = cast("date", df["date"].min())
            hi = cast("date", df["date"].max())
            min_date = lo if min_date is None else min(min_date, lo)
            max_date = hi if max_date is None else max(max_date, hi)
        if min_date is None or max_date is None:
            raise NoDataError("No dates found in price data")

        # Contiguous daily spine; one left-joined column per coin (null on gaps).
        spine = pl.DataFrame({"date": pl.date_range(min_date, max_date, interval="1d", eager=True)})
        coin_ids = list(price_data.keys())
        long = pl.concat(
            df.select(
                pl.col("date"),
                pl.lit(coin_id).alias("coin_id"),
                pl.col("close"),
                pl.col("volume_to"),
            )
            for coin_id, df in price_data.items()
        )
        close_wide = long.pivot(values="close", index="date", on="coin_id")
        volume_wide = long.pivot(values="volume_to", index="date", on="coin_id")

        # Align to the full spine and pin column order to price_data order so the
        # top-N volume tie-break is deterministic (matches the legacy path).
        order = ["date", *coin_ids]
        close_df = spine.join(close_wide, on="date", how="left").sort("date").select(order)
        volume_df = spine.join(volume_wide, on="date", how="left").sort("date").select(order)

        volume_df, volume_outliers = self._apply_volume_corrections(
            volume_df, show_progress=show_progress
        )
        return close_df, volume_df, volume_outliers

    def _apply_volume_corrections(
        self,
        volume_df: pl.DataFrame,
        show_progress: bool = True,
    ) -> tuple[pl.DataFrame, list[dict]]:
        """Detect and cap volume outliers using past-only rolling medians."""
        return apply_volume_corrections_to_dataframe(
            volume_df,
            threshold=DEFAULT_VOLUME_OUTLIER_THRESHOLD,
            min_volume=DEFAULT_MIN_VOLUME_FOR_OUTLIER_CHECK,
            window_days=DEFAULT_OUTLIER_WINDOW_DAYS,
            max_iterations=10,
            show_progress=show_progress,
        )

    def apply_volume_sma_smoothing(self, volume_df: pl.DataFrame) -> pl.DataFrame:
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
        index_df, composition_records, scaling_events, stale_entry_reanchors = (
            self._calculate_total2_iterative(
                close_df,
                smoothed_volume_df,
                first_seen_dates,
                show_progress=show_progress,
            )
        )

        if start_date:
            index_df = index_df.filter(pl.col("date") >= start_date)
        if end_date:
            index_df = index_df.filter(pl.col("date") <= end_date)
        index_df = index_df.drop_nulls(subset=["total2_price"])
        if index_df.is_empty():
            raise ProcessorError("No valid index values after filtering")

        composition_df = (
            pl.DataFrame(composition_records) if composition_records else pl.DataFrame()
        )
        if not composition_df.is_empty():
            if start_date:
                composition_df = composition_df.filter(pl.col("date") >= start_date)
            if end_date:
                composition_df = composition_df.filter(pl.col("date") <= end_date)

        max_change, max_coin, max_date = self.calculate_max_weight_change(composition_df)
        date_range = (
            cast("date", index_df["date"].min()),
            cast("date", index_df["date"].max()),
        )

        return Total2Result(
            index_df=index_df,
            composition_df=composition_df,
            coins_processed=len(price_data),
            date_range=date_range,
            avg_coins_per_day=(
                cast("float", index_df["coin_count"].mean())
                if "coin_count" in index_df.columns
                else 0.0
            ),
            max_weight_change=max_change,
            max_weight_change_coin=max_coin,
            max_weight_change_date=max_date,
            volume_outliers_corrected=volume_outliers,
            scaling_events=scaling_events,
            round_trip_corrections=round_trip_corrections,
            symbol_replacements=symbol_replacements,
            stale_entry_reanchors=stale_entry_reanchors,
            index_type="total2b",
        )

    def _calculate_first_seen_dates(
        self,
        close_df: pl.DataFrame,
        volume_df: pl.DataFrame,
        show_progress: bool = True,
    ) -> tuple[dict[str, date], list[dict]]:
        """
        First date each coin appears with both close > 0 and volume > 0.

        Also runs the symbol-replacement detector and resets first_seen to the
        post-replacement date when a provider reassigns a ticker (HYPE 2024-12,
        MOVE 2024, LIT 2026-01-08 are known examples).

        Returns the first-seen map and the list of detected replacement events
        (also logged when show_progress=True).
        """
        first_seen: dict[str, date] = {}
        symbol_replacements: list[dict] = []

        dates = close_df["date"].to_list()
        date_pos = {d: i for i, d in enumerate(dates)}
        coin_cols = [c for c in close_df.columns if c != "date"]

        for coin_id in coin_cols:
            if coin_id not in volume_df.columns:
                continue
            close_col = close_df[coin_id]
            both_valid = (close_col.fill_null(0) > 0) & (volume_df[coin_id].fill_null(0) > 0)
            if not both_valid.any():
                continue
            initial_first_seen = dates[cast("int", both_valid.arg_max())]  # first True
            replacement_date = detect_symbol_replacement(
                close_col,
                dates,
                increase_threshold=self.symbol_replacement_increase_threshold,
                decrease_threshold=self.symbol_replacement_decrease_threshold,
                first_seen=initial_first_seen,
            )
            if replacement_date is not None:
                close_vals = close_col.to_numpy()
                symbol_replacements.append(
                    {
                        "coin": coin_id.upper(),
                        "original_first_seen": str(initial_first_seen),
                        "replacement_date": str(replacement_date),
                        "price_before": float(
                            close_vals[date_pos[replacement_date - timedelta(days=1)]]
                        ),
                        "price_after": float(close_vals[date_pos[replacement_date]]),
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
        close_m: np.ndarray,
        volume_m: np.ndarray,
        coin_ids: list[str],
        dates: list[date],
        first_seen_dates: dict[str, date],
    ) -> np.ndarray:
        """
        Pre-compute (days × coins) boolean matrix: True where a coin is eligible.

        Eligibility = freeze period passed AND close > 0 AND smoothed volume > 0.
        Null closes/volumes read back as NaN, and ``NaN > 0`` is False — matching
        the previous ``> 0 & notna`` guard.
        """
        base_eligible = (close_m > 0) & (volume_m > 0)

        freeze = np.zeros(base_eligible.shape, dtype=bool)
        dates_arr = np.array(dates, dtype=object)
        for j, coin_id in enumerate(coin_ids):
            first_seen = first_seen_dates.get(coin_id)
            if first_seen is None:
                continue
            eligibility_date = first_seen + timedelta(days=self.freeze_period_days)
            freeze[:, j] = dates_arr >= eligibility_date

        return base_eligible & freeze

    def _calculate_total2_iterative(
        self,
        close_df: pl.DataFrame,
        smoothed_volume_df: pl.DataFrame,
        first_seen_dates: dict[str, date],
        show_progress: bool = True,
    ) -> tuple[pl.DataFrame, list[dict], list[dict], list[dict]]:
        """
        Per-day pass over the eligibility-masked close/volume matrices.

        Entry-day price scaling. When a coin first becomes freeze-eligible (and
        the index is mature) its multiplier is anchored so its scaled price lines
        up with the prior index value::

            scaled_price = raw_price * prev_total2 / raw_price_at_entry

        The entry-day scaled price then equals prev_total2 (continuity) and the
        coin tracks its day-over-day return via that fixed factor. This
        first-eligibility anchoring is preserved unchanged — it is what defines
        the historical index — with one correction:

        Stale-entry re-anchor (fixes A+B merged — the 2026-06 "bart"). A coin can
        sit freeze-eligible but below the top-N by volume for months while its
        raw price drifts far. Its first-eligibility multiplier then goes stale,
        and on the day its volume finally ranks it INTO the top-N its scaled
        price can be tens of times the index level (LAB entered the top-N in May
        2026 carrying a Dec-2025 anchor at ~36x the index and, at ~1.6% volume
        weight, dominated the volume-weighted price mean, spiking then crashing
        the index). When a coin ENTERS the composition (not present the previous
        index day) with a scaled price above
        ``stale_entry_reanchor_ratio`` x prev_total2, its factor is RE-ANCHORED
        to the current index level — the coin joins at ~1x and then tracks its
        own return. Re-anchoring (rather than excluding the coin, an earlier
        idea) keeps a real constituent in the index and, crucially, only touches
        *entering* coins: a continuously present long-term outperformer such as
        BNB (legitimately many x the index) is never re-anchored, so real
        multi-cycle appreciation is preserved.

        Returns:
            (index_df, composition_records, scaling_events, stale_entry_reanchors)
        """
        coin_ids = [c for c in close_df.columns if c != "date"]
        dates = close_df["date"].to_list()
        close_values = close_df.select(coin_ids).to_numpy()
        volume_values = smoothed_volume_df.select(coin_ids).to_numpy()
        eligibility_values = self._build_eligibility_mask(
            close_values, volume_values, coin_ids, dates, first_seen_dates
        )

        index_records: list[dict] = []
        composition_records: list[dict] = []
        scaling_events: list[dict] = []
        stale_entry_reanchors: list[dict] = []

        coin_scaling_factors: dict[str, float] = {}
        coins_eligible_prev: set[str] = set()  # previous day's eligible set
        prev_index_coins: set[str] = set()  # composition (top-N) of the previous index day
        prev_total2: float | None = None

        reanchor_ratio = self.stale_entry_reanchor_ratio
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

            should_scale = (
                len(coins_eligible_prev) >= self.min_coins_for_scaling
                and prev_total2 is not None
                and prev_total2 > 0
            )

            # First-eligibility anchoring (unchanged; defines the historical index).
            new_entries = set(eligible_coins) - coins_eligible_prev
            for coin_id in new_entries:
                if should_scale and prev_total2 is not None:
                    raw_price_at_entry = close_values[date_idx, coin_to_idx[coin_id]]
                    if raw_price_at_entry > 0:
                        scaling_factor = prev_total2 / raw_price_at_entry
                        coin_scaling_factors[coin_id] = scaling_factor
                        scaling_events.append(
                            {
                                "date": str(dt),
                                "type": "price_scaling",
                                "coin": coin_id.upper(),
                                "original": float(raw_price_at_entry),
                                "corrected": float(raw_price_at_entry * scaling_factor),
                                "change_factor": float(scaling_factor),
                                "prev_total2b": float(prev_total2),
                            }
                        )
            coins_eligible_prev = set(eligible_coins)

            # Top-N by volume (volume ranking is independent of price scaling).
            top_ids = sorted(
                eligible_coins,
                key=lambda c: volume_values[date_idx, coin_to_idx[c]],
                reverse=True,
            )[: self.top_n]

            # --- Stale-entry re-anchor (fixes A+B) ----------------------------
            # A coin ENTERING the composition (not in the previous index day)
            # whose scaled price is already > reanchor_ratio x the index carried a
            # stale first-eligibility multiplier. Reset it to the index level so
            # it joins at ~1x rather than dominating (the LAB bart) or being
            # dropped (which would corrupt the index for legitimate long-term
            # outperformers). Continuously present coins are never touched.
            if should_scale and prev_total2 is not None and reanchor_ratio and reanchor_ratio > 0:
                limit = reanchor_ratio * prev_total2
                for coin_id in top_ids:
                    if coin_id in prev_index_coins:
                        continue  # continuing member — keep its factor (tracks return)
                    idx = coin_to_idx[coin_id]
                    raw_price = close_values[date_idx, idx]
                    if raw_price <= 0:
                        continue
                    factor = coin_scaling_factors.get(coin_id)
                    scaled = raw_price * factor if factor is not None else raw_price
                    if scaled > limit:
                        coin_scaling_factors[coin_id] = prev_total2 / raw_price
                        stale_entry_reanchors.append(
                            {
                                "date": str(dt),
                                "coin": coin_id.upper(),
                                "stale_scaled_price": float(scaled),
                                "reanchored_to": float(prev_total2),
                                "stale_ratio": float(scaled / prev_total2),
                                "raw_price": float(raw_price),
                            }
                        )

            volumes: list[tuple[str, float, float]] = []
            for coin_id in top_ids:
                idx = coin_to_idx[coin_id]
                vol = volume_values[date_idx, idx]
                raw_price = close_values[date_idx, idx]
                price = (
                    raw_price * coin_scaling_factors[coin_id]
                    if coin_id in coin_scaling_factors
                    else raw_price
                )
                volumes.append((coin_id, vol, price))

            total_volume = sum(v for _, v, _ in volumes)
            if total_volume <= 0:
                continue

            weighted_sum = sum(p * v for _, v, p in volumes)
            total2_price = weighted_sum / total_volume

            index_records.append(
                {
                    "date": dt,
                    "total2_price": total2_price,
                    "total_volume": total_volume,
                    "coin_count": len(volumes),
                }
            )
            prev_total2 = total2_price
            prev_index_coins = {c for c, _, _ in volumes}

            for rank, (coin_id, vol, price) in enumerate(volumes, start=1):
                composition_records.append(
                    {
                        "date": dt,
                        "rank": rank,
                        "coin_id": coin_id,
                        "volume": vol,
                        "weight": vol / total_volume,
                        "price_btc": price,
                    }
                )

        if not index_records:
            index_df = pl.DataFrame(
                schema={
                    "date": pl.Date,
                    "total2_price": pl.Float64,
                    "total_volume": pl.Float64,
                    "coin_count": pl.Int64,
                }
            )
        else:
            index_df = pl.DataFrame(index_records)

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

        if show_progress and stale_entry_reanchors:
            logger.info(
                "  Re-anchored %d stale-entry coin(s) (scaled price > %.1fx index on entry):",
                len(stale_entry_reanchors),
                reanchor_ratio,
            )
            for ev in stale_entry_reanchors[:10]:
                logger.info(
                    "    %6s %s: %.4f (%.1fx index) -> %.4f",
                    ev["coin"],
                    ev["date"],
                    ev["stale_scaled_price"],
                    ev["stale_ratio"],
                    ev["reanchored_to"],
                )
            if len(stale_entry_reanchors) > 10:
                logger.info("    ... and %d more", len(stale_entry_reanchors) - 10)

        return index_df, composition_records, scaling_events, stale_entry_reanchors

    def get_freeze_period_status(
        self,
        price_data: dict[str, pl.DataFrame] | None = None,
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

        statuses: list[dict] = []
        for coin_id, df in price_data.items():
            if "close" not in df.columns or "volume_to" not in df.columns:
                continue
            valid = df.filter((pl.col("close") > 0) & (pl.col("volume_to") > 0))
            if valid.is_empty():
                continue
            first_seen = cast("date", valid["date"].min())
            days_since_first = (target_date - first_seen).days
            days_remaining = self.freeze_period_days - days_since_first
            statuses.append(
                {
                    "coin_id": coin_id.upper(),
                    "first_seen": str(first_seen),
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
        composition_df: pl.DataFrame,
        min_date: date | None = None,
    ) -> tuple[float | None, str | None, date | None]:
        """
        Largest day-over-day weight change for any coin in the index.

        Used to flag dates where index variation reflects composition churn
        rather than actual price moves. Default min_date = 2016-07-04 (the day
        the index first had 30 coins; weights are noisy before that).
        """
        if composition_df.is_empty():
            return None, None, None
        if min_date is None:
            min_date = date(2016, 7, 4)

        filtered_df = composition_df.filter(pl.col("date") >= min_date)
        if filtered_df.is_empty():
            return None, None, None

        # Wide weights (dates × coins) as percentages; absent coin-days are 0.
        wide = filtered_df.sort("date").pivot(
            values="weight", index="date", on="coin_id", aggregate_function="first"
        )
        coin_cols = [c for c in wide.columns if c != "date"]
        wide = wide.with_columns((pl.col(c).fill_null(0.0) * 100) for c in coin_cols)

        # Day-over-day diff per coin, drop the first (all-null) row, then find the
        # single largest |change| across the whole matrix via a long form.
        diff = wide.select(pl.col("date"), *(pl.col(c).diff().alias(c) for c in coin_cols)).slice(1)
        if diff.is_empty():
            return None, None, None
        long = (
            diff.unpivot(index="date", on=coin_cols, variable_name="coin_id", value_name="change")
            .drop_nulls("change")
            .with_columns(pl.col("change").abs().alias("abs_change"))
        )
        if long.is_empty():
            return None, None, None
        # Tie-break by earliest date then coin order to match the legacy argmax.
        top = long.sort(["abs_change", "date", "coin_id"], descending=[True, False, False]).row(
            0, named=True
        )
        return float(top["change"]), top["coin_id"], top["date"]

    def calculate_coin_statistics(self, composition_df: pl.DataFrame) -> list[dict]:
        """
        Per-coin participation stats, ranked by days_in_total2 descending.

        One record per coin that ever appeared in the index, with first/last
        date, first/last price+weight, min/max price+weight, total days, and
        whether the coin is still in the index on the latest date.
        """
        if composition_df.is_empty():
            return []

        resolver = CoinMetadataResolver()
        latest_date = composition_df["date"].max()
        latest_coins = set(
            composition_df.filter(pl.col("date") == latest_date)["coin_id"].to_list()
        )

        # first/last are order-based → sort by date so they mean earliest/latest.
        agg = (
            composition_df.sort("date")
            .group_by("coin_id")
            .agg(
                pl.col("date").first().alias("first_date"),
                pl.col("price_btc").first().alias("first_price"),
                pl.col("weight").first().alias("first_weight"),
                pl.col("date").last().alias("last_date"),
                pl.col("price_btc").last().alias("last_price"),
                pl.col("weight").last().alias("last_weight"),
                pl.col("price_btc").min().alias("min_price"),
                pl.col("price_btc").max().alias("max_price"),
                pl.col("weight").min().alias("min_weight"),
                pl.col("weight").max().alias("max_weight"),
                pl.len().alias("days_in_total2"),
            )
            # days desc, coin_id asc tie-break matches the legacy stable sort.
            .sort(["days_in_total2", "coin_id"], descending=[True, False])
            .with_row_index("rank", offset=1)
        )

        coin_stats: list[dict] = []
        for row in agg.iter_rows(named=True):
            meta = resolver.resolve(row["coin_id"])
            coin_stats.append(
                {
                    "coin_id": meta.ticker,
                    "url": meta.url,
                    "days_in_total2": int(row["days_in_total2"]),
                    "still_present": row["coin_id"] in latest_coins,
                    "first_date": str(row["first_date"]),
                    "first_price": float(row["first_price"]),
                    "first_weight": float(row["first_weight"]) * 100,
                    "last_date": str(row["last_date"]),
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
        result.index_df.write_parquet(index_path)
        if not result.composition_df.is_empty():
            result.composition_df.write_parquet(composition_path)

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
            "stale_entry_reanchors": result.stale_entry_reanchors or [],
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

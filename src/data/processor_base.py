"""
Base data processor for TOTAL2 index calculations.

Contains shared algorithms for both TOTAL2 and TOTAL2b:
- Volume-weighted average calculation
- Volume outlier detection and correction (via price_filters module)
- Volume SMA smoothing with zero padding (via price_filters module)
- Coin filtering for eligibility
- Composition record building
- Max weight change tracking

The volume outlier detection and SMA smoothing algorithms are also
available as standalone functions in data/price_filters.py for use
by other modules (e.g., pattern analysis).
"""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
from tqdm import tqdm

from analysis.filters import CoinFilter
from config import (
    DEFAULT_QUOTE_CURRENCY,
    PROCESSED_DIR,
    TOP_N_BY_VOLUME_FOR_TOTAL2,
    TOTAL2_COMPOSITION_FILE,
    TOTAL2_INDEX_FILE,
    TOTAL2_MAX_WEIGHT_CHANGE_FILE,
    VOLUME_SMA_WINDOW,
    coin_url,
)
from data.cache import PriceDataCache
from data.price_filters import (
    DEFAULT_MIN_VOLUME_FOR_OUTLIER_CHECK,
    DEFAULT_OUTLIER_WINDOW_DAYS,
    DEFAULT_VOLUME_OUTLIER_THRESHOLD,
    apply_volume_corrections_to_dataframe,
    apply_volume_sma_smoothing_to_dataframe,
)
from utils.logging import get_logger

logger = get_logger(__name__)


class ProcessorError(Exception):
    """Base exception for processor errors."""

    pass


class NoDataError(ProcessorError):
    """Raised when required data is missing or unavailable."""

    pass


@dataclass
class Total2Result:
    """Complete result of TOTAL2/TOTAL2b calculation."""

    index_df: pd.DataFrame  # Daily index values (date, total2_price, total_volume, coin_count)
    composition_df: (
        pd.DataFrame
    )  # Daily composition (date, rank, coin_id, volume, weight, price_btc)
    coins_processed: int
    date_range: tuple[date, date]
    avg_coins_per_day: float
    index_type: str = "total2"  # "total2" or "total2b"
    max_weight_change: float | None = None
    max_weight_change_coin: str | None = None
    max_weight_change_date: date | None = None
    volume_outliers_corrected: list[dict] | None = None
    scaling_events: list[dict] | None = None
    round_trip_corrections: list[dict] | None = None


class BaseTotal2Processor(ABC):
    """
    Base processor for TOTAL2-family index calculations.

    Shared functionality:
    - Loading and filtering price data
    - Volume outlier detection and correction
    - Volume SMA smoothing with zero padding
    - Volume-weighted average calculation
    - Composition record building
    - Max weight change tracking
    - Save/load operations

    Subclasses implement:
    - Price adjustment strategy (capping vs scaling)
    - Coin entry timing (immediate vs freeze period)
    """

    # Index type identifier for subclasses to override
    INDEX_TYPE = "base"

    def __init__(
        self,
        price_cache: PriceDataCache | None = None,
        coin_filter: CoinFilter | None = None,
        top_n: int = TOP_N_BY_VOLUME_FOR_TOTAL2,
        volume_sma_window: int = VOLUME_SMA_WINDOW,
        quote_currency: str = DEFAULT_QUOTE_CURRENCY,
    ):
        """
        Initialize the processor.

        Args:
            price_cache: Cache for price data (default: new instance)
            coin_filter: Coin filter for exclusions (default: new instance)
            top_n: Number of coins to include in index (default: TOP_N_BY_VOLUME_FOR_TOTAL2)
            volume_sma_window: SMA window for volume smoothing (default: VOLUME_SMA_WINDOW)
            quote_currency: Quote currency for prices (default: DEFAULT_QUOTE_CURRENCY)
        """
        self.price_cache = price_cache or PriceDataCache()
        self.coin_filter = coin_filter or CoinFilter()
        self.top_n = top_n
        self.volume_sma_window = volume_sma_window
        self.quote_currency = quote_currency

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

        # Default to only loading columns needed for TOTAL2 calculation
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
        """
        Filter coin IDs to exclude BTC, derivatives, and stablecoins.

        Args:
            coin_ids: List of coin IDs to filter

        Returns:
            Filtered list of coin IDs eligible for TOTAL2
        """
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

        Creates two DataFrames with:
        - Rows: all dates from earliest to latest across all coins
        - Columns: coin IDs

        Also detects and corrects volume outliers from bad data.

        Args:
            price_data: Dictionary of price DataFrames per coin
            show_progress: Whether to print progress messages

        Returns:
            Tuple of (close_df, volume_df, volume_outliers)
        """
        # Find global date range
        all_dates = set()
        for df in price_data.values():
            all_dates.update(df.index)

        if not all_dates:
            raise NoDataError("No dates found in price data")

        # Create complete date index
        min_date = min(all_dates)
        max_date = max(all_dates)
        date_index = pd.date_range(start=min_date, end=max_date, freq="D")

        # Build price and volume DataFrames
        close_data = {}
        volume_data = {}

        for coin_id, df in price_data.items():
            close_data[coin_id] = df["close"].reindex(date_index)
            volume_data[coin_id] = df["volume_to"].reindex(date_index)

        close_df = pd.DataFrame(close_data, index=date_index)
        volume_df = pd.DataFrame(volume_data, index=date_index)

        # Apply volume data corrections for outliers
        volume_df, volume_outliers = self._apply_volume_corrections(
            volume_df, show_progress=show_progress
        )

        return close_df, volume_df, volume_outliers

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

        Uses the common helper from data/price_filters.py which is also
        available for use by other modules (e.g., pattern analysis).

        Args:
            volume_df: DataFrame with volume data (dates × coins)
            show_progress: Whether to print correction messages

        Returns:
            Tuple of (corrected_volume_df, list_of_corrections)
        """
        return apply_volume_corrections_to_dataframe(
            volume_df,
            threshold=DEFAULT_VOLUME_OUTLIER_THRESHOLD,
            min_volume=DEFAULT_MIN_VOLUME_FOR_OUTLIER_CHECK,
            window_days=DEFAULT_OUTLIER_WINDOW_DAYS,
            max_iterations=10,
            show_progress=show_progress,
        )

    def apply_volume_sma_smoothing(
        self,
        volume_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Apply SMA smoothing to volume data with zero padding for warmup.

        Zero-padding ensures new coins enter the index gradually over the
        SMA window period, preventing sudden weight jumps.

        Uses the common helper from data/price_filters.py which is also
        available for use by other modules (e.g., pattern analysis).

        Args:
            volume_df: DataFrame with volume data (dates × coins)

        Returns:
            Smoothed volume DataFrame
        """
        return apply_volume_sma_smoothing_to_dataframe(
            volume_df,
            window=self.volume_sma_window,
            zero_pad=True,
        )

    def calculate_max_weight_change(
        self,
        composition_df: pd.DataFrame,
        min_date: date | None = None,
    ) -> tuple[float | None, str | None, date | None]:
        """
        Calculate the maximum daily weight change for any coin in the index.

        Args:
            composition_df: DataFrame with columns: date, rank, coin_id, weight, ...
            min_date: Only consider dates >= this date (default: 2016-07-04)

        Returns:
            Tuple of (max_change_pct, coin_id, date) or (None, None, None)
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

        weight_diff = weight_pivot.diff()
        weight_diff = weight_diff.iloc[1:]

        if weight_diff.empty:
            return None, None, None

        abs_diff = weight_diff.abs()
        max_change = abs_diff.max().max()

        if pd.isna(max_change):
            return None, None, None

        # Vectorized lookup: stack to Series, find argmax
        abs_stacked = abs_diff.stack()
        dt, coin_id = abs_stacked.idxmax()
        actual_change = weight_diff.loc[dt, coin_id]
        change_date = dt.date() if hasattr(dt, "date") else dt
        return float(actual_change), coin_id, change_date

    def calculate_coin_statistics(
        self,
        composition_df: pd.DataFrame,
    ) -> list[dict]:
        """
        Calculate statistics for each coin's participation in the index.

        Returns ranking of coins by number of days they appear in the index.

        Args:
            composition_df: DataFrame with daily composition records

        Returns:
            List of coin statistics dictionaries, sorted by days_in_total2 descending
        """
        if composition_df.empty:
            return []

        latest_date = composition_df["date"].max()
        latest_coins = set(
            composition_df[composition_df["date"] == latest_date]["coin_id"].tolist()
        )

        # Sort once, then use first/last via groupby
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
        # Sort by days descending, assign rank
        agg = agg.sort_values("days_in_total2", ascending=False)
        agg["rank"] = range(1, len(agg) + 1)

        coin_stats = []
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

    @abstractmethod
    def calculate_total2(
        self,
        coin_ids: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        show_progress: bool = True,
    ) -> Total2Result:
        """
        Calculate the volume-weighted index.

        Subclasses implement their specific price adjustment strategy.

        Args:
            coin_ids: Optional list of coin IDs
            start_date: Optional start date
            end_date: Optional end date
            show_progress: Show progress bar

        Returns:
            Total2Result with index and composition DataFrames
        """
        pass

    def save_results(
        self,
        result: Total2Result,
        index_path: Path | None = None,
        composition_path: Path | None = None,
    ) -> tuple[Path, Path]:
        """
        Save index results to parquet files and statistics to JSON.

        Args:
            result: Total2Result from calculate_total2
            index_path: Path for index file (default: from config)
            composition_path: Path for composition file (default: from config)

        Returns:
            Tuple of (index_path, composition_path)
        """
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
            "coin_statistics": coin_statistics,
            "index_type": result.index_type,
        }
        TOTAL2_MAX_WEIGHT_CHANGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(TOTAL2_MAX_WEIGHT_CHANGE_FILE, "w", encoding="utf-8") as f:
            json.dump(max_weight_info, f, indent=2)

        return index_path, composition_path

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
    VOLUME_SMA_WINDOW,
)
from data.cache import PriceDataCache


class ProcessorError(Exception):
    """Base exception for processor errors."""

    pass


@dataclass
class Total2Result:
    """Result of TOTAL2 calculation."""

    index_df: pd.DataFrame
    composition_df: pd.DataFrame
    coins_processed: int
    date_range: tuple[date, date]
    avg_coins_per_day: float


class Total2Processor:
    """
    Processor for calculating the volume-weighted TOTAL2 market index.

    TOTAL2 is a volume-weighted index of top N altcoins,
    excluding BTC, derivatives, and stablecoins.

    The composition changes daily based on smoothed 24h trading volume rankings.

    Algorithm (vectorized):
    1. Load all price data into DataFrames (coins as columns, dates as rows)
    2. Apply SMA smoothing to volume data (VOLUME_SMA_WINDOW days, default: 28)
    3. Rank coins by smoothed volume for each day
    4. Create mask for top N coins
    5. Calculate: TOTAL2 = Σ(price × smoothed_volume) / Σ(smoothed_volume)

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
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Build aligned price and volume DataFrames for vectorized calculation.

        Creates two DataFrames with:
        - Rows: all dates from earliest to latest across all coins
        - Columns: coin IDs

        Args:
            price_data: Dictionary of price DataFrames per coin

        Returns:
            Tuple of (close_df, volume_df) with aligned indices
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

        return close_df, volume_df

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

        # Build aligned DataFrames
        close_df, volume_df = self.build_aligned_dataframes(price_data)

        if show_progress:
            print(f"Applying {self.volume_sma_window}-day SMA to volume...")

        # Apply SMA to volume
        smoothed_volume_df = volume_df.rolling(
            window=self.volume_sma_window, min_periods=self.volume_sma_window
        ).mean()

        # Set prices to NaN for warmup period per coin
        # For each coin, the first (sma_window - 1) days after their first valid volume
        # should have NaN prices to avoid using incomplete SMA
        warmup_days = self.volume_sma_window - 1
        for coin_id in close_df.columns:
            # Find first valid (non-NaN) value
            first_valid = volume_df[coin_id].first_valid_index()
            if first_valid is not None:
                warmup_end = first_valid + pd.Timedelta(days=warmup_days)
                close_df.loc[:warmup_end, coin_id] = np.nan

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

        return Total2Result(
            index_df=index_df,
            composition_df=composition_df,
            coins_processed=len(price_data),
            date_range=(data_start, data_end),
            avg_coins_per_day=avg_coins,
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
        Save TOTAL2 results to parquet files.

        Args:
            result: Total2Result from calculate_total2
            index_path: Path for index file (default: from config)
            composition_path: Path for composition file (default: from config)

        Returns:
            Tuple of (index_path, composition_path)
        """
        index_path = index_path or TOTAL2_INDEX_FILE
        composition_path = composition_path or TOTAL2_COMPOSITION_FILE

        # Ensure directory exists
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

        # Save index
        result.index_df.to_parquet(index_path)

        # Save composition
        if not result.composition_df.empty:
            result.composition_df.to_parquet(composition_path, index=False)

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

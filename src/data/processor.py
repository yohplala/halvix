"""
Data processor for TOTAL2 index calculation.

Calculates the volume-weighted TOTAL2 index:
- For each day, identifies top N coins by 24h trading volume
- Excludes BTC, derivatives, and stablecoins
- Computes volume-weighted average price in BTC
- Tracks daily composition (which coins were in the index)
"""

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from analysis.filters import TokenFilter
from config import (
    PROCESSED_DIR,
    TOP_N_FOR_TOTAL2,
    TOTAL2_COMPOSITION_FILE,
    TOTAL2_INDEX_FILE,
)
from tqdm import tqdm

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

    The composition changes daily based on 24h trading volume rankings.

    Algorithm:
    - For each day, rank coins by their 24h volume (volumeto in BTC)
    - Take top N coins
    - Calculate: TOTAL2 = Σ(price × volume) / Σ(volume)

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
    ):
        """
        Initialize the TOTAL2 processor.

        Args:
            price_cache: Cache for price data (default: new instance)
            token_filter: Token filter for exclusions (default: new instance)
            top_n: Number of coins to include in TOTAL2 (default: TOP_N_FOR_TOTAL2)
        """
        self.price_cache = price_cache or PriceDataCache()
        self.token_filter = token_filter or TokenFilter()
        self.top_n = top_n

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
            coin_ids = self.price_cache.list_cached_coins()

        data = {}
        iterator = tqdm(coin_ids, desc="Loading price data") if show_progress else coin_ids

        for coin_id in iterator:
            df = self.price_cache.get_prices(coin_id)
            if df is not None and not df.empty:
                data[coin_id] = df

        return data

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
            should_exclude, reason = self.token_filter.should_exclude(
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
        Calculate the volume-weighted TOTAL2 index for all available dates.

        Args:
            coin_ids: Optional list of coin IDs (default: all cached, filtered)
            start_date: Optional start date (default: earliest available)
            end_date: Optional end date (default: latest available)
            show_progress: Show progress bar

        Returns:
            Total2Result with index and composition DataFrames
        """
        # Load all price data
        all_cached = self.price_cache.list_cached_coins()

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

        # Determine date range
        data_start, data_end = self.get_common_date_range(price_data)

        if start_date is not None and start_date > data_start:
            data_start = start_date
        if end_date is not None and end_date < data_end:
            data_end = end_date

        # Generate all dates in range
        date_range = pd.date_range(start=data_start, end=data_end, freq="D")

        # Calculate TOTAL2 for each day
        index_records = []
        composition_records = []

        iterator = tqdm(date_range, desc="Calculating TOTAL2") if show_progress else date_range

        for current_date in iterator:
            result = self._calculate_daily_total2(
                price_data=price_data,
                target_date=current_date,
            )

            if result is not None:
                index_record, comp_records = result
                index_records.append(index_record)
                composition_records.extend(comp_records)

        if not index_records:
            raise ProcessorError("Could not calculate TOTAL2 for any date")

        # Build DataFrames
        index_df = pd.DataFrame(index_records)
        index_df["date"] = pd.to_datetime(index_df["date"])
        index_df = index_df.set_index("date").sort_index()

        composition_df = pd.DataFrame(composition_records)
        if not composition_df.empty:
            composition_df["date"] = pd.to_datetime(composition_df["date"])
            composition_df = composition_df.sort_values(["date", "rank"])

        # Calculate stats
        avg_coins = index_df["coin_count"].mean() if "coin_count" in index_df.columns else 0

        return Total2Result(
            index_df=index_df,
            composition_df=composition_df,
            coins_processed=len(price_data),
            date_range=(data_start, data_end),
            avg_coins_per_day=avg_coins,
        )

    def _calculate_daily_total2(
        self,
        price_data: dict[str, pd.DataFrame],
        target_date: datetime,
    ) -> tuple[dict, list[dict]] | None:
        """
        Calculate volume-weighted TOTAL2 for a single day.

        Uses 24h trading volume (volumeto) for both:
        - Ranking coins (top N by volume)
        - Weighting the average price

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
                price = row["price"] if "price" in row.index else None
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

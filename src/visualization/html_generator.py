"""
HTML page generator for Halvix data status and index pages.

This module provides the HtmlGenerator class for generating:
- Data status page (data_status.html)
- Main index page (index.html)
- TOTAL2 statistics page (total2_statistics.html)
"""

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from config import (
    COINS_TO_DOWNLOAD_JSON,
    DOWNLOAD_FAILED_CSV,
    DOWNLOAD_SKIPPED_CSV,
    FETCH_METADATA_JSON,
    NO_USD_DATA_CSV,
    PRICES_DIR,
    PROJECT_ROOT,
    TOP_N_BY_MARKETCAP_TO_FETCH,
    TOTAL2_MAX_WEIGHT_CHANGE_FILE,
)
from utils.logging import get_logger
from visualization.charts import _get_footer_css, _get_footer_html
from visualization.templates import render_template

logger = get_logger(__name__)


# =============================================================================
# Helper Functions (module-level)
# =============================================================================


def _load_csv_with_schema(
    filepath: Path, schema: list[str], required_fields: int = 5
) -> list[dict]:
    """Load coins from CSV file with given schema.

    Args:
        filepath: Path to CSV file
        schema: List of field names for the dict keys
        required_fields: Minimum number of fields required per line

    Returns:
        List of coin dictionaries
    """
    if not filepath.exists():
        return []
    coins = []
    try:
        with open(filepath, encoding="utf-8") as f:
            lines = f.readlines()
            if len(lines) > 1:
                for line in lines[1:]:  # Skip header
                    parts = line.strip().split(";")
                    if len(parts) >= required_fields:
                        coins.append(
                            {
                                key: parts[i] if i < len(parts) else ""
                                for i, key in enumerate(schema)
                            }
                        )
    except Exception as e:
        logger.warning(f"Failed to load {filepath}: {e}")
    return coins


# Default schema for coin CSV files (skipped, failed, etc.)
_COIN_CSV_SCHEMA = ["id", "name", "symbol", "reason", "url"]


def _load_csv_coins(filepath: Path) -> list[dict]:
    """Load coins from a semicolon-delimited CSV file."""
    return _load_csv_with_schema(filepath, _COIN_CSV_SCHEMA, required_fields=5)


def _get_reason_class(reason: str) -> str:
    """Map a skip/fail reason string to a CSS class for badge styling."""
    if "BTC" in reason or "Bitcoin" in reason:
        return "reason-btc"
    if "Stablecoin" in reason:
        return "reason-stablecoin"
    if "historical" in reason.lower() or "Insufficient" in reason:
        return "reason-history"
    if "CCCAGG" in reason or "pair" in reason.lower() or "market" in reason.lower():
        return "reason-nopair"
    return "reason-wrapped"


def _format_market_cap(market_cap: float, has_usd_data: bool) -> str:
    """Format market cap value for display."""
    if not has_usd_data or market_cap == 0:
        return '<span style="color: var(--text-muted);">N/A</span>'
    if market_cap >= 1_000_000_000:
        return f"${market_cap / 1_000_000_000:.2f}B"
    if market_cap >= 1_000_000:
        return f"${market_cap / 1_000_000:.2f}M"
    return f"${market_cap:,.0f}"


# =============================================================================
# HtmlGenerator Class
# =============================================================================


class HtmlGenerator:
    """Generator for HTML documentation pages.

    This class handles loading data from various sources and generating
    HTML pages for the Halvix site.
    """

    def __init__(self, output_dir: Path | None = None):
        """Initialize the HTML generator.

        Args:
            output_dir: Output directory for generated HTML files.
                       Defaults to PROJECT_ROOT / "site"
        """
        self.output_dir = output_dir or (PROJECT_ROOT / "site")

    # =========================================================================
    # Private Data Loading Methods
    # =========================================================================

    def _load_coins_to_download(self) -> list[dict]:
        """Load coins to download from JSON file."""
        if not COINS_TO_DOWNLOAD_JSON.exists():
            return []
        with open(COINS_TO_DOWNLOAD_JSON, encoding="utf-8") as f:
            return json.load(f)

    def _load_skipped_coins(self) -> list[dict]:
        """Load filtered coins (stablecoins, wrapped, etc.) from CSV file."""
        return _load_csv_coins(DOWNLOAD_SKIPPED_CSV)

    def _load_failed_coins(self) -> list[dict]:
        """Load failed downloads (no BTC pair, etc.) from CSV file."""
        return _load_csv_coins(DOWNLOAD_FAILED_CSV)

    def _load_no_usd_data_coins(self) -> list[dict]:
        """Load coins without USD data from CSV file."""
        if not NO_USD_DATA_CSV.exists():
            return []
        try:
            df = pd.read_csv(NO_USD_DATA_CSV)
            return df.to_dict("records")
        except Exception as e:
            logger.warning(f"Failed to load no-USD data coins from {NO_USD_DATA_CSV}: {e}")
            return []

    def _load_fetch_metadata(self) -> dict:
        """Load fetch metadata (counts, timestamp) from JSON file."""
        if not FETCH_METADATA_JSON.exists():
            return {}
        with open(FETCH_METADATA_JSON, encoding="utf-8") as f:
            return json.load(f)

    def _load_max_weight_change_info(self) -> dict | None:
        """Load max weight change info from JSON file."""
        if not TOTAL2_MAX_WEIGHT_CHANGE_FILE.exists():
            return None
        try:
            with open(TOTAL2_MAX_WEIGHT_CHANGE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(
                f"Failed to load max weight change info from {TOTAL2_MAX_WEIGHT_CHANGE_FILE}: {e}"
            )
            return None

    def _get_all_price_summaries(self) -> dict[str, dict]:
        """
        Get summary of all downloaded price data with quote information.

        Returns:
            Dictionary mapping coin_id to price data summary including quotes available.
            Example: {"eth": {"quotes": ["BTC"], "start_date": "2015-08-07", ...}}
        """
        summaries: dict[str, dict] = {}

        if not PRICES_DIR.exists():
            return summaries

        for parquet_file in sorted(PRICES_DIR.glob("*.parquet")):
            filename = parquet_file.stem

            # Handle pair-based filenames (e.g., eth-btc.parquet)
            if "-" in filename:
                parts = filename.rsplit("-", 1)
                if len(parts) == 2:
                    coin_id, quote = parts
                    quote = quote.upper()
                else:
                    coin_id = filename
                    quote = "BTC"
            else:
                # Legacy format - assume BTC quote
                coin_id = filename
                quote = "BTC"

            try:
                df = pd.read_parquet(parquet_file)
                if not df.empty:
                    start_date = df.index.min()
                    end_date = df.index.max()

                    if coin_id not in summaries:
                        summaries[coin_id] = {
                            "coin_id": coin_id,
                            "quotes": [],
                            "start_date": start_date.strftime("%Y-%m-%d"),
                            "end_date": end_date.strftime("%Y-%m-%d"),
                            "days": len(df),
                        }

                    # Add this quote to the list
                    if quote not in summaries[coin_id]["quotes"]:
                        summaries[coin_id]["quotes"].append(quote)

            except Exception as e:
                logger.warning(f"Failed to load price summary from {parquet_file}: {e}")

        return summaries

    # =========================================================================
    # Private HTML Generation Methods
    # =========================================================================

    def _generate_html(
        self,
        coins_to_download: list[dict],
        skipped_coins: list[dict],
        failed_coins: list[dict],
        no_usd_data_coins: list[dict],
        price_summaries: dict[str, dict],
        fetch_metadata: dict,
    ) -> str:
        """Generate the data status HTML page via Jinja2 template."""
        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

        # Count statistics from metadata
        coins_requested = fetch_metadata.get("coins_requested", TOP_N_BY_MARKETCAP_TO_FETCH)
        coins_with_usd = fetch_metadata.get("coins_fetched", 0)
        coins_no_usd = fetch_metadata.get("coins_no_usd_data", 0)
        coins_no_usd_accepted = fetch_metadata.get("coins_no_usd_accepted", 0)
        total_accepted = fetch_metadata.get("coins_accepted", len(coins_to_download))
        coins_with_data = sum(
            1 for c in coins_to_download if c.get("id", "").lower() in price_summaries
        )

        all_skipped = skipped_coins + failed_coins
        total_skipped = len(all_skipped)
        total_pairs = sum(len(s.get("quotes", [])) for s in price_summaries.values())

        # Sort coins: first by has_usd_data (True first), then by market cap (descending)
        coins_sorted_raw = sorted(
            [c for c in coins_to_download if c.get("id", "").lower() in price_summaries],
            key=lambda c: (not c.get("has_usd_data", True), -c.get("market_cap", 0)),
        )

        # Pre-process coins for template
        coins_sorted = []
        for coin in coins_sorted_raw:
            coin_id = coin.get("id", "").lower()
            price_info = price_summaries.get(coin_id, {})
            has_usd_data = coin.get("has_usd_data", True)
            market_cap = coin.get("market_cap", 0)
            symbol = coin.get("symbol", "N/A")
            quotes = price_info.get("quotes", [])

            coins_sorted.append(
                {
                    "symbol": symbol,
                    "name": coin.get("name", "N/A"),
                    "url": f"https://www.cryptocompare.com/coins/{symbol.upper()}/overview",
                    "source_str": (
                        "USD"
                        if has_usd_data
                        else '<span style="color: var(--accent-orange);">BTC-only</span>'
                    ),
                    "quotes_str": ", ".join(quotes) if quotes else "N/A",
                    "market_cap_str": _format_market_cap(market_cap, has_usd_data),
                    "start_date": price_info.get("start_date", "N/A"),
                    "end_date": price_info.get("end_date", "N/A"),
                    "days": price_info.get("days", 0),
                }
            )

        # Pre-process skipped coins
        skipped_processed = []
        for coin in all_skipped:
            reason = coin.get("reason", "Unknown")
            skipped_processed.append(
                {
                    "symbol": coin.get("symbol", "N/A"),
                    "name": coin.get("name", "N/A"),
                    "url": coin.get("url", "#"),
                    "reason": reason,
                    "reason_class": _get_reason_class(reason),
                }
            )

        return render_template(
            "data_status.html",
            back_link="index.html",
            update_time=update_time,
            coins_requested=coins_requested,
            coins_with_usd=coins_with_usd,
            coins_no_usd=coins_no_usd,
            coins_no_usd_accepted=coins_no_usd_accepted,
            total_accepted=total_accepted,
            coins_with_data=coins_with_data,
            total_skipped=total_skipped,
            total_pairs=total_pairs,
            coins_sorted=coins_sorted,
            all_skipped=skipped_processed,
        )

    def _generate_index_html(self, max_weight_info: dict | None = None) -> str:
        """Generate the main index HTML page via Jinja2 template."""
        footer_css = _get_footer_css()
        footer_html = _get_footer_html()

        volume_outliers = (
            max_weight_info.get("volume_outliers_corrected", []) if max_weight_info else []
        )
        price_events = (
            max_weight_info.get("price_outliers_corrected", []) if max_weight_info else []
        )
        coin_statistics = max_weight_info.get("coin_statistics", []) if max_weight_info else []
        total_corrections = len(volume_outliers) + len(price_events)
        total_coins = len(coin_statistics)

        return render_template(
            "index.html",
            footer_css=footer_css,
            footer_html=footer_html,
            total_coins=total_coins,
            total_corrections=total_corrections,
        )

    def _generate_total2_statistics_html(
        self,
        volume_outliers: list[dict],
        price_events: list[dict],
        coin_statistics: list[dict],
        max_weight_change: float | None = None,
        max_weight_change_coin: str | None = None,
        max_weight_change_date: str | None = None,
        index_type: str = "total2b",
    ) -> str:
        """Generate the TOTAL2 statistics HTML page via Jinja2 template."""
        # Set section content based on index type
        if index_type == "total2b":
            price_section_title = "Price Scaling Events"
            price_section_description = (
                "<strong>TOTAL2b price scaling:</strong> When a coin first enters the index "
                "(after the 21-day freeze period), its price is scaled by "
                "<code>TOTAL2b_d-1 / COIN_PRICE_d</code> to prevent large absolute price offsets. "
                "This preserves day-over-day price change factors while ensuring smooth index entry.<br><br>"
                f"<strong>{len(price_events)} scaling events</strong> were applied."
            )
            price_corrected_header = "Scaled Price (BTC)"
        else:
            price_section_title = "Entry Warmup Capping"
            price_section_description = (
                "<strong>TOTAL2 entry warmup:</strong> When a coin first enters TOTAL2, its price is capped "
                "to max +70% gain or -50% loss per day during a 21-day warmup period, starting from market level "
                "(TOTAL2 value). This prevents artificial spikes from coins with extreme prices.<br>"
                "<strong>TOTAL2 series smoothing:</strong> Extreme day-over-day movements in the aggregate index "
                "are capped at 3x increase or 0.35x decrease.<br><br>"
                f"<strong>{len(price_events)} capping events</strong> were applied."
            )
            price_corrected_header = "Capped Price (BTC)"

        # Build quality analysis box context
        quality_box = None
        if max_weight_change is not None:
            is_warning = abs(max_weight_change) > 0.5
            quality_box = {
                "max_weight_change": max_weight_change,
                "coin": max_weight_change_coin or "N/A",
                "date": max_weight_change_date or "N/A",
                "warning_class": "warning-box" if is_warning else "ok-box",
                "status_class": "text-warning" if is_warning else "text-ok",
                "status_text": (
                    "\u26a0\ufe0f Exceeds 0.5% threshold"
                    if is_warning
                    else "\u2713 Within acceptable range"
                ),
            }

        return render_template(
            "total2_statistics.html",
            back_link="index.html",
            coin_statistics=coin_statistics,
            volume_outliers=volume_outliers,
            price_events=price_events,
            price_section_title=price_section_title,
            price_section_description=price_section_description,
            price_corrected_header=price_corrected_header,
            quality_box=quality_box,
        )

    # =========================================================================
    # Public Page Generation Methods
    # =========================================================================

    def generate_data_status_page(self) -> Path:
        """Generate the data status HTML file (data_status.html).

        Returns:
            Path to the generated data_status.html file
        """
        # Create directory using Pathlib with proper mode
        self.output_dir.mkdir(parents=True, exist_ok=True, mode=0o755)

        coins_to_download = self._load_coins_to_download()
        skipped_coins = self._load_skipped_coins()
        failed_coins = self._load_failed_coins()
        no_usd_data_coins = self._load_no_usd_data_coins()
        price_summaries = self._get_all_price_summaries()
        fetch_metadata = self._load_fetch_metadata()

        html_content = self._generate_html(
            coins_to_download,
            skipped_coins,
            failed_coins,
            no_usd_data_coins,
            price_summaries,
            fetch_metadata,
        )
        output_file = self.output_dir / "data_status.html"

        with open(output_file, "w", encoding="utf-8") as f:
            f.write(html_content)

        logger.info("Data status page generated: %s", output_file)
        return output_file

    def generate_index_page(self) -> Path:
        """Generate the main index.html page.

        Returns:
            Path to the generated index.html file
        """
        self.output_dir.mkdir(parents=True, exist_ok=True, mode=0o755)

        # Load max weight change info if available
        max_weight_info = self._load_max_weight_change_info()

        # Generate main index page
        html_content = self._generate_index_html(max_weight_info)
        output_file = self.output_dir / "index.html"

        with open(output_file, "w", encoding="utf-8") as f:
            f.write(html_content)

        logger.info("Index page generated: %s", output_file)
        return output_file

    def generate_total2_statistics_page(self) -> Path | None:
        """Generate the TOTAL2 statistics page (total2_statistics.html).

        Returns:
            Path to the generated total2_statistics.html file, or None if no data available
        """
        self.output_dir.mkdir(parents=True, exist_ok=True, mode=0o755)

        # Load max weight change info
        max_weight_info = self._load_max_weight_change_info()

        # Extract data for statistics page
        volume_outliers = (
            max_weight_info.get("volume_outliers_corrected", []) if max_weight_info else []
        )
        price_events = (
            max_weight_info.get("price_outliers_corrected", []) if max_weight_info else []
        )
        coin_statistics = max_weight_info.get("coin_statistics", []) if max_weight_info else []

        if not volume_outliers and not price_events and not coin_statistics:
            logger.info("No TOTAL2 statistics data available, skipping page generation")
            return None

        stats_html = self._generate_total2_statistics_html(
            volume_outliers,
            price_events,
            coin_statistics,
            max_weight_change=max_weight_info.get("max_weight_change") if max_weight_info else None,
            max_weight_change_coin=max_weight_info.get("coin") if max_weight_info else None,
            max_weight_change_date=max_weight_info.get("date") if max_weight_info else None,
            index_type=(
                max_weight_info.get("index_type", "total2b") if max_weight_info else "total2b"
            ),
        )
        stats_file = self.output_dir / "total2_statistics.html"

        with open(stats_file, "w", encoding="utf-8") as f:
            f.write(stats_html)

        logger.info("TOTAL2 statistics page generated: %s", stats_file)
        return stats_file

    def generate_all(self) -> dict[str, Path | None]:
        """Generate all HTML pages.

        Returns:
            Dictionary mapping page name to file path (or None if not generated)
        """
        paths: dict[str, Path | None] = {}

        paths["index"] = self.generate_index_page()
        paths["data_status"] = self.generate_data_status_page()
        paths["total2_statistics"] = self.generate_total2_statistics_page()

        return paths

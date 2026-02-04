"""
Halvix - Cryptocurrency Halving Cycle Analysis

Command-line entry point for the analysis pipeline.

Usage:
    python -m main [command] [options]

Commands:
    list-coins        Fetch and filter top N coins by market cap
    fetch-prices      Fetch price data for filtered coins
    calculate-total2  Calculate TOTAL2 market index
    generate-cycle-charts   Generate halving cycle comparison charts
    analyze-patterns  Analyze cycle patterns and generate projections
    status            Show current data status
    clear-cache       Clear cached API data

Examples:
    # Fetch top N coins and filter
    python -m main list-coins

    # Fetch price data (incremental update)
    python -m main fetch-prices

    # Full refresh of price data
    python -m main fetch-prices --full-refresh

    # Calculate TOTAL2 index
    python -m main calculate-total2

    # Show data status
    python -m main status

    # Verbose logging
    python -m main list-coins --verbose
"""

import argparse
import csv
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

from tqdm import tqdm

from api.cryptocompare import CryptoCompareClient
from config import (
    COINS_TO_DOWNLOAD_JSON,
    CRYPTOCOMPARE_COIN_URL,
    DOWNLOAD_FAILED_CSV,
    DOWNLOAD_SKIPPED_CSV,
    FETCH_METADATA_JSON,
    OUTPUT_DIR,
    PROJECT_ROOT,
    TOP_N_BY_MARKETCAP_TO_FETCH,
    TOP_N_BY_VOLUME_FOR_TOTAL2,
    TOTAL2_INDEX_FILE,
)
from data.cache import FileCache, PriceDataCache
from data.fetcher import DataFetcher
from data.processor import get_processor
from utils.logging import get_logger, setup_logging

# Module logger
logger = get_logger(__name__)

# Documentation output directory (separate from docs/ which contains markdown)
DOCS_SITE_DIR = PROJECT_ROOT / "site"


# =============================================================================
# Data File Helpers
# =============================================================================


def _save_fetch_metadata(metadata: dict) -> None:
    """Save fetch metadata to JSON file."""
    FETCH_METADATA_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(FETCH_METADATA_JSON, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)


def _save_failed_coins(failed_coins: list[dict]) -> None:
    """Save failed downloads to CSV file."""
    if not failed_coins:
        # Clear the file if no failed coins
        if DOWNLOAD_FAILED_CSV.exists():
            DOWNLOAD_FAILED_CSV.unlink()
        return

    DOWNLOAD_FAILED_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(DOWNLOAD_FAILED_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["Coin ID", "Name", "Symbol", "Reason", "URL"])
        for coin in failed_coins:
            writer.writerow(
                [
                    coin.get("id", ""),
                    coin.get("name", ""),
                    coin.get("symbol", ""),
                    coin.get("reason", ""),
                    coin.get("url", ""),
                ]
            )


def _append_insufficient_history_to_skipped(
    removed_coins: list[dict],
    price_cache: PriceDataCache,
    min_data_date: date,
) -> None:
    """
    Append coins with insufficient historical data to download_skipped.csv.

    Args:
        removed_coins: List of coin dicts that were removed due to insufficient history
        price_cache: Price data cache to get actual start dates
        min_data_date: The minimum data date requirement
    """
    if not removed_coins:
        return

    # Load existing skipped coins to avoid duplicates
    existing_ids = set()
    if DOWNLOAD_SKIPPED_CSV.exists():
        with open(DOWNLOAD_SKIPPED_CSV, encoding="utf-8") as f:
            lines = f.readlines()
            for line in lines[1:]:  # Skip header
                parts = line.strip().split(";")
                if parts:
                    existing_ids.add(parts[0].lower())

    # Prepare new entries
    new_entries = []
    for coin in removed_coins:
        coin_id = coin.get("id", "")
        if coin_id.lower() in existing_ids:
            continue  # Skip if already in skipped list

        symbol = coin.get("symbol", coin_id.upper())
        name = coin.get("name", symbol)
        url = f"{CRYPTOCOMPARE_COIN_URL}/{symbol.upper()}/overview"

        # Get actual start date for the reason message
        df = price_cache.get_prices(coin_id)
        if df is not None and not df.empty:
            start_date = df.index.min().date()
            reason = f"Insufficient historical data (starts {start_date})"
        else:
            reason = "No price data available"

        new_entries.append([coin_id, name, symbol, reason, url])

    if not new_entries:
        return

    # Append to CSV file
    file_exists = DOWNLOAD_SKIPPED_CSV.exists()
    with open(DOWNLOAD_SKIPPED_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")
        if not file_exists:
            writer.writerow(["Coin ID", "Name", "Symbol", "Reason", "URL"])
        for entry in new_entries:
            writer.writerow(entry)


# =============================================================================
# CLI Command Handlers
# =============================================================================


def cmd_list_coins(args: argparse.Namespace) -> int:
    """Fetch and filter top N coins."""
    logger.info("=" * 60)
    logger.info("HALVIX - Fetching Top Coins")
    logger.info("=" * 60)

    n = args.top
    logger.info("Fetching top %d coins by market cap...", n)

    # Check API connectivity
    client = CryptoCompareClient()

    if not args.skip_ping:
        logger.info("Checking CryptoCompare API connectivity...")
        if not client.ping():
            logger.error("Could not connect to CryptoCompare API")
            return 1
        logger.info("API is reachable")

    # Fetch and filter
    fetcher = DataFetcher(client=client)

    result = fetcher.fetch_and_filter_coins(
        n=n,
        use_cache=not args.no_cache,
        export_skipped=True,
    )

    if not result.success:
        logger.error("Failed: %s", result.message)
        if result.errors:
            for error in result.errors:
                logger.error("  - %s", error)
        return 1

    # Print summary
    logger.info("-" * 60)
    logger.info("RESULTS")
    logger.info("-" * 60)
    logger.info("  Coins requested:  %d", result.coins_requested)
    logger.info("")
    logger.info("  With USD data:    %d", result.coins_fetched)
    logger.info("    - Filtered:     %d", result.coins_filtered)
    usd_accepted = result.coins_fetched - result.coins_filtered
    logger.info("    - Accepted:     %d", usd_accepted)
    logger.info("")
    logger.info("  Without USD data: %d (BTC pairs only)", result.coins_no_usd_data)
    logger.info("    - Filtered:     %d", result.coins_no_usd_filtered)
    if result.coins_no_usd_capped > 0:
        logger.info("    - Capped:       %d (excluded to meet limit)", result.coins_no_usd_capped)
    logger.info("    - Accepted:     %d", result.coins_no_usd_accepted)
    logger.info("")
    logger.info(
        "  Total accepted:   %d coins (capped at %d)", result.coins_accepted, result.coins_requested
    )

    # Print filter breakdown for USD coins
    summary = fetcher.get_filter_summary()
    if summary["by_reason"]:
        logger.info("Filtered by reason (USD coins):")
        for reason, count in sorted(summary["by_reason"].items()):
            logger.info("  - %s: %d", reason, count)

    # Print filter breakdown for BTC-only coins
    no_usd_summary = fetcher.get_no_usd_filter_summary()
    if no_usd_summary["by_reason"]:
        logger.info("Filtered by reason (BTC-only coins):")
        for reason, count in sorted(no_usd_summary["by_reason"].items()):
            logger.info("  - %s: %d", reason, count)

    # Save fetch metadata for the data status page
    _save_fetch_metadata(
        {
            "coins_requested": result.coins_requested,
            "coins_fetched": result.coins_fetched,
            "coins_no_usd_data": result.coins_no_usd_data,
            "coins_no_usd_filtered": result.coins_no_usd_filtered,
            "coins_no_usd_accepted": result.coins_no_usd_accepted,
            "coins_no_usd_capped": result.coins_no_usd_capped,
            "coins_filtered": result.coins_filtered,
            "coins_accepted": result.coins_accepted,
            "timestamp": datetime.now().isoformat(),
        }
    )

    # Clear any previous failed downloads (will be populated during fetch-prices)
    if DOWNLOAD_FAILED_CSV.exists():
        DOWNLOAD_FAILED_CSV.unlink()

    logger.info("Output files:")
    logger.info("  - Coins to download: %s", COINS_TO_DOWNLOAD_JSON)
    logger.info("  - Skipped coins: %s", DOWNLOAD_SKIPPED_CSV)

    logger.info("Successfully processed %d coins", result.coins_accepted)

    # Generate documentation automatically
    logger.info("-" * 60)
    logger.info("Generating documentation...")
    from visualization import HtmlGenerator

    HtmlGenerator().generate_data_status_page()

    logger.info("Run 'python -m main fetch-prices' to fetch price data")

    return 0


def cmd_fetch_prices(args: argparse.Namespace) -> int:
    """Fetch price data for filtered coins using CryptoCompare."""
    from config import QUOTE_CURRENCIES

    logger.info("=" * 60)
    logger.info("HALVIX - Fetching Price Data")
    logger.info("=" * 60)

    fetcher = DataFetcher()

    # Load coins to download
    try:
        coins = fetcher.load_coins_to_download()
    except Exception as e:
        logger.error("Failed to load coins: %s", e)
        logger.info("Run 'python -m main list-coins' first to generate the coin list.")
        return 1

    logger.info("Found %d coins to fetch prices for", len(coins))

    # Determine which pairs to fetch
    # Default: BTC pairs for altcoins, USD for BTC only
    # --all-pairs: fetch all currency pairs
    if args.all_pairs:
        currencies_to_fetch = QUOTE_CURRENCIES
        logger.info("Quote currencies: %s (all pairs)", ", ".join(currencies_to_fetch))
    else:
        currencies_to_fetch = ["BTC"]  # Default: only BTC pairs
        logger.info("Quote currencies: BTC (default, use --all-pairs for BTC and USD)")

    logger.info("Date range: %s to %s", fetcher.history_start_date, fetcher.history_end_date)
    logger.info(
        "  (covers all 4 halving cycles with %s span)",
        fetcher.history_end_date - fetcher.history_start_date,
    )

    # Mode display
    incremental = not args.full_refresh
    if incremental:
        logger.info("Mode: Incremental (fetching only new data since last cache)")
    else:
        logger.info("Mode: Full refresh (fetching complete history)")

    if args.limit:
        coins = coins[: args.limit]
        logger.info("Limiting to first %d coins", args.limit)

    logger.info("Fetching historical price data from CryptoCompare...")

    # Separate BTC from altcoins - BTC needs USD pair, not BTC pair
    btc_coins = [c for c in coins if c.get("id", "").lower() == "btc"]
    altcoins = [c for c in coins if c.get("id", "").lower() != "btc"]

    # Fetch prices for altcoins with selected quote currencies
    results = fetcher.fetch_all_prices(
        coins=altcoins,
        vs_currencies=currencies_to_fetch,
        use_cache=not args.no_cache,
        incremental=incremental,
        show_progress=not args.quiet,
    )

    # Always fetch BTC-USD (BTC/BTC doesn't exist and wouldn't make sense)
    if btc_coins:
        logger.info("Fetching BTC-USD...")
        btc_results = fetcher.fetch_all_prices(
            coins=btc_coins,
            vs_currencies=["USD"],
            use_cache=not args.no_cache,
            incremental=incremental,
            show_progress=False,
        )
        # Merge BTC results into main results for consistent counting
        results.update(btc_results)

    logger.info("-" * 60)
    logger.info("RESULTS")
    logger.info("-" * 60)

    # Count actually fetched (non-empty results)
    # BTC is now fetched with USD pair directly, so no more "skipped" case
    successful_coins = []
    failed_coins = []

    for coin in coins:
        coin_id = coin["id"]
        coin_data = results.get(coin_id, {})
        has_data = any(not df.empty for df in coin_data.values())

        if has_data:
            successful_coins.append(coin_id)
        else:
            # Coin failed to fetch or returned empty data (no pair exists on CryptoCompare)
            failed_coins.append(coin_id)

    logger.info("  Coins processed: %d (attempted: %d)", len(successful_coins), len(coins))

    # Log and save failed coins with explanation of why they failed
    if failed_coins:
        logger.warning("  Failed/empty:    %d coins", len(failed_coins))
        # Check each failed coin to explain why it failed and save to CSV
        # IMPORTANT: Reuse the fetcher's client to maintain rate limit state
        # Creating a new CryptoCompareClient() would reset _last_request_time
        # and potentially hit rate limits after the price fetching phase
        failed_coins_data = []
        rate_limit_hit = False  # Stop making API calls once rate limit is hit
        for coin_id in tqdm(failed_coins, desc="Checking failed coins"):
            # Find the coin data
            coin_data = next((c for c in coins if c.get("id") == coin_id), {})
            coin_symbol = coin_data.get("symbol", coin_id.upper())
            coin_name = coin_data.get("name", coin_symbol)
            url = f"{CRYPTOCOMPARE_COIN_URL}/{coin_symbol.upper()}/overview"

            if rate_limit_hit:
                # Skip API calls once we've hit rate limit, use generic reason
                reason = "Rate limit exceeded - skipped check"
            else:
                # Check histoday availability to get the actual API error message
                pair_info = fetcher.client.check_histoday_availability(coin_symbol, "BTC")
                reason = pair_info["reason"]
                # Detect if we hit rate limit and stop further checks
                if "rate limit" in reason.lower():
                    rate_limit_hit = True
                    logger.warning(
                        "Rate limit hit - skipping remaining %d coin checks",
                        len(failed_coins) - len(failed_coins_data) - 1,
                    )

            failed_coins_data.append(
                {
                    "id": coin_id,
                    "name": coin_name,
                    "symbol": coin_symbol,
                    "reason": reason,
                    "url": url,
                }
            )

        # Log first 10
        for coin in failed_coins_data[:10]:
            logger.warning("    - %s: %s", coin["symbol"], coin["reason"])
        if len(failed_coins_data) > 10:
            logger.warning("    ... and %d more", len(failed_coins_data) - 10)

        # Save to CSV for the data status page
        _save_failed_coins(failed_coins_data)
    else:
        # Clear any previous failed coins
        _save_failed_coins([])

    # Show cache stats per currency
    price_cache = PriceDataCache()
    for currency in currencies_to_fetch:
        cached_coins = price_cache.list_cached_coins(currency)
        logger.info(
            "  Cached (%s):    %d coins (altcoin/%s pairs)", currency, len(cached_coins), currency
        )

    # Also show BTC-USD if we're in default mode (BTC pairs only)
    total_cached = 0
    if not args.all_pairs:
        btc_cached = price_cache.list_cached_coins("BTC")
        btc_usd_cached = price_cache.has_prices("btc", "USD")
        total_cached = len(btc_cached) + (1 if btc_usd_cached else 0)
        if btc_usd_cached:
            logger.info("  Cached (USD):    1 coin (BTC/USD)")
        logger.info("  Total cached:    %d coins", total_cached)

    logger.info("Price data saved to: %s", fetcher.price_cache.prices_dir)

    # Migrate legacy files to pair format if needed
    migrated = fetcher.price_cache.migrate_to_pair_format()
    if migrated > 0:
        logger.info("Migrated %d legacy files to pair format", migrated)

    # Generate documentation automatically
    logger.info("-" * 60)
    logger.info("Generating documentation...")
    from visualization import HtmlGenerator

    HtmlGenerator().generate_data_status_page()

    return 0


def cmd_calculate_total2(args: argparse.Namespace) -> int:
    """Calculate volume-weighted TOTAL2/TOTAL2b market index."""
    from config import DEFAULT_QUOTE_CURRENCY, VOLUME_SMA_WINDOW

    # Determine index type (default: total2b)
    index_type = getattr(args, "index_type", "total2b")

    logger.info("=" * 60)
    logger.info("HALVIX - Calculate %s Index (Volume-Weighted)", index_type.upper())
    logger.info("=" * 60)

    # Use config defaults if not provided via command line
    quote_currency = args.quote_currency if args.quote_currency else DEFAULT_QUOTE_CURRENCY
    volume_sma = args.volume_sma if args.volume_sma else VOLUME_SMA_WINDOW

    processor = get_processor(
        index_type=index_type,
        top_n=args.top_n,
        volume_sma_window=volume_sma,
        quote_currency=quote_currency,
    )

    # Check for price data
    cached_coins = processor.price_cache.list_cached_coins(quote_currency)
    if not cached_coins:
        logger.error("No cached price data found for %s.", quote_currency)
        logger.info("Run 'python -m main fetch-prices' first.")
        return 1

    logger.info("Found %d coins with cached price data (%s)", len(cached_coins), quote_currency)
    logger.info("Using top %d coins by smoothed volume for TOTAL2 calculation", args.top_n)
    logger.info("Volume smoothing: %d-day SMA", volume_sma)

    try:
        result = processor.calculate_total2(show_progress=not args.quiet)

        logger.info("-" * 60)
        logger.info("RESULTS")
        logger.info("-" * 60)
        logger.info("  Coins processed:     %d", result.coins_processed)
        logger.info("  Date range:          %s to %s", result.date_range[0], result.date_range[1])
        logger.info("  Total days:          %d", len(result.index_df))
        logger.info("  Avg coins per day:   %.1f", result.avg_coins_per_day)

        # Show sample of index
        if not result.index_df.empty:
            logger.info("Latest TOTAL2 values:")
            latest = result.index_df.tail(5)
            for idx, row in latest.iterrows():
                logger.info(
                    "  %s: %.8f BTC (%d coins)", idx.date(), row["total2_price"], row["coin_count"]
                )

        # Show max weight change (important for detecting sudden composition changes)
        # Only tracked after 2017-11-01 when TOTAL2 has 50 coins
        logger.info("-" * 60)
        logger.info("WEIGHT CHANGE ANALYSIS (after 2017-11-01)")
        logger.info("-" * 60)
        logger.info("  Purpose: Ensure curve variations reflect price, not weight changes")
        if result.max_weight_change is not None:
            logger.info(
                "  Max daily weight change: %.4f%% for %s on %s",
                result.max_weight_change,
                result.max_weight_change_coin.upper() if result.max_weight_change_coin else "N/A",
                result.max_weight_change_date,
            )
            if abs(result.max_weight_change) > 0.5:
                logger.warning(
                    "  ⚠️  Weight change exceeds 0.5%% threshold - consider increasing VOLUME_SMA_WINDOW"
                )
            else:
                logger.info("  ✓ Weight change within acceptable range (< 0.5%%)")
        else:
            logger.info("  No weight change data available (not enough data after 2017-11-01)")

        # Save results
        if not args.dry_run:
            index_path, comp_path = processor.save_results(result)
            logger.info("Output files:")
            logger.info("  - TOTAL2 index:       %s", index_path)
            logger.info("  - Daily composition:  %s", comp_path)
            logger.info("TOTAL2 calculation complete")
        else:
            logger.info("[Dry run - results not saved]")

        return 0

    except Exception as e:
        logger.exception("Failed to calculate TOTAL2: %s", e)
        return 1


def cmd_generate_cycle_charts(args: argparse.Namespace) -> int:
    """Generate halving cycle comparison charts (BTC and TOTAL2)."""
    from visualization import generate_all_cycle_charts

    logger.info("=" * 60)
    logger.info("HALVIX - Generate Charts")
    logger.info("=" * 60)

    # Default to site/charts for GitHub Pages deployment
    site_charts_dir = DOCS_SITE_DIR / "charts"
    output_dir = args.output_dir if args.output_dir else site_charts_dir

    try:
        logger.info("Generating charts in: %s", output_dir)
        paths = generate_all_cycle_charts(output_dir)

        logger.info("-" * 60)
        logger.info("CHARTS GENERATED")
        logger.info("-" * 60)
        for name, path in paths.items():
            logger.info("  %s: %s", name, path)

        # Generate the main index.html page
        logger.info("Generating main index page...")
        from visualization import HtmlGenerator

        HtmlGenerator().generate_all()

        return 0

    except FileNotFoundError as e:
        logger.error("Missing data: %s", e)
        logger.info("Run 'calculate-total2' and 'fetch-prices' first.")
        return 1
    except Exception as e:
        logger.exception("Failed to generate charts: %s", e)
        return 1


def cmd_analyze_patterns(args: argparse.Namespace) -> int:
    """Analyze cycle patterns and generate target projections."""
    from visualization.pattern_charts import generate_all_pattern_charts

    logger.info("=" * 60)
    logger.info("HALVIX - Cycle Pattern Analysis")
    logger.info("=" * 60)

    output_dir = args.output_dir if args.output_dir else DOCS_SITE_DIR
    top_n = args.top_n

    logger.info("Output directory: %s", output_dir)
    logger.info("Top N altcoins: %d", top_n)

    try:
        logger.info("Analyzing cycle patterns...")
        paths = generate_all_pattern_charts(
            output_dir=output_dir,
            top_n=top_n,
            show_progress=not args.quiet,
        )

        logger.info("-" * 60)
        logger.info("PATTERN ANALYSIS COMPLETE")
        logger.info("-" * 60)
        logger.info("Generated files:")
        for name, path in paths.items():
            logger.info("  %s: %s", name, path)

        # Update main index page to include pattern analysis link
        logger.info("Updating main index page...")
        from visualization import HtmlGenerator

        HtmlGenerator().generate_all()

        return 0

    except FileNotFoundError as e:
        logger.error("Missing data: %s", e)
        logger.info("Run 'fetch-prices' first to download price data.")
        return 1
    except Exception as e:
        logger.exception("Failed to analyze patterns: %s", e)
        return 1


def cmd_status(args: argparse.Namespace) -> int:
    """Show current data status."""
    logger.info("=" * 60)
    logger.info("HALVIX - Data Status")
    logger.info("=" * 60)

    # Check coins to download
    if COINS_TO_DOWNLOAD_JSON.exists():
        with open(COINS_TO_DOWNLOAD_JSON) as f:
            coins = json.load(f)
        logger.info("Coins to download: %d", len(coins))
    else:
        logger.info("Coins to download: Not yet generated")
        logger.info("  Run 'python -m main list-coins' to generate")

    # Check skipped coins CSV
    if DOWNLOAD_SKIPPED_CSV.exists():
        logger.info("Skipped coins CSV: %s", DOWNLOAD_SKIPPED_CSV)

    # Check price cache
    price_cache = PriceDataCache()
    cached_coins = price_cache.list_cached_coins()
    logger.info("Cached price data: %d coins", len(cached_coins))

    if cached_coins:
        logger.debug("Cached coins:")
        for coin_id in cached_coins[:20]:
            df = price_cache.get_prices(coin_id)
            if df is not None:
                date_range = f"{df.index.min().date()} to {df.index.max().date()}"
                logger.debug("  - %s: %d days (%s)", coin_id, len(df), date_range)
        if len(cached_coins) > 20:
            logger.debug("  ... and %d more", len(cached_coins) - 20)

    # Check TOTAL2 index
    if TOTAL2_INDEX_FILE.exists():
        import pandas as pd

        total2_df = pd.read_parquet(TOTAL2_INDEX_FILE)
        date_range = f"{total2_df.index.min().date()} to {total2_df.index.max().date()}"
        logger.info("TOTAL2 index: %d days (%s)", len(total2_df), date_range)

        logger.debug("Latest values:")
        for idx, row in total2_df.tail(3).iterrows():
            logger.debug("  %s: %.8f BTC", idx.date(), row["total2_price"])
    else:
        logger.info("TOTAL2 index: Not calculated yet")
        logger.info("  Run 'python -m main calculate-total2' to generate")

    # Check cache directory
    cache = FileCache()
    cache_files = list(cache.cache_dir.glob("*"))
    logger.info("API cache files: %d", len(cache_files))

    return 0


def cmd_clear_cache(args: argparse.Namespace) -> int:
    """Clear cached data."""
    logger.info("=" * 60)
    logger.info("HALVIX - Clear Cache")
    logger.info("=" * 60)

    cleared_any = False

    if args.prices:
        price_cache = PriceDataCache()
        count = price_cache.clear()
        logger.info("Cleared %d price data files", count)
        cleared_any = True

    if args.api:
        cache = FileCache()
        count = cache.clear()
        logger.info("Cleared %d API cache files", count)
        cleared_any = True

    if not cleared_any:
        logger.info("Specify one or more cache types to clear:")
        logger.info("  --prices   Clear price data cache")
        logger.info("  --api      Clear API response cache")
        return 1

    logger.info("Cache cleared")
    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="halvix",
        description="Cryptocurrency price analysis relative to Bitcoin halving cycles",
    )

    # Global arguments
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging (DEBUG level)",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress progress bars",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        help="Log to file (in addition to console)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # list-coins command
    list_parser = subparsers.add_parser(
        "list-coins",
        help="Fetch and filter top N coins by market cap",
    )
    list_parser.add_argument(
        "--top",
        "-n",
        type=int,
        default=TOP_N_BY_MARKETCAP_TO_FETCH,
        help=f"Number of top coins to fetch (default: {TOP_N_BY_MARKETCAP_TO_FETCH})",
    )
    list_parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Force fresh API fetch, ignore cache",
    )
    list_parser.add_argument(
        "--skip-ping",
        action="store_true",
        help="Skip API connectivity check",
    )

    # fetch-prices command
    fetch_parser = subparsers.add_parser(
        "fetch-prices",
        help="Fetch price data for filtered coins",
    )
    fetch_parser.add_argument(
        "--limit",
        "-l",
        type=int,
        help="Limit to first N coins (for testing)",
    )
    fetch_parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Force fresh API fetch, ignore cache",
    )
    fetch_parser.add_argument(
        "--full-refresh",
        action="store_true",
        help="Fetch complete history instead of incremental update",
    )
    fetch_parser.add_argument(
        "--all-pairs",
        action="store_true",
        help="Fetch all currency pairs (BTC and USD). Default: only BTC pairs for altcoins, USD for BTC",
    )

    # calculate-total2 command
    total2_parser = subparsers.add_parser(
        "calculate-total2",
        help="Calculate TOTAL2/TOTAL2b market index from cached price data",
    )
    total2_parser.add_argument(
        "--index-type",
        "-t",
        type=str,
        choices=["total2", "total2b"],
        default="total2b",
        help="Index type: 'total2' (legacy with price capping) or 'total2b' (new with freeze period and scaling). Default: total2b",
    )
    total2_parser.add_argument(
        "--top-n",
        "-n",
        type=int,
        default=TOP_N_BY_VOLUME_FOR_TOTAL2,
        help=f"Number of coins in TOTAL2 (default: {TOP_N_BY_VOLUME_FOR_TOTAL2})",
    )
    total2_parser.add_argument(
        "--volume-sma",
        type=int,
        default=None,
        help="Volume SMA window in days (default: from config)",
    )
    total2_parser.add_argument(
        "--quote-currency",
        type=str,
        default=None,
        help="Quote currency for prices (default: from config)",
    )
    total2_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Calculate but don't save results",
    )

    # generate-cycle-charts command
    charts_parser = subparsers.add_parser(
        "generate-cycle-charts",
        help="Generate halving cycle comparison charts (BTC and TOTAL2)",
    )
    charts_parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for charts (default: site/charts)",
    )

    # analyze-patterns command
    patterns_parser = subparsers.add_parser(
        "analyze-patterns",
        help="Analyze cycle patterns and generate price target projections",
    )
    patterns_parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for pattern charts (default: site/)",
    )
    patterns_parser.add_argument(
        "--top-n",
        "-n",
        type=int,
        default=9,
        help="Number of top altcoins to include (default: 9)",
    )

    # status command
    subparsers.add_parser(
        "status",
        help="Show current data status",
    )

    # clear-cache command
    clear_parser = subparsers.add_parser(
        "clear-cache",
        help="Clear cached data",
    )
    clear_parser.add_argument(
        "--prices",
        action="store_true",
        help="Clear price data cache",
    )
    clear_parser.add_argument(
        "--api",
        action="store_true",
        help="Clear API response cache",
    )

    args = parser.parse_args()

    # Setup logging based on global args
    log_level = logging.DEBUG if args.verbose else logging.INFO
    log_file = args.log_file or (OUTPUT_DIR / "halvix.log" if args.verbose else None)
    setup_logging(level=log_level, log_file=log_file, verbose=args.verbose)

    if args.command is None:
        parser.print_help()
        return 0

    # Ensure quiet is available for all commands
    if not hasattr(args, "quiet"):
        args.quiet = False

    # Route to command handler
    commands = {
        "list-coins": cmd_list_coins,
        "fetch-prices": cmd_fetch_prices,
        "calculate-total2": cmd_calculate_total2,
        "generate-cycle-charts": cmd_generate_cycle_charts,
        "analyze-patterns": cmd_analyze_patterns,
        "status": cmd_status,
        "clear-cache": cmd_clear_cache,
    }

    handler = commands.get(args.command)
    if handler:
        try:
            return handler(args)
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
            return 130
        except Exception as e:
            logger.exception("Unexpected error: %s", e)
            return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

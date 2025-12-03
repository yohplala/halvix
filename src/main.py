"""
Halvix - Cryptocurrency Halving Cycle Analysis

Command-line entry point for the analysis pipeline.

Usage:
    python -m main [command] [options]

Commands:
    list-coins        Fetch and filter top N coins by market cap
    fetch-prices      Fetch price data for filtered coins
    calculate-total2  Calculate TOTAL2 market index
    status            Show current data status
    clear-cache       Clear cached API data

Examples:
    # Fetch top 300 coins and filter
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
import json
import logging
import sys
from pathlib import Path

from api.cryptocompare import CryptoCompareClient
from config import (
    ACCEPTED_COINS_JSON,
    OUTPUT_DIR,
    REJECTED_COINS_CSV,
    TOP_N_COINS,
    TOP_N_FOR_TOTAL2,
    TOTAL2_INDEX_FILE,
)
from utils.logging import get_logger, setup_logging

from data.cache import FileCache, PriceDataCache
from data.fetcher import DataFetcher
from data.processor import Total2Processor

# Module logger
logger = get_logger(__name__)


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
        for_total2=args.for_total2,
        use_cache=not args.no_cache,
        export_filtered=True,
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
    logger.info("  Coins fetched:    %d", result.coins_fetched)
    logger.info("  Coins filtered:   %d", result.coins_filtered)
    logger.info("  Coins accepted:   %d", result.coins_accepted)

    # Print filter breakdown
    summary = fetcher.get_filter_summary()
    if summary["by_reason"]:
        logger.info("Filtered by reason:")
        for reason, count in sorted(summary["by_reason"].items()):
            logger.info("  - %s: %d", reason, count)

    logger.info("Output files:")
    logger.info("  - Accepted coins: %s", ACCEPTED_COINS_JSON)
    logger.info("  - Rejected coins: %s", REJECTED_COINS_CSV)

    logger.info("Successfully processed %d coins", result.coins_accepted)
    logger.info("Run 'python -m main fetch-prices' to fetch price data")

    return 0


def cmd_fetch_prices(args: argparse.Namespace) -> int:
    """Fetch price data for filtered coins using CryptoCompare."""
    logger.info("=" * 60)
    logger.info("HALVIX - Fetching Price Data")
    logger.info("=" * 60)

    fetcher = DataFetcher()

    # Load accepted coins
    try:
        coins = fetcher.load_accepted_coins()
    except Exception as e:
        logger.error("Failed to load coins: %s", e)
        logger.info("Run 'python -m main list-coins' first to generate the coin list.")
        return 1

    logger.info("Found %d coins to fetch prices for", len(coins))
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

    results = fetcher.fetch_all_prices(
        coins=coins,
        use_cache=not args.no_cache,
        incremental=incremental,
        show_progress=not args.quiet,
    )

    logger.info("-" * 60)
    logger.info("RESULTS")
    logger.info("-" * 60)
    logger.info("  Prices fetched: %d coins", len(results))

    # Show cache stats
    price_cache = PriceDataCache()
    cached_coins = price_cache.list_cached_coins()
    logger.info("  Total cached:   %d coins", len(cached_coins))

    logger.info("Price data saved to: %s", fetcher.price_cache.prices_dir)

    return 0


def cmd_calculate_total2(args: argparse.Namespace) -> int:
    """Calculate volume-weighted TOTAL2 market index."""
    logger.info("=" * 60)
    logger.info("HALVIX - Calculate TOTAL2 Index (Volume-Weighted)")
    logger.info("=" * 60)

    processor = Total2Processor(top_n=args.top_n)

    # Check for price data
    cached_coins = processor.price_cache.list_cached_coins()
    if not cached_coins:
        logger.error("No cached price data found.")
        logger.info("Run 'python -m main fetch-prices' first.")
        return 1

    logger.info("Found %d coins with cached price data", len(cached_coins))
    logger.info("Using top %d coins by volume for TOTAL2 calculation", args.top_n)

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


def cmd_status(args: argparse.Namespace) -> int:
    """Show current data status."""
    logger.info("=" * 60)
    logger.info("HALVIX - Data Status")
    logger.info("=" * 60)

    # Check accepted coins
    if ACCEPTED_COINS_JSON.exists():
        with open(ACCEPTED_COINS_JSON) as f:
            coins = json.load(f)
        logger.info("Accepted coins: %d", len(coins))
    else:
        logger.info("Accepted coins: Not yet generated")
        logger.info("  Run 'python -m main list-coins' to generate")

    # Check rejected coins CSV
    if REJECTED_COINS_CSV.exists():
        logger.info("Rejected coins CSV: %s", REJECTED_COINS_CSV)

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
        default=TOP_N_COINS,
        help=f"Number of top coins to fetch (default: {TOP_N_COINS})",
    )
    list_parser.add_argument(
        "--for-total2",
        action="store_true",
        help="Also exclude stablecoins (for TOTAL2 calculation)",
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

    # calculate-total2 command
    total2_parser = subparsers.add_parser(
        "calculate-total2",
        help="Calculate TOTAL2 market index from cached price data",
    )
    total2_parser.add_argument(
        "--top-n",
        "-n",
        type=int,
        default=TOP_N_FOR_TOTAL2,
        help=f"Number of coins in TOTAL2 (default: {TOP_N_FOR_TOTAL2})",
    )
    total2_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Calculate but don't save results",
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

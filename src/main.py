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
from datetime import datetime
from pathlib import Path

import pandas as pd
from api.cryptocompare import CryptoCompareClient
from config import (
    ACCEPTED_COINS_JSON,
    OUTPUT_DIR,
    PRICES_DIR,
    PROJECT_ROOT,
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

# Documentation output directory (separate from docs/ which contains markdown)
DOCS_SITE_DIR = PROJECT_ROOT / "site"


# =============================================================================
# Documentation Generator
# =============================================================================


def _load_accepted_coins() -> list[dict]:
    """Load accepted coins from JSON file."""
    if not ACCEPTED_COINS_JSON.exists():
        return []
    with open(ACCEPTED_COINS_JSON, encoding="utf-8") as f:
        return json.load(f)


def _load_rejected_coins() -> list[dict]:
    """Load rejected coins from CSV file."""
    if not REJECTED_COINS_CSV.exists():
        return []

    rejected = []
    with open(REJECTED_COINS_CSV, encoding="utf-8") as f:
        lines = f.readlines()
        if len(lines) > 1:
            for line in lines[1:]:  # Skip header
                parts = line.strip().split(";")
                if len(parts) >= 5:
                    rejected.append(
                        {
                            "id": parts[0],
                            "name": parts[1],
                            "symbol": parts[2],
                            "reason": parts[3],
                            "url": parts[4],
                        }
                    )
    return rejected


def _get_price_data_summary() -> list[dict]:
    """Get summary of price data for each coin."""
    summaries = []

    if not PRICES_DIR.exists():
        return summaries

    for parquet_file in sorted(PRICES_DIR.glob("*.parquet")):
        coin_id = parquet_file.stem
        try:
            df = pd.read_parquet(parquet_file)
            if not df.empty:
                start_date = df.index.min()
                end_date = df.index.max()
                summaries.append(
                    {
                        "coin_id": coin_id,
                        "start_date": start_date.strftime("%Y-%m-%d"),
                        "end_date": end_date.strftime("%Y-%m-%d"),
                        "days": len(df),
                    }
                )
        except Exception:
            pass

    return summaries


def _generate_html(
    accepted_coins: list[dict],
    rejected_coins: list[dict],
    price_summaries: list[dict],
) -> str:
    """Generate the complete HTML documentation page."""
    update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Halvix - Cryptocurrency Data Status</title>
    <style>
        :root {{
            --bg-primary: #0d1117;
            --bg-secondary: #161b22;
            --bg-tertiary: #21262d;
            --text-primary: #c9d1d9;
            --text-secondary: #8b949e;
            --text-muted: #6e7681;
            --accent-orange: #f0883e;
            --accent-green: #3fb950;
            --accent-red: #f85149;
            --accent-blue: #58a6ff;
            --border-color: #30363d;
        }}

        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.6;
            min-height: 100vh;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
            padding: 2rem;
        }}

        header {{
            text-align: center;
            padding: 3rem 0;
            border-bottom: 1px solid var(--border-color);
            margin-bottom: 2rem;
        }}

        h1 {{
            font-size: 2.5rem;
            color: var(--accent-orange);
            margin-bottom: 0.5rem;
            font-weight: 600;
        }}

        .subtitle {{
            color: var(--text-secondary);
            font-size: 1.1rem;
        }}

        .update-time {{
            color: var(--text-muted);
            font-size: 0.9rem;
            margin-top: 1rem;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1.5rem;
            margin-bottom: 3rem;
        }}

        .stat-card {{
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 1.5rem;
            text-align: center;
        }}

        .stat-value {{
            font-size: 2.5rem;
            font-weight: 700;
            color: var(--accent-blue);
        }}

        .stat-value.green {{ color: var(--accent-green); }}
        .stat-value.red {{ color: var(--accent-red); }}
        .stat-value.orange {{ color: var(--accent-orange); }}

        .stat-label {{
            color: var(--text-secondary);
            font-size: 0.9rem;
            margin-top: 0.5rem;
        }}

        section {{
            margin-bottom: 3rem;
        }}

        h2 {{
            color: var(--text-primary);
            font-size: 1.5rem;
            margin-bottom: 1rem;
            padding-bottom: 0.5rem;
            border-bottom: 2px solid var(--accent-orange);
            display: inline-block;
        }}

        .section-description {{
            color: var(--text-secondary);
            margin-bottom: 1.5rem;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            background: var(--bg-secondary);
            border-radius: 8px;
            overflow: hidden;
        }}

        th, td {{
            padding: 0.75rem 1rem;
            text-align: left;
            border-bottom: 1px solid var(--border-color);
        }}

        th {{
            background: var(--bg-tertiary);
            color: var(--text-primary);
            font-weight: 600;
            text-transform: uppercase;
            font-size: 0.8rem;
            letter-spacing: 0.5px;
        }}

        tr:hover {{
            background: var(--bg-tertiary);
        }}

        .coin-symbol {{
            font-weight: 600;
            color: var(--accent-orange);
        }}

        .coin-name {{
            color: var(--text-primary);
        }}

        .coin-id {{
            color: var(--text-muted);
            font-size: 0.85rem;
        }}

        .market-cap {{
            color: var(--accent-green);
            font-family: 'Monaco', 'Menlo', monospace;
        }}

        .reason-badge {{
            display: inline-block;
            padding: 0.25rem 0.75rem;
            border-radius: 12px;
            font-size: 0.8rem;
            font-weight: 500;
        }}

        .reason-wrapped {{ background: #3f2d1e; color: #f0883e; }}
        .reason-btc {{ background: #2d1e3f; color: #a371f7; }}
        .reason-stablecoin {{ background: #1e2d3f; color: #58a6ff; }}

        .date-range {{
            font-family: 'Monaco', 'Menlo', monospace;
            font-size: 0.85rem;
            color: var(--text-secondary);
        }}

        .days-count {{
            color: var(--accent-green);
            font-weight: 600;
        }}

        a {{
            color: var(--accent-blue);
            text-decoration: none;
        }}

        a:hover {{
            text-decoration: underline;
        }}

        .table-container {{
            overflow-x: auto;
            border-radius: 8px;
            border: 1px solid var(--border-color);
        }}

        footer {{
            text-align: center;
            padding: 2rem;
            color: var(--text-muted);
            border-top: 1px solid var(--border-color);
            margin-top: 3rem;
        }}

        .github-link {{
            color: var(--accent-orange);
        }}

        @media (max-width: 768px) {{
            .container {{
                padding: 1rem;
            }}

            h1 {{
                font-size: 1.8rem;
            }}

            .stat-value {{
                font-size: 2rem;
            }}

            th, td {{
                padding: 0.5rem;
                font-size: 0.85rem;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🔶 Halvix Data Status</h1>
            <p class="subtitle">Cryptocurrency Price Analysis Relative to Bitcoin Halving Cycles</p>
            <p class="update-time">Last updated: {update_time}</p>
        </header>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value">{len(accepted_coins) + len(rejected_coins)}</div>
                <div class="stat-label">Total Coins Fetched</div>
            </div>
            <div class="stat-card">
                <div class="stat-value green">{len(accepted_coins)}</div>
                <div class="stat-label">Accepted Coins</div>
            </div>
            <div class="stat-card">
                <div class="stat-value red">{len(rejected_coins)}</div>
                <div class="stat-label">Filtered Out</div>
            </div>
            <div class="stat-card">
                <div class="stat-value orange">{len(price_summaries)}</div>
                <div class="stat-label">Coins with Price Data</div>
            </div>
        </div>

        <section id="accepted">
            <h2>✅ Accepted Coins ({len(accepted_coins)})</h2>
            <p class="section-description">
                These coins passed the filter and are included in the analysis.
                Wrapped, staked, bridged tokens, stablecoins and Bitcoin are excluded.
                Click coin name to view on CryptoCompare.
            </p>
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Symbol</th>
                            <th>Name</th>
                            <th>Market Cap</th>
                        </tr>
                    </thead>
                    <tbody>
"""

    for i, coin in enumerate(accepted_coins, 1):
        market_cap = coin.get("market_cap", 0)
        if market_cap >= 1_000_000_000:
            market_cap_str = f"${market_cap / 1_000_000_000:.2f}B"
        elif market_cap >= 1_000_000:
            market_cap_str = f"${market_cap / 1_000_000:.2f}M"
        else:
            market_cap_str = f"${market_cap:,.0f}"

        symbol = coin.get("symbol", "N/A")
        name = coin.get("name", "N/A")
        coin_url = f"https://www.cryptocompare.com/coins/{symbol.upper()}/overview"

        html += f"""                        <tr>
                            <td>{i}</td>
                            <td class="coin-symbol">{symbol}</td>
                            <td class="coin-name"><a href="{coin_url}" target="_blank">{name}</a></td>
                            <td class="market-cap">{market_cap_str}</td>
                        </tr>
"""

    html += (
        """                    </tbody>
                </table>
            </div>
        </section>

        <section id="rejected">
            <h2>❌ Filtered Out Coins ("""
        + str(len(rejected_coins))
        + """)</h2>
            <p class="section-description">
                These coins were excluded from analysis. Click the coin name to view on CryptoCompare.
            </p>
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>Symbol</th>
                            <th>Name</th>
                            <th>Reason</th>
                        </tr>
                    </thead>
                    <tbody>
"""
    )

    for coin in rejected_coins:
        reason = coin.get("reason", "Unknown")
        reason_class = "reason-wrapped"
        if "BTC" in reason or "Bitcoin" in reason:
            reason_class = "reason-btc"
        elif "Stablecoin" in reason:
            reason_class = "reason-stablecoin"

        html += f"""                        <tr>
                            <td class="coin-symbol">{coin.get('symbol', 'N/A')}</td>
                            <td class="coin-name"><a href="{coin.get('url', '#')}" target="_blank">{coin.get('name', 'N/A')}</a></td>
                            <td><span class="reason-badge {reason_class}">{reason}</span></td>
                        </tr>
"""

    html += """                    </tbody>
                </table>
            </div>
        </section>
"""

    if price_summaries:
        html += (
            """
        <section id="price-data">
            <h2>📊 Price Data Summary ("""
            + str(len(price_summaries))
            + """ coins)</h2>
            <p class="section-description">
                Historical daily price data stored in Parquet format.
                Data spans from before the first Bitcoin halving to the present.
            </p>
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>Coin ID</th>
                            <th>Start Date</th>
                            <th>End Date</th>
                            <th>Days</th>
                        </tr>
                    </thead>
                    <tbody>
"""
        )

        for summary in price_summaries:
            html += f"""                        <tr>
                            <td class="coin-symbol">{summary['coin_id'].upper()}</td>
                            <td class="date-range">{summary['start_date']}</td>
                            <td class="date-range">{summary['end_date']}</td>
                            <td class="days-count">{summary['days']:,}</td>
                        </tr>
"""

        html += """                    </tbody>
                </table>
            </div>
        </section>
"""

    html += """
        <footer>
            <p>
                <a href="https://github.com/yohplala/halvix" class="github-link">Halvix</a> -
                Cryptocurrency price analysis relative to Bitcoin halving cycles.
            </p>
            <p style="margin-top: 0.5rem;">
                Data source: <a href="https://www.cryptocompare.com/" target="_blank">CryptoCompare</a>
            </p>
        </footer>
    </div>
</body>
</html>
"""

    return html


def generate_docs() -> Path:
    """Generate the documentation HTML file."""
    # Create directory using Pathlib with proper mode
    DOCS_SITE_DIR.mkdir(parents=True, exist_ok=True, mode=0o755)

    accepted_coins = _load_accepted_coins()
    rejected_coins = _load_rejected_coins()
    price_summaries = _get_price_data_summary()

    html_content = _generate_html(accepted_coins, rejected_coins, price_summaries)
    output_file = DOCS_SITE_DIR / "index.html"

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html_content)

    logger.info("Documentation generated: %s", output_file)
    return output_file


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

    # Generate documentation automatically
    logger.info("-" * 60)
    logger.info("Generating documentation...")
    generate_docs()

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

    # Generate documentation automatically
    logger.info("-" * 60)
    logger.info("Generating documentation...")
    generate_docs()

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

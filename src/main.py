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

import pandas as pd

from api.cryptocompare import CryptoCompareClient
from config import (
    COINS_TO_DOWNLOAD_JSON,
    CRYPTOCOMPARE_COIN_URL,
    DOWNLOAD_SKIPPED_CSV,
    MIN_DATA_DATE,
    OUTPUT_DIR,
    PRICES_DIR,
    PROJECT_ROOT,
    TOP_N_COINS,
    TOP_N_FOR_TOTAL2,
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
# Documentation Generator
# =============================================================================


def _load_coins_to_download() -> list[dict]:
    """Load coins to download from JSON file."""
    if not COINS_TO_DOWNLOAD_JSON.exists():
        return []
    with open(COINS_TO_DOWNLOAD_JSON, encoding="utf-8") as f:
        return json.load(f)


def _load_skipped_coins() -> list[dict]:
    """Load skipped coins from CSV file."""
    if not DOWNLOAD_SKIPPED_CSV.exists():
        return []

    skipped = []
    with open(DOWNLOAD_SKIPPED_CSV, encoding="utf-8") as f:
        lines = f.readlines()
        if len(lines) > 1:
            for line in lines[1:]:  # Skip header
                parts = line.strip().split(";")
                if len(parts) >= 5:
                    skipped.append(
                        {
                            "id": parts[0],
                            "name": parts[1],
                            "symbol": parts[2],
                            "reason": parts[3],
                            "url": parts[4],
                        }
                    )
    return skipped


# Backwards compatibility aliases
_load_accepted_coins = _load_coins_to_download
_load_rejected_coins = _load_skipped_coins


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


# Backwards compatibility alias
_append_insufficient_history_to_rejected = _append_insufficient_history_to_skipped


def _get_price_data_summary(quote_currency: str = "BTC") -> dict[str, dict]:
    """
    Get summary of price data for each coin.

    Args:
        quote_currency: Quote currency to filter by (default: "BTC")

    Returns:
        Dictionary mapping coin_id to price data summary
    """
    summaries = {}

    if not PRICES_DIR.exists():
        return summaries

    for parquet_file in sorted(PRICES_DIR.glob("*.parquet")):
        filename = parquet_file.stem

        # Handle pair-based filenames (e.g., eth-btc.parquet)
        if "-" in filename:
            parts = filename.rsplit("-", 1)
            if len(parts) == 2:
                coin_id, quote = parts
                # Special case: BTC should use USD quote since BTC-BTC doesn't make sense
                if coin_id.lower() == "btc":
                    if quote.upper() != "USD":
                        continue
                elif quote.upper() != quote_currency.upper():
                    continue
            else:
                coin_id = filename
        else:
            # Legacy format - assume BTC quote
            coin_id = filename
            if quote_currency.upper() != "BTC":
                continue

        try:
            df = pd.read_parquet(parquet_file)
            if not df.empty:
                start_date = df.index.min()
                end_date = df.index.max()
                summaries[coin_id] = {
                    "coin_id": coin_id,
                    "start_date": start_date.strftime("%Y-%m-%d"),
                    "end_date": end_date.strftime("%Y-%m-%d"),
                    "days": len(df),
                }
        except Exception:
            pass

    return summaries


def _generate_html(
    coins_to_download: list[dict],
    skipped_coins: list[dict],
    price_summaries: dict[str, dict],
) -> str:
    """
    Generate the complete HTML documentation page.

    Args:
        coins_to_download: List of coins to download dictionaries
        skipped_coins: List of skipped coin dictionaries
        price_summaries: Dictionary mapping coin_id to price data summary

    Returns:
        Complete HTML string
    """
    update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

    # Count coins with downloaded price data
    coins_with_data = sum(
        1 for c in coins_to_download if c.get("id", "").lower() in price_summaries
    )

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
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif;
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
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            padding: 0.5rem 2rem;
            text-align: center;
            border-bottom: 1px solid var(--border-color);
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.75rem;
            position: relative;
        }}

        .back-link {{
            color: var(--text-secondary);
            text-decoration: none;
            font-size: 1.1rem;
            position: absolute;
            left: 1.25rem;
        }}

        .back-link:hover {{
            color: var(--accent-blue);
        }}

        .header-content {{
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }}

        .logo {{
            font-size: 1.25rem;
        }}

        h1 {{
            font-size: 1.1rem;
            font-weight: 700;
            background: linear-gradient(90deg, var(--accent-orange), var(--accent-blue));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}

        .page-title {{
            font-size: 1.5rem;
            font-weight: 600;
            color: var(--text-primary);
            margin-bottom: 1rem;
        }}

        .update-time {{
            color: var(--text-muted);
            font-size: 0.85rem;
            margin-bottom: 1.5rem;
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
        .reason-history {{ background: #2d3f1e; color: #7ee68f; }}

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

        /* Consistent column widths for coin tables */
        table th:first-child,
        table td:first-child {{
            width: 60px;
            text-align: center;
        }}

        table th:nth-child(2),
        table td:nth-child(2) {{
            width: 100px;
        }}

        table th:nth-child(3),
        table td:nth-child(3) {{
            min-width: 200px;
        }}

        footer {{
            text-align: center;
            padding: 0.75rem;
            border-top: 1px solid var(--border-color);
            color: var(--text-secondary);
            font-size: 0.75rem;
        }}

        footer a {{
            color: var(--accent-blue);
            text-decoration: none;
        }}

        footer a:hover {{
            text-decoration: underline;
        }}

        @media (max-width: 768px) {{
            h1 {{
                font-size: 1.75rem;
            }}

            nav ul {{
                flex-wrap: wrap;
                gap: 1rem;
            }}

            .container {{
                padding: 1rem;
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
    <header>
        <a href="index.html" class="back-link">← Back</a>
        <div class="header-content">
            <div class="logo">📊</div>
            <h1>Halvix</h1>
        </div>
    </header>

    <div class="container">
        <h2 class="page-title">🔶 Data Status</h2>
        <p class="update-time">Last updated: {update_time}</p>
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value">{len(coins_to_download) + len(skipped_coins)}</div>
                <div class="stat-label">Total Coins Fetched</div>
            </div>
            <div class="stat-card">
                <div class="stat-value green">{coins_with_data}</div>
                <div class="stat-label">Downloaded BTC-pairs Data</div>
            </div>
            <div class="stat-card">
                <div class="stat-value red">{len(skipped_coins)}</div>
                <div class="stat-label">Skipped (Not Downloaded)</div>
            </div>
            <div class="stat-card">
                <div class="stat-value orange">{len(coins_to_download)}</div>
                <div class="stat-label">Coins to Download</div>
            </div>
        </div>

        <section id="downloaded">
            <h2>📊 Downloaded BTC-pairs Data ({coins_with_data} coins)</h2>
            <p class="section-description">
                Coins with BTC-pair data downloaded from CryptoCompare. Excludes wrapped, staked, bridged tokens, and stablecoins. BTC itself uses USD pair.
                Data spans from before the first Bitcoin halving to yesterday.
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
                            <th>Start Date</th>
                            <th>End Date</th>
                            <th>Days</th>
                        </tr>
                    </thead>
                    <tbody>
"""

    # Sort coins by market cap (descending) for consistent ranking
    coins_sorted = sorted(
        [c for c in coins_to_download if c.get("id", "").lower() in price_summaries],
        key=lambda c: c.get("market_cap", 0),
        reverse=True,
    )

    for i, coin in enumerate(coins_sorted, 1):
        coin_id = coin.get("id", "").lower()
        price_info = price_summaries.get(coin_id, {})

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
        start_date = price_info.get("start_date", "N/A")
        end_date = price_info.get("end_date", "N/A")
        days = price_info.get("days", 0)

        html += f"""                        <tr>
                            <td>{i}</td>
                            <td class="coin-symbol">{symbol}</td>
                            <td class="coin-name"><a href="{coin_url}" target="_blank">{name}</a></td>
                            <td class="market-cap">{market_cap_str}</td>
                            <td class="date-range">{start_date}</td>
                            <td class="date-range">{end_date}</td>
                            <td class="days-count">{days:,}</td>
                        </tr>
"""

    html += (
        """                    </tbody>
                </table>
            </div>
        </section>

        <section id="skipped">
            <h2>⏭️ Skipped ("""
        + str(len(skipped_coins))
        + """)</h2>
            <p class="section-description">
                These coins were skipped from download: stablecoins, wrapped/staked/bridged tokens, BTC derivatives,
                and coins without sufficient historical data (before 2024-01-10).
                Click the coin name to view on CryptoCompare.
            </p>
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Symbol</th>
                            <th>Name</th>
                            <th>Reason</th>
                        </tr>
                    </thead>
                    <tbody>
"""
    )

    for i, coin in enumerate(skipped_coins, 1):
        reason = coin.get("reason", "Unknown")
        reason_class = "reason-wrapped"
        if "BTC" in reason or "Bitcoin" in reason:
            reason_class = "reason-btc"
        elif "Stablecoin" in reason:
            reason_class = "reason-stablecoin"
        elif "historical" in reason.lower() or "Insufficient" in reason:
            reason_class = "reason-history"

        html += f"""                        <tr>
                            <td>{i}</td>
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

    html += """
    </div>

    <footer>
        <p>
            Generated by <strong>Halvix</strong> •
            <a href="https://github.com/yohplala/halvix">Source Code</a> •
            Data from <a href="https://www.cryptocompare.com/" target="_blank">CryptoCompare</a>
        </p>
    </footer>
</body>
</html>
"""

    return html


def generate_docs() -> Path:
    """Generate the data status HTML file (data_status.html)."""
    # Create directory using Pathlib with proper mode
    DOCS_SITE_DIR.mkdir(parents=True, exist_ok=True, mode=0o755)

    coins_to_download = _load_coins_to_download()
    skipped_coins = _load_skipped_coins()
    price_summaries = _get_price_data_summary()

    html_content = _generate_html(coins_to_download, skipped_coins, price_summaries)
    output_file = DOCS_SITE_DIR / "data_status.html"

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html_content)

    logger.info("Data status page generated: %s", output_file)
    return output_file


def _load_max_weight_change_info() -> dict | None:
    """Load max weight change info from JSON file."""
    from config import TOTAL2_MAX_WEIGHT_CHANGE_FILE

    if not TOTAL2_MAX_WEIGHT_CHANGE_FILE.exists():
        return None
    try:
        with open(TOTAL2_MAX_WEIGHT_CHANGE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _generate_index_html(max_weight_info: dict | None = None) -> str:
    """
    Generate the main index HTML page with simple navigation links.

    Uses shared footer from visualization.charts for consistency across all pages.

    Args:
        max_weight_info: Optional dict with max_weight_change, coin, date, volume_outliers_corrected

    Returns:
        Complete HTML string for index.html
    """
    from visualization.charts import _get_footer_css, _get_footer_html

    # Get shared footer components for consistency
    footer_css = _get_footer_css()
    footer_html = _get_footer_html()

    # Extract stats for display in TOTAL2 buttons row
    volume_outliers = (
        max_weight_info.get("volume_outliers_corrected", []) if max_weight_info else []
    )
    price_outliers = max_weight_info.get("price_outliers_corrected", []) if max_weight_info else []
    coin_statistics = max_weight_info.get("coin_statistics", []) if max_weight_info else []
    total_outliers = len(volume_outliers) + len(price_outliers)
    total_coins = len(coin_statistics)

    html = (
        """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Halvix - Cryptocurrency Halving Cycle Analysis</title>
    <style>
        :root {
            --bg-primary: #0d1117;
            --bg-secondary: #161b22;
            --text-primary: #e6edf3;
            --text-secondary: #8b949e;
            --accent-orange: #f7931a;
            --accent-blue: #58a6ff;
            --accent-green: #3fb950;
            --border-color: #30363d;
            --button-height: 5.5rem;
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            line-height: 1.6;
        }

        header {
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            padding: 1.5rem;
            text-align: center;
            border-bottom: 1px solid var(--border-color);
        }

        .logo {
            font-size: 2.5rem;
            margin-bottom: 0.4rem;
        }

        h1 {
            font-size: 1.75rem;
            font-weight: 700;
            background: linear-gradient(90deg, var(--accent-orange), var(--accent-blue));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }

        .subtitle {
            color: var(--text-secondary);
            font-size: 1rem;
            margin-top: 0.5rem;
        }

        main {
            max-width: 800px;
            margin: 0 auto;
            padding: 3rem 2rem;
        }

        .nav-list {
            list-style: none;
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }

        .nav-list li a {
            display: flex;
            align-items: center;
            gap: 1rem;
            padding: 1.25rem 1.5rem;
            min-height: var(--button-height);
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            text-decoration: none;
            color: var(--text-primary);
            font-size: 1.1rem;
            font-weight: 500;
            transition: all 0.2s ease;
        }

        .nav-list li a:hover {
            border-color: var(--accent-blue);
            transform: translateX(4px);
            background: var(--bg-primary);
        }

        .nav-list li a .icon {
            font-size: 1.5rem;
            width: 40px;
            text-align: center;
        }

        .nav-list li a .description {
            font-size: 0.85rem;
            color: var(--text-secondary);
            font-weight: 400;
            margin-top: 0.25rem;
        }

        .nav-list li a .link-content {
            flex: 1;
        }

        .nav-list li a .arrow {
            display: none;
        }

        .warning-box {
            border-color: #f59e0b !important;
            background: rgba(245, 158, 11, 0.1) !important;
        }

        .ok-box {
            border-color: var(--accent-green) !important;
            background: rgba(63, 185, 80, 0.1) !important;
        }

        .text-warning {
            color: #f59e0b;
        }

        .text-ok {
            color: var(--accent-green);
        }

        .text-muted {
            color: var(--text-secondary);
            font-size: 0.8rem;
        }

        /* Info box (non-clickable) styling */
        .info-box-container {
            list-style: none;
        }

        .info-box {
            display: flex;
            align-items: center;
            gap: 1rem;
            padding: 1.25rem 1.5rem;
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            color: var(--text-primary);
        }

        .info-box .icon {
            font-size: 1.5rem;
            width: 40px;
            text-align: center;
        }

        .info-box .info-content {
            flex: 1;
        }

        .info-box .info-title {
            font-size: 1.1rem;
            font-weight: 500;
            margin-bottom: 0.25rem;
        }

        .info-box .info-description {
            font-size: 0.85rem;
            color: var(--text-secondary);
            font-weight: 400;
        }

        /* Horizontal button row for TOTAL2 buttons */
        .total2-row {
            display: flex;
            gap: 1rem;
            width: 100%;
            list-style: none;
            margin-top: 1rem;
        }

        .total2-row > li {
            flex: 1;
            min-width: 0;
        }

        .total2-row li a {
            display: flex;
            flex-direction: row;
            align-items: center;
            gap: 0.75rem;
            padding: 1rem 1.25rem;
            min-height: var(--button-height);
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            text-decoration: none;
            color: var(--text-primary);
            font-size: 0.95rem;
            font-weight: 500;
            transition: all 0.2s ease;
        }

        .total2-row li a:hover {
            border-color: var(--accent-blue);
            background: var(--bg-primary);
        }

        .total2-row li a .icon {
            font-size: 1.25rem;
            flex-shrink: 0;
        }

        .total2-row li a .link-content {
            flex: 1;
            min-width: 0;
        }

        .total2-row li a .link-content > div:first-child {
            font-size: 0.95rem;
        }

        .total2-row li a .description {
            font-size: 0.75rem;
            color: var(--text-secondary);
            font-weight: 400;
            margin-top: 0.2rem;
            line-height: 1.3;
        }

        .total2-row li a .arrow {
            display: none;
        }

        """
        + footer_css
        + """

        @media (max-width: 768px) {
            h1 {
                font-size: 1.5rem;
            }

            .nav-list li a {
                padding: 1rem;
                font-size: 1rem;
            }

            .total2-row {
                flex-direction: column;
            }
        }
    </style>
</head>
<body>
    <header>
        <div class="logo">📊</div>
        <h1>Halvix</h1>
        <p class="subtitle">Cryptocurrency Analysis Relative to Bitcoin Halving Cycles</p>
    </header>

    <main>
        <ul class="nav-list">
            <li>
                <a href="data_status.html">
                    <span class="icon">🔶</span>
                    <div class="link-content">
                        <div>Data Status</div>
                        <div class="description">View downloaded coins, price data coverage, and skipped tokens</div>
                    </div>
                    <span class="arrow">→</span>
                </a>
            </li>
            <li>
                <a href="charts/btc_charts.html">
                    <span class="icon">₿</span>
                    <div class="link-content">
                        <div>Bitcoin Charts</div>
                        <div class="description">BTC/USD normalized and absolute price across 4 halving cycles</div>
                    </div>
                    <span class="arrow">→</span>
                </a>
            </li>
        </ul>
"""
        + f"""
        <ul class="total2-row">
            <li>
                <a href="charts/total2_charts.html">
                    <span class="icon">📈</span>
                    <div class="link-content">
                        <div>TOTAL2 Charts</div>
                        <div class="description">TOTAL2 index vs USD and BTC across 3 halving cycles</div>
                    </div>
                    <span class="arrow">→</span>
                </a>
            </li>
            <li>
                <a href="charts/total2_composition.html">
                    <span class="icon">🧩</span>
                    <div class="link-content">
                        <div>TOTAL2 Composition</div>
                        <div class="description">Explore which coins make up TOTAL2 on any date</div>
                    </div>
                    <span class="arrow">→</span>
                </a>
            </li>
            <li>
                <a href="total2_statistics.html">
                    <span class="icon">📊</span>
                    <div class="link-content">
                        <div>TOTAL2 Statistics</div>
                        <div class="description">{total_coins} coins tracked, {total_outliers} outlier corrections</div>
                    </div>
                    <span class="arrow">→</span>
                </a>
            </li>
        </ul>
    </main>

    """
        + footer_html
        + """
</body>
</html>
"""
    )
    return html


def _generate_total2_statistics_html(
    volume_outliers: list[dict],
    price_outliers: list[dict],
    coin_statistics: list[dict],
    max_weight_change: float | None = None,
    max_weight_change_coin: str | None = None,
    max_weight_change_date: str | None = None,
) -> str:
    """
    Generate HTML page with TOTAL2 statistics including coin rankings.

    Uses the shared HTML helpers from visualization.charts for consistent styling.
    Includes sortable tables with JavaScript.

    Args:
        volume_outliers: List of volume outlier dicts
        price_outliers: List of price outlier dicts
        coin_statistics: List of coin statistics dicts (ranked by days in TOTAL2)
        max_weight_change: Maximum weight change percentage
        max_weight_change_coin: Coin with maximum weight change
        max_weight_change_date: Date of maximum weight change

    Returns:
        Complete HTML string for total2_statistics.html
    """
    from visualization.charts import (
        _get_base_css,
        _get_footer_css,
        _get_footer_html,
        _get_header_css,
        _get_header_html,
    )

    # Build coin statistics table rows
    coin_rows = ""
    for stats in coin_statistics:
        still_present = "✓" if stats["still_present"] else "✗"
        still_class = "text-ok" if stats["still_present"] else "text-muted"
        coin_rows += f"""
            <tr>
                <td class="number">{stats['rank']}</td>
                <td><a href="{stats['url']}" target="_blank"><strong>{stats['coin_id']}</strong></a></td>
                <td class="number">{stats['days_in_total2']:,}</td>
                <td class="{still_class}">{still_present}</td>
                <td>{stats['first_date']}</td>
                <td class="number">{stats['first_price']:.8f}</td>
                <td class="number">{stats['first_weight']:.2f}%</td>
                <td>{stats['last_date']}</td>
                <td class="number">{stats['last_price']:.8f}</td>
                <td class="number">{stats['last_weight']:.2f}%</td>
                <td class="number">{stats['min_price']:.8f}</td>
                <td class="number">{stats['max_price']:.8f}</td>
                <td class="number">{stats['min_weight']:.2f}%</td>
                <td class="number">{stats['max_weight']:.2f}%</td>
            </tr>"""

    # Build volume outlier table rows
    volume_rows = ""
    for o in volume_outliers:
        volume_rows += f"""
            <tr>
                <td><strong>{o['coin']}</strong></td>
                <td>{o['date']}</td>
                <td class="number">{o['original']:,.2f}</td>
                <td class="number">{o['corrected']:,.2f}</td>
                <td class="number">{o['ratio']:,.0f}x</td>
            </tr>"""

    # Build price outlier table rows
    price_rows = ""
    for o in price_outliers:
        ratio_str = f"{o['ratio']:.1f}x" if o["ratio"] > 1 else f"{o['ratio']:.2f}x"
        price_rows += f"""
            <tr>
                <td><strong>{o['coin']}</strong></td>
                <td>{o['date']}</td>
                <td class="number">{o['original']:.6f}</td>
                <td class="number">{o['corrected']:.6f}</td>
                <td class="number">{ratio_str}</td>
                <td>{o.get('type', 'unknown')}</td>
            </tr>"""

    # Get shared CSS and HTML components
    base_css = _get_base_css()
    header_css = _get_header_css()
    footer_css = _get_footer_css()
    header_html = _get_header_html(back_link="index.html")
    footer_html = _get_footer_html()

    # Page-specific CSS for tables
    page_css = """
        main {
            max-width: 1600px;
            margin: 0 auto;
            padding: 2rem;
        }

        h2 {
            font-size: 1.5rem;
            margin-bottom: 0.5rem;
        }

        .description {
            color: var(--text-secondary);
            margin-bottom: 2rem;
            line-height: 1.6;
        }

        .table-container {
            overflow-x: auto;
            margin-bottom: 2rem;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            background: var(--bg-secondary);
            border-radius: 8px;
            overflow: hidden;
        }

        th, td {
            padding: 0.5rem 0.75rem;
            text-align: center;
            border-bottom: 1px solid var(--border-color);
            white-space: nowrap;
        }

        th {
            background: var(--bg-primary);
            font-weight: 600;
            color: var(--text-secondary);
            font-size: 0.75rem;
            text-transform: uppercase;
            cursor: pointer;
            user-select: none;
        }

        th:hover {
            background: var(--bg-secondary);
            color: var(--accent-blue);
        }

        th.sorted-asc::after {
            content: ' ▲';
            color: var(--accent-blue);
        }

        th.sorted-desc::after {
            content: ' ▼';
            color: var(--accent-blue);
        }

        td.number {
            font-family: 'SF Mono', Consolas, monospace;
            text-align: center;
            font-size: 0.85rem;
        }

        tr:hover {
            background: rgba(88, 166, 255, 0.05);
        }

        .section {
            margin-bottom: 3rem;
        }

        .section h2 {
            margin-bottom: 0.5rem;
        }

        .text-ok {
            color: var(--accent-green);
            font-weight: bold;
        }

        .text-muted {
            color: var(--text-secondary);
        }

        .text-warning {
            color: #f59e0b;
        }

        a {
            color: var(--accent-blue);
            text-decoration: none;
        }

        a:hover {
            text-decoration: underline;
        }

        /* Warning/Info box at top */
        .quality-box {
            display: flex;
            align-items: center;
            gap: 1rem;
            padding: 1.25rem 1.5rem;
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            margin-bottom: 2rem;
        }

        .quality-box.warning-box {
            border-color: #f59e0b;
            background: rgba(245, 158, 11, 0.1);
        }

        .quality-box.ok-box {
            border-color: var(--accent-green);
            background: rgba(63, 185, 80, 0.1);
        }

        .quality-box .icon {
            font-size: 1.5rem;
        }

        .quality-box .info-content {
            flex: 1;
        }

        .quality-box .info-title {
            font-size: 1.1rem;
            font-weight: 500;
            margin-bottom: 0.25rem;
        }

        .quality-box .info-description {
            font-size: 0.9rem;
            color: var(--text-secondary);
        }
    """

    # JavaScript for sortable tables
    sort_js = """
    <script>
    function sortTable(table, columnIndex, isNumeric, isPercent) {
        const tbody = table.querySelector('tbody');
        const rows = Array.from(tbody.querySelectorAll('tr'));
        const th = table.querySelectorAll('th')[columnIndex];
        const isAsc = th.classList.contains('sorted-asc');

        // Remove sort classes from all headers
        table.querySelectorAll('th').forEach(h => {
            h.classList.remove('sorted-asc', 'sorted-desc');
        });

        // Sort rows
        rows.sort((a, b) => {
            let aVal = a.cells[columnIndex].textContent.trim();
            let bVal = b.cells[columnIndex].textContent.trim();

            if (isPercent) {
                aVal = parseFloat(aVal.replace('%', '')) || 0;
                bVal = parseFloat(bVal.replace('%', '')) || 0;
            } else if (isNumeric) {
                aVal = parseFloat(aVal.replace(/,/g, '').replace('x', '')) || 0;
                bVal = parseFloat(bVal.replace(/,/g, '').replace('x', '')) || 0;
            } else if (aVal === '✓' || aVal === '✗') {
                aVal = aVal === '✓' ? 1 : 0;
                bVal = bVal === '✓' ? 1 : 0;
            }

            if (isAsc) {
                return aVal < bVal ? 1 : aVal > bVal ? -1 : 0;
            } else {
                return aVal > bVal ? 1 : aVal < bVal ? -1 : 0;
            }
        });

        // Update sort indicator
        th.classList.add(isAsc ? 'sorted-desc' : 'sorted-asc');

        // Re-append sorted rows
        rows.forEach(row => tbody.appendChild(row));
    }

    document.addEventListener('DOMContentLoaded', function() {
        // Make coin statistics table sortable
        const coinTable = document.getElementById('coin-stats-table');
        if (coinTable) {
            coinTable.querySelectorAll('th').forEach((th, index) => {
                th.addEventListener('click', () => {
                    const numericCols = [0, 2, 5, 6, 8, 9, 10, 11, 12, 13];
                    const percentCols = [6, 9, 12, 13];
                    sortTable(coinTable, index, numericCols.includes(index), percentCols.includes(index));
                });
            });
            // Default sort by days (column 2) descending
            coinTable.querySelectorAll('th')[2].classList.add('sorted-desc');
        }
    });
    </script>
    """

    # Build quality analysis box
    quality_box_html = ""
    if max_weight_change is not None:
        warning_class = "warning-box" if abs(max_weight_change) > 0.5 else "ok-box"
        status_class = "text-warning" if abs(max_weight_change) > 0.5 else "text-ok"
        status_text = (
            "⚠️ Exceeds 0.5% threshold"
            if abs(max_weight_change) > 0.5
            else "✓ Within acceptable range"
        )
        quality_box_html = f"""
        <div class="quality-box {warning_class}">
            <span class="icon">📊</span>
            <div class="info-content">
                <div class="info-title">TOTAL2 Quality Analysis</div>
                <div class="info-description">
                    <strong>Max Weight Change:</strong> {max_weight_change:.4f}% for <strong>{max_weight_change_coin or 'N/A'}</strong> on {max_weight_change_date or 'N/A'}<br>
                    <span class="{status_class}">{status_text}</span>
                </div>
            </div>
        </div>
        """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TOTAL2 Statistics - Halvix</title>
    <style>
        {base_css}
        {header_css}
        {footer_css}
        {page_css}
    </style>
</head>
<body>
    {header_html}

    <main>
        {quality_box_html}

        <div class="section">
            <h2>📊 Coins in TOTAL2 - Ranking by Days ({len(coin_statistics)} coins)</h2>
            <p class="description">
                Ranking of all coins that have appeared in TOTAL2, sorted by number of days present.
                Click any column header to sort. Links go to CryptoCompare coin pages.
            </p>

            <div class="table-container">
                <table id="coin-stats-table">
                    <thead>
                        <tr>
                            <th>Rank</th>
                            <th>Coin</th>
                            <th>Days</th>
                            <th>Still In</th>
                            <th>First Date</th>
                            <th>First Price</th>
                            <th>First Weight</th>
                            <th>Last Date</th>
                            <th>Last Price</th>
                            <th>Last Weight</th>
                            <th>Min Price</th>
                            <th>Max Price</th>
                            <th>Min Weight</th>
                            <th>Max Weight</th>
                        </tr>
                    </thead>
                    <tbody>
                        {coin_rows}
                    </tbody>
                </table>
            </div>
        </div>

        <div class="section">
            <h2>🔧 Volume Outliers Corrected</h2>
            <p class="description">
                CryptoCompare occasionally has bad data points with impossible volume spikes.
                These corrupt values are automatically detected using a rolling median of past 7 days,
                and corrected using a capped average approach. A data point is flagged as an outlier
                if its volume is &gt;20x the rolling median AND &gt;5,000 BTC.<br><br>
                <strong>{len(volume_outliers)} corrections</strong> were applied.
            </p>

            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>Coin</th>
                            <th>Date</th>
                            <th>Original Volume (BTC)</th>
                            <th>Corrected Volume (BTC)</th>
                            <th>Ratio</th>
                        </tr>
                    </thead>
                    <tbody>
                        {volume_rows}
                    </tbody>
                </table>
            </div>
        </div>

        <div class="section">
            <h2>📈 Price Corrections (Outliers + Entry Warmup)</h2>
            <p class="description">
                <strong>Day-over-day outliers:</strong> Extreme price changes (&gt;5x spike or &gt;80% crash)
                are detected and corrected using a capped average approach.<br>
                <strong>TOTAL2 entry warmup:</strong> When a coin first enters TOTAL2, its price is capped
                to max +80% gain or -40% loss per day, starting from market level (TOTAL2 value).
                This prevents artificial spikes from coins like ZEC (27.8 BTC on day 1) or YFI (3.73 BTC after 10x growth).<br><br>
                <strong>{len(price_outliers)} corrections</strong> were applied.
            </p>

            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>Coin</th>
                            <th>Date</th>
                            <th>Original Price (BTC)</th>
                            <th>Corrected Price (BTC)</th>
                            <th>Ratio</th>
                            <th>Type</th>
                        </tr>
                    </thead>
                    <tbody>
                        {price_rows}
                    </tbody>
                </table>
            </div>
        </div>
    </main>

    {footer_html}
    {sort_js}
</body>
</html>
"""
    return html


def generate_index_page() -> Path:
    """Generate the main index.html page and TOTAL2 statistics page in the site directory."""
    DOCS_SITE_DIR.mkdir(parents=True, exist_ok=True, mode=0o755)

    # Load max weight change info if available
    max_weight_info = _load_max_weight_change_info()

    # Generate main index page
    html_content = _generate_index_html(max_weight_info)
    output_file = DOCS_SITE_DIR / "index.html"

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html_content)

    logger.info("Index page generated: %s", output_file)

    # Generate TOTAL2 statistics page if we have data
    volume_outliers = (
        max_weight_info.get("volume_outliers_corrected", []) if max_weight_info else []
    )
    price_outliers = max_weight_info.get("price_outliers_corrected", []) if max_weight_info else []
    coin_statistics = max_weight_info.get("coin_statistics", []) if max_weight_info else []

    if volume_outliers or price_outliers or coin_statistics:
        stats_html = _generate_total2_statistics_html(
            volume_outliers,
            price_outliers,
            coin_statistics,
            max_weight_change=max_weight_info.get("max_weight_change") if max_weight_info else None,
            max_weight_change_coin=max_weight_info.get("coin") if max_weight_info else None,
            max_weight_change_date=max_weight_info.get("date") if max_weight_info else None,
        )
        stats_file = DOCS_SITE_DIR / "total2_statistics.html"

        with open(stats_file, "w", encoding="utf-8") as f:
            f.write(stats_html)

        logger.info("TOTAL2 statistics page generated: %s", stats_file)

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
    logger.info("  - Coins to download: %s", COINS_TO_DOWNLOAD_JSON)
    logger.info("  - Skipped coins: %s", DOWNLOAD_SKIPPED_CSV)

    logger.info("Successfully processed %d coins", result.coins_accepted)

    # Generate documentation automatically
    logger.info("-" * 60)
    logger.info("Generating documentation...")
    generate_docs()

    logger.info("Run 'python -m main fetch-prices' to fetch price data")

    return 0


def cmd_fetch_prices(args: argparse.Namespace) -> int:
    """Fetch price data for filtered coins using CryptoCompare."""
    from config import QUOTE_CURRENCIES

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

    # Fetch prices for selected quote currencies
    results = fetcher.fetch_all_prices(
        coins=coins,
        vs_currencies=currencies_to_fetch,
        use_cache=not args.no_cache,
        incremental=incremental,
        show_progress=not args.quiet,
    )

    # Always fetch BTC-USD (even in default mode) since BTC-BTC doesn't exist
    if not args.all_pairs:
        btc_coins = [c for c in coins if c.get("id", "").lower() == "btc"]
        if btc_coins:
            logger.info("Fetching BTC-USD (required since BTC-BTC doesn't exist)...")
            fetcher.fetch_all_prices(
                coins=btc_coins,
                vs_currencies=["USD"],
                use_cache=not args.no_cache,
                incremental=incremental,
                show_progress=False,
            )

    logger.info("-" * 60)
    logger.info("RESULTS")
    logger.info("-" * 60)
    logger.info(
        "  Prices fetched: %d coins × %d currencies", len(results), len(currencies_to_fetch)
    )

    # Show cache stats per currency
    price_cache = PriceDataCache()
    for currency in currencies_to_fetch:
        cached_coins = price_cache.list_cached_coins(currency)
        logger.info("  Cached (%s):    %d coins", currency, len(cached_coins))

    logger.info("Price data saved to: %s", fetcher.price_cache.prices_dir)

    # Report coins without sufficient history for individual analysis
    # Note: These coins ARE downloaded and WILL be used for TOTAL2
    # They just won't be analyzed individually (halving cycle comparison)
    logger.info("-" * 60)
    logger.info(
        "Checking data availability for individual analysis (MIN_DATA_DATE: %s)...", MIN_DATA_DATE
    )

    coins_with_history = fetcher.get_coins_with_data_before(
        MIN_DATA_DATE, coins, quote_currency="BTC"
    )
    recent_coins = [c for c in coins if c not in coins_with_history]

    if recent_coins:
        logger.info(
            "Found %d recent coins (data starts after %s):", len(recent_coins), MIN_DATA_DATE
        )
        logger.info("  → These ARE used for TOTAL2 calculation")
        logger.info("  → These will NOT be analyzed individually (insufficient history)")

        for coin in recent_coins[:10]:
            df = fetcher.price_cache.get_prices(coin["id"], "BTC")
            if df is not None and not df.empty:
                start_date = df.index.min().date()
                logger.info("  - %s (%s): data starts %s", coin["symbol"], coin["id"], start_date)
        if len(recent_coins) > 10:
            logger.info("  ... and %d more", len(recent_coins) - 10)
    else:
        logger.info("All %d coins have data before %s", len(coins), MIN_DATA_DATE)

    logger.info("Coins suitable for individual analysis: %d", len(coins_with_history))

    # Migrate legacy files to pair format if needed
    migrated = fetcher.price_cache.migrate_to_pair_format()
    if migrated > 0:
        logger.info("Migrated %d legacy files to pair format", migrated)

    # Generate documentation automatically
    logger.info("-" * 60)
    logger.info("Generating documentation...")
    generate_docs()

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


def cmd_generate_charts(args: argparse.Namespace) -> int:
    """Generate interactive Plotly charts for halving cycle analysis."""
    from visualization import generate_all_charts

    logger.info("=" * 60)
    logger.info("HALVIX - Generate Charts")
    logger.info("=" * 60)

    # Default to site/charts for GitHub Pages deployment
    site_charts_dir = DOCS_SITE_DIR / "charts"
    output_dir = args.output_dir if args.output_dir else site_charts_dir

    try:
        logger.info("Generating charts in: %s", output_dir)
        paths = generate_all_charts(output_dir)

        logger.info("-" * 60)
        logger.info("CHARTS GENERATED")
        logger.info("-" * 60)
        for name, path in paths.items():
            logger.info("  %s: %s", name, path)

        # Generate the main index.html page
        logger.info("Generating main index page...")
        generate_index_page()

        return 0

    except FileNotFoundError as e:
        logger.error("Missing data: %s", e)
        logger.info("Run 'calculate-total2' and 'fetch-prices' first.")
        return 1
    except Exception as e:
        logger.exception("Failed to generate charts: %s", e)
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
        default=TOP_N_FOR_TOTAL2,
        help=f"Number of coins in TOTAL2 (default: {TOP_N_FOR_TOTAL2})",
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

    # generate-charts command
    charts_parser = subparsers.add_parser(
        "generate-charts",
        help="Generate interactive Plotly charts for halving cycle analysis",
    )
    charts_parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for charts (default: site/charts)",
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
        "generate-charts": cmd_generate_charts,
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

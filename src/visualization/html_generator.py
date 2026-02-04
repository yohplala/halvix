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
from visualization.charts import (
    _get_base_css,
    _get_footer_css,
    _get_footer_html,
    _get_header_css,
    _get_header_html,
)

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
        """
        Generate the complete HTML documentation page.

        Uses shared footer from visualization.charts for consistency across all pages.

        Args:
            coins_to_download: List of coins to download dictionaries
            skipped_coins: List of filtered coins (stablecoins, wrapped, etc.)
            failed_coins: List of coins that failed to download (no BTC pair, etc.)
            no_usd_data_coins: List of coins without USD data from API
            price_summaries: Dictionary mapping coin_id to price data summary with quotes
            fetch_metadata: Metadata about the fetch operation

        Returns:
            Complete HTML string
        """
        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        footer_css = _get_footer_css()
        footer_html = _get_footer_html()

        # Count statistics from metadata
        coins_requested = fetch_metadata.get("coins_requested", TOP_N_BY_MARKETCAP_TO_FETCH)
        coins_with_usd = fetch_metadata.get("coins_fetched", 0)
        coins_no_usd = fetch_metadata.get("coins_no_usd_data", 0)
        coins_no_usd_accepted = fetch_metadata.get("coins_no_usd_accepted", 0)
        total_accepted = fetch_metadata.get("coins_accepted", len(coins_to_download))

        # Count coins with actual downloaded price data
        coins_with_data = sum(
            1 for c in coins_to_download if c.get("id", "").lower() in price_summaries
        )

        # All skipped = filtered + failed downloads
        all_skipped = skipped_coins + failed_coins
        total_skipped = len(all_skipped)

        # Count pairs downloaded (sum of all quotes for all coins)
        total_pairs = sum(len(s.get("quotes", [])) for s in price_summaries.values())

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

        .stat-sublabel {{
            color: var(--text-muted);
            font-size: 0.75rem;
            margin-top: 0.25rem;
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
        .reason-nopair {{ background: #3f1e1e; color: #f85149; }}

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
        {footer_css}
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
                <div class="stat-value">{coins_requested}</div>
                <div class="stat-label">Coins Requested</div>
                <div class="stat-sublabel">{coins_with_usd} USD + {coins_no_usd} no-USD</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{total_accepted}</div>
                <div class="stat-label">Coins Accepted</div>
                <div class="stat-sublabel">{coins_no_usd_accepted} from no-USD (BTC only)</div>
            </div>
            <div class="stat-card">
                <div class="stat-value green">{coins_with_data}</div>
                <div class="stat-label">Coins Downloaded</div>
            </div>
            <div class="stat-card">
                <div class="stat-value red">{total_skipped}</div>
                <div class="stat-label">Skipped / Failed</div>
            </div>
            <div class="stat-card">
                <div class="stat-value orange">{total_pairs}</div>
                <div class="stat-label">Total Pairs</div>
            </div>
        </div>

        <section id="downloaded">
            <h2>📊 Downloaded Price Data ({coins_with_data} coins)</h2>
            <p class="section-description">
                Coins with price data downloaded from CryptoCompare.
                <strong>Source</strong>: "USD" coins have market cap data; "BTC-only" coins were discovered without USD data but have BTC trading pairs.
                Click coin name to view on CryptoCompare.
            </p>
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Symbol</th>
                            <th>Name</th>
                            <th>Source</th>
                            <th>Quote(s)</th>
                            <th>Market Cap</th>
                            <th>Start Date</th>
                            <th>End Date</th>
                            <th>Days</th>
                        </tr>
                    </thead>
                    <tbody>
"""

        # Sort coins: first by has_usd_data (True first), then by market cap (descending)
        coins_sorted = sorted(
            [c for c in coins_to_download if c.get("id", "").lower() in price_summaries],
            key=lambda c: (not c.get("has_usd_data", True), -c.get("market_cap", 0)),
        )

        for i, coin in enumerate(coins_sorted, 1):
            coin_id = coin.get("id", "").lower()
            price_info = price_summaries.get(coin_id, {})
            has_usd_data = coin.get("has_usd_data", True)

            # Market cap display - N/A for coins without USD data
            market_cap = coin.get("market_cap", 0)
            if not has_usd_data or market_cap == 0:
                market_cap_str = '<span style="color: var(--text-muted);">N/A</span>'
            elif market_cap >= 1_000_000_000:
                market_cap_str = f"${market_cap / 1_000_000_000:.2f}B"
            elif market_cap >= 1_000_000:
                market_cap_str = f"${market_cap / 1_000_000:.2f}M"
            else:
                market_cap_str = f"${market_cap:,.0f}"

            # Source indicator
            if has_usd_data:
                source_str = "USD"
            else:
                source_str = '<span style="color: var(--accent-orange);">BTC-only</span>'

            symbol = coin.get("symbol", "N/A")
            name = coin.get("name", "N/A")
            coin_url = f"https://www.cryptocompare.com/coins/{symbol.upper()}/overview"
            quotes = price_info.get("quotes", [])
            quotes_str = ", ".join(quotes) if quotes else "N/A"
            start_date = price_info.get("start_date", "N/A")
            end_date = price_info.get("end_date", "N/A")
            days = price_info.get("days", 0)

            html += f"""                        <tr>
                            <td>{i}</td>
                            <td class="coin-symbol">{symbol}</td>
                            <td class="coin-name"><a href="{coin_url}" target="_blank">{name}</a></td>
                            <td>{source_str}</td>
                            <td>{quotes_str}</td>
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
            <h2>⏭️ Skipped / Failed ("""
            + str(total_skipped)
            + """)</h2>
            <p class="section-description">
                Coins excluded from analysis: stablecoins, wrapped/staked/bridged tokens, and coins without available trading pairs on CryptoCompare.
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

        for i, coin in enumerate(all_skipped, 1):
            reason = coin.get("reason", "Unknown")
            reason_class = "reason-wrapped"
            if "BTC" in reason or "Bitcoin" in reason:
                reason_class = "reason-btc"
            elif "Stablecoin" in reason:
                reason_class = "reason-stablecoin"
            elif "historical" in reason.lower() or "Insufficient" in reason:
                reason_class = "reason-history"
            elif "CCCAGG" in reason or "pair" in reason.lower() or "market" in reason.lower():
                reason_class = "reason-nopair"

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

        html += f"""
    </div>

    {footer_html}
</body>
</html>
"""

        return html

    def _generate_index_html(self, max_weight_info: dict | None = None) -> str:
        """
        Generate the main index HTML page with simple navigation links.

        Uses shared footer from visualization.charts for consistency across all pages.

        Args:
            max_weight_info: Optional dict with max_weight_change, coin, date, volume_outliers_corrected

        Returns:
            Complete HTML string for index.html
        """
        # Get shared footer components for consistency
        footer_css = _get_footer_css()
        footer_html = _get_footer_html()

        # Extract stats for display in TOTAL2 buttons row
        volume_outliers = (
            max_weight_info.get("volume_outliers_corrected", []) if max_weight_info else []
        )
        price_events = (
            max_weight_info.get("price_outliers_corrected", []) if max_weight_info else []
        )
        coin_statistics = max_weight_info.get("coin_statistics", []) if max_weight_info else []
        total_corrections = len(volume_outliers) + len(price_events)
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
                        <div class="description">{total_coins} coins tracked, {total_corrections} corrections</div>
                    </div>
                    <span class="arrow">→</span>
                </a>
            </li>
        </ul>

        <ul class="nav-list" style="margin-top: 1rem;">
            <li>
                <a href="pattern_analysis.html">
                    <span class="icon">🎯</span>
                    <div class="link-content">
                        <div>Cycle Pattern Analysis</div>
                        <div class="description">Price target projections using trendlines, Fibonacci, and diminishing returns</div>
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
        self,
        volume_outliers: list[dict],
        price_events: list[dict],
        coin_statistics: list[dict],
        max_weight_change: float | None = None,
        max_weight_change_coin: str | None = None,
        max_weight_change_date: str | None = None,
        index_type: str = "total2b",
    ) -> str:
        """
        Generate HTML page with TOTAL2 statistics including coin rankings.

        Uses the shared HTML helpers from visualization.charts for consistent styling.
        Includes sortable tables with JavaScript.

        Args:
            volume_outliers: List of volume outlier dicts
            price_events: List of price event dicts (scaling for TOTAL2b, capping for TOTAL2)
            coin_statistics: List of coin statistics dicts (ranked by days in TOTAL2)
            max_weight_change: Maximum weight change percentage
            max_weight_change_coin: Coin with maximum weight change
            max_weight_change_date: Date of maximum weight change
            index_type: "total2" or "total2b" - determines display text

        Returns:
            Complete HTML string for total2_statistics.html
        """
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

        # Build price events table rows
        # Both total2 and total2b use 'change_factor' key for their events
        price_rows = ""
        for o in price_events:
            factor = o.get("change_factor", 1.0)
            factor_str = f"{factor:.1f}x" if factor > 1 else f"{factor:.2f}x"
            price_rows += f"""
            <tr>
                <td><strong>{o['coin']}</strong></td>
                <td>{o['date']}</td>
                <td class="number">{o['original']:.6f}</td>
                <td class="number">{o['corrected']:.6f}</td>
                <td class="number">{factor_str}</td>
                <td>{o.get('type', 'unknown')}</td>
            </tr>"""

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
            # TOTAL2 (legacy)
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
            <h2>📈 {price_section_title}</h2>
            <p class="description">
                {price_section_description}
            </p>

            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>Coin</th>
                            <th>Date</th>
                            <th>Original Price (BTC)</th>
                            <th>{price_corrected_header}</th>
                            <th>Factor</th>
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

"""
Visualization module for Halvix.

Creates interactive Plotly charts for:
- TOTAL2 index across halving cycles
- BTC vs USD across halving cycles
- Interactive coin composition viewer
"""

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from config import (
    DAYS_AFTER_HALVING,
    DAYS_BEFORE_HALVING,
    HALVING_DATES,
    OUTPUT_DIR,
    TOTAL2_COMPOSITION_FILE,
    TOTAL2_INDEX_FILE,
)
from data.cache import PriceDataCache

# Color palettes
TOTAL2_COLORS = [
    "rgba(100, 149, 237, 0.5)",  # Cycle 1 - lightest blue
    "rgba(65, 105, 225, 0.7)",  # Cycle 2
    "rgba(30, 64, 175, 0.85)",  # Cycle 3
    "rgba(15, 32, 100, 1.0)",  # Cycle 4 - darkest blue
]

BTC_COLORS = [
    "rgba(255, 180, 100, 0.5)",  # Cycle 1 - lightest orange
    "rgba(255, 140, 0, 0.7)",  # Cycle 2
    "rgba(255, 100, 0, 0.85)",  # Cycle 3
    "rgba(230, 80, 0, 1.0)",  # Cycle 4 - darkest orange
]


def get_cycle_data(
    df: pd.DataFrame,
    halving_date: date,
    price_col: str = "close",
    days_before: int = DAYS_BEFORE_HALVING,
    days_after: int = DAYS_AFTER_HALVING,
    normalize: bool = False,
) -> pd.DataFrame:
    """
    Extract data for a halving cycle and normalize to days from halving.

    Args:
        df: DataFrame with DatetimeIndex
        halving_date: The halving date for this cycle
        price_col: Column name for price data
        days_before: Days before halving to include
        days_after: Days after halving to include
        normalize: If True, normalize prices to 1.0 at halving day

    Returns:
        DataFrame with 'days_from_halving' column and optionally normalized price
    """
    start = halving_date - timedelta(days=days_before)
    end = halving_date + timedelta(days=days_after)

    # Filter to cycle range
    mask = (df.index.date >= start) & (df.index.date <= end)
    cycle_df = df[mask].copy()

    if cycle_df.empty:
        return cycle_df

    # Add days from halving
    cycle_df["days_from_halving"] = (
        (cycle_df.index.date - halving_date).astype("timedelta64[D]").astype(int)
    )

    if normalize and price_col in cycle_df.columns:
        # Find the value at day 0 (halving day) or closest day after
        halving_mask = cycle_df["days_from_halving"] >= 0
        if halving_mask.any():
            first_day = cycle_df[halving_mask].iloc[0]
            halving_value = first_day[price_col]
            if halving_value > 0:
                cycle_df["normalized"] = cycle_df[price_col] / halving_value

    return cycle_df


def create_btc_usd_normalized_chart(
    output_path: Path | None = None,
) -> go.Figure:
    """
    Create BTC vs USD chart with 4 halving cycles, normalized to 1.0 at halving.

    Args:
        output_path: Path to save HTML file

    Returns:
        Plotly Figure
    """
    # Load BTC-USD data
    cache = PriceDataCache()
    btc_df = cache.get_prices("btc", "USD")

    if btc_df is None or btc_df.empty:
        raise FileNotFoundError("BTC-USD price data not found. Run fetch-prices first.")

    # Create figure
    fig = go.Figure()

    # Add trace for each halving cycle
    for i, halving_date in enumerate(HALVING_DATES):
        cycle_num = i + 1
        cycle_df = get_cycle_data(btc_df, halving_date, price_col="close", normalize=True)

        if cycle_df.empty or "normalized" not in cycle_df.columns:
            continue

        # Get actual halving price for hover
        halving_mask = cycle_df["days_from_halving"] >= 0
        if halving_mask.any():
            halving_price = cycle_df[halving_mask].iloc[0]["close"]
        else:
            halving_price = 0

        fig.add_trace(
            go.Scatter(
                x=cycle_df["days_from_halving"],
                y=cycle_df["normalized"],
                mode="lines",
                name=f"Cycle {cycle_num} ({halving_date.year})",
                line={"color": BTC_COLORS[i], "width": 2.5},
                hovertemplate=(
                    f"Cycle {cycle_num}<br>"
                    "Day: %{x}<br>"
                    "Multiplier: %{y:.2f}x<br>"
                    f"(Halving price: ${halving_price:,.0f})"
                    "<extra></extra>"
                ),
            )
        )

    # Layout
    fig.update_layout(
        title={
            "text": "Bitcoin (BTC) Price - Normalized to Halving Day",
            "font": {"size": 22, "family": "Arial Black"},
        },
        xaxis={
            "title": "Days from Halving",
            "tickmode": "linear",
            "dtick": 100,
            "gridcolor": "rgba(128, 128, 128, 0.2)",
            "zeroline": True,
            "zerolinecolor": "white",
            "zerolinewidth": 2,
        },
        yaxis={
            "title": "Price Multiplier (1.0 = Halving Day)",
            "type": "log",
            "gridcolor": "rgba(128, 128, 128, 0.2)",
        },
        legend={
            "yanchor": "top",
            "y": 0.99,
            "xanchor": "left",
            "x": 0.01,
            "bgcolor": "rgba(0,0,0,0.5)",
        },
        template="plotly_dark",
        hovermode="x unified",
        height=700,
        margin={"t": 80},
    )

    # Add vertical line at halving (day 0)
    fig.add_vline(x=0, line_dash="dash", line_color="rgba(255,255,255,0.7)", line_width=2)
    fig.add_annotation(
        x=0,
        y=1.02,
        yref="paper",
        text="⚡ HALVING",
        showarrow=False,
        font={"color": "white", "size": 14},
    )

    # Add horizontal line at 1.0
    fig.add_hline(y=1, line_dash="dot", line_color="rgba(255,255,255,0.3)")

    if output_path:
        fig.write_html(output_path)

    return fig


def create_total2_dual_chart(
    output_path: Path | None = None,
) -> go.Figure:
    """
    Create TOTAL2 chart with 2 subplots: USD (left) and BTC (right).
    Both normalized to 1.0 at halving day.

    Args:
        output_path: Path to save HTML file

    Returns:
        Plotly Figure
    """
    # Load TOTAL2 data (BTC denominated)
    if not TOTAL2_INDEX_FILE.exists():
        raise FileNotFoundError("TOTAL2 index not found. Run calculate-total2 first.")

    total2_btc_df = pd.read_parquet(TOTAL2_INDEX_FILE)

    # Load BTC-USD for conversion
    cache = PriceDataCache()
    btc_usd_df = cache.get_prices("btc", "USD")

    if btc_usd_df is None or btc_usd_df.empty:
        raise FileNotFoundError("BTC-USD price data not found. Run fetch-prices first.")

    # Calculate TOTAL2 in USD
    total2_usd_df = total2_btc_df.copy()
    # Align dates and multiply
    btc_usd_aligned = btc_usd_df["close"].reindex(total2_usd_df.index)
    total2_usd_df["total2_usd"] = total2_usd_df["total2_price"] * btc_usd_aligned

    # Create subplots
    fig = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=("TOTAL2 vs USD (Normalized)", "TOTAL2 vs BTC (Normalized)"),
        horizontal_spacing=0.08,
    )

    # Add traces for each halving cycle
    for i, halving_date in enumerate(HALVING_DATES):
        cycle_num = i + 1

        # USD chart (left)
        cycle_usd = get_cycle_data(
            total2_usd_df, halving_date, price_col="total2_usd", normalize=True
        )
        if not cycle_usd.empty and "normalized" in cycle_usd.columns:
            fig.add_trace(
                go.Scatter(
                    x=cycle_usd["days_from_halving"],
                    y=cycle_usd["normalized"],
                    mode="lines",
                    name=f"Cycle {cycle_num} ({halving_date.year})",
                    line={"color": TOTAL2_COLORS[i], "width": 2},
                    legendgroup=f"cycle{cycle_num}",
                    hovertemplate=(
                        f"Cycle {cycle_num}<br>"
                        "Day: %{x}<br>"
                        "Multiplier: %{y:.2f}x"
                        "<extra></extra>"
                    ),
                ),
                row=1,
                col=1,
            )

        # BTC chart (right)
        cycle_btc = get_cycle_data(
            total2_btc_df, halving_date, price_col="total2_price", normalize=True
        )
        if not cycle_btc.empty and "normalized" in cycle_btc.columns:
            fig.add_trace(
                go.Scatter(
                    x=cycle_btc["days_from_halving"],
                    y=cycle_btc["normalized"],
                    mode="lines",
                    name=f"Cycle {cycle_num} ({halving_date.year})",
                    line={"color": TOTAL2_COLORS[i], "width": 2},
                    legendgroup=f"cycle{cycle_num}",
                    showlegend=False,  # Only show in legend once
                    hovertemplate=(
                        f"Cycle {cycle_num}<br>"
                        "Day: %{x}<br>"
                        "Multiplier: %{y:.2f}x"
                        "<extra></extra>"
                    ),
                ),
                row=1,
                col=2,
            )

    # Update layout
    fig.update_layout(
        title={
            "text": "TOTAL2 Index - Normalized to Halving Day",
            "font": {"size": 22, "family": "Arial Black"},
            "x": 0.5,
        },
        template="plotly_dark",
        hovermode="x unified",
        height=600,
        legend={
            "yanchor": "top",
            "y": 0.99,
            "xanchor": "left",
            "x": 0.01,
            "bgcolor": "rgba(0,0,0,0.5)",
        },
        margin={"t": 100},
    )

    # Update axes
    for col in [1, 2]:
        fig.update_xaxes(
            title_text="Days from Halving",
            tickmode="linear",
            dtick=200,
            gridcolor="rgba(128, 128, 128, 0.2)",
            zeroline=True,
            zerolinecolor="white",
            zerolinewidth=2,
            row=1,
            col=col,
        )
        fig.update_yaxes(
            title_text="Multiplier (1.0 = Halving)",
            type="log",
            gridcolor="rgba(128, 128, 128, 0.2)",
            row=1,
            col=col,
        )

    # Add vertical lines at halving
    for col in [1, 2]:
        fig.add_vline(x=0, line_dash="dash", line_color="rgba(255,255,255,0.5)", row=1, col=col)

    # Add horizontal lines at 1.0
    fig.add_hline(y=1, line_dash="dot", line_color="rgba(255,255,255,0.3)", row=1, col=1)
    fig.add_hline(y=1, line_dash="dot", line_color="rgba(255,255,255,0.3)", row=1, col=2)

    if output_path:
        fig.write_html(output_path)

    return fig


def create_total2_halving_chart(
    output_path: Path | None = None,
    show_composition: bool = True,
) -> go.Figure:
    """
    Create TOTAL2 chart with 4 halving cycles (absolute values).

    Args:
        output_path: Path to save HTML file
        show_composition: If True, add interactive composition viewer

    Returns:
        Plotly Figure
    """
    # Load TOTAL2 data
    if not TOTAL2_INDEX_FILE.exists():
        raise FileNotFoundError("TOTAL2 index not found. Run calculate-total2 first.")

    total2_df = pd.read_parquet(TOTAL2_INDEX_FILE)

    # Load composition data if needed
    composition_df = None
    if show_composition and TOTAL2_COMPOSITION_FILE.exists():
        composition_df = pd.read_parquet(TOTAL2_COMPOSITION_FILE)

    # Create figure
    fig = go.Figure()

    # Add trace for each halving cycle
    for i, halving_date in enumerate(HALVING_DATES):
        cycle_num = i + 1
        cycle_df = get_cycle_data(total2_df, halving_date, price_col="total2_price")

        if cycle_df.empty:
            continue

        # Prepare hover text with composition info
        if composition_df is not None:
            hover_texts = []
            for idx, row in cycle_df.iterrows():
                dt = idx.date()
                comp = composition_df[composition_df["date"] == dt]
                if not comp.empty:
                    top_coins = comp.nsmallest(10, "rank")["coin_id"].str.upper().tolist()
                    coins_str = ", ".join(top_coins)
                    hover_texts.append(
                        f"Date: {dt}<br>"
                        f"TOTAL2: {row['total2_price']:.8f} BTC<br>"
                        f"Coins: {row['coin_count']}<br>"
                        f"Top 10: {coins_str}"
                    )
                else:
                    hover_texts.append(
                        f"Date: {dt}<br>"
                        f"TOTAL2: {row['total2_price']:.8f} BTC<br>"
                        f"Coins: {row['coin_count']}"
                    )
        else:
            hover_texts = None

        fig.add_trace(
            go.Scatter(
                x=cycle_df["days_from_halving"],
                y=cycle_df["total2_price"],
                mode="lines",
                name=f"Cycle {cycle_num} ({halving_date.year})",
                line={"color": TOTAL2_COLORS[i], "width": 2},
                hovertemplate="%{text}<extra></extra>" if hover_texts else None,
                text=hover_texts,
            )
        )

    # Layout
    fig.update_layout(
        title={
            "text": "TOTAL2 Index Across Bitcoin Halving Cycles",
            "font": {"size": 20},
        },
        xaxis={
            "title": "Days from Halving",
            "tickmode": "linear",
            "dtick": 100,
            "gridcolor": "rgba(128, 128, 128, 0.2)",
        },
        yaxis={
            "title": "TOTAL2 (BTC)",
            "type": "log",
            "gridcolor": "rgba(128, 128, 128, 0.2)",
        },
        legend={
            "yanchor": "top",
            "y": 0.99,
            "xanchor": "left",
            "x": 0.01,
        },
        template="plotly_dark",
        hovermode="x unified",
        height=600,
    )

    # Add vertical line at halving (day 0)
    fig.add_vline(x=0, line_dash="dash", line_color="white", opacity=0.5)
    fig.add_annotation(
        x=0,
        y=1,
        yref="paper",
        text="HALVING",
        showarrow=False,
        font={"color": "white"},
    )

    if output_path:
        fig.write_html(output_path)

    return fig


def create_btc_usd_halving_chart(
    output_path: Path | None = None,
) -> go.Figure:
    """
    Create BTC vs USD chart with 4 halving cycles (absolute values).

    Args:
        output_path: Path to save HTML file

    Returns:
        Plotly Figure
    """
    # Load BTC-USD data
    cache = PriceDataCache()
    btc_df = cache.get_prices("btc", "USD")

    if btc_df is None or btc_df.empty:
        raise FileNotFoundError("BTC-USD price data not found. Run fetch-prices first.")

    # Create figure
    fig = go.Figure()

    # Add trace for each halving cycle
    for i, halving_date in enumerate(HALVING_DATES):
        cycle_num = i + 1
        cycle_df = get_cycle_data(btc_df, halving_date, price_col="close")

        if cycle_df.empty:
            continue

        fig.add_trace(
            go.Scatter(
                x=cycle_df["days_from_halving"],
                y=cycle_df["close"],
                mode="lines",
                name=f"Cycle {cycle_num} ({halving_date.year})",
                line={"color": BTC_COLORS[i], "width": 2},
                hovertemplate=("Day: %{x}<br>" "Price: $%{y:,.2f}<br>" "<extra></extra>"),
            )
        )

    # Layout
    fig.update_layout(
        title={
            "text": "Bitcoin (BTC) Price Across Halving Cycles",
            "font": {"size": 20},
        },
        xaxis={
            "title": "Days from Halving",
            "tickmode": "linear",
            "dtick": 100,
            "gridcolor": "rgba(128, 128, 128, 0.2)",
        },
        yaxis={
            "title": "BTC Price (USD)",
            "type": "log",
            "tickprefix": "$",
            "gridcolor": "rgba(128, 128, 128, 0.2)",
        },
        legend={
            "yanchor": "top",
            "y": 0.99,
            "xanchor": "left",
            "x": 0.01,
        },
        template="plotly_dark",
        hovermode="x unified",
        height=600,
    )

    # Add vertical line at halving (day 0)
    fig.add_vline(x=0, line_dash="dash", line_color="white", opacity=0.5)
    fig.add_annotation(
        x=0,
        y=1,
        yref="paper",
        text="HALVING",
        showarrow=False,
        font={"color": "white"},
    )

    if output_path:
        fig.write_html(output_path)

    return fig


def create_composition_viewer_html(
    output_path: Path,
) -> Path:
    """
    Create an HTML page with interactive TOTAL2 composition viewer.

    Allows selecting a date and viewing which coins were in TOTAL2.

    Args:
        output_path: Path to save HTML file

    Returns:
        Path to created file
    """
    # Load composition data
    if not TOTAL2_COMPOSITION_FILE.exists():
        raise FileNotFoundError("TOTAL2 composition not found. Run calculate-total2 first.")

    composition_df = pd.read_parquet(TOTAL2_COMPOSITION_FILE)
    pd.read_parquet(TOTAL2_INDEX_FILE)

    # Get unique dates
    dates = sorted(composition_df["date"].unique())

    # Create date options
    date_options = "\n".join([f'<option value="{d}">{d}</option>' for d in dates])

    # Create composition data as JSON
    composition_by_date = {}
    for dt in dates:
        day_comp = composition_df[composition_df["date"] == dt].sort_values("rank")
        composition_by_date[str(dt)] = [
            {
                "rank": int(row["rank"]),
                "coin_id": row["coin_id"].upper(),
                "volume": float(row["volume"]),
                "weight": float(row["weight"]) * 100,
                "price_btc": float(row["price_btc"]),
            }
            for _, row in day_comp.iterrows()
        ]

    import json

    composition_json = json.dumps(composition_by_date)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TOTAL2 Composition Viewer</title>
    <style>
        :root {{
            --bg-primary: #0d1117;
            --bg-secondary: #161b22;
            --text-primary: #c9d1d9;
            --text-secondary: #8b949e;
            --accent-blue: #58a6ff;
            --accent-green: #3fb950;
            --border-color: #30363d;
        }}
        body {{
            font-family: 'Segoe UI', sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            margin: 0;
            padding: 2rem;
        }}
        h1 {{ color: var(--accent-blue); }}
        .controls {{
            margin-bottom: 2rem;
        }}
        select {{
            padding: 0.5rem 1rem;
            font-size: 1rem;
            background: var(--bg-secondary);
            color: var(--text-primary);
            border: 1px solid var(--border-color);
            border-radius: 4px;
        }}
        .stats {{
            display: flex;
            gap: 2rem;
            margin-bottom: 1rem;
        }}
        .stat {{
            background: var(--bg-secondary);
            padding: 1rem;
            border-radius: 8px;
        }}
        .stat-value {{
            font-size: 1.5rem;
            font-weight: bold;
            color: var(--accent-green);
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: var(--bg-secondary);
        }}
        th, td {{
            padding: 0.75rem;
            text-align: left;
            border-bottom: 1px solid var(--border-color);
        }}
        th {{ background: #21262d; }}
        .coin-symbol {{ color: var(--accent-blue); font-weight: bold; }}
        .weight {{ color: var(--accent-green); }}
    </style>
</head>
<body>
    <h1>📊 TOTAL2 Composition Viewer</h1>

    <div class="controls">
        <label for="date-select">Select Date:</label>
        <select id="date-select">
            {date_options}
        </select>
    </div>

    <div class="stats">
        <div class="stat">
            <div>Coins in TOTAL2</div>
            <div class="stat-value" id="coin-count">-</div>
        </div>
        <div class="stat">
            <div>Total Volume</div>
            <div class="stat-value" id="total-volume">-</div>
        </div>
    </div>

    <table id="composition-table">
        <thead>
            <tr>
                <th>Rank</th>
                <th>Coin</th>
                <th>Weight</th>
                <th>Price (BTC)</th>
                <th>Volume (BTC)</th>
            </tr>
        </thead>
        <tbody id="composition-body">
        </tbody>
    </table>

    <script>
        const compositionData = {composition_json};

        function updateTable(dateStr) {{
            const data = compositionData[dateStr] || [];
            const tbody = document.getElementById('composition-body');

            if (data.length === 0) {{
                tbody.innerHTML = '<tr><td colspan="5">No data for this date</td></tr>';
                document.getElementById('coin-count').textContent = '-';
                document.getElementById('total-volume').textContent = '-';
                return;
            }}

            document.getElementById('coin-count').textContent = data.length;
            const totalVol = data.reduce((sum, c) => sum + c.volume, 0);
            document.getElementById('total-volume').textContent = totalVol.toFixed(2) + ' BTC';

            tbody.innerHTML = data.map(coin => `
                <tr>
                    <td>${{coin.rank}}</td>
                    <td class="coin-symbol">${{coin.coin_id}}</td>
                    <td class="weight">${{coin.weight.toFixed(2)}}%</td>
                    <td>${{coin.price_btc.toFixed(8)}}</td>
                    <td>${{coin.volume.toFixed(2)}}</td>
                </tr>
            `).join('');
        }}

        document.getElementById('date-select').addEventListener('change', (e) => {{
            updateTable(e.target.value);
        }});

        // Initialize with last date
        const select = document.getElementById('date-select');
        select.selectedIndex = select.options.length - 1;
        updateTable(select.value);
    </script>
</body>
</html>
"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return output_path


def generate_all_charts(output_dir: Path | None = None) -> dict[str, Path]:
    """
    Generate all visualization charts.

    Args:
        output_dir: Directory to save charts (default: OUTPUT_DIR/charts)

    Returns:
        Dictionary mapping chart name to file path
    """
    if output_dir is None:
        output_dir = OUTPUT_DIR / "charts"
    output_dir.mkdir(parents=True, exist_ok=True)

    paths = {}

    # BTC vs USD normalized chart (main chart)
    btc_norm_path = output_dir / "btc_usd_normalized.html"
    create_btc_usd_normalized_chart(btc_norm_path)
    paths["btc_normalized"] = btc_norm_path

    # TOTAL2 dual chart (USD and BTC side by side)
    total2_dual_path = output_dir / "total2_dual_normalized.html"
    create_total2_dual_chart(total2_dual_path)
    paths["total2_dual"] = total2_dual_path

    # Legacy charts (absolute values)
    total2_path = output_dir / "total2_halving_cycles.html"
    create_total2_halving_chart(total2_path)
    paths["total2"] = total2_path

    btc_path = output_dir / "btc_halving_cycles.html"
    create_btc_usd_halving_chart(btc_path)
    paths["btc"] = btc_path

    # Composition viewer
    comp_path = output_dir / "total2_composition.html"
    create_composition_viewer_html(comp_path)
    paths["composition"] = comp_path

    return paths

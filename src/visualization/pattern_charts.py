"""
Pattern Analysis Charts for Halvix.

Generates HTML pages with cycle pattern analysis charts showing:
- Full price curve in light grey
- Min/max points with solid lines
- Target projections from 3 methods

All charts use the same time scale (cycles 3, 4, and projected 5).
"""

from datetime import date, timedelta
from pathlib import Path

import plotly.graph_objects as go

from analysis.cycle_patterns import (
    BTCPatternResult,
    CoinPatternResult,
)
from config import (
    DAYS_AFTER_HALVING,
    DAYS_BEFORE_HALVING,
    HALVING_DATES,
    PROJECTED_5TH_HALVING,
)
from data.cache import PriceDataCache
from visualization.charts import (
    _get_base_css,
    _get_footer_css,
    _get_footer_html,
    _get_header_css,
    _get_header_html,
)

# =============================================================================
# Color Configuration
# =============================================================================

# Background curve color (light grey, very transparent)
CURVE_COLOR = "rgba(128, 128, 128, 0.25)"

# Point colors by type
POINT_COLORS = {
    "min1": "#f85149",  # Red for pre-halving min
    "max1": "#f0883e",  # Orange for pre-halving max
    "min2": "#a371f7",  # Purple for post-halving min
    "max2": "#3fb950",  # Green for post-halving max
}

# Trendline colors
UPPER_TRENDLINE_COLOR = "rgba(63, 185, 80, 0.6)"  # Green
LOWER_TRENDLINE_COLOR = "rgba(248, 81, 73, 0.6)"  # Red

# Target colors
TARGET_COLORS = {
    "trendline": "#58a6ff",  # Blue
    "fibonacci": "#f0883e",  # Orange
    "diminishing": "#a371f7",  # Purple
}

# Cycle colors (matching charts.py)
CYCLE_COLORS = {
    2: "rgba(144, 224, 239, 0.9)",  # Cycle 2 (2016) - pale cyan
    3: "rgba(56, 189, 248, 0.95)",  # Cycle 3 (2020) - bright cyan-blue
    4: "rgba(100, 160, 255, 1.0)",  # Cycle 4 (2024) - lighter sky blue
}


# =============================================================================
# Chart Generation
# =============================================================================


def _get_time_range() -> tuple[date, date]:
    """
    Get the time range for pattern charts (cycles 3, 4, and projected 5).

    Returns:
        Tuple of (start_date, end_date)
    """
    # Start: 550 days before 2020 halving (cycle 3 start)
    start = HALVING_DATES[2] - timedelta(days=DAYS_BEFORE_HALVING)

    # End: 950 days after projected 5th halving (cycle 5 end)
    end = PROJECTED_5TH_HALVING + timedelta(days=DAYS_AFTER_HALVING)

    return start, end


def _add_halving_lines(fig: go.Figure, row: int = 1, col: int = 1) -> None:
    """Add vertical lines at halving dates."""
    halvings = [HALVING_DATES[2], HALVING_DATES[3], PROJECTED_5TH_HALVING]
    labels = ["3rd Halving\n2020", "4th Halving\n2024", "5th Halving\n2028 (proj.)"]

    for halving_date, _label in zip(halvings, labels, strict=False):
        fig.add_vline(
            x=halving_date,
            line={"dash": "dot", "color": "rgba(200,200,200,0.4)", "width": 1.5},
            row=row,
            col=col,
        )


def create_btc_pattern_chart(
    result: BTCPatternResult,
    price_cache: PriceDataCache,
    output_path: Path | None = None,
) -> go.Figure:
    """
    Create BTC/USD pattern analysis chart.

    Args:
        result: BTC pattern analysis result
        price_cache: Price data cache
        output_path: Optional path to save HTML

    Returns:
        Plotly Figure
    """
    # Load BTC-USD data
    btc_df = price_cache.get_prices("btc", "USD")
    if btc_df is None or btc_df.empty:
        raise ValueError("BTC-USD data not available")

    # Filter to time range
    start_date, end_date = _get_time_range()
    mask = (btc_df.index.date >= start_date) & (btc_df.index.date <= end_date)
    plot_df = btc_df[mask]

    fig = go.Figure()

    # 1. Full price curve in light grey
    fig.add_trace(
        go.Scatter(
            x=plot_df.index,
            y=plot_df["close"],
            mode="lines",
            name="BTC/USD",
            line={"color": CURVE_COLOR, "width": 1},
            hovertemplate="Date: %{x|%Y-%m-%d}<br>Price: $%{y:,.2f}<extra></extra>",
        )
    )

    # 2. Add min/max points with connecting lines
    # Group points by cycle
    cycles = sorted({p.cycle_num for p in result.points})

    for cycle_num in cycles:
        cycle_points = sorted(
            [p for p in result.points if p.cycle_num == cycle_num],
            key=lambda x: x.date,
        )

        if not cycle_points:
            continue

        # Add connecting line for this cycle
        x_vals = [p.date for p in cycle_points]
        y_vals = [p.price for p in cycle_points]

        fig.add_trace(
            go.Scatter(
                x=x_vals,
                y=y_vals,
                mode="lines",
                name=f"Cycle {cycle_num}",
                line={"color": CYCLE_COLORS.get(cycle_num, "#888"), "width": 2.5},
                showlegend=True,
            )
        )

        # Add individual points with markers
        for p in cycle_points:
            fig.add_trace(
                go.Scatter(
                    x=[p.date],
                    y=[p.price],
                    mode="markers",
                    marker={
                        "size": 12,
                        "color": POINT_COLORS.get(p.point_type, "#888"),
                        "line": {"width": 2, "color": "#fff"},
                    },
                    name=f"{p.point_type.upper()} C{p.cycle_num}",
                    showlegend=False,
                    hovertemplate=(
                        f"{p.point_type.upper()} (Cycle {p.cycle_num})<br>"
                        f"Date: {p.date}<br>"
                        f"Price: ${p.price:,.2f}<br>"
                        f"Days from halving: {p.days_from_halving:+d}"
                        "<extra></extra>"
                    ),
                )
            )

    # 3. Add target annotations
    # Position targets at projected cycle 5 peak date
    target_date = PROJECTED_5TH_HALVING + timedelta(days=550)

    targets = []
    if result.trendline_target:
        targets.append(
            ("Trendline", result.trendline_target, result.trendline_target_pct, TARGET_COLORS["trendline"])
        )
    if result.fib_target:
        targets.append(
            ("Fib 127.2%", result.fib_target, result.fib_target_pct, TARGET_COLORS["fibonacci"])
        )
    if result.dim_return_target:
        targets.append(
            ("Dim. Return", result.dim_return_target, result.dim_return_target_pct, TARGET_COLORS["diminishing"])
        )

    for _i, (label, target_price, target_pct, color) in enumerate(targets):
        # Add target marker
        fig.add_trace(
            go.Scatter(
                x=[target_date],
                y=[target_price],
                mode="markers+text",
                marker={"size": 10, "color": color, "symbol": "star"},
                text=[f"  {label}: ${target_price:,.0f} (+{target_pct:.0f}%)"],
                textposition="middle right",
                textfont={"color": color, "size": 11},
                name=f"Target: {label}",
                showlegend=False,
                hovertemplate=(
                    f"<b>{label} Target</b><br>"
                    f"Price: ${target_price:,.2f}<br>"
                    f"Gain: +{target_pct:.1f}%<br>"
                    f"Date: ~{target_date}"
                    "<extra></extra>"
                ),
            )
        )

    # Add halving lines
    _add_halving_lines(fig)

    # Layout
    fig.update_layout(
        title={
            "text": "Bitcoin (BTC/USD) - Cycle Pattern Analysis",
            "font": {"size": 20, "family": "Arial Black"},
        },
        xaxis={
            "title": "Date",
            "gridcolor": "rgba(128, 128, 128, 0.2)",
            "range": [start_date, end_date],
        },
        yaxis={
            "title": "Price (USD)",
            "type": "log",
            "tickprefix": "$",
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
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0d1117",
        hovermode="x unified",
        height=600,
        margin={"t": 80, "b": 60},
    )

    # Add pattern type annotation
    if result.pattern_type:
        pattern_display = result.pattern_type.replace("_", " ").title()
        fig.add_annotation(
            x=0.99,
            y=0.01,
            xref="paper",
            yref="paper",
            text=f"Pattern: {pattern_display}",
            showarrow=False,
            font={"color": "#8b949e", "size": 12},
            xanchor="right",
            yanchor="bottom",
        )

    if output_path:
        _write_pattern_chart(fig, output_path, "BTC/USD Pattern Analysis")

    return fig


def create_altcoin_pattern_chart(
    result: CoinPatternResult,
    price_cache: PriceDataCache,
    output_path: Path | None = None,
) -> go.Figure:
    """
    Create altcoin/BTC pattern analysis chart.

    Args:
        result: Coin pattern analysis result
        price_cache: Price data cache
        output_path: Optional path to save HTML

    Returns:
        Plotly Figure
    """
    # Load coin price data (vs BTC)
    coin_df = price_cache.get_prices(result.coin_id, "BTC")
    if coin_df is None or coin_df.empty:
        raise ValueError(f"{result.coin_id.upper()}/BTC data not available")

    # Filter to time range
    start_date, end_date = _get_time_range()
    mask = (coin_df.index.date >= start_date) & (coin_df.index.date <= end_date)
    plot_df = coin_df[mask]

    fig = go.Figure()

    # 1. Full price curve in light grey
    fig.add_trace(
        go.Scatter(
            x=plot_df.index,
            y=plot_df["close"],
            mode="lines",
            name=f"{result.coin_id.upper()}/BTC",
            line={"color": CURVE_COLOR, "width": 1},
            hovertemplate="Date: %{x|%Y-%m-%d}<br>Price: %{y:.8f} BTC<extra></extra>",
        )
    )

    # 2. Add min/max points with connecting lines
    cycles = sorted({p.cycle_num for p in result.points})

    for cycle_num in cycles:
        cycle_points = sorted(
            [p for p in result.points if p.cycle_num == cycle_num],
            key=lambda x: x.date,
        )

        if not cycle_points:
            continue

        # Add connecting line for this cycle
        x_vals = [p.date for p in cycle_points]
        y_vals = [p.price for p in cycle_points]

        fig.add_trace(
            go.Scatter(
                x=x_vals,
                y=y_vals,
                mode="lines",
                name=f"Cycle {cycle_num}",
                line={"color": CYCLE_COLORS.get(cycle_num, "#888"), "width": 2.5},
                showlegend=True,
            )
        )

        # Add individual points with markers
        for p in cycle_points:
            fig.add_trace(
                go.Scatter(
                    x=[p.date],
                    y=[p.price],
                    mode="markers",
                    marker={
                        "size": 12,
                        "color": POINT_COLORS.get(p.point_type, "#888"),
                        "line": {"width": 2, "color": "#fff"},
                    },
                    name=f"{p.point_type.upper()} C{p.cycle_num}",
                    showlegend=False,
                    hovertemplate=(
                        f"{p.point_type.upper()} (Cycle {p.cycle_num})<br>"
                        f"Date: {p.date}<br>"
                        f"Price: {p.price:.8f} BTC<br>"
                        f"Days from halving: {p.days_from_halving:+d}"
                        "<extra></extra>"
                    ),
                )
            )

    # 3. Add target annotations
    target_date = PROJECTED_5TH_HALVING + timedelta(days=550)

    targets = []
    if result.trendline_target:
        targets.append(
            ("Trendline", result.trendline_target, result.trendline_target_pct, TARGET_COLORS["trendline"])
        )
    if result.fib_target:
        targets.append(
            ("Fib 127.2%", result.fib_target, result.fib_target_pct, TARGET_COLORS["fibonacci"])
        )
    if result.dim_return_target:
        targets.append(
            ("Dim. Return", result.dim_return_target, result.dim_return_target_pct, TARGET_COLORS["diminishing"])
        )

    for label, target_price, target_pct, color in targets:
        fig.add_trace(
            go.Scatter(
                x=[target_date],
                y=[target_price],
                mode="markers+text",
                marker={"size": 10, "color": color, "symbol": "star"},
                text=[f"  {label}: +{target_pct:.0f}%"],
                textposition="middle right",
                textfont={"color": color, "size": 11},
                name=f"Target: {label}",
                showlegend=False,
                hovertemplate=(
                    f"<b>{label} Target</b><br>"
                    f"Price: {target_price:.8f} BTC<br>"
                    f"Gain: +{target_pct:.1f}%<br>"
                    f"Date: ~{target_date}"
                    "<extra></extra>"
                ),
            )
        )

    # Add halving lines
    _add_halving_lines(fig)

    # Layout
    confidence_badge = f"[{result.confidence.upper()}]" if result.confidence else ""
    fig.update_layout(
        title={
            "text": f"{result.coin_id.upper()}/BTC - Cycle Pattern Analysis {confidence_badge}",
            "font": {"size": 20, "family": "Arial Black"},
        },
        xaxis={
            "title": "Date",
            "gridcolor": "rgba(128, 128, 128, 0.2)",
            "range": [start_date, end_date],
        },
        yaxis={
            "title": "Price (BTC)",
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
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0d1117",
        hovermode="x unified",
        height=600,
        margin={"t": 80, "b": 60},
    )

    # Add pattern type and composite target annotations
    annotations = []
    if result.pattern_type:
        pattern_display = result.pattern_type.replace("_", " ").title()
        annotations.append(f"Pattern: {pattern_display}")
    if result.composite_target_pct:
        annotations.append(f"Composite: +{result.composite_target_pct:.0f}%")

    if annotations:
        fig.add_annotation(
            x=0.99,
            y=0.01,
            xref="paper",
            yref="paper",
            text=" | ".join(annotations),
            showarrow=False,
            font={"color": "#8b949e", "size": 12},
            xanchor="right",
            yanchor="bottom",
        )

    if output_path:
        _write_pattern_chart(fig, output_path, f"{result.coin_id.upper()}/BTC Pattern Analysis")

    return fig


def _write_pattern_chart(fig: go.Figure, output_path: Path, title: str) -> None:
    """Write a pattern chart to HTML with template wrapper."""
    base_css = _get_base_css()
    header_css = _get_header_css()
    footer_css = _get_footer_css()
    header_html = _get_header_html(back_link="../pattern_analysis.html")
    footer_html = _get_footer_html()

    chart_css = """
        .chart-container {
            width: 100%;
            padding: 0.75rem;
        }

        @media (max-width: 768px) {
            header h1 {
                font-size: 0.9rem;
            }
            header {
                padding: 0.4rem 1rem;
            }
        }
    """

    chart_html = fig.to_html(
        full_html=False,
        include_plotlyjs="cdn",
        config={"responsive": True},
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - Halvix</title>
    <style>
        {base_css}
        {header_css}
        {footer_css}
        {chart_css}
    </style>
</head>
<body>
    {header_html}

    <div class="chart-container">
        {chart_html}
    </div>

    {footer_html}
</body>
</html>
"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


def generate_pattern_analysis_page(
    btc_result: BTCPatternResult | None,
    top_coins: list[CoinPatternResult],
    output_path: Path,
) -> Path:
    """
    Generate the main pattern analysis HTML page with all charts.

    Args:
        btc_result: BTC pattern analysis result
        top_coins: List of top altcoin results (sorted by composite target)
        output_path: Path to save the main page (e.g., site/pattern_analysis.html)

    Returns:
        Path to the generated HTML file
    """
    base_css = _get_base_css()
    header_css = _get_header_css()
    footer_css = _get_footer_css()
    header_html = _get_header_html(back_link="index.html")
    footer_html = _get_footer_html()

    # Page-specific CSS
    page_css = """
        main {
            max-width: 1400px;
            margin: 0 auto;
            padding: 2rem;
        }

        h2 {
            font-size: 1.5rem;
            margin-bottom: 0.5rem;
            color: var(--accent-blue);
        }

        .description {
            color: var(--text-secondary);
            margin-bottom: 2rem;
            line-height: 1.6;
        }

        .summary-box {
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
            margin-bottom: 2rem;
        }

        .summary-title {
            font-size: 1.2rem;
            font-weight: 600;
            margin-bottom: 1rem;
            color: var(--text-primary);
        }

        .method-legend {
            display: flex;
            gap: 2rem;
            flex-wrap: wrap;
            margin-bottom: 1rem;
        }

        .method-item {
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        .method-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
        }

        .method-dot.trendline { background: #58a6ff; }
        .method-dot.fibonacci { background: #f0883e; }
        .method-dot.diminishing { background: #a371f7; }

        .charts-grid {
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
        }

        .chart-card {
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            overflow: hidden;
        }

        .chart-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 1rem 1.5rem;
            border-bottom: 1px solid var(--border-color);
        }

        .chart-title {
            font-size: 1.1rem;
            font-weight: 600;
            color: var(--text-primary);
        }

        .chart-badge {
            padding: 0.25rem 0.75rem;
            border-radius: 12px;
            font-size: 0.8rem;
            font-weight: 500;
        }

        .badge-high { background: rgba(63, 185, 80, 0.2); color: #3fb950; }
        .badge-medium { background: rgba(240, 136, 62, 0.2); color: #f0883e; }
        .badge-low { background: rgba(139, 148, 158, 0.2); color: #8b949e; }

        .chart-targets {
            display: flex;
            gap: 1.5rem;
            padding: 0.75rem 1.5rem;
            background: rgba(0,0,0,0.2);
            flex-wrap: wrap;
        }

        .target-item {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            font-size: 0.9rem;
        }

        .target-value {
            font-weight: 600;
        }

        .target-value.positive { color: #3fb950; }
        .target-value.negative { color: #f85149; }

        .chart-link {
            display: block;
            padding: 1rem 1.5rem;
            text-align: center;
            color: var(--accent-blue);
            text-decoration: none;
            border-top: 1px solid var(--border-color);
            transition: background 0.2s;
        }

        .chart-link:hover {
            background: rgba(88, 166, 255, 0.1);
        }

        .ranking-table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 1rem;
        }

        .ranking-table th,
        .ranking-table td {
            padding: 0.75rem;
            text-align: left;
            border-bottom: 1px solid var(--border-color);
        }

        .ranking-table th {
            background: rgba(0,0,0,0.2);
            font-weight: 600;
            color: var(--text-secondary);
            font-size: 0.85rem;
            text-transform: uppercase;
        }

        .ranking-table td.number {
            font-family: 'SF Mono', Consolas, monospace;
            text-align: right;
        }

        .ranking-table .coin-name {
            font-weight: 600;
            color: var(--accent-blue);
        }

        @media (max-width: 768px) {
            main {
                padding: 1rem;
            }

            .method-legend {
                flex-direction: column;
                gap: 0.5rem;
            }

            .chart-targets {
                flex-direction: column;
                gap: 0.5rem;
            }
        }
    """

    # Build ranking table rows
    table_rows = []
    for i, coin in enumerate(top_coins, 1):
        composite_class = "positive" if (coin.composite_target_pct or 0) > 0 else "negative"
        confidence_class = f"badge-{coin.confidence}"

        row = f"""
            <tr>
                <td>{i}</td>
                <td class="coin-name">{coin.coin_id.upper()}</td>
                <td><span class="chart-badge {confidence_class}">{coin.confidence.upper()}</span></td>
                <td class="number">{coin.num_cycles}</td>
                <td class="number target-value {composite_class}">+{coin.composite_target_pct:.1f}%</td>
                <td class="number">{f'+{coin.trendline_target_pct:.0f}%' if coin.trendline_target_pct else 'N/A'}</td>
                <td class="number">{f'+{coin.fib_target_pct:.0f}%' if coin.fib_target_pct else 'N/A'}</td>
                <td class="number">{f'+{coin.dim_return_target_pct:.0f}%' if coin.dim_return_target_pct else 'N/A'}</td>
                <td><a href="charts/pattern_{coin.coin_id}.html">View Chart</a></td>
            </tr>
        """
        table_rows.append(row)

    table_html = "\n".join(table_rows)

    # Build BTC summary
    btc_summary = ""
    if btc_result:
        btc_composite = btc_result.composite_target_pct or 0
        btc_summary = f"""
        <div class="chart-card">
            <div class="chart-header">
                <span class="chart-title">Bitcoin (BTC/USD)</span>
                <span class="chart-badge badge-high">BASELINE</span>
            </div>
            <div class="chart-targets">
                <div class="target-item">
                    <span class="method-dot trendline"></span>
                    <span>Trendline: </span>
                    <span class="target-value positive">{f'+{btc_result.trendline_target_pct:.0f}%' if btc_result.trendline_target_pct else 'N/A'}</span>
                </div>
                <div class="target-item">
                    <span class="method-dot fibonacci"></span>
                    <span>Fibonacci: </span>
                    <span class="target-value positive">{f'+{btc_result.fib_target_pct:.0f}%' if btc_result.fib_target_pct else 'N/A'}</span>
                </div>
                <div class="target-item">
                    <span class="method-dot diminishing"></span>
                    <span>Dim. Return: </span>
                    <span class="target-value positive">{f'+{btc_result.dim_return_target_pct:.0f}%' if btc_result.dim_return_target_pct else 'N/A'}</span>
                </div>
                <div class="target-item">
                    <strong>Composite: </strong>
                    <span class="target-value positive">+{btc_composite:.0f}%</span>
                </div>
            </div>
            <a href="charts/pattern_btc.html" class="chart-link">View Full Chart →</a>
        </div>
        """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cycle Pattern Analysis - Halvix</title>
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
        <h2>Cycle Pattern Analysis</h2>
        <p class="description">
            Analysis of price patterns across Bitcoin halving cycles (2020, 2024) with projections
            for cycle 5 (2028). Three methods are used to estimate targets: log-linear trendline
            regression, Fibonacci 127.2% extension, and diminishing returns model.
            The composite score is an equal-weight average of all available methods.
        </p>

        <div class="summary-box">
            <div class="summary-title">Analysis Methods</div>
            <div class="method-legend">
                <div class="method-item">
                    <span class="method-dot trendline"></span>
                    <span><strong>Trendline</strong>: Log-linear regression through cycle peaks</span>
                </div>
                <div class="method-item">
                    <span class="method-dot fibonacci"></span>
                    <span><strong>Fibonacci</strong>: 127.2% extension from previous cycle</span>
                </div>
                <div class="method-item">
                    <span class="method-dot diminishing"></span>
                    <span><strong>Dim. Return</strong>: Historical cycle gain decay factor</span>
                </div>
            </div>
        </div>

        <div class="charts-grid">
            {btc_summary}
        </div>

        <h2 style="margin-top: 2rem;">Top 9 Altcoins by Composite Target</h2>
        <p class="description">
            Altcoins ranked by their composite projected return for cycle 5.
            Confidence levels: HIGH (3+ cycles), MEDIUM (2 cycles), LOW (1 cycle).
        </p>

        <div class="table-container" style="overflow-x: auto;">
            <table class="ranking-table">
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Coin</th>
                        <th>Confidence</th>
                        <th>Cycles</th>
                        <th>Composite</th>
                        <th>Trendline</th>
                        <th>Fibonacci</th>
                        <th>Dim. Return</th>
                        <th>Chart</th>
                    </tr>
                </thead>
                <tbody>
                    {table_html}
                </tbody>
            </table>
        </div>
    </main>

    {footer_html}
</body>
</html>
"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return output_path


def generate_all_pattern_charts(
    output_dir: Path,
    top_n: int = 9,
    show_progress: bool = True,
) -> dict[str, Path]:
    """
    Generate all pattern analysis charts and the main page.

    Args:
        output_dir: Directory to save charts (e.g., site/)
        top_n: Number of top altcoins to include
        show_progress: Show progress bar

    Returns:
        Dictionary mapping chart name to file path
    """
    # Import here to avoid circular import - CyclePatternAnalyzer uses PriceDataCache
    from analysis.cycle_patterns import CyclePatternAnalyzer as Analyzer

    paths = {}

    # Initialize analyzer and cache
    price_cache = PriceDataCache()
    analyzer = Analyzer(price_cache=price_cache, min_cycles=1)

    # Analyze BTC
    btc_result = analyzer.analyze_btc()
    if btc_result:
        btc_chart_path = output_dir / "charts" / "pattern_btc.html"
        create_btc_pattern_chart(btc_result, price_cache, btc_chart_path)
        paths["btc"] = btc_chart_path

    # Analyze all altcoins
    coin_results = analyzer.analyze_all_coins(filter_total2=True, show_progress=show_progress)

    # Get top N
    top_coins = analyzer.get_top_coins(coin_results, n=top_n)

    # Generate chart for each top coin
    for coin in top_coins:
        chart_path = output_dir / "charts" / f"pattern_{coin.coin_id}.html"
        try:
            create_altcoin_pattern_chart(coin, price_cache, chart_path)
            paths[coin.coin_id] = chart_path
        except Exception as e:
            print(f"Warning: Could not generate chart for {coin.coin_id}: {e}")

    # Generate main page
    main_page_path = output_dir / "pattern_analysis.html"
    generate_pattern_analysis_page(btc_result, top_coins, main_page_path)
    paths["main"] = main_page_path

    # Save results JSON
    json_path = analyzer.save_results(btc_result, coin_results)
    paths["json"] = json_path

    return paths

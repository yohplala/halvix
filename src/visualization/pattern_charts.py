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
    CYCLE5_MIN1_APPROX_DAYS_BEFORE_HALVING,
    DAYS_AFTER_HALVING,
    DAYS_BEFORE_HALVING,
    HALVING_DATES,
    PROJECTED_5TH_HALVING,
)
from data.cache import PriceDataCache
from data.price_filters import detect_symbol_replacement
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
    5: "rgba(100, 160, 255, 1.0)",  # Cycle 5 (2028) - same as cycle 4
}


def _add_target_predictions(
    fig: go.Figure,
    targets: list[tuple[str, float, float, str]],
    target_date: date,
    is_btc: bool = False,
    pattern_type: str | None = None,
    composite_pct: float | None = None,
) -> None:
    """
    Add target prediction stars and text label to a chart.

    Uses go.Scatter() for proper positioning in log scale.
    Text is positioned at the average of min/max prices, with each line
    having its own color matching its star. Pattern type and composite
    are shown in grey below the targets.

    Args:
        fig: Plotly figure to add traces to
        targets: List of (label, target_price, target_pct, color) tuples
        target_date: Date to position the targets at
        is_btc: True for BTC/USD formatting, False for altcoin/BTC formatting
        pattern_type: Pattern type string (e.g., "higher_highs")
        composite_pct: Composite target percentage
    """
    if not targets:
        return

    # Sort targets by price (descending) - highest first
    targets_sorted = sorted(targets, key=lambda t: t[1], reverse=True)

    # Calculate text Y position: geometric mean of min and max prices (for log scale)
    min_price = min(t[1] for t in targets)
    max_price = max(t[1] for t in targets)
    text_y_position = (min_price * max_price) ** 0.5  # Geometric mean for log scale

    # Add star markers for all targets
    for label, target_price, target_pct, color in targets:
        if is_btc:
            price_fmt = f"${target_price:,.2f}"
        else:
            price_fmt = f"{target_price:.8f} BTC"

        fig.add_trace(
            go.Scatter(
                x=[target_date],
                y=[target_price],
                mode="markers",
                marker={"size": 12, "color": color, "symbol": "star"},
                name=f"Target: {label}",
                showlegend=False,
                hovertemplate=(
                    f"<b>{label} Target</b><br>"
                    f"Price: {price_fmt}<br>"
                    f"Gain: +{target_pct:.1f}%<br>"
                    f"Date: ~{target_date}"
                    "<extra></extra>"
                ),
            )
        )

    # Build list of all text lines: targets + pattern + composite
    text_lines: list[tuple[str, str]] = []  # (text, color)
    grey_color = "#8b949e"

    for label, target_price, target_pct, color in targets_sorted:
        if is_btc:
            price_k = target_price / 1000 if target_price >= 1000 else target_price
            price_str = f"${price_k:.0f}k" if target_price >= 1000 else f"${target_price:.2f}"
        else:
            price_str = f"+{target_pct:.0f}%"
        text_lines.append((f"{label}: {price_str}", color))

    # Add pattern type line in grey
    if pattern_type:
        pattern_display = pattern_type.replace("_", " ").title()
        text_lines.append((f"Pattern: {pattern_display}", grey_color))

    # Add composite line in grey
    if composite_pct is not None:
        text_lines.append((f"Composite: +{composite_pct:.0f}%", grey_color))

    # Add separate text trace for each line (each with its own color)
    # Use vertical spacing in log scale (multiply/divide by factor)
    num_lines = len(text_lines)
    line_spacing_factor = 1.32  # Spacing between lines in log scale

    for i, (text_label, color) in enumerate(text_lines):
        # Calculate Y position for this line (offset from center in log space)
        # Center the lines around text_y_position
        center_offset = (num_lines - 1) / 2
        line_y = text_y_position * (line_spacing_factor ** (center_offset - i))

        fig.add_trace(
            go.Scatter(
                x=[target_date + timedelta(days=45)],  # Shift right to avoid star overlap
                y=[line_y],
                mode="text",
                text=[text_label],
                textposition="middle right",
                textfont={"size": 13, "color": color},
                showlegend=False,
                hoverinfo="skip",
            )
        )


def _get_cycle5_min1_display_date() -> date:
    """
    Get the approximated date for displaying cycle 5 min1 on charts.

    This uses the trendline regression date rather than the actual detected date,
    providing a stable position for the current cycle minimum point.

    Returns:
        The approximated date for cycle 5 min1 (520 days before 5th halving)
    """
    return PROJECTED_5TH_HALVING - timedelta(days=CYCLE5_MIN1_APPROX_DAYS_BEFORE_HALVING)


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
    # Add extra 300 days to accommodate prediction text labels on the right
    end = PROJECTED_5TH_HALVING + timedelta(days=DAYS_AFTER_HALVING + 300)

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
    # Filter out points with price 0 and group by cycle
    valid_points = [p for p in result.points if p.price > 0]
    cycles = sorted({p.cycle_num for p in valid_points})

    # Get cycle 5 approximated date for display
    cycle5_display_date = _get_cycle5_min1_display_date()

    # Build index of max2 and min1 points by cycle for inter-cycle connections
    max2_by_cycle = {}
    min1_by_cycle = {}
    for p in valid_points:
        if p.point_type == "max2":
            max2_by_cycle[p.cycle_num] = p
        elif p.point_type == "min1":
            min1_by_cycle[p.cycle_num] = p

    for cycle_num in cycles:
        cycle_points = sorted(
            [p for p in valid_points if p.cycle_num == cycle_num],
            key=lambda x: x.date,
        )

        if not cycle_points:
            continue

        # For cycle 5, use approximated date for min1
        if cycle_num == 5:
            x_vals = [
                cycle5_display_date if p.point_type == "min1" else p.date for p in cycle_points
            ]
        else:
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
        for i, p in enumerate(cycle_points):
            display_date = x_vals[i]
            fig.add_trace(
                go.Scatter(
                    x=[display_date],
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
                        f"Date: {display_date}<br>"
                        f"Price: ${p.price:,.2f}<br>"
                        f"Days from halving: {p.days_from_halving:+d}"
                        "<extra></extra>"
                    ),
                )
            )

    # 3. Connect max2 of each cycle to min1 of the next cycle
    for i, cycle_num in enumerate(cycles[:-1]):
        next_cycle = cycles[i + 1]
        prev_max2 = max2_by_cycle.get(cycle_num)
        next_min1 = min1_by_cycle.get(next_cycle)

        if prev_max2 and next_min1:
            # Use approximated date for cycle 5 min1
            next_min1_date = cycle5_display_date if next_cycle == 5 else next_min1.date

            fig.add_trace(
                go.Scatter(
                    x=[prev_max2.date, next_min1_date],
                    y=[prev_max2.price, next_min1.price],
                    mode="lines",
                    name=f"Cycle {next_cycle}",
                    line={"color": CYCLE_COLORS.get(next_cycle, "#888"), "width": 2.5},
                    showlegend=False,
                )
            )

    # 3. Add target predictions (stars + text label)
    target_date = PROJECTED_5TH_HALVING + timedelta(days=550)

    targets = []
    if result.trendline_target:
        targets.append(
            (
                "Trendline",
                result.trendline_target,
                result.trendline_target_pct,
                TARGET_COLORS["trendline"],
            )
        )
    if result.fib_target:
        targets.append(
            ("Fib 127.2%", result.fib_target, result.fib_target_pct, TARGET_COLORS["fibonacci"])
        )
    if result.dim_return_target:
        targets.append(
            (
                "Dim. Return",
                result.dim_return_target,
                result.dim_return_target_pct,
                TARGET_COLORS["diminishing"],
            )
        )

    _add_target_predictions(
        fig,
        targets,
        target_date,
        is_btc=True,
        pattern_type=result.pattern_type,
        composite_pct=result.composite_target_pct,
    )

    # Add halving lines
    _add_halving_lines(fig)

    # Layout
    fig.update_layout(
        title={
            "text": "#0 - Bitcoin (BTC/USD) - Cycle Pattern Analysis",
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
            "font": {"size": 14},
        },
        template="plotly_dark",
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0d1117",
        hovermode="x unified",
        height=600,
        margin={"t": 80, "b": 60, "r": 180},
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

    # Apply symbol replacement detection (same as cycle_patterns.py analysis)
    # This ensures the chart shows the same data that was analyzed
    if "close" in coin_df.columns:
        replacement_date = detect_symbol_replacement(coin_df["close"])
        if replacement_date is not None:
            logger.info(
                "%s: Filtering chart to post-symbol-replacement data from %s",
                result.coin_id.upper(),
                replacement_date.date(),
            )
            coin_df = coin_df[coin_df.index >= replacement_date]

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
    # Filter out points with price 0 and group by cycle
    valid_points = [p for p in result.points if p.price > 0]
    cycles = sorted({p.cycle_num for p in valid_points})

    # Get cycle 5 approximated date for display
    cycle5_display_date = _get_cycle5_min1_display_date()

    # Build index of max2 and min1 points by cycle for inter-cycle connections
    max2_by_cycle = {}
    min1_by_cycle = {}
    # Track which cycles have only min1 (current cycle)
    current_cycle_num = None
    for p in valid_points:
        if p.point_type == "max2":
            max2_by_cycle[p.cycle_num] = p
        elif p.point_type == "min1":
            min1_by_cycle[p.cycle_num] = p

    # Determine if the max cycle is the current cycle (has only min1)
    max_cycle = max(cycles) if cycles else 0
    max_cycle_points = [p for p in valid_points if p.cycle_num == max_cycle]
    if max_cycle_points and all(p.point_type == "min1" for p in max_cycle_points):
        current_cycle_num = max_cycle

    for cycle_num in cycles:
        cycle_points = sorted(
            [p for p in valid_points if p.cycle_num == cycle_num],
            key=lambda x: x.date,
        )

        if not cycle_points:
            continue

        # For current cycle (only min1), use approximated date for min1
        if cycle_num == current_cycle_num:
            x_vals = [
                cycle5_display_date if p.point_type == "min1" else p.date for p in cycle_points
            ]
        else:
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
        for i, p in enumerate(cycle_points):
            display_date = x_vals[i]
            fig.add_trace(
                go.Scatter(
                    x=[display_date],
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
                        f"Date: {display_date}<br>"
                        f"Price: {p.price:.8f} BTC<br>"
                        f"Days from halving: {p.days_from_halving:+d}"
                        "<extra></extra>"
                    ),
                )
            )

    # 3. Connect max2 of each cycle to min1 of the next cycle
    for i, cycle_num in enumerate(cycles[:-1]):
        next_cycle = cycles[i + 1]
        prev_max2 = max2_by_cycle.get(cycle_num)
        next_min1 = min1_by_cycle.get(next_cycle)

        if prev_max2 and next_min1:
            # Use approximated date for current cycle min1
            next_min1_date = (
                cycle5_display_date if next_cycle == current_cycle_num else next_min1.date
            )

            fig.add_trace(
                go.Scatter(
                    x=[prev_max2.date, next_min1_date],
                    y=[prev_max2.price, next_min1.price],
                    mode="lines",
                    name=f"Cycle {next_cycle}",
                    line={"color": CYCLE_COLORS.get(next_cycle, "#888"), "width": 2.5},
                    showlegend=False,
                )
            )

    # 4. Add target predictions (stars + text label)
    target_date = PROJECTED_5TH_HALVING + timedelta(days=550)

    targets = []
    if result.trendline_target:
        targets.append(
            (
                "Trendline",
                result.trendline_target,
                result.trendline_target_pct,
                TARGET_COLORS["trendline"],
            )
        )
    if result.fib_target:
        targets.append(
            ("Fib 127.2%", result.fib_target, result.fib_target_pct, TARGET_COLORS["fibonacci"])
        )
    if result.dim_return_target:
        targets.append(
            (
                "Dim. Return",
                result.dim_return_target,
                result.dim_return_target_pct,
                TARGET_COLORS["diminishing"],
            )
        )

    _add_target_predictions(
        fig,
        targets,
        target_date,
        is_btc=False,
        pattern_type=result.pattern_type,
        composite_pct=result.composite_target_pct,
    )

    # Add halving lines
    _add_halving_lines(fig)

    # Layout
    confidence_badge = f"[{result.confidence.upper()}]" if result.confidence else ""
    rank_prefix = f"#{result.rank} - " if result.rank is not None else ""
    fig.update_layout(
        title={
            "text": f"{rank_prefix}{result.coin_id.upper()}/BTC - Cycle Pattern Analysis {confidence_badge}",
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
            "font": {"size": 14},
        },
        template="plotly_dark",
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0d1117",
        hovermode="x unified",
        height=600,
        margin={"t": 80, "b": 60, "r": 180},
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
    price_cache: PriceDataCache | None = None,
) -> Path:
    """
    Generate the main pattern analysis HTML page with all charts embedded.

    Args:
        btc_result: BTC pattern analysis result
        top_coins: List of top altcoin results (sorted by composite target)
        output_path: Path to save the main page (e.g., site/pattern_analysis.html)
        price_cache: Price data cache for generating embedded charts

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
            max-width: 1600px;
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

        .chart-badge {
            padding: 0.25rem 0.75rem;
            border-radius: 12px;
            font-size: 0.8rem;
            font-weight: 500;
        }

        .badge-high { background: rgba(63, 185, 80, 0.2); color: #3fb950; }
        .badge-medium { background: rgba(240, 136, 62, 0.2); color: #f0883e; }
        .badge-low { background: rgba(139, 148, 158, 0.2); color: #8b949e; }

        .target-value {
            font-weight: 600;
        }

        .target-value.positive { color: #3fb950; }
        .target-value.negative { color: #f85149; }

        .ranking-table {
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 2rem;
        }

        .ranking-table th,
        .ranking-table td {
            padding: 0.75rem;
            text-align: center;
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
            text-align: center;
        }

        .ranking-table .coin-name {
            font-weight: 600;
            color: var(--accent-blue);
        }

        .ranking-table .pair-type {
            color: var(--text-secondary);
            font-size: 0.8rem;
        }

        .ranking-table tr.btc-row {
            background: rgba(247, 147, 26, 0.1);
        }

        .ranking-table tr.btc-row .coin-name {
            color: var(--accent-orange);
        }

        .charts-container {
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }

        .chart-wrapper {
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 0.5rem;
        }

        @media (max-width: 768px) {
            main {
                padding: 1rem;
            }
        }
    """

    # Build ranking table rows - BTC first, then alts
    table_rows = []

    # Add BTC as first row
    if btc_result:
        btc_composite = btc_result.composite_target_pct or 0
        composite_class = "positive" if btc_composite > 0 else "negative"
        btc_row = f"""
            <tr class="btc-row">
                <td>0</td>
                <td class="coin-name">BTC <span class="pair-type">(/USD)</span></td>
                <td><span class="chart-badge badge-high">HIGH</span></td>
                <td class="number">{btc_result.num_cycles}</td>
                <td class="number target-value {composite_class}">+{btc_composite:.1f}%</td>
                <td class="number">{f'+{btc_result.trendline_target_pct:.0f}%' if btc_result.trendline_target_pct else 'N/A'}</td>
                <td class="number">{f'+{btc_result.fib_target_pct:.0f}%' if btc_result.fib_target_pct else 'N/A'}</td>
                <td class="number">{f'+{btc_result.dim_return_target_pct:.0f}%' if btc_result.dim_return_target_pct else 'N/A'}</td>
            </tr>
        """
        table_rows.append(btc_row)

    # Add altcoins (using coin.rank which was set after sorting by composite target)
    for coin in top_coins:
        composite_class = "positive" if (coin.composite_target_pct or 0) > 0 else "negative"
        confidence_class = f"badge-{coin.confidence}"
        rank_display = coin.rank if coin.rank is not None else "-"

        row = f"""
            <tr>
                <td>{rank_display}</td>
                <td class="coin-name">{coin.coin_id.upper()} <span class="pair-type">(/BTC)</span></td>
                <td><span class="chart-badge {confidence_class}">{coin.confidence.upper()}</span></td>
                <td class="number">{coin.num_cycles}</td>
                <td class="number target-value {composite_class}">+{coin.composite_target_pct:.1f}%</td>
                <td class="number">{f'+{coin.trendline_target_pct:.0f}%' if coin.trendline_target_pct else 'N/A'}</td>
                <td class="number">{f'+{coin.fib_target_pct:.0f}%' if coin.fib_target_pct else 'N/A'}</td>
                <td class="number">{f'+{coin.dim_return_target_pct:.0f}%' if coin.dim_return_target_pct else 'N/A'}</td>
            </tr>
        """
        table_rows.append(row)

    table_html = "\n".join(table_rows)

    # Build embedded charts - BTC first, then alts in order of composite
    charts_html = ""

    if price_cache and btc_result:
        try:
            btc_fig = create_btc_pattern_chart(btc_result, price_cache)
            btc_chart_html = btc_fig.to_html(
                full_html=False,
                include_plotlyjs="cdn",
                config={"responsive": True},
            )
            charts_html += f"""
            <div class="chart-wrapper">
                {btc_chart_html}
            </div>
            """
        except Exception as e:
            logger.warning("Could not generate embedded BTC chart: %s", e)

    if price_cache:
        for coin in top_coins:
            try:
                coin_fig = create_altcoin_pattern_chart(coin, price_cache)
                coin_chart_html = coin_fig.to_html(
                    full_html=False,
                    include_plotlyjs=False,  # Already included from BTC chart
                    config={"responsive": True},
                )
                charts_html += f"""
                <div class="chart-wrapper">
                    {coin_chart_html}
                </div>
                """
            except Exception as e:
                logger.warning("Could not generate embedded chart for %s: %s", coin.coin_id, e)

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
            <strong>Ranking is by composite score (descending).</strong> Coins with negative
            trendline predictions are filtered out (underperforming BTC).
            The composite score is an equal-weight average of all available methods.
        </p>

        <div class="table-container" style="overflow-x: auto;">
            <table class="ranking-table">
                <thead>
                    <tr>
                        <th>Rank</th>
                        <th>Coin</th>
                        <th>Confidence</th>
                        <th>Cycles</th>
                        <th>Composite</th>
                        <th>Trendline</th>
                        <th>Fibonacci</th>
                        <th>Dim. Return</th>
                    </tr>
                </thead>
                <tbody>
                    {table_html}
                </tbody>
            </table>
        </div>

        <h2>Charts</h2>
        <div class="charts-container">
            {charts_html}
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

    # Get top N (filtered to positive trendline predictions, sorted by composite target)
    top_coins = analyzer.get_top_coins(coin_results, n=top_n)

    # Set rank for each coin (1-indexed, based on composite score)
    for i, coin in enumerate(top_coins, 1):
        coin.rank = i

    # Generate chart for each top coin
    for coin in top_coins:
        chart_path = output_dir / "charts" / f"pattern_{coin.coin_id}.html"
        try:
            create_altcoin_pattern_chart(coin, price_cache, chart_path)
            paths[coin.coin_id] = chart_path
        except Exception as e:
            logger.warning("Could not generate chart for %s: %s", coin.coin_id, e)

    # Generate main page with embedded charts
    main_page_path = output_dir / "pattern_analysis.html"
    generate_pattern_analysis_page(btc_result, top_coins, main_page_path, price_cache)
    paths["main"] = main_page_path

    # Save results JSON
    json_path = analyzer.save_results(btc_result, coin_results)
    paths["json"] = json_path

    return paths

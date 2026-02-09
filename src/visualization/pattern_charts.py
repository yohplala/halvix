"""
Pattern Analysis Charts for Halvix.

Generates HTML pages with cycle pattern analysis charts showing:
- Full price curve in light grey
- Dashed upper/lower trendlines
- Min/max points with solid lines
- Target projections from 4 methods (trendline, fibonacci, diminishing, historical peak)

All charts use the same time scale (cycles 3, 4, and projected 5).
"""

import math
from datetime import date, timedelta
from pathlib import Path

import plotly.graph_objects as go

from analysis.cycle_patterns import CoinPatternResult
from config import (
    CURRENT_CYCLE_MIN1_APPROX_DAYS_BEFORE_HALVING,
    DAYS_AFTER_HALVING,
    DAYS_BEFORE_HALVING,
    HALVING_DATES,
    PATTERN_ANALYSIS_TOP_N,
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

# Background curve color (nearly white)
CURVE_COLOR = "rgba(245, 245, 245, 0.7)"

# Trendline and reference line color (darker grey for visibility)
TRENDLINE_COLOR = "rgba(160, 160, 160, 0.7)"

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
    "historical": "#3fb950",  # Green (matches max2 point color)
}

# Cycle colors (matching charts.py)
CYCLE_COLORS = {
    2: "rgba(130, 225, 215, 0.9)",  # Cycle 2 (2016) - pale teal
    3: "rgba(90, 175, 255, 0.95)",  # Cycle 3 (2020) - sky blue
    4: "rgba(170, 150, 255, 1.0)",  # Cycle 4 (2024) - lavender
    5: "rgba(70, 200, 240, 1.0)",  # Cycle 5 (2028) - bright cyan
}


def _format_pct(value: float, decimals: int = 0) -> str:
    """
    Format a percentage value with proper sign handling.

    Positive values get a '+' prefix, negative values get '-' (no double sign).

    Args:
        value: The percentage value
        decimals: Number of decimal places (default: 0)

    Returns:
        Formatted string like "+42%" or "-15%"
    """
    if decimals == 0:
        return f"{value:+.0f}%"
    else:
        return f"{value:+.{decimals}f}%"


def _add_target_predictions(
    fig: go.Figure,
    targets: list[tuple[str, float, float, str]],
    target_date: date,
    is_btc: bool = False,
    composite_pct: float | None = None,
) -> None:
    """
    Add target prediction stars and text label to a chart.

    Stars are positioned at the target date/price. Text labels are displayed
    in the bottom right corner of the chart, starting a few days after the
    2028 halving.

    Args:
        fig: Plotly figure to add traces to
        targets: List of (label, target_price, target_pct, color) tuples
        target_date: Date to position the star markers at
        is_btc: True for BTC/USD formatting, False for altcoin/BTC formatting
        composite_pct: Composite target percentage
    """
    if not targets:
        return

    # Sort targets by price (descending) - highest first
    targets_sorted = sorted(targets, key=lambda t: t[1], reverse=True)

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
                    f"Gain: {_format_pct(target_pct, 1)}"
                    "<extra></extra>"
                ),
            )
        )

    # Build list of all text lines: composite first, then targets (same order as stars)
    text_lines: list[tuple[str, str]] = []  # (text, color)
    grey_color = "#8b949e"

    # Add composite line first (at the top)
    if composite_pct is not None:
        text_lines.append((f"Composite: {_format_pct(composite_pct)}", grey_color))

    # Add targets in same order as stars (by price descending)
    for label, target_price, target_pct, color in targets_sorted:
        if is_btc:
            price_k = target_price / 1000 if target_price >= 1000 else target_price
            price_str = f"${price_k:.0f}k" if target_price >= 1000 else f"${target_price:.2f}"
        else:
            price_str = _format_pct(target_pct)
        text_lines.append((f"{label}: {price_str}", color))

    # Add text annotations at the bottom, left of the 5th halving vertical line
    text_x_date = HALVING_DATES[-1] - timedelta(days=30)
    num_lines = len(text_lines)
    line_spacing = 0.035  # Vertical spacing in paper coordinates

    for i, (text_label, color) in enumerate(text_lines):
        # Y position from bottom up (0.05 base, increasing for each line)
        y_paper = 0.05 + (num_lines - 1 - i) * line_spacing

        fig.add_annotation(
            x=text_x_date,
            y=y_paper,
            xref="x",
            yref="paper",
            text=text_label,
            showarrow=False,
            font={"size": 13, "color": color},
            xanchor="right",
            yanchor="middle",
        )


def _add_trendlines(
    fig: go.Figure,
    upper_slope: float | None,
    upper_intercept: float | None,
    lower_slope: float | None,
    lower_intercept: float | None,
    start_date: date,
    end_date: date,
) -> None:
    """
    Add dashed upper and lower trendlines to a chart.

    Draws log-linear trendlines using the fitted slopes and intercepts.
    Uses the same color as the price curve with dashed style.

    Args:
        fig: Plotly figure to add traces to
        upper_slope: Slope of upper trendline (log scale)
        upper_intercept: Y-intercept of upper trendline (log scale)
        lower_slope: Slope of lower trendline (log scale)
        lower_intercept: Y-intercept of lower trendline (log scale)
        start_date: Chart start date
        end_date: Chart end date
    """
    if upper_slope is None or upper_intercept is None:
        return
    if lower_slope is None or lower_intercept is None:
        return

    # Reference date for x-axis (same as in cycle_patterns.py)
    reference_date = HALVING_DATES[1]  # 2016-07-09

    # Calculate x values (days from reference)
    x_start_days = (start_date - reference_date).days
    x_end_days = (end_date - reference_date).days

    # Calculate y values (log scale, then convert back)
    # Guard against overflow
    try:
        upper_y_start = 10 ** (upper_slope * x_start_days + upper_intercept)
        upper_y_end = 10 ** (upper_slope * x_end_days + upper_intercept)
        lower_y_start = 10 ** (lower_slope * x_start_days + lower_intercept)
        lower_y_end = 10 ** (lower_slope * x_end_days + lower_intercept)
    except (OverflowError, ValueError):
        return

    # Draw upper trendline
    fig.add_trace(
        go.Scatter(
            x=[start_date, end_date],
            y=[upper_y_start, upper_y_end],
            mode="lines",
            name="Upper Trendline",
            line={"color": TARGET_COLORS["trendline"], "width": 1, "dash": "dash"},
            showlegend=False,
            hoverinfo="skip",
        )
    )

    # Draw lower trendline
    fig.add_trace(
        go.Scatter(
            x=[start_date, end_date],
            y=[lower_y_start, lower_y_end],
            mode="lines",
            name="Lower Trendline",
            line={"color": TRENDLINE_COLOR, "width": 1, "dash": "dot"},
            showlegend=False,
            hoverinfo="skip",
        )
    )


def _add_historical_peak_line(
    fig: go.Figure,
    hist_peak_target: float,
    start_date: date,
    target_date: date,
) -> None:
    """
    Add horizontal dashed line at the historical peak level, from start_date to target_date.

    Args:
        fig: Plotly figure to add the line to
        hist_peak_target: Price level for the horizontal line
        start_date: Left edge of the line
        target_date: Right edge of the line (where the star is)
    """
    fig.add_trace(
        go.Scatter(
            x=[start_date, target_date],
            y=[hist_peak_target, hist_peak_target],
            mode="lines",
            name="Hist. Peak Level",
            line={"color": TARGET_COLORS["historical"], "width": 1, "dash": "dash"},
            showlegend=False,
            hoverinfo="skip",
        )
    )


def _add_fib_hint_lines(
    fig: go.Figure,
    result: CoinPatternResult,
    target_date: date,
    cycle5_display_date: date,
    current_cycle_num: int | None,
    idx: dict[tuple[int, str], list],
    cycles: list[int],
) -> None:
    """
    Draw dashed thin orange line connecting Fib A->B->C extrema.

    Reconstructs the same A, B, C points used by _calculate_fib_extension:
    A = previous cycle min (prefer min1, fallback min2)
    B = previous cycle max2
    C = current cycle min1

    A, B, C are shifted slightly downward to avoid visual overlap with
    the diminishing-returns hint lines; the target (★) stays at true price.
    """
    if result.fib_target is None:
        return

    if len(cycles) < 2:
        return

    latest_cycle = max(cycles)
    prev_cycle = max(c for c in cycles if c < latest_cycle)

    # A = prev cycle min1, fallback min2
    a_point = None
    for pt in ("min1", "min2"):
        pts = idx.get((prev_cycle, pt), [])
        if pts:
            a_point = pts[0]
            break

    # B = prev cycle max2
    b_pts = idx.get((prev_cycle, "max2"), [])
    b_point = b_pts[0] if b_pts else None

    # C = latest cycle min1
    c_pts = idx.get((latest_cycle, "min1"), [])
    c_point = c_pts[0] if c_pts else None

    if not (a_point and b_point and c_point):
        return

    # Handle cycle 5 display date for C
    c_date = c_point.date
    if current_cycle_num and latest_cycle == current_cycle_num:
        c_date = cycle5_display_date

    # Shift A, B, C down slightly (log-scale) to separate from dim-return lines.
    # ★ (target) stays at true price.
    fib_y_shift = 0.90
    fig.add_trace(
        go.Scatter(
            x=[a_point.date, b_point.date, c_date, target_date],
            y=[
                a_point.price * fib_y_shift,
                b_point.price * fib_y_shift,
                c_point.price * fib_y_shift,
                result.fib_target,
            ],
            mode="lines",
            name="Fib A→B→C→★",
            line={"color": TARGET_COLORS["fibonacci"], "width": 1.5, "dash": "dash"},
            showlegend=False,
            hoverinfo="skip",
        )
    )


def _add_dim_return_hint_lines(
    fig: go.Figure,
    result: CoinPatternResult,
    target_date: date,
    cycle5_display_date: date,
    current_cycle_num: int | None,
    idx: dict[tuple[int, str], list],
    cycles: list[int],
) -> None:
    """
    Draw dotted purple lines connecting min-max pairs for diminishing returns.

    For each cycle with both min and max points, draws a vertical-ish line
    from the lowest min to the highest max. Also draws a projection line
    from the latest min point to the star (projected target).
    """
    if result.dim_return_target is None:
        return

    valid_points = [p for p in result.points if p.price > 0]

    dim_color = TARGET_COLORS["diminishing"]

    # Draw min-max pair lines for each cycle that has both
    # Prefer major types (min1, max2); fallback to minor (min2, max1)
    for cycle in cycles:
        min_points = idx.get((cycle, "min1"), [])
        if not min_points:
            min_points = idx.get((cycle, "min2"), [])
        max_points = idx.get((cycle, "max2"), [])
        if not max_points:
            max_points = idx.get((cycle, "max1"), [])

        if min_points and max_points:
            min_p = min(min_points, key=lambda p: p.price)
            max_p = max(max_points, key=lambda p: p.price)

            min_date = min_p.date
            max_date = max_p.date
            if current_cycle_num and cycle == current_cycle_num:
                if min_p.point_type == "min1":
                    min_date = cycle5_display_date

            fig.add_trace(
                go.Scatter(
                    x=[min_date, max_date],
                    y=[min_p.price, max_p.price],
                    mode="lines",
                    name="Dim. Return pair",
                    line={"color": dim_color, "width": 1, "dash": "dash"},
                    showlegend=False,
                    hoverinfo="skip",
                )
            )

    # Connect latest min to the projected star
    latest_min = None
    for p in sorted(valid_points, key=lambda x: x.date, reverse=True):
        if "min" in p.point_type:
            latest_min = p
            break

    if latest_min:
        min_date = latest_min.date
        if (
            current_cycle_num
            and latest_min.cycle_num == current_cycle_num
            and latest_min.point_type == "min1"
        ):
            min_date = cycle5_display_date

        fig.add_trace(
            go.Scatter(
                x=[min_date, target_date],
                y=[latest_min.price, result.dim_return_target],
                mode="lines",
                name="Dim. Return proj.",
                line={"color": dim_color, "width": 1, "dash": "dash"},
                showlegend=False,
                hoverinfo="skip",
            )
        )


def _calculate_y_axis_range(
    price_series: list[float],
    point_prices: list[float],
    target_prices: list[float],
    hist_peak: float | None = None,
    padding: float = 0.2,
) -> list[float] | None:
    """
    Calculate y-axis range for log-scale charts based on actual data.

    This prevents trendlines extrapolated to empty regions (e.g., backwards to 2020
    for coins that didn't exist) from stretching the axis to extreme values like 1 BTC.

    IMPORTANT: For Plotly log scale, the range must be specified in exponents (powers of 10),
    not actual values. For example, to set range from 0.00001 to 0.01:
    - Actual values: [0.00001, 0.01]
    - Exponents: [log10(0.00001), log10(0.01)] = [-5, -2]

    See: https://plotly.com/python/log-plot/

    Args:
        price_series: List of prices from the price curve (e.g., df["close"])
        point_prices: List of prices from min/max point markers
        target_prices: List of prices from target star markers
        hist_peak: Historical peak price (optional)
        padding: Padding in decades (log10 units) above and below. Default 0.3 (~2x margin)

    Returns:
        List of [min_exponent, max_exponent] for Plotly yaxis.range, or None if no valid data
    """
    # Collect all positive y values from visible data elements
    y_values = [v for v in price_series if v > 0]
    y_values.extend([p for p in point_prices if p > 0])
    y_values.extend([t for t in target_prices if t > 0])
    if hist_peak and hist_peak > 0:
        y_values.append(hist_peak)

    if not y_values:
        return None

    y_min = min(y_values)
    y_max = max(y_values)

    # Return range in log10 exponents with padding
    return [math.log10(y_min) - padding, math.log10(y_max) + padding]


def _get_cycle5_min1_display_date() -> date:
    """
    Get the approximated date for displaying cycle 5 min1 on charts.

    This uses the trendline regression date rather than the actual detected date,
    providing a stable position for the current cycle minimum point.

    Returns:
        The approximated date for cycle 5 min1 (520 days before 5th halving)
    """
    return HALVING_DATES[-1] - timedelta(days=CURRENT_CYCLE_MIN1_APPROX_DAYS_BEFORE_HALVING)


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
    end = HALVING_DATES[-1] + timedelta(days=DAYS_AFTER_HALVING)

    return start, end


def _add_halving_lines(fig: go.Figure, row: int = 1, col: int = 1) -> None:
    """Add vertical lines at halving dates."""
    for halving_date in (HALVING_DATES[2], HALVING_DATES[3], HALVING_DATES[-1]):
        fig.add_vline(
            x=halving_date,
            line={"dash": "dot", "color": "rgba(200,200,200,0.4)", "width": 1.5},
            row=row,
            col=col,
        )


def create_btc_pattern_chart(
    result: CoinPatternResult,
    price_cache: PriceDataCache,
    output_path: Path | None = None,
) -> go.Figure:
    """Create BTC/USD pattern analysis chart."""
    return _create_pattern_chart(result, price_cache, is_btc=True, output_path=output_path)


def create_altcoin_pattern_chart(
    result: CoinPatternResult,
    price_cache: PriceDataCache,
    output_path: Path | None = None,
) -> go.Figure:
    """Create altcoin/BTC pattern analysis chart."""
    return _create_pattern_chart(result, price_cache, is_btc=False, output_path=output_path)


def _create_pattern_chart(
    result: CoinPatternResult,
    price_cache: PriceDataCache,
    is_btc: bool,
    output_path: Path | None = None,
) -> go.Figure:
    """
    Create pattern analysis chart for BTC or an altcoin.

    Args:
        result: Coin pattern analysis result
        price_cache: Price data cache
        is_btc: True for BTC/USD chart, False for altcoin/BTC chart
        output_path: Optional path to save HTML

    Returns:
        Plotly Figure
    """
    # Set format parameters based on asset type
    if is_btc:
        pair_label = "BTC/USD"
        hover_price_fmt = "<b>BTC/USD</b><br>Price: $%{y:,.2f}<extra></extra>"
        marker_price_tmpl = "Price: ${price:,.2f}"
        yaxis_title = "Price (USD)"
    else:
        pair_label = f"{result.coin_id.upper()}/BTC"
        hover_price_fmt = (
            f"<b>{result.coin_id.upper()}/BTC</b><br>Price: %{{y:.8f}} BTC<extra></extra>"
        )
        marker_price_tmpl = "Price: {price:.8f} BTC"
        yaxis_title = "Price (BTC)"

    # Load price data
    if is_btc:
        price_df = price_cache.get_prices("btc", "USD")
        if price_df is None or price_df.empty:
            raise ValueError("BTC-USD data not available")
    else:
        price_df = price_cache.get_prices(result.coin_id, "BTC")
        if price_df is None or price_df.empty:
            raise ValueError(f"{pair_label} data not available")
        # Apply symbol replacement detection (same filtering as analysis)
        if "close" in price_df.columns:
            replacement_date = detect_symbol_replacement(price_df["close"])
            if replacement_date is not None:
                logger.info(
                    "Symbol replacement detected for %s at %s, filtering chart data",
                    result.coin_id,
                    replacement_date,
                )
                price_df = price_df[price_df.index >= replacement_date]

    # Filter to time range
    start_date, end_date = _get_time_range()
    mask = (price_df.index.date >= start_date) & (price_df.index.date <= end_date)
    plot_df = price_df[mask]

    fig = go.Figure()

    # 1. Full price curve in light grey
    fig.add_trace(
        go.Scatter(
            x=plot_df.index,
            y=plot_df["close"],
            mode="lines",
            name=pair_label,
            line={"color": CURVE_COLOR, "width": 1},
            hovertemplate=hover_price_fmt,
        )
    )

    # 1b. Add dashed trendlines (stop at target_date, not chart edge)
    target_date = HALVING_DATES[-1] + timedelta(days=550)
    _add_trendlines(
        fig,
        result.upper_slope,
        result.upper_intercept,
        result.lower_slope,
        result.lower_intercept,
        start_date,
        target_date,
    )

    # 2. Add min/max points with connecting lines
    # Filter out points with price 0 and group by cycle
    valid_points = [p for p in result.points if p.price > 0]
    cycles = sorted({p.cycle_num for p in valid_points})

    # Get cycle 5 approximated date for display
    cycle5_display_date = _get_cycle5_min1_display_date()

    # Build points index once for all chart helpers
    idx: dict[tuple[int, str], list] = {}
    for p in valid_points:
        idx.setdefault((p.cycle_num, p.point_type), []).append(p)

    # Derive max2/min1 lookups from the index for inter-cycle connections
    max2_by_cycle = {k[0]: pts[0] for k, pts in idx.items() if k[1] == "max2"}
    min1_by_cycle = {k[0]: pts[0] for k, pts in idx.items() if k[1] == "min1"}
    current_cycle_num = None

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
                line={"color": CYCLE_COLORS.get(cycle_num, "#888"), "width": 1, "dash": "dot"},
                showlegend=True,
                hoverinfo="skip",
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
                        f"<b>{p.point_type.upper()} (Cycle {p.cycle_num})</b><br>"
                        f"{marker_price_tmpl.format(price=p.price)}<br>"
                        f"Days from halving: {p.days_from_halving:+d}"
                        "<extra></extra>"
                    ),
                )
            )

    # 3. Connect cycles: max2 → min1, with fallback to last→first point
    for i, cycle_num in enumerate(cycles[:-1]):
        next_cycle = cycles[i + 1]
        prev_max2 = max2_by_cycle.get(cycle_num)
        next_min1 = min1_by_cycle.get(next_cycle)

        if prev_max2 and next_min1:
            # Standard bridge: max2 → min1
            next_min1_date = (
                cycle5_display_date if next_cycle == current_cycle_num else next_min1.date
            )

            fig.add_trace(
                go.Scatter(
                    x=[prev_max2.date, next_min1_date],
                    y=[prev_max2.price, next_min1.price],
                    mode="lines",
                    name=f"Cycle {next_cycle}",
                    line={
                        "color": CYCLE_COLORS.get(next_cycle, "#888"),
                        "width": 1,
                        "dash": "dot",
                    },
                    showlegend=False,
                    hoverinfo="skip",
                )
            )
        else:
            # Fallback bridge: last point of current cycle → first of next
            curr_pts = sorted(
                [p for p in valid_points if p.cycle_num == cycle_num],
                key=lambda x: x.date,
            )
            next_pts = sorted(
                [p for p in valid_points if p.cycle_num == next_cycle],
                key=lambda x: x.date,
            )
            if curr_pts and next_pts:
                last_p = curr_pts[-1]
                first_p = next_pts[0]
                first_date = (
                    cycle5_display_date
                    if next_cycle == current_cycle_num and first_p.point_type == "min1"
                    else first_p.date
                )
                fig.add_trace(
                    go.Scatter(
                        x=[last_p.date, first_date],
                        y=[last_p.price, first_p.price],
                        mode="lines",
                        name=f"Cycle {next_cycle}",
                        line={
                            "color": CYCLE_COLORS.get(next_cycle, "#888"),
                            "width": 1,
                            "dash": "dot",
                        },
                        showlegend=False,
                        hoverinfo="skip",
                    )
                )

    # 3b. Add method hint lines (visual guides for projection methods)
    _add_fib_hint_lines(
        fig, result, target_date, cycle5_display_date, current_cycle_num, idx, cycles
    )
    _add_dim_return_hint_lines(
        fig, result, target_date, cycle5_display_date, current_cycle_num, idx, cycles
    )

    # 4. Add target predictions (stars + text label)
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
            ("Fib 100%", result.fib_target, result.fib_target_pct, TARGET_COLORS["fibonacci"])
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
    if result.hist_peak_target:
        targets.append(
            (
                "Hist. Peak",
                result.hist_peak_target,
                result.hist_peak_target_pct,
                TARGET_COLORS["historical"],
            )
        )
        # Add horizontal line at historical peak level
        _add_historical_peak_line(fig, result.hist_peak_target, start_date, target_date)

    _add_target_predictions(
        fig,
        targets,
        target_date,
        is_btc=is_btc,
        composite_pct=result.composite_target_pct,
    )

    # Add halving lines
    _add_halving_lines(fig)

    # Filter points to visible date range for y-axis calculation
    # This prevents points from earlier cycles (outside chart range) from stretching the axis
    visible_points = [p for p in valid_points if start_date <= p.date <= end_date]

    # Calculate y-axis range from actual visible data (excludes trendlines)
    y_range = _calculate_y_axis_range(
        price_series=list(plot_df["close"].dropna()),
        point_prices=[p.price for p in visible_points],
        target_prices=[t[1] for t in targets] if targets else [],
        hist_peak=result.hist_peak_target,
    )

    # Layout
    if is_btc:
        title_text = "#0 - Bitcoin (BTC/USD) - Cycle Pattern Analysis"
        yaxis_config = {
            "title": yaxis_title,
            "type": "log",
            "tickprefix": "$",
            "gridcolor": "rgba(128, 128, 128, 0.2)",
            "range": y_range,
        }
    else:
        confidence_badge = f"[{result.confidence.upper()}]" if result.confidence else ""
        rank_prefix = f"#{result.rank} - " if result.rank is not None else ""
        title_text = f"{rank_prefix}{pair_label} - Cycle Pattern Analysis {confidence_badge}"
        yaxis_config = {
            "title": yaxis_title,
            "type": "log",
            "gridcolor": "rgba(128, 128, 128, 0.2)",
            "range": y_range,
        }

    fig.update_layout(
        title={
            "text": title_text,
            "font": {"size": 20, "family": "Arial Black"},
        },
        xaxis={
            "title": "Date",
            "gridcolor": "rgba(128, 128, 128, 0.2)",
            "range": [start_date, end_date],
            "hoverformat": "%Y-%m-%d",
        },
        yaxis=yaxis_config,
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
        margin={"t": 80, "b": 60, "r": 20},
    )

    if output_path:
        _write_pattern_chart(fig, output_path, f"{pair_label} Pattern Analysis")

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
    btc_result: CoinPatternResult | None,
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

        .ranking-table .coin-name a {
            color: inherit;
            text-decoration: none;
        }

        .ranking-table .coin-name a:hover {
            text-decoration: underline;
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
        btc_trendline = (
            _format_pct(btc_result.trendline_target_pct)
            if btc_result.trendline_target_pct is not None
            else "N/A"
        )
        btc_fib = (
            _format_pct(btc_result.fib_target_pct)
            if btc_result.fib_target_pct is not None
            else "N/A"
        )
        btc_dim = (
            _format_pct(btc_result.dim_return_target_pct)
            if btc_result.dim_return_target_pct is not None
            else "N/A"
        )
        btc_hist = (
            _format_pct(btc_result.hist_peak_target_pct)
            if btc_result.hist_peak_target_pct is not None
            else "N/A"
        )
        btc_row = f"""
            <tr class="btc-row">
                <td>0</td>
                <td class="coin-name"><a href="https://www.cryptocompare.com/coins/BTC/overview" target="_blank">BTC</a> <span class="pair-type">(/USD)</span></td>
                <td><span class="chart-badge badge-high">HIGH</span></td>
                <td class="number">{btc_result.num_cycles}</td>
                <td class="number target-value {composite_class}">{_format_pct(btc_composite, 1)}</td>
                <td class="number">{btc_trendline}</td>
                <td class="number">{btc_fib}</td>
                <td class="number">{btc_dim}</td>
                <td class="number">{btc_hist}</td>
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
                <td class="coin-name"><a href="https://www.cryptocompare.com/coins/{coin.coin_id.upper()}/overview" target="_blank">{coin.coin_id.upper()}</a> <span class="pair-type">(/BTC)</span></td>
                <td><span class="chart-badge {confidence_class}">{coin.confidence.upper()}</span></td>
                <td class="number">{coin.num_cycles}</td>
                <td class="number target-value {composite_class}">{_format_pct(coin.composite_target_pct, 1)}</td>
                <td class="number">{_format_pct(coin.trendline_target_pct) if coin.trendline_target_pct is not None else 'N/A'}</td>
                <td class="number">{_format_pct(coin.fib_target_pct) if coin.fib_target_pct is not None else 'N/A'}</td>
                <td class="number">{_format_pct(coin.dim_return_target_pct) if coin.dim_return_target_pct is not None else 'N/A'}</td>
                <td class="number">{_format_pct(coin.hist_peak_target_pct) if coin.hist_peak_target_pct is not None else 'N/A'}</td>
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
            for cycle 5 (2028). Four methods are used to estimate targets: log-linear trendline
            regression, Fibonacci 100% extension, diminishing returns model, and historical peak.
            <strong>Ranking is by composite score (descending).</strong> Coins with negative
            trendline predictions are filtered out (underperforming BTC).
            The composite score is a weighted average of available methods, with weights depending on confidence level.
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
                        <th>Hist. Peak</th>
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
    top_n: int = PATTERN_ANALYSIS_TOP_N,
    include: list[str] | None = None,
    show_progress: bool = True,
) -> dict[str, Path]:
    """
    Generate all pattern analysis charts and the main page.

    Args:
        output_dir: Directory to save charts (e.g., site/)
        top_n: Number of top altcoins to include
        include: Coin IDs to always include regardless of filters
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
    include_set = set(include) if include else None
    coin_results = analyzer.analyze_all_coins(
        filter_total2=True, include=include_set, show_progress=show_progress
    )

    # Get top N (filtered to positive trendline predictions, sorted by composite target)
    top_coins = analyzer.get_top_coins(coin_results, n=top_n, include=include_set)

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

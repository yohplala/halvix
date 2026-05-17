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
    BTC_CYCLE_BOTTOMS,
    BTC_CYCLE_PEAKS,
    DAYS_AFTER_HALVING,
    DAYS_BEFORE_HALVING,
    HALVING_DATES,
    OUTPUT_DIR,
    TOTAL2_COMPOSITION_FILE,
    TOTAL2_INDEX_FILE,
)
from data.cache import PriceDataCache
from visualization._layout import (
    _get_base_css,
    _get_footer_css,
    _get_footer_html,
    _get_header_css,
    _get_header_html,
)

# =============================================================================
# Color Palettes - High contrast on dark background (#0d1117)
# =============================================================================

# BTC: Yellow to bright orange progression (5 cycles)
# Designed for maximum contrast and visual distinction
BTC_COLORS = [
    "rgba(255, 245, 157, 0.9)",  # Cycle 1 (2012) - pale yellow
    "rgba(255, 200, 87, 0.92)",  # Cycle 2 (2016) - light orange
    "rgba(255, 145, 50, 0.95)",  # Cycle 3 (2020) - bright orange
    "rgba(255, 140, 90, 1.0)",  # Cycle 4 (2024) - lighter coral orange (better contrast)
    "rgba(255, 100, 70, 1.0)",  # Cycle 5 (2028) - deep coral (projected)
]

# TOTAL2: Cyan to blue progression (skip cycle 1)
# Designed for clear distinction from BTC and high visibility
TOTAL2_COLORS = [
    "rgba(200, 230, 255, 0.85)",  # Cycle 1 (unused) - placeholder
    "rgba(144, 224, 239, 0.9)",  # Cycle 2 (2016) - pale cyan
    "rgba(56, 189, 248, 0.95)",  # Cycle 3 (2020) - bright cyan-blue
    "rgba(100, 160, 255, 1.0)",  # Cycle 4 (2024) - lighter sky blue (better contrast)
    "rgba(80, 130, 255, 1.0)",  # Cycle 5 (2028) - deeper blue (projected)
]

# Line styles per cycle (solid or dotted)
# Cycle 2 uses dotted lines to distinguish from more recent cycles
LINE_DASH_STYLES = [
    "solid",  # Cycle 1 (2012)
    "dot",  # Cycle 2 (2016) - dotted
    "solid",  # Cycle 3 (2020)
    "solid",  # Cycle 4 (2024)
    "solid",  # Cycle 5 (2028)
]


# =============================================================================
# Page-template wrapper for Plotly charts
#
# CSS/HTML layout primitives live in ``visualization/_layout.py``; this
# module composes them with chart HTML for a complete page.
# =============================================================================


def _get_page_template(title: str, chart_html: str, back_link: str = "../index.html") -> str:
    """
    Wrap a Plotly chart in a page template with minimal header.

    Uses shared CSS and HTML components for consistency across all pages.

    Args:
        title: Page title for the browser tab
        chart_html: The Plotly chart HTML content
        back_link: Link for the back arrow (default: ../index.html)

    Returns:
        Complete HTML page with minimal header, chart, and footer
    """
    base_css = _get_base_css()
    header_css = _get_header_css()
    footer_css = _get_footer_css()
    header_html = _get_header_html(back_link)
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

    return f"""<!DOCTYPE html>
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


def _write_chart_with_template(
    fig: go.Figure,
    output_path: Path,
    title: str,
) -> None:
    """
    Write a Plotly figure to HTML with a page template wrapper.

    Args:
        fig: Plotly figure to save
        output_path: Path to save HTML file
        title: Page title for the browser tab
    """
    # Generate the Plotly chart HTML (just the div and script, not full page)
    chart_html = fig.to_html(
        full_html=False,
        include_plotlyjs="cdn",
        config={"responsive": True},
    )
    # Wrap in page template
    full_html = _get_page_template(title, chart_html)
    # Write to file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(full_html)


def _add_cycle_extremes_lines(
    fig: go.Figure,
    halving_date: date,
    xref: str = "x",
    yref: str = "y",
    row: int | None = None,
    col: int | None = None,
) -> None:
    """
    Add vertical lines for BTC cycle peaks (green) and bottoms (red) to a chart.

    Only adds lines for peaks/bottoms that fall within the cycle window.
    Lines are constrained to the specific subplot using row/col parameters.

    Args:
        fig: Plotly figure to add lines to
        halving_date: The halving date for this cycle
        xref: X-axis reference (e.g., "x", "x2" for subplots)
        yref: Y-axis reference (e.g., "y", "y2" for subplots)
        row: Row number for subplot (1-indexed), used with add_vline
        col: Column number for subplot (1-indexed), used with add_vline
    """
    cycle_start = halving_date - timedelta(days=DAYS_BEFORE_HALVING)
    cycle_end = halving_date + timedelta(days=DAYS_AFTER_HALVING)

    # Add peak lines (50% transparent green)
    for peak_date in BTC_CYCLE_PEAKS:
        if cycle_start <= peak_date <= cycle_end:
            days_from_halving = (peak_date - halving_date).days
            fig.add_vline(
                x=days_from_halving,
                line={"dash": "solid", "color": "rgba(63, 185, 80, 0.5)", "width": 1},
                row=row,
                col=col,
            )

    # Add bottom lines (50% transparent red)
    for bottom_date in BTC_CYCLE_BOTTOMS:
        if cycle_start <= bottom_date <= cycle_end:
            days_from_halving = (bottom_date - halving_date).days
            fig.add_vline(
                x=days_from_halving,
                line={"dash": "solid", "color": "rgba(248, 81, 73, 0.5)", "width": 1},
                row=row,
                col=col,
            )


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


def create_btc_combined_chart(
    output_path: Path | None = None,
) -> go.Figure:
    """
    Create combined BTC chart page with normalized and absolute charts stacked vertically.

    Args:
        output_path: Path to save HTML file

    Returns:
        Plotly Figure with 2 subplots
    """

    # Load BTC-USD data
    cache = PriceDataCache()
    btc_df = cache.get_prices("btc", "USD")

    if btc_df is None or btc_df.empty:
        raise FileNotFoundError("BTC-USD price data not found. Run fetch-prices first.")

    # Create figure with 2 rows
    fig = make_subplots(
        rows=2,
        cols=1,
        subplot_titles=(
            "Bitcoin (BTC) Price - Normalized to Halving Day",
            "Bitcoin (BTC) Price - Absolute (USD)",
        ),
        vertical_spacing=0.08,
        row_heights=[0.5, 0.5],
    )

    # Add traces for each halving cycle - NORMALIZED (row 1)
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

        # Format dates for hover
        dates_formatted = [d.strftime("%Y-%m-%d") for d in cycle_df.index]

        fig.add_trace(
            go.Scatter(
                x=cycle_df["days_from_halving"],
                y=cycle_df["normalized"],
                mode="lines",
                name=f"Cycle {cycle_num} ({halving_date.year})",
                line={"color": BTC_COLORS[i], "width": 1.5, "dash": LINE_DASH_STYLES[i]},
                legendgroup=f"cycle{cycle_num}",
                customdata=dates_formatted,
                hovertemplate=(
                    ""
                    f"Cycle {cycle_num}: %{{customdata}}<br>"
                    "Multiplier: %{y:.2f}x<br>"
                    f"(Halving price: ${halving_price:,.0f})"
                    "<extra></extra>"
                ),
            ),
            row=1,
            col=1,
        )

    # Add traces for each halving cycle - ABSOLUTE (row 2)
    for i, halving_date in enumerate(HALVING_DATES):
        cycle_num = i + 1
        cycle_df = get_cycle_data(btc_df, halving_date, price_col="close")

        if cycle_df.empty:
            continue

        # Format dates for hover
        dates_formatted = [d.strftime("%Y-%m-%d") for d in cycle_df.index]

        fig.add_trace(
            go.Scatter(
                x=cycle_df["days_from_halving"],
                y=cycle_df["close"],
                mode="lines",
                name=f"Cycle {cycle_num} ({halving_date.year})",
                line={"color": BTC_COLORS[i], "width": 1.5, "dash": LINE_DASH_STYLES[i]},
                legendgroup=f"cycle{cycle_num}",
                showlegend=False,
                customdata=dates_formatted,
                hovertemplate=(
                    ""
                    f"Cycle {cycle_num}: %{{customdata}}<br>"
                    "Price: $%{y:,.2f}"
                    "<extra></extra>"
                ),
            ),
            row=2,
            col=1,
        )

    # Update layout
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0d1117",
        hovermode="x unified",
        height=1100,
        legend={
            "yanchor": "top",
            "y": 0.99,
            "xanchor": "left",
            "x": 0.01,
            "bgcolor": "rgba(0,0,0,0.5)",
        },
        margin={"t": 60, "b": 40},
    )

    # Update axes
    fig.update_xaxes(
        title_text="Days from Halving",
        tickmode="linear",
        dtick=100,
        gridcolor="rgba(128, 128, 128, 0.2)",
        row=1,
        col=1,
    )
    fig.update_xaxes(
        title_text="Days from Halving",
        tickmode="linear",
        dtick=100,
        gridcolor="rgba(128, 128, 128, 0.2)",
        row=2,
        col=1,
    )
    fig.update_yaxes(
        title_text="Price Multiplier (1.0 = Halving Day)",
        type="log",
        gridcolor="rgba(128, 128, 128, 0.2)",
        row=1,
        col=1,
    )
    fig.update_yaxes(
        title_text="BTC Price (USD)",
        type="log",
        tickprefix="$",
        gridcolor="rgba(128, 128, 128, 0.2)",
        row=2,
        col=1,
    )

    # Add vertical lines at halving for both charts (subplot-scoped)
    fig.add_vline(
        x=0, line={"dash": "dot", "color": "rgba(200,200,200,0.5)", "width": 1}, row=1, col=1
    )
    fig.add_vline(
        x=0, line={"dash": "dot", "color": "rgba(200,200,200,0.5)", "width": 1}, row=2, col=1
    )

    # Add horizontal line at 1.0 for normalized chart
    fig.add_hline(y=1, line={"dash": "dot", "color": "rgba(255,255,255,0.3)"}, row=1, col=1)

    # Add cycle peak/bottom lines for each halving cycle (subplot-scoped)
    for halving_date in HALVING_DATES:
        _add_cycle_extremes_lines(fig, halving_date, row=1, col=1)
        _add_cycle_extremes_lines(fig, halving_date, row=2, col=1)

    if output_path:
        _write_chart_with_template(
            fig,
            output_path,
            "Bitcoin (BTC) Charts",
        )

    return fig


def create_total2_combined_chart(
    output_path: Path | None = None,
) -> go.Figure:
    """
    Create combined TOTAL2 chart page with 2 charts stacked vertically:
    1. TOTAL2/USD - Normalized to Halving Day
    2. TOTAL2/BTC - Absolute Values

    Args:
        output_path: Path to save HTML file

    Returns:
        Plotly Figure with 2 subplots
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
    btc_usd_aligned = btc_usd_df["close"].reindex(total2_usd_df.index)
    total2_usd_df["total2_usd"] = total2_usd_df["total2_price"] * btc_usd_aligned

    # Create figure with 2 rows
    fig = make_subplots(
        rows=2,
        cols=1,
        subplot_titles=(
            "TOTAL2/USD - Normalized to Halving Day",
            "TOTAL2/BTC - Absolute Values",
        ),
        vertical_spacing=0.08,
        row_heights=[0.5, 0.5],
    )

    # Add traces for each halving cycle (skip cycle 1 - insufficient data)
    for i, halving_date in enumerate(HALVING_DATES):
        cycle_num = i + 1

        # Skip cycle 1 (2012) - data too sparse
        if cycle_num == 1:
            continue

        # Row 1: USD normalized
        cycle_usd = get_cycle_data(
            total2_usd_df, halving_date, price_col="total2_usd", normalize=True
        )
        if not cycle_usd.empty and "normalized" in cycle_usd.columns:
            # Build customdata with date and coin_count
            customdata_usd = [
                [d.strftime("%Y-%m-%d"), int(total2_usd_df.loc[d, "coin_count"])]
                for d in cycle_usd.index
            ]
            fig.add_trace(
                go.Scatter(
                    x=cycle_usd["days_from_halving"],
                    y=cycle_usd["normalized"],
                    mode="lines",
                    name=f"Cycle {cycle_num} ({halving_date.year})",
                    line={"color": TOTAL2_COLORS[i], "width": 1.5, "dash": LINE_DASH_STYLES[i]},
                    legendgroup=f"cycle{cycle_num}",
                    customdata=customdata_usd,
                    hovertemplate=(
                        ""
                        f"Cycle {cycle_num}: %{{customdata[0]}}<br>"
                        "Multiplier: %{y:.2f}x<br>"
                        "Coins: %{customdata[1]}"
                        "<extra></extra>"
                    ),
                ),
                row=1,
                col=1,
            )

        # Row 2: BTC absolute
        cycle_abs = get_cycle_data(total2_btc_df, halving_date, price_col="total2_price")
        if not cycle_abs.empty:
            # Build customdata with date and coin_count
            customdata_abs = [
                [d.strftime("%Y-%m-%d"), int(total2_btc_df.loc[d, "coin_count"])]
                for d in cycle_abs.index
            ]
            fig.add_trace(
                go.Scatter(
                    x=cycle_abs["days_from_halving"],
                    y=cycle_abs["total2_price"],
                    mode="lines",
                    name=f"Cycle {cycle_num} ({halving_date.year})",
                    line={"color": TOTAL2_COLORS[i], "width": 1.5, "dash": LINE_DASH_STYLES[i]},
                    legendgroup=f"cycle{cycle_num}",
                    showlegend=False,
                    customdata=customdata_abs,
                    hovertemplate=(
                        ""
                        f"Cycle {cycle_num}: %{{customdata[0]}}<br>"
                        "TOTAL2: %{y:.8f} BTC<br>"
                        "Coins: %{customdata[1]}"
                        "<extra></extra>"
                    ),
                ),
                row=2,
                col=1,
            )

    # Update layout
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0d1117",
        hovermode="x unified",
        height=1000,
        legend={
            "yanchor": "top",
            "y": 0.99,
            "xanchor": "left",
            "x": 0.01,
            "bgcolor": "rgba(0,0,0,0.5)",
        },
        margin={"t": 60, "b": 40},
    )

    # Update axes for both rows
    for row in [1, 2]:
        fig.update_xaxes(
            title_text="Days from Halving",
            tickmode="linear",
            dtick=200,
            gridcolor="rgba(128, 128, 128, 0.2)",
            row=row,
            col=1,
        )

    fig.update_yaxes(
        title_text="Multiplier (1.0 = Halving)",
        type="log",
        gridcolor="rgba(128, 128, 128, 0.2)",
        row=1,
        col=1,
    )
    fig.update_yaxes(
        title_text="TOTAL2 (BTC)",
        type="log",
        gridcolor="rgba(128, 128, 128, 0.2)",
        row=2,
        col=1,
    )

    # Add vertical lines at halving for both charts (subplot-scoped)
    fig.add_vline(
        x=0, line={"dash": "dot", "color": "rgba(200,200,200,0.5)", "width": 1}, row=1, col=1
    )
    fig.add_vline(
        x=0, line={"dash": "dot", "color": "rgba(200,200,200,0.5)", "width": 1}, row=2, col=1
    )

    # Add horizontal line at 1.0 for normalized chart (row 1 only)
    fig.add_hline(y=1, line={"dash": "dot", "color": "rgba(255,255,255,0.3)"}, row=1, col=1)

    # Add cycle peak/bottom lines for each halving cycle (subplot-scoped)
    for halving_date in HALVING_DATES:
        _add_cycle_extremes_lines(fig, halving_date, row=1, col=1)
        _add_cycle_extremes_lines(fig, halving_date, row=2, col=1)

    if output_path:
        _write_chart_with_template(
            fig,
            output_path,
            "TOTAL2 Index Charts",
        )

    return fig


def create_composition_viewer_html(
    output_path: Path,
    last_updated: str | None = None,
) -> dict[str, Path]:
    """
    Create HTML pages with interactive TOTAL2 composition viewer, split by month.

    Creates one page per month with navigation between months.

    Args:
        output_path: Base path for HTML files (will append month suffix)
        last_updated: Optional timestamp string for footer display.

    Returns:
        Dictionary mapping month key (e.g., "2024_01") to file path
    """
    import json
    from datetime import UTC, datetime

    from visualization.templates import render_template

    if last_updated is None:
        last_updated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    # Load composition data
    if not TOTAL2_COMPOSITION_FILE.exists():
        raise FileNotFoundError("TOTAL2 composition not found. Run calculate-total2 first.")

    composition_df = pd.read_parquet(TOTAL2_COMPOSITION_FILE)

    # Get unique dates and group by month
    dates = sorted(composition_df["date"].unique())

    def get_month_key(d: date) -> str:
        """Get month key like '2024_01' from a date."""
        return f"{d.year}_{d.month:02d}"

    def get_month_display(month_key: str) -> str:
        """Get display name like 'Jan 2024' from month key."""
        year, month = month_key.split("_")
        month_names = [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ]
        return f"{month_names[int(month) - 1]} {year}"

    def get_cycle_info(d) -> str:
        """
        Get cycle day info for a date, showing which cycle(s) it belongs to.

        Returns string like "C4: Day 12" or "C3: Day 880 | C4: Day -5" for overlaps.
        """
        # Convert Pandas Timestamp to date if needed
        if hasattr(d, "date"):
            d = d.date()
        cycle_infos = []
        for i, halving_date in enumerate(HALVING_DATES):
            cycle_num = i + 1
            # Skip cycle 1 (not shown in charts)
            if cycle_num == 1:
                continue
            start = halving_date - timedelta(days=DAYS_BEFORE_HALVING)
            end = halving_date + timedelta(days=DAYS_AFTER_HALVING)
            if start <= d <= end:
                day_num = (d - halving_date).days
                cycle_infos.append(f"C{cycle_num}: Day {day_num}")
        return " | ".join(cycle_infos) if cycle_infos else ""

    # Get all unique months
    months = sorted({get_month_key(d) for d in dates})

    # Load TOTAL2 index for displaying values
    total2_df = pd.read_parquet(TOTAL2_INDEX_FILE)

    # Build month navigation table (shared across all pages)
    years_in_nav = sorted({m.split("_")[0] for m in months})
    month_letters = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"]

    # Build a set of available months for quick lookup
    available_months = set(months)

    created_files = {}

    for month_key in months:
        # Parse year and month from key
        year, month = month_key.split("_")
        year = int(year)
        month = int(month)

        # Filter dates for this month
        month_dates = [d for d in dates if get_month_key(d) == month_key]

        # Find the last day of the previous month for cross-month comparison
        prev_month_last_day = None
        month_idx = months.index(month_key)
        if month_idx > 0:
            prev_month_key = months[month_idx - 1]
            prev_month_dates = [d for d in dates if get_month_key(d) == prev_month_key]
            if prev_month_dates:
                prev_month_last_day = max(prev_month_dates)

        # Create date options for this month with cycle day info
        date_options_list = []
        for d in month_dates:
            # Convert to date string for clean display
            date_str = d.date() if hasattr(d, "date") else d
            cycle_info = get_cycle_info(d)
            if cycle_info:
                display = f"{date_str}  ({cycle_info})"
            else:
                display = str(date_str)
            date_options_list.append(f'<option value="{d}">{display}</option>')
        date_options = "\n".join(date_options_list)

        # Create composition data as JSON for this month, including TOTAL2 value
        # Also include previous month's last day for cross-month comparison
        composition_by_date = {}

        # Add previous month's last day if available (for comparison only)
        if prev_month_last_day is not None:
            dt = prev_month_last_day
            day_comp = composition_df[composition_df["date"] == dt].sort_values("rank")
            total2_value = None
            if dt in total2_df.index:
                total2_value = float(total2_df.loc[dt, "total2_price"])
            elif hasattr(dt, "date") and pd.Timestamp(dt.date()) in total2_df.index:
                total2_value = float(total2_df.loc[pd.Timestamp(dt.date()), "total2_price"])

            composition_by_date[str(dt)] = {
                "total2_value": total2_value,
                "coins": [
                    {
                        "rank": int(row["rank"]),
                        "coin_id": row["coin_id"].upper(),
                        "volume": float(row["volume"]),
                        "weight": float(row["weight"]) * 100,
                        "price_btc": float(row["price_btc"]),
                    }
                    for _, row in day_comp.iterrows()
                ],
            }

        # Add this month's dates
        for dt in month_dates:
            day_comp = composition_df[composition_df["date"] == dt].sort_values("rank")
            # Get TOTAL2 value for this date
            total2_value = None
            if dt in total2_df.index:
                total2_value = float(total2_df.loc[dt, "total2_price"])
            elif hasattr(dt, "date") and pd.Timestamp(dt.date()) in total2_df.index:
                total2_value = float(total2_df.loc[pd.Timestamp(dt.date()), "total2_price"])

            composition_by_date[str(dt)] = {
                "total2_value": total2_value,
                "coins": [
                    {
                        "rank": int(row["rank"]),
                        "coin_id": row["coin_id"].upper(),
                        "volume": float(row["volume"]),
                        "weight": float(row["weight"]) * 100,
                        "price_btc": float(row["price_btc"]),
                    }
                    for _, row in day_comp.iterrows()
                ],
            }

        composition_json = json.dumps(composition_by_date)

        # Generate month navigation as a table (12 columns for months, rows for years)
        month_nav_rows = []
        for nav_year in years_in_nav:
            row_cells = [f'<td class="year-label">{nav_year}</td>']
            for m_idx in range(1, 13):
                m_key = f"{nav_year}_{m_idx:02d}"
                letter = month_letters[m_idx - 1]
                if m_key in available_months:
                    if m_key == month_key:
                        row_cells.append(
                            f'<td><span class="month-current" '
                            f'title="{get_month_display(m_key)}">{letter}</span></td>'
                        )
                    else:
                        row_cells.append(
                            f'<td><a href="total2_composition_{m_key}.html" '
                            f'class="month-link" title="{get_month_display(m_key)}">'
                            f"{letter}</a></td>"
                        )
                else:
                    # Month not available - show greyed out
                    row_cells.append(f'<td><span class="month-empty">{letter}</span></td>')
            month_nav_rows.append("<tr>" + "".join(row_cells) + "</tr>")
        month_nav_html = "\n".join(month_nav_rows)

        display_month = get_month_display(month_key)

        html_content = render_template(
            "composition_viewer.html",
            display_month=display_month,
            month_nav_html=month_nav_html,
            date_options=date_options,
            composition_json=composition_json,
            last_updated=last_updated,
            back_link="../index.html",
        )

        # Construct filename for this month
        month_output_path = output_path.parent / f"total2_composition_{month_key}.html"
        month_output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(month_output_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        created_files[month_key] = month_output_path

    # Create a redirect from the original path to the latest month
    latest_month = max(months)
    redirect_html = render_template(
        "redirect.html",
        redirect_url=f"total2_composition_{latest_month}.html",
        redirect_text=f"TOTAL2 Composition {get_month_display(latest_month)}",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(redirect_html)

    return created_files


def generate_all_cycle_charts(output_dir: Path | None = None) -> dict[str, Path]:
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

    # Combined BTC chart (normalized + absolute stacked vertically)
    btc_combined_path = output_dir / "btc_charts.html"
    create_btc_combined_chart(btc_combined_path)
    paths["btc_combined"] = btc_combined_path

    # Combined TOTAL2 chart (all 3 stacked vertically)
    total2_combined_path = output_dir / "total2_charts.html"
    create_total2_combined_chart(total2_combined_path)
    paths["total2_combined"] = total2_combined_path

    # Composition viewer (split by month)
    comp_path = output_dir / "total2_composition.html"
    month_paths = create_composition_viewer_html(comp_path)
    paths["composition"] = comp_path
    for month_key, month_path in month_paths.items():
        paths[f"composition_{month_key}"] = month_path

    return paths

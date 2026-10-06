"""
Visualization module for Halvix.

Creates interactive Plotly charts for:
- TOTAL2 index across halving cycles
- BTC vs USD across halving cycles
- Interactive coin composition viewer

Every cycle is drawn on a "days from halving" x-axis. The current cycle's
halving is a forecast (see ``analysis.halving_calendar``) until its real date
is added to the config.
"""

from datetime import date
from pathlib import Path
from statistics import geometric_mean

import plotly.graph_objects as go
import polars as pl
from plotly.subplots import make_subplots

from analysis.halving_calendar import HalvingCycle, halving_cycles
from config import (
    BTC_CYCLE_BOTTOMS,
    BTC_CYCLE_PEAKS,
    OUTPUT_DIR,
    TOTAL2_COMPOSITION_FILE,
    TOTAL2_INDEX_FILE,
)
from data.cache import PriceDataCache
from data.coin_metadata import CoinMetadataResolver
from visualization._layout import render_chart_page

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
    "rgba(255, 100, 70, 1.0)",  # Cycle 5 (forecast) - deep coral
]

# TOTAL2: Cyan to blue progression (skip cycle 1)
# Designed for clear distinction from BTC and high visibility
TOTAL2_COLORS = [
    "rgba(200, 230, 255, 0.85)",  # Cycle 1 (unused) - placeholder
    "rgba(144, 224, 239, 0.9)",  # Cycle 2 (2016) - pale cyan
    "rgba(56, 189, 248, 0.95)",  # Cycle 3 (2020) - bright cyan-blue
    "rgba(100, 160, 255, 1.0)",  # Cycle 4 (2024) - lighter sky blue (better contrast)
    "rgba(80, 130, 255, 1.0)",  # Cycle 5 (forecast) - deeper blue
]

# Line styles per cycle (solid or dotted)
# Cycle 2 uses dotted lines to distinguish from more recent cycles
LINE_DASH_STYLES = [
    "solid",  # Cycle 1 (2012)
    "dot",  # Cycle 2 (2016) - dotted
    "solid",  # Cycle 3 (2020)
    "solid",  # Cycle 4 (2024)
    "solid",  # Cycle 5 (forecast)
]

GRID_COLOR = "rgba(128, 128, 128, 0.2)"
HALVING_LINE = {"dash": "dot", "color": "rgba(200,200,200,0.5)", "width": 1}
PEAK_LINE_COLOR = "rgba(63, 185, 80, 0.5)"  # 50% transparent green
BOTTOM_LINE_COLOR = "rgba(248, 81, 73, 0.5)"  # 50% transparent red


# =============================================================================
# Page-template wrapper for Plotly charts
#
# CSS/HTML layout primitives live in ``visualization/_layout.py``; this
# module composes them with chart HTML for a complete page.
# =============================================================================


def _write_chart_with_template(
    fig: go.Figure,
    output_path: Path,
    title: str,
) -> None:
    """
    Write a Plotly figure to HTML with the shared chart-page wrapper.

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
    full_html = render_chart_page(title, chart_html)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(full_html)


# =============================================================================
# Cycle data
# =============================================================================


def get_cycle_data(df: pl.DataFrame, cycle: HalvingCycle, price_col: str) -> pl.DataFrame:
    """
    Extract a cycle's window with an integer ``days_from_halving`` column.

    Rows without a positive ``price_col`` value are dropped (e.g. a TOTAL2/USD
    day missing its BTC/USD rate), so every returned point can be plotted on a
    log axis and normalized.
    """
    return df.filter(
        pl.col("date").is_between(cycle.start, cycle.end) & (pl.col(price_col) > 0)
    ).with_columns(
        (pl.col("date") - cycle.halving).dt.total_days().cast(pl.Int64).alias("days_from_halving")
    )


def _halving_day_value(cycle_df: pl.DataFrame, price_col: str) -> float | None:
    """Value on halving day (or the closest day after), None before the halving."""
    after = cycle_df.filter(pl.col("days_from_halving") >= 0)
    return None if after.is_empty() else after[price_col][0]


def _forecast_halving_value(
    cycle_df: pl.DataFrame, price_col: str, references: list[pl.DataFrame]
) -> float | None:
    """
    Halving-day value for a cycle whose halving has not happened yet.

    Chosen so the cycle's first point sits at the average of the reference
    cycles' normalized values on that same day offset. The average is a
    geometric mean, as the values are multipliers shown on a log axis.
    """
    if cycle_df.is_empty():
        return None
    first_day = cycle_df["days_from_halving"][0]
    multipliers = [
        same_day["normalized"][0]
        for ref in references
        if not (same_day := ref.filter(pl.col("days_from_halving") == first_day)).is_empty()
    ]
    if not multipliers:
        return None
    return cycle_df[price_col][0] / geometric_mean(multipliers)


def normalize_cycles(
    cycle_frames: dict[HalvingCycle, pl.DataFrame], price_col: str
) -> dict[HalvingCycle, tuple[pl.DataFrame, float]]:
    """
    Normalize each cycle to its halving-day value, in a ``normalized`` column.

    Known cycles use their actual halving-day value. A forecast cycle uses a
    forecast value that starts its curve at the average of the known cycles'
    values on the same day (see ``_forecast_halving_value``); the real value
    takes over once the real halving date is in the config.

    Returns:
        ``{cycle: (normalized frame, halving value)}`` in cycle order; cycles
        that cannot be normalized yet are left out.
    """

    def scaled(df: pl.DataFrame, value: float) -> pl.DataFrame:
        return df.with_columns((pl.col(price_col) / value).alias("normalized"))

    normalized = {
        cycle: (scaled(df, value), value)
        for cycle, df in cycle_frames.items()
        if not cycle.forecast and (value := _halving_day_value(df, price_col)) is not None
    }
    references = [df for df, _ in normalized.values()]
    for cycle, df in cycle_frames.items():  # the forecast cycle is the last one
        if cycle.forecast and (value := _forecast_halving_value(df, price_col, references)):
            normalized[cycle] = (scaled(df, value), value)
    return normalized


# =============================================================================
# Shared figure helpers
# =============================================================================


def _add_cycle_trace(
    fig: go.Figure,
    cycle: HalvingCycle,
    cycle_df: pl.DataFrame,
    y_col: str,
    *,
    row: int,
    palette: list[str],
    hover: str,
    extra_cols: tuple[str, ...] = (),
) -> None:
    """
    Add one cycle's curve to a row of a two-row cycle figure.

    ``hover`` is the hover-template body below the date line; in it,
    ``%{customdata[1]}``… refer to ``extra_cols``. The legend entry is shown on
    row 1 only (both rows share a legend group, so it toggles both).
    """
    style = min(cycle.number, len(palette)) - 1  # reuse the last style beyond the palette
    customdata = cycle_df.select(pl.col("date").dt.strftime("%Y-%m-%d"), *extra_cols).rows()
    fig.add_trace(
        go.Scatter(
            x=cycle_df["days_from_halving"].to_list(),
            y=cycle_df[y_col].to_list(),
            mode="lines",
            name=cycle.label,
            line={"color": palette[style], "width": 1.5, "dash": LINE_DASH_STYLES[style]},
            legendgroup=f"cycle{cycle.number}",
            showlegend=row == 1,
            customdata=customdata,
            hovertemplate=f"Cycle {cycle.number}: %{{customdata[0]}}<br>{hover}<extra></extra>",
        ),
        row=row,
        col=1,
    )


def _add_cycle_extremes_lines(fig: go.Figure, cycle: HalvingCycle, row: int) -> None:
    """
    Add vertical lines for the BTC cycle peaks (green) and bottoms (red)
    falling inside a cycle's window, scoped to one subplot row.
    """
    for dates, color in (
        (BTC_CYCLE_PEAKS, PEAK_LINE_COLOR),
        (BTC_CYCLE_BOTTOMS, BOTTOM_LINE_COLOR),
    ):
        for extreme in dates:
            if cycle.contains(extreme):
                fig.add_vline(
                    x=cycle.days_from_halving(extreme),
                    line={"dash": "solid", "color": color, "width": 1},
                    row=row,
                    col=1,
                )


def _finish_cycle_figure(
    fig: go.Figure,
    *,
    height: int,
    dtick: int,
    y_titles: tuple[str, str],
    absolute_tickprefix: str = "",
) -> None:
    """
    Style a two-row cycle figure (row 1 normalized, row 2 absolute) and add
    its reference lines: halving day, the 1.0 multiplier, and the BTC
    peak/bottom lines of every halving cycle — including cycles the figure
    does not plot (TOTAL2 skips cycle 1), so the BTC and TOTAL2 pages show the
    same set of lines (e.g. the 2015-01-14 bottom at +777 days from the 1st
    halving).
    """
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0d1117",
        hovermode="x unified",
        height=height,
        legend={
            "yanchor": "top",
            "y": 0.99,
            "xanchor": "left",
            "x": 0.01,
            "bgcolor": "rgba(0,0,0,0.5)",
        },
        margin={"t": 60, "b": 40},
    )
    fig.update_xaxes(
        title_text="Days from Halving", tickmode="linear", dtick=dtick, gridcolor=GRID_COLOR
    )
    for row, y_title in enumerate(y_titles, start=1):
        fig.update_yaxes(title_text=y_title, type="log", gridcolor=GRID_COLOR, row=row, col=1)
    fig.update_yaxes(tickprefix=absolute_tickprefix, row=2, col=1)

    fig.add_hline(y=1, line={"dash": "dot", "color": "rgba(255,255,255,0.3)"}, row=1, col=1)
    for row in (1, 2):
        fig.add_vline(x=0, line=HALVING_LINE, row=row, col=1)
        for cycle in halving_cycles():
            _add_cycle_extremes_lines(fig, cycle, row)


def _two_row_figure(titles: tuple[str, str]) -> go.Figure:
    return make_subplots(
        rows=2,
        cols=1,
        subplot_titles=titles,
        vertical_spacing=0.08,
        row_heights=[0.5, 0.5],
    )


# =============================================================================
# Cycle charts
# =============================================================================


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
    btc_df = PriceDataCache().get_prices("btc", "USD")
    if btc_df is None or btc_df.is_empty():
        raise FileNotFoundError("BTC-USD price data not found. Run fetch-prices first.")

    frames = {cycle: get_cycle_data(btc_df, cycle, "close") for cycle in halving_cycles()}
    frames = {cycle: df for cycle, df in frames.items() if not df.is_empty()}

    fig = _two_row_figure(
        (
            "Bitcoin (BTC) Price - Normalized to Halving Day",
            "Bitcoin (BTC) Price - Absolute (USD)",
        )
    )

    for cycle, (cycle_df, halving_price) in normalize_cycles(frames, "close").items():
        price_label = "Forecast halving price" if cycle.forecast else "Halving price"
        _add_cycle_trace(
            fig,
            cycle,
            cycle_df,
            "normalized",
            row=1,
            palette=BTC_COLORS,
            hover=f"Multiplier: %{{y:.2f}}x<br>({price_label}: ${halving_price:,.0f})",
        )
    for cycle, cycle_df in frames.items():
        _add_cycle_trace(
            fig, cycle, cycle_df, "close", row=2, palette=BTC_COLORS, hover="Price: $%{y:,.2f}"
        )

    _finish_cycle_figure(
        fig,
        height=1100,
        dtick=100,
        y_titles=("Price Multiplier (1.0 = Halving Day)", "BTC Price (USD)"),
        absolute_tickprefix="$",
    )

    if output_path:
        _write_chart_with_template(fig, output_path, "Bitcoin (BTC) Charts")

    return fig


def create_total2_combined_chart(
    output_path: Path | None = None,
) -> go.Figure:
    """
    Create combined TOTAL2 chart page with 2 charts stacked vertically:
    1. TOTAL2/USD - Normalized to Halving Day
    2. TOTAL2/BTC - Absolute Values

    Cycle 1 (2012) is skipped: altcoin data is too sparse.

    Args:
        output_path: Path to save HTML file

    Returns:
        Plotly Figure with 2 subplots
    """
    if not TOTAL2_INDEX_FILE.exists():
        raise FileNotFoundError("TOTAL2 index not found. Run calculate-total2 first.")
    total2_df = pl.read_parquet(TOTAL2_INDEX_FILE).with_columns(pl.col("date").cast(pl.Date))

    btc_usd_df = PriceDataCache().get_prices("btc", "USD")
    if btc_usd_df is None or btc_usd_df.is_empty():
        raise FileNotFoundError("BTC-USD price data not found. Run fetch-prices first.")

    # TOTAL2 in USD: align BTC-USD onto the index dates via a join.
    total2_df = total2_df.join(
        btc_usd_df.select("date", pl.col("close").alias("btc_usd")), on="date", how="left"
    ).with_columns((pl.col("total2_price") * pl.col("btc_usd")).alias("total2_usd"))

    cycles = halving_cycles()[1:]
    usd_frames = {cycle: get_cycle_data(total2_df, cycle, "total2_usd") for cycle in cycles}
    btc_frames = {cycle: get_cycle_data(total2_df, cycle, "total2_price") for cycle in cycles}
    usd_frames = {cycle: df for cycle, df in usd_frames.items() if not df.is_empty()}
    btc_frames = {cycle: df for cycle, df in btc_frames.items() if not df.is_empty()}

    fig = _two_row_figure(
        ("TOTAL2/USD - Normalized to Halving Day", "TOTAL2/BTC - Absolute Values")
    )

    for cycle, (cycle_df, _) in normalize_cycles(usd_frames, "total2_usd").items():
        _add_cycle_trace(
            fig,
            cycle,
            cycle_df,
            "normalized",
            row=1,
            palette=TOTAL2_COLORS,
            hover="Multiplier: %{y:.2f}x<br>Coins: %{customdata[1]}",
            extra_cols=("coin_count",),
        )
    for cycle, cycle_df in btc_frames.items():
        _add_cycle_trace(
            fig,
            cycle,
            cycle_df,
            "total2_price",
            row=2,
            palette=TOTAL2_COLORS,
            hover="TOTAL2: %{y:.8f} BTC<br>Coins: %{customdata[1]}",
            extra_cols=("coin_count",),
        )

    _finish_cycle_figure(
        fig,
        height=1000,
        dtick=200,
        y_titles=("Multiplier (1.0 = Halving)", "TOTAL2 (BTC)"),
    )

    if output_path:
        _write_chart_with_template(fig, output_path, "TOTAL2 Index Charts")

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

    composition_df = pl.read_parquet(TOTAL2_COMPOSITION_FILE).with_columns(
        pl.col("date").cast(pl.Date)
    )

    # Get unique dates and group by month
    dates = sorted(composition_df["date"].unique().to_list())

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

    chart_cycles = halving_cycles()[1:]  # cycles shown in the TOTAL2 charts

    def get_cycle_info(d: date) -> str:
        """
        Get cycle day info for a date, showing which cycle(s) it belongs to.

        Returns string like "C4: Day 12" or "C3: Day 880 | C4: Day -5" for overlaps.
        """
        return " | ".join(
            f"C{cycle.number}: Day {cycle.days_from_halving(d)}"
            for cycle in chart_cycles
            if cycle.contains(d)
        )

    # Get all unique months
    months = sorted({get_month_key(d) for d in dates})

    # Load TOTAL2 index for displaying values (date → total2_price lookup).
    total2_df = pl.read_parquet(TOTAL2_INDEX_FILE).with_columns(pl.col("date").cast(pl.Date))
    total2_by_date = dict(
        zip(total2_df["date"].to_list(), total2_df["total2_price"].to_list(), strict=True)
    )

    # Resolve parquet stems to display tickers (e.g. "tag-2" -> "TAG")
    resolver = CoinMetadataResolver()

    # Build month navigation table (shared across all pages)
    years_in_nav = sorted({m.split("_")[0] for m in months})
    month_letters = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"]

    # Build a set of available months for quick lookup
    available_months = set(months)

    created_files = {}

    for month_key in months:
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
            cycle_info = get_cycle_info(d)
            display = f"{d}  ({cycle_info})" if cycle_info else str(d)
            date_options_list.append(f'<option value="{d}">{display}</option>')
        date_options = "\n".join(date_options_list)

        # Create composition data as JSON for this month, including TOTAL2 value
        # Also include previous month's last day for cross-month comparison
        composition_by_date = {}

        def _day_entry(dt: date) -> dict:
            day_comp = composition_df.filter(pl.col("date") == dt).sort("rank")
            t2 = total2_by_date.get(dt)
            return {
                "total2_value": float(t2) if t2 is not None else None,
                "coins": [
                    {
                        "rank": int(row["rank"]),
                        "coin_id": resolver.ticker(row["coin_id"]),
                        "volume": float(row["volume"]),
                        "weight": float(row["weight"]) * 100,
                        "price_btc": float(row["price_btc"]),
                    }
                    for row in day_comp.iter_rows(named=True)
                ],
            }

        # Add previous month's last day if available (for comparison only)
        if prev_month_last_day is not None:
            composition_by_date[str(prev_month_last_day)] = _day_entry(prev_month_last_day)

        # Add this month's dates
        for dt in month_dates:
            composition_by_date[str(dt)] = _day_entry(dt)

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

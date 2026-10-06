"""
Tests for the halving-cycle chart helpers (cycle windows, normalization,
peak/bottom lines).
"""

from datetime import date, timedelta
from statistics import geometric_mean

import plotly.graph_objects as go
import polars as pl
import pytest
from plotly.subplots import make_subplots

from analysis.halving_calendar import HalvingCycle
from visualization.charts import (
    BOTTOM_LINE_COLOR,
    _add_cycle_extremes_lines,
    get_cycle_data,
    normalize_cycles,
)


def _daily(start: date, values: list[float]) -> pl.DataFrame:
    return pl.DataFrame(
        {"date": [start + timedelta(days=i) for i in range(len(values))], "close": values}
    )


def _cycle_frame(cycle: HalvingCycle, start_value: float, days: int) -> pl.DataFrame:
    """A cycle window starting at ``start_value`` and doubling by halving day."""
    values = [start_value * 2 ** (i / 550) for i in range(days)]
    return get_cycle_data(_daily(cycle.start, values), cycle, "close")


class TestGetCycleData:
    def test_clips_window_and_counts_days_from_halving(self):
        cycle = HalvingCycle(2, date(2016, 7, 9))
        df = _daily(cycle.start - timedelta(days=10), [1.0] * 2000)
        out = get_cycle_data(df, cycle, "close")
        assert out["date"][0] == cycle.start
        assert out["date"][-1] == cycle.end
        assert out["days_from_halving"][0] == -550
        assert out.filter(pl.col("date") == cycle.halving)["days_from_halving"][0] == 0

    def test_drops_missing_and_non_positive_values(self):
        cycle = HalvingCycle(2, date(2016, 7, 9))
        df = _daily(cycle.start, [1.0, None, 0.0, 2.0])
        assert get_cycle_data(df, cycle, "close")["close"].to_list() == [1.0, 2.0]


class TestNormalizeCycles:
    def test_known_cycle_is_one_on_halving_day(self):
        cycle = HalvingCycle(3, date(2020, 5, 11))
        (df, value), *_ = normalize_cycles(
            {cycle: _cycle_frame(cycle, 10.0, 600)}, "close"
        ).values()
        assert value == pytest.approx(20.0)
        assert df.filter(pl.col("days_from_halving") == 0)["normalized"][0] == pytest.approx(1.0)

    def test_forecast_cycle_starts_at_geometric_mean_of_known_starts(self):
        known = [HalvingCycle(3, date(2020, 5, 11)), HalvingCycle(4, date(2024, 4, 19))]
        # Halving value = start * 2 * k, so the start multiplier is 1/(2k)
        frames = {c: _cycle_frame(c, 10.0, 600) for c in known}
        frames[known[1]] = frames[known[1]].with_columns(
            pl.when(pl.col("days_from_halving") >= 0)
            .then(pl.col("close") * 4)
            .otherwise(pl.col("close"))
        )
        forecast = HalvingCycle(5, date(2027, 12, 5), forecast=True)
        frames[forecast] = _cycle_frame(forecast, 50_000.0, 100)  # no halving-day data

        normalized = normalize_cycles(frames, "close")

        assert list(normalized) == [*known, forecast]
        known_starts = [normalized[c][0]["normalized"][0] for c in known]
        assert known_starts == pytest.approx([0.5, 0.125])
        df, value = normalized[forecast]
        assert df["normalized"][0] == pytest.approx(geometric_mean(known_starts))
        assert value == pytest.approx(50_000.0 / geometric_mean(known_starts))

    def test_forecast_cycle_without_reference_is_skipped(self):
        forecast = HalvingCycle(5, date(2027, 12, 5), forecast=True)
        assert normalize_cycles({forecast: _cycle_frame(forecast, 1.0, 10)}, "close") == {}


class TestCycleExtremesLines:
    def test_confirmed_bottom_drawn_in_cycles_4_and_5(self):
        fig = make_subplots(rows=2, cols=1)
        fig.add_trace(go.Scatter(x=[0], y=[1]), row=1, col=1)  # vlines skip empty subplots
        _add_cycle_extremes_lines(fig, HalvingCycle(4, date(2024, 4, 19)), row=1)
        _add_cycle_extremes_lines(fig, HalvingCycle(5, date(2027, 12, 5), forecast=True), row=1)
        bottoms = [s.x0 for s in fig.layout.shapes if s.line.color == BOTTOM_LINE_COLOR]
        # 2022-11-21 (cycle 4 start) and 2026-06-30 in both cycles
        assert sorted(bottoms) == [-523, -515, 802]

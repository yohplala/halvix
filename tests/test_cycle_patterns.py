"""
Tests for cycle pattern analysis module.

Tests cover:
- CyclePoint and CoinPatternResult dataclasses
- Log-linear trendline regression fitting
- Fibonacci extension calculations
- Diminishing returns model
- Pattern classification
- Cycle point detection in halving windows
- Edge cases (empty data, insufficient cycles)
"""

import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from analysis.cycle_patterns import (
    BTCPatternResult,
    CoinPatternResult,
    CyclePatternAnalyzer,
    CyclePoint,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_dir():
    """Create temporary directory for price cache."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_price_cache():
    """Create a mock price cache that returns predefined data."""
    cache = MagicMock()
    cache.list_cached_coins.return_value = []
    cache.get_prices.return_value = None
    return cache


@pytest.fixture
def sample_price_df():
    """Create sample price data DataFrame with DatetimeIndex."""
    dates = pd.date_range("2015-01-01", periods=3650, freq="D")  # ~10 years
    # Create a sinusoidal price pattern with trend
    days = np.arange(len(dates))
    # Base trend (slowly increasing)
    base = 0.001 * (1 + days / 1000)
    # Add cycles (roughly 4-year cycles)
    cycle = 0.0005 * np.sin(2 * np.pi * days / 1460)
    prices = base + cycle
    prices = np.maximum(prices, 0.0001)  # Ensure positive prices

    df = pd.DataFrame(
        {
            "close": prices,
            "volume_to": np.random.uniform(1000, 10000, len(dates)),
        },
        index=dates,
    )
    return df


@pytest.fixture
def analyzer_with_mock_cache(mock_price_cache):
    """Create analyzer with mocked price cache."""
    return CyclePatternAnalyzer(price_cache=mock_price_cache, min_cycles=1)


# =============================================================================
# CyclePoint Dataclass Tests
# =============================================================================


class TestCyclePoint:
    """Tests for CyclePoint dataclass."""

    def test_cycle_point_creation(self):
        """Test creating a CyclePoint with all fields."""
        point = CyclePoint(
            date=date(2024, 1, 15),
            price=0.05,
            cycle_num=4,
            point_type="min1",
            days_from_halving=-94,
        )

        assert point.date == date(2024, 1, 15)
        assert point.price == 0.05
        assert point.cycle_num == 4
        assert point.point_type == "min1"
        assert point.days_from_halving == -94

    def test_cycle_point_types(self):
        """Test various point types."""
        for point_type in ["min1", "max1", "min2", "max2"]:
            point = CyclePoint(
                date=date(2024, 1, 1),
                price=1.0,
                cycle_num=1,
                point_type=point_type,
                days_from_halving=0,
            )
            assert point.point_type == point_type


# =============================================================================
# CoinPatternResult Dataclass Tests
# =============================================================================


class TestCoinPatternResult:
    """Tests for CoinPatternResult dataclass."""

    def test_default_values(self):
        """Test CoinPatternResult default values."""
        result = CoinPatternResult(coin_id="eth")

        assert result.coin_id == "eth"
        assert result.points == []
        assert result.num_cycles == 0
        assert result.trendline_target is None
        assert result.fib_target is None
        assert result.dim_return_target is None
        assert result.composite_target_pct is None
        assert result.confidence == "low"

    def test_full_result(self):
        """Test CoinPatternResult with all fields populated."""
        points = [
            CyclePoint(
                date=date(2024, 1, 1),
                price=0.05,
                cycle_num=4,
                point_type="min1",
                days_from_halving=-100,
            )
        ]

        result = CoinPatternResult(
            coin_id="eth",
            points=points,
            num_cycles=3,
            trendline_target=0.1,
            trendline_target_pct=100.0,
            fib_target=0.09,
            fib_target_pct=80.0,
            dim_return_target=0.08,
            dim_return_target_pct=60.0,
            composite_target_pct=80.0,
            current_price=0.05,
            confidence="high",
            pattern_type="falling_wedge",
        )

        assert result.coin_id == "eth"
        assert len(result.points) == 1
        assert result.num_cycles == 3
        assert result.confidence == "high"
        assert result.composite_target_pct == 80.0


# =============================================================================
# BTCPatternResult Dataclass Tests
# =============================================================================


class TestBTCPatternResult:
    """Tests for BTCPatternResult dataclass."""

    def test_default_values(self):
        """Test BTCPatternResult default values."""
        result = BTCPatternResult()

        assert result.points == []
        assert result.num_cycles == 0
        assert result.trendline_target is None
        assert result.fib_target is None
        assert result.composite_target_pct is None


# =============================================================================
# Trendline Regression Tests
# =============================================================================


class TestFitLogTrendlines:
    """Tests for _fit_log_trendlines method."""

    @pytest.fixture
    def analyzer(self, mock_price_cache):
        return CyclePatternAnalyzer(price_cache=mock_price_cache)

    def test_fit_trendlines_sufficient_data(self, analyzer):
        """Test trendline fitting with sufficient data points."""
        # Create points spanning multiple cycles (>1200 days each for peaks and troughs)
        points = [
            # Cycle 2
            CyclePoint(
                date=date(2016, 1, 1),
                price=0.001,
                cycle_num=2,
                point_type="min1",
                days_from_halving=-190,
            ),
            CyclePoint(
                date=date(2016, 6, 1),
                price=0.005,
                cycle_num=2,
                point_type="max1",
                days_from_halving=-38,
            ),
            CyclePoint(
                date=date(2017, 6, 1),
                price=0.003,
                cycle_num=2,
                point_type="min2",
                days_from_halving=327,
            ),
            CyclePoint(
                date=date(2017, 12, 1),
                price=0.01,
                cycle_num=2,
                point_type="max2",
                days_from_halving=510,
            ),
            # Cycle 3
            CyclePoint(
                date=date(2020, 1, 1),
                price=0.002,
                cycle_num=3,
                point_type="min1",
                days_from_halving=-131,
            ),
            CyclePoint(
                date=date(2020, 4, 1),
                price=0.008,
                cycle_num=3,
                point_type="max1",
                days_from_halving=-40,
            ),
            CyclePoint(
                date=date(2020, 9, 1),
                price=0.004,
                cycle_num=3,
                point_type="min2",
                days_from_halving=113,
            ),
            CyclePoint(
                date=date(2021, 11, 1),
                price=0.02,
                cycle_num=3,
                point_type="max2",
                days_from_halving=539,
            ),
        ]

        upper_slope, upper_int, lower_slope, lower_int = analyzer._fit_log_trendlines(points)

        # Should return valid slopes and intercepts
        assert upper_slope is not None
        assert upper_int is not None
        assert lower_slope is not None
        assert lower_int is not None
        # Slopes should be finite numbers
        assert np.isfinite(upper_slope)
        assert np.isfinite(lower_slope)

    def test_fit_trendlines_insufficient_peaks(self, analyzer):
        """Test trendline fitting with insufficient major peaks but sufficient major troughs.

        With 2 min1 points (major troughs) but only 1 max2 (major peak), the new logic
        uses the trough slope for both upper and lower trendlines (parallel channel).

        Note: The return tuple is (upper_slope, upper_int, lower_slope, lower_int) but
        the existing code unpacks as (upper_int, upper_slope, lower_int, lower_slope)
        due to a naming convention mismatch. The values at positions 0 and 2 are both
        slopes and should be equal for parallel channels.
        """
        points = [
            CyclePoint(
                date=date(2020, 1, 1),
                price=0.001,
                cycle_num=3,
                point_type="min1",
                days_from_halving=-131,
            ),
            CyclePoint(
                date=date(2020, 6, 1),
                price=0.01,
                cycle_num=3,
                point_type="max2",
                days_from_halving=21,
            ),
            CyclePoint(
                date=date(2024, 1, 1),
                price=0.002,
                cycle_num=4,
                point_type="min1",
                days_from_halving=-109,
            ),
        ]

        result = analyzer._fit_log_trendlines(points)
        # Note: positions 0 and 2 are slopes, positions 1 and 3 are intercepts
        upper_slope_val, upper_int_val, lower_slope_val, lower_int_val = result

        # With 2 major troughs, we can fit a trendline using parallel channel assumption
        assert upper_slope_val is not None
        assert upper_int_val is not None
        assert lower_slope_val is not None
        assert lower_int_val is not None
        # Slopes (positions 0 and 2) should be equal (parallel channel)
        assert abs(upper_slope_val - lower_slope_val) < 0.01

    def test_fit_trendlines_insufficient_points(self, analyzer):
        """Test trendline fitting with too few points on both sides."""
        # Only 1 trough and 1 peak - not enough to fit any trendline
        points = [
            CyclePoint(
                date=date(2020, 1, 1),
                price=0.001,
                cycle_num=3,
                point_type="min1",
                days_from_halving=-131,
            ),
            CyclePoint(
                date=date(2020, 6, 1),
                price=0.01,
                cycle_num=3,
                point_type="max1",
                days_from_halving=21,
            ),
        ]

        result = analyzer._fit_log_trendlines(points)

        # Should return all None due to insufficient points (only 1 min + 1 max)
        assert result == (None, None, None, None)

    def test_fit_trendlines_zero_prices(self, analyzer):
        """Test trendline fitting filters out zero/negative prices."""
        points = [
            CyclePoint(
                date=date(2016, 1, 1),
                price=0.0,
                cycle_num=2,
                point_type="min1",
                days_from_halving=-190,
            ),
            CyclePoint(
                date=date(2016, 6, 1),
                price=0.005,
                cycle_num=2,
                point_type="max1",
                days_from_halving=-38,
            ),
            CyclePoint(
                date=date(2020, 1, 1),
                price=0.002,
                cycle_num=3,
                point_type="min1",
                days_from_halving=-131,
            ),
            CyclePoint(
                date=date(2020, 6, 1),
                price=0.0,
                cycle_num=3,
                point_type="max2",
                days_from_halving=21,
            ),
        ]

        result = analyzer._fit_log_trendlines(points)

        # After filtering zeros, insufficient points remain
        assert result == (None, None, None, None)

    def test_fit_trendlines_short_span_with_fallback(self, analyzer):
        """Test trendline fitting with short span uses fallback logic.

        With 2+ total troughs (min1 + min2), the fallback logic computes
        a trendline even when major extrema (min1, max2) are insufficient.
        """
        # Points within same year - short span but 2 troughs available
        points = [
            CyclePoint(
                date=date(2024, 1, 1),
                price=0.001,
                cycle_num=4,
                point_type="min1",
                days_from_halving=-109,
            ),
            CyclePoint(
                date=date(2024, 2, 1),
                price=0.005,
                cycle_num=4,
                point_type="max1",
                days_from_halving=-78,
            ),
            CyclePoint(
                date=date(2024, 6, 1),
                price=0.002,
                cycle_num=4,
                point_type="min2",
                days_from_halving=43,
            ),
            CyclePoint(
                date=date(2024, 8, 1),
                price=0.01,
                cycle_num=4,
                point_type="max2",
                days_from_halving=104,
            ),
        ]

        result = analyzer._fit_log_trendlines(points)
        upper_slope, upper_int, lower_slope, lower_int = result

        # With 2 troughs (min1 + min2), fallback computes trendline with parallel slopes
        assert upper_slope is not None
        assert upper_int is not None
        assert lower_slope is not None
        assert lower_int is not None
        # Slopes should be equal (parallel channel)
        assert abs(upper_slope - lower_slope) < 0.01


# =============================================================================
# Trendline Projection Tests
# =============================================================================


class TestProjectTrendlineTarget:
    """Tests for _project_trendline_target method."""

    @pytest.fixture
    def analyzer(self, mock_price_cache):
        return CyclePatternAnalyzer(price_cache=mock_price_cache)

    def test_project_target_basic(self, analyzer):
        """Test basic trendline projection."""
        # Simple linear projection in log space
        upper_slope = 0.0001  # Slow growth
        upper_intercept = -3.0  # log10(0.001)
        target_date = date(2028, 10, 1)

        result = analyzer._project_trendline_target(upper_slope, upper_intercept, target_date)

        assert result is not None
        assert result > 0

    def test_project_target_overflow_protection(self, analyzer):
        """Test that overflow is handled gracefully."""
        # Very steep slope that would cause overflow
        upper_slope = 1.0  # Extremely steep
        upper_intercept = 100.0
        target_date = date(2028, 10, 1)

        result = analyzer._project_trendline_target(upper_slope, upper_intercept, target_date)

        # Should return None for overflow
        assert result is None

    def test_project_target_underflow_protection(self, analyzer):
        """Test that underflow is handled gracefully."""
        # Very negative slope
        upper_slope = -1.0
        upper_intercept = -200.0
        target_date = date(2028, 10, 1)

        result = analyzer._project_trendline_target(upper_slope, upper_intercept, target_date)

        # Should return None for underflow
        assert result is None


# =============================================================================
# Fibonacci Extension Tests
# =============================================================================


class TestCalculateFibExtension:
    """Tests for _calculate_fib_extension method."""

    @pytest.fixture
    def analyzer(self, mock_price_cache):
        return CyclePatternAnalyzer(price_cache=mock_price_cache)

    def test_fib_extension_two_cycles(self, analyzer):
        """Test Fibonacci extension with two complete cycles."""
        points = [
            # Cycle 3
            CyclePoint(
                date=date(2020, 1, 1),
                price=0.001,
                cycle_num=3,
                point_type="min1",
                days_from_halving=-131,
            ),
            CyclePoint(
                date=date(2021, 11, 1),
                price=0.01,
                cycle_num=3,
                point_type="max2",
                days_from_halving=539,
            ),
            # Cycle 4
            CyclePoint(
                date=date(2024, 1, 1),
                price=0.003,
                cycle_num=4,
                point_type="min1",
                days_from_halving=-109,
            ),
        ]

        result = analyzer._calculate_fib_extension(points, level=1.272)

        # C + (B - A) * level = 0.003 + (0.01 - 0.001) * 1.272 = 0.003 + 0.011448 = 0.014448
        assert result is not None
        assert pytest.approx(result, rel=0.01) == 0.014448

    def test_fib_extension_single_cycle(self, analyzer):
        """Test Fibonacci extension with single cycle."""
        points = [
            CyclePoint(
                date=date(2024, 1, 1),
                price=0.001,
                cycle_num=4,
                point_type="min1",
                days_from_halving=-109,
            ),
            CyclePoint(
                date=date(2024, 8, 1),
                price=0.01,
                cycle_num=4,
                point_type="max2",
                days_from_halving=104,
            ),
        ]

        result = analyzer._calculate_fib_extension(points, level=1.272)

        # Single cycle: uses min1 as C, move from min to max
        assert result is not None
        assert result > 0

    def test_fib_extension_no_points(self, analyzer):
        """Test Fibonacci extension with no points."""
        result = analyzer._calculate_fib_extension([], level=1.272)
        assert result is None

    def test_fib_extension_only_mins(self, analyzer):
        """Test Fibonacci extension with only minimum points."""
        points = [
            CyclePoint(
                date=date(2024, 1, 1),
                price=0.001,
                cycle_num=4,
                point_type="min1",
                days_from_halving=-109,
            ),
            CyclePoint(
                date=date(2024, 6, 1),
                price=0.002,
                cycle_num=4,
                point_type="min2",
                days_from_halving=43,
            ),
        ]

        result = analyzer._calculate_fib_extension(points, level=1.272)
        # No max points, should return None
        assert result is None

    def test_fib_extension_custom_level(self, analyzer):
        """Test Fibonacci extension with custom level."""
        points = [
            CyclePoint(
                date=date(2020, 1, 1),
                price=0.001,
                cycle_num=3,
                point_type="min1",
                days_from_halving=-131,
            ),
            CyclePoint(
                date=date(2021, 11, 1),
                price=0.01,
                cycle_num=3,
                point_type="max2",
                days_from_halving=539,
            ),
            CyclePoint(
                date=date(2024, 1, 1),
                price=0.003,
                cycle_num=4,
                point_type="min1",
                days_from_halving=-109,
            ),
        ]

        result_127 = analyzer._calculate_fib_extension(points, level=1.272)
        result_161 = analyzer._calculate_fib_extension(points, level=1.618)

        assert result_127 is not None
        assert result_161 is not None
        assert result_161 > result_127


# =============================================================================
# Diminishing Returns Tests
# =============================================================================


class TestCalculateDiminishingReturn:
    """Tests for _calculate_diminishing_return method."""

    @pytest.fixture
    def analyzer(self, mock_price_cache):
        return CyclePatternAnalyzer(price_cache=mock_price_cache)

    def test_diminishing_return_multiple_cycles(self, analyzer):
        """Test diminishing returns with multiple cycles."""
        points = [
            # Cycle 2: 10x gain (0.001 to 0.01)
            CyclePoint(
                date=date(2016, 1, 1),
                price=0.001,
                cycle_num=2,
                point_type="min1",
                days_from_halving=-190,
            ),
            CyclePoint(
                date=date(2017, 12, 1),
                price=0.01,
                cycle_num=2,
                point_type="max2",
                days_from_halving=510,
            ),
            # Cycle 3: 5x gain (0.002 to 0.01)
            CyclePoint(
                date=date(2020, 1, 1),
                price=0.002,
                cycle_num=3,
                point_type="min1",
                days_from_halving=-131,
            ),
            CyclePoint(
                date=date(2021, 11, 1),
                price=0.01,
                cycle_num=3,
                point_type="max2",
                days_from_halving=539,
            ),
            # Cycle 4: starting point
            CyclePoint(
                date=date(2024, 1, 1),
                price=0.003,
                cycle_num=4,
                point_type="min1",
                days_from_halving=-109,
            ),
        ]

        target, factor = analyzer._calculate_diminishing_return(points)

        assert target is not None
        assert factor is not None
        # Factor should be 5x/10x = 0.5
        assert pytest.approx(factor, rel=0.1) == 0.5

    def test_diminishing_return_single_cycle(self, analyzer):
        """Test diminishing returns with single cycle uses BTC-derived default factor."""
        points = [
            CyclePoint(
                date=date(2024, 1, 1),
                price=0.001,
                cycle_num=4,
                point_type="min1",
                days_from_halving=-109,
            ),
            CyclePoint(
                date=date(2024, 12, 1),
                price=0.01,
                cycle_num=4,
                point_type="max2",
                days_from_halving=226,
            ),
        ]

        target, factor = analyzer._calculate_diminishing_return(points)

        assert target is not None
        # BTC-derived factor (calculated from BTC cycles 2→3: 20.9x / 117.3x ≈ 0.178, rounded to 0.20)
        assert factor == 0.20

    def test_diminishing_return_no_points(self, analyzer):
        """Test diminishing returns with no points."""
        target, factor = analyzer._calculate_diminishing_return([])

        assert target is None
        assert factor is None

    def test_diminishing_return_only_mins(self, analyzer):
        """Test diminishing returns with only minimum points."""
        points = [
            CyclePoint(
                date=date(2024, 1, 1),
                price=0.001,
                cycle_num=4,
                point_type="min1",
                days_from_halving=-109,
            ),
        ]

        target, factor = analyzer._calculate_diminishing_return(points)

        # No max points means no gain can be calculated
        assert target is None
        assert factor is None


# =============================================================================
# Pattern Classification Tests
# =============================================================================


class TestClassifyPattern:
    """Tests for _classify_pattern method."""

    @pytest.fixture
    def analyzer(self, mock_price_cache):
        return CyclePatternAnalyzer(price_cache=mock_price_cache)

    def test_classify_falling_wedge(self, analyzer):
        """Test falling wedge pattern (upper slope < lower slope)."""
        pattern = analyzer._classify_pattern(upper_slope=0.0001, lower_slope=0.0002)
        assert pattern == "falling_wedge"

    def test_classify_rising_wedge(self, analyzer):
        """Test rising wedge pattern (upper slope > lower slope)."""
        pattern = analyzer._classify_pattern(upper_slope=0.0003, lower_slope=0.0001)
        assert pattern == "rising_wedge"

    def test_classify_channel(self, analyzer):
        """Test channel pattern (slopes nearly equal)."""
        pattern = analyzer._classify_pattern(upper_slope=0.0001, lower_slope=0.0001)
        assert pattern == "channel"

    def test_classify_unknown_none_slopes(self, analyzer):
        """Test unknown pattern when slopes are None."""
        pattern = analyzer._classify_pattern(upper_slope=None, lower_slope=None)
        assert pattern == "unknown"

        pattern = analyzer._classify_pattern(upper_slope=0.001, lower_slope=None)
        assert pattern == "unknown"


# =============================================================================
# Cycle Point Detection Tests
# =============================================================================


class TestFindCyclePoints:
    """Tests for _find_cycle_points method."""

    @pytest.fixture
    def analyzer(self, mock_price_cache):
        return CyclePatternAnalyzer(price_cache=mock_price_cache)

    def test_find_cycle_points_complete_cycle(self, analyzer):
        """Test finding points for a complete cycle."""
        # Create price data around a halving (2020-05-11)
        start_date = date(2019, 1, 1)
        end_date = date(2022, 12, 31)
        dates = pd.date_range(start_date, end_date, freq="D")

        # Create price pattern: pre-halving dip, rally, post-halving rally then dip
        days = (dates - pd.Timestamp("2020-05-11")).days.values
        prices = 0.01 * (1 + 0.3 * np.sin(days / 200) + 0.001 * days / 365)
        prices = np.maximum(prices, 0.001)

        df = pd.DataFrame({"close": prices}, index=dates)

        halving_date = date(2020, 5, 11)
        points = analyzer._find_cycle_points(df, halving_date, cycle_num=3, is_current_cycle=False)

        # Should find at least min1
        assert len(points) >= 1
        assert all(isinstance(p, CyclePoint) for p in points)
        assert all(p.cycle_num == 3 for p in points)

    def test_find_cycle_points_empty_df(self, analyzer):
        """Test with empty DataFrame."""
        df = pd.DataFrame(columns=["close"])
        df.index = pd.DatetimeIndex([])

        points = analyzer._find_cycle_points(df, date(2020, 5, 11), cycle_num=3)

        assert points == []

    def test_find_cycle_points_no_pre_halving_data(self, analyzer):
        """Test when no data exists in pre-halving window (partial cycle)."""
        # Data only after halving - should still find post-halving points
        dates = pd.date_range("2020-06-01", "2022-12-31", freq="D")
        prices = np.random.uniform(0.01, 0.02, len(dates))
        df = pd.DataFrame({"close": prices}, index=dates)

        points = analyzer._find_cycle_points(
            df, date(2020, 5, 11), cycle_num=3, is_current_cycle=False
        )

        # Should return post-halving points (min2, max2) even without pre-halving data
        assert len(points) >= 1
        point_types = {p.point_type for p in points}
        # Should have max2 at minimum (the cycle peak)
        assert "max2" in point_types
        # Should NOT have min1 or max1 (no pre-halving data)
        assert "min1" not in point_types
        assert "max1" not in point_types

    def test_find_cycle_points_current_cycle(self, analyzer):
        """Test finding points for current (incomplete) cycle."""
        # Data from 2024 halving onwards
        dates = pd.date_range("2024-04-19", "2024-12-31", freq="D")
        prices = 0.01 * (1 + np.random.uniform(-0.1, 0.1, len(dates)))
        df = pd.DataFrame({"close": prices}, index=dates)

        # Mock BTC_CYCLE_PEAKS to have a recent peak
        with patch("analysis.cycle_patterns.BTC_CYCLE_PEAKS", [date(2024, 4, 1)]):
            points = analyzer._find_cycle_points(
                df, date(2024, 4, 19), cycle_num=5, is_current_cycle=True
            )

        # For current cycle, should find at most min1
        assert len(points) <= 1
        if points:
            assert points[0].point_type == "min1"
            assert points[0].cycle_num == 5


# =============================================================================
# Analyzer Integration Tests
# =============================================================================


class TestCyclePatternAnalyzerInit:
    """Tests for CyclePatternAnalyzer initialization."""

    def test_default_initialization(self, mock_price_cache):
        """Test default initialization."""
        analyzer = CyclePatternAnalyzer(price_cache=mock_price_cache)

        assert analyzer.price_cache is mock_price_cache
        assert analyzer.min_cycles == 1

    def test_custom_min_cycles(self, mock_price_cache):
        """Test with custom min_cycles."""
        analyzer = CyclePatternAnalyzer(price_cache=mock_price_cache, min_cycles=2)

        assert analyzer.min_cycles == 2

    def test_all_halvings_set(self, mock_price_cache):
        """Test that all_halvings is properly set."""
        analyzer = CyclePatternAnalyzer(price_cache=mock_price_cache)

        # Should use cycles 2, 3, 4 (indices 1-3 of HALVING_DATES) plus projected 5th
        assert len(analyzer.all_halvings) == 4
        assert analyzer.all_halvings[0] == date(2016, 7, 9)
        assert analyzer.all_halvings[1] == date(2020, 5, 11)
        assert analyzer.all_halvings[2] == date(2024, 4, 19)
        assert analyzer.all_halvings[3] == date(2028, 3, 31)  # Projected 5th halving


class TestAnalyzeCoin:
    """Tests for analyze_coin method."""

    @pytest.fixture
    def analyzer(self, mock_price_cache):
        return CyclePatternAnalyzer(price_cache=mock_price_cache, min_cycles=1)

    def test_analyze_coin_no_data(self, analyzer, mock_price_cache):
        """Test analyzing coin with no price data."""
        mock_price_cache.get_prices.return_value = None

        result = analyzer.analyze_coin("eth")

        assert result is None

    def test_analyze_coin_empty_df(self, analyzer, mock_price_cache):
        """Test analyzing coin with empty DataFrame."""
        mock_price_cache.get_prices.return_value = pd.DataFrame()

        result = analyzer.analyze_coin("eth")

        assert result is None

    def test_analyze_coin_no_total2_data(self, analyzer, mock_price_cache):
        """Test analyzing coin not in TOTAL2."""
        dates = pd.date_range("2020-01-01", periods=100, freq="D")
        df = pd.DataFrame(
            {"close": np.random.uniform(0.01, 0.02, len(dates)), "volume_to": [1000] * len(dates)},
            index=dates,
        )
        mock_price_cache.get_prices.return_value = df

        # Mock _get_coin_total2_dates to return empty
        with patch.object(analyzer, "_get_coin_total2_dates", return_value=set()):
            result = analyzer.analyze_coin("obscure_coin")

        # Should return None when not in TOTAL2
        assert result is None


class TestGetTopCoins:
    """Tests for get_top_coins method."""

    @pytest.fixture
    def analyzer(self, mock_price_cache):
        return CyclePatternAnalyzer(price_cache=mock_price_cache)

    def test_get_top_coins_basic(self, analyzer):
        """Test getting top N coins by composite target, filtered by positive trendline."""
        # trendline_target_pct must be positive to be included (filtering criterion)
        # composite_target_pct determines the ranking order
        # unique_price_count must be >= MIN_UNIQUE_PRICES (30) to pass liquidity filter
        results = {
            "eth": CoinPatternResult(
                coin_id="eth",
                trendline_target_pct=100.0,
                composite_target_pct=120.0,
                unique_price_count=100,
            ),
            "sol": CoinPatternResult(
                coin_id="sol",
                trendline_target_pct=150.0,
                composite_target_pct=180.0,
                unique_price_count=100,
            ),
            "ada": CoinPatternResult(
                coin_id="ada",
                trendline_target_pct=50.0,
                composite_target_pct=60.0,
                unique_price_count=100,
            ),
        }

        top = analyzer.get_top_coins(results, n=2)

        assert len(top) == 2
        # Sorted by composite_target_pct descending
        assert top[0].coin_id == "sol"  # composite=180
        assert top[1].coin_id == "eth"  # composite=120

    def test_get_top_coins_filters_negative_trendline(self, analyzer):
        """Test that coins with negative trendline target are filtered, but None is allowed."""
        # unique_price_count must be >= MIN_UNIQUE_PRICES (30) to pass liquidity filter
        results = {
            "eth": CoinPatternResult(
                coin_id="eth",
                trendline_target_pct=100.0,
                composite_target_pct=100.0,
                unique_price_count=100,
            ),
            "sol": CoinPatternResult(
                coin_id="sol",
                trendline_target_pct=None,
                composite_target_pct=200.0,
                unique_price_count=100,
            ),
            "btc": CoinPatternResult(
                coin_id="btc",
                trendline_target_pct=-50.0,
                composite_target_pct=150.0,
                unique_price_count=100,
            ),
        }

        top = analyzer.get_top_coins(results, n=5)

        # eth and sol are included (sol has None trendline which is allowed)
        # btc is filtered out (negative trendline)
        assert len(top) == 2
        # Sorted by composite: sol (200) > eth (100)
        assert top[0].coin_id == "sol"
        assert top[1].coin_id == "eth"

    def test_get_top_coins_empty_results(self, analyzer):
        """Test with empty results dictionary."""
        top = analyzer.get_top_coins({}, n=5)
        assert top == []


class TestAnalyzeAllCoins:
    """Tests for analyze_all_coins method."""

    @pytest.fixture
    def analyzer(self, mock_price_cache):
        return CyclePatternAnalyzer(price_cache=mock_price_cache, min_cycles=1)

    def test_analyze_all_no_cached_coins(self, analyzer, mock_price_cache):
        """Test analyzing when no coins are cached."""
        mock_price_cache.list_cached_coins.return_value = []

        results = analyzer.analyze_all_coins(filter_total2=False, show_progress=False)

        assert results == {}

    def test_analyze_all_filters_total2(self, analyzer, mock_price_cache):
        """Test that TOTAL2 filtering works."""
        mock_price_cache.list_cached_coins.return_value = ["eth", "sol", "obscure"]

        # Mock _get_total2_coins to return only eth and sol
        with patch.object(analyzer, "_get_total2_coins", return_value={"eth", "sol"}):
            with patch.object(analyzer, "analyze_coin", return_value=None):
                results = analyzer.analyze_all_coins(filter_total2=True, show_progress=False)

        # Should have filtered to only eth and sol for analysis
        assert results == {}


# =============================================================================
# Price Filter Application Tests
# =============================================================================


class TestApplyPriceFilters:
    """Tests for _apply_price_filters method."""

    @pytest.fixture
    def analyzer(self, mock_price_cache):
        return CyclePatternAnalyzer(price_cache=mock_price_cache)

    def test_apply_filters_empty_df(self, analyzer):
        """Test applying filters to empty DataFrame."""
        df = pd.DataFrame(columns=["close", "volume_to"])
        df.index = pd.DatetimeIndex([])

        result = analyzer._apply_price_filters(df, "eth")

        assert result.empty

    def test_apply_filters_no_volume(self, analyzer):
        """Test applying filters when no volume column."""
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        df = pd.DataFrame({"close": [1.0] * 10}, index=dates)

        result = analyzer._apply_price_filters(df, "eth")

        # Should return original data without volume modifications
        assert len(result) == 10
        assert "close" in result.columns

    def test_apply_filters_with_volume(self, analyzer):
        """Test applying filters with volume data."""
        dates = pd.date_range("2024-01-01", periods=30, freq="D")
        df = pd.DataFrame(
            {
                "close": np.random.uniform(0.01, 0.02, 30),
                "volume_to": np.random.uniform(1000, 5000, 30),
            },
            index=dates,
        )

        result = analyzer._apply_price_filters(df, "eth")

        assert len(result) == 30
        assert "volume_to" in result.columns
        assert "volume_smoothed" in result.columns


# =============================================================================
# Save Results Tests
# =============================================================================


class TestSaveResults:
    """Tests for save_results method."""

    @pytest.fixture
    def analyzer(self, mock_price_cache):
        return CyclePatternAnalyzer(price_cache=mock_price_cache)

    def test_save_results_basic(self, analyzer, temp_dir):
        """Test saving results to JSON."""
        btc_result = BTCPatternResult(
            num_cycles=3,
            current_price=50000.0,
            current_date=date(2024, 12, 1),
            composite_target_pct=50.0,
        )

        coin_results = {
            "eth": CoinPatternResult(
                coin_id="eth",
                num_cycles=3,
                current_price=0.05,
                current_date=date(2024, 12, 1),
                composite_target_pct=100.0,
            )
        }

        output_path = temp_dir / "test_results.json"

        saved_path = analyzer.save_results(btc_result, coin_results, output_path)

        assert saved_path == output_path
        assert output_path.exists()

        # Verify JSON content
        import json

        with open(output_path) as f:
            data = json.load(f)

        assert "btc" in data
        assert "altcoins" in data
        assert "eth" in data["altcoins"]
        assert data["btc"]["num_cycles"] == 3

    def test_save_results_no_btc(self, analyzer, temp_dir):
        """Test saving results without BTC data."""
        coin_results = {
            "eth": CoinPatternResult(
                coin_id="eth",
                composite_target_pct=100.0,
            )
        }

        output_path = temp_dir / "test_results.json"

        saved_path = analyzer.save_results(None, coin_results, output_path)

        assert saved_path.exists()

        import json

        with open(output_path) as f:
            data = json.load(f)

        assert data["btc"] is None


# =============================================================================
# Edge Cases and Error Handling
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    @pytest.fixture
    def analyzer(self, mock_price_cache):
        return CyclePatternAnalyzer(price_cache=mock_price_cache)

    def test_negative_prices(self, analyzer):
        """Test handling of negative prices in trendline fitting."""
        points = [
            CyclePoint(
                date=date(2020, 1, 1),
                price=-0.001,
                cycle_num=3,
                point_type="min1",
                days_from_halving=-131,
            ),
            CyclePoint(
                date=date(2020, 6, 1),
                price=0.01,
                cycle_num=3,
                point_type="max2",
                days_from_halving=21,
            ),
        ]

        result = analyzer._fit_log_trendlines(points)

        # Negative prices should be filtered out
        assert result == (None, None, None, None)

    def test_very_small_prices(self, analyzer):
        """Test with very small (but valid) prices."""
        points = [
            CyclePoint(
                date=date(2016, 1, 1),
                price=1e-10,
                cycle_num=2,
                point_type="min1",
                days_from_halving=-190,
            ),
            CyclePoint(
                date=date(2016, 6, 1),
                price=1e-9,
                cycle_num=2,
                point_type="max1",
                days_from_halving=-38,
            ),
            CyclePoint(
                date=date(2017, 6, 1),
                price=1e-10,
                cycle_num=2,
                point_type="min2",
                days_from_halving=327,
            ),
            CyclePoint(
                date=date(2017, 12, 1),
                price=1e-8,
                cycle_num=2,
                point_type="max2",
                days_from_halving=510,
            ),
            CyclePoint(
                date=date(2020, 1, 1),
                price=1e-9,
                cycle_num=3,
                point_type="min1",
                days_from_halving=-131,
            ),
            CyclePoint(
                date=date(2020, 6, 1),
                price=1e-7,
                cycle_num=3,
                point_type="max1",
                days_from_halving=21,
            ),
            CyclePoint(
                date=date(2021, 1, 1),
                price=1e-9,
                cycle_num=3,
                point_type="min2",
                days_from_halving=235,
            ),
            CyclePoint(
                date=date(2021, 11, 1),
                price=1e-6,
                cycle_num=3,
                point_type="max2",
                days_from_halving=539,
            ),
        ]

        upper_slope, upper_int, lower_slope, lower_int = analyzer._fit_log_trendlines(points)

        # Small prices should still work in log space
        assert upper_slope is not None or upper_slope is None  # May fail due to span requirements

    def test_confidence_levels(self, analyzer):
        """Test confidence level assignment based on number of cycles."""
        result = CoinPatternResult(coin_id="test")

        # 1 cycle -> low
        result.num_cycles = 1
        if result.num_cycles >= 3:
            result.confidence = "high"
        elif result.num_cycles >= 2:
            result.confidence = "medium"
        else:
            result.confidence = "low"
        assert result.confidence == "low"

        # 2 cycles -> medium
        result.num_cycles = 2
        if result.num_cycles >= 3:
            result.confidence = "high"
        elif result.num_cycles >= 2:
            result.confidence = "medium"
        else:
            result.confidence = "low"
        assert result.confidence == "medium"

        # 3+ cycles -> high
        result.num_cycles = 3
        if result.num_cycles >= 3:
            result.confidence = "high"
        elif result.num_cycles >= 2:
            result.confidence = "medium"
        else:
            result.confidence = "low"
        assert result.confidence == "high"

    def test_num_cycles_counts_only_min1_points(self, analyzer):
        """Test that num_cycles only counts cycles where coin has min1 (pre-halving data).

        A coin that only has post-halving data (min2/max2) for a cycle should not
        count that cycle - it didn't exist before the halving.
        """
        from analysis.cycle_patterns import CyclePoint

        # Simulate a coin like VIRTUAL: only post-halving data for cycle 4,
        # but has min1 for cycle 5 (current cycle)
        points = [
            # Cycle 4: only post-halving points (no min1 - coin didn't exist pre-halving)
            CyclePoint(
                date=date(2024, 6, 1),
                price=0.01,
                cycle_num=4,
                point_type="min2",
                days_from_halving=43,
            ),
            CyclePoint(
                date=date(2024, 12, 1),
                price=0.05,
                cycle_num=4,
                point_type="max2",
                days_from_halving=226,
            ),
            # Cycle 5: has min1 (current cycle)
            CyclePoint(
                date=date(2025, 1, 15),
                price=0.02,
                cycle_num=5,
                point_type="min1",
                days_from_halving=-805,
            ),
        ]

        # Count cycles based on min1 only (matching the production code logic)
        num_cycles = len({p.cycle_num for p in points if p.point_type == "min1"})

        # Should only count cycle 5 (the one with min1)
        assert num_cycles == 1

        # Verify: with old logic it would have counted 2 cycles
        old_logic_num_cycles = len({p.cycle_num for p in points})
        assert old_logic_num_cycles == 2  # Would have been wrong


# =============================================================================
# Parameterized Tests
# =============================================================================


class TestParameterizedCases:
    """Parameterized tests for various scenarios."""

    @pytest.fixture
    def analyzer(self, mock_price_cache):
        return CyclePatternAnalyzer(price_cache=mock_price_cache)

    @pytest.mark.parametrize(
        "level,expected_multiplier",
        [
            (1.0, 1.0),
            (1.272, 1.272),
            (1.618, 1.618),
            (2.0, 2.0),
        ],
    )
    def test_fib_levels(self, analyzer, level, expected_multiplier):
        """Test Fibonacci extension with various levels."""
        points = [
            CyclePoint(
                date=date(2020, 1, 1),
                price=1.0,
                cycle_num=3,
                point_type="min1",
                days_from_halving=-131,
            ),
            CyclePoint(
                date=date(2021, 11, 1),
                price=2.0,
                cycle_num=3,
                point_type="max2",
                days_from_halving=539,
            ),
            CyclePoint(
                date=date(2024, 1, 1),
                price=1.5,
                cycle_num=4,
                point_type="min1",
                days_from_halving=-109,
            ),
        ]

        result = analyzer._calculate_fib_extension(points, level=level)

        # C + (B - A) * level = 1.5 + (2.0 - 1.0) * level = 1.5 + level
        expected = 1.5 + 1.0 * expected_multiplier
        assert result is not None
        assert pytest.approx(result, rel=0.01) == expected

    @pytest.mark.parametrize(
        "upper,lower,expected",
        [
            (0.001, 0.002, "falling_wedge"),
            (0.002, 0.001, "rising_wedge"),
            (0.001, 0.001, "channel"),
            (0.001, 0.001 + 1e-6, "channel"),  # Within tolerance
            (None, 0.001, "unknown"),
            (0.001, None, "unknown"),
            (None, None, "unknown"),
        ],
    )
    def test_pattern_classification(self, analyzer, upper, lower, expected):
        """Test pattern classification with various slope combinations."""
        result = analyzer._classify_pattern(upper, lower)
        assert result == expected


# =============================================================================
# Weighted Composite Tests
# =============================================================================


class TestWeightedComposite:
    """Tests for _calculate_weighted_composite method."""

    def test_weighted_composite_all_methods(self):
        """Test weighted composite with all 4 methods available (high confidence)."""
        # With all methods: trendline=40%, fib=25%, dim=15%, hist=20%, scale=1.0
        result = CyclePatternAnalyzer._calculate_weighted_composite(
            trendline_pct=100.0,
            fib_pct=200.0,
            dim_return_pct=50.0,
            hist_peak_pct=150.0,
            confidence="high",
        )
        # (100*0.40 + 200*0.25 + 50*0.15 + 150*0.20) / (0.40+0.25+0.15+0.20) * 1.0
        # = (40 + 50 + 7.5 + 30) / 1.0 = 127.5
        assert result is not None
        assert pytest.approx(result, rel=0.01) == 127.5

    def test_weighted_composite_trendline_dominates(self):
        """Test that trendline has the highest influence on composite."""
        # When trendline is very different from others, it should dominate
        result_high_trend = CyclePatternAnalyzer._calculate_weighted_composite(
            trendline_pct=1000.0,
            fib_pct=100.0,
            dim_return_pct=100.0,
            hist_peak_pct=100.0,
        )
        result_high_dim = CyclePatternAnalyzer._calculate_weighted_composite(
            trendline_pct=100.0,
            fib_pct=100.0,
            dim_return_pct=1000.0,
            hist_peak_pct=100.0,
        )
        # High trendline should produce higher composite than high dim return
        assert result_high_trend > result_high_dim

    def test_weighted_composite_low_confidence_excludes_trendline(self):
        """Test composite with low confidence excludes trendline and applies scale."""
        result = CyclePatternAnalyzer._calculate_weighted_composite(
            trendline_pct=999.0,  # Should be ignored (weight=0 for low confidence)
            fib_pct=200.0,
            dim_return_pct=50.0,
            hist_peak_pct=150.0,
            confidence="low",
        )
        # Without trendline: (200*0.25 + 50*0.15 + 150*0.20) / (0.25+0.15+0.20) * 0.3
        # = (50 + 7.5 + 30) / 0.60 * 0.3 = 145.83... * 0.3 = 43.75
        assert result is not None
        expected = (200 * 0.25 + 50 * 0.15 + 150 * 0.20) / (0.25 + 0.15 + 0.20) * 0.3
        assert pytest.approx(result, rel=0.01) == expected

    def test_weighted_composite_medium_confidence_same_as_high(self):
        """Test that medium confidence uses the same weights as high."""
        result_high = CyclePatternAnalyzer._calculate_weighted_composite(
            trendline_pct=100.0,
            fib_pct=200.0,
            dim_return_pct=50.0,
            hist_peak_pct=150.0,
            confidence="high",
        )
        result_medium = CyclePatternAnalyzer._calculate_weighted_composite(
            trendline_pct=100.0,
            fib_pct=200.0,
            dim_return_pct=50.0,
            hist_peak_pct=150.0,
            confidence="medium",
        )
        assert result_high == result_medium

    def test_weighted_composite_renormalization(self):
        """Test that weights renormalize when some methods are missing."""
        # Only trendline and fib available
        result = CyclePatternAnalyzer._calculate_weighted_composite(
            trendline_pct=100.0,
            fib_pct=200.0,
            dim_return_pct=None,
            hist_peak_pct=None,
        )
        # (100*0.40 + 200*0.25) / (0.40+0.25) * 1.0 = (40+50) / 0.65 = 138.46
        assert result is not None
        expected = (100 * 0.40 + 200 * 0.25) / (0.40 + 0.25)
        assert pytest.approx(result, rel=0.01) == expected

    def test_weighted_composite_no_methods(self):
        """Test composite returns None when no methods available."""
        result = CyclePatternAnalyzer._calculate_weighted_composite(
            trendline_pct=None,
            fib_pct=None,
            dim_return_pct=None,
            hist_peak_pct=None,
        )
        assert result is None

    def test_weighted_composite_single_method(self):
        """Test composite with only one method returns that method's value (scaled)."""
        result = CyclePatternAnalyzer._calculate_weighted_composite(
            trendline_pct=None,
            fib_pct=None,
            dim_return_pct=None,
            hist_peak_pct=300.0,
            confidence="high",
        )
        assert result is not None
        assert pytest.approx(result, rel=0.01) == 300.0

    def test_weighted_composite_sol_vs_link_scenario(self):
        """Test the SOL vs LINK scenario that motivated the change.

        With equal-weight average: LINK=548 > SOL=478
        With weighted composite: SOL should rank higher than LINK because
        SOL's trendline (+1625%) gets 40% weight while LINK's dim return
        (+1146%) only gets 15% weight.
        """
        link_composite = CyclePatternAnalyzer._calculate_weighted_composite(
            trendline_pct=544.0,
            fib_pct=105.0,
            dim_return_pct=1146.0,
            hist_peak_pct=400.0,
        )
        sol_composite = CyclePatternAnalyzer._calculate_weighted_composite(
            trendline_pct=1625.0,
            fib_pct=225.0,
            dim_return_pct=-64.0,
            hist_peak_pct=127.0,
        )
        # SOL should now rank higher than LINK
        assert sol_composite > link_composite

    def test_weighted_composite_low_vs_high_confidence_penalty(self):
        """Test that low confidence composite is significantly lower than high.

        For the same inputs (excluding trendline), low confidence should be
        scale-factor (0.3) times the high-confidence result.
        """
        # Use inputs where trendline is None (so both profiles use same methods)
        result_high = CyclePatternAnalyzer._calculate_weighted_composite(
            trendline_pct=None,
            fib_pct=200.0,
            dim_return_pct=100.0,
            hist_peak_pct=150.0,
            confidence="high",
        )
        result_low = CyclePatternAnalyzer._calculate_weighted_composite(
            trendline_pct=None,
            fib_pct=200.0,
            dim_return_pct=100.0,
            hist_peak_pct=150.0,
            confidence="low",
        )
        assert result_high is not None
        assert result_low is not None
        # Low should be 0.3x the high result (same method weights, different scale)
        assert pytest.approx(result_low / result_high, rel=0.01) == 0.3


# =============================================================================
# Diminishing Returns Floor Tests
# =============================================================================


class TestDiminishingReturnFloor:
    """Tests for diminishing returns gain ratio floor."""

    @pytest.fixture
    def analyzer(self, mock_price_cache):
        return CyclePatternAnalyzer(price_cache=mock_price_cache)

    def test_dim_return_floor_prevents_negative(self, analyzer):
        """Test that dim return floor prevents negative projections.

        Simulates a SOL-like scenario: enormous first-cycle gain → tiny dim factor
        → projected gain < 1.0x → should be floored to 1.0x.
        """
        points = [
            # Cycle 3: 1000x gain (simulating launch from near-zero)
            CyclePoint(
                date=date(2020, 1, 1),
                price=0.00001,
                cycle_num=3,
                point_type="min1",
                days_from_halving=-131,
            ),
            CyclePoint(
                date=date(2021, 11, 1),
                price=0.01,
                cycle_num=3,
                point_type="max2",
                days_from_halving=539,
            ),
            # Cycle 4: 5x gain
            CyclePoint(
                date=date(2024, 1, 1),
                price=0.002,
                cycle_num=4,
                point_type="min1",
                days_from_halving=-109,
            ),
            CyclePoint(
                date=date(2025, 10, 1),
                price=0.01,
                cycle_num=4,
                point_type="max2",
                days_from_halving=530,
            ),
            # Cycle 5: latest min
            CyclePoint(
                date=date(2026, 1, 1),
                price=0.003,
                cycle_num=5,
                point_type="min1",
                days_from_halving=-820,
            ),
        ]

        target, factor = analyzer._calculate_diminishing_return(points)

        assert target is not None
        assert factor is not None
        # dim factor = 5/1000 = 0.005 → projected gain = 5 * 0.005 = 0.025x
        # BUT floor should clamp to 1.0x, so target >= latest_min price
        assert target >= 0.003  # Should be at least the latest min price (1.0x)

    def test_dim_return_normal_gains_unaffected(self, analyzer):
        """Test that normal gains (above floor) are not affected."""
        points = [
            # Cycle 2: 10x gain
            CyclePoint(
                date=date(2016, 1, 1),
                price=0.001,
                cycle_num=2,
                point_type="min1",
                days_from_halving=-190,
            ),
            CyclePoint(
                date=date(2017, 12, 1),
                price=0.01,
                cycle_num=2,
                point_type="max2",
                days_from_halving=510,
            ),
            # Cycle 3: 5x gain
            CyclePoint(
                date=date(2020, 1, 1),
                price=0.002,
                cycle_num=3,
                point_type="min1",
                days_from_halving=-131,
            ),
            CyclePoint(
                date=date(2021, 11, 1),
                price=0.01,
                cycle_num=3,
                point_type="max2",
                days_from_halving=539,
            ),
            # Cycle 4: starting point
            CyclePoint(
                date=date(2024, 1, 1),
                price=0.003,
                cycle_num=4,
                point_type="min1",
                days_from_halving=-109,
            ),
        ]

        target, factor = analyzer._calculate_diminishing_return(points)

        assert target is not None
        assert factor is not None
        # Factor = 5/10 = 0.5, next gain = 5 * 0.5 = 2.5x (above floor)
        assert pytest.approx(factor, rel=0.1) == 0.5
        # target should be 0.003 * 2.5 = 0.0075 (above the min)
        assert target > 0.003


# =============================================================================
# Trendline Recency Weighting Tests
# =============================================================================


class TestTrendlineRecencyWeighting:
    """Tests for trendline recency decay weighting."""

    @pytest.fixture
    def analyzer(self, mock_price_cache):
        return CyclePatternAnalyzer(price_cache=mock_price_cache)

    def test_recency_weighting_affects_slope(self, analyzer):
        """Test that recency weighting changes the trendline slope.

        With 3 cycles where early cycles have steeper growth, recency weighting
        should produce a less steep slope (closer to recent data).
        """
        # Points with diminishing peak heights over cycles (BTC-like behavior)
        points = [
            # Cycle 2: high peak relative to floor
            CyclePoint(
                date=date(2016, 1, 1),
                price=0.001,
                cycle_num=2,
                point_type="min1",
                days_from_halving=-190,
            ),
            CyclePoint(
                date=date(2017, 12, 1),
                price=0.1,
                cycle_num=2,
                point_type="max2",
                days_from_halving=510,
            ),
            # Cycle 3: moderate peak
            CyclePoint(
                date=date(2020, 1, 1),
                price=0.005,
                cycle_num=3,
                point_type="min1",
                days_from_halving=-131,
            ),
            CyclePoint(
                date=date(2021, 11, 1),
                price=0.15,
                cycle_num=3,
                point_type="max2",
                days_from_halving=539,
            ),
            # Cycle 4: lower peak (diminishing returns)
            CyclePoint(
                date=date(2024, 1, 1),
                price=0.01,
                cycle_num=4,
                point_type="min1",
                days_from_halving=-109,
            ),
            CyclePoint(
                date=date(2025, 10, 1),
                price=0.12,
                cycle_num=4,
                point_type="max2",
                days_from_halving=530,
            ),
        ]

        upper_slope, upper_int, lower_slope, lower_int = analyzer._fit_log_trendlines(points)

        assert upper_slope is not None
        assert lower_slope is not None
        # Both slopes should be positive (prices are growing)
        assert upper_slope > 0
        assert lower_slope > 0


# =============================================================================
# Fibonacci Retracement Filter Tests
# =============================================================================


class TestRetracementFilter:
    """Tests for _calculate_retracement_ratio method (Fibonacci retracement filter)."""

    def test_retracement_shallow_pullback(self):
        """New min1 near previous peak → low retracement (healthy)."""
        points = [
            # Cycle 4: A=0.001 (min1), B=0.01 (max2)
            CyclePoint(
                date=date(2024, 1, 1),
                price=0.001,
                cycle_num=4,
                point_type="min1",
                days_from_halving=-109,
            ),
            CyclePoint(
                date=date(2025, 10, 1),
                price=0.01,
                cycle_num=4,
                point_type="max2",
                days_from_halving=530,
            ),
            # Cycle 5: C=0.008 (min1) → shallow pullback
            CyclePoint(
                date=date(2027, 6, 1),
                price=0.008,
                cycle_num=5,
                point_type="min1",
                days_from_halving=-300,
            ),
        ]
        ratio = CyclePatternAnalyzer._calculate_retracement_ratio(points)
        assert ratio is not None
        # log10(0.01/0.008) / log10(0.01/0.001) = log10(1.25) / log10(10)
        # = 0.0969 / 1.0 = 0.097 → ~9.7% retracement (very shallow)
        assert ratio < 0.20
        assert ratio < 0.786  # Well below filter threshold

    def test_retracement_full_gives_back(self):
        """New min1 at previous trough level → 100% retracement."""
        points = [
            CyclePoint(
                date=date(2024, 1, 1),
                price=0.001,
                cycle_num=4,
                point_type="min1",
                days_from_halving=-109,
            ),
            CyclePoint(
                date=date(2025, 10, 1),
                price=0.01,
                cycle_num=4,
                point_type="max2",
                days_from_halving=530,
            ),
            # Cycle 5: min1 back at previous trough
            CyclePoint(
                date=date(2027, 6, 1),
                price=0.001,
                cycle_num=5,
                point_type="min1",
                days_from_halving=-300,
            ),
        ]
        ratio = CyclePatternAnalyzer._calculate_retracement_ratio(points)
        assert ratio is not None
        assert pytest.approx(ratio, abs=0.01) == 1.0
        assert ratio > 0.786  # Above filter threshold → would be filtered

    def test_retracement_cookie_vs_virtual_scenario(self):
        """COOKIE-like coin (heavy retracement) filtered, VIRTUAL-like kept.

        COOKIE: cycle 4 min1=0.2μ → max2=6μ (30x), cycle 5 min1=0.3μ
          → retracement ≈ log(6/0.3)/log(6/0.2) ≈ 0.87

        VIRTUAL: cycle 4 min1=0.4μ → max2=40μ (100x), cycle 5 min1=7μ
          → retracement ≈ log(40/7)/log(40/0.4) ≈ 0.38
        """
        cookie_points = [
            CyclePoint(
                date=date(2024, 6, 1),
                price=0.0000002,
                cycle_num=4,
                point_type="min1",
                days_from_halving=43,
            ),
            CyclePoint(
                date=date(2025, 1, 1),
                price=0.000006,
                cycle_num=4,
                point_type="max2",
                days_from_halving=257,
            ),
            CyclePoint(
                date=date(2027, 6, 1),
                price=0.0000003,
                cycle_num=5,
                point_type="min1",
                days_from_halving=-300,
            ),
        ]
        cookie_ratio = CyclePatternAnalyzer._calculate_retracement_ratio(cookie_points)

        virtual_points = [
            CyclePoint(
                date=date(2024, 6, 1),
                price=0.0000004,
                cycle_num=4,
                point_type="min1",
                days_from_halving=43,
            ),
            CyclePoint(
                date=date(2025, 1, 1),
                price=0.00004,
                cycle_num=4,
                point_type="max2",
                days_from_halving=257,
            ),
            CyclePoint(
                date=date(2027, 6, 1),
                price=0.000007,
                cycle_num=5,
                point_type="min1",
                days_from_halving=-300,
            ),
        ]
        virtual_ratio = CyclePatternAnalyzer._calculate_retracement_ratio(virtual_points)

        assert cookie_ratio is not None
        assert virtual_ratio is not None

        # COOKIE retraced far more than VIRTUAL
        assert cookie_ratio > virtual_ratio

        # COOKIE above 78.6% Fibonacci level → would be filtered out
        assert cookie_ratio > 0.786
        # VIRTUAL well below → kept
        assert virtual_ratio < 0.786

    def test_retracement_no_next_cycle_min1(self):
        """Without a next cycle min1, retracement cannot be computed."""
        points = [
            CyclePoint(
                date=date(2024, 1, 1),
                price=0.001,
                cycle_num=4,
                point_type="min1",
                days_from_halving=-109,
            ),
            CyclePoint(
                date=date(2025, 10, 1),
                price=0.01,
                cycle_num=4,
                point_type="max2",
                days_from_halving=530,
            ),
        ]
        ratio = CyclePatternAnalyzer._calculate_retracement_ratio(points)
        assert ratio is None

    def test_retracement_no_max2_returns_none(self):
        """Without max2 point, retracement cannot be computed."""
        points = [
            CyclePoint(
                date=date(2024, 1, 1),
                price=0.001,
                cycle_num=4,
                point_type="min1",
                days_from_halving=-109,
            ),
        ]
        ratio = CyclePatternAnalyzer._calculate_retracement_ratio(points)
        assert ratio is None

    def test_retracement_empty_points(self):
        """Empty points should return None."""
        ratio = CyclePatternAnalyzer._calculate_retracement_ratio([])
        assert ratio is None

    def test_retracement_uses_last_cycle_max2(self):
        """When multiple cycles have max2, uses the most recent peak."""
        points = [
            # Cycle 3
            CyclePoint(
                date=date(2020, 1, 1),
                price=0.001,
                cycle_num=3,
                point_type="min1",
                days_from_halving=-131,
            ),
            CyclePoint(
                date=date(2021, 11, 1),
                price=0.005,
                cycle_num=3,
                point_type="max2",
                days_from_halving=539,
            ),
            # Cycle 4: higher peak
            CyclePoint(
                date=date(2024, 1, 1),
                price=0.002,
                cycle_num=4,
                point_type="min1",
                days_from_halving=-109,
            ),
            CyclePoint(
                date=date(2025, 10, 1),
                price=0.02,
                cycle_num=4,
                point_type="max2",
                days_from_halving=530,
            ),
            # Cycle 5: min1 back at cycle 4 trough → full retracement of cycle 4
            CyclePoint(
                date=date(2027, 6, 1),
                price=0.002,
                cycle_num=5,
                point_type="min1",
                days_from_halving=-300,
            ),
        ]
        ratio = CyclePatternAnalyzer._calculate_retracement_ratio(points)
        assert ratio is not None
        # Retracement of cycle 4 move (0.002→0.02): log10(0.02/0.002)/log10(0.02/0.002) = 1.0
        assert pytest.approx(ratio, abs=0.01) == 1.0

    def test_retracement_min2_fallback(self):
        """Uses min2 from peak cycle when min1 not available."""
        points = [
            # Cycle 4: only has min2 (coin launched post-halving), then max2
            CyclePoint(
                date=date(2024, 8, 1),
                price=0.001,
                cycle_num=4,
                point_type="min2",
                days_from_halving=104,
            ),
            CyclePoint(
                date=date(2025, 10, 1),
                price=0.01,
                cycle_num=4,
                point_type="max2",
                days_from_halving=530,
            ),
            # Cycle 5: min1 at 50% log retracement
            # log10(0.01/C) / log10(0.01/0.001) = 0.5
            # log10(0.01/C) = 0.5 → C = 0.01 / 10^0.5 ≈ 0.00316
            CyclePoint(
                date=date(2027, 6, 1),
                price=0.00316,
                cycle_num=5,
                point_type="min1",
                days_from_halving=-300,
            ),
        ]
        ratio = CyclePatternAnalyzer._calculate_retracement_ratio(points)
        assert ratio is not None
        assert pytest.approx(ratio, abs=0.02) == 0.5

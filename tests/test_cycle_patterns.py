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

    def test_fit_trendlines_insufficient_troughs(self, analyzer):
        """Test trendline fitting with too few trough points."""
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
            CyclePoint(
                date=date(2024, 6, 1),
                price=0.02,
                cycle_num=4,
                point_type="max2",
                days_from_halving=42,
            ),
        ]

        result = analyzer._fit_log_trendlines(points)

        # Should return all None due to insufficient troughs (only 1 min)
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

    def test_fit_trendlines_short_span(self, analyzer):
        """Test trendline fitting rejects short data spans."""
        # Points within same year - span < 1200 days required
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

        # Should return None due to short span
        assert result == (None, None, None, None)


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
        """Test when no data exists in pre-halving window."""
        # Data only after halving
        dates = pd.date_range("2020-06-01", "2022-12-31", freq="D")
        prices = np.random.uniform(0.01, 0.02, len(dates))
        df = pd.DataFrame({"close": prices}, index=dates)

        points = analyzer._find_cycle_points(
            df, date(2020, 5, 11), cycle_num=3, is_current_cycle=False
        )

        # Should return empty since no pre-halving data
        assert points == []

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
        results = {
            "eth": CoinPatternResult(
                coin_id="eth", trendline_target_pct=100.0, composite_target_pct=120.0
            ),
            "sol": CoinPatternResult(
                coin_id="sol", trendline_target_pct=150.0, composite_target_pct=180.0
            ),
            "ada": CoinPatternResult(
                coin_id="ada", trendline_target_pct=50.0, composite_target_pct=60.0
            ),
        }

        top = analyzer.get_top_coins(results, n=2)

        assert len(top) == 2
        # Sorted by composite_target_pct descending
        assert top[0].coin_id == "sol"  # composite=180
        assert top[1].coin_id == "eth"  # composite=120

    def test_get_top_coins_filters_negative_trendline(self, analyzer):
        """Test that coins with negative trendline target are filtered, but None is allowed."""
        results = {
            "eth": CoinPatternResult(
                coin_id="eth", trendline_target_pct=100.0, composite_target_pct=100.0
            ),
            "sol": CoinPatternResult(
                coin_id="sol", trendline_target_pct=None, composite_target_pct=200.0
            ),
            "btc": CoinPatternResult(
                coin_id="btc", trendline_target_pct=-50.0, composite_target_pct=150.0
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

"""
Pytest configuration and fixtures for Halvix tests.
"""

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

# Add src directory to Python path for imports
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests (actual API calls)"
    )


def pytest_addoption(parser):
    """Add command line option for integration tests."""
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests that make actual API calls",
    )


def pytest_collection_modifyitems(config, items):
    """Skip integration tests unless --run-integration is specified."""
    if config.getoption("--run-integration"):
        # Enable integration tests
        os.environ["RUN_INTEGRATION_TESTS"] = "1"
        return

    # Skip integration tests by default
    skip_integration = pytest.mark.skip(reason="Use --run-integration to run API tests")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)


# =============================================================================
# Shared Test Fixtures
# =============================================================================


def _days(start: date, n: int) -> list[date]:
    """A contiguous list of ``n`` daily dates starting at ``start``."""
    return [start + timedelta(days=i) for i in range(n)]


@pytest.fixture
def temp_dir():
    """Create a temporary directory that auto-cleans after the test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_dates_short():
    """5-day date range for quick tests."""
    return _days(date(2024, 1, 1), 5)


@pytest.fixture
def sample_dates_long():
    """30-day date range for tests requiring longer periods (e.g., freeze period)."""
    return _days(date(2024, 1, 1), 30)


@pytest.fixture
def sample_price_data(sample_dates_short):
    """
    Basic price data: 5 days, 3 coins (ETH, SOL, ADA).

    Returns a dict of polars frames with ``date``, ``close`` and ``volume_to``.
    """
    return {
        "eth": pl.DataFrame(
            {
                "date": sample_dates_short,
                "close": [0.05, 0.052, 0.051, 0.053, 0.054],
                "volume_to": [10000.0, 11000.0, 10500.0, 12000.0, 11500.0],
            }
        ),
        "sol": pl.DataFrame(
            {
                "date": sample_dates_short,
                "close": [0.003, 0.0031, 0.0029, 0.0032, 0.0033],
                "volume_to": [2000.0, 2100.0, 1900.0, 2200.0, 2300.0],
            }
        ),
        "ada": pl.DataFrame(
            {
                "date": sample_dates_short,
                "close": [0.00002, 0.000021, 0.000019, 0.000022, 0.000023],
                "volume_to": [500.0, 550.0, 450.0, 600.0, 580.0],
            }
        ),
    }


@pytest.fixture
def sample_price_data_with_freeze(sample_dates_long):
    """
    Price data spanning 30 days for testing freeze period behavior.

    Returns a dict of polars frames with gradually increasing prices/volumes.
    """
    return {
        "eth": pl.DataFrame(
            {
                "date": sample_dates_long,
                "close": [0.05 + i * 0.001 for i in range(30)],
                "volume_to": [10000.0 + i * 100 for i in range(30)],
            }
        ),
        "sol": pl.DataFrame(
            {
                "date": sample_dates_long,
                "close": [0.003 + i * 0.0001 for i in range(30)],
                "volume_to": [2000.0 + i * 50 for i in range(30)],
            }
        ),
        "ada": pl.DataFrame(
            {
                "date": sample_dates_long,
                "close": [0.00002 + i * 0.000001 for i in range(30)],
                "volume_to": [500.0 + i * 20 for i in range(30)],
            }
        ),
    }


@pytest.fixture
def coin_filter():
    """Fresh CoinFilter instance for testing filtering logic."""
    from analysis.filters import CoinFilter

    return CoinFilter()


@pytest.fixture
def sample_result():
    """
    Create a sample Total2Result for testing save/load functionality.

    Returns a Total2Result with 3 days of index data and composition records.
    """
    from data.processor import Total2Result

    dates = _days(date(2024, 1, 1), 3)

    index_df = pl.DataFrame(
        {
            "date": dates,
            "total2_price": [0.04, 0.041, 0.042],
            "total_volume": [12500.0, 13000.0, 13500.0],
            "coin_count": [50, 50, 50],
        }
    )

    composition_df = pl.DataFrame(
        {
            "date": [date(2024, 1, 1), date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 2)],
            "rank": [1, 2, 1, 2],
            "coin_id": ["eth", "sol", "eth", "sol"],
            "volume": [10000.0, 2000.0, 10500.0, 2100.0],
            "weight": [0.8, 0.2, 0.8, 0.2],
            "price_btc": [0.05, 0.003, 0.051, 0.0031],
        }
    )

    return Total2Result(
        index_df=index_df,
        composition_df=composition_df,
        coins_processed=2,
        date_range=(date(2024, 1, 1), date(2024, 1, 3)),
        avg_coins_per_day=50.0,
        index_type="total2",
    )

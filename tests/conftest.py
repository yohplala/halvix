"""
Pytest configuration and fixtures for Halvix tests.
"""

import os
import sys
import tempfile
from pathlib import Path

import pandas as pd
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


@pytest.fixture
def temp_dir():
    """Create a temporary directory that auto-cleans after the test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_dates_short():
    """5-day date range for quick tests."""
    return pd.date_range("2024-01-01", periods=5, freq="D")


@pytest.fixture
def sample_dates_long():
    """30-day date range for tests requiring longer periods (e.g., freeze period)."""
    return pd.date_range("2024-01-01", periods=30, freq="D")


@pytest.fixture
def sample_price_data(sample_dates_short):
    """
    Basic price data: 5 days, 3 coins (ETH, SOL, ADA).

    Returns a dictionary of DataFrames with 'close' and 'volume_to' columns.
    """
    eth_data = pd.DataFrame(
        {
            "close": [0.05, 0.052, 0.051, 0.053, 0.054],
            "volume_to": [10000, 11000, 10500, 12000, 11500],
        },
        index=sample_dates_short,
    )

    sol_data = pd.DataFrame(
        {
            "close": [0.003, 0.0031, 0.0029, 0.0032, 0.0033],
            "volume_to": [2000, 2100, 1900, 2200, 2300],
        },
        index=sample_dates_short,
    )

    ada_data = pd.DataFrame(
        {
            "close": [0.00002, 0.000021, 0.000019, 0.000022, 0.000023],
            "volume_to": [500, 550, 450, 600, 580],
        },
        index=sample_dates_short,
    )

    return {
        "eth": eth_data,
        "sol": sol_data,
        "ada": ada_data,
    }


@pytest.fixture
def sample_price_data_with_freeze(sample_dates_long):
    """
    Price data spanning 30 days for testing freeze period behavior.

    Returns a dictionary of DataFrames with gradually increasing prices and volumes.
    """
    eth_data = pd.DataFrame(
        {
            "close": [0.05 + i * 0.001 for i in range(30)],
            "volume_to": [10000 + i * 100 for i in range(30)],
        },
        index=sample_dates_long,
    )

    sol_data = pd.DataFrame(
        {
            "close": [0.003 + i * 0.0001 for i in range(30)],
            "volume_to": [2000 + i * 50 for i in range(30)],
        },
        index=sample_dates_long,
    )

    ada_data = pd.DataFrame(
        {
            "close": [0.00002 + i * 0.000001 for i in range(30)],
            "volume_to": [500 + i * 20 for i in range(30)],
        },
        index=sample_dates_long,
    )

    return {
        "eth": eth_data,
        "sol": sol_data,
        "ada": ada_data,
    }


@pytest.fixture
def volume_series_with_outlier():
    """
    Volume series with a single obvious outlier on day 10.

    15 days of data with volumes around 10000, except day 10 which is 500000.
    """
    dates = pd.date_range("2024-01-01", periods=15, freq="D")
    volumes = [
        10000,
        10500,
        9500,
        10200,
        10800,
        9800,
        10100,
        10300,
        9900,
        500000,  # Outlier on day 10 (index 9)
        10000,
        10100,
        10200,
        10300,
        10400,
    ]
    return pd.Series(volumes, index=dates)


@pytest.fixture
def volume_df_with_outliers():
    """
    DataFrame with volume outliers in multiple coins.

    ETH has outlier on day 10, SOL has outlier on day 13, ADA has no outliers.
    """
    dates = pd.date_range("2024-01-01", periods=15, freq="D")
    data = {
        "eth": [10000.0] * 9 + [500000.0] + [10000.0] * 5,  # Outlier on day 10
        "sol": [2000.0] * 12 + [100000.0] + [2000.0] * 2,  # Outlier on day 13
        "ada": [500.0] * 15,  # No outliers (below typical min_volume threshold)
    }
    return pd.DataFrame(data, index=dates)


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
    from datetime import date

    from data.processor import Total2Result

    dates = pd.date_range("2024-01-01", periods=3, freq="D")

    index_df = pd.DataFrame(
        {
            "total2_price": [0.04, 0.041, 0.042],
            "total_volume": [12500, 13000, 13500],
            "coin_count": [50, 50, 50],
        },
        index=dates,
    )
    index_df.index.name = "date"

    composition_df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-01", "2024-01-02", "2024-01-02"]),
            "rank": [1, 2, 1, 2],
            "coin_id": ["eth", "sol", "eth", "sol"],
            "volume": [10000, 2000, 10500, 2100],
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

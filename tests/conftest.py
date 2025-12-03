"""
Pytest configuration and fixtures for Halvix tests.
"""

import os
import sys
from pathlib import Path

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

"""
Tests for configuration module.
"""

import os
from datetime import date

from config import HALVING_DATES, _load_local_env


class TestLoadLocalEnv:
    """Tests for the local .env loader."""

    def test_parses_keys_and_strips_quotes(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text(
            "# a comment\n"
            "\n"
            'COINGECKO_API_KEY="cg-123"\n'
            "PRICE_PROVIDER = coingecko \n"
            "IGNORED line without equals\n"
        )
        monkeypatch.delenv("COINGECKO_API_KEY", raising=False)
        monkeypatch.delenv("PRICE_PROVIDER", raising=False)
        _load_local_env(env)
        assert os.environ["COINGECKO_API_KEY"] == "cg-123"
        assert os.environ["PRICE_PROVIDER"] == "coingecko"

    def test_real_env_takes_precedence(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("COINGECKO_API_KEY=from-file\n")
        monkeypatch.setenv("COINGECKO_API_KEY", "from-env")
        _load_local_env(env)
        assert os.environ["COINGECKO_API_KEY"] == "from-env"  # env wins

    def test_missing_file_is_noop(self, tmp_path):
        _load_local_env(tmp_path / "does-not-exist.env")  # must not raise


class TestHalvingDates:
    """Tests for HALVING_DATES list."""

    def test_chronological_order(self):
        """Test that halving dates are in chronological order."""
        for i in range(len(HALVING_DATES) - 1):
            assert HALVING_DATES[i] < HALVING_DATES[i + 1]

    def test_all_dates_are_date_objects(self):
        """Test that all entries are date objects."""
        for d in HALVING_DATES:
            assert isinstance(d, date)

    def test_has_expected_count(self):
        """Test that we have the expected number of halvings."""
        assert len(HALVING_DATES) == 5

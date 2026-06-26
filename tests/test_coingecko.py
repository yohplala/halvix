"""
Tests for the CoinGecko price provider and the provider abstraction.

Network calls are mocked; a single optional live test is skipped by default.
"""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest

from api import get_price_provider
from api.base import Coin, PriceProvider, PriceProviderError
from api.coingecko import CoinGeckoClient, CoinGeckoError


def _ms(dt: datetime) -> int:
    """UTC datetime -> epoch milliseconds."""
    return int(dt.replace(tzinfo=UTC).timestamp() * 1000)


class TestProviderFactory:
    def test_explicit_select_cryptocompare(self):
        from api.cryptocompare import CryptoCompareClient

        assert isinstance(get_price_provider("cryptocompare"), CryptoCompareClient)

    def test_explicit_select_coingecko(self):
        assert isinstance(get_price_provider("coingecko"), CoinGeckoClient)
        assert isinstance(get_price_provider("coingecko"), PriceProvider)

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError):
            get_price_provider("does-not-exist")


class TestProviderAutoSelection:
    """The backend is auto-selected from configured API keys."""

    def test_coingecko_key_selects_coingecko(self, monkeypatch):
        import api

        monkeypatch.setattr(api, "COINGECKO_API_KEY", "cg-key")
        monkeypatch.setattr(api, "CRYPTOCOMPARE_API_KEY", "cc-key")
        assert isinstance(get_price_provider(), CoinGeckoClient)  # CoinGecko preferred

    def test_only_cryptocompare_key_selects_cryptocompare(self, monkeypatch):
        import api
        from api.cryptocompare import CryptoCompareClient

        monkeypatch.setattr(api, "COINGECKO_API_KEY", None)
        monkeypatch.setattr(api, "CRYPTOCOMPARE_API_KEY", "cc-key")
        assert isinstance(get_price_provider(), CryptoCompareClient)

    def test_no_keys_defaults_to_coingecko_keyless(self, monkeypatch):
        import api

        monkeypatch.setattr(api, "COINGECKO_API_KEY", None)
        monkeypatch.setattr(api, "CRYPTOCOMPARE_API_KEY", None)
        assert isinstance(get_price_provider(), CoinGeckoClient)


class TestCoinGeckoInit:
    def test_no_key_has_no_auth_header(self):
        # Explicit empty string => no key (api_key=None would fall back to the
        # configured/.env key, which may be set in the dev environment).
        client = CoinGeckoClient(api_key="")
        assert "x-cg-demo-api-key" not in client.session.headers

    def test_key_sets_demo_header(self):
        client = CoinGeckoClient(api_key="demo-123")
        assert client.session.headers["x-cg-demo-api-key"] == "demo-123"

    def test_is_price_provider(self):
        assert isinstance(CoinGeckoClient(), PriceProvider)


class TestDiscovery:
    def test_parses_and_dedups_by_symbol(self):
        page = [
            {
                "id": "bitcoin",
                "symbol": "btc",
                "name": "Bitcoin",
                "market_cap": 3,
                "current_price": 60000,
                "total_volume": 9,
                "circulating_supply": 19,
            },
            {
                "id": "ethereum",
                "symbol": "eth",
                "name": "Ethereum",
                "market_cap": 2,
                "current_price": 3000,
                "total_volume": 8,
                "circulating_supply": 120,
            },
            # duplicate symbol with lower market cap -> dropped
            {
                "id": "ethereum-classic-clone",
                "symbol": "eth",
                "name": "Clone",
                "market_cap": 1,
                "current_price": 1,
                "total_volume": 1,
                "circulating_supply": 1,
            },
        ]
        with patch.object(CoinGeckoClient, "_request", return_value=page):
            client = CoinGeckoClient()
            coins = client.get_top_coins_by_market_cap(n=10)

        assert [c.symbol for c in coins] == ["BTC", "ETH"]
        assert coins[0].provider_id == "bitcoin"
        assert client._symbol_to_id["eth"] == "ethereum"  # higher-cap kept
        assert coins[0].market_cap_rank == 1 and coins[1].market_cap_rank == 2

    def test_track_no_data_returns_empty_second(self):
        with patch.object(CoinGeckoClient, "_request", return_value=[]):
            client = CoinGeckoClient()
            coins, no_data = client.get_top_coins_by_market_cap(n=5, track_no_data=True)
        assert coins == [] and no_data == []


class TestResampling:
    def test_to_daily_ohlcv(self):
        d1 = datetime(2025, 1, 1)
        d2 = datetime(2025, 1, 2)
        prices = [
            [_ms(d1.replace(hour=0)), 10.0],
            [_ms(d1.replace(hour=12)), 15.0],
            [_ms(d1.replace(hour=23)), 12.0],
            [_ms(d2.replace(hour=0)), 20.0],
            [_ms(d2.replace(hour=23)), 25.0],
        ]
        volumes = [
            [_ms(d1.replace(hour=0)), 100.0],
            [_ms(d1.replace(hour=12)), 120.0],
            [_ms(d1.replace(hour=23)), 130.0],
            [_ms(d2.replace(hour=0)), 200.0],
            [_ms(d2.replace(hour=23)), 250.0],
        ]
        df = CoinGeckoClient._to_daily_ohlcv(prices, volumes)

        assert list(df.columns) == ["open", "high", "low", "close", "volume_from", "volume_to"]
        assert df.index.tz is None
        row1 = df.loc["2025-01-01"]
        assert (row1["open"], row1["high"], row1["low"], row1["close"]) == (10, 15, 10, 12)
        assert row1["volume_to"] == 130.0  # last reading of the day
        assert row1["volume_from"] == pytest.approx(130.0 / 12.0)
        row2 = df.loc["2025-01-02"]
        assert row2["close"] == 25.0 and row2["volume_to"] == 250.0

    def test_empty_prices_returns_empty_frame(self):
        with patch.object(
            CoinGeckoClient, "_request", return_value={"prices": [], "total_volumes": []}
        ):
            client = CoinGeckoClient()
            df = client.get_full_daily_history("ETH", "BTC", provider_id="ethereum")
        assert df.empty


class TestRequestSizing:
    """A new/deep request asks for the tier's max window; an incremental one a small window."""

    EMPTY = {"prices": [], "total_volumes": []}

    def _days_param(self, m):
        return m.call_args.kwargs["params"]["days"]

    def test_new_coin_no_start_requests_cap(self):
        from config import COINGECKO_MAX_DAYS_PER_REQUEST

        client = CoinGeckoClient()
        with patch.object(client, "_request", return_value=self.EMPTY) as m:
            client.get_full_daily_history("ETH", "BTC", provider_id="ethereum")
        assert self._days_param(m) == COINGECKO_MAX_DAYS_PER_REQUEST

    def test_deep_span_capped(self):
        from config import COINGECKO_MAX_DAYS_PER_REQUEST

        client = CoinGeckoClient()
        with patch.object(client, "_request", return_value=self.EMPTY) as m:
            client.get_full_daily_history(
                "ETH", "BTC", start_date=date(2012, 1, 1), provider_id="ethereum"
            )
        # Never exceeds the tier limit (CoinGecko 401s on larger ranges).
        assert self._days_param(m) == COINGECKO_MAX_DAYS_PER_REQUEST

    def test_incremental_requests_numeric_days(self):
        client = CoinGeckoClient()
        start = datetime.now(UTC).date() - timedelta(days=10)
        with patch.object(client, "_request", return_value=self.EMPTY) as m:
            client.get_full_daily_history("ETH", "BTC", start_date=start, provider_id="ethereum")
        days = self._days_param(m)
        assert isinstance(days, int) and 2 <= days <= 365


class TestHistoryWindow:
    def test_filters_to_window_and_drops_today(self):
        today = datetime.now(UTC).date()
        # one point today (incomplete) + a few past days
        days = [today - timedelta(days=n) for n in (4, 3, 2, 1, 0)]
        prices = [[_ms(datetime(d.year, d.month, d.day, 12)), 1.0 + i] for i, d in enumerate(days)]
        volumes = [[p[0], 100.0] for p in prices]
        payload = {"prices": prices, "total_volumes": volumes}

        with patch.object(CoinGeckoClient, "_request", return_value=payload):
            client = CoinGeckoClient()
            df = client.get_full_daily_history(
                "ETH", "BTC", start_date=today - timedelta(days=2), provider_id="ethereum"
            )

        got = {d.date() for d in df.index}
        assert today not in got  # incomplete current day dropped
        assert min(got) >= today - timedelta(days=2)  # honours start_date
        assert max(got) == today - timedelta(days=1)


class TestResolveId:
    def test_explicit_provider_id(self):
        assert CoinGeckoClient()._resolve_id("eth", "ethereum") == "ethereum"

    def test_symbol_map_fallback(self):
        client = CoinGeckoClient()
        client._symbol_to_id["eth"] = "ethereum"
        assert client._resolve_id("ETH", None) == "ethereum"

    def test_unresolvable_raises(self):
        with pytest.raises(CoinGeckoError):
            CoinGeckoClient()._resolve_id("zzz", None)


class TestHealth:
    def test_ping_true_on_success(self):
        with patch.object(CoinGeckoClient, "_request", return_value={"gecko_says": "ok"}):
            assert CoinGeckoClient().ping() is True

    def test_ping_false_on_error(self):
        with patch.object(CoinGeckoClient, "_request", side_effect=PriceProviderError("down")):
            assert CoinGeckoClient().ping() is False

    def test_check_availability_unresolved(self):
        res = CoinGeckoClient().check_histoday_availability("zzz", "BTC", provider_id=None)
        assert res["available"] == ""
        assert "No CoinGecko id" in res["reason"]


class TestCoinDataclass:
    def test_to_dict_includes_provider_id(self):
        c = Coin("ETH", "Ethereum", 1.0, 1, 2.0, 3.0, 4.0, provider_id="ethereum")
        d = c.to_dict()
        assert d["id"] == "eth" and d["provider_id"] == "ethereum"

"""Tests for the cross-provider coin-identity registry."""

from data.coin_registry import CoinRegistry


def _reg(tmp_path):
    return CoinRegistry(path=tmp_path / "coin_registry.json")


class TestGetSet:
    def test_set_and_get(self, tmp_path):
        r = _reg(tmp_path)
        r.set_stem("coingecko", "ethereum", "eth")
        assert r.get_stem("coingecko", "ethereum") == "eth"
        assert r.get_stem("coingecko", "unknown") is None
        assert r.get_stem("cryptocompare", "ethereum") is None

    def test_all_stems(self, tmp_path):
        r = _reg(tmp_path)
        r.set_stem("cryptocompare", "BTCY", "btcy")
        r.set_stem("coingecko", "btc-yield", "btcy-2")
        r.set_stem("coingecko", "ethereum", "eth")
        assert r.all_stems() == {"btcy", "btcy-2", "eth"}


class TestAllocateStem:
    def test_free_symbol(self, tmp_path):
        assert _reg(tmp_path).allocate_stem("BTCY") == "btcy"

    def test_collision_suffixes(self, tmp_path):
        r = _reg(tmp_path)
        r.set_stem("cryptocompare", "BTCY", "btcy")
        assert r.allocate_stem("BTCY") == "btcy-2"
        r.set_stem("coingecko", "btc-yield", "btcy-2")
        assert r.allocate_stem("BTCY") == "btcy-3"

    def test_respects_reserved(self, tmp_path):
        # btcy exists on disk but isn't registered yet -> still avoided
        assert _reg(tmp_path).allocate_stem("BTCY", reserved={"btcy"}) == "btcy-2"


class TestPersistence:
    def test_round_trip(self, tmp_path):
        r = _reg(tmp_path)
        r.set_stem("coingecko", "ethereum", "eth")
        r.save()
        r2 = CoinRegistry(path=tmp_path / "coin_registry.json")
        assert r2.get_stem("coingecko", "ethereum") == "eth"

    def test_missing_file_is_empty(self, tmp_path):
        assert _reg(tmp_path).all_stems() == set()

    def test_corrupt_file_is_empty(self, tmp_path):
        p = tmp_path / "coin_registry.json"
        p.write_text("not json {{{")
        assert CoinRegistry(path=p).all_stems() == set()


class TestBootstrap:
    def test_seeds_when_absent(self, tmp_path):
        r = _reg(tmp_path)
        assert r.bootstrap_provider("cryptocompare", {"BTC": "btc", "ETH": "eth"}) is True
        assert r.get_stem("cryptocompare", "BTC") == "btc"

    def test_does_not_overwrite_existing(self, tmp_path):
        r = _reg(tmp_path)
        r.set_stem("cryptocompare", "BTC", "btc")
        assert r.bootstrap_provider("cryptocompare", {"BTC": "WRONG"}) is False
        assert r.get_stem("cryptocompare", "BTC") == "btc"

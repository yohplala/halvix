"""Tests for the stem → (ticker, name, CoinGecko slug) resolver."""

import json

from data.coin_metadata import CoinMetadataResolver
from data.coin_registry import CoinRegistry


def _resolver(tmp_path, coins):
    """Build a resolver with an isolated registry + coin list (no external seed)."""
    registry = CoinRegistry(path=tmp_path / "coin_registry.json")
    # Bind the two colliding TAG assets: the CryptoCompare-era one owns "tag",
    # the CoinGecko "tagger" coin was forked to "tag-2".
    registry.set_stem("cryptocompare", "TAG", "tag")
    registry.set_stem("coingecko", "tagger", "tag-2")
    registry.set_stem("coingecko", "solana", "sol")
    # Isolate from the committed seed (point at a non-existent seed file).
    return CoinMetadataResolver(registry=registry, coins=coins, seed_path=tmp_path / "no-seed.json")


COINS = [
    {"id": "sol", "symbol": "SOL", "name": "Solana", "provider_id": "solana"},
    {"id": "tag", "symbol": "TAG", "name": "Tagger", "provider_id": "tagger"},
]


class TestResolve:
    def test_collided_stem_uses_real_ticker_and_slug(self, tmp_path):
        # The reported bug: stem "tag-2" was shown as "TAG-2" and linked to a
        # symbol search. It must resolve to ticker TAG and the /coins/tagger page.
        m = _resolver(tmp_path, COINS).resolve("tag-2")
        assert m.ticker == "TAG"
        assert m.name == "Tagger"
        assert m.slug == "tagger"
        assert m.url == "https://www.coingecko.com/en/coins/tagger"

    def test_plain_stem_direct_coin_page(self, tmp_path):
        m = _resolver(tmp_path, COINS).resolve("sol")
        assert m.ticker == "SOL"
        assert m.url == "https://www.coingecko.com/en/coins/solana"

    def test_unknown_stem_strips_suffix_and_falls_back_to_search(self, tmp_path):
        m = _resolver(tmp_path, COINS).resolve("mystery-9")
        assert m.ticker == "MYSTERY"
        assert m.slug is None
        assert m.url == "https://www.coingecko.com/en/search?query=MYSTERY"

    def test_unrelated_bare_stem_not_mislabeled(self, tmp_path):
        # "tag" is the CryptoCompare-era asset, NOT tagger. It has no CoinGecko
        # slug, so it must not borrow tagger's identity via a naive id match.
        m = _resolver(tmp_path, COINS).resolve("tag")
        assert m.ticker == "TAG"
        assert m.slug is None

    def test_ticker_and_url_helpers(self, tmp_path):
        r = _resolver(tmp_path, COINS)
        assert r.ticker("tag-2") == "TAG"
        assert r.url("sol") == "https://www.coingecko.com/en/coins/solana"

    def test_coin_returns_raw_metadata(self, tmp_path):
        r = _resolver(tmp_path, COINS)
        assert r.coin("tag-2")["name"] == "Tagger"
        assert r.coin("mystery-9") == {}


class TestSources:
    def test_missing_coin_file_is_tolerated(self, tmp_path):
        registry = CoinRegistry(path=tmp_path / "coin_registry.json")
        r = CoinMetadataResolver(
            registry=registry,
            coins_path=tmp_path / "absent.json",
            seed_path=tmp_path / "absent-seed.json",
        )
        m = r.resolve("foo")
        assert m.ticker == "FOO"
        assert m.slug is None

    def test_seed_supplies_slug_without_registry_or_coins(self, tmp_path):
        # The deployed-page bug: on the GitHub Pages job the runtime registry and
        # discovery list are absent, so links must resolve from the committed
        # slug->stem seed alone (ticker recovered by stripping the stem suffix).
        empty_registry = CoinRegistry(path=tmp_path / "empty.json")
        seed_path = tmp_path / "seed.json"
        seed_path.write_text(
            json.dumps({"coingecko": {"solana": "sol", "tagger": "tag-2"}}), encoding="utf-8"
        )
        r = CoinMetadataResolver(registry=empty_registry, coins=[], seed_path=seed_path)

        sol = r.resolve("sol")
        assert sol.ticker == "SOL"
        assert sol.url == "https://www.coingecko.com/en/coins/solana"
        tag = r.resolve("tag-2")
        assert tag.ticker == "TAG"
        assert tag.url == "https://www.coingecko.com/en/coins/tagger"

    def test_registry_takes_precedence_over_seed(self, tmp_path):
        registry = CoinRegistry(path=tmp_path / "coin_registry.json")
        registry.set_stem("coingecko", "runtime-slug", "xyz")
        seed_path = tmp_path / "seed.json"
        seed_path.write_text(json.dumps({"coingecko": {"seed-slug": "xyz"}}), encoding="utf-8")
        r = CoinMetadataResolver(registry=registry, coins=[], seed_path=seed_path)
        # Runtime registry mapping wins over the seed for the same stem.
        assert r.resolve("xyz").slug == "runtime-slug"

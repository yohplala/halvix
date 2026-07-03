"""
Resolve a parquet *stem* (the on-disk cache key, e.g. ``tag-2``) to a coin's
human identity: display ticker, full name, and CoinGecko slug (for a direct
reference link).

The price cache keys files by stem, which can diverge from the coin's ticker
when a symbol collides across providers: a second coin whose symbol is also
``TAG`` is stored as ``tag-2``. Rendering the stem directly shows "TAG-2" and
links to a symbol search instead of the coin's page. This module joins two
on-disk sources to recover the real identity:

- ``coins_to_download.json`` — the discovery list carrying ``{id, symbol, name,
  provider_id (CoinGecko slug), ...}`` per coin.
- ``CoinRegistry`` — the cross-provider ``{provider: {native_id: stem}}`` map,
  which is how the fetcher chooses each coin's stem.

For every coin in the discovery list we compute its stem exactly as the fetcher
does (native id → registry stem, else bare id) and index the metadata by that
stem. A collided coin therefore resolves to its real ticker/slug, and an
unrelated coin that merely shares the bare stem is never mislabeled.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from config import COINGECKO_IDENTITY_SEED_JSON, COINS_TO_DOWNLOAD_JSON, coin_url
from data.coin_registry import CoinRegistry
from utils.logging import get_logger

logger = get_logger(__name__)

# Trailing collision suffix a stem carries when its bare symbol was already
# taken (``tag-2``, ``btt-2``). Stripped to recover a display ticker when no
# richer metadata is available.
_SUFFIX_RE = re.compile(r"-\d+$")


@dataclass(frozen=True)
class CoinMeta:
    """A coin's display identity, resolved from its on-disk stem."""

    stem: str  # on-disk cache key, e.g. "tag-2"
    ticker: str  # display symbol, e.g. "TAG"
    name: str  # full name, e.g. "Tagger"
    slug: str | None  # CoinGecko slug, e.g. "tagger" (None if unknown)

    @property
    def url(self) -> str:
        """CoinGecko reference URL — the coin page when the slug is known."""
        return coin_url(self.ticker, self.slug)


class CoinMetadataResolver:
    """Maps on-disk stems to :class:`CoinMeta` (ticker, name, CoinGecko slug)."""

    def __init__(
        self,
        registry: CoinRegistry | None = None,
        coins: list[dict] | None = None,
        coins_path: Path = COINS_TO_DOWNLOAD_JSON,
        seed_path: Path = COINGECKO_IDENTITY_SEED_JSON,
    ):
        """
        Args:
            registry: Cross-provider identity map (default: load from disk).
            coins: Pre-loaded discovery list; if None, read ``coins_path``.
            coins_path: Discovery-list JSON to read when ``coins`` is None.
            seed_path: Committed CoinGecko slug→stem identity seed — the always-
                available slug fallback (the runtime registry / discovery list
                are not deployed to the GitHub Pages job).
        """
        self._registry = registry or CoinRegistry()
        self._by_stem: dict[str, dict] = {}
        self._slug_by_stem: dict[str, str] = {}
        self._load(self._read_coins(coins_path) if coins is None else coins, seed_path)

    def _load(self, coins: list[dict], seed_path: Path) -> None:
        # Reverse the CoinGecko slug→stem maps so a stem can recover its slug.
        # The runtime registry (current, fetch-populated) takes precedence; the
        # committed identity seed fills the rest and, crucially, is the ONLY
        # source available in the deploy environment (the registry and discovery
        # list live under the gitignored data/processed and are not published).
        for slug, stem in self._registry.provider_map("coingecko").items():
            self._slug_by_stem.setdefault(stem, slug)
        for slug, stem in self._read_seed(seed_path).items():
            self._slug_by_stem.setdefault(stem, slug)

        for coin in coins:
            if isinstance(coin, dict):
                self._by_stem[self._stem_for(coin)] = coin

    @staticmethod
    def _read_seed(seed_path: Path) -> dict[str, str]:
        """Load the committed ``{coingecko: {slug: stem}}`` identity seed."""
        if not seed_path.exists():
            return {}
        try:
            data = json.loads(seed_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError, OSError:
            logger.warning("Could not read identity seed at %s.", seed_path)
            return {}
        cg = data.get("coingecko", {})
        return {str(k): str(v) for k, v in cg.items()} if isinstance(cg, dict) else {}

    @staticmethod
    def _read_coins(coins_path: Path) -> list[dict]:
        """Load the discovery list; tolerate a missing or malformed file."""
        if not coins_path.exists():
            return []
        try:
            data = json.loads(coins_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError, OSError:
            logger.warning("Could not read coin list at %s.", coins_path)
            return []
        return [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []

    def _stem_for(self, coin: dict) -> str:
        """Compute a coin's on-disk stem the way ``DataFetcher`` does."""
        slug = coin.get("provider_id")
        if slug:
            stem = self._registry.get_stem("coingecko", str(slug))
            if stem:
                return stem
        symbol = (coin.get("symbol") or coin.get("id") or "").upper()
        if symbol:
            stem = self._registry.get_stem("cryptocompare", symbol)
            if stem:
                return stem
        return str(coin.get("id", "")).lower()

    def coin(self, stem: str) -> dict:
        """Raw discovery-list entry for a stem (empty dict if unknown)."""
        return self._by_stem.get(stem.lower(), {})

    def resolve(self, stem: str) -> CoinMeta:
        """Resolve a stem to its display identity (never raises)."""
        stem = stem.lower()
        coin = self._by_stem.get(stem)
        slug = self._slug_by_stem.get(stem)
        if coin:
            ticker = (coin.get("symbol") or stem).upper()
            return CoinMeta(
                stem=stem,
                ticker=ticker,
                name=coin.get("name") or ticker,
                slug=coin.get("provider_id") or slug,
            )
        ticker = _SUFFIX_RE.sub("", stem).upper()
        return CoinMeta(stem=stem, ticker=ticker, name=ticker, slug=slug)

    def ticker(self, stem: str) -> str:
        """Display ticker for a stem (e.g. ``tag-2`` → ``TAG``)."""
        return self.resolve(stem).ticker

    def url(self, stem: str) -> str:
        """CoinGecko reference URL for a stem (direct coin page when known)."""
        return self.resolve(stem).url

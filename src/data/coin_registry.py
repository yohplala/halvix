"""
Cross-provider coin-identity registry.

Different providers can use the same SYMBOL for DIFFERENT assets (e.g.
CryptoCompare's ``BTCY`` vs a CoinGecko coin whose symbol is also ``BTCY``).
The on-disk price cache is keyed by a stable *stem* (the file id, e.g.
``btcy`` → ``btcy-btc.parquet``); a genuinely different asset sharing a symbol
gets its own stem (``btcy-2``).

This registry persists the binding from each provider's UNIQUE native id to the
stem, so once an asset is identified the right file is chosen deterministically
on every later run — no price comparison needed again:

    { provider_name: { provider_native_id: stem } }

- CryptoCompare addresses coins by symbol → native id is the (upper) symbol.
- CoinGecko addresses coins by slug → native id is the slug (``provider_id``).

e.g. {"cryptocompare": {"BTCY": "btcy"}, "coingecko": {"btc-yield": "btcy-2"}}
"""

import json
from pathlib import Path

from config import COIN_REGISTRY_JSON
from utils.logging import get_logger

logger = get_logger(__name__)


class CoinRegistry:
    """Persistent ``{provider: {native_id: stem}}`` identity map."""

    def __init__(self, path: Path = COIN_REGISTRY_JSON):
        self.path = path
        self._data: dict[str, dict[str, str]] = {}
        self.load()

    def load(self) -> None:
        """Load the registry from disk (empty if missing or unreadable)."""
        if not self.path.exists():
            self._data = {}
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            # Be defensive about shape: {str: {str: str}}
            self._data = {
                str(p): {str(k): str(v) for k, v in m.items()}
                for p, m in data.items()
                if isinstance(m, dict)
            }
        except json.JSONDecodeError, OSError, AttributeError:
            logger.warning("Could not read coin registry at %s; starting empty.", self.path)
            self._data = {}

    def save(self) -> None:
        """Persist the registry to disk (sorted for stable diffs)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")

    def get_stem(self, provider: str, native_id: str) -> str | None:
        """Return the stem bound to (provider, native_id), or None if unseen."""
        return self._data.get(provider, {}).get(native_id)

    def set_stem(self, provider: str, native_id: str, stem: str) -> None:
        """Bind (provider, native_id) → stem."""
        self._data.setdefault(provider, {})[native_id] = stem

    def all_stems(self) -> set[str]:
        """Every stem currently registered (across all providers)."""
        return {stem for mapping in self._data.values() for stem in mapping.values()}

    def provider_map(self, provider: str) -> dict[str, str]:
        """Return a copy of a provider's ``{native_id: stem}`` mapping (empty if unseen)."""
        return dict(self._data.get(provider, {}))

    def allocate_stem(self, symbol: str, reserved: set[str] | None = None) -> str:
        """
        Pick a free stem for a (new) asset with the given symbol.

        Returns the bare lowercase symbol if free, else ``symbol-2``, ``symbol-3``…
        ``reserved`` lets callers exclude stems that exist on disk but are not yet
        registered (so a bootstrap can't collide).
        """
        taken = self.all_stems() | (reserved or set())
        base = symbol.lower()
        if base not in taken:
            return base
        i = 2
        while f"{base}-{i}" in taken:
            i += 1
        return f"{base}-{i}"

    def bootstrap_provider(self, provider: str, native_to_stem: dict[str, str]) -> bool:
        """
        Seed a provider's mapping if it has none yet (one-time migration).

        Returns True if seeding happened. Existing entries are never overwritten.
        """
        if self._data.get(provider):
            return False
        self._data[provider] = dict(native_to_stem)
        return True

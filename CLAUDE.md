# AI Agent Context

**[← Back to README](README.md)**

---

> **Purpose**: Quick reference for AI agents to understand and work on this cryptocurrency analysis tool.

## Documentation Index

| Topic | Document |
|-------|----------|
| **Tutorial & CLI usage** | [docs/TUTORIAL.md](docs/TUTORIAL.md) |
| **Data sources & providers** | [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) |
| **TOTAL2 calculation** | [docs/TOTAL2_CALCULATION.md](docs/TOTAL2_CALCULATION.md) |
| **Cycle pattern analysis** | [docs/PATTERN_ANALYSIS.md](docs/PATTERN_ANALYSIS.md) |
| **Identification kernel** | [docs/IDENTIFICATION_KERNEL.md](docs/IDENTIFICATION_KERNEL.md) |
| **Deployment (GitHub Pages)** | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |

---

## 1. Development Environment

**This project uses Poetry** for dependency management.

```bash
# Install dependencies
poetry install

# Run commands (always use poetry run)
poetry run python -m main <command>
poetry run pytest
poetry run ruff check src/ tests/
poetry run black src/ tests/
```

---

## 2. Project Structure

```
halvix/
├── pyproject.toml              # Poetry config (Python 3.14+)
├── poetry.lock                 # Pinned dependency graph (committed)
├── CLAUDE.md                   # This file
├── README.md
├── CHANGELOG.md
├── .pre-commit-config.yaml     # black + ruff + hygiene hooks
├── docs/                       # Detailed documentation
│
├── src/                        # Source code
│   ├── config.py               # Constants, halving dates, settings
│   ├── main.py                 # CLI entry point
│   ├── api/
│   │   ├── base.py             # PriceProvider protocol + shared Coin dataclass
│   │   ├── coingecko.py        # CoinGecko client (default provider)
│   │   └── cryptocompare.py    # CryptoCompare client (alternative provider)
│   ├── data/
│   │   ├── cache.py            # File-based caching
│   │   ├── fetcher.py          # Data retrieval
│   │   ├── price_filters.py    # Volume outlier detection, SMA, round-trip & symbol-replacement smoothing
│   │   └── processor.py        # Total2Processor + factory + result dataclass
│   ├── analysis/
│   │   ├── filters.py          # Token filtering
│   │   ├── cycle_points.py     # Dataclasses + pure helpers (PointType, CyclePoint, …)
│   │   ├── point_detection.py  # Min/max identification kernel (3-pass segment scan)
│   │   ├── projections.py      # Trendline / fib / diminishing / historical-peak models
│   │   └── cycle_patterns.py   # CyclePatternAnalyzer orchestrator (thin wrappers + IO)
│   ├── utils/
│   │   └── logging.py
│   └── visualization/
│       ├── __init__.py         # Module exports
│       ├── _layout.py          # Shared HTML/CSS layout primitives (header/footer/base CSS)
│       ├── charts.py           # Halving cycle chart generation
│       ├── html_generator.py   # HTML page generation
│       ├── pattern_charts.py   # Pattern analysis charts
│       └── templates/          # Jinja2 HTML templates
│
├── tests/                      # Pytest tests
├── data/
│   ├── raw/prices/             # Parquet price files
│   ├── processed/              # Output files
│   └── cache/                  # API cache
└── site/                       # Generated HTML (GitHub Pages)
```

---

## 3. Key Architecture

### TOTAL2 Processor (see [docs/TOTAL2_CALCULATION.md](docs/TOTAL2_CALCULATION.md))

```python
from data.processor import get_processor

# Factory function - returns Total2Processor
processor = get_processor()
result = processor.calculate_total2()
```

`Total2Processor`: 21-day freeze period + entry-day price scaling. The on-disk
metadata key is `total2b` — an older algorithm-variant name kept so existing
JSON/parquet consumers don't break.

### Price Providers

Price data is sourced through a provider abstraction (`api.base.PriceProvider`):

```python
from api import get_price_provider

provider = get_price_provider()          # honours PRICE_PROVIDER (default: coingecko)
coins = provider.get_top_coins_by_market_cap(n=300)
```

- **CoinGecko** (`api/coingecko.py`) — default; full coin coverage, native
  market-cap ranking, recent price+volume vs BTC/USD. Optional free Demo key via
  `COINGECKO_API_KEY` (keyless works but is throttled harder).
- **CryptoCompare** (`api/cryptocompare.py`) — alternative; requires
  `CRYPTOCOMPARE_API_KEY`. Select with `PRICE_PROVIDER=cryptocompare`.

Full history is cached on the `raw-data` branch, so the daily job only tops up
the most recent days for the top coins by market cap.

### Token Filtering

Located in `src/analysis/filters.py`. Exclusions defined in `src/config.py`:
- `EXCLUDED_STABLECOINS` - USDT, USDC, DAI, etc.
- `EXCLUDED_WRAPPED_STAKED_IDS` - WBTC, STETH, etc.
- `EXCLUDED_PATTERNS` - regex patterns
- `ALLOWED_TOKENS` - overrides (SUI, SEI, STX, etc.)

---

## 4. CLI Commands

See [docs/TUTORIAL.md](docs/TUTORIAL.md) for complete CLI reference and usage examples.

---

## 5. Testing

```bash
poetry run pytest                           # All tests
poetry run pytest tests/test_processor.py   # Specific file
poetry run pytest -v --tb=short             # Verbose
```

---

## 6. Code Patterns

### Import Pattern
Modules are in `src/`, added to PYTHONPATH via `pyproject.toml`:
```python
# In src/data/processor.py
from config import TOP_N_BY_VOLUME_FOR_TOTAL2

# In tests/
from data.processor import Total2Processor
```

### Key Config Values (from `src/config.py`)
```python
TOP_N_BY_MARKETCAP_TO_FETCH = 1200     # Coins to fetch (among those, downloads of some can be skipped)
TOP_N_BY_VOLUME_FOR_TOTAL2 = 30        # Coins in index
VOLUME_SMA_WINDOW = 120                # Days for volume smoothing
DEFAULT_QUOTE_CURRENCY = "BTC"
```

### Common Pitfalls
1. Always use `poetry run` for commands
2. Check `ALLOWED_TOKENS` before filtering tokens
3. Price provider is CoinGecko by default (`PRICE_PROVIDER`); a `COINGECKO_API_KEY`
   raises rate limits. The daily job is scoped to the top ~300 coins to stay
   within the free tier.

---

**[← Back to README](README.md)**

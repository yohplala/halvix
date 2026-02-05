# AI Agent Context

**[← Back to README](README.md)**

---

> **Purpose**: Quick reference for AI agents to understand and work on this cryptocurrency analysis tool.

## Documentation Index

| Topic | Document |
|-------|----------|
| **Tutorial & CLI usage** | [docs/TUTORIAL.md](docs/TUTORIAL.md) |
| **CryptoCompare API details** | [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) |
| **TOTAL2/TOTAL2b calculation** | [docs/TOTAL2_CALCULATION.md](docs/TOTAL2_CALCULATION.md) |
| **Cycle pattern analysis** | [docs/PATTERN_ANALYSIS.md](docs/PATTERN_ANALYSIS.md) |
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
├── pyproject.toml              # Poetry config (Python 3.13+)
├── CLAUDE.md                   # This file
├── README.md
├── docs/                       # Detailed documentation
│
├── src/                        # Source code
│   ├── config.py               # Constants, halving dates, settings
│   ├── main.py                 # CLI entry point
│   ├── api/
│   │   └── cryptocompare.py    # CryptoCompare API client
│   ├── data/
│   │   ├── cache.py            # File-based caching
│   │   ├── fetcher.py          # Data retrieval
│   │   ├── price_filters.py    # Volume outlier detection, SMA smoothing
│   │   ├── processor.py        # Re-exports and factory function
│   │   ├── processor_base.py   # BaseTotal2Processor (shared)
│   │   ├── processor_total2.py # Total2Processor (legacy)
│   │   └── processor_total2b.py # Total2bProcessor (default)
│   ├── analysis/
│   │   ├── filters.py          # Token filtering
│   │   └── cycle_patterns.py   # Cycle pattern analysis
│   ├── utils/
│   │   └── logging.py
│   └── visualization/
│       ├── __init__.py         # Module exports
│       ├── charts.py           # Halving cycle chart generation
│       ├── html_generator.py   # HTML page generation
│       └── pattern_charts.py   # Pattern analysis charts
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

# Factory function - returns Total2Processor or Total2bProcessor
processor = get_processor("total2b")  # or "total2" for legacy
result = processor.calculate_total2()
```

| Processor | Description |
|-----------|-------------|
| `Total2bProcessor` | **Default**. 21-day freeze period + price scaling at entry |
| `Total2Processor` | Legacy. Entry warmup price capping + TOTAL2 series smoothing |


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
from data.processor import Total2bProcessor
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
3. API rate limit: Dynamic (checks `/stats/rate/limit` endpoint); fallback 12 calls/min (5 seconds between requests)

---

*Last updated: 2026-02-05*

**[← Back to README](README.md)**

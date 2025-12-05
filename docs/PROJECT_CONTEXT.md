# Halvix - Cryptocurrency Halving Cycle Analysis

> **Purpose**: This document provides complete project context for AI agents to implement, debug, and extend this cryptocurrency analysis tool.

## 1. Project Overview

### 1.1 Project Name
**Halvix** - A portmanteau of "halving" and "index", representing the core functionality of analyzing cryptocurrency performance relative to Bitcoin halving cycles.

### 1.2 Objective
Analyze cryptocurrency price performance relative to Bitcoin halving cycles. Compare each coin's performance against BTC and a computed TOTAL2 market index, with filtering based on positive trend indicators.

### 1.3 Key Outputs
- Individual charts per cryptocurrency (stacked by halving cycle)
- Top 10 performers summary chart
- Cached price data repository
- Regression analysis results
- CSV export of filtered tokens for review

---

## 2. Project Structure

```
halvix/
├── pyproject.toml              # Poetry dependency management
├── poetry.lock
├── README.md
├── CHANGELOG.md                # Version history
├── docs/
│   ├── PROJECT_CONTEXT.md      # This file (AI agent context)
│   ├── DATA_SOURCES.md         # CryptoCompare API documentation
│   ├── EDGE_CASES.md           # Edge cases and solutions
│   └── TOTAL2_CALCULATION.md   # TOTAL2 index methodology
├── .vscode/
│   └── settings.json           # VS Code pytest configuration
│
├── src/                        # Source code (modules directly in src/)
│   ├── __init__.py
│   ├── config.py               # Constants, halving dates, API settings
│   ├── main.py                 # CLI entry point
│   ├── api/
│   │   ├── __init__.py
│   │   └── cryptocompare.py    # CryptoCompare API client
│   ├── data/
│   │   ├── __init__.py
│   │   ├── fetcher.py          # Data retrieval
│   │   ├── processor.py        # TOTAL2 calculation
│   │   └── cache.py            # File-based caching
│   ├── analysis/
│   │   ├── __init__.py
│   │   └── filters.py          # Token filtering
│   ├── utils/
│   │   ├── __init__.py
│   │   └── logging.py          # Logging configuration
│   └── visualization/
│       └── __init__.py         # Charts to implement
│
├── tests/                      # Test suite
│   ├── __init__.py
│   └── conftest.py             # Pytest configuration
│
├── data/
│   ├── raw/prices/             # Raw price data (parquet files)
│   ├── processed/              # Processed data & results
│   │   ├── rejected_coins.csv  # Excluded coins with URLs
│   │   ├── regression_results.csv
│   │   └── total2_index.parquet
│   └── cache/                  # API cache
│
└── output/
    ├── charts/individual/      # Per-coin charts
    └── reports/                # Analysis reports
```

---

## 3. Data Specifications

### 3.1 BTC Halving Dates

| Halving | Date | Cycle Window Start | Cycle Window End |
|---------|------|-------------------|------------------|
| 1st | 2012-11-28 | 2011-06-02 | 2015-04-27 |
| 2nd | 2016-07-09 | 2015-01-02 | 2018-11-05 |
| 3rd | 2020-05-11 | 2018-11-08 | 2022-09-07 |
| 4th | 2024-04-19 | 2022-10-16 | 2026-08-16 |
| 5th (projected) | ~2028-03-XX | - | - |

### 3.2 Time Windows
- **Days before halving**: 550
- **Days after halving**: 880 (extended to capture bear market phase following bull run)
- **Total window**: 1430 days

### 3.3 Data Source

**CryptoCompare API** (single source of truth):
- **Base URL**: `https://min-api.cryptocompare.com`
- **Rate limit**: 10 calls/second (free tier)
- **Used for**:
  - Top coins by market cap (`/data/top/mktcapfull`)
  - Historical daily prices (`/data/v2/histoday`)
  - Volume data for TOTAL2 weighting
- **Advantage**: **No time limit** on free tier - can fetch full history

### 3.4 Price Data Caching

Price data is stored in parquet format (one file per coin-pair) in `data/raw/prices/`.

**File naming convention:**
```
{coin_id}-{quote_currency}.parquet
```

Examples: `eth-btc.parquet`, `eth-usd.parquet`, `sol-btc.parquet`

**Quote currencies configured in `config.py`:**
```python
QUOTE_CURRENCIES = ["BTC", "USD"]
```

**Incremental update behavior:**
1. Load existing parquet file for the coin-pair
2. Fetch only new data from `last_cached_date + 1` to yesterday
3. Merge using `pd.concat([cached, new_data])`
4. Deduplicate (keep newest if overlap)
5. **Overwrite the same parquet file** with combined data

This approach is preferred because:
- Dataset is small (~5000 rows per coin-pair)
- Daily updates add only a few rows
- Simpler than append-only storage
- Ensures data consistency

---

## 4. Token Filtering

### 4.1 Filter Implementation
Located in `src/analysis/filters.py`

### 4.2 Exclusion Categories

All categories below are excluded from **all analysis** (halving cycles and TOTAL2):

#### Bitcoin (base currency):
- Bitcoin is excluded as it's the base currency for price analysis

#### Stablecoins (stable vs fiat, not representative of crypto market trends):

Defined in `config.py` as `EXCLUDED_STABLECOINS`:
- **USD**: USDT, USDC, DAI, USDS, USDE, FDUSD, TUSD, FRAX, GHO, USD1, RLUSD, etc.
- **Euro**: EURS, EURT, EURC, AGEUR

#### Wrapped/Staked/Bridged:

Defined in `config.py` as `EXCLUDED_WRAPPED_STAKED_IDS` and `EXCLUDED_PATTERNS`:
- **Wrapped BTC**: WBTC, TBTC, FBTC, LBTC, SOLVBTC, CBBTC, etc.
- **Staked ETH**: STETH, WSTETH, RETH, CBETH, etc.
- **Wrapped ETH**: WETH, WBETH, WEETH
- **Aave wrapped**: AETHWETH, AWETH, AUSDC, etc.
- **Staked SOL**: JITOSOL, MSOL, BNSOL
- **Bridged**: Various bridged tokens

Plus pattern-based filtering for tokens matching: `^wrapped-`, `^staked-`, `^bridged-`, `^aeth`, `lido`, `rocket.?pool`, etc.

#### Allowed Tokens (never filtered):

Defined in `config.py` as `ALLOWED_TOKENS` - these override pattern-based exclusions:
```python
ALLOWED_TOKENS = {
    "sui", "sei",           # L1 blockchains (not "staked" tokens)
    "stk", "sand",          # Legitimate tokens
    "wif",                  # Meme tokens with "wif" prefix
    "xlm", "stx", "strk",   # Tokens with "st" prefix but not staked
    "storj", "snt", "strax",
    "stpt", "wild", "wifi",
}
```

#### Insufficient Historical Data:

Coins must have price data available before `MIN_DATA_DATE` (2024-01-10) to be included in **halving cycle analysis**. This ensures:
- Sufficient data for meaningful halving cycle comparisons
- Coins have enough history to calculate trends and patterns
- New/recent coins don't skew individual analysis with incomplete data

This filter is applied **after** fetching price data (in `fetch-prices` command), since the actual data start date is only known after fetching.

**Note:** This filter does NOT apply to TOTAL2 calculation. Recent coins are included in TOTAL2 because the index must be **immutable** - the value for any given day should not change when recalculated in the future. If we excluded recent coins today but included them next year (when they're no longer "recent"), historical TOTAL2 values would change retroactively. Instead, TOTAL2 captures the actual market composition (top 50 by volume) on each day, ensuring stable and reproducible values.

### 4.3 CSV Export
Rejected coins exported to `data/processed/rejected_coins.csv`:
- Semicolon delimiter (Excel compatible)
- Columns: Coin ID, Name, Symbol, Reason, URL
- Includes all rejection reasons: stablecoins, wrapped/staked/bridged tokens, BTC derivatives, and insufficient historical data

---

## 5. TOTAL2 Index Calculation

> **Detailed documentation:** [docs/TOTAL2_CALCULATION.md](docs/TOTAL2_CALCULATION.md)

### 5.1 Definition
Volume-weighted average price of top `TOP_N_FOR_TOTAL2` coins (default: 50), excluding:
- Bitcoin
- All wrapped/staked/bridged tokens
- All stablecoins

### 5.2 Volume Smoothing
Volume is smoothed using a 28-day Simple Moving Average (`VOLUME_SMA_WINDOW`) to reduce daily volatility.
This ensures stable rankings that don't fluctuate wildly from one day to the next.

### 5.3 Algorithm (Vectorized)
```python
# 1. Filter coin IDs BEFORE loading (BTC, derivatives, stablecoins are excluded)
eligible_coins = filter_coins_for_total2(all_cached_coins)

# 2. Load price data for eligible coins only
close_df = load_prices(eligible_coins)  # (dates × coins)
volume_df = load_volumes(eligible_coins)

# 3. Apply SMA smoothing
smoothed_volume = volume_df.rolling(window=VOLUME_SMA_WINDOW).mean()

# 4. Rank by smoothed volume per day
rank_df = smoothed_volume.rank(axis=1, ascending=False)
mask = rank_df <= TOP_N_FOR_TOTAL2

# 5. Calculate weighted average
numerator = (close_df.where(mask) * smoothed_volume.where(mask)).sum(axis=1)
denominator = smoothed_volume.where(mask).sum(axis=1)
total2 = numerator / denominator
```

---

## 6. Data Backfilling

### 6.1 Cutoff Date
- Only process coins with data before **2024-01-10**

### 6.2 Backfill Strategy
For coins missing early data:
1. Find first date with available data
2. Calculate scaling factor: `coin_price / total2_price` at that date
3. Apply scaling factor to TOTAL2 for all prior dates
4. Prepend scaled values to coin data

### 6.3 Normalization
After backfilling, normalize all series to start at 1.0.

---

## 7. Linear Regression

### 7.1 Configuration
- **Start date**: 2023-11-01
- **End date**: Current date
- **Minimum data points**: 30

### 7.2 Formula
`y = a*x + b` where:
- `x`: days since start
- `y`: normalized price (vs BTC)
- `a`: slope (trend strength)
- `b`: intercept

### 7.3 Filter Criterion
Keep only coins with `a > 0` (positive trend)

---

## 8. CLI Commands

### 8.1 Available Commands

```bash
# Fetch and filter top coins by market cap
python -m main list-coins [--top-n N] [--skip-ping]

# Fetch price data for filtered coins
python -m main fetch-prices [--limit N] [--no-incremental]

# Calculate TOTAL2 index
python -m main calculate-total2 [--top-n N] [--volume-sma N] [--quote-currency BTC|USD]

# Generate interactive charts
python -m main generate-charts [--output-dir PATH]

# Show current data status
python -m main status

# Clear cached data
python -m main clear-cache [--prices] [--api]
```

### 8.2 TOTAL2 Recalculation

The `calculate-total2` command **recomputes TOTAL2 from scratch** each time:

1. Loads all cached price data from `data/raw/prices/`
2. Applies token filters (excludes BTC, stablecoins, wrapped/staked)
3. Calculates volume-weighted average for each day
4. Overwrites `data/processed/total2_index.parquet`

This approach ensures:
- **Consistency**: All historical values are calculated with the same parameters
- **New coins included**: Recent coins appear in TOTAL2 for dates they have data
- **Reproducibility**: Same input data always produces same output

When adding new coins (e.g., expanding TOP_N_COINS), run:
```bash
python -m main list-coins      # Update coin list
python -m main fetch-prices    # Download new coin data
python -m main calculate-total2  # Recompute TOTAL2 with all coins
python -m main generate-charts   # Update visualizations
```

---

## 9. Visualization

### 9.1 Library: Plotly

### 9.2 Generated Charts

The `generate-charts` command creates interactive HTML files in `output/charts/`:

| File | Description |
|------|-------------|
| `total2_halving_cycles.html` | TOTAL2 across 3 halving cycles (2016, 2020, 2024 - cycle 1 excluded due to sparse data) |
| `btc_halving_cycles.html` | BTC/USD across 4 halving cycles (lighter→darker orange) |
| `total2_composition.html` | Interactive viewer: select date, see which coins are in TOTAL2 |

### 9.3 Chart Specifications

#### Halving Cycle Charts:
- **X-axis**: Days from halving (0 = halving day)
- **Y-axis**: Log scale for price
- **Vertical line**: Marks halving event
- **Hover**: Shows date, price, coin count, top 10 coins (for TOTAL2)

#### Individual Coin Charts:
- **Candlesticks**: Dark orange (#E67E22 up, #D35400 down)
- **TOTAL2 overlay**: Light grey (#CCCCCC)
- **Regression line**: Dotted dark blue (#1B4F72) - last cycle only
- **Aggregation**: Half-monthly (SMS frequency)

#### Layout:
- Stack all cycles vertically (latest on top)
- Formula displayed on last cycle chart
- Skip cycles without data

#### Top 10 Summary:
- Single chart with 10 lines
- Last cycle only
- Ranked by `a` coefficient

---

## 10. Development Commands

```bash
# Install dependencies
poetry install

# Activate virtual environment
poetry shell

# Run all tests
poetry run pytest tests/ -v

# Run specific test file
poetry run pytest tests/test_filters.py -v

# Run with coverage
poetry run pytest tests/ --cov=src --cov-report=html

# Format code
poetry run black src/ tests/

# Lint code
poetry run ruff src/ tests/
```

---

## 11. Key Configuration Values

```python
# From src/config.py

HALVING_DATES = [
    date(2012, 11, 28),
    date(2016, 7, 9),
    date(2020, 5, 11),
    date(2024, 4, 19),
]

DAYS_BEFORE_HALVING = 550
DAYS_AFTER_HALVING = 880

REGRESSION_START_DATE = date(2023, 11, 1)
MIN_DATA_DATE = date(2024, 1, 10)

TOP_N_COINS = 1000  # Increased to include historical coins (e.g., XEM)
TOP_N_FOR_TOTAL2 = 50
TOP_N_SUMMARY = 10
VOLUME_SMA_WINDOW = 28  # Days for volume smoothing

# Quote currencies for price data
QUOTE_CURRENCIES = ["BTC", "USD"]
DEFAULT_QUOTE_CURRENCY = "BTC"

CRYPTOCOMPARE_API_CALLS_PER_MINUTE = 30
CRYPTOCOMPARE_MAX_DAYS_PER_REQUEST = 2000
HALF_MONTHLY_FREQ = "SMS"
```

---

## 12. Notes for AI Agents

### 11.1 Import Pattern
Modules are directly in `src/`, not in a package subfolder:
```python
# In src/analysis/filters.py
from config import ALLOWED_TOKENS, ...

# In tests/test_filters.py
from analysis.filters import TokenFilter
```

### 11.2 Path Configuration
- `pyproject.toml` has `pythonpath = ["src"]`
- `conftest.py` adds `src` to `sys.path`
- VS Code settings have `python.analysis.extraPaths`

### 11.3 Testing
- Use `token_filter` as fixture name (not `filter` - reserved keyword)
- Tests use parametrization for extensive coverage
- CSV export uses semicolon for Excel compatibility

### 11.4 Common Pitfalls
1. Always check `ALLOWED_TOKENS` before filtering
2. Rate limit API calls (30/minute for CryptoCompare)
3. Handle missing data with backfilling

---

*Last updated: 2025-12-04*
*Document version: 5.0*
*Project name: Halvix*

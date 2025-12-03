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
├── PROJECT_CONTEXT.md          # This file (AI agent context)
├── docs/
│   └── TOTAL2_CALCULATION.md   # TOTAL2 index methodology
├── .vscode/
│   └── settings.json           # VS Code pytest configuration
│
├── src/                        # Source code (modules directly in src/)
│   ├── __init__.py
│   ├── config.py               # Constants, halving dates, API settings
│   ├── api/
│   │   ├── __init__.py
│   │   ├── coingecko.py        # CoinGecko API client ✅ IMPLEMENTED
│   │   └── cryptocompare.py    # CryptoCompare API client ✅ IMPLEMENTED
│   ├── data/
│   │   ├── __init__.py
│   │   ├── fetcher.py          # Data retrieval ✅ IMPLEMENTED
│   │   ├── processor.py        # TOTAL2 calculation ✅ IMPLEMENTED
│   │   ├── cache.py            # File-based caching ✅ IMPLEMENTED
│   │   └── symbol_mapping.py   # Symbol validation ✅ IMPLEMENTED
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── filters.py          # Token filtering ✅ IMPLEMENTED
│   │   └── regression.py       # Linear regression (to implement)
│   ├── visualization/
│   │   ├── __init__.py
│   │   ├── charts.py           # Plotly charts (to implement)
│   │   └── styles.py           # Color schemes (to implement)
│   └── main.py                 # Entry point ✅ IMPLEMENTED
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py             # Pytest configuration
│   ├── test_filters.py         # Token filtering tests ✅ IMPLEMENTED
│   ├── test_coingecko.py       # CoinGecko client tests ✅ IMPLEMENTED
│   ├── test_cryptocompare.py   # CryptoCompare client tests ✅ IMPLEMENTED
│   ├── test_cache.py           # Cache tests ✅ IMPLEMENTED
│   ├── test_fetcher.py         # Data fetcher tests ✅ IMPLEMENTED
│   ├── test_processor.py       # TOTAL2 processor tests ✅ IMPLEMENTED
│   └── test_symbol_mapping.py  # Symbol validation tests ✅ IMPLEMENTED
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
| 1st | 2012-11-28 | 2011-06-02 | 2014-06-01 |
| 2nd | 2016-07-09 | 2015-01-02 | 2018-01-09 |
| 3rd | 2020-05-11 | 2018-11-08 | 2021-11-12 |
| 4th | 2024-04-19 | 2022-10-16 | 2025-10-21 |
| 5th (projected) | ~2028-03-XX | - | - |

### 3.2 Time Windows
- **Days before halving**: 550
- **Days after halving**: 550
- **Total window**: 1100 days

### 3.3 Data Sources

**CoinGecko API** (for coin list and metadata):
- **Base URL**: `https://api.coingecko.com/api/v3`
- **Rate limit**: 10-30 calls/minute (free tier)
- **Used for**: Top coins list, market cap rankings, filtering
- **Limitation**: Free tier limited to 365 days of historical data

**CryptoCompare API** (for historical prices):
- **Base URL**: `https://min-api.cryptocompare.com`
- **Rate limit**: 100k calls/month (free tier)
- **Used for**: Historical daily prices (OHLCV)
- **Advantage**: **No time limit** on free tier - can fetch full history
- **Price denomination**: BTC (`tsym=BTC`)

---

## 4. Token Filtering

### 4.1 Filter Implementation
Located in `src/analysis/filters.py` - **IMPLEMENTED**

### 4.2 Exclusion Categories

#### Wrapped/Staked/Bridged (excluded from all analysis):

Defined in `config.py` as `EXCLUDED_WRAPPED_STAKED_IDS` and `EXCLUDED_PATTERNS`:
- **Wrapped BTC**: wrapped-bitcoin, tbtc, fbtc, lbtc, solvbtc, cbbtc, etc.
- **Staked ETH**: staked-ether, lido-staked-ether, wrapped-steth, rocket-pool-eth, coinbase-wrapped-staked-eth, etc.
- **Wrapped ETH**: wrapped-ether, wrapped-beacon-eth, wrapped-eeth
- **Staked SOL**: jito-staked-sol, marinade-staked-sol, bnsol
- **Bridged**: arbitrum-bridged-btc, l2-standard-bridged-weth, binance-bridged-usdt-bnb-smart-chain, etc.

Plus pattern-based filtering for tokens matching: `^wrapped-`, `^staked-`, `^bridged-`, `lido`, `rocket.?pool`, etc.

#### Stablecoins (excluded from TOTAL2):

Defined in `config.py` as `EXCLUDED_STABLECOINS`:
- **USD**: tether, usd-coin, dai, usds, ethena-usde, first-digital-usd, true-usd, frax, gho, etc.
- **Euro**: stasis-euro, tether-eurt, angle-euro
- **Bridged stablecoins**: binance-bridged-usdt-bnb-smart-chain, polygon-bridged-dai, etc.

#### Allowed Tokens (never filtered):

Defined in `config.py` as `ALLOWED_TOKENS` - these override pattern-based exclusions:
```python
ALLOWED_TOKENS = {
    "sui", "sei-network", "sei",       # L1 blockchains (not "staked" tokens)
    "stk", "the-sandbox", "sand",      # Legitimate tokens
    "dogwifhat", "wif",                # Meme tokens with "wif" prefix
    "stellar", "stacks", "starknet",   # Tokens with "st" prefix but not staked
    "storj", "status", "stratis",      # Other "st" prefix tokens
    "wilder-world", "wifi",            # Tokens with "wi" prefix
}
```

### 4.3 CSV Export
Rejected coins exported to `data/processed/rejected_coins.csv`:
- Semicolon delimiter (Excel compatible)
- Columns: Coin ID, Name, Symbol, Reason, CoinGecko URL

---

## 5. TOTAL2 Index Calculation

> **Detailed documentation:** [docs/TOTAL2_CALCULATION.md](docs/TOTAL2_CALCULATION.md)

### 5.1 Definition
Weighted average price of top `TOP_N_FOR_TOTAL2` coins (default: 50) by market cap, excluding:
- Bitcoin
- All wrapped/staked/bridged BTC tokens
- All stablecoins

### 5.2 Algorithm
```python
def compute_total2_daily(coins_data: dict, date: date) -> float:
    # 1. Filter out BTC, derivatives, stablecoins
    # 2. Get market caps for date
    # 3. Sort by market cap, take top 50
    # 4. Calculate weighted average price
    total_market_cap = sum(c['market_cap'] for c in top_50)
    weighted_price = sum(
        c['price_btc'] * (c['market_cap'] / total_market_cap) 
        for c in top_50
    )
    return weighted_price
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

## 8. Visualization

### 8.1 Library: Plotly

### 8.2 Chart Specifications

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

## 9. Implementation Status

| Module | File | Status |
|--------|------|--------|
| Configuration | `src/config.py` | ✅ Complete |
| Token Filtering | `src/analysis/filters.py` | ✅ Complete |
| Filter Tests | `tests/test_filters.py` | ✅ Complete |
| CoinGecko Client | `src/api/coingecko.py` | ✅ Complete |
| CoinGecko Tests | `tests/test_coingecko.py` | ✅ Complete |
| File Cache | `src/data/cache.py` | ✅ Complete |
| Cache Tests | `tests/test_cache.py` | ✅ Complete |
| Data Fetcher | `src/data/fetcher.py` | ✅ Complete |
| Fetcher Tests | `tests/test_fetcher.py` | ✅ Complete |
| CLI Entry Point | `src/main.py` | ✅ Complete |
| CryptoCompare Client | `src/api/cryptocompare.py` | ✅ Complete |
| CryptoCompare Tests | `tests/test_cryptocompare.py` | ✅ Complete |
| Data Processor | `src/data/processor.py` | ✅ Complete |
| Processor Tests | `tests/test_processor.py` | ✅ Complete |
| Symbol Mapping | `src/data/symbol_mapping.py` | ✅ Complete |
| Symbol Mapping Tests | `tests/test_symbol_mapping.py` | ✅ Complete |
| Regression | `src/analysis/regression.py` | ⏳ To implement |
| Charts | `src/visualization/charts.py` | ⏳ To implement |

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
DAYS_AFTER_HALVING = 550

REGRESSION_START_DATE = date(2023, 11, 1)
MIN_DATA_DATE = date(2024, 1, 10)

TOP_N_COINS = 300
TOP_N_FOR_TOTAL2 = 50
TOP_N_SUMMARY = 10

API_CALLS_PER_MINUTE = 10
HALF_MONTHLY_FREQ = "SMS"
```

---

## 12. Notes for AI Agents

### 12.1 Import Pattern
Modules are directly in `src/`, not in a package subfolder:
```python
# In src/analysis/filters.py
from config import ALLOWED_TOKENS, ...

# In tests/test_filters.py
from analysis.filters import TokenFilter
```

### 12.2 Path Configuration
- `pyproject.toml` has `pythonpath = ["src"]`
- `conftest.py` adds `src` to `sys.path`
- VS Code settings have `python.analysis.extraPaths`

### 12.3 Testing
- Use `token_filter` as fixture name (not `filter` - reserved keyword)
- Tests use parametrization for extensive coverage
- CSV export uses semicolon for Excel compatibility

### 12.4 Common Pitfalls
1. Always check `ALLOWED_TOKENS` before filtering
2. Use `for_total2=True` when filtering for TOTAL2 calculation
3. Rate limit API calls (10/minute)
4. Handle missing data with backfilling

---

*Last updated: 2025-12-03*
*Document version: 3.0*
*Project name: Halvix*

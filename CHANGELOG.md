# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Calendar Versioning](https://calver.org/) with the
format YYYY.MM.patch.

**Legend**
- **Categories** indicate the type of changes (Tests, Code, Documentation, etc.).
- Each version represents a significant milestone in development.

## 2025.12

### [2025.12.5] - 2025-12-04

**Bug fixes and documentation improvements**

- **Fixed:** End date for price fetching now dynamically set to yesterday instead of being capped at analysis end date (2025-10-21)
- **Fixed:** Coins without price data before MIN_DATA_DATE (2024-01-10) are now automatically removed from accepted_coins.json after price fetching
- **Added:** Coins removed due to insufficient historical data are now added to rejected_coins.csv with detailed reason (includes actual start date)
- **Added:** New badge style for "Insufficient historical data" in HTML documentation
- **Fixed:** Project structure in PROJECT_CONTEXT.md now correctly shows docs/ directory location
- **Fixed:** Stablecoin exclusion reason updated from "no price movement vs BTC" to "stable vs fiat, not representative of crypto market trends"
- **Updated:** Documentation to list insufficient historical data as a filtering criterion

**Categories:** Bug Fixes, Documentation, Features

### [2025.12.4] - 2025-12-03

**Codebase cleanup and consistency fixes**

- **Removed:** Obsolete CoinGecko references from code and documentation
- **Removed:** `--for-total2` CLI option (stablecoins are always excluded)
- **Removed:** `for_total2` parameter from all filter functions
- **Removed:** Obsolete files: `coingecko.py`, `symbol_mapping.py`, and related tests
- **Removed:** Redundant implementation status table from `PROJECT_CONTEXT.md`
- **Fixed:** `__version__` removed from `src/__init__.py` (use `importlib.metadata`)
- **Fixed:** Test `test_stablecoins_kept_when_not_for_total2` removed (obsolete)
- **Updated:** Cache docstrings to use "Coin ID" instead of "CoinGecko coin ID"
- **Updated:** Project structure in docs to reflect actual files

**Categories:** Cleanup, Documentation, Tests

### [2025.12.3] - 2025-12-03

**Filter stablecoins by default + GitHub Pages documentation**

- **Changed:** Stablecoins now always excluded from analysis (not just TOTAL2)
- **Added:** GitHub Pages deployment with live data status page
- **Added:** Automatic documentation generation after `list-coins` and `fetch-prices` commands
- **Added:** AETHWETH and other Aave wrapped tokens to exclusion list
- **Added:** EURC to stablecoin exclusion list
- **Improved:** HTML tables now have clickable coin names linking to CryptoCompare
- **Improved:** Removed redundant ID column from HTML tables
- **Updated:** Documentation to reflect new filtering behavior

**Categories:** Features, Documentation, Filtering

### [2025.12.2] - 2025-12-03

**Major refactoring: single data source with volume-weighted TOTAL2**

- **Removed:** CoinGecko API client (coingecko.py)
- **Removed:** Symbol mapping module (symbol_mapping.py)
- **Removed:** python-dateutil dependency
- **Changed:** CryptoCompare is now the single data source for all data
- **Changed:** TOTAL2 calculation now uses volume-weighting instead of market-cap weighting
- **Changed:** Coin IDs now use lowercase symbols (e.g., "eth" instead of "ethereum")
- **Changed:** User-Agent now uses dynamic version from package metadata
- **Updated:** All documentation to reflect new architecture
- **Updated:** Tests to use volume-based TOTAL2 calculation
- **Fixed:** Entry point in pyproject.toml follows typical pattern

**Categories:** Refactoring, API, Documentation, Tests

### [2025.12.1] - 2025-12-03

**Initial release - Bitcoin halving cycle analysis**

- **Feature:** Data fetching from CoinGecko and CryptoCompare APIs
- **Feature:** Price data processing and caching with Parquet format
- **Feature:** Symbol mapping between different data sources
- **Feature:** Analysis filters for halving cycle comparison
- **Feature:** Visualization support with Plotly
- **Testing:** Comprehensive test suite for all components
- **Documentation:** Project context, data sources, and edge cases documentation

**Categories:** Features, Testing, Documentation, Data, API

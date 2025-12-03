# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Calendar Versioning](https://calver.org/) with the
format YYYY.MM.patch.

**Legend**
- **Categories** indicate the type of changes (Tests, Code, Documentation, etc.).
- Each version represents a significant milestone in development.

## 2025.12

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

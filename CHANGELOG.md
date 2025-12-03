# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Calendar Versioning](https://calver.org/) with the
format YYYY.MM.patch.

**Legend**
- **Categories** indicate the type of changes (Tests, Code, Documentation, etc.).
- Each version represents a significant milestone in development.

## 2025.12

### [2025.12.1] - 2025-12-03

```
- Feature: Initial release of halvix
- Feature: Data fetching from CoinGecko and CryptoCompare APIs
- Feature: Price data processing and caching with Parquet format
- Feature: Symbol mapping between different data sources
- Feature: Analysis filters for halving cycle comparison
- Feature: Visualization support with Plotly
- Testing: Comprehensive test suite for all components
- Documentation: Project context, data sources, and edge cases documentation

Files in this release:
- src/api/coingecko.py
- src/api/cryptocompare.py
- src/data/cache.py
- src/data/fetcher.py
- src/data/processor.py
- src/data/symbol_mapping.py
- src/analysis/filters.py
- src/visualization/__init__.py
- src/config.py
- src/main.py
- tests/
- docs/
```

**Initial release - Review of BTC halving cycles**

- **Categories:** Features, Testing, Documentation, Data, API


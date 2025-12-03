# Halvix

**Cryptocurrency price analysis relative to Bitcoin halving cycles.**

Halvix analyzes cryptocurrency performance across BTC halving cycles, comparing each coin's price action against the TOTAL2 market index (volume-weighted index of top altcoins).

## Features

- 📊 Retrieve and analyze top 300 cryptocurrencies by market cap
- 📈 Compare price performance across 4 BTC halving cycles
- 🔍 Filter out wrapped, staked, bridged tokens and stablecoins
- 📉 Linear regression analysis to identify uptrending coins
- 🎨 Generate visual charts with Plotly (candlesticks + overlays)
- 🏆 Rank top 10 performers by trend strength

## Quick Start

```bash
# Install
poetry install

# Fetch and filter coins
poetry run python -m main list-coins

# Fetch price data
poetry run python -m main fetch-prices

# Check status
poetry run python -m main status
```

📖 **See [Tutorial](docs/TUTORIAL.md)** for detailed step-by-step instructions.

## Documentation

### 📊 Live Data

- **[Data Status](https://yohplala.github.io/halvix/)** - Current coin lists, filtered coins, and price data summary

### 📋 References

- **[CHANGELOG](CHANGELOG.md)** - Version history and release notes
- **[Project Context](docs/PROJECT_CONTEXT.md)** - Full project specification for developers
- **[Data Sources](docs/DATA_SOURCES.md)** - CryptoCompare API details, rate limits, caching
- **[TOTAL2 Calculation](docs/TOTAL2_CALCULATION.md)** - How the TOTAL2 market index is calculated

## Project Status

| Module | Status |
|--------|--------|
| Configuration | ✅ Complete |
| Token Filtering | ✅ Complete |
| CryptoCompare Client | ✅ Complete |
| Data Fetcher & Caching | ✅ Complete |
| TOTAL2 Calculation | ✅ Complete |
| GitHub Pages Docs | ✅ Complete |
| Linear Regression | ⏳ To implement |
| Visualization | ⏳ To implement |

## License

MIT

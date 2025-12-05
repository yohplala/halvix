# Halvix

**Cryptocurrency price analysis relative to Bitcoin halving cycles.**

Halvix analyzes cryptocurrency performance across BTC halving cycles, comparing each coin's price action against the TOTAL2 market index (volume-weighted index of top altcoins).

## Features

- 📊 Retrieve and analyze top 1000 cryptocurrencies by market cap
- 🔍 Filter out from download wrapped, staked, bridged tokens and stablecoins
- 📉 Volume-weighted TOTAL2 index with 28-day SMA smoothing
- 📈 Compare altcoins price performance across 2nd to 4th BTC halving cycles
- 🎨 Interactive Plotly charts with normalized values
- 🧩 Composition viewer to explore TOTAL2 makeup on any date

## Quick Start

```bash
# Install
poetry install

# Fetch and filter coins
poetry run python -m main list-coins

# Fetch price data (BTC and USD)
poetry run python -m main fetch-prices

# Calculate TOTAL2 index
poetry run python -m main calculate-total2

# Generate interactive charts
poetry run python -m main generate-charts

# Check status
poetry run python -m main status
```

📖 **See [Tutorial](docs/TUTORIAL.md)** for detailed step-by-step instructions.

## Documentation

### 📊 Live Data & Charts

- **[Charts Dashboard](https://yohplala.github.io/halvix/charts.html)** - Interactive halving cycle charts (BTC, TOTAL2)
- **[Data Status](https://yohplala.github.io/halvix/index.html)** - Current coin lists, filtered coins, and price data summary

### 📋 References
- **[Project Context](docs/PROJECT_CONTEXT.md)** - Full project specification for developers
- **[Data Sources](docs/DATA_SOURCES.md)** - CryptoCompare API details, rate limits, caching
- **[TOTAL2 Calculation](docs/TOTAL2_CALCULATION.md)** - How the TOTAL2 market index is calculated
- **[Deployment](docs/DEPLOYMENT.md)** - Charts generation and GitHub Pages deployment workflow
- **[Changelog](CHANGELOG.md)** - Version history and release notes

## Project Status

| Module | Status |
|--------|--------|
| Configuration | ✅ Complete |
| Token Filtering | ✅ Complete |
| CryptoCompare Client | ✅ Complete |
| Data Fetcher & Caching | ✅ Complete |
| TOTAL2 Calculation | ✅ Complete |
| GitHub Pages Docs | ✅ Complete |
| Halving Cycle Charts | ✅ Complete |
| Linear Regression | ⏳ To implement |


## License

[MIT](LICENSE)

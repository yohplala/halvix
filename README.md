# Halvix

**Cryptocurrency price analysis relative to Bitcoin halving cycles.**

Halvix analyzes cryptocurrency performance across BTC halving cycles (2nd through 5th), comparing each coin's price action against the TOTAL2b market index (volume-weighted index of top altcoins).

## Features

- 📊 Retrieve and analyze top 1200 cryptocurrencies by market cap
- 🔍 Filter out wrapped, staked, bridged tokens and stablecoins
- 📉 Volume-weighted TOTAL2b index with 21-day freeze period and price scaling
- 📈 Compare altcoin price performance across 2nd to 5th BTC halving cycles
- 🎯 Cycle pattern analysis with 4 projection methods (trendline, Fibonacci, diminishing returns, historical peak)
- 🏆 Composite ranking of altcoins by projected return with confidence scoring
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

# Generate halving cycle charts
poetry run python -m main generate-cycle-charts

# Analyze cycle patterns
poetry run python -m main analyze-patterns

# Check status
poetry run python -m main status
```

📖 **See [Tutorial](docs/TUTORIAL.md)** for detailed step-by-step instructions.

## Documentation

### 📊 Live Data & Charts

- **[Charts Dashboard](https://yohplala.github.io/halvix/index.html)** - Interactive halving cycle charts (BTC, TOTAL2)

### 📋 References
- **[AI Agent Context](CLAUDE.md)** - Full project specification for AI agents and developers
- **[Data Sources](docs/DATA_SOURCES.md)** - CryptoCompare API details, rate limits, caching, data pipeline
- **[TOTAL2 Calculation](docs/TOTAL2_CALCULATION.md)** - How the TOTAL2 market index is calculated
- **[Identification Kernel](docs/IDENTIFICATION_KERNEL.md)** - Segment-based cycle point detection algorithm
- **[Pattern Analysis](docs/PATTERN_ANALYSIS.md)** - Cycle pattern analysis and price target projections
- **[Deployment](docs/DEPLOYMENT.md)** - Charts generation and GitHub Pages deployment workflow
- **[Changelog](CHANGELOG.md)** - Version history and release notes

## Project Status

| Module | Status |
|--------|--------|
| Configuration | ✅ Complete |
| Coin Filtering | ✅ Complete |
| CryptoCompare Client | ✅ Complete |
| Data Fetcher & Caching | ✅ Complete |
| TOTAL2 Calculation | ✅ Complete |
| GitHub Pages Docs | ✅ Complete |
| Halving Cycle Charts | ✅ Complete |
| Cycle Pattern Analysis | ✅ Complete |


## License

[MIT](LICENSE)

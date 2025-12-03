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
- 📁 Export filtered tokens to CSV for review

## Installation

```bash
# Install dependencies with Poetry
poetry install

# Activate virtual environment (optional, for interactive use)
poetry shell
```

## Quick Start (Command Line)

### Prerequisites

Ensure you have Python 3.11+ and Poetry installed:

```bash
# Check Python version
python --version  # Should be 3.11 or higher

# Install Poetry (if not already installed)
# Windows (PowerShell)
(Invoke-WebRequest -Uri https://install.python-poetry.org -UseBasicParsing).Content | python -

# macOS/Linux
curl -sSL https://install.python-poetry.org | python3 -
```

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd halvix

# Install dependencies
poetry install
```

### Running the Analysis Pipeline

The analysis is run in stages via the command line:

#### Step 1: Fetch and Filter Top Coins

Fetch the top N coins by market cap from CryptoCompare and apply filtering to exclude wrapped, staked, bridged tokens:

```bash
# Fetch top 300 coins (default)
poetry run python -m main list-coins

# Fetch a different number of coins
poetry run python -m main list-coins --top 500
poetry run python -m main list-coins -n 100

# Also exclude stablecoins (for TOTAL2 calculation)
poetry run python -m main list-coins --for-total2

# Force fresh fetch, ignore cache
poetry run python -m main list-coins --no-cache
```

**Output files:**
- `data/processed/accepted_coins.json` - Coins accepted for analysis
- `data/processed/rejected_coins.csv` - Coins rejected with reasons

#### Step 2: Fetch Price Data

Fetch historical price data (in BTC) for all filtered coins:

```bash
# Fetch prices (incremental - only new data since last cache)
poetry run python -m main fetch-prices

# Full refresh (fetch complete history)
poetry run python -m main fetch-prices --full-refresh

# Limit to first N coins (for testing)
poetry run python -m main fetch-prices --limit 10
```

**Features:**
- **Incremental updates**: Only fetches new data since last cache, then merges with existing data and overwrites the parquet file (efficient for small daily updates)
- **Yesterday as end date**: Avoids incomplete intraday data

**Note:** This step uses CryptoCompare API for full historical data (~5000+ days). Rate limiting is applied automatically.

**Output:** Price data cached in `data/raw/prices/` as parquet files (one file per coin, overwritten on each update).

#### Step 3: Calculate TOTAL2 Index

Calculate the volume-weighted TOTAL2 index from cached price data:

```bash
# Calculate TOTAL2 with default 50 coins
poetry run python -m main calculate-total2

# Calculate with different number of coins
poetry run python -m main calculate-total2 --top-n 30

# Dry run (calculate but don't save)
poetry run python -m main calculate-total2 --dry-run
```

**Output files:**
- `data/processed/total2_index.parquet` - Daily TOTAL2 values
- `data/processed/total2_daily_composition.parquet` - Which coins were in TOTAL2 each day

See [TOTAL2 Calculation](docs/TOTAL2_CALCULATION.md) for methodology details.

#### Step 4: Check Data Status

View current data status and cached files:

```bash
# Show basic status
poetry run python -m main status

# Show detailed information
poetry run python -m main status --verbose
```

#### Step 5: Clear Cache (Optional)

Clear cached data when needed:

```bash
# Clear price data cache
poetry run python -m main clear-cache --prices

# Clear API response cache
poetry run python -m main clear-cache --api

# Clear all caches
poetry run python -m main clear-cache --prices --api
```

### Alternative: Running with Poetry Shell

```bash
# Activate virtual environment
poetry shell

# Then run commands without 'poetry run' prefix
cd src
python -m main list-coins
python -m main fetch-prices
python -m main status

# Deactivate when done
exit
```

### Running Tests

```bash
# Run all tests
poetry run pytest

# Run tests with verbose output
poetry run pytest -v

# Run specific test file
poetry run pytest tests/test_filters.py -v

# Run tests with coverage report
poetry run pytest --cov=src --cov-report=html

# Run integration tests (actual API calls)
poetry run pytest --run-integration -v
```

### Code Quality

```bash
# Format code with Black
poetry run black src/ tests/

# Lint code with Ruff
poetry run ruff check src/ tests/

# Auto-fix linting issues
poetry run ruff check --fix src/ tests/
```

## Documentation

- **📊 [Live Data Status](https://yohplala.github.io/halvix/)** - Current coin lists and price data status (GitHub Pages)
- **[CHANGELOG](CHANGELOG.md)** - Version history and release notes
- **[TOTAL2 Calculation](docs/TOTAL2_CALCULATION.md)** - How the TOTAL2 market index is calculated
- **[Data Sources](docs/DATA_SOURCES.md)** - CryptoCompare API details, rate limits, data flow
- **[PROJECT_CONTEXT.md](PROJECT_CONTEXT.md)** - Full project specification for developers

## Configuration

Key parameters in `src/config.py`:

| Parameter | Value | Description |
|-----------|-------|-------------|
| `TOP_N_COINS` | 300 | Number of top coins to analyze (configurable via `--top` flag) |
| `TOP_N_FOR_TOTAL2` | 50 | Number of coins in TOTAL2 index ([see calculation](docs/TOTAL2_CALCULATION.md)) |
| `HALVING_DATES` | 2012-11-28, 2016-07-09, 2020-05-11, 2024-04-19 | BTC halving dates |
| `DAYS_BEFORE_HALVING` | 550 | Days before halving in time window |
| `DAYS_AFTER_HALVING` | 550 | Days after halving in time window |
| `REGRESSION_START_DATE` | 2023-11-01 | Start of regression analysis window |
| `CRYPTOCOMPARE_API_CALLS_PER_MINUTE` | 30 | CryptoCompare rate limit |

## Token Filtering

The project automatically filters out:

### Excluded from all analysis:
- **Wrapped tokens**: wBTC, wETH, wSOL, wBNB, etc.
- **Staked tokens**: stETH, JitoSOL, mSOL, cbETH, etc.
- **Bridged tokens**: Arbitrum bridged BTC, L2 bridged WETH, etc.
- **Liquid staking derivatives**: Lido, Rocket Pool, Renzo, etc.

### Excluded from TOTAL2 calculation:
- All of the above, plus:
- **Stablecoins**: USDT, USDC, DAI, FRAX, GHO, etc.

### Always allowed (never filtered):
- SUI, SEI, STK, SAND, WIF
- Stellar (XLM), Stacks (STX), Starknet (STRK)

Rejected coins are exported to `data/processed/rejected_coins.csv` for review.

## Data Source

Halvix uses **CryptoCompare** as its single data source:

| Feature | Details |
|---------|---------|
| **Top coins** | `/data/top/mktcapfull` - Market cap rankings with full data |
| **Historical prices** | `/data/v2/histoday` - Daily OHLCV with **unlimited** history |
| **Volume data** | 24h volume for TOTAL2 weighting |
| **Rate limit** | 10 calls/second (free tier) |

📖 **See [Data Sources](docs/DATA_SOURCES.md)** for detailed rate limits and troubleshooting.

## Development

### Project Status

| Module | Status |
|--------|--------|
| Configuration (`config.py`) | ✅ Complete |
| Token Filtering (`analysis/filters.py`) | ✅ Complete |
| CryptoCompare Client (`api/cryptocompare.py`) | ✅ Complete |
| File Caching (`data/cache.py`) | ✅ Complete |
| Data Fetcher (`data/fetcher.py`) | ✅ Complete |
| CLI Entry Point (`main.py`) | ✅ Complete |
| Data Processor/TOTAL2 (`data/processor.py`) | ✅ Complete |
| Linear Regression | ⏳ To implement |
| Visualization | ⏳ To implement |

### Development Commands

```bash
# Run all tests
poetry run pytest

# Run tests with coverage
poetry run pytest --cov=src --cov-report=html
# Open htmlcov/index.html in browser to view report

# Format code
poetry run black src/ tests/

# Lint code
poetry run ruff check src/ tests/

# Watch tests (requires pytest-watch: poetry add --dev pytest-watch)
poetry run ptw
```

### Interactive Development

```bash
# Start IPython in project context
poetry run ipython

# Then in IPython (after cd src):
# >>> from analysis.filters import TokenFilter
# >>> tf = TokenFilter()
# >>> tf.is_wrapped_or_staked("wbtc", "Wrapped BTC")
# True
```

## VS Code Setup

The project includes VS Code settings for pytest integration:
1. Open the project in VS Code
2. Install the Python extension
3. Tests will auto-discover via `.vscode/settings.json`
4. Use the Testing sidebar to run tests

## License

MIT

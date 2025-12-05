# Halvix Tutorial

**[← Back to README](../README.md)**

---

Step-by-step guide to using Halvix for cryptocurrency halving cycle analysis.

## Prerequisites

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

## Installation

```bash
# Clone the repository
git clone https://github.com/yohplala/halvix.git
cd halvix

# Install dependencies
poetry install

# Activate virtual environment (optional, for interactive use)
poetry shell
```

---

## Running the Analysis Pipeline

The analysis is run in stages via the command line.

### Step 1: Fetch and Filter Top Coins

Fetch the top N coins by market cap from CryptoCompare and apply filtering to exclude wrapped, staked, bridged tokens and stablecoins:

```bash
# Fetch top 1000 coins (default)
poetry run python -m main list-coins

# Fetch a different number of coins
poetry run python -m main list-coins --top 500
poetry run python -m main list-coins -n 100

# Force fresh fetch, ignore cache
poetry run python -m main list-coins --no-cache
```

**Output files:**
- `data/processed/accepted_coins.json` - Coins accepted for analysis
- `data/processed/rejected_coins.csv` - Coins rejected with reasons
- `site/index.html` - Auto-generated documentation page

### Step 2: Fetch Price Data

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
- **Incremental updates**: Only fetches new data since last cache, then merges with existing data and overwrites the parquet file
- **Yesterday as end date**: Avoids incomplete intraday data
- **Automatic trimming**: Leading rows with zero prices (before coin existed) are removed

**Note:** This step uses CryptoCompare API for full historical data (~5000+ days). Rate limiting is applied automatically.

**Output:** Price data cached in `data/raw/prices/` as parquet files (one file per coin).

### Step 3: Calculate TOTAL2 Index

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

See [TOTAL2 Calculation](TOTAL2_CALCULATION.md) for methodology details.

### Step 4: Check Data Status

View current data status and cached files:

```bash
# Show basic status
poetry run python -m main status

# Show detailed information
poetry run python -m main status --verbose
```

### Step 5: Clear Cache (Optional)

Clear cached data when needed:

```bash
# Clear price data cache
poetry run python -m main clear-cache --prices

# Clear API response cache
poetry run python -m main clear-cache --api

# Clear all caches
poetry run python -m main clear-cache --prices --api
```

---

## Alternative: Running with Poetry Shell

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

---

## Running Tests

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

---

## Code Quality

```bash
# Format code with Black
poetry run black src/ tests/

# Lint code with Ruff
poetry run ruff check src/ tests/

# Auto-fix linting issues
poetry run ruff check --fix src/ tests/
```

---

## Development Commands

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

---

## Interactive Development

```bash
# Start IPython in project context
poetry run ipython

# Then in IPython (after cd src):
# >>> from analysis.filters import TokenFilter
# >>> tf = TokenFilter()
# >>> tf.is_wrapped_or_staked("wbtc", "Wrapped BTC")
# True
```

---

## VS Code Setup

The project includes VS Code settings for pytest integration:

1. Open the project in VS Code
2. Install the Python extension
3. Tests will auto-discover via `.vscode/settings.json`
4. Use the Testing sidebar to run tests

---

*Last updated: 2025-12-03*

---

**[← Back to README](../README.md)**

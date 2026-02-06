# Halvix Tutorial

**[← Back to README](../README.md)**

---

Step-by-step guide to using Halvix for cryptocurrency halving cycle analysis.

## Prerequisites

Ensure you have Python 3.13+ and Poetry installed:

```bash
# Check Python version
python --version  # Should be 3.13 or higher

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
# Fetch top 1200 coins (default)
poetry run python -m main list-coins

# Fetch a different number of coins
poetry run python -m main list-coins --top 500
poetry run python -m main list-coins -n 100

# Force fresh fetch, ignore cache
poetry run python -m main list-coins --no-cache
```

**Output files:**
- `data/processed/coins_to_download.json` - Coins to download
- `data/processed/download_skipped.csv` - Download skipped with reasons
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
# Calculate TOTAL2 with default 30 coins
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

### Step 4: Generate Halving Cycle Charts

Generate interactive HTML charts comparing BTC and TOTAL2 across halving cycles:

```bash
# Generate all cycle charts (default output: site/charts/)
poetry run python -m main generate-cycle-charts

# Custom output directory
poetry run python -m main generate-cycle-charts --output-dir ./my-charts
```

**Output files:**
- `site/charts/btc_charts.html` - BTC/USD normalized and absolute price across 4 halving cycles
- `site/charts/total2_charts.html` - TOTAL2 index vs USD and BTC across 3 halving cycles
- `site/charts/total2_composition.html` - Interactive explorer for TOTAL2 composition by date
- `site/index.html` - Main navigation page linking all charts

### Step 5: Analyze Cycle Patterns

Run pattern analysis to identify cycle min/max points and project price targets using trendlines, Fibonacci extensions, and diminishing returns models:

```bash
# Run pattern analysis (default: top 14 altcoins)
poetry run python -m main analyze-patterns

# Analyze more altcoins
poetry run python -m main analyze-patterns --top-n 15

# Custom output directory
poetry run python -m main analyze-patterns --output-dir ./output

# Suppress progress bars
poetry run python -m main analyze-patterns --quiet
```

**Options:**
- `--top-n N` / `-n N` - Number of top altcoins to include (default: 14)
- `--output-dir PATH` - Output directory for pattern charts (default: site/)
- `--quiet` / `-q` - Suppress progress bars

**Output files:**
- `site/pattern_analysis.html` - Main page with altcoin ranking table and composite scores
- `site/charts/pattern_btc.html` - BTC/USD pattern chart with cycle points
- `site/charts/pattern_{coin}.html` - Individual altcoin pattern charts
- `data/processed/pattern_targets.json` - JSON with all computed targets

See [PATTERN_ANALYSIS.md](PATTERN_ANALYSIS.md) for detailed methodology.

### Step 6: Check Data Status

View current data status and cached files:

```bash
# Show basic status
poetry run python -m main status

# Show detailed information
poetry run python -m main status --verbose
```

### Step 7: Clear Cache (Optional)

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
# >>> from analysis.filters import CoinFilter
# >>> cf = CoinFilter()
# >>> cf.is_wrapped_or_staked("wbtc", "Wrapped BTC")
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

**[← Back to README](../README.md)**

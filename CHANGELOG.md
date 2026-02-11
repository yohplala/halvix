# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Calendar Versioning](https://calver.org/) with the
format YYYY.MM.patch.

**Legend**
- **Categories** indicate the type of changes (Tests, Code, Documentation, etc.).
- Each version represents a significant milestone in development.

## 2026.02

### [2026.02.3] - 2026-02-11

**Segment-based cycle point detection**

- **Added:** Segment-based cycle point detection algorithm (`_identify_cycle_points`)
  - 3-pass detection: max2 search, min2 candidates, sequential validation
  - 23.6% Fibonacci retracement thresholds for optional points (min2, max1)
  - Alternation rule: no max1 in prev segment suppresses min2 in next
  - Merge logic for adjacent maxes when min2 validation fails
  - min2 search extends back to prev max1 to catch pre-halving lows (e.g., COVID crash)
  - min1 replacement when price goes lower before max2 (no min2 case)
- **Added:** Short-history token support
  - Launch-price min2 fallback (`_adjust_launch_min2`) for tokens starting near a halving
  - Projected min1 at 23.6% retracement level with open-circle chart markers
  - max1 detection before min2 for tokens with no prior segment data
  - min1 < max2 guard for tokens without reference price
- **Added:** Visual hints for projection methods in pattern charts
  - Fibonacci extension: solid orange line connecting A→B→C extrema
  - Diminishing returns: dotted purple lines connecting min-max pairs
  - Trendlines: dotted blue (upper) and grey (lower)
  - Historical peak: green line matching star color
- **Added:** `--include` CLI flag to force-include coins bypassing TOTAL2 filter
- **Added:** `IDENTIFICATION_KERNEL.md` documentation
- **Changed:** Unified BTC/altcoin chart into single `_create_pattern_chart`
- **Changed:** Fibonacci extension level 127.2% → 100%
- **Changed:** Diminishing returns floor (`DIM_RETURN_MIN_GAIN_RATIO`) 0.1 → 1.0
- **Changed:** 5th halving consolidated into `HALVING_DATES`, removed `PROJECTED_5TH_HALVING`
- **Removed:** Legacy Total2 processor (`processor_total2.py`)
- **Removed:** Dead code: `HalvingCycle`, `HALVING_CYCLES`, `utils/dates.py`, stale re-exports
- **Improved:** Vectorized hot paths (max weight change, coin statistics, symbol resurrection)
- **Improved:** Narrowed broad `except Exception` to specific exception types
- **Improved:** Extracted Jinja2 templates for data status, index, and total2 statistics pages
- **Fixed:** `data_status.html` showing 0 coins in daily build (missing fetch metadata restore)
- **Fixed:** Jinja2 autoescape issues (missing `| safe` filters for raw HTML)

**Categories:** Features, Analysis, Visualization, Refactoring, Code Quality, Documentation, CI/CD

### [2026.02.1] - 2026-02-05

**Cycle pattern analysis with price target projections**

- **Added:** Cycle pattern analysis module (`analysis/cycle_patterns.py`)
  - Identifies min/max points within halving cycle windows
  - Four projection methods: Log-Linear Trendline, Fibonacci 100% Extension, Diminishing Returns, Historical Peak
  - Composite scoring with confidence-weighted average of available methods
  - TOTAL2 composition filtering for altcoin analysis
  - Symbol replacement detection for tokens with zero price history
- **Added:** Pattern analysis charts (`visualization/pattern_charts.py`)
  - BTC/USD and altcoin/BTC pattern visualization
  - Interactive charts showing cycle points and target projections
  - Main pattern analysis page with top 14 altcoins ranking
  - Refactored target labels for proper log-scale positioning with colored text
- **Improved:** Stricter data validation for cycle pattern analysis
  - Log-price overflow protection for extreme trendline projections
  - Prevents unreliable extrapolations from short data spans
- **Changed:** Renamed `AGENTS.md` to `CLAUDE.md`
- **Removed:** Cursor rules file
- **Fixed:** Edge cases in pattern analysis for coins with limited history
- **Fixed:** Linter errors (set comprehensions, unused imports/variables)

**Categories:** Features, Analysis, Visualization, Code Quality, Refactoring

## 2025.12

### [2025.12.10] - 2025-12-25

**Codebase cleanup and rate limiting fixes**

- **Fixed:** Rate limit `recommended_wait_seconds` logic to match test expectations
  - Corrected wait times: second quota exhausted (1s), minute near limit (10s), hour near limit (60s)
  - Removed unreachable code path in `recommended_wait_seconds` property
- **Fixed:** All rate limiting tests now pass (17/17)
- **Improved:** Comprehensive codebase review for consistency
  - Verified no leftover refactoring artifacts (CoinGecko, symbol_mapping references)
  - Confirmed all imports and dependencies are consistent
  - Verified documentation matches code implementation
  - Confirmed error handling and logging patterns are consistent
- **Verified:** Code quality checks pass (no linter errors, all tests passing)

**Categories:** Bug Fixes, Code Quality, Testing

### [2025.12.9] - 2025-12-23

**Automated daily updates and refresh timestamps**

- **Added:** Daily Update workflow (`daily-update.yml`) for automated pipeline execution
  - Scheduled to run at 6:00 AM UTC every day
  - Runs full pipeline: fetch data → calculate TOTAL2b → deploy to GitHub Pages
  - Manual trigger option with `skip_fetch` parameter
- **Added:** "Last updated" timestamp displayed in footer of all generated HTML pages
  - Shows UTC datetime when charts were generated
  - Visible on index page, chart pages, and composition viewer
- **Updated:** `DEPLOYMENT.md` documentation with daily schedule information
  - New architecture diagram showing automated workflow
  - Quick reference section for automatic updates
  - Updated troubleshooting section
- **Fixed:** `data_status.html` now saved to `raw-data` branch and restored during deploy
  - Page was missing from GitHub Pages because it wasn't persisted between workflow jobs

**Categories:** CI/CD, Features, Documentation, Bug Fixes

### [2025.12.8] - 2025-12-19

**TOTAL2b processor: freeze period and price scaling**

- **Added:** New TOTAL2b processor with improved entry mechanics
  - 21-day freeze period for new coins before index inclusion
  - Price scaling at entry: scales new coin prices to match previous TOTAL2b value
  - Symbol replacement detection (e.g., HYPE replaced by Hyperliquid)
  - No outlier detection (simpler, more transparent methodology)
- **Added:** `.cursorrules` file for development environment configuration
- **Added:** Factory function `get_processor()` to select TOTAL2 or TOTAL2b processor
- **Changed:** TOTAL2b is now the default index type
- **Changed:** Renamed `PROJECT_CONTEXT.md` to `AGENTS.md` for AI agent quick reference
- **Changed:** CI workflow refactored: separate `data.yml` workflow, removed test job
- **Fixed:** Multiple fixes in TOTAL2 calculation logic
- **Updated:** `TOTAL2_CALCULATION.md` with comprehensive TOTAL2b algorithm documentation
- **Updated:** `DEPLOYMENT.md`, `DATA_SOURCES.md`, `TUTORIAL.md`, `README.md` documentation

**Categories:** Features, Algorithm, Refactoring, Documentation, CI/CD

### [2025.12.7] - 2025-12-08

**TOTAL2 price smoothing overhaul and governance tokens inclusion**

- **Added:** Governance tokens to ALLOWED_TOKENS list (BARD, DBR, FXS, LDO, MNDE, REZ, RPL, SD, SWELL)
  - These are governance tokens for staking/bridging protocols, not wrapped tokens themselves
  - Note: FXS (Frax Share) is the governance token; FRAX is the stablecoin
- **Added:** TOTAL2 entry warmup with iterative price capping
  - Replaces SMA smoothing with max +80% gain / -40% loss per day capping
  - Uses corrected TOTAL2 (market level) as baseline before entry
  - Handles both ZEC-type (extreme launch price) and YFI-type (growth before entry) cases
  - Configurable via `TOTAL2_ENTRY_MAX_INCREASE`, `TOTAL2_ENTRY_MAX_DECREASE`, `TOTAL2_ENTRY_WARMUP_DAYS`
- **Added:** TOTAL2 Statistics page with coin ranking table
  - Sortable table showing all coins that appeared in TOTAL2
  - Displays rank, days in TOTAL2, first/last/min/max price and weight
  - Links to CryptoCompare coin pages
- **Added:** Two-pass TOTAL2 calculation algorithm
  - Pass 1: Calculate raw TOTAL2, apply outlier detection to series itself
  - Pass 2: Apply entry warmup capping, recalculate final TOTAL2
- **Changed:** Renamed "Data Outliers Corrected" to "TOTAL2 Statistics" page
- **Changed:** Renamed `volume_outliers.html` to `total2_statistics.html`
- **Changed:** Table columns in statistics page now center-aligned
- **Fixed:** ZEC launch day spike (27.8 BTC) now properly smoothed via entry warmup
- **Fixed:** YFI entry spike (3.73 BTC after 10x growth) now properly smoothed
- **Removed:** SMA-based price warmup (replaced by iterative capping)
- **Removed:** Listing-based warmup (replaced by TOTAL2 entry-based warmup)
- **Updated:** TOTAL2_CALCULATION.md with detailed documentation of new algorithm
- **Updated:** config.py comments with ZEC and YFI case studies

**Categories:** Features, Algorithm, Documentation, Filtering

### [2025.12.5] - 2025-12-04

**Visualization, dual currency support, expanded coin coverage, and bug fixes**

- **Added:** Interactive halving cycle charts with Plotly
  - `btc_usd_normalized.html` - BTC/USD across 4 cycles (normalized to halving day)
  - `total2_dual_normalized.html` - TOTAL2 side-by-side (USD left, BTC right)
  - `total2_composition.html` - Interactive date picker to explore TOTAL2 makeup
- **Added:** Charts dashboard page (`site/charts.html`) with navigation
- **Added:** Dual currency support - fetch both BTC and USD prices
- **Added:** `generate-charts` CLI command
- **Added:** Navigation bar in HTML documentation pages
- **Added:** Coins removed due to insufficient historical data are now added to rejected_coins.csv with detailed reason (includes actual start date)
- **Added:** New badge style for "Insufficient historical data" in HTML documentation
- **Changed:** `TOP_N_BY_MARKETCAP_TO_FETCH` increased from 300 to 1000 (includes historical coins like XEM)
- **Changed:** Price files now use pair-based naming: `eth-btc.parquet`, `eth-usd.parquet`
- **Changed:** 120-day SMA warmup period means coins gradually enter TOTAL2 over 120 days
- **Fixed:** BTC is now downloaded (for charts) but excluded from TOTAL2 calculation
- **Fixed:** Recent coins are included in TOTAL2 but marked as "not for individual analysis"
- **Fixed:** End date for price fetching now dynamically set to yesterday instead of being capped at analysis end date (2025-10-21)
- **Fixed:** Coins without price data are now automatically removed from accepted_coins.json after price fetching
- **Fixed:** Project structure in PROJECT_CONTEXT.md now correctly shows docs/ directory location
- **Fixed:** Stablecoin exclusion reason updated from "no price movement vs BTC" to "stable vs fiat, not representative of crypto market trends"
- **Updated:** README with charts section and GitHub Pages setup instructions
- **Updated:** All documentation to reflect new features and configuration
- **Updated:** Documentation to list insufficient historical data as a filtering criterion

**Categories:** Features, Visualization, Documentation, API, Bug Fixes

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

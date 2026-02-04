# Deployment Workflow

**[← Back to README](../README.md)**

---

This document describes how data is fetched, processed, and deployed to GitHub Pages using GitHub Actions workflows.

## Overview

Halvix uses a **fully automated CI pipeline** with:

- **Daily Update** (scheduled) → Runs the full pipeline automatically at 6:00 AM UTC
- Three **manually-triggered workflows** for on-demand runs
- Two **data branches** (orphan) for storing raw and processed data

### Workflows

| Workflow | Trigger | Description |
|----------|---------|-------------|
| **Daily Update** | ⏰ 6:00 AM UTC (scheduled) | Runs full pipeline: fetch → calculate → deploy |
| Fetch Raw Data | 🖱️ Manual | Stores price data in `raw-data` branch |
| Calculate TOTAL2 | 🖱️ Manual | Stores processed index in `processed-data` branch |
| Deploy to GitHub Pages | 🖱️ Manual | Generates charts and deploys |

This approach ensures:
- **Automatic daily updates** with fresh data every morning
- No local data generation required for production
- Clean separation between raw data, processed data, and source code
- Incremental updates (only fetch new data, reuse existing)
- No storage bloat (orphan branches with single commit)
- Lightweight git history (no data files in main branch)

## Architecture Diagram

```
┌────────────────────────────────────────────────────────────────────┐
│                        Halvix CI Pipeline                          │
│          ⏰ Daily Update (6:00 AM UTC) or 🖱️ Manual Trigger        │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  STEP 1: Fetch Raw Data                                            │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │ • Runs: list-coins + fetch-prices                          │    │
│  │ • Pushes to: raw-data branch (orphan)                      │    │
│  └────────────────────────────────────────────────────────────┘    │
│                              │                                     │
│                              ▼                                     │
│  ┌─────────────────────────────────────────────┐                   │
│  │          raw-data branch (orphan)           │                   │
│  │  • raw/prices/*.parquet (price data)        │                   │
│  │  • cache/top_coins_*.json (coin list cache) │                   │
│  │  • processed/ (coins list, metadata, etc.)  │                   │
│  │  • site/data_status.html                    │                   │
│  └─────────────────────────────────────────────┘                   │
│                              │                                     │
│                              ▼                                     │
│  STEP 2: Calculate TOTAL2                                          │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │ • Pulls: raw-data branch                                   │    │
│  │ • Runs: calculate-total2 --index-type total2b              │    │
│  │ • Pushes to: processed-data branch (orphan)                │    │
│  └────────────────────────────────────────────────────────────┘    │
│                              │                                     │
│                              ▼                                     │
│  ┌───────────────────────────────────────────────┐                 │
│  │       processed-data branch (orphan)          │                 │
│  │  • processed/total2_index.parquet             │                 │
│  │  • processed/total2_daily_composition.parquet │                 │
│  │  • processed/total2_max_weight_change.json    │                 │
│  └───────────────────────────────────────────────┘                 │
│                              │                                     │
│                              ▼                                     │
│  STEP 3: Deploy to GitHub Pages                                    │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │ • Pulls: raw-data + processed-data branches                │    │
│  │ • Runs: generate-cycle-charts, analyze-patterns            │    │
│  │ • Deploys: site/ → GitHub Pages (artifact-based)           │    │
│  └────────────────────────────────────────────────────────────┘    │
│                              │                                     │
│                              ▼                                     │
│  ┌─────────────────────────────────────────────┐                   │
│  │           GitHub Pages                      │                   │
│  │  https://yohplala.github.io/halvix/         │                   │
│  │  (Last updated timestamp shown in footer)   │                   │
│  └─────────────────────────────────────────────┘                   │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

## Branch Structure

### `main` branch
Source code only. No data files (html, parquet, json, csv are gitignored).

### `raw-data` branch (orphan)
Contains raw price data fetched from CryptoCompare API:

| Path | Description |
|------|-------------|
| `raw/prices/*.parquet` | Daily OHLCV price data for each coin |
| `cache/top_coins_*.json` | Cached coin list from market cap API |
| `processed/coins_to_download.json` | List of coins selected for download (with `has_usd_data` flag) |
| `processed/download_skipped.csv` | Coins filtered out (stablecoins, wrapped, etc.) |
| `processed/download_failed.csv` | Coins that failed to download (no BTC pair) |
| `processed/no_usd_data.csv` | Coins without USD data from API (before filtering) |
| `processed/fetch_metadata.json` | Fetch statistics (counts, timestamp) |
| `site/data_status.html` | Generated data status page |

### `processed-data` branch (orphan)
Contains calculated TOTAL2 index data:

| Path | Description |
|------|-------------|
| `processed/total2_index.parquet` | TOTAL2 index time series |
| `processed/total2_daily_composition.parquet` | Daily coin composition |
| `processed/total2_max_weight_change.json` | Statistics and metadata |

### Why orphan branches?
- **No history**: Each push uses `--amend` to keep only the latest commit
- **No bloat**: Avoids accumulating gigabytes of binary file history
- **Clean separation**: Data is isolated from source code history

## Workflows

### Daily Update (`daily-update.yml`) ⭐ Recommended

The **Daily Update** workflow runs the complete pipeline automatically every day at **6:00 AM UTC**.

**Trigger**: Scheduled (cron: `0 6 * * *`) + Manual (workflow_dispatch)

**What it does**:
1. **Fetch Data**: Fetches latest coin list and price data
2. **Calculate TOTAL2**: Computes the TOTAL2b index
3. **Deploy**: Generates charts and deploys to GitHub Pages

**Inputs** (manual trigger only):
| Input | Description | Default |
|-------|-------------|---------|
| `skip_fetch` | Skip fetching new data (use existing raw-data) | false |

**To manually trigger**:
1. Go to GitHub → Actions → "Daily Update"
2. Click "Run workflow"
3. Click "Run workflow" button

> **Note**: The daily scheduled run uses `total2b` index type. Use the individual manual workflows if you need different options.

---

### Individual Workflows (Manual)

The individual workflows are available for on-demand runs with custom options.

#### 1. Fetch Raw Data (`fetch-raw-data.yml`)

Fetches cryptocurrency price data from CryptoCompare API.

**Trigger**: Manual only (workflow_dispatch)

**Inputs**:
| Input | Description | Default |
|-------|-------------|---------|
| `limit` | Limit number of coins to fetch | (all) |

**What it does**:
1. Checks out `main` branch (source code)
2. Checks out `raw-data` branch (existing data)
3. Runs `poetry run python -m main list-coins`
4. Runs `poetry run python -m main fetch-prices`
5. Force-pushes updated data to `raw-data` branch

**To run**:
1. Go to GitHub → Actions → "Fetch Raw Data"
2. Click "Run workflow"
3. Optionally set a coin limit
4. Click "Run workflow" button

#### 2. Calculate TOTAL2 (`calculate-total2.yml`)

Calculates the TOTAL2/TOTAL2b market index from cached price data.

**Trigger**: Manual only (workflow_dispatch)

**Inputs**:
| Input | Description | Default |
|-------|-------------|---------|
| `index_type` | Index algorithm (total2 or total2b) | total2b |

**What it does**:
1. Checks out `main` branch (source code)
2. Checks out `raw-data` branch (price data)
3. Checks out `processed-data` branch (existing processed data)
4. Runs `poetry run python -m main calculate-total2 --index-type <type>`
5. Force-pushes updated data to `processed-data` branch

**To run**:
1. Go to GitHub → Actions → "Calculate TOTAL2"
2. Click "Run workflow"
3. Select index type (total2b recommended)
4. Click "Run workflow" button

#### 3. Deploy to GitHub Pages (`pages.yml`)

Generates charts and deploys to GitHub Pages.

**Trigger**: Manual only (workflow_dispatch)

**What it does**:
1. Checks out `main` branch (source code)
2. Checks out `raw-data` branch (for BTC-USD prices)
3. Checks out `processed-data` branch (for TOTAL2 data)
4. Runs `poetry run python -m main generate-cycle-charts`
5. Runs `poetry run python -m main analyze-patterns`
5. Uploads `site/` directory as GitHub Pages artifact
6. Deploys to GitHub Pages

**To run**:
1. Go to GitHub → Actions → "Deploy to GitHub Pages"
2. Click "Run workflow"
3. Click "Run workflow" button

## Generated Files

Charts are generated in CI and deployed directly to GitHub Pages. They are **not** stored in the git repository.

### Charts Directory (`site/charts/`)

| File | Description |
|------|-------------|
| `btc_charts.html` | Combined BTC chart with normalized and absolute prices |
| `total2_charts.html` | Combined TOTAL2 chart (USD normalized + BTC absolute) |
| `total2_composition.html` | Redirect to latest month |
| `total2_composition_YYYY_MM.html` | Monthly composition pages |

### Monthly Composition Pages

The TOTAL2 composition viewer is split into **monthly pages** to keep file sizes manageable:

```
site/charts/
├── total2_composition.html          ← Redirect to latest month
├── total2_composition_2013_09.html  ← September 2013
├── total2_composition_2013_10.html  ← October 2013
├── ...
└── total2_composition_2025_12.html  ← December 2025
```

**Why monthly pages?**
- Each page contains JSON data for all dates in that month
- Full history would create a very large single file (10+ years of daily data)
- Monthly splitting keeps individual pages fast to load (~50-100KB each)
- Navigation between months is provided in the page header

## Quick Reference

### Automatic Daily Updates

The pipeline runs automatically every day at **6:00 AM UTC** via the "Daily Update" workflow.

All generated pages display a "Last updated" timestamp in the footer.

### Manual Full Pipeline (from GitHub UI)

**Option A**: Run the unified "Daily Update" workflow (recommended)
```
Run "Daily Update" workflow
   └── Fetches prices → Calculates index → Deploys to GitHub Pages
```

**Option B**: Run individual workflows
```
1. Run "Fetch Raw Data" workflow
   └── Fetches prices → raw-data branch

2. Run "Calculate TOTAL2" workflow
   └── Calculates index → processed-data branch

3. Run "Deploy to GitHub Pages" workflow
   └── Generates charts → GitHub Pages
```

### Local Development

You can run commands locally for testing. Local data files are gitignored.

```bash
# Fetch data locally
poetry run python -m main list-coins
poetry run python -m main fetch-prices

# Calculate TOTAL2 locally
poetry run python -m main calculate-total2

# Generate charts locally
poetry run python -m main generate-cycle-charts
poetry run python -m main analyze-patterns
```

Local data is stored in:
- `data/` directory (parquet, json, csv files)
- `site/` directory (generated html files)

All these files are gitignored and not committed to the repository.

### View Live Charts

- **Main Dashboard**: https://yohplala.github.io/halvix/
- **BTC Charts**: https://yohplala.github.io/halvix/charts/btc_charts.html
- **TOTAL2 Charts**: https://yohplala.github.io/halvix/charts/total2_charts.html
- **TOTAL2 Composition**: https://yohplala.github.io/halvix/charts/total2_composition.html

## Troubleshooting

### "raw-data branch does not exist" error

Run the "Fetch Raw Data" workflow first to initialize the branch.

### "processed-data branch does not exist" error

Run the "Calculate TOTAL2" workflow first. This requires `raw-data` to exist.

### Charts not updating on GitHub Pages

1. Verify the "Deploy to GitHub Pages" workflow completed successfully
2. Check GitHub Actions tab for errors
3. Clear browser cache and hard refresh (Ctrl+Shift+R)

### Workflow fails with permissions error

Ensure the repository has GitHub Actions permissions set to "Read and write":
1. Go to Settings → Actions → General
2. Under "Workflow permissions", select "Read and write permissions"
3. Save

### Data seems outdated

All generated pages display a **"Last updated"** timestamp in the footer.

To see when data branches were last updated:
1. Go to the `raw-data` or `processed-data` branch on GitHub
2. Check the README.md which shows the last update timestamp

The Daily Update workflow runs automatically at 6:00 AM UTC. If data appears stale:
1. Check the GitHub Actions tab for failed runs
2. Manually trigger the "Daily Update" workflow

---

*Last updated: 2026-02-04*

---

**[← Back to README](../README.md)**

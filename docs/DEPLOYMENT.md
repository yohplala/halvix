# Deployment Workflow

This document describes how data is fetched, processed, and deployed to GitHub Pages using GitHub Actions workflows.

---

**[← Back to README](../README.md)**

---

## Overview

Halvix uses a **fully automated CI pipeline** with three manually-triggered workflows and two data branches:

1. **Fetch Raw Data** → Stores price data in `raw-data` branch
2. **Calculate TOTAL2** → Stores processed index in `processed-data` branch
3. **Deploy to GitHub Pages** → Generates charts and deploys

This approach ensures:
- No local data generation required for production
- Clean separation between raw data, processed data, and source code
- Incremental updates (only fetch new data, reuse existing)
- No storage bloat (orphan branches with single commit)
- Lightweight git history (no data files in main branch)

## Architecture Diagram

```
┌────────────────────────────────────────────────────────────────────┐
│                        Halvix CI Pipeline                          │
│                    (All workflows manually triggered)              │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  WORKFLOW 1: Fetch Raw Data                                        │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │ • Manual trigger from GitHub Actions UI                    │    │
│  │ • Runs: list-coins + fetch-prices                          │    │
│  │ • Pushes to: raw-data branch (orphan)                      │    │
│  └────────────────────────────────────────────────────────────┘    │
│                              │                                     │
│                              ▼                                     │
│  ┌─────────────────────────────────────────────┐                   │
│  │          raw-data branch (orphan)           │                   │
│  │  • raw/prices/*.parquet (price data)        │                   │
│  │  • cache/top_coins_1000.json (coin list)    │                   │
│  │  • processed/coins_to_download.json         │                   │
│  │  • processed/download_skipped.csv           │                   │
│  └─────────────────────────────────────────────┘                   │
│                              │                                     │
│                              ▼                                     │
│  WORKFLOW 2: Calculate TOTAL2                                      │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │ • Manual trigger from GitHub Actions UI                    │    │
│  │ • Pulls: raw-data branch                                   │    │
│  │ • Runs: calculate-total2                                   │    │
│  │ • Pushes to: processed-data branch (orphan)                │    │
│  └────────────────────────────────────────────────────────────┘    │
│                              │                                     │
│                              ▼                                     │
│  ┌─────────────────────────────────────────────┐                   │
│  │       processed-data branch (orphan)        │                   │
│  │  • processed/total2_index.parquet           │                   │
│  │  • processed/total2_daily_composition.parquet│                  │
│  │  • processed/total2_max_weight_change.json  │                   │
│  └─────────────────────────────────────────────┘                   │
│                              │                                     │
│                              ▼                                     │
│  WORKFLOW 3: Deploy to GitHub Pages                                │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │ • Manual trigger from GitHub Actions UI                    │    │
│  │ • Pulls: raw-data + processed-data branches                │    │
│  │ • Runs: generate-charts                                    │    │
│  │ • Deploys: site/ → GitHub Pages (artifact-based)           │    │
│  └────────────────────────────────────────────────────────────┘    │
│                              │                                     │
│                              ▼                                     │
│  ┌─────────────────────────────────────────────┐                   │
│  │           GitHub Pages                      │                   │
│  │  https://yohplala.github.io/halvix/         │                   │
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
| `cache/top_coins_1000.json` | Cached top 1000 coins by market cap |
| `processed/coins_to_download.json` | List of coins selected for download |
| `processed/download_skipped.csv` | Coins skipped during download |

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

All three workflows are **manually triggered** from the GitHub Actions UI.

### 1. Fetch Raw Data (`fetch-raw-data.yml`)

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

### 2. Calculate TOTAL2 (`calculate-total2.yml`)

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

### 3. Deploy to GitHub Pages (`pages.yml`)

Generates charts and deploys to GitHub Pages.

**Trigger**: Manual only (workflow_dispatch)

**What it does**:
1. Checks out `main` branch (source code)
2. Checks out `raw-data` branch (for BTC-USD prices)
3. Checks out `processed-data` branch (for TOTAL2 data)
4. Runs `poetry run python -m main generate-charts`
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

### Full Pipeline (from GitHub UI)

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
poetry run python -m main generate-charts
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

The orphan branches only keep the latest commit. To see when data was last updated:
1. Go to the `raw-data` or `processed-data` branch on GitHub
2. Check the README.md which shows the last update timestamp

---

*See also: [TUTORIAL.md](TUTORIAL.md) for step-by-step usage guide*

*[← Back to README](../README.md)*

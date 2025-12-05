# Deployment Workflow

This document describes how charts and visualization pages are generated and deployed to GitHub Pages.

---

**[← Back to README](../README.md)**

---

## Overview

Halvix uses a **local generation → git push → CI deploy** workflow:

1. **Charts are generated locally** on your machine using your local data
2. **HTML files are committed** to git and pushed to the `main` branch
3. **CI automatically deploys** the `site/` directory to GitHub Pages

This approach ensures:
- Full control over the data used for chart generation
- No API credentials needed in CI
- Reproducible builds from committed HTML files
- Fast CI deployments (just static file hosting)

## Workflow Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Charts Deployment Workflow                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  LOCAL MACHINE                                                       │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │ 1. Fetch data & calculate TOTAL2                           │     │
│  │    poetry run python -m main list-coins                    │     │
│  │    poetry run python -m main fetch-prices                  │     │
│  │    poetry run python -m main calculate-total2              │     │
│  │                                                             │     │
│  │ 2. Generate charts                                          │     │
│  │    poetry run python -m main generate-charts               │     │
│  │    → Creates HTML files in site/charts/                    │     │
│  │                                                             │     │
│  │ 3. Commit and push                                          │     │
│  │    git add site/                                            │     │
│  │    git commit -m "Update charts"                           │     │
│  │    git push                                                 │     │
│  └────────────────────────────────────────────────────────────┘     │
│                              │                                       │
│                              ▼                                       │
│  GITHUB CI (pages.yml)                                               │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │ 4. Checkout main branch                                     │     │
│  │ 5. Upload site/ directory as artifact                      │     │
│  │ 6. Deploy to GitHub Pages                                   │     │
│  │    → https://yohplala.github.io/halvix/                    │     │
│  └────────────────────────────────────────────────────────────┘     │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Generated Files

### Charts Directory (`site/charts/`)

| File | Description |
|------|-------------|
| `btc_usd_normalized.html` | BTC/USD normalized to halving day |
| `btc_halving_cycles.html` | BTC/USD absolute values |
| `total2_dual_normalized.html` | TOTAL2 vs USD and BTC side-by-side |
| `total2_halving_cycles.html` | TOTAL2/BTC absolute values |
| `total2_composition.html` | Redirect to latest month |
| `total2_composition_YYYY_MM.html` | Monthly composition pages |

### Monthly Composition Pages

The TOTAL2 composition viewer is split into **monthly pages** to keep file sizes manageable:

```
site/charts/
├── total2_composition.html          ← Redirect to latest month
├── total2_composition_2014_09.html  ← September 2014
├── total2_composition_2014_10.html  ← October 2014
├── ...
├── total2_composition_2025_11.html  ← November 2025
└── total2_composition_2025_12.html  ← December 2025
```

**Why monthly pages?**
- Each page contains JSON data for all dates in that month
- Full history would create a very large single file (10+ years of daily data)
- Monthly splitting keeps individual pages fast to load (~50-100KB each)
- Navigation between months is provided in the page header

### Composition Viewer Features

Each composition page includes:
- **Date dropdown** with cycle day info (e.g., "2024-04-01 (C4: Day -18)")
- **Month navigation** to browse across years
- **Coin table** showing rank, symbol, weight, price, and volume
- **Statistics** showing total coins and total volume

The cycle day info helps navigate between TOTAL2 evolution charts (which use day numbers) and specific dates in the composition viewer.

## CI Workflow

The GitHub Actions workflow (`.github/workflows/pages.yml`) is triggered on:
- Push to `main` branch (when `site/` files change)
- Manual trigger via `workflow_dispatch`

```yaml
on:
  push:
    branches: ["main"]
    paths:
      - "site/**"
      - ".github/workflows/pages.yml"
  workflow_dispatch:
```

The workflow simply deploys the `site/` directory as-is, without any chart generation in CI.

## Quick Reference

### Generate and Deploy Charts

```bash
# 1. Update data (if needed)
poetry run python -m main list-coins
poetry run python -m main fetch-prices
poetry run python -m main calculate-total2

# 2. Generate all charts
poetry run python -m main generate-charts

# 3. Commit and push
git add site/
git commit -m "Update charts with latest data"
git push
```

### View Live Charts

- **Charts Dashboard**: https://yohplala.github.io/halvix/charts.html
- **Data Status**: https://yohplala.github.io/halvix/index.html
- **TOTAL2 Composition**: https://yohplala.github.io/halvix/charts/total2_composition.html

## Troubleshooting

### Charts not updating on GitHub Pages

1. Check that `site/` files are committed: `git status site/`
2. Verify the CI workflow ran: Check GitHub Actions tab
3. Clear browser cache and hard refresh

### Composition viewer shows placeholder

The redirect file `site/charts/total2_composition.html` must be committed (not gitignored). Check:
```bash
git ls-files site/charts/total2_composition.html
```

If empty, the file is gitignored. Remove it from `.gitignore` and commit.

---

*See also: [TUTORIAL.md](TUTORIAL.md) for step-by-step usage guide*

*[← Back to README](../README.md)*

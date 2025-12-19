# Contributing to Halvix

## Development Setup

This project uses **Poetry** for dependency management.

### Prerequisites

- Python 3.13+
- Poetry (`pip install poetry` or see [Poetry installation](https://python-poetry.org/docs/#installation))

### Installation

```bash
# Clone the repository
git clone https://github.com/yohplala/halvix.git
cd halvix

# Install dependencies (creates virtual environment automatically)
poetry install

# Activate the virtual environment (optional - poetry run works without this)
poetry shell
```

### Running Commands

Always use `poetry run` to ensure you're using the virtual environment:

```bash
# Run CLI commands
poetry run python -m main list-coins
poetry run python -m main fetch-prices
poetry run python -m main calculate-total2
poetry run python -m main generate-charts

# Run tests
poetry run pytest

# Run linter
poetry run ruff check src/ tests/

# Run formatter
poetry run black src/ tests/

# Run pre-commit hooks
poetry run pre-commit run --all-files
```

### Project Structure

```
halvix/
├── src/                    # Source code
│   ├── api/               # CryptoCompare API client
│   ├── analysis/          # Token filtering
│   ├── data/              # Fetcher, cache, processors
│   ├── visualization/     # Chart generation
│   ├── utils/             # Logging utilities
│   ├── config.py          # Configuration constants
│   └── main.py            # CLI entry point
├── tests/                  # Pytest tests
├── docs/                   # Documentation (markdown)
├── site/                   # Generated HTML (GitHub Pages)
└── pyproject.toml          # Poetry configuration
```

### Code Style

- **Formatter**: Black (line length: 100)
- **Linter**: Ruff
- **Python**: 3.13+

Pre-commit hooks are configured to run Black and Ruff automatically.

### Testing

```bash
# Run all tests
poetry run pytest

# Run with coverage
poetry run pytest --cov=src

# Run specific test file
poetry run pytest tests/test_processor.py

# Run specific test
poetry run pytest tests/test_processor.py::TestProcessorFactory::test_get_total2b_processor
```

### Adding Dependencies

```bash
# Add a runtime dependency
poetry add <package>

# Add a development dependency
poetry add --group dev <package>
```

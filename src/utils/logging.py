"""
Logging configuration for Halvix.

Provides consistent logging across all modules with configurable verbosity.
"""

import logging
import sys
from pathlib import Path

# Default log format
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Shorter format for console
CONSOLE_FORMAT = "%(levelname)-8s | %(message)s"

# Module-level logger cache
_loggers: dict[str, logging.Logger] = {}


def setup_logging(
    level: int = logging.INFO,
    log_file: Path | None = None,
    verbose: bool = False,
) -> None:
    """
    Configure logging for the application.

    Args:
        level: Logging level (default: INFO)
        log_file: Optional path to log file
        verbose: If True, use DEBUG level and detailed format
    """
    if verbose:
        level = logging.DEBUG
        console_format = LOG_FORMAT
    else:
        console_format = CONSOLE_FORMAT

    # Configure root logger
    root_logger = logging.getLogger("halvix")
    root_logger.setLevel(level)

    # Remove existing handlers
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter(console_format, LOG_DATE_FORMAT))
    root_logger.addHandler(console_handler)

    # File handler (optional)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)  # Always log everything to file
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
        root_logger.addHandler(file_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger for a module.

    Usage:
        from utils.logging import get_logger
        logger = get_logger(__name__)

        logger.info("Processing coin: %s", coin_id)
        logger.debug("Detailed data: %s", data)
        logger.warning("Price difference %.2f%% exceeds threshold", diff)
        logger.error("Failed to fetch: %s", error)

    Args:
        name: Module name (usually __name__)

    Returns:
        Logger instance
    """
    # Use halvix namespace for all loggers
    if not name.startswith("halvix"):
        name = f"halvix.{name}"

    if name not in _loggers:
        _loggers[name] = logging.getLogger(name)

    return _loggers[name]

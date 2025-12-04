"""
Configuration constants for the Halvix project.

Halvix - Cryptocurrency price analysis relative to Bitcoin halving cycles.
"""

from datetime import date, timedelta
from pathlib import Path

# =============================================================================
# Project Paths
# =============================================================================

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PRICES_DIR = RAW_DATA_DIR / "prices"
PROCESSED_DIR = DATA_DIR / "processed"
CACHE_DIR = DATA_DIR / "cache"
OUTPUT_DIR = PROJECT_ROOT / "output"
CHARTS_DIR = OUTPUT_DIR / "charts"
INDIVIDUAL_CHARTS_DIR = CHARTS_DIR / "individual"
REPORTS_DIR = OUTPUT_DIR / "reports"

# =============================================================================
# Bitcoin Halving Dates
# =============================================================================

HALVING_DATES: list[date] = [
    date(2012, 11, 28),  # 1st halving
    date(2016, 7, 9),  # 2nd halving
    date(2020, 5, 11),  # 3rd halving
    date(2024, 4, 19),  # 4th halving
]

# Projected 5th halving (approximately 4 years after 4th)
PROJECTED_5TH_HALVING = date(2028, 3, 15)

# =============================================================================
# Time Window Configuration
# =============================================================================

DAYS_BEFORE_HALVING = 550
DAYS_AFTER_HALVING = 880  # Extended to capture bear market phase following bull run
TOTAL_WINDOW_DAYS = DAYS_BEFORE_HALVING + DAYS_AFTER_HALVING  # 1430 days


def get_cycle_window(halving_date: date) -> tuple[date, date]:
    """
    Calculate the time window for a halving cycle.

    Args:
        halving_date: The date of the Bitcoin halving

    Returns:
        Tuple of (start_date, end_date) for the cycle window
    """
    start = halving_date - timedelta(days=DAYS_BEFORE_HALVING)
    end = halving_date + timedelta(days=DAYS_AFTER_HALVING)
    return (start, end)


def get_all_cycle_windows() -> list[tuple[int, date, date, date]]:
    """
    Get all halving cycle windows with their metadata.

    Returns:
        List of tuples: (cycle_number, start_date, halving_date, end_date)
    """
    windows = []
    for i, halving_date in enumerate(HALVING_DATES, start=1):
        start, end = get_cycle_window(halving_date)
        windows.append((i, start, halving_date, end))
    return windows


# Pre-computed cycle windows for reference
CYCLE_WINDOWS = get_all_cycle_windows()

# =============================================================================
# Linear Regression Configuration
# =============================================================================

REGRESSION_START_DATE = date(2023, 11, 1)

# Minimum number of data points required for regression
MIN_REGRESSION_POINTS = 30


def get_regression_end_date() -> date:
    """
    Get the regression end date.

    Returns today's date at call time to handle applications
    running across midnight.

    Returns:
        Current date
    """
    return date.today()


# =============================================================================
# Data Filtering Configuration
# =============================================================================

# Minimum date for coin data availability
# Only process coins with data available before this date
MIN_DATA_DATE = date(2024, 1, 10)

# Number of top coins to fetch
TOP_N_COINS = 300

# Number of top coins to use for TOTAL2 calculation
TOP_N_FOR_TOTAL2 = 50

# Number of top performers to show in summary chart
TOP_N_SUMMARY = 10

# =============================================================================
# Stablecoin Exclusion List
# =============================================================================

# These coins are excluded from ALL analysis (halving cycles and TOTAL2)
# Stablecoins have no meaningful price movement relative to BTC
# Use lowercase symbols for matching
EXCLUDED_STABLECOINS = {
    # Major USD stablecoins (by symbol)
    "usdt",
    "usdc",
    "dai",
    "usds",
    "usde",
    "susds",
    "pyusd",
    "susde",
    "usd1",
    "usdf",
    "usdtb",
    "bfusd",
    "rlusd",
    "usdg",
    "usyc",
    "fdusd",
    "usdy",
    "usd0",
    "usdd",
    "tusd",
    "gho",
    "usdb",
    "frax",
    "lusd",
    "crvusd",
    "gusd",
    "busd",
    "usdp",
    "susd",
    "nusd",
    # Euro stablecoins
    "eurs",
    "eurt",
    "eurc",
    "ageur",
    # Other stablecoins
    "mim",
    "dola",
}

# =============================================================================
# Wrapped/Staked/Bridged Token Exclusion
# =============================================================================

# Exact symbols to exclude (wrapped, staked, bridged, liquid staking tokens)
# Use lowercase for matching
EXCLUDED_WRAPPED_STAKED_IDS = {
    # Wrapped BTC variants
    "wbtc",
    "tbtc",
    "hbtc",
    "renbtc",
    "sbtc",
    "fbtc",
    "lbtc",
    "solvbtc",
    "clbtc",
    "cbbtc",
    "enzobtc",
    # Wrapped/Staked ETH variants
    "steth",
    "wsteth",
    "weth",
    "wbeth",
    "weeth",
    "reth",
    "cbeth",
    "sfrxeth",
    "meth",
    "lseth",
    "rseth",
    "ezeth",
    "oseth",
    "ethx",
    "eeth",
    "sweth",
    # Aave wrapped tokens
    "aethweth",
    "aethusdc",
    "aethusdt",
    "aethdai",
    "aweth",
    "ausdc",
    "ausdt",
    "adai",
    # Wrapped/Staked SOL variants
    "wsol",
    "jitosol",
    "msol",
    "bnsol",
    # Wrapped BNB
    "wbnb",
}

# Patterns to match in coin ID or name (case-insensitive regex)
EXCLUDED_PATTERNS = [
    # Wrapped tokens
    r"^wrapped-",
    r"^w[a-z]{2,6}$",  # wBTC, wETH, wSOL, wBNB, etc.
    r"-wrapped$",
    r"-wrapped-",
    # Staked tokens
    r"^staked-",
    r"^st[a-z]{2,6}$",  # stETH, stSOL, etc.
    r"-staked$",
    r"-staked-",
    r"liquid.?staking",
    # Bridged tokens
    r"^bridged-",
    r"-bridged$",
    r"-bridged-",
    r"bridge[d]?$",
    # Restaked tokens
    r"restaked",
    r"^rs[a-z]{2,6}$",  # rsETH, etc.
    # Specific protocols for liquid staking
    r"lido",
    r"rocket.?pool",
    r"coinbase.?wrapped",
    r"marinade",
    r"jito.?staked",
    r"ether\.?fi",
    r"swell",
    r"kelp.?dao",
    r"renzo",
    r"stader",
    r"stakewise",
    r"lombard",
    r"solv.?btc",
    r"threshold.?btc",
    # Aave wrapped/deposited tokens
    r"^aave.*weth",
    r"^aave.*eth",
    r"^aeth",  # aETH variants like aETHWETH
]

# =============================================================================
# Allowed Tokens (override exclusions)
# These tokens should NEVER be filtered out despite matching patterns
# Use lowercase symbols for matching
# =============================================================================

ALLOWED_TOKENS = {
    "sui",  # SUI blockchain native token
    "sei",  # SEI blockchain native token
    "stk",  # STK token
    "sand",  # The Sandbox
    "wif",  # dogwifhat meme token
    "xlm",  # Stellar (has 'st' in name but is not staked)
    "stx",  # Stacks (has 'st' prefix but is not staked)
    "storm",  # STORM token
    "snt",  # Status
    "storj",  # STORJ token
    "strax",  # Stratis
    "stpt",  # STP Network
    "strk",  # Starknet
    "wild",  # Wilder World
    "wifi",  # WIFI token
}

# =============================================================================
# CryptoCompare API Configuration
# =============================================================================

# CryptoCompare is the sole data source for Halvix:
# - Top coins by market cap for coin discovery
# - Historical price data with full history (no time limit on free tier)
# - Volume data for TOTAL2 calculation
CRYPTOCOMPARE_BASE_URL = "https://min-api.cryptocompare.com"
CRYPTOCOMPARE_COIN_URL = "https://www.cryptocompare.com/coins"

# Rate limiting (free tier: 10 calls/second, we stay conservative)
CRYPTOCOMPARE_API_CALLS_PER_MINUTE = 30

# Maximum days per request (API limit)
CRYPTOCOMPARE_MAX_DAYS_PER_REQUEST = 2000

# Retry configuration
API_MAX_RETRIES = 5
API_RETRY_MIN_WAIT = 1  # seconds
API_RETRY_MAX_WAIT = 60  # seconds

# Cache expiry (24 hours for coin list data)
CACHE_EXPIRY_SECONDS = 86400

# =============================================================================
# Visualization Configuration
# =============================================================================

COLORS = {
    "coin_candle_up": "#E67E22",  # Dark orange (bullish)
    "coin_candle_down": "#D35400",  # Darker orange (bearish)
    "coin_line": "#E67E22",  # Dark orange for line plots
    "total2_line": "#CCCCCC",  # Light grey
    "regression_line": "#1B4F72",  # Dark blue
    "background": "#FFFFFF",
    "grid": "#F0F0F0",
    "text": "#333333",
}

# Chart dimensions
CHART_WIDTH = 1400
CHART_HEIGHT_PER_CYCLE = 400
CHART_DPI = 150

# Font settings
CHART_FONT_FAMILY = "Arial, sans-serif"
CHART_TITLE_SIZE = 16
CHART_LABEL_SIZE = 12

# =============================================================================
# Half-Monthly Aggregation
# =============================================================================

# Pandas frequency string for semi-monthly resampling
# 'SMS' = Semi-Month Start frequency (1st and 15th)
HALF_MONTHLY_FREQ = "SMS"

# =============================================================================
# Output Files
# =============================================================================

# Coin lists after filtering
ACCEPTED_COINS_JSON = PROCESSED_DIR / "accepted_coins.json"
REJECTED_COINS_CSV = PROCESSED_DIR / "rejected_coins.csv"

# Analysis results
REGRESSION_RESULTS_CSV = PROCESSED_DIR / "regression_results.csv"
TOTAL2_INDEX_FILE = PROCESSED_DIR / "total2_index.parquet"
TOTAL2_COMPOSITION_FILE = PROCESSED_DIR / "total2_daily_composition.parquet"

# =============================================================================
# Data Fetching Configuration
# =============================================================================

# Always use yesterday as end date for price fetching
# Today's data is incomplete (market hasn't closed yet)
USE_YESTERDAY_AS_END_DATE = True

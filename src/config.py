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
# BTC Cycle Peaks and Bottoms (verified from CryptoCompare data)
# =============================================================================
# These dates mark significant cycle extremes in BTC/USD price history.
# Used for visualization to show where bottoms and peaks occurred relative to halvings.

# BTC cycle peaks (bull market tops) - verified with +/-30 day accuracy
# Note: Last peak is for current cycle and may change
BTC_CYCLE_PEAKS: list[date] = [
    # date(2011, 6, 8),  # Pre halving 1 peak: $29.60
    # date(2013, 12, 4),  # Post halving 1 peak: $1,237.55
    date(2017, 12, 16),  # Post halving 2 peak: $19,345.49
    date(2021, 11, 8),  # Post halving 3 peak: $67,549.14
    date(2025, 10, 6),  # Post halving 4 peak (projected/current)
]

# BTC cycle bottoms (bear market lows) - verified with +/-30 day accuracy
# These mark the lowest points before the next bull run
BTC_CYCLE_BOTTOMS: list[date] = [
    # date(2011, 11, 18),  # Pre halving 1 bottom: $2.05
    date(2015, 1, 14),  # Pre halving 2 bottom: $164.92
    date(2018, 12, 15),  # Pre halving 3 bottom: $3,232.51
    date(2022, 11, 21),  # Pre halving 4 bottom: $15,760.19
]

# =============================================================================
# Time Window Configuration
# =============================================================================

DAYS_BEFORE_HALVING = 550
DAYS_AFTER_HALVING = 950  # Extended to capture bear market phase following bull run
TOTAL_WINDOW_DAYS = DAYS_BEFORE_HALVING + DAYS_AFTER_HALVING  # 1500 days


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
# Data Filtering Configuration
# =============================================================================

# Number of top coins to fetch (increased to 1200 to include historical coins like XEM)
TOP_N_BY_MARKETCAP_TO_FETCH = 1200

# Number of top coins to use for TOTAL2 calculation
TOP_N_BY_VOLUME_FOR_TOTAL2 = 30

# Volume smoothing window for TOTAL2 calculation (days)
# Uses Simple Moving Average to smooth out daily volume spikes
# 120 days (~4 months) provides stable ranking and reduces max weight change
VOLUME_SMA_WINDOW = 120

# TOTAL2 Entry Warmup: Actual price capping when a coin first enters TOTAL2
#
# When a coin first enters TOTAL2 (TOP30 by volume), its price may cause
# artificial spikes in the index. This warmup CAPS prices (not just monitors) by:
# 1. Using raw TOTAL2 value as the baseline price at entry (market level)
# 2. CAPPING daily price changes to MAX_INCREASE / MAX_DECREASE during warmup
# 3. Each day uses the previous day's CAPPED price as reference
#
# This handles TWO types of cases:
#
# CASE 1 - ZEC (2016-10-28): Listed AND entered TOTAL2 on same day
#   - Day 0 (before): Use corrected TOTAL2 (~0.01 BTC) as baseline
#   - Day 1: Actual 27.8 BTC → Cap at 1.7x = 0.017 BTC
#   - Day 2: Actual 2.79 BTC → Cap at 1.7x = 0.029 BTC
#   - ... converges to actual price (~1-2 BTC) in ~9 days
#
# CASE 2 - YFI (2020-09-14): Entered TOTAL2 45 days after listing
#   - Day 0 (before): Use corrected TOTAL2 (~0.012 BTC) as baseline
#   - Day 1: Actual 3.73 BTC → Cap at 1.7x = 0.020 BTC
#   - Day 2: Actual 3.27 BTC → Cap at 1.7x = 0.034 BTC
#   - ... converges to actual price (~3.7 BTC) in ~10 days
#
# Both cases gradually ramp up from market level, preventing artificial TOTAL2 spikes.
TOTAL2_ENTRY_MAX_INCREASE = 1.7  # Max 1.7x (70% gain) per day during warmup
TOTAL2_ENTRY_MAX_DECREASE = 0.5  # Min 0.5x (50% loss) per day during warmup
TOTAL2_ENTRY_WARMUP_PERIOD_DAYS = 21  # How many days entry warmup applies (3 weeks)

# =============================================================================
# TOTAL2b New Coin Entry Settings
# =============================================================================
# TOTAL2b uses a different approach: freeze period + price scaling at entry
#
# Freeze Period: Coins must wait this many days after first appearing in
# CryptoCompare before they can join the index. This ensures stable price
# data and avoids launch-day volatility.
#
# Price Scaling: When a coin enters TOTAL2b (after freeze period + reaching
# TOP30), its price is scaled by TOTAL2b_d-1/COIN_PRICE_d (where COIN_PRICE_d
# is the coin price at entry day d). This preserves day-over-day price changes.
TOTAL2B_ENTRY_FREEZE_PERIOD_DAYS = 21  # Days to wait before coin can join (3 weeks)
TOTAL2B_MIN_COINS_FOR_SCALING = 30  # Only apply scaling after index has this many coins

# Symbol Replacement Detection: CryptoCompare sometimes reuses symbols for different
# tokens (e.g., old worthless "HYPE" replaced by Hyperliquid "HYPE" in Dec 2024,
# or old "OMG" replaced by OmiseGO in July 2017 with a 633x jump).
# When a coin's price jumps by more than this factor in a single day, we treat it
# as a symbol replacement and reset the first_seen date to after the jump.
# This prevents old scaling factors from being incorrectly applied to new tokens.
# Note: 30x is used because a 30x daily gain is extremely unusual even for volatile altcoins.
TOTAL2B_SYMBOL_REPLACEMENT_THRESHOLD = 30  # 30x price change indicates symbol swap

# Quote currencies for price data
# Prices are fetched against each of these currencies
QUOTE_CURRENCIES = ["BTC", "USD"]

# Default quote currency for analysis
DEFAULT_QUOTE_CURRENCY = "BTC"

# =============================================================================
# Stablecoin Exclusion List
# =============================================================================

# These coins are excluded from ALL analysis (halving cycles and TOTAL2)
# Stablecoins are stable vs fiat, not representative of crypto market trends
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
    "eurcv",  # Euro CoinVertible
    "eurq",  # Quantoz EURQ
    "eurr",  # Euro stablecoin
    "ageur",
    # Other stablecoins
    "mim",
    "dola",
    "ausd",  # Acala USD (Polkadot stablecoin)
    # Algorithmic stablecoins (depegged but originally USD-pegged)
    "ust",  # TerraUSD (collapsed May 2022)
    "ustc",  # TerraUSD Classic (post-collapse renamed UST)
    # Note: LUNA/LUNC are NOT excluded - they are not stablecoins themselves,
    # they were the mechanism tokens used to maintain UST's peg
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
    # Governance tokens for staking/bridging/wrapping protocols
    # (not wrapped/staked tokens themselves - they have independent price action)
    "bard",  # Bard governance token
    "dbr",  # Debridger governance token
    "ethfi",  # Ether.fi governance token
    "fxs",  # Frax Share - governance token (FRAX is the stablecoin)
    "ldo",  # Lido DAO governance token
    "mnde",  # Marinade Finance governance token
    "rez",  # Renzo governance token
    "rpl",  # Rocket Pool governance token
    "sd",  # Stader governance token
    "swell",  # Swell Network governance token
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

# Rate limiting: The client uses dynamic rate limiting by checking the
# /stats/rate/limit endpoint to monitor actual quota usage.
# This constant serves as a FALLBACK minimum interval between requests,
# used when rate limit status is unavailable or as a baseline throttle.
# We use a very conservative fallback of 1 call every 5 seconds.
CRYPTOCOMPARE_API_CALLS_PER_MINUTE = 12  # Fallback: 5 seconds between requests

# Maximum days per request (API limit)
CRYPTOCOMPARE_MAX_DAYS_PER_REQUEST = 2000

# Retry configuration
API_MAX_RETRIES = 5
API_RETRY_MIN_WAIT = 1  # seconds
API_RETRY_MAX_WAIT = 60  # seconds

# Cache expiry (24 hours for coin list data)
CACHE_EXPIRY_SECONDS = 86400

# =============================================================================
# Output Files
# =============================================================================

# Coin lists for download phase
# coins_to_download.json - coins that will have price data fetched
# download_skipped.csv - coins that are skipped with reason (stablecoins, wrapped tokens, etc.)
# download_failed.csv - coins that failed to download (no BTC pair on CryptoCompare, etc.)
# no_usd_data.csv - coins returned by API without USD price data (silently skipped)
# fetch_metadata.json - metadata about the fetch operation (counts, timestamp)
COINS_TO_DOWNLOAD_JSON = PROCESSED_DIR / "coins_to_download.json"
DOWNLOAD_SKIPPED_CSV = PROCESSED_DIR / "download_skipped.csv"
DOWNLOAD_FAILED_CSV = PROCESSED_DIR / "download_failed.csv"
NO_USD_DATA_CSV = PROCESSED_DIR / "no_usd_data.csv"
FETCH_METADATA_JSON = PROCESSED_DIR / "fetch_metadata.json"

# Analysis results
TOTAL2_INDEX_FILE = PROCESSED_DIR / "total2_index.parquet"
TOTAL2_COMPOSITION_FILE = PROCESSED_DIR / "total2_daily_composition.parquet"
TOTAL2_MAX_WEIGHT_CHANGE_FILE = PROCESSED_DIR / "total2_max_weight_change.json"

# =============================================================================
# Data Fetching Configuration
# =============================================================================

# Always use yesterday as end date for price fetching.
# Today's data is incomplete (market hasn't closed yet).
# This is a fixed constant - do not change as it ensures data consistency.
USE_YESTERDAY_AS_END_DATE = True

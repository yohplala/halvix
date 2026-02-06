"""
Configuration constants for the Halvix project.

Halvix - Cryptocurrency price analysis relative to Bitcoin halving cycles.
"""

import math
from dataclasses import dataclass
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
PROJECTED_5TH_HALVING = date(2028, 3, 31)

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

# How far before/after halving to include in cycle analysis
DAYS_BEFORE_HALVING = 550
DAYS_AFTER_HALVING = 950  # Extended to capture bear market phase following bull run
TOTAL_WINDOW_DAYS = DAYS_BEFORE_HALVING + DAYS_AFTER_HALVING  # 1500 days

# Expected peak timing: ~550 days after halving (~18 months)
# This is when the bull market typically peaks before the next bear market.
# Note: This equals DAYS_BEFORE_HALVING by design (peak is when next cycle's pre-window starts)
EXPECTED_PEAK_DAYS_AFTER_HALVING = 550


# =============================================================================
# HalvingCycle Value Object
# =============================================================================


@dataclass(frozen=True)
class HalvingCycle:
    """
    Immutable value object representing a Bitcoin halving cycle.

    Encapsulates all data related to a specific halving cycle including dates,
    windows, and associated market extremes. Uses frozen=True to ensure
    immutability as a value object.

    Attributes:
        cycle_num: Cycle number (1-5). Cycle 1 is the 2012 halving.
        halving_date: The date the halving occurred/will occur.
        window_start: Start of analysis window (halving_date - DAYS_BEFORE_HALVING).
        window_end: End of analysis window (halving_date + DAYS_AFTER_HALVING).
        peak_date: Date of the cycle's bull market peak (None if unknown/not yet).
        bottom_date: Date of the pre-halving bear market bottom (None if unknown).
        is_current: Whether this is the current/incomplete cycle.
    """

    cycle_num: int
    halving_date: date
    window_start: date
    window_end: date
    peak_date: date | None = None
    bottom_date: date | None = None
    is_current: bool = False

    @classmethod
    def from_halving_date(
        cls,
        cycle_num: int,
        halving_date: date,
        peak_date: date | None = None,
        bottom_date: date | None = None,
        is_current: bool = False,
    ) -> "HalvingCycle":
        """
        Create a HalvingCycle from a halving date, computing window dates.

        Args:
            cycle_num: Cycle number (1, 2, 3, 4, 5)
            halving_date: The halving date
            peak_date: Optional peak date for this cycle
            bottom_date: Optional bottom date for this cycle
            is_current: Whether this is the current incomplete cycle

        Returns:
            HalvingCycle instance with computed window dates
        """
        window_start = halving_date - timedelta(days=DAYS_BEFORE_HALVING)
        window_end = halving_date + timedelta(days=DAYS_AFTER_HALVING)
        return cls(
            cycle_num=cycle_num,
            halving_date=halving_date,
            window_start=window_start,
            window_end=window_end,
            peak_date=peak_date,
            bottom_date=bottom_date,
            is_current=is_current,
        )

    @property
    def total_days(self) -> int:
        """Total number of days in the cycle window."""
        return (self.window_end - self.window_start).days

    def contains_date(self, dt: date) -> bool:
        """Check if a date falls within this cycle's window."""
        return self.window_start <= dt <= self.window_end

    def days_from_halving(self, dt: date) -> int:
        """Calculate days from halving (negative = before, positive = after)."""
        return (dt - self.halving_date).days


def _build_halving_cycles() -> list[HalvingCycle]:
    """
    Build the list of HalvingCycle objects from configuration data.

    Internal function to create HALVING_CYCLES list at module load time.
    Maps halving dates to their associated peaks and bottoms.
    """
    # Map cycle numbers to their peak/bottom dates
    # Indices align: bottoms[0] is before HALVING_DATES[1] (cycle 2), etc.
    peak_by_cycle: dict[int, date | None] = {}
    bottom_by_cycle: dict[int, date | None] = {}

    # BTC_CYCLE_PEAKS indices: 0 = cycle 2, 1 = cycle 3, 2 = cycle 4
    for i, peak in enumerate(BTC_CYCLE_PEAKS):
        peak_by_cycle[i + 2] = peak

    # BTC_CYCLE_BOTTOMS indices: 0 = before cycle 2, 1 = before cycle 3, 2 = before cycle 4
    for i, bottom in enumerate(BTC_CYCLE_BOTTOMS):
        bottom_by_cycle[i + 2] = bottom

    cycles = []
    for i, halving in enumerate(HALVING_DATES):
        cycle_num = i + 1
        is_current = cycle_num == len(HALVING_DATES)  # Last halving is current

        cycles.append(
            HalvingCycle.from_halving_date(
                cycle_num=cycle_num,
                halving_date=halving,
                peak_date=peak_by_cycle.get(cycle_num),
                bottom_date=bottom_by_cycle.get(cycle_num),
                is_current=is_current,
            )
        )

    return cycles


# Pre-built list of all halving cycles
HALVING_CYCLES: list[HalvingCycle] = _build_halving_cycles()


def get_cycle(cycle_num: int) -> HalvingCycle | None:
    """
    Get a specific halving cycle by number.

    Args:
        cycle_num: Cycle number (1-5)

    Returns:
        HalvingCycle if found, None otherwise
    """
    for cycle in HALVING_CYCLES:
        if cycle.cycle_num == cycle_num:
            return cycle
    return None


def get_cycle_for_date(dt: date) -> HalvingCycle | None:
    """
    Find which halving cycle a date falls within.

    Args:
        dt: The date to check

    Returns:
        HalvingCycle containing the date, or None if outside all windows
    """
    for cycle in HALVING_CYCLES:
        if cycle.contains_date(dt):
            return cycle
    return None


# =============================================================================
# Legacy Cycle Window Functions (for backward compatibility)
# =============================================================================
# These functions are kept for backward compatibility but delegate to HalvingCycle.


def get_cycle_window(halving_date: date) -> tuple[date, date]:
    """
    Calculate the time window for a halving cycle.

    Deprecated: Use HalvingCycle.from_halving_date() instead.

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

    Deprecated: Use HALVING_CYCLES list instead.

    Returns:
        List of tuples: (cycle_number, start_date, halving_date, end_date)
    """
    return [
        (cycle.cycle_num, cycle.window_start, cycle.halving_date, cycle.window_end)
        for cycle in HALVING_CYCLES
    ]


# Pre-computed cycle windows for reference (legacy format)
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

# TOTAL2 series smoothing parameters (caps extreme day-over-day aggregate index movements)
TOTAL2_SERIES_MAX_INCREASE = 3.0  # Cap TOTAL2 increase at 3x per day (200% gain)
TOTAL2_SERIES_MAX_DECREASE = 0.35  # Cap TOTAL2 decrease at 0.35x per day (65% loss)

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
TOTAL2_MIN_COINS_FOR_INDEX = 3  # Minimum coins required to calculate index for a day

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

# =============================================================================
# Pattern Analysis Configuration
# =============================================================================

# Maximum log10 price value for trendline projection (guards against float64 overflow)
# Values > 308 would overflow; we use 300 as a safety margin
# This happens with very steep slopes from short data spans or outliers
TRENDLINE_LOG_PRICE_LIMIT = 300

# Fibonacci extension levels for price projection (most common trading levels)
FIBONACCI_LEVELS = {
    "127.2%": 1.272,  # Primary target (default)
    "161.8%": 1.618,  # Golden ratio extension
    "261.8%": 2.618,  # Extended target
}

# Default Fibonacci level for pattern analysis
DEFAULT_FIBONACCI_LEVEL = FIBONACCI_LEVELS["127.2%"]

# Default diminishing returns factor derived from BTC historical data
# Used when only one cycle of data is available for a coin
#
# Calculation from BTC/USD historical cycles (prices from CryptoCompare):
#   Cycle 2: Bottom $164.92 (2015-01-14) → Peak $19,345.49 (2017-12-16) = 117.3x gain
#   Cycle 3: Bottom $3,232.51 (2018-12-15) → Peak $67,549.14 (2021-11-08) = 20.9x gain
#   Diminishing factor = 20.9 / 117.3 = 0.178
#
# We use 0.20 as a slightly conservative estimate to account for:
# - Uncertainty in exact cycle timing
# - Variation between BTC and altcoin diminishing patterns
# - The fact that altcoins often show steeper diminishing returns vs BTC
DEFAULT_DIMINISHING_FACTOR = 0.20

# Trendline regression point weights
# Major points (min1, max2) are the true cycle extremes - higher weight
# Minor points (max1, min2) are intermediate points - lower weight
# With only 2 points per category, weights have no effect (line is unique)
# With 3+ points, weights affect which points the regression line fits more closely
TRENDLINE_MAJOR_POINT_WEIGHT = 0.67  # Weight for min1 (true bottom) and max2 (true peak)
TRENDLINE_MINOR_POINT_WEIGHT = 0.33  # Weight for max1 and min2 (intermediate points)

# Trendline recency decay factor
# Controls how much older cycles are downweighted relative to recent ones.
# Applied as: recency_weight = TRENDLINE_RECENCY_DECAY ** (max_cycle - point_cycle)
# With 0.7: most recent cycle = 1.0, one back = 0.7, two back = 0.49
# This ensures trendlines follow recent extrema more closely, preventing
# early high-growth cycles from making projections overly optimistic.
TRENDLINE_RECENCY_DECAY = 0.7

# Minimum lower trendline slope (floor appreciation) requirement
# Coins with declining or stagnant floors (min points getting lower) are filtered out.
# The slope is in log10-space per day. To convert annual percentage to slope:
#   annual_gain = 10^(slope * 365)
#   slope = log10(1 + annual_pct/100) / 365
# For 8% annual floor appreciation: slope = log10(1.08) / 365 ≈ 0.0000915
# Coins with lower_slope below this threshold are filtered out as underperforming.
MIN_LOWER_SLOPE_ANNUAL_PCT = 8  # Require at least 8% annual floor appreciation
MIN_LOWER_SLOPE = math.log10(1 + MIN_LOWER_SLOPE_ANNUAL_PCT / 100) / 365

# Minimum coin age for pattern analysis (filters out very new coins)
# Coins with first price date less than this many days ago are excluded from top coins
# This helps avoid unreliable projections from coins with very limited price history
MIN_COIN_AGE_DAYS = 365  # 1 year minimum

# Minimum unique prices for pattern analysis (filters out illiquid/staircase patterns)
# Coins with very few distinct price values indicate low trading activity or liquidity issues.
# Examples: ZBCN, HTX show "staircase" patterns where price stays constant for extended periods.
# Such coins should be filtered out as their price data is not representative of market dynamics.
# Threshold: require at least 30 unique price values over the coin's history.
MIN_UNIQUE_PRICES = 30

# Composite score weight profiles by confidence level
#
# Each profile defines method weights and an overall scale factor.
# A single code path uses the profile matching the coin's confidence level,
# rather than separate logic for low-confidence coins.
#
# Method weights (before renormalization):
# - trendline: Captures structural multi-cycle trend direction (most informative)
# - fibonacci: Technical projection based on previous cycle move
# - historical: Reality anchor based on achieved valuations
# - diminishing: Most volatile/unreliable, sensitive to outlier launch cycles
#
# Scale factor:
# - Applied after computing the weighted average to adjust for confidence uncertainty.
# - 1.0 for high/medium confidence (no adjustment)
# - 0.3 for low confidence (70% penalty reflecting higher uncertainty)
#
# Low confidence (1 cycle): trendline weight = 0 because a 2-point trendline is
# statistically unreliable, and scale = 0.3 to penalize for limited data.
COMPOSITE_WEIGHT_PROFILES: dict[str, dict[str, float]] = {
    "high": {
        "trendline": 0.40,
        "fibonacci": 0.25,
        "historical": 0.20,
        "diminishing": 0.15,
        "scale": 1.0,
    },
    "medium": {
        "trendline": 0.40,
        "fibonacci": 0.25,
        "historical": 0.20,
        "diminishing": 0.15,
        "scale": 1.0,
    },
    "low": {
        "trendline": 0.0,
        "fibonacci": 0.25,
        "historical": 0.20,
        "diminishing": 0.15,
        "scale": 0.3,
    },
}

# Diminishing returns minimum gain floor
# The dim returns model projects decreasing but still positive gains each cycle.
# A projected gain < 1.0x (i.e., a loss from the cycle minimum) is nonsensical
# for this model - it contradicts the "diminishing positive returns" concept.
# When the projected gain falls below this floor, it is clamped to this value.
# 1.0 = break-even (0% return from latest min), meaning the model contributes
# a neutral prediction rather than a misleading negative one.
DIM_RETURN_MIN_GAIN_RATIO = 1.0

# Retracement penalty: penalizes coins that have given back most of their cycle gains
#
# Motivation: A coin like COOKIE can peak at 30x its cycle low, then crash back down
# to near the low. All projection methods still produce huge targets (relative to the
# low), inflating the composite. Meanwhile a coin like VIRTUAL holds up much better.
#
# Measured in log-space (consistent with log-scale trendlines):
#   log_retracement = log10(peak / current) / log10(peak / trough)
#   0.0 = coin at peak, 1.0 = coin back at cycle trough
#
# The penalty is a multiplier on the composite score:
#   - Below threshold: no penalty (multiplier = 1.0)
#   - Above threshold: linear ramp down to (1 - RETRACEMENT_PENALTY_MAX) at full retracement
#
# Example with default values (threshold=0.75, max=0.5):
#   retracement=0.50 → multiplier=1.00 (below threshold)
#   retracement=0.75 → multiplier=1.00 (at threshold)
#   retracement=0.875 → multiplier=0.75 (half penalty)
#   retracement=1.00 → multiplier=0.50 (full penalty)
RETRACEMENT_PENALTY_THRESHOLD = 0.75  # No penalty below 75% log-retracement
RETRACEMENT_PENALTY_MAX = 0.5  # Maximum penalty: 50% reduction at full retracement

# Cycle 5 min1 approximate date for trendline regression
# Since cycle 5 is ongoing, the actual min1 date may not yet reflect the true cycle bottom.
# For trendline regression (which uses dates as x-coordinates), we use an approximated date
# based on typical cycle timing: 520 days before the projected 5th halving.
# This places min1 within the typical window [halving-550, halving] and provides a stable
# reference point for regression calculations regardless of when the actual minimum occurs.
# Note: The actual detected min1 date/price is still used for display and other methods.
CYCLE5_MIN1_APPROX_DAYS_BEFORE_HALVING = 520

# =============================================================================
# Pattern Analysis Coin Selection
# =============================================================================

# Number of top altcoins to include in pattern analysis page (ranked by composite score)
PATTERN_ANALYSIS_TOP_N = 14

# How far back to look for coins that were in TOTAL2 (years)
# Coins must have been in TOTAL2 within this period to be analyzed.
# This expanded selection allows analysis of coins even if they temporarily
# dropped out of the TOTAL2 top 30.
TOTAL2_LOOKBACK_YEARS = 3


# =============================================================================
# Configuration Validation
# =============================================================================


class ConfigurationError(Exception):
    """Raised when configuration values are invalid or inconsistent."""


def validate_config() -> None:
    """
    Validate configuration values for consistency and correctness.

    Raises:
        ConfigurationError: If any configuration values are invalid

    Checks performed:
    - Halving dates are in chronological order
    - Time window constants are positive and consistent
    - Numeric thresholds are within reasonable ranges
    - Required lists/sets are not empty
    """
    errors: list[str] = []

    # Validate halving dates are chronological
    for i in range(len(HALVING_DATES) - 1):
        if HALVING_DATES[i] >= HALVING_DATES[i + 1]:
            errors.append(
                f"HALVING_DATES must be chronological: "
                f"{HALVING_DATES[i]} >= {HALVING_DATES[i + 1]}"
            )

    # Validate projected halving is after the last known halving
    if HALVING_DATES and PROJECTED_5TH_HALVING <= HALVING_DATES[-1]:
        errors.append(
            f"PROJECTED_5TH_HALVING ({PROJECTED_5TH_HALVING}) must be after "
            f"last halving ({HALVING_DATES[-1]})"
        )

    # Validate time window constants
    if DAYS_BEFORE_HALVING <= 0:
        errors.append(f"DAYS_BEFORE_HALVING must be positive: {DAYS_BEFORE_HALVING}")
    if DAYS_AFTER_HALVING <= 0:
        errors.append(f"DAYS_AFTER_HALVING must be positive: {DAYS_AFTER_HALVING}")
    if TOTAL_WINDOW_DAYS != DAYS_BEFORE_HALVING + DAYS_AFTER_HALVING:
        errors.append(
            f"TOTAL_WINDOW_DAYS ({TOTAL_WINDOW_DAYS}) must equal "
            f"DAYS_BEFORE_HALVING + DAYS_AFTER_HALVING "
            f"({DAYS_BEFORE_HALVING} + {DAYS_AFTER_HALVING})"
        )

    # Validate TOTAL2 settings
    if TOP_N_BY_MARKETCAP_TO_FETCH <= 0:
        errors.append(
            f"TOP_N_BY_MARKETCAP_TO_FETCH must be positive: {TOP_N_BY_MARKETCAP_TO_FETCH}"
        )
    if TOP_N_BY_VOLUME_FOR_TOTAL2 <= 0:
        errors.append(f"TOP_N_BY_VOLUME_FOR_TOTAL2 must be positive: {TOP_N_BY_VOLUME_FOR_TOTAL2}")
    if VOLUME_SMA_WINDOW <= 0:
        errors.append(f"VOLUME_SMA_WINDOW must be positive: {VOLUME_SMA_WINDOW}")
    if TOTAL2_MIN_COINS_FOR_INDEX <= 0:
        errors.append(f"TOTAL2_MIN_COINS_FOR_INDEX must be positive: {TOTAL2_MIN_COINS_FOR_INDEX}")
    if TOTAL2_MIN_COINS_FOR_INDEX > TOP_N_BY_VOLUME_FOR_TOTAL2:
        errors.append(
            f"TOTAL2_MIN_COINS_FOR_INDEX ({TOTAL2_MIN_COINS_FOR_INDEX}) should not exceed "
            f"TOP_N_BY_VOLUME_FOR_TOTAL2 ({TOP_N_BY_VOLUME_FOR_TOTAL2})"
        )

    # Validate entry warmup parameters
    if TOTAL2_ENTRY_MAX_INCREASE <= 1.0:
        errors.append(f"TOTAL2_ENTRY_MAX_INCREASE must be > 1.0: {TOTAL2_ENTRY_MAX_INCREASE}")
    if not (0.0 < TOTAL2_ENTRY_MAX_DECREASE < 1.0):
        errors.append(
            f"TOTAL2_ENTRY_MAX_DECREASE must be between 0 and 1: {TOTAL2_ENTRY_MAX_DECREASE}"
        )
    if TOTAL2_ENTRY_WARMUP_PERIOD_DAYS <= 0:
        errors.append(
            f"TOTAL2_ENTRY_WARMUP_PERIOD_DAYS must be positive: {TOTAL2_ENTRY_WARMUP_PERIOD_DAYS}"
        )

    # Validate TOTAL2b parameters
    if TOTAL2B_ENTRY_FREEZE_PERIOD_DAYS <= 0:
        errors.append(
            f"TOTAL2B_ENTRY_FREEZE_PERIOD_DAYS must be positive: {TOTAL2B_ENTRY_FREEZE_PERIOD_DAYS}"
        )
    if TOTAL2B_MIN_COINS_FOR_SCALING <= 0:
        errors.append(
            f"TOTAL2B_MIN_COINS_FOR_SCALING must be positive: {TOTAL2B_MIN_COINS_FOR_SCALING}"
        )
    if TOTAL2B_SYMBOL_REPLACEMENT_THRESHOLD <= 1.0:
        errors.append(
            f"TOTAL2B_SYMBOL_REPLACEMENT_THRESHOLD must be > 1.0: "
            f"{TOTAL2B_SYMBOL_REPLACEMENT_THRESHOLD}"
        )

    # Validate required sets are not empty
    if not EXCLUDED_STABLECOINS:
        errors.append("EXCLUDED_STABLECOINS must not be empty")
    if not EXCLUDED_WRAPPED_STAKED_IDS:
        errors.append("EXCLUDED_WRAPPED_STAKED_IDS must not be empty")
    if not EXCLUDED_PATTERNS:
        errors.append("EXCLUDED_PATTERNS must not be empty")

    # Validate trendline parameters
    if TRENDLINE_LOG_PRICE_LIMIT <= 0:
        errors.append(f"TRENDLINE_LOG_PRICE_LIMIT must be positive: {TRENDLINE_LOG_PRICE_LIMIT}")
    if not (0.0 < TRENDLINE_MAJOR_POINT_WEIGHT <= 1.0):
        errors.append(
            f"TRENDLINE_MAJOR_POINT_WEIGHT must be between 0 and 1: "
            f"{TRENDLINE_MAJOR_POINT_WEIGHT}"
        )
    if not (0.0 < TRENDLINE_MINOR_POINT_WEIGHT <= 1.0):
        errors.append(
            f"TRENDLINE_MINOR_POINT_WEIGHT must be between 0 and 1: "
            f"{TRENDLINE_MINOR_POINT_WEIGHT}"
        )

    # Validate Fibonacci level
    if DEFAULT_FIBONACCI_LEVEL <= 1.0:
        errors.append(f"DEFAULT_FIBONACCI_LEVEL must be > 1.0: {DEFAULT_FIBONACCI_LEVEL}")

    # Validate diminishing factor
    if not (0.0 < DEFAULT_DIMINISHING_FACTOR < 1.0):
        errors.append(
            f"DEFAULT_DIMINISHING_FACTOR must be between 0 and 1: " f"{DEFAULT_DIMINISHING_FACTOR}"
        )

    # Validate trendline recency decay
    if not (0.0 < TRENDLINE_RECENCY_DECAY <= 1.0):
        errors.append(f"TRENDLINE_RECENCY_DECAY must be between 0 and 1: {TRENDLINE_RECENCY_DECAY}")

    # Validate composite weight profiles
    required_keys = {"trendline", "fibonacci", "historical", "diminishing", "scale"}
    for level in ("high", "medium", "low"):
        if level not in COMPOSITE_WEIGHT_PROFILES:
            errors.append(f"COMPOSITE_WEIGHT_PROFILES missing '{level}' profile")
            continue
        profile = COMPOSITE_WEIGHT_PROFILES[level]
        missing = required_keys - set(profile.keys())
        if missing:
            errors.append(f"COMPOSITE_WEIGHT_PROFILES['{level}'] missing keys: {missing}")
            continue
        # Method weights must be non-negative
        for key in ("trendline", "fibonacci", "historical", "diminishing"):
            if profile[key] < 0:
                errors.append(
                    f"COMPOSITE_WEIGHT_PROFILES['{level}']['{key}'] "
                    f"must be non-negative: {profile[key]}"
                )
        # At least one method weight must be > 0
        method_sum = sum(
            profile[k] for k in ("trendline", "fibonacci", "historical", "diminishing")
        )
        if method_sum <= 0:
            errors.append(
                f"COMPOSITE_WEIGHT_PROFILES['{level}'] must have at least one "
                f"positive method weight, got sum={method_sum}"
            )
        # Scale factor must be positive
        if profile["scale"] <= 0:
            errors.append(
                f"COMPOSITE_WEIGHT_PROFILES['{level}']['scale'] "
                f"must be positive: {profile['scale']}"
            )

    # Validate diminishing returns gain floor
    if DIM_RETURN_MIN_GAIN_RATIO < 0:
        errors.append(
            f"DIM_RETURN_MIN_GAIN_RATIO must be non-negative: {DIM_RETURN_MIN_GAIN_RATIO}"
        )

    # Validate retracement penalty parameters
    if not (0.0 < RETRACEMENT_PENALTY_THRESHOLD <= 1.0):
        errors.append(
            f"RETRACEMENT_PENALTY_THRESHOLD must be in (0, 1]: {RETRACEMENT_PENALTY_THRESHOLD}"
        )
    if not (0.0 < RETRACEMENT_PENALTY_MAX <= 1.0):
        errors.append(f"RETRACEMENT_PENALTY_MAX must be in (0, 1]: {RETRACEMENT_PENALTY_MAX}")

    if errors:
        raise ConfigurationError("Configuration validation failed:\n" + "\n".join(errors))

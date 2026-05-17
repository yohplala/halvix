"""
Configuration constants for the Halvix project.

Halvix - Cryptocurrency price analysis relative to Bitcoin halving cycles.
"""

import math
from datetime import date
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
CACHE_EXPIRY_SECONDS = 86400
OUTPUT_DIR = PROJECT_ROOT / "output"

# =============================================================================
# Bitcoin Halving Dates
# =============================================================================

HALVING_DATES: list[date] = [
    date(2012, 11, 28),  # 1st halving
    date(2016, 7, 9),  # 2nd halving
    date(2020, 5, 11),  # 3rd halving
    date(2024, 4, 19),  # 4th halving
    date(2028, 3, 31),  # 5th halving (projected)
]

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
USE_YESTERDAY_AS_END_DATE = True

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
# or LIT changed from Litentry to Lighter in Jan 2026 with a 4.43x jump).
# Asymmetric thresholds: increases are more suspicious than decreases because
# legitimate crashes can cause sharp drops (e.g., OM/MANTRA at 0.164x), but a
# 4x+ daily gain against BTC is virtually impossible without a symbol swap.
# Increase threshold calibrated from LIT (Litentry→Lighter) swap: 4.43x on 2026-01-08.
# Decrease threshold set at 0.101 (~1/10): below OM's 0.164x crash, above real swaps.
SYMBOL_REPLACEMENT_INCREASE_THRESHOLD = 4.42  # ratio > 4.42x flags replacement
SYMBOL_REPLACEMENT_DECREASE_THRESHOLD = 0.101  # ratio < 0.101x flags replacement

# Round-trip Detection: catches price spike-and-revert patterns in the close
# series (single-day or multi-day) that distort the TOTAL2 index. Symbol-
# replacement detection misses these because the new price is transient, not
# permanent; the remedy here is to smooth every elevated/depressed day in the
# pattern back to the pre-spike baseline, keeping the coin in the index.
#
# At each position i the detector scans [i, i+window] for an extremum and
# checks whether the price returns to baseline within the same window AFTER
# the extremum:
#   - up_candidate: max(close[i..i+window]) / close[i-1] > JUMP_THRESHOLD
#   - down_candidate: min(close[i..i+window]) / close[i-1] < 1/JUMP_THRESHOLD
# On a confirmed revert (revert_ratio inside [1/REVERT_THRESHOLD, REVERT_THRESHOLD]),
# every day from i through the extremum is replaced by close[i-1].
#
# Examples this catches:
#   - SIREN 2026-04-16: single-day 2.49x spike, reverts next day
#   - RAVE 2026-04-15..18: 3-day climb (1.57x, 1.26x compounding to 2.7x peak)
#     followed by a 0.13x crash
# Durable bull moves (a 3x rally that stays elevated) never round-trip and are
# correctly left alone.
PRICE_ROUND_TRIP_JUMP_THRESHOLD = 2.0
PRICE_ROUND_TRIP_REVERT_THRESHOLD = 1.5
PRICE_ROUND_TRIP_WINDOW_DAYS = 7

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
    "aeur",  # Anchored Coins EUR (empirically tracks EUR: ~€1, 0.4% USD-implied std)
    "europ",  # EUR-pegged stablecoin (empirically: ~€1, 2.4% std, recently launched)
    # Other stablecoins
    "mim",
    "dola",
    "ausd",  # Acala USD (Polkadot stablecoin)
    "usat",  # USD-pegged stablecoin (empirically: $1.00, 0.04% USD-implied std)
    "usdcv",  # USD CoinVertible (Societe Generale-FORGE; $1.00, 0.04% std)
    "usdr",  # USD-pegged stablecoin (empirically: $1.00, 0.3% std)
    "usdq",  # USD-pegged stablecoin (empirically: $1.00, 0.8% std)
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


def coin_url(symbol: str) -> str:
    """Build CryptoCompare overview URL for a coin symbol."""
    return f"{CRYPTOCOMPARE_COIN_URL}/{symbol.upper()}/overview"


# Rate limiting: The client uses dynamic rate limiting by checking the
# /stats/rate/limit endpoint to monitor actual quota usage.
# This constant serves as a FALLBACK minimum interval between requests,
# used when rate limit status is unavailable or as a baseline throttle.
# We use a very conservative fallback of 1 call every 5 seconds.
CRYPTOCOMPARE_API_CALLS_PER_MINUTE = 12  # Fallback: 5 seconds between requests

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

# =============================================================================
# Pattern Analysis Configuration
# =============================================================================

# Maximum log10 price value for trendline projection (guards against float64 overflow)
# Values > 308 would overflow; we use 300 as a safety margin
# This happens with very steep slopes from short data spans or outliers
TRENDLINE_LOG_PRICE_LIMIT = 300

# Default Fibonacci level for pattern analysis
DEFAULT_FIBONACCI_LEVEL = 1.0

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

# Cycle point weights for regression and historical peak averaging
# Major points (min1, max2) are the true cycle extremes - higher weight
# Minor points (max1, min2) are intermediate points - lower weight
# Used in: (1) trendline log-linear regression (weighted least squares),
#          (2) historical peak weighted average calculation
# With only 2 points per category, weights have no effect (line is unique)
# With 3+ points, weights affect which points the regression line fits more closely
MAJOR_POINT_WEIGHT = 0.67  # Weight for min1 (true bottom) and max2 (true peak)
MINOR_POINT_WEIGHT = 0.33  # Weight for max1 and min2 (intermediate points)

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
# For 4% annual floor appreciation: slope = log10(1.04) / 365 ≈ 0.0000467
# Coins with lower_slope below this threshold are filtered out as underperforming.
MIN_LOWER_SLOPE_ANNUAL_PCT = 4  # Require at least 4% annual floor appreciation
MIN_LOWER_SLOPE = math.log10(1 + MIN_LOWER_SLOPE_ANNUAL_PCT / 100) / 365

# Minimum upper trendline projection percentage
# Coins whose upper trendline projects a decline steeper than this are filtered out.
# A mild negative projection (e.g., -10%) indicates a compression pattern and is allowed,
# but a steep decline (e.g., -40%) signals structural weakness in cycle peaks.
MIN_UPPER_TRENDLINE_TARGET_PCT = -30  # Allow up to -30%; filter below

# Minimum coin age for pattern analysis (filters out very new coins)
# Coins with first price date less than this many days ago are excluded from top coins
# This helps avoid unreliable projections from coins with very limited price history
MIN_COIN_AGE_DAYS = 365  # 1 year minimum

# Minimum unique prices for pattern analysis (filters out illiquid/staircase patterns)
# Coins with very few distinct price values indicate low trading activity or liquidity issues.
# Examples: ZBCN, HTX show "staircase" patterns where price stays constant for extended periods.
# Such coins should be filtered out as their price data is not representative of market dynamics.
# Measured over a recent window (UNIQUE_PRICES_WINDOW_DAYS) to catch coins that became illiquid.
UNIQUE_PRICES_WINDOW_DAYS = 90  # ~3 months lookback for unique price check
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
# - 1.0 for high confidence (no adjustment), 0.9 for medium
# - 0.15 for low confidence (85% penalty reflecting very high uncertainty)
#
# Low confidence (1 cycle): historical peak dominates at 70%; trendline gets
# a modest 10% weight for directional signal. Scale = 0.15 keeps low-confidence
# coins below high-confidence peers.
COMPOSITE_WEIGHT_PROFILES: dict[str, dict[str, float]] = {
    "high": {
        "trendline": 0.55,
        "fibonacci": 0.19,
        "historical": 0.15,
        "diminishing": 0.11,
        "scale": 1.0,
    },
    "medium": {
        "trendline": 0.40,
        "fibonacci": 0.25,
        "historical": 0.20,
        "diminishing": 0.15,
        "scale": 0.9,
    },
    "low": {
        "trendline": 0.10,
        "fibonacci": 0.08,
        "historical": 0.70,
        "diminishing": 0.12,
        "scale": 0.15,
    },
}

# Diminishing returns minimum gain floor
# The dim returns model projects decreasing but still positive gains each cycle.
# A projected gain < 1.0x (i.e., a loss from the cycle minimum) is nonsensical
# for this model - it contradicts the "diminishing positive returns" concept.
# When the projected gain ratio (max/min) falls below this floor, it is clamped.
# A gain ratio < 1.0 is structurally impossible (peak below trough), so the
# floor must be at least 1.0 to keep projections meaningful.
DIM_RETURN_MIN_GAIN_RATIO = 1.0

# Fibonacci retracement filter: filters out coins that retraced too much of their
# last cycle's gain (trough → peak).
#
# Motivation: A coin like COOKIE can peak at 30x its cycle low, then crash back down
# to near the low. All projection methods still produce huge targets (relative to the
# low), inflating the composite. Meanwhile a coin like VIRTUAL holds up much better.
#
# Measured in log-space (consistent with log-scale trendlines) using last cycle points:
#   A = prev cycle min (min1 or min2)
#   B = prev cycle max2 (peak)
#   C = current cycle min1 (new trough)
#   log_retracement = log10(B / C) / log10(B / A)
#   0.0 = coin at peak, 1.0 = coin back at cycle trough
#
# Standard Fibonacci retracement levels:
#   23.6% - shallow pullback (very healthy)
#   38.2% - normal correction
#   50.0% - moderate
#   61.8% - deep (golden ratio boundary)
#   78.6% - very deep (structural weakness, sqrt of 0.618)
#   88.6% - last Fibonacci support before full retracement (sqrt of 0.786)
#
# We use 88.6% as the cutoff: beyond this, the coin has retraced so deeply that
# the "higher low" structure is broken — similar to a declining floor slope.
MAX_RETRACEMENT_LEVEL = 0.886

# Cycle 5 min1 approximate date for trendline regression
# Since cycle 5 is ongoing, the actual min1 date may not yet reflect the true cycle bottom.
# For trendline regression (which uses dates as x-coordinates), we use an approximated date
# based on typical cycle timing: 520 days before the projected 5th halving.
# This places min1 within the typical window [halving-550, halving] and provides a stable
# reference point for regression calculations regardless of when the actual minimum occurs.
# Note: The actual detected min1 date/price is still used for display and other methods.
CURRENT_CYCLE_MIN1_APPROX_DAYS_BEFORE_HALVING = 520

# Minimum Fibonacci retracement for cycle point validity
# Points (min2, max1) must show at least 23.6% retracement to be considered significant.
# 23.6% is the smallest standard Fibonacci level — anything less is noise, not structure.
# For min1 in the current/incomplete cycle, this also gates whether the bear has started.
MIN_RETRACEMENT_LEVEL = 0.236

# Buffer (days) to exclude pre-halving rally from max2 search.
# The max2 search window ends at H[n] - buffer to prevent the pre-halving pump
# (which is structurally max1) from being picked as the cycle peak.
# BTC's pre-H4 rally exceeded the Nov 2021 ATH ~36 days before halving; 60 days
# provides comfortable margin without risking exclusion of a genuine cycle top.
MAX2_PRE_HALVING_BUFFER_DAYS = 60

# Buffer (days) to suppress min2 at first data point.
# If a detected min2 falls within this many days of the coin's first available
# price, it is suppressed — launch price is not a structural dip.
LAUNCH_DATE_BUFFER_DAYS = 7

# Continuous retracement penalty parameters
# Applied to coins between GOLDEN_RETRACEMENT_LEVEL and MAX_RETRACEMENT_LEVEL
# to gradually penalize composite score as retracement deepens.
GOLDEN_RETRACEMENT_LEVEL = 0.618  # 61.8% Fibonacci level (golden ratio)
RETRACEMENT_PENALTY_AT_MAX = 0.5  # Composite multiplied by this at MAX_RETRACEMENT_LEVEL

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
# CSV Export Configuration
# =============================================================================

# Semicolon delimiter for Excel compatibility (Excel auto-splits on ";")
CSV_DELIMITER = ";"

# =============================================================================
# Trendline Pattern Classification
# =============================================================================

# Slope difference threshold for classifying trendline patterns.
# When abs(upper_slope - lower_slope) < this value, slopes are considered
# parallel and the pattern is classified as "channel" rather than a wedge.
SLOPE_DIFF_CHANNEL_THRESHOLD = 0.00001

# =============================================================================
# API Rate Limiting Thresholds
# =============================================================================

# CryptoCompare rate limit "near limit" thresholds.
# When remaining calls fall below these values, the client starts throttling.
RATE_LIMIT_HOURLY_THRESHOLD = 300  # 10% of 3000 hourly quota
RATE_LIMIT_MONTHLY_THRESHOLD = 1000  # 2% of 50000 monthly quota

# How often (seconds) to re-check the rate limit endpoint
RATE_CHECK_INTERVAL_SECONDS = 30.0

# CryptoCompare histoday API maximum days per request
CRYPTOCOMPARE_MAX_DAYS_PER_REQUEST = 2000

# CryptoCompare top coins API page size
CRYPTOCOMPARE_TOP_COINS_PER_PAGE = 100

# =============================================================================
# Chart Layout Constants
# =============================================================================

# Annotation positioning for target text block on pattern charts
CHART_ANNOTATION_DAYS_OFFSET = 30  # Days before halving line for text X position
CHART_LINE_SPACING = 0.035  # Vertical spacing between annotation lines (paper coords)
CHART_ANNOTATION_BASE_Y = 0.05  # Y position of bottom annotation line (paper coords)

# Fibonacci hint line Y shift (log-scale multiplier to separate from dim-return lines)
FIB_HINT_Y_SHIFT = 0.90

# Y-axis padding for log-scale charts (added above/below data range in log10 space)
CHART_Y_AXIS_PADDING = 0.2

# Price formatting threshold for BTC/USD display
BTC_PRICE_K_THRESHOLD = 1000  # Prices >= this are shown as "$Xk"

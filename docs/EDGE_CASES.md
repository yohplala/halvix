# Edge Cases and Solutions

This document analyzes edge cases identified in the data fetching pipeline and their implemented solutions.

> **Status**: All edge cases have been implemented ✅

## 1. BTC as Quote Currency

### Status: ✅ Already Implemented

CryptoCompare supports BTC as the quote currency (`tsym=BTC`). Our implementation already defaults to this:

```python
# src/api/cryptocompare.py
def get_daily_history(
    self,
    symbol: str,
    vs_currency: str = "BTC",  # ✅ BTC is default
    ...
)
```

**No changes needed.**

---

## 2. Symbol Mapping Validation

### Problem

CoinGecko uses human-readable IDs (`ethereum`, `solana`) while CryptoCompare uses uppercase trading symbols (`ETH`, `SOL`). There's no guarantee the mapping is correct:

- Some coins have different symbols on different exchanges
- Symbol conflicts exist (e.g., "LUNA" could refer to Terra Classic or Terra 2.0)
- New coins may not exist on CryptoCompare

### Solution: Cross-Validation with Price Comparison

**Validation Strategy:**
1. For each coin, fetch **yesterday's close price** from both APIs
2. CoinGecko: Get current price (in BTC) via `/coins/markets`
3. CryptoCompare: Get yesterday's close via `/data/v2/histoday`
4. Compare prices - if within **4% tolerance**, consider mapping valid
5. Cache validated mappings to avoid re-validation on restart

### Implementation

```python
# New file: src/data/symbol_mapping.py

@dataclass
class SymbolMapping:
    coingecko_id: str
    coingecko_symbol: str
    cryptocompare_symbol: str
    validated_at: datetime
    coingecko_price: float
    cryptocompare_price: float
    price_diff_percent: float
    is_valid: bool

class SymbolMappingCache:
    """
    Manages and validates mappings between CoinGecko IDs and CryptoCompare symbols.
    
    Validation process:
    1. Get yesterday's price from CoinGecko
    2. Get yesterday's close price from CryptoCompare
    3. Compare prices (must be within 4% tolerance)
    4. Cache valid mappings for future use
    """
    
    TOLERANCE_PERCENT = 4.0  # 4% price difference allowed
    
    def __init__(self, cache_file: Path):
        self.cache_file = cache_file
        self._mappings: dict[str, SymbolMapping] = {}
        self._load_cache()
    
    def validate_mapping(
        self,
        coingecko_id: str,
        coingecko_symbol: str,
        coingecko_client: CoinGeckoClient,
        cryptocompare_client: CryptoCompareClient,
    ) -> SymbolMapping:
        """
        Validate that a CoinGecko ID maps to the correct CryptoCompare symbol.
        
        Returns:
            SymbolMapping with validation results
        """
        ...
    
    def has_mapping(self, coingecko_id: str) -> bool:
        """Check if a coin has already been validated (whether valid or invalid)."""
        return coingecko_id in self._mappings
    
    def get_cryptocompare_symbol(self, coingecko_id: str) -> str | None:
        """Get the validated CryptoCompare symbol for a CoinGecko ID."""
        mapping = self._mappings.get(coingecko_id)
        if mapping and mapping.is_valid:
            return mapping.cryptocompare_symbol
        return None
```

### Validation Logic

```python
def _calculate_price_diff(self, price1: float, price2: float) -> float:
    """Calculate percentage difference between two prices."""
    if price1 == 0 or price2 == 0:
        return float('inf')
    avg = (price1 + price2) / 2
    return abs(price1 - price2) / avg * 100

def validate_mapping(self, coingecko_id: str, coingecko_symbol: str, ...) -> SymbolMapping:
    # Already validated?
    if self.is_validated(coingecko_id):
        return self._mappings[coingecko_id]
    
    cryptocompare_symbol = coingecko_symbol.upper()
    yesterday = date.today() - timedelta(days=1)
    
    # Get CoinGecko price (current price, close to yesterday's close)
    cg_price = coingecko_client.get_current_price(coingecko_id, vs_currency="btc")
    
    # Get CryptoCompare yesterday's close
    cc_data = cryptocompare_client.get_daily_history(
        symbol=cryptocompare_symbol,
        vs_currency="BTC",
        limit=1,
    )
    cc_price = cc_data[-1]["close"] if cc_data else 0
    
    # Calculate difference
    diff_percent = self._calculate_price_diff(cg_price, cc_price)
    is_valid = diff_percent <= self.TOLERANCE_PERCENT
    
    mapping = SymbolMapping(
        coingecko_id=coingecko_id,
        coingecko_symbol=coingecko_symbol,
        cryptocompare_symbol=cryptocompare_symbol,
        validated_at=datetime.now(),
        coingecko_price=cg_price,
        cryptocompare_price=cc_price,
        price_diff_percent=diff_percent,
        is_valid=is_valid,
    )
    
    self._mappings[coingecko_id] = mapping
    self._save_cache()
    
    return mapping
```

### Cache File Format

```json
{
  "ethereum": {
    "coingecko_id": "ethereum",
    "coingecko_symbol": "eth",
    "cryptocompare_symbol": "ETH",
    "validated_at": "2025-12-03T10:30:00",
    "coingecko_price": 0.0412,
    "cryptocompare_price": 0.0415,
    "price_diff_percent": 0.72,
    "is_valid": true
  },
  "some-scam-coin": {
    "coingecko_id": "some-scam-coin",
    "coingecko_symbol": "scam",
    "cryptocompare_symbol": "SCAM",
    "validated_at": "2025-12-03T10:31:00",
    "coingecko_price": 0.0001,
    "cryptocompare_price": 0.5000,
    "price_diff_percent": 199.98,
    "is_valid": false
  }
}
```

---

## 3. Incremental Data Fetching

### Problem

Currently, every price fetch retrieves the **full history** (5000+ days), even if we already have most of it cached. This is:
- Slow (multiple paginated requests)
- Wasteful of API quota
- Unnecessary after initial fetch

### Solution: Fetch Only New Data

```python
# In DataFetcher.fetch_coin_prices()

def fetch_coin_prices(
    self,
    coin_id: str,
    symbol: str,
    vs_currency: str = "BTC",
    use_cache: bool = True,
    incremental: bool = True,  # NEW
) -> pd.DataFrame:
    """
    Fetch historical price data, with incremental updates.
    
    If incremental=True and cached data exists:
    - Only fetch from (last_cached_date + 1) to yesterday
    - Merge new data with existing cache
    """
    cached = self.price_cache.get_prices(coin_id)
    yesterday = date.today() - timedelta(days=1)
    
    if cached is not None and not cached.empty and incremental:
        last_cached = cached.index.max().date()
        
        if last_cached >= yesterday:
            # Cache is up-to-date
            return cached
        
        # Fetch only new data
        new_data = self.cryptocompare.get_full_daily_history(
            symbol=symbol,
            vs_currency=vs_currency,
            start_date=last_cached + timedelta(days=1),
            end_date=yesterday,
        )
        
        if not new_data.empty:
            # Merge with existing cache
            combined = pd.concat([cached, new_data])
            combined = combined[~combined.index.duplicated(keep='last')]
            combined = combined.sort_index()
            self.price_cache.set_prices(coin_id, combined)
            return combined
        
        return cached
    
    # No cache or full refresh requested
    return self._fetch_full_history(coin_id, symbol, vs_currency, yesterday)
```

---

## 4. End Date Should Be Yesterday

### Problem

Today's price data is incomplete:
- CryptoCompare's `close` price for today is the **current price**, not the end-of-day price
- This creates data quality issues for analysis

### Solution: Always Use Yesterday as End Date

```python
# In multiple places

from datetime import date, timedelta

def get_safe_end_date() -> date:
    """Get yesterday's date (latest date with complete data)."""
    return date.today() - timedelta(days=1)

# Update get_full_daily_history default
def get_full_daily_history(
    self,
    symbol: str,
    vs_currency: str = "BTC",
    start_date: date | None = None,
    end_date: date | None = None,  # Default will be yesterday, not today
    ...
):
    if end_date is None:
        end_date = date.today() - timedelta(days=1)  # Yesterday, not today
```

---

## 5. Configuration Changes

Add to `src/config.py`:

```python
# Symbol mapping validation
SYMBOL_MAPPING_FILE = PROCESSED_DIR / "symbol_mappings.json"
SYMBOL_MAPPING_TOLERANCE_PERCENT = 5.0  # Maximum allowed price difference

# Data completeness
USE_YESTERDAY_AS_END_DATE = True  # Don't fetch incomplete today's data
```

---

## 6. Updated Data Flow

```
┌────────────────────────────────────────────────────────────────────┐
│                     Updated Fetch Pipeline                          │
├────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  1. Load Symbol Mapping Cache                                       │
│     └── data/processed/symbol_mappings.json                        │
│                                                                     │
│  2. For each coin from CoinGecko:                                  │
│     ├── Already validated? → Use cached mapping                    │
│     └── New coin? → Validate mapping:                              │
│         ├── Get CoinGecko price (yesterday)                        │
│         ├── Get CryptoCompare price (yesterday)                    │
│         ├── Compare (within 5%?) → Valid                           │
│         └── Save to mapping cache                                  │
│                                                                     │
│  3. For each validated coin:                                       │
│     ├── Has cached prices?                                         │
│     │   ├── Up to yesterday? → Skip (cache is current)            │
│     │   └── Older? → Fetch incrementally from last_date           │
│     └── No cache? → Fetch full history                            │
│                                                                     │
│  4. All fetches end at YESTERDAY (not today)                       │
│     └── Today's data is incomplete                                 │
│                                                                     │
└────────────────────────────────────────────────────────────────────┘
```

---

## 7. Implementation Priority

| Task | Complexity | Impact | Priority |
|------|------------|--------|----------|
| End date = yesterday | Low | High | 1️⃣ |
| Incremental fetching | Medium | High | 2️⃣ |
| Symbol mapping validation | Medium | High | 3️⃣ |
| Mapping cache persistence | Low | Medium | 4️⃣ |

### Estimated Effort

- **End date fix**: ~30 minutes (simple parameter change)
- **Incremental fetching**: ~2 hours (logic + tests)
- **Symbol mapping validation**: ~3 hours (new module + tests)
- **Integration**: ~1 hour

**Total: ~6-7 hours**

---

## 8. CLI Integration

```bash
# Validate new symbol mappings
python -m main validate-symbols

# Fetch prices (incremental by default)
python -m main fetch-prices --incremental

# Force full refresh
python -m main fetch-prices --full-refresh

# Show mapping status
python -m main status --show-mappings
```

---

## 9. Test Cases

### Symbol Mapping Validation Tests

```python
class TestSymbolMappingValidation:
    def test_valid_mapping_within_tolerance(self):
        """ETH prices from both APIs should match within 5%."""
        
    def test_invalid_mapping_outside_tolerance(self):
        """Mismatched coins should fail validation."""
        
    def test_cached_mapping_skips_validation(self):
        """Already-validated coins shouldn't re-validate."""
        
    def test_nonexistent_cryptocompare_symbol(self):
        """Coins not on CryptoCompare should fail gracefully."""
```

### Incremental Fetching Tests

```python
class TestIncrementalFetching:
    def test_incremental_fetch_merges_correctly(self):
        """New data should merge with existing cache."""
        
    def test_no_fetch_when_cache_current(self):
        """Should skip fetch if cache is up to yesterday."""
        
    def test_full_fetch_when_no_cache(self):
        """Should fetch full history when no cache exists."""
```

---

*Document created: 2025-12-03*


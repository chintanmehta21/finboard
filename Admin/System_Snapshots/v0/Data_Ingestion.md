# Finboard v2.0 — Data Ingestion & Sources

**Snapshot Date**: 2026-03-17
**Source Files**: `src/data/` (fyers_client.py, nse_bhavcopy.py, fundamentals.py, nse_fiidii.py, nse_pledge.py, universe.py, sample_data.py, nse_session.py)

---

## Data Source Overview

| Source | Data Type | Module | Rate Limit | Fallback |
|--------|----------|--------|------------|----------|
| **Fyers API v3** | OHLCV (daily candles) | `fyers_client.py` | 8 req/sec (limit 10) | None (critical) |
| **Fyers API v3** | Nifty 500, India VIX | `fyers_client.py` | Shared with OHLCV | None |
| **yfinance** | USD/INR (primary) | `fyers_client.py` | ~2 req/sec | Fyers fallback → default 87.0 |
| **yfinance** | Quarterly fundamentals | `fundamentals.py` | ~2 req/sec | Stock excluded if unavailable |
| **NSE Quote API** | Delivery volume % | `nse_bhavcopy.py` | 5 per batch, 1.5s delay | ZIP download → synthetic 50% |
| **NSE API** | FII/DII net flows | `nse_fiidii.py` | 1 req/attempt | NSDL → MoneyControl → cache |
| **NSE API** | Promoter pledge % | `nse_pledge.py` | 2 req/sec | 0% pledge (pass-through) |
| **NSE Archives** | NSE 500 constituents | `universe.py` | 1 req/download | NSE India API fallback |
| **yfinance** | Sample OHLCV (test mode) | `sample_data.py` | Unbounded | Synthetic generation |

---

## 1. NSE 500 Universe — `src/data/universe.py`

### What It Does
Maintains the list of ~500 stocks that form the analysis universe. The list is stored locally as a CSV and auto-refreshed from NSE when stale.

### Loading Flow
```
load_universe()
    │
    ├─ Check if data/nse500_constituents.csv exists
    │   ├─ If missing or > 90 days old → download_nse500_constituents()
    │   └─ If < 100 symbols → download_nse500_constituents()
    │
    ├─ Read CSV → extract 'Symbol' column
    │
    └─ Return list of symbols (e.g., ['RELIANCE', 'TCS', 'INFY', ...])
```

### Download Sources (priority order)
1. **NSE Archives static CSV** (primary, no Cloudflare protection)
2. **NSE India API endpoint** (fallback, may be blocked by Cloudflare)

### Sector Map
`get_sector_map()` returns a dict mapping each symbol to its sector (e.g., `{'RELIANCE': 'Energy', 'TCS': 'IT'}`). Used for sector concentration caps in portfolio construction.

### Constants
```python
UNIVERSE_FILE = 'data/nse500_constituents.csv'
STALE_DAYS = 90        # Auto-refresh after 90 days
MIN_SYMBOLS = 100      # Auto-refresh if fewer than 100
```

---

## 2. OHLCV Data — `src/data/fyers_client.py`

### What It Does
Fetches 2 years of daily OHLCV (Open, High, Low, Close, Volume) data for all NSE 500 stocks from the Fyers API. Also fetches index-level data (Nifty 500, India VIX, USD/INR).

### Stock Data Flow
```
fetch_all_ohlcv(fyers, symbols, years=2)
    │
    ├─ For each symbol (in batches of 8 per second):
    │   │
    │   └─ _fetch_history_chunked(fyers, symbol, start, end)
    │       │
    │       ├─ Split date range into <= 365-day chunks (Fyers API limit)
    │       ├─ Fetch each chunk via fyers.history()
    │       ├─ Concatenate all chunks
    │       ├─ Remove duplicate dates
    │       └─ Return DataFrame[date_index, open, high, low, close, volume]
    │
    └─ Return dict { symbol: DataFrame }
```

### Index Data Flow
```
fetch_index_data(fyers, years=2)
    │
    ├─ Nifty 500: fyers.history(symbol='NSE:NIFTY500-INDEX')
    │
    ├─ India VIX: fyers.history(symbol='NSE:INDIAVIX-INDEX')
    │
    └─ USD/INR:
        ├─ Primary: _fetch_usdinr_yfinance() → yfinance ticker 'USDINR=X'
        ├─ Fallback: fyers.history(symbol='NSE:USDINR-INDEX')
        └─ Default: 87.0 if both fail
    │
    └─ Return dict { 'nifty_df': DataFrame, 'vix_df': DataFrame, 'usdinr_df': DataFrame }
```

### Rate Limiting
- **8 requests per second** (Fyers limit is 10, we stay under)
- **1-second delay** between batches
- Each batch = 8 concurrent symbol fetches

### Date Chunking
Fyers API has a 365-day maximum per request. For 2-year lookback (730 days), each symbol requires 2 API calls stitched together.

### Constants
```python
BATCH_SIZE = 8          # Requests per second
BATCH_DELAY = 1.0       # Seconds between batches
MAX_DAILY_RANGE = 365   # Fyers API limit per request
```

---

## 3. Delivery Volume (Bhavcopy) — `src/data/nse_bhavcopy.py`

### What It Does
Fetches delivery volume data from NSE — the percentage of traded volume that resulted in actual delivery (shares changing hands). This data is NOT available from Fyers or yfinance.

### Why It Matters
Delivery % distinguishes between speculative trading (intraday, no delivery) and institutional accumulation (high delivery %). A 70% delivery day vs a 30% day signals very different buyer intent.

### Fetch Flow (3-tier fallback)
```
fetch_bhavcopy(trade_date, symbols)
    │
    ├─ Tier 1: _fetch_via_quote_api(symbols)
    │   ├─ Per-symbol NSE quote endpoint
    │   ├─ Batches of 5, 1.5-second delay
    │   ├─ Session refreshed every 40 requests (anti-bot)
    │   └─ Returns DataFrame[symbol, deliv_qty, deliv_pct, close, total_volume]
    │
    ├─ Tier 2: _fetch_via_zip(trade_date)
    │   ├─ Downloads bulk bhavcopy ZIP from NSE archives
    │   ├─ Extracts CSV, normalizes columns
    │   └─ Returns DataFrame (may be blocked by Cloudflare)
    │
    └─ Tier 3: _generate_synthetic_delivery(symbols)
        ├─ Last resort: assumes 50% delivery for all stocks
        └─ Returns DataFrame with neutral delivery values
```

### NSE Session Handling
NSE uses aggressive anti-bot measures (Cloudflare, cookie validation). The bhavcopy module:
1. Seeds a session with homepage cookies (`nse_session.py`)
2. Uses browser-like headers (User-Agent, Accept, Referer)
3. Re-seeds session every 40 requests to prevent stale cookies
4. Applies 1.5-second delays between batches

### Output DataFrame Columns
| Column | Type | Description |
|--------|------|-------------|
| `symbol` | str | Stock ticker (e.g., 'RELIANCE') |
| `deliv_qty` | float | Delivered quantity (shares) |
| `deliv_pct` | float | Delivery % (0-100) |
| `close` | float | Closing price |
| `total_volume` | float | Total traded volume |

### Constants
```python
QUOTE_BATCH_SIZE = 5       # Symbols per batch
QUOTE_DELAY = 1.5          # Seconds between batches
SESSION_REFRESH = 40       # Re-seed session after this many requests
```

---

## 4. Quarterly Fundamentals — `src/data/fundamentals.py`

### What It Does
Fetches quarterly financial statement data from yfinance for computing Beneish M-Score, Cash Conversion Ratio, and earnings gate checks.

### Data Points Retrieved (per stock)
| Field | Source | Used For |
|-------|--------|----------|
| `cfo` | Cash Flow Statement | CCR (CFO/EBITDA) |
| `ebitda` | Income Statement | CCR denominator |
| `net_income` | Income Statement | EPS proxy, margin check |
| `receivables_t` / `receivables_t1` | Balance Sheet | DSRI (M-Score) |
| `sales_t` / `sales_t1` | Income Statement | QoQ sales, SGI (M-Score) |
| `total_assets` | Balance Sheet | AQI, TATA (M-Score) |
| `debt_t` / `debt_t1` | Balance Sheet | LVGI (M-Score), D/E ratio |
| `current_assets_t` / `current_assets_t1` | Balance Sheet | AQI (M-Score) |
| `ppe_t` / `ppe_t1` | Balance Sheet | AQI (M-Score) |
| `debt_equity` | Computed | Stage 1B filter |

### Fetch Flow
```
get_fundamentals_batch(symbols)
    │
    ├─ For each symbol (rate-limited ~2 req/sec):
    │   │
    │   └─ get_fundamentals(symbol)
    │       ├─ yfinance.Ticker(f'{symbol}.NS')
    │       ├─ Access .quarterly_financials, .quarterly_balance_sheet, .quarterly_cashflow
    │       ├─ Extract latest and previous quarter values
    │       └─ Return dict or None (if data unavailable)
    │
    └─ Return dict { symbol: fundamentals_dict | None }
```

### Handling Missing Data
- If yfinance returns no data for a stock → fundamentals = None
- Stocks with None fundamentals fail Stage 1A (conservative: exclude unknowns)
- ~15% of NSE 500 stocks may have incomplete yfinance data

### In-Memory Cache
```python
_fundamentals_cache = {}  # Cleared per pipeline run
```

---

## 5. FII/DII Institutional Flows — `src/data/nse_fiidii.py`

### What It Does
Fetches daily FII (Foreign Institutional Investor) and DII (Domestic Institutional Investor) net buy/sell data from NSE. Used in regime detection (BEAR if FII flight).

### Fetch Flow (4-tier fallback)
```
fetch_fiidii_flows(trade_date)
    │
    ├─ Tier 1: _fetch_from_nse()
    │   └─ Try multiple NSE API endpoints (3 different URLs)
    │
    ├─ Tier 2: _fetch_from_alternative()
    │   ├─ NSDL FPI data
    │   └─ MoneyControl alternative
    │
    ├─ Tier 3: _load_cache()
    │   └─ Load from .cache/fiidii_last.json
    │
    └─ Tier 4: Default zeros
        └─ Return {fii_net: 0, dii_net: 0, ...}
```

### Output Dict
| Field | Type | Description |
|-------|------|-------------|
| `fii_net` | float | FII net flow (INR Crore, negative = selling) |
| `dii_net` | float | DII net flow (INR Crore, positive = buying) |
| `fii_buy` | float | FII gross purchases |
| `fii_sell` | float | FII gross sales |
| `dii_buy` | float | DII gross purchases |
| `dii_sell` | float | DII gross sales |

### DataFrame Conversion
`build_fiidii_df(fii_data)` converts the dict into a single-row DataFrame with `fii_net`, `dii_net`, and `dii_net_30d` columns for regime detection input.

---

## 6. Promoter Pledge Data — `src/data/nse_pledge.py`

### What It Does
Fetches promoter share pledging data from NSE shareholding patterns. Used in Stage 1A forensic gate.

### Fetch Flow
```
get_pledge_data_batch(symbols)
    │
    ├─ For each symbol (rate-limited 2 req/sec):
    │   │
    │   └─ get_pledge_data(symbol)
    │       ├─ Fetch NSE shareholding pattern
    │       ├─ _extract_pledge_pct(data) → current quarter pledge %
    │       ├─ _extract_prev_pledge_pct(data) → previous quarter pledge %
    │       └─ Return { pledge_pct, pledge_delta_1q, data_available }
    │
    └─ Return dict { symbol: pledge_dict }
```

### Output Dict (per stock)
| Field | Type | Description |
|-------|------|-------------|
| `pledge_pct` | float | Current promoter pledge % (0-100) |
| `pledge_delta_1q` | float | Change vs previous quarter (pp) |
| `data_available` | bool | Whether data was successfully fetched |

### Handling Missing Data
If pledge data is unavailable for a stock, it defaults to `{pledge_pct: 0, pledge_delta_1q: 0, data_available: False}`, which passes the forensic gate (conservative: don't exclude unknowns on this single factor).

---

## 7. NSE Session Helper — `src/data/nse_session.py`

### What It Does
Provides a shared HTTP session factory for all NSE API calls. Seeds the session with homepage cookies to bypass basic anti-bot checks.

### Session Creation
```
create_nse_session()
    │
    ├─ Create requests.Session()
    ├─ Set browser-like headers (User-Agent, Accept, etc.)
    ├─ Visit NSE homepage (3 attempts, 3-second delays)
    ├─ Capture cookies set by Cloudflare/NSE
    ├─ Switch to API-specific headers
    └─ Return seeded Session
```

### Headers
```python
NSE_SEED_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ...',
    'Accept': 'text/html,application/xhtml+xml,...',
    'Accept-Language': 'en-US,en;q=0.9',
}
NSE_API_HEADERS = {
    'User-Agent': '...',
    'Accept': 'application/json',
    'Referer': 'https://www.nseindia.com/',
}
```

---

## 8. Sample Data Generator — `src/data/sample_data.py`

### What It Does
Generates test data without requiring Fyers API credentials. Used for local development, system tests, and when `FYERS_TOTP_KEY` is not configured.

### Sample Universe (50 stocks)
Covers 10 sectors with representative large-cap stocks:
- **IT**: INFY, TCS, WIPRO, HCLTECH, TECHM
- **Banking**: HDFCBANK, ICICIBANK, SBIN, KOTAKBANK, AXISBANK
- **Auto**: MARUTI, TATAMOTORS, M&M, BAJAJ-AUTO, HEROMOTOCO
- **Pharma**: SUNPHARMA, DRREDDY, CIPLA, DIVISLAB, BIOCON
- **Energy**: RELIANCE, ONGC, BPCL, IOC, NTPC
- **FMCG**: HINDUNILVR, ITC, NESTLEIND, BRITANNIA, DABUR
- **Metals**: TATASTEEL, HINDALCO, JSWSTEEL, VEDL, COALINDIA
- **Finance**: BAJFINANCE, BAJAJFINSV, SBILIFE, HDFCLIFE, ICICIPRULI
- **Infra**: LTIM, LT, ADANIENT, ADANIPORTS, ULTRACEMCO
- **Consumer**: TITAN, ASIANPAINT, PIDILITIND, HAVELLS, INDUSTOWER

### Data Generation Flow
```
generate_sample_ohlcv(symbols, days=504)
    │
    ├─ Try 1: _try_yfinance_ohlcv(symbols, days)
    │   ├─ Fetch from yfinance using .NS suffix
    │   ├─ Requires internet access
    │   └─ Returns real OHLCV data for available stocks
    │
    └─ Fallback: _generate_synthetic_ohlcv(symbols, days)
        ├─ Random walk with daily volatility 1-2.5%
        ├─ Sector-correlated moves (0.3-0.5 correlation)
        ├─ Realistic volume patterns
        └─ Returns synthetic OHLCV data
```

### Other Sample Generators
| Function | What It Generates |
|----------|------------------|
| `generate_sample_index_data()` | Nifty 500, VIX (12-22), USD/INR (80-92) |
| `generate_sample_bhavcopy()` | 50% default delivery for all stocks |
| `generate_sample_fundamentals()` | Randomized but plausible financial ratios |
| `generate_sample_fii_data()` | Synthetic FII/DII flows |
| `generate_sample_pledge_data()` | 0-3% pledge with small deltas |

---

## Parallel Fetch Architecture

In the daily pipeline, three independent data fetches run concurrently via `ThreadPoolExecutor`:

```python
# src/main.py :: _parallel_fetch()
with ThreadPoolExecutor(max_workers=3) as executor:
    futures = {
        executor.submit(_fetch_fiidii): 'fiidii',
        executor.submit(_fetch_fundamentals): 'fundamentals',
        executor.submit(_fetch_pledge): 'pledge',
    }
```

OHLCV and index data are fetched sequentially before this (they require the Fyers instance and are needed to determine the last trading date for bhavcopy).

### Full Data Loading Sequence
```
1. Sequential: Fyers authentication
2. Sequential: Load universe (CSV read)
3. Sequential: fetch_index_data() → Nifty, VIX, USD/INR
4. Sequential: fetch_all_ohlcv() → 500 stocks OHLCV
5. Sequential: Detect last trading date from OHLCV
6. Sequential: fetch_bhavcopy() → delivery volume
7. Parallel: fetch_fiidii_flows() + get_fundamentals_batch() + get_pledge_data_batch()
```

---

## Standardized Data Dict

All data loaders return a standardized dict consumed by `run_full_pipeline()`:

```python
{
    'ohlcv_data':         dict[str, pd.DataFrame],  # {symbol: DataFrame[open,high,low,close,volume]}
    'bhavcopy_df':        pd.DataFrame,              # [symbol, deliv_qty, deliv_pct, close, total_volume]
    'fundamentals':       dict[str, dict | None],    # {symbol: financials_dict}
    'pledge_data':        dict[str, dict],            # {symbol: {pledge_pct, pledge_delta_1q}}
    'sector_map':         dict[str, str],             # {symbol: sector_name}
    'last_trading_date':  date,                       # Most recent trading date
    'regime_data': {
        'nifty_df':       pd.DataFrame,              # Nifty 500 OHLCV
        'vix_df':         pd.DataFrame,              # India VIX OHLCV
        'usdinr_df':      pd.DataFrame,              # USD/INR OHLCV
        'fii_df':         pd.DataFrame,              # FII/DII flows
    }
}
```

Both `_load_live_data()` and `_load_sample_data()` produce this exact structure, making the pipeline source-agnostic.

---

## Date Slicing for Historical Analysis

When `target_date` is specified (for system tests or backtest), all data is sliced to that date:

```python
# src/main.py :: _slice_ohlcv()
def _slice_ohlcv(ohlcv_data, as_of_date):
    for symbol, df in ohlcv_data.items():
        cut = df[df.index <= as_of_date]
        if len(cut) >= 100:  # Keep only if sufficient history
            sliced[symbol] = cut

# src/main.py :: _slice_index()
def _slice_index(index_data, as_of_date):
    for key, df in index_data.items():
        sliced[key] = df[df.index <= as_of_date]
```

This prevents look-ahead bias in historical testing while reusing the exact same pipeline code.

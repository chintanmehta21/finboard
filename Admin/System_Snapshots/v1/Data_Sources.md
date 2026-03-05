# Finboard v1 — Data Sources

Snapshot as of 2026-03-05. This documents where every piece of data in the system comes from, what format it arrives in, and what alternatives exist.

---

## 1. Stock OHLCV (Open, High, Low, Close, Volume)

**Source**: Fyers API v3 (`src/data/fyers_client.py`)
- Endpoint: `fyers.history()` via `fyers_apiv3` Python SDK
- Symbol format: `NSE:RELIANCE-EQ`
- Resolution: Daily candles
- History: 2 years (chunked into 365-day API requests because Fyers limits max range per call)
- Rate limit: 8 requests/sec (self-imposed; Fyers allows 10/sec)
- Authentication: TOTP headless login via `src/auth/token_manager.py`

**Output**: Dict `{symbol: DataFrame}` where DataFrame has index=date, columns=[open, high, low, close, volume]

**Fallback (sample mode)**: When `FYERS_TOTP_KEY` is not configured, the system falls back to `src/data/sample_data.py` which tries yfinance first (`{symbol}.NS`), then generates synthetic OHLCV using log-normal random walks. This is used for CI/CD testing but produces unreliable analysis results.

**Alternate source that could work**: yfinance (`{symbol}.NS`) provides the same daily OHLCV, but it's slower (no batch API), has intermittent failures, and occasionally returns stale data. Fyers is significantly more reliable for Indian equities.

---

## 2. Nifty 500 Index Data

**Source**: Fyers API v3
- Symbol: `NSE:NIFTY500-INDEX`
- Same chunked fetch as stocks
- Used for: 200 DMA calculation, Mansfield Relative Strength benchmark, regime detection

**Fallback**: yfinance `^CRSLDX` (CRSP-like proxy) or synthetic data in sample mode.

---

## 3. India VIX

**Source**: Fyers API v3
- Symbol: `NSE:INDIAVIX-INDEX`
- Used for: Regime detection (VIX > 24 = BEAR, VIX 16-24 = SIDEWAYS), VIX-adaptive stop tightening

**Fallback**: yfinance `^INDIAVIX` in sample mode. Default value of 18 (moderate) if all sources fail.

---

## 4. USD/INR Exchange Rate

**Source (PRIMARY)**: yfinance (`src/data/fyers_client.py :: _fetch_usdinr_yfinance()`)
- Ticker: `USDINR=X`
- This was changed to primary because yfinance reliably returns this data (tested: 517 candles, latest 92.01)
- Used for: Regime detection (30-day INR depreciation > 2% triggers BEAR), macro dashboard display

**Source (FALLBACK)**: Fyers API `NSE:USDINR-INDEX` — tried only if yfinance returns empty

**Known issue**: We previously had a hardcoded default of 87.0 in config. This has been removed. If both yfinance and Fyers fail, the value will be 0 and the regime detection will skip the INR depreciation check (safe default behavior since `0 / 0` check returns 0% move).

---

## 5. Delivery Volume (Bhavcopy)

**Source**: NSE India Quote API (`src/data/nse_bhavcopy.py`)
- Endpoint: `https://www.nseindia.com/api/quote-equity?symbol={symbol}`
- Fetched per-symbol (not bulk) because bulk bhavcopy ZIP downloads are unreliable
- Fields extracted: `deliveryQuantity`, `deliveryToTradedQuantity` (delivery %)
- Rate limit: 5 symbols per batch, 1.5s delay, session refresh every 40 requests
- Requires NSE session with Cloudflare cookies

**Fallback 1**: NSE archives bulk CSV (`CM-UDiFF-*` format) — but this endpoint has been unreliable since late 2025, frequently returning 404.

**Fallback 2**: Synthetic 50% delivery percentage — allows pipeline to continue but produces neutral delivery conviction scores.

**Used for**: Delivery Conviction factor (Factor 2 in the 5-factor model). Stocks with rising delivery % relative to their 20-day average show institutional accumulation.

---

## 6. FII/DII Institutional Flows

**Source (PRIMARY)**: NSE India API (`src/data/nse_fiidii.py`)
- Endpoint: `https://www.nseindia.com/api/fiidiiTradeReact`
- Returns JSON with FPI/DII buy/sell values in crores
- Requires NSE session (Cloudflare bypass)
- **Critical fix**: Accept-Encoding must NOT include `br` (brotli) — the requests library cannot decode brotli natively, causing garbled responses

**Source (FALLBACK 1)**: NSDL FPI reports — HTML parsing of `fpi.nsdl.co.in` daily reports
**Source (FALLBACK 2)**: MoneyControl API — `api.moneycontrol.com/mcapi/v1/fii-dii/overview`
**Source (FALLBACK 3)**: Local cache at `.cache/fiidii_last.json` — last successful fetch

**Output**: Dict with `fii_net`, `dii_net`, `fii_buy`, `fii_sell`, `dii_buy`, `dii_sell` (all in INR crores)

**Used for**: Regime corroboration (DII buying = downside protection), macro dashboard display

**Current status**: Working. Latest test: FII net = -3,753 Cr, DII net = +5,153 Cr.

---

## 7. Quarterly Fundamentals

**Source**: yfinance (`src/data/fundamentals.py`)
- Ticker: `{symbol}.NS`
- Data fetched: Quarterly income statement, balance sheet, cash flow
- Fields: CFO, EBITDA, Net Income, Revenue (t and t-4Q), Receivables, Total Assets, Debt, Equity, Current Assets, PPE
- Rate limit: ~2 requests/sec with 0.5s delays
- In-memory cache per run

**Known limitation**: yfinance quarterly data availability for Indian stocks is inconsistent. In a 50-symbol test run, only 4 out of 50 symbols returned usable quarterly data. This is the single biggest data quality issue in the system — it means most stocks fail at Stage 1A (forensic gate) because missing fundamentals = automatic exclusion (conservative approach).

**Alternate sources that could work**:
- Screener.in API (unofficial) — better coverage for Indian quarterly data
- BSE India financial results API — official but requires additional parsing
- Trendlyne/Tickertape APIs — paid, but comprehensive
- Direct NSE XBRL filings — most complete, but complex to parse

**Used for**: M-Score (5 ratios), CCR (CFO/EBITDA), D/E ratio, Earnings gate (QoQ sales, EPS growth)

---

## 8. Promoter Pledge Data

**Source**: NSE India Shareholding API (`src/data/nse_pledge.py`)
- Endpoint: NSE shareholding pattern API per symbol
- Fields: Current pledge %, QoQ change
- Rate limit: 2/sec, 1.0s delay

**Current status**: Mostly returning no data (0/50 in test run). The NSE endpoint for pledge data appears to be unreliable or the response format may have changed.

**Fallback**: Default `{pledge_pct: 0, pledge_delta: 0, data_available: False}` — this means the pledge gate in forensic_pass() effectively doesn't filter anything (0% pledge always passes).

**Used for**: Forensic gate Stage 1A (pledge < 5% and delta < 2pp)

---

## 9. NSE 500 Universe Constituent List

**Source (PRIMARY)**: NSE Archives static CSV
- URL: `https://archives.nseindia.com/content/indices/ind_nifty500list.csv`
- No Cloudflare blocking (direct download)
- Contains: Symbol, Company Name, Industry/Sector
- Saved to: `data/nse500_constituents.csv`

**Source (FALLBACK)**: NSE India API `equity-stockIndices?index=NIFTY%20500` — blocked by Cloudflare in testing.

**Auto-refresh logic**: Downloads automatically if CSV is missing, older than 90 days, or has < 100 symbols.

**Current status**: Working. Downloaded 500 symbols successfully.

---

## 10. Sector Mapping

**Source**: Same CSV as universe (`data/nse500_constituents.csv`, SECTOR column)
- Loaded by `universe.py :: get_sector_map()`
- Maps each symbol to its SEBI sector classification

**Used for**: Sector concentration caps (max 25% in one sector), defensive rotation filtering (FMCG, Pharma, IT sectors)

---

## Summary Table

| Data | Primary Source | Fallback(s) | Status |
|------|---------------|-------------|--------|
| Stock OHLCV | Fyers API | yfinance, synthetic | Working |
| Nifty 500 Index | Fyers API | yfinance | Working |
| India VIX | Fyers API | yfinance, default 18 | Working |
| USD/INR | yfinance USDINR=X | Fyers NSE:USDINR-INDEX | Working (92.01) |
| Delivery Volume | NSE Quote API | NSE bulk CSV, synthetic 50% | Working (47/50) |
| FII/DII Flows | NSE fiidiiTradeReact | NSDL, MoneyControl, cache | Working (-3753/+5153) |
| Fundamentals | yfinance quarterly | None currently | Partial (4/50 in test) |
| Pledge Data | NSE shareholding API | Default zeros | Not working (0/50) |
| Universe | NSE Archives CSV | NSE API | Working (500 symbols) |
| Sector Map | Universe CSV | None | Working |

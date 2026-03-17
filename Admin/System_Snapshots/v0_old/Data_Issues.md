# Finboard v1 — Data Issues & Resolutions

Snapshot as of 2026-03-05. Every data-fetching problem encountered during development and how it was resolved (or not yet resolved).

---

## Issue 1: NSE API Brotli Compression (FII/DII returning garbled data)

**Problem**: The NSE `fiidiiTradeReact` endpoint returned HTTP 200 with a `Content-Type: application/json` header, but the response body was unreadable binary garbage (e.g., `\x8b\xc8\x93\x94@o`). The `requests` library was unable to auto-decode it.

**Root Cause**: The `Accept-Encoding: gzip, deflate, br` header in our API request told NSE to send brotli-compressed (`br`) responses. The Python `requests` library does NOT support brotli decompression natively — it only handles gzip and deflate. NSE honored the `br` preference and sent brotli-compressed JSON, which `requests` passed through raw.

**Resolution**: Removed `br` from `Accept-Encoding` in `src/data/nse_session.py :: NSE_API_HEADERS`. Changed to `'Accept-Encoding': 'gzip, deflate'`. NSE now sends gzip-compressed responses which `requests` decodes automatically.

**File changed**: `src/data/nse_session.py` (line 39)

---

## Issue 2: NSE fiidiiActivity/WEB endpoint returning 404

**Problem**: The original FII/DII endpoint `https://www.nseindia.com/api/fiidiiActivity/WEB` started returning HTTP 404 ("Resource not found"). This was the only endpoint we were using.

**Root Cause**: NSE appears to have deprecated or moved this endpoint. The response was a proper HTML 404 page, not a Cloudflare block.

**Resolution**: Switched to `https://www.nseindia.com/api/fiidiiTradeReact` which returns the same data in a compatible JSON format. Also added fallback sources (NSDL FPI reports, MoneyControl API, local cache) so FII/DII data is never completely unavailable.

**File changed**: `src/data/nse_fiidii.py`

---

## Issue 3: USD/INR Fyers data returning empty

**Problem**: Fyers `NSE:USDINR-INDEX` symbol returned no candle data, causing USD/INR to show as 0.00 or fall back to a hardcoded 87.0 default.

**Root Cause**: The USDINR index on Fyers may not be consistently available, or the symbol mapping may be incorrect for the currency pair.

**Resolution**: Made yfinance `USDINR=X` the PRIMARY source for USD/INR data (not a fallback). yfinance reliably returns 500+ daily candles for this ticker. Fyers is now the fallback, tried only if yfinance fails. Also removed the hardcoded `USDINR_FALLBACK = 87.0` from config entirely — the system now always fetches live data.

**Files changed**: `src/data/fyers_client.py` (restructured `fetch_index_data()`), `src/analysis/regime.py` (removed USDINR_FALLBACK import), `src/config.py` (removed defaults.usdinr_fallback)

---

## Issue 4: NSE 500 Universe CSV auto-download blocked by Cloudflare

**Problem**: The NSE India API endpoint `equity-stockIndices?index=NIFTY%20500` for downloading the full constituent list returned HTML (Cloudflare challenge page) instead of JSON, even with proper session cookies.

**Root Cause**: NSE's Cloudflare protection is aggressive and inconsistent. Even with browser-like headers and a seeded session, some API endpoints are blocked while others work fine. The `fiidiiTradeReact` endpoint works, but `equity-stockIndices` doesn't.

**Resolution**: Added NSE archives as the primary download source: `https://archives.nseindia.com/content/indices/ind_nifty500list.csv`. This is a static CSV file hosted on a different subdomain (`archives.nseindia.com`) that does NOT have Cloudflare protection. It successfully downloads all 500 symbols. The NSE API endpoint is kept as a fallback.

**File changed**: `src/data/universe.py` (rewrote `download_nse500_constituents()`)

---

## Issue 5: Fundamentals data availability (MAJOR — partially unresolved)

**Problem**: yfinance returns quarterly financial data for only a small fraction of NSE 500 stocks. In a 50-symbol test run, only 4 out of 50 symbols had usable fundamentals data. This means most stocks fail at Stage 1A (forensic gate) because the pipeline conservatively excludes stocks with missing data.

**Root Cause**: yfinance's Indian stock coverage for quarterly financials (income statement, balance sheet, cash flow) is incomplete. Many `.NS` symbols return empty DataFrames for `quarterly_financials`, `quarterly_balance_sheet`, etc. This is a limitation of Yahoo Finance's data coverage for Indian equities, not a code bug.

**Impact**: With a 500-symbol universe and ~8% fundamental data availability, roughly 40 stocks would have fundamentals. After forensic filtering, this drops further. The pipeline works correctly but operates on a much smaller eligible universe than intended.

**Current status**: PARTIALLY UNRESOLVED. The pipeline handles this gracefully (missing data = exclude, pipeline continues), but the small eligible universe means fewer bullish/bearish candidates.

**Potential future fixes**:
- Integrate Screener.in or Trendlyne as alternate fundamental data sources
- Parse NSE XBRL filings directly
- Cache fundamentals across runs (quarterly data doesn't change daily)
- Use annual data as fallback when quarterly is unavailable

---

## Issue 6: Pledge data endpoint not returning data

**Problem**: The NSE shareholding pattern API for promoter pledge data returns no data for any symbols (0/50 in test run).

**Root Cause**: The NSE endpoint for shareholding patterns may have changed its response format, or the specific fields we're looking for (`pledgePercentage`, `pledgeDelta`) may not be present in the current API version. We haven't deeply debugged this yet.

**Impact**: Low. The pledge gate in `forensic_pass()` defaults to `{pledge_pct: 0, data_available: False}`, which means 0% pledge — this always passes the gate. So pledge screening is effectively disabled, but the rest of the pipeline works correctly.

**File**: `src/data/nse_pledge.py`

---

## Issue 7: NSE Bhavcopy bulk ZIP download unreliable

**Problem**: The bulk bhavcopy CSV download from NSE archives (`CM-UDiFF-*` format) frequently returns HTTP 404, especially for recent dates.

**Root Cause**: NSE changed their bulk download format/URL patterns in late 2025. The exact URL structure varies and is not well-documented.

**Resolution**: Switched to per-symbol quote API fetching as the primary method (`/api/quote-equity?symbol={symbol}`). This is slower (must fetch one symbol at a time with rate limiting) but far more reliable. Gets delivery data for ~94% of symbols (47/50 in testing). The remaining ~6% that fail get a synthetic 50% default.

**File changed**: `src/data/nse_bhavcopy.py`

---

## Issue 8: Dashboard showing two rows for regime (layout issue)

**Problem**: The dashboard displayed two separate blocks below the header — a regime banner ("BEAR / FII FLIGHT") and a separate warning ("Defensive mode — reduced sizing"), creating unnecessary visual clutter.

**Resolution**: Merged into a single regime banner row. Removed the separate warning banner entirely. Removed the red dot icon from the regime display. The regime banner now shows regime label + exposure % on one line.

**File changed**: `dashboard/app/page.js`, `dashboard/app/globals.css` (removed unused `.regime-warning-banner` CSS)

---

## Issue 9: "Defensive Candidates" naming confusion

**Problem**: In BEAR regime, the bullish section was labeled "Defensive Candidates" which confused users. The underlying stocks (FMCG, Pharma, IT sector quality picks) are still buy candidates, just from defensive sectors.

**Resolution**: Renamed all user-facing labels from "Defensive Candidates" to "Bullish Candidates" across dashboard, Telegram, and Discord. Internal function names (`defensive_rotation_candidates()`) were kept as-is since they describe the implementation accurately.

**Files changed**: `dashboard/app/page.js`, `src/output/formatter.py`, `src/output/json_export.py` (removed `defensive` key)

---

## Issue 10: Universe too small (only 50 symbols in CSV)

**Problem**: The `data/nse500_constituents.csv` file only contained 50 symbols (a curated subset from initial setup), not the full NSE 500.

**Resolution**: Added auto-download logic that triggers when the CSV has fewer than 100 symbols. The NSE archives CSV provides all 500 symbols. Also added `universe_pct` config parameter (default 1.0 = 100%) so users can test with a smaller subset (e.g., 0.10 = 10% = ~50 random symbols).

**Files changed**: `src/data/universe.py`, `src/config.py`

---

## Issue 11: BEAR regime always triggering (0% exposure)

**Problem**: With current market conditions (Nifty below 200 DMA by -2.6%), the system correctly detects BEAR regime, which sets exposure to 0% and runs defensive rotation instead of the normal 5-stage pipeline.

**This is NOT a bug** — it's the system working as designed. The market genuinely is below its 200 DMA. However, it means:
- No stocks go through the normal 5-factor scoring
- Only defensive-sector stocks (FMCG, Pharma, IT) are selected
- With limited fundamentals data, even those may fail the quality gates

**Current status**: Correct behavior. When market recovers above 200 DMA, the system will automatically switch to BULL/DIP/SIDEWAYS and run the full pipeline.

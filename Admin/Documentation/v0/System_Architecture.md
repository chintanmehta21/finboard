# Finboard v2.0 — System Architecture

**Snapshot Date**: 2026-03-17
**Status**: Production-Ready (fully automated, zero-cost)

---

## Overview

Finboard is a fully automated TechnoFundamental signal system for NSE 500 stocks. It runs a 5-stage quantitative analysis pipeline daily, detecting market regime, filtering for quality, ranking by multi-factor scores, and delivering actionable buy/sell signals via Telegram, Discord, and a web dashboard.

The entire system operates at zero cost using free-tier services: GitHub Actions (compute), Fyers API (market data), yfinance (fundamentals), NSE scraping (delivery/pledge/FII data), Telegram Bot API, Discord webhooks, and Vercel (dashboard hosting).

---

## High-Level Architecture Diagram

```
                         GitHub Actions (Cron)
                    Mon-Fri 9:00 PM IST (analyze.yml)
                    Friday 10:00 PM IST (backtest.yml)
                                │
                                ▼
                     ┌─────────────────────┐
                     │   src/main.py        │
                     │   (Orchestrator)     │
                     └────────┬────────────┘
                              │
               ┌──────────────┼──────────────┐
               ▼              ▼              ▼
        ┌──────────┐   ┌──────────┐   ┌──────────────┐
        │  Auth    │   │  Data    │   │  Analysis    │
        │  Module  │   │  Module  │   │  Pipeline    │
        └──────────┘   └──────────┘   └──────────────┘
        Fyers TOTP      5 Sources       5 Stages
        Headless        Parallel         Regime
        + Token Cache   Fetch            Overlay
                              │
                              ▼
               ┌──────────────┼──────────────┐
               ▼              ▼              ▼
        ┌──────────┐   ┌──────────┐   ┌──────────────┐
        │ Telegram │   │ Discord  │   │ JSON Export   │
        │ Bot API  │   │ Webhook  │   │ → signals.json│
        └──────────┘   └──────────┘   └──────┬───────┘
                                              │
                                     git commit + push
                                              │
                                              ▼
                                     ┌────────────────┐
                                     │  Vercel CDN    │
                                     │  (Dashboard)   │
                                     │  Next.js SSG   │
                                     └────────────────┘
```

---

## Component Map

| Component | Technology | Location | Purpose |
|-----------|-----------|----------|---------|
| **Orchestrator** | Python 3.11 | `src/main.py` | Coordinates all pipeline phases |
| **Authentication** | pyotp + requests | `src/auth/token_manager.py` | Fyers TOTP headless login |
| **Data: OHLCV** | Fyers API v3 | `src/data/fyers_client.py` | 2-year daily price/volume |
| **Data: Delivery** | NSE Bhavcopy | `src/data/nse_bhavcopy.py` | Delivery volume % |
| **Data: Fundamentals** | yfinance | `src/data/fundamentals.py` | Quarterly financials |
| **Data: FII/DII** | NSE API | `src/data/nse_fiidii.py` | Institutional flows |
| **Data: Pledge** | NSE API | `src/data/nse_pledge.py` | Promoter pledge % |
| **Data: Universe** | NSE CSV | `src/data/universe.py` | NSE 500 constituent list |
| **Data: Sample** | yfinance + synthetic | `src/data/sample_data.py` | Test mode data |
| **Analysis: Pipeline** | pandas + numpy | `src/analysis/pipeline.py` | 5-stage orchestration |
| **Analysis: Forensic** | Custom formulas | `src/analysis/forensic.py` | M-Score, CCR, pledge gates |
| **Analysis: Factors** | Custom formulas | `src/analysis/factors.py` | 5-factor scoring |
| **Analysis: Regime** | Custom logic | `src/analysis/regime.py` | 4-state macro detection |
| **Analysis: Exit** | Custom rules | `src/analysis/exit_rules.py` | 4 independent triggers |
| **Analysis: Portfolio** | Custom sizing | `src/analysis/portfolio.py` | ATR sizing + constraints |
| **Analysis: Bearish** | Custom model | `src/analysis/bearish.py` | Short + defensive candidates |
| **Analysis: Targets** | ATR-based | `src/analysis/price_targets.py` | Entry/exit price levels |
| **Analysis: Correlation** | Pearson | `src/analysis/factor_correlation.py` | Multicollinearity check |
| **Output: Telegram** | Bot API | `src/output/telegram_bot.py` | HTML-formatted alerts |
| **Output: Discord** | Webhook | `src/output/discord_bot.py` | Markdown-formatted alerts |
| **Output: JSON** | File I/O | `src/output/json_export.py` | Dashboard data export |
| **Output: Formatter** | String templates | `src/output/formatter.py` | Shared message formatting |
| **Config** | Python dict | `src/config.py` | System constants |
| **Key Loader** | python-dotenv | `src/utils/key_loader.py` | Credential management |
| **Dashboard** | Next.js 14 | `dashboard/` | Static web dashboard |
| **CI/CD: Daily** | GitHub Actions | `.github/workflows/analyze.yml` | Daily pipeline cron |
| **CI/CD: Backtest** | GitHub Actions | `.github/workflows/backtest.yml` | Weekly backtest |
| **Test: SystemTest** | Python | `Tests/SystemTest/` | End-to-end validation |
| **Test: Backtest** | Python | `Tests/backtest/` | Walk-forward simulation |

---

## Data Flow (Daily Pipeline)

### Phase 1: Authentication
```
Admin/.env (local) OR GitHub Secrets (CI)
    → key_loader.py loads credentials
    → token_manager.py authenticates with Fyers
    → Cached tokens reused (< 23 hours)
    → Fallback: full TOTP re-auth (< 5 seconds)
```

### Phase 2: Data Ingestion (Parallel)
```
ThreadPoolExecutor (3 workers):
    ├─ fetch_all_ohlcv()     → 500 stocks × 2 years OHLCV (rate: 8 req/sec)
    ├─ fetch_index_data()    → Nifty 500, VIX, USD/INR candles
    ├─ fetch_bhavcopy()      → Today's delivery volume (NSE quote API)
    ├─ get_fundamentals()    → Quarterly financials (yfinance, ~2 req/sec)
    ├─ fetch_fiidii_flows()  → FII/DII net flows (NSE API + cache fallback)
    └─ get_pledge_data()     → Promoter pledge % (NSE shareholding API)
```

### Phase 3: Analysis (5-Stage Pipeline)
```
Stage 1A: Forensic Filter
    → Beneish M-Score < -2.22
    → Cash Conversion Ratio >= 0.80
    → Promoter Pledge < 5% AND delta < +2pp

Stage 1B: Liquidity & Clean Books
    → Average Daily Turnover > INR 10 Crore
    → Worst-5-day stress test (5th percentile ADT > 50% of threshold)
    → Debt/Equity < 1.5

Stage 1C: Point-in-Time Earnings Gate
    → QoQ Sales Growth > 0%
    → EPS Growth Proxy > 10% over 2 quarters

Stage 2: Multi-Factor Ranking
    → 5 factors: Mansfield RS, Delivery Conviction, VAM, Forensic Quality, Revision Breadth
    → Percentile-ranked within eligible universe
    → Regime-adaptive factor weights applied
    → Confidence score = weighted sum (0-100 scale)

Stage 3: Macro & Regime Overlay
    → 4 regimes: BULL (100%), DIP (60%), SIDEWAYS (30%), BEAR (0%)
    → Adjusted confidence = confidence × regime_scalar
    → VIX > 20: stops tightened 30%, time stop shortened to 13 weeks
    → BEAR regime: routes to defensive rotation (quality + momentum)
```

### Phase 4: Output
```
Telegram Bot API  → Top 5 bullish + bearish, HTML format, chunked at 4000 chars
Discord Webhook   → Top 5 bullish + bearish, Markdown format, chunked at 1900 chars
JSON Export       → Top 10 bullish + bearish → dashboard/public/data/signals.json
                  → Backup to signals_prev.json before overwrite
```

### Phase 5: Deployment
```
GitHub Actions auto-commit → signals.json pushed to main branch
Vercel detects push        → Rebuilds Next.js static export
CDN serves updated dashboard
```

---

## Key Design Principles

1. **Single Entry Point**: `run_analysis()` in `src/main.py` is the only way to run analysis. Daily cron, system tests, and any future consumer call the same function. When pipeline logic changes, all callers automatically get updated behavior.

2. **Standardized Data Dict**: All data loaders (`_load_live_data()`, `_load_sample_data()`) return an identical dict structure with keys: `ohlcv_data`, `bhavcopy_df`, `fundamentals`, `pledge_data`, `sector_map`, `last_trading_date`, `regime_data`. The pipeline never knows or cares where data came from.

3. **Continuous Regime (Not Binary)**: Regime is a scalar 0.0-1.0 instead of on/off. This eliminates whipsaw around the 200 DMA boundary. Factor weights shift smoothly across regimes.

4. **Fail-Safe Defaults**: Missing data gracefully degrades (synthetic delivery %, neutral VIX, zero FII flows). The pipeline never crashes due to a single data source being unavailable.

5. **Zero Cost**: Every service used is free-tier: GitHub Actions (2000 min/month), Fyers API (free for retail), yfinance (open-source), NSE public data, Telegram/Discord free APIs, Vercel free tier.

6. **No Logic Duplication**: Tests call the real pipeline (`run_full_pipeline()` or `run_analysis()`), not simplified copies. Walk-forward backtest uses the exact same analysis code path.

---

## Error Handling & Resilience

| Scenario | Handling |
|----------|----------|
| Fyers token expired | Auto re-auth via TOTP (< 5 sec) |
| NSE blocks scraping | Session cookie seeding + browser-like headers + exponential backoff |
| yfinance returns no data | Stock excluded at Stage 1A (conservative) |
| Market holiday (no bhavcopy) | Holiday message sent, pipeline exits cleanly |
| GitHub Actions cache evicted | Full TOTP re-auth is fast, no pipeline impact |
| Push conflict (concurrent commits) | `git pull --rebase origin main` before push |
| Factor multicollinearity | Pairwise Pearson check (max 0.60 threshold) warns in logs |
| VIX spike (> 20) | Stops auto-tightened 30%, time stop shortened to 13 weeks |
| BEAR regime detected | Pipeline routes to defensive rotation instead of normal 5-factor |

---

## Performance Characteristics

| Metric | Value |
|--------|-------|
| Daily pipeline runtime | ~29 minutes (GitHub Actions) |
| Weekly backtest (52 weeks) | ~60 minutes |
| OHLCV fetch (500 stocks) | ~8 minutes (rate-limited 8 req/sec) |
| Bhavcopy fetch (500 stocks) | ~10 minutes (NSE quote API, 5 per batch) |
| Fundamentals fetch | ~5 minutes (yfinance, ~2 req/sec) |
| Pipeline computation | < 30 seconds |
| JSON export + commit | < 10 seconds |
| Vercel rebuild | ~30 seconds |

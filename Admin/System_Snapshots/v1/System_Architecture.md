# Finboard v1 — System Architecture

Snapshot as of 2026-03-05. Full technical architecture of the automated NSE quant signal system.

---

## High-Level Overview

Finboard is a fully automated quantitative stock analysis system for the Indian equity market (NSE 500). It runs daily before market open, analyzes 500 stocks through a 5-stage pipeline, and outputs buy/sell candidates to Telegram, Discord, and a web dashboard.

**Cost**: 100% free tier. No paid APIs, no cloud compute bills.

```
GitHub Actions (cron 4:15 PM IST, Mon-Fri)
    |
    v
Python Pipeline (src/main.py)
    |
    +-- Data Ingestion --> Fyers API, NSE India, yfinance
    |
    +-- 5-Stage Analysis --> src/analysis/*
    |
    +-- Output
        +-- Telegram Bot API
        +-- Discord Webhook
        +-- signals.json --> git commit --> Vercel (dashboard)
```

---

## Execution Environment

### Production (GitHub Actions)
- **Trigger**: Cron schedule `15 10 * * 1-5` (4:15 PM IST / 10:45 AM UTC, Mon-Fri)
  - NSE market closes at 3:30 PM IST
  - Bhavcopy available ~4:00 PM IST
  - Pipeline runs at 4:15 PM IST, ~45 min after market close
- **Runner**: ubuntu-latest (GitHub-hosted)
- **Python**: 3.11
- **Secrets**: All API keys stored as GitHub Secrets, injected as environment variables
- **Output**: After pipeline completes, signals.json is committed and pushed to the repo. Vercel auto-deploys on push.

### Local Development
- **OS**: Windows 11
- **Python**: 3.11
- **Keys**: Stored in `Admin/.env` (gitignored), loaded by `python-dotenv`
- **Dashboard dev**: `npm run dev` in `dashboard/` directory (Next.js dev server on port 3000)

---

## Module Structure

```
QuantSystem_v1/
├── src/
│   ├── main.py                    # Entry point, orchestrates everything
│   ├── config.py                  # System-wide constants
│   ├── auth/
│   │   └── token_manager.py       # Fyers TOTP headless authentication
│   ├── data/
│   │   ├── fyers_client.py        # Stock OHLCV + index data from Fyers
│   │   ├── nse_bhavcopy.py        # Delivery volume data from NSE
│   │   ├── nse_fiidii.py          # FII/DII institutional flows from NSE
│   │   ├── nse_pledge.py          # Promoter pledge data from NSE
│   │   ├── nse_session.py         # Shared NSE HTTP session (Cloudflare bypass)
│   │   ├── universe.py            # NSE 500 constituent list management
│   │   ├── fundamentals.py        # Quarterly financials from yfinance
│   │   └── sample_data.py         # Synthetic/yfinance fallback for testing
│   ├── analysis/
│   │   ├── pipeline.py            # 5-stage pipeline orchestrator
│   │   ├── forensic.py            # Stage 1A: M-Score, CCR, Pledge gates
│   │   ├── factors.py             # Stage 2: 5 factor calculations
│   │   ├── regime.py              # Stage 3: 4-state regime detection
│   │   ├── bearish.py             # Bearish candidates + defensive rotation
│   │   ├── portfolio.py           # Position sizing (ATR-based)
│   │   ├── price_targets.py       # Target/stop calculation
│   │   ├── exit_rules.py          # 4 exit triggers
│   │   └── factor_correlation.py  # Pairwise correlation check
│   ├── output/
│   │   ├── formatter.py           # Telegram (HTML) + Discord (Markdown)
│   │   ├── telegram_bot.py        # Telegram Bot API delivery
│   │   ├── discord_bot.py         # Discord Webhook delivery
│   │   └── json_export.py         # Dashboard JSON export
│   └── utils/
│       └── key_loader.py          # Loads API keys from .env or env vars
├── dashboard/
│   ├── app/
│   │   ├── page.js                # Main dashboard React component
│   │   ├── globals.css            # All styles (dark theme)
│   │   └── layout.js              # Next.js root layout
│   └── public/
│       └── data/
│           ├── signals.json       # Current pipeline output
│           └── signals_prev.json  # Previous run backup
├── data/
│   └── nse500_constituents.csv    # NSE 500 symbol list (auto-updated)
├── Admin/
│   ├── .env                       # API keys (gitignored)
│   └── execution_plan.md          # Development phases tracker
├── .github/
│   └── workflows/
│       ├── analyze.yml            # Daily pipeline workflow
│       └── backtest.yml           # Backtest workflow
└── .cache/
    └── fiidii_last.json           # FII/DII cache (gitignored)
```

---

## Data Flow (Live Pipeline)

### Step 1: Authentication
- `token_manager.py :: get_fyers_instance()`
- Checks cached token first (< 23 hours old)
- If expired, performs TOTP headless login:
  1. Send OTP to Fyers API
  2. Generate TOTP from secret key
  3. Verify TOTP, then verify PIN
  4. Exchange auth_code for access token
- Returns authenticated `fyersModel.FyersModel` instance

### Step 2: Load Universe
- `universe.py :: load_universe()`
- Reads `data/nse500_constituents.csv`
- Auto-downloads from NSE archives if stale (> 90 days) or too small (< 100 symbols)
- Applies `UNIVERSE_PCT` config: 1.0 = all 500 symbols, 0.10 = random 50 for testing
- Returns list of symbol strings

### Step 3: Data Ingestion

**Sequential (Fyers rate-limited)**:
1. `fetch_index_data()` — Nifty 500, VIX from Fyers; USD/INR from yfinance
2. `fetch_all_ohlcv()` — 2-year daily OHLCV for all symbols, batched 8/sec
3. `fetch_bhavcopy()` — Delivery data from NSE quote API, per-symbol

**Parallel (ThreadPoolExecutor, 3 workers)**:
1. Thread 1: `fetch_fiidii_flows()` — FII/DII from NSE
2. Thread 2: `get_fundamentals_batch()` — Quarterly financials from yfinance
3. Thread 3: `get_pledge_data_batch()` — Pledge data from NSE

### Step 4: Analysis Pipeline
- `pipeline.py :: run_full_pipeline()`
- See Logic.md for detailed stage-by-stage explanation
- Outputs dict with bullish candidates, bearish candidates, regime info, macro snapshot

### Step 5: Output
- `telegram_bot.py :: send_signal_report()` — Formats + sends to Telegram
- `discord_bot.py :: send_signal_report()` — Formats + sends to Discord
- `json_export.py :: export_signals()` — Writes signals.json (backs up previous first)

---

## Key Management

**Local development**:
- All keys in `Admin/.env` (standard dotenv format)
- Loaded by `src/utils/key_loader.py` using `python-dotenv`
- `Admin/` is gitignored — never pushed to GitHub

**Production (GitHub Actions)**:
- Keys stored as GitHub Secrets
- Injected as environment variables by the workflow YAML
- `key_loader.py` falls back to `os.environ` when `.env` file doesn't exist

**Required keys**:
| Key | Purpose |
|-----|---------|
| `FYERS_APP_ID` | Fyers API application ID |
| `FYERS_SECRET` | Fyers API secret |
| `FYERS_CLIENT_ID` | Fyers trading account ID |
| `FYERS_TOTP_KEY` | TOTP secret for headless login |
| `FYERS_PIN` | 4-digit PIN for Fyers verification |
| `TELEGRAM_TOKEN` | Telegram Bot API token |
| `TELEGRAM_CHAT` | Telegram channel/chat ID |
| `DISCORD_WEBHOOK_URL` | Discord webhook URL |

---

## Dashboard Architecture

- **Framework**: Next.js 14 (App Router)
- **Rendering**: Client-side (`'use client'` directive)
- **Data**: Static JSON fetch from `/data/signals.json` on page load
- **Hosting**: Vercel (free tier, auto-deploy on git push)
- **Styling**: Vanilla CSS with CSS variables for theming
- **No backend**: Dashboard is purely read-only; data is updated by git commit from GitHub Actions

**Update cycle**:
1. GitHub Actions runs pipeline at 4:15 PM IST
2. Pipeline writes `signals.json` to `dashboard/public/data/`
3. GitHub Actions commits and pushes the updated file
4. Vercel detects the push and auto-deploys
5. Dashboard fetches the new JSON on next page load

---

## Error Handling & Resilience

### Fallback Chain Pattern
Every data source uses a fallback chain:
1. Primary source (e.g., Fyers, NSE API)
2. Secondary source (e.g., yfinance, alternate endpoint)
3. Cache (e.g., `.cache/fiidii_last.json`)
4. Safe default (e.g., 0, empty DataFrame, synthetic data)

### Pipeline Resilience
- Missing fundamentals → stock excluded (conservative)
- Missing delivery data → synthetic 50% default
- Missing pledge data → 0% pledge (passes gate)
- Bhavcopy unavailable → treated as market holiday, pipeline exits gracefully
- All API failures → error notifications sent to Telegram + Discord

### JSON Export Safety
- Previous `signals.json` backed up as `signals_prev.json` before overwriting
- If pipeline produces no data, the backup ensures dashboard always has something to display

---

## Configuration (`src/config.py`)

```python
SYSTEM_CONFIG = {
    "system": {
        "name": "Finboard",
        "version": "2.0",
        "full_name": "Finboard v2.0",
        "universe_pct": 1.0,    # 1.0 = 100%, 0.10 = 10% for testing
    },
    "output": {
        "telegram_top_n": 5,    # Max candidates in Telegram message
        "discord_top_n": 5,     # Max candidates in Discord message
        "dashboard_top_n": 10,  # Max candidates on web dashboard
    },
}
```

All other modules import from this config instead of hardcoding values.

---

## Rate Limits & Timing

| Source | Rate Limit | Our Setting | Delay |
|--------|-----------|-------------|-------|
| Fyers API | 10 req/sec | 8 req/sec | 1.0s per batch of 8 |
| NSE Quote API | Unknown (aggressive bot detection) | ~3 req/sec | 1.5s per batch of 5 + session refresh every 40 |
| NSE FII/DII | Unknown | 1 req/attempt | 3s between retries |
| yfinance | ~2 req/sec | ~2 req/sec | 0.5s per 2 symbols |
| Telegram Bot API | 30 msg/sec | 1 msg at a time | None needed |
| Discord Webhook | 5 req/5sec | 1 msg at a time | None needed |

**Total pipeline runtime** (estimated for 500 symbols):
- OHLCV fetch: ~75 seconds (500 symbols / 8 per sec)
- Bhavcopy: ~150 seconds (500 symbols / 3 per sec)
- Fundamentals: ~250 seconds (500 symbols / 2 per sec, parallel)
- Analysis: ~5 seconds (in-memory computation)
- **Total**: ~5-8 minutes

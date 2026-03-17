# Finboard v2.0 — Complete Directory Structure

**Snapshot Date**: 2026-03-17

---

## Full File Tree

```
QuantSystem_v1/
│
├── .github/
│   └── workflows/
│       ├── analyze.yml                 Daily pipeline cron (Mon-Fri 9 PM IST)
│       ├── backtest.yml                Weekly backtest (Friday 10 PM IST)
│       └── test_notify.yml             Manual notification test
│
├── Admin/                              [GITIGNORED except System_Snapshots/]
│   ├── .env                            API keys (single source of truth)
│   ├── execution_plan.md               Master task tracker (14 phases)
│   ├── directory_structure.md           File tree reference
│   ├── admin_requests.md               Setup guide (10 sections)
│   ├── metric_definitions_home.md      Metric definitions
│   ├── Init_Docs/                      Original PDF specifications
│   │   ├── Architecture_Blueprint.pdf
│   │   └── TechnoFundamental_System_v2.pdf
│   └── System_Snapshots/               [TRACKED in git]
│       ├── v0/                         Current snapshot (this documentation)
│       │   ├── System_Architecture.md
│       │   ├── Trading_Logic.md
│       │   ├── Data_Ingestion.md
│       │   ├── Authentication.md
│       │   ├── Output_Channels.md
│       │   ├── Dashboard.md
│       │   ├── CI_CD_Workflows.md
│       │   ├── Testing_Framework.md
│       │   ├── Directory_Structure.md
│       │   └── Configuration.md
│       └── v0_old/                     Previous snapshot (archived)
│
├── src/                                Python backend (analysis engine)
│   ├── __init__.py
│   ├── config.py                       System constants (name, version, limits)
│   ├── main.py                         Master orchestrator + run_analysis() entry point
│   │
│   ├── auth/
│   │   ├── __init__.py
│   │   └── token_manager.py            Fyers TOTP headless auth + token cache
│   │
│   ├── data/
│   │   ├── __init__.py
│   │   ├── universe.py                 NSE 500 constituent list (auto-refresh)
│   │   ├── fyers_client.py             Fyers OHLCV + index data fetch
│   │   ├── nse_bhavcopy.py             NSE delivery volume data
│   │   ├── nse_session.py              Shared NSE HTTP session factory
│   │   ├── nse_fiidii.py               FII/DII institutional flows
│   │   ├── nse_pledge.py               Promoter pledge data
│   │   ├── fundamentals.py             yfinance quarterly financials
│   │   └── sample_data.py              Test data generator (yfinance + synthetic)
│   │
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── pipeline.py                 5-stage pipeline orchestrator
│   │   ├── forensic.py                 Stage 1A: M-Score, CCR, pledge gates
│   │   ├── factors.py                  Stage 2: 5-factor scoring engine
│   │   ├── regime.py                   Stage 3: 4-state macro regime detection
│   │   ├── bearish.py                  Bearish/short + defensive candidates
│   │   ├── portfolio.py                Portfolio sizing + constraints
│   │   ├── price_targets.py            ATR-based price targets
│   │   ├── exit_rules.py               4 independent exit triggers
│   │   └── factor_correlation.py       Pairwise Pearson check (max 0.60)
│   │
│   ├── output/
│   │   ├── __init__.py
│   │   ├── formatter.py                Shared message formatting (Telegram/Discord)
│   │   ├── telegram_bot.py             Telegram Bot API delivery
│   │   ├── discord_bot.py              Discord webhook delivery
│   │   └── json_export.py              Dashboard JSON export + backup
│   │
│   └── utils/
│       ├── __init__.py
│       └── key_loader.py               Credential loader (Admin/.env → os.environ)
│
├── Tests/
│   ├── __init__.py
│   │
│   ├── SystemTest/                     End-to-end pipeline validation
│   │   ├── __init__.py
│   │   ├── run_system_test.py          Test runner (calls run_analysis())
│   │   ├── validators.py               30+ validation assertions
│   │   ├── config.json                 Run mode configuration
│   │   ├── Results/                    Test result files
│   │   └── Logs/                       Test execution logs
│   │
│   ├── backtest/                       Walk-forward historical simulation
│   │   ├── __init__.py
│   │   ├── run_backtest.py             Backtest orchestrator (52-week)
│   │   ├── data_provider.py            Fetch-once, slice-many data provider
│   │   ├── portfolio_tracker.py        Position tracking state machine
│   │   ├── metrics.py                  40+ performance metrics
│   │   └── backtest_results/           Output CSVs (tracked in git)
│   │       ├── trades_YYYY-MM-DD.csv
│   │       ├── summary_YYYY-MM-DD.csv
│   │       └── portfolio_history_YYYY-MM-DD.csv
│   │
│   └── realtime/                       Live monitoring (placeholder)
│       └── .gitkeep
│
├── dashboard/                          Next.js web dashboard
│   ├── package.json                    Dependencies (Next.js 14, React 18)
│   ├── next.config.js                  Static export configuration
│   ├── vercel.json                     Vercel build settings
│   ├── .env.local                      NEXT_PUBLIC_CM_HYPERLINK
│   │
│   ├── app/
│   │   ├── page.js                     Main dashboard (all components)
│   │   ├── layout.js                   Root layout + metadata + font
│   │   └── globals.css                 Full theme (544 lines, dark mode)
│   │
│   └── public/
│       ├── favicon.svg                 Finboard "F" icon
│       └── data/
│           ├── signals.json            Current day signals (updated daily)
│           └── signals_prev.json       Previous day backup
│
├── data/                               Cached data files
│   └── nse500_constituents.csv         NSE 500 universe (auto-refreshed)
│
├── scripts/
│   └── dry_run.py                      Notification test script
│
├── logs/                               [GITIGNORED] Daily pipeline logs
│   └── run_YYYY-MM-DD.log
│
├── .token_cache/                       [GITIGNORED] Fyers token cache
│   └── fyers_tokens.json
│
├── .cache/                             [GITIGNORED] FII/DII cache
│   └── fiidii_last.json
│
├── .env.example                        Template for API keys
├── .gitignore                          Ignore rules
├── .gitattributes                      LF normalization
├── requirements.txt                    Python dependencies (8 packages)
├── package.json                        Root package (dashboard build wrapper)
├── vercel.json                         Root Vercel config (points to dashboard/)
├── README.md                           Project documentation
└── CLAUDE.md                           AI assistant instructions
```

---

## Module Dependency Map

```
src/main.py
    ├── src/config.py                   (constants)
    ├── src/utils/key_loader.py         (credentials)
    ├── src/auth/token_manager.py       (Fyers auth)
    ├── src/data/universe.py            (NSE 500 list)
    ├── src/data/fyers_client.py        (OHLCV + indices)
    ├── src/data/nse_bhavcopy.py        (delivery data)
    ├── src/data/fundamentals.py        (quarterly financials)
    ├── src/data/nse_fiidii.py          (FII/DII flows)
    ├── src/data/nse_pledge.py          (pledge data)
    ├── src/data/sample_data.py         (test mode data)
    ├── src/analysis/pipeline.py        (5-stage engine)
    ├── src/output/telegram_bot.py      (Telegram delivery)
    ├── src/output/discord_bot.py       (Discord delivery)
    └── src/output/json_export.py       (dashboard export)

src/analysis/pipeline.py
    ├── src/analysis/forensic.py        (Stage 1A)
    ├── src/analysis/factors.py         (Stage 2)
    ├── src/analysis/regime.py          (Stage 3)
    ├── src/analysis/bearish.py         (BEAR mode)
    ├── src/analysis/portfolio.py       (sizing)
    └── src/analysis/price_targets.py   (targets)

src/data/nse_bhavcopy.py
    └── src/data/nse_session.py         (shared NSE session)

Tests/SystemTest/run_system_test.py
    ├── src/main.py :: run_analysis()   (single entry point)
    ├── src/output/json_export.py       (export test)
    ├── src/output/formatter.py         (format test)
    └── Tests/SystemTest/validators.py  (assertions)

Tests/backtest/run_backtest.py
    ├── src/analysis/pipeline.py :: run_full_pipeline()
    ├── Tests/backtest/data_provider.py
    ├── Tests/backtest/portfolio_tracker.py
    └── Tests/backtest/metrics.py
```

---

## Gitignored vs Tracked

### Gitignored (never committed)
- `Admin/` (except `Admin/System_Snapshots/`)
- `.token_cache/`
- `.cache/`
- `logs/`
- `.env`, `.env.local`
- `__pycache__/`, `*.pyc`
- `dashboard/node_modules/`, `dashboard/.next/`, `dashboard/out/`

### Tracked (committed to git)
- `Admin/System_Snapshots/` (documentation)
- `dashboard/public/data/signals.json` (dashboard data, updated daily by bot)
- `Tests/backtest/backtest_results/*.csv` (backtest results, updated weekly by bot)
- All source code in `src/`, `Tests/`, `dashboard/app/`
- Configuration files at root level

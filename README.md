# Finboard v2.0

**TechnoFundamental Quantitative Signal System for NSE 500**

A fully automated, zero-cost quantitative trading signal system that scans the entire NSE 500 universe daily, applies a rigorous 5-stage filter pipeline, and delivers ranked bullish and bearish candidates with confidence scores, price targets, and risk metrics.

## What It Does

Every trading day after market close (9:00 PM IST), the system automatically:

1. **Authenticates** with Fyers API using headless TOTP (zero manual intervention)
2. **Fetches** daily OHLCV for 500 stocks, delivery volume from NSE bhavcopy, FII/DII flows, and quarterly fundamentals
3. **Filters** through a 5-stage pipeline that eliminates governance risks, illiquid stocks, and weak earnings
4. **Ranks** survivors using 4 uncorrelated factors with regime-adaptive weights (IC-calibrated)
5. **Delivers** top 10 bullish + bearish candidates via Telegram, Discord, and a web dashboard

On market holidays, the `analyze_available` workflow automatically falls back to the latest available trading day and still delivers signals.

## The 5-Stage Pipeline

```
NSE 500 Universe (~500 stocks)
        |
Stage 1A: Forensic Filter
        |  Beneish M-Score < -2.22 | CFO/EBITDA >= 0.80 | Pledge < 5%
        |
Stage 1B: Liquidity & Clean Books
        |  ADT > 10 Crore | Sector-adjusted Debt/Equity caps
        |
Stage 1C: Point-in-Time Earnings Gate
        |  QoQ Sales growth (cyclicals: YoY) | Negative earnings nuance
        |
Stage 2: Multi-Factor Ranking (4 Factors, IC-calibrated)
        |  Mansfield RS (35-40%) | Delivery Conviction (20-35%)
        |  Vol-Adj Momentum (15-20%) | Earnings Revision Proxy (20-30%)
        |  [Weights shift by regime — FQ removed: negative IC confirmed]
        |
Stage 3: Macro & Regime Overlay
        |  BULL (100%) | DIP (60%) | SIDEWAYS (30%) | BEAR (10%)
        |
  Top 10 Bullish + Top 10 Bearish Candidates
```

## Three Pillars of Edge

- **Forensic Skepticism** — Beneish M-Score and Cash Conversion Ratio eliminate governance landmines before any technical signal is evaluated
- **Forward-Looking Fundamentals** — Earnings revision proxy identifies institutional positioning before price moves
- **Microstructure Discipline** — True delivery volume and ATR-based sizing align entries with institutional accumulation

## 4-State Regime Detection

| Regime | Exposure | Trigger |
|--------|----------|---------|
| Structural Bull | 100% | Nifty > 200 DMA, VIX 3d avg < 16, INR stable |
| Risk-On Dip | 60% | Near 200 DMA or RSI < 40, trend intact |
| Volatile Sideways | 30% | VIX 16-24, market oscillating |
| Bear / FII Flight | 10% | Nifty < 200 DMA or VIX 3d avg > 24 or INR crash |

VIX uses a 3-day average to prevent single-day spike whipsaw. BEAR runs the full pipeline at 10% exposure rather than bypassing.

## Exit Rules

| Trigger | Rule |
|---------|------|
| Technical | RS < 0 AND close < 20-week MA |
| Fundamental | QoQ sales drop > 5% |
| Risk Stop | Close < entry − 3× ATR14 (2.1× when VIX > 20) |
| Time Stop | 20 weeks (10 weeks when VIX > 20) |

## Tech Stack (100% Free)

| Component | Technology | Cost |
|-----------|-----------|------|
| Execution | GitHub Actions (cron) | Free |
| Price Data | Fyers API v3 | Free |
| Delivery Data | NSE Bhavcopy CSV | Free |
| Fundamentals | yfinance | Free |
| FII/DII Flows | NSE India | Free |
| Auth | TOTP Headless (pyotp) | Free |
| Alerts | Telegram Bot + Discord Webhook | Free |
| Dashboard | Next.js on Vercel | Free |

**Total monthly cost: Rs.0 / $0**

## Project Structure

```
finboard/
├── .github/workflows/
│   ├── analyze.yml             # Daily cron (Mon-Fri 9 PM IST) — updates dashboard
│   ├── analyze_available.yml   # Same cron, holiday fallback — no dashboard update
│   └── backtest.yml            # Weekly walk-forward backtest (Friday 10 PM IST)
├── src/
│   ├── main.py                 # Pipeline orchestrator (--fallback, --no-dashboard flags)
│   ├── config.py               # System constants
│   ├── auth/token_manager.py   # Fyers TOTP headless auth
│   ├── data/
│   │   ├── fyers_client.py     # OHLCV, VIX, USDINR fetch
│   │   ├── nse_bhavcopy.py     # Delivery volume from NSE
│   │   ├── nse_fiidii.py       # FII/DII institutional flows
│   │   ├── nse_pledge.py       # Promoter pledging data
│   │   ├── fundamentals.py     # yfinance quarterly financials
│   │   ├── sample_data.py      # yfinance fallback for testing
│   │   └── universe.py         # NSE 500 constituent list
│   ├── analysis/
│   │   ├── forensic.py         # Beneish M-Score, CCR, pledge
│   │   ├── factors.py          # 4-factor scoring engine
│   │   ├── regime.py           # 4-state regime detection
│   │   ├── pipeline.py         # Full 5-stage orchestrator
│   │   ├── bearish.py          # Bearish/bullish candidate scoring
│   │   ├── exit_rules.py       # 4 independent exit triggers
│   │   └── factor_correlation.py # Pairwise IC + correlation checks
│   └── output/
│       ├── formatter.py        # Shared Telegram/Discord formatting
│       ├── telegram_bot.py     # Telegram Bot API delivery
│       ├── discord_bot.py      # Discord webhook delivery
│       └── json_export.py      # JSON export for dashboard
├── dashboard/                  # Next.js web app (Vercel)
├── Tests/
│   ├── SystemTest/             # End-to-end system test (47 validators)
│   └── backtest/               # Walk-forward backtest harness
├── Admin/Documentation/        # Versioned design docs (gitignored except this folder)
├── data/nse500_constituents.csv
└── requirements.txt
```

## Notifications

All notifications are sent exactly once per run by Python — no duplicate workflow-level alerts.

| Event | Message |
|-------|---------|
| Successful run | Full signal report with bullish/bearish candidates and macro snapshot |
| Market holiday (`analyze.yml`) | `Finboard — Error` / Market holiday today. System will resume on the next trading day. |
| Market holiday (`analyze_available.yml`) | Runs on previous day's data — sends normal signal report |
| Pipeline error | `Finboard — Error` / Pipeline encountered an error. Check GitHub Actions logs. |

## Disclaimer

This system generates educational stock screening signals. It is **NOT** financial advice. Always do your own research and consult a SEBI-registered advisor before making investment decisions. Past performance does not guarantee future results.

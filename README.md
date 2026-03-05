# Finboard v2.0

**TechnoFundamental Quantitative Signal System for NSE 500**

A fully automated, zero-cost quantitative trading signal system that scans the entire NSE 500 universe daily, applies a rigorous 5-stage filter pipeline, and delivers ranked bullish and bearish candidates with confidence scores, price targets, and risk metrics.

## What It Does

Every trading day after market close (4:15 PM IST), the system automatically:

1. **Authenticates** with Fyers API using headless TOTP (zero manual intervention)
2. **Fetches** daily OHLCV for 500 stocks, delivery volume from NSE bhavcopy, FII/DII flows, and quarterly fundamentals
3. **Filters** through a 5-stage pipeline that eliminates governance risks, illiquid stocks, and weak earnings
4. **Ranks** survivors using 5 uncorrelated factors with regime-adaptive weights
5. **Delivers** top 10 bullish + bearish candidates via Telegram, Discord, and a web dashboard

## The 5-Stage Pipeline

```
NSE 500 Universe (~500 stocks)
        |
Stage 1A: Forensic Filter
        |  Beneish M-Score < -2.22 | CFO/EBITDA >= 0.80 | Pledge < 5%
        |
Stage 1B: Liquidity & Clean Books
        |  ADT > 10 Crore | Debt/Equity < 1.5
        |
Stage 1C: Point-in-Time Earnings Gate
        |  QoQ Sales > 0% | 2Q EPS Growth > 10%
        |
Stage 2: Multi-Factor Ranking (5 Factors)
        |  Mansfield RS (25%) | Delivery Conviction (20%)
        |  Vol-Adj Momentum (20%) | Forensic Quality (20%)
        |  Earnings Revision Proxy (15%)
        |
Stage 3: Macro & Regime Overlay
        |  BULL (100%) | DIP (60%) | SIDEWAYS (30%) | BEAR (0%)
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
| Structural Bull | 100% | Nifty > 200 DMA, VIX < 16, INR stable |
| Risk-On Dip | 60% | Near 200 DMA or RSI < 40, trend intact |
| Volatile Sideways | 30% | VIX 16-24, market oscillating |
| Bear / FII Flight | 0% new buys | Nifty < 200 DMA or VIX > 24 or INR crash |

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
nse-alpha-system/
|-- .github/workflows/analyze.yml    # Daily cron (Mon-Fri 4:15 PM IST)
|-- src/
|   |-- main.py                      # Pipeline orchestrator
|   |-- auth/token_manager.py        # Fyers TOTP headless auth
|   |-- data/
|   |   |-- fyers_client.py          # OHLCV, VIX, USDINR fetch
|   |   |-- nse_bhavcopy.py          # Delivery volume from NSE
|   |   |-- nse_fiidii.py            # FII/DII institutional flows
|   |   |-- nse_pledge.py            # Promoter pledging data
|   |   |-- fundamentals.py          # yfinance quarterly financials
|   |   |-- universe.py              # NSE 500 constituent list
|   |-- analysis/
|   |   |-- forensic.py              # Beneish M-Score, CCR, pledge
|   |   |-- factors.py               # 5-factor scoring engine
|   |   |-- regime.py                # 4-state regime detection
|   |   |-- portfolio.py             # ATR sizing, sector caps
|   |   |-- bearish.py               # Bearish/short candidates
|   |   |-- price_targets.py         # ATR-projected price bands
|   |   |-- pipeline.py              # Full 5-stage orchestrator
|   |-- output/
|       |-- formatter.py             # Shared message formatting
|       |-- telegram_bot.py          # Telegram Bot API delivery
|       |-- discord_bot.py           # Discord webhook delivery
|       |-- json_export.py           # JSON export for dashboard
|-- dashboard/                       # Next.js web app (Vercel)
|-- data/nse500_constituents.csv     # PIT universe list
|-- Admin/execution_plan.md          # Development execution plan
|-- requirements.txt
|-- README.md
```

## Setup (One-Time, ~30 Minutes)

### Prerequisites
- Active Fyers trading account with API access enabled
- Telegram account (for bot alerts)
- GitHub account (for Actions + repo)

### Step 1: Fyers API
1. Log in to myapi.fyers.in, create an app
2. Enable External 2FA TOTP at myaccount.fyers.in/ManageAccount
3. Copy: App ID, Secret Key, Client ID, PIN, TOTP Secret Key

### Step 2: Telegram Bot
1. Message @BotFather on Telegram, create a new bot
2. Create a private channel, add bot as admin
3. Get chat_id via @userinfobot

### Step 3: Discord (Optional)
1. In your Discord server, go to channel Settings > Integrations > Webhooks
2. Create webhook, copy the URL

### Step 4: GitHub Setup
1. Create a private repo
2. Push all code
3. Add secrets in repo Settings > Secrets > Actions:
   - `FYERS_APP_ID`, `FYERS_SECRET_KEY`, `FYERS_CLIENT_ID`, `FYERS_PIN`, `FYERS_TOTP_KEY`
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
   - `DISCORD_WEBHOOK_URL` (optional)

### Step 5: Dashboard (Vercel)
1. Import the `dashboard/` directory as a new Vercel project
2. Deploy — the dashboard reads `signals.json` updated by GitHub Actions

### Step 6: Validate
1. Go to Actions tab > Run workflow (manual trigger)
2. Confirm Telegram message received and dashboard updates

## Maintenance

| Frequency | Task | Time |
|-----------|------|------|
| Quarterly | Update NSE 500 constituent CSV | 20 min |
| Automatic | TOTP re-auth every 15 days | 0 min |
| If needed | Update auth flow if Fyers changes API | 30 min |

## Disclaimer

This system generates educational stock screening signals. It is **NOT** financial advice. Always do your own research and consult a SEBI-registered advisor before making investment decisions. Past performance does not guarantee future results.

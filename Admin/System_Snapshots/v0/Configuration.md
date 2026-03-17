# Finboard v2.0 — Configuration & Environment

**Snapshot Date**: 2026-03-17
**Source Files**: `src/config.py`, `src/utils/key_loader.py`, `.env.example`, `requirements.txt`, `package.json`

---

## System Constants — `src/config.py`

```python
SYSTEM_CONFIG = {
    "system": {
        "name": "Finboard",
        "version": "2.0",
        "full_name": "Finboard v2.0",
        "universe_pct": 1.0,      # 1.0 = full NSE 500, 0.05 = 5% for tests
    },
    "output": {
        "telegram_top_n": 5,       # Top N candidates in Telegram alerts
        "discord_top_n": 5,        # Top N candidates in Discord alerts
        "dashboard_top_n": 10,     # Top N candidates in JSON export
    },
}

# Convenience accessors
SYSTEM_NAME = "Finboard"
SYSTEM_VERSION = "2.0"
SYSTEM_FULL_NAME = "Finboard v2.0"
UNIVERSE_PCT = 1.0
TELEGRAM_TOP_N = 5
DISCORD_TOP_N = 5
DASHBOARD_TOP_N = 10
DIVIDER_TELEGRAM = "━" × 28       # Visual divider in messages
```

---

## API Credentials

### Key Management Architecture
```
Local Development:
    Admin/.env (python-dotenv)
        → src/utils/key_loader.py :: get_key()
        → Returns value from .env file

Production (GitHub Actions):
    GitHub Repository Secrets
        → Injected as os.environ by workflow YAML
        → src/utils/key_loader.py :: get_key()
        → Returns value from os.environ (fallback)
```

### Key Loader Functions — `src/utils/key_loader.py`

| Function | Purpose |
|----------|---------|
| `get_key(key_name, default='')` | Get key with fallback to default. Filters placeholder values. |
| `require_key(key_name)` | Get key or raise RuntimeError if missing. |
| `reload_env()` | Force reload Admin/.env (hot reload during session). |
| `get_all_keys()` | Return all known keys with masked values (for logging). |

### Required Secrets

| Key | Required For | Format | Example |
|-----|-------------|--------|---------|
| `FYERS_APP_ID` | Fyers API auth | `XXXXXXXX-100` | `A1B2C3D4-100` |
| `FYERS_SECRET` | Fyers API auth | Alphanumeric | `K9X2M5...` |
| `FYERS_CLIENT_ID` | Fyers account | `FYXXXXX` | `FY12345` |
| `FYERS_PIN` | Fyers 2FA | 4 digits | `1234` |
| `FYERS_TOTP_KEY` | Fyers TOTP | Base32 string | `JBSWY3DPEHPK3PXP` |
| `TELEGRAM_TOKEN` | Telegram Bot | `bot_id:token` | `123456:ABC-DEF...` |
| `TELEGRAM_CHAT` | Telegram channel | Numeric ID | `-1001234567890` |
| `DISCORD_WEBHOOK_URL` | Discord webhook | Full URL | `https://discord.com/api/webhooks/...` |

### .env.example Template
```bash
# Fyers API
FYERS_APP_ID=your_app_id_here
FYERS_SECRET=your_secret_here
FYERS_CLIENT_ID=your_client_id_here
FYERS_PIN=your_pin_here
FYERS_TOTP_KEY=your_totp_key_here

# Telegram
TELEGRAM_TOKEN=your_bot_token_here
TELEGRAM_CHAT=your_chat_id_here

# Discord
DISCORD_WEBHOOK_URL=your_webhook_url_here
```

### GitHub Secrets Mapping
| `.env` Key | GitHub Secret Name |
|-----------|-------------------|
| `FYERS_APP_ID` | `FYERS_APP_ID` |
| `FYERS_SECRET` | `FYERS_SECRET_KEY` |
| `FYERS_CLIENT_ID` | `FYERS_CLIENT_ID` |
| `FYERS_PIN` | `FYERS_PIN` |
| `FYERS_TOTP_KEY` | `FYERS_TOTP_KEY` |
| `TELEGRAM_TOKEN` | `TELEGRAM_BOT_TOKEN` |
| `TELEGRAM_CHAT` | `TELEGRAM_CHAT_ID` |
| `DISCORD_WEBHOOK_URL` | `DISCORD_WEBHOOK_URL` |

---

## Pipeline Thresholds & Constants

### Stage 1A: Forensic Filter (`src/analysis/forensic.py`)
```python
M_SCORE_THRESHOLD = -2.22      # Above this = probable manipulator
CCR_THRESHOLD = 0.80           # Below this = poor cash conversion
PLEDGE_MAX_PCT = 5.0           # Max promoter pledge %
PLEDGE_MAX_DELTA = 2.0         # Max quarterly pledge change (pp)
```

### Stage 1B: Liquidity (`src/analysis/pipeline.py`)
```python
MIN_ADT = 1e7                  # INR 10 Crore minimum ADT
MAX_DEBT_EQUITY = 1.5          # Maximum debt/equity ratio
```

### Stage 1C: Earnings Gate (`src/analysis/pipeline.py`)
```python
MIN_QOQ_SALES_GROWTH = 0.0    # QoQ sales must be positive
MIN_EPS_GROWTH_2Q = 0.10      # 10% EPS growth over 2 quarters
```

### Stage 3: Regime Detection (`src/analysis/regime.py`)
```python
REGIME_SCALARS = {
    'BULL': 1.0,               # 100% exposure
    'DIP': 0.6,                # 60% exposure
    'SIDEWAYS': 0.3,           # 30% exposure
    'BEAR': 0.0,               # 0% new buys
}

REGIME_WEIGHTS = {
    'BULL':     {'rs': 0.30, 'del': 0.20, 'vam': 0.25, 'for': 0.15, 'rev': 0.10},
    'DIP':      {'rs': 0.20, 'del': 0.30, 'vam': 0.10, 'for': 0.30, 'rev': 0.10},
    'SIDEWAYS': {'rs': 0.05, 'del': 0.30, 'vam': 0.05, 'for': 0.40, 'rev': 0.20},
    'BEAR':     {'rs': 0.00, 'del': 0.00, 'vam': 0.00, 'for': 0.00, 'rev': 0.00},
}
```

### VIX-Adaptive Risk (`src/analysis/pipeline.py` + `exit_rules.py`)
```python
VIX_HIGH_THRESHOLD = 20       # VIX above this triggers tightening
VIX_STOP_TIGHTENING = 0.70    # Multiply stop by this (30% tighter)
```

### Portfolio Construction (`src/analysis/portfolio.py`)
```python
RISK_PER_TRADE_PCT = 0.01     # 1% risk per trade
ATR_STOP_MULTIPLIER = 2.0     # Stop at 2x ATR14
MAX_SECTOR_PCT = 0.25         # 25% max per sector
MAX_POSITION_PCT = 0.15       # 15% max per stock
MAX_ADT_PCT = 0.02            # 2% of 20-day ADT
MAX_STOCKS = 10               # Maximum portfolio positions
MAX_SAME_SUBINDUSTRY = 2      # Max 2 stocks from same sub-industry
MAX_PORTFOLIO_BETA = 1.3      # Portfolio beta cap vs Nifty 500
MIN_DEFENSIVE_PCT = 0.20      # 20% min in defensive sectors
DEFENSIVE_SECTORS = {'FMCG', 'Healthcare', 'IT', 'Pharma'}
```

### Exit Rules (`src/analysis/exit_rules.py`)
```python
ATR_STOP_MULTIPLIER = 2.0          # ATR-based stop
TIME_STOP_WEEKS_NORMAL = 26        # 26-week time stop
TIME_STOP_WEEKS_HIGH_VIX = 13      # 13 weeks if VIX > 20
VIX_HIGH_THRESHOLD = 20
VIX_STOP_TIGHTENING = 0.70
SALES_DROP_EXIT_THRESHOLD = -0.05   # -5% QoQ sales → exit
RS_EXIT_THRESHOLD = 0.0             # RS < 0 → exit (with MA confirmation)
```

### Factor Correlation (`src/analysis/factor_correlation.py`)
```python
MAX_PAIRWISE_CORRELATION = 0.60    # Warning threshold
FACTOR_COLUMNS = ['mrs', 'deliv', 'vam', 'fq', 'rev']
```

### Data Fetch Rate Limits
```python
# fyers_client.py
BATCH_SIZE = 8                 # Requests per second (Fyers)
BATCH_DELAY = 1.0              # Seconds between batches
MAX_DAILY_RANGE = 365          # Fyers max days per request

# nse_bhavcopy.py
QUOTE_BATCH_SIZE = 5           # NSE requests per batch
QUOTE_DELAY = 1.5              # Seconds between batches
SESSION_REFRESH = 40           # Re-seed NSE session every N requests

# universe.py
STALE_DAYS = 90                # Auto-refresh universe after 90 days
MIN_SYMBOLS = 100              # Auto-refresh if < 100 symbols
```

### Bearish Model (`src/analysis/bearish.py`)
```python
SHORT_M_SCORE_THRESHOLD = -1.5     # Above this = high manipulation risk
SHORT_RS_THRESHOLD = 0             # Must be negative
NEG_REVISION_THRESHOLD = 0.3       # Below this = negative revisions
```

### Output Limits
```python
# telegram_bot.py
MAX_MESSAGE_LENGTH = 4000          # Telegram limit (4096 actual)

# discord_bot.py
MAX_DISCORD_LENGTH = 1900          # Discord limit (2000 actual)
```

---

## Python Dependencies — `requirements.txt`

| Package | Version | Purpose |
|---------|---------|---------|
| `fyers-apiv3` | >= 3.1.0 | Fyers API client (OHLCV, auth) |
| `yfinance` | >= 0.2.40 | Yahoo Finance (fundamentals, USD/INR fallback) |
| `pandas` | >= 2.1.0 | Data manipulation (DataFrames) |
| `numpy` | >= 1.26.0 | Numerical computing |
| `requests` | >= 2.31.0 | HTTP (NSE scraping, Telegram, Discord) |
| `pyotp` | >= 2.9.0 | TOTP generation (headless Fyers auth) |
| `python-dotenv` | >= 1.0.0 | .env file loading |
| `pytz` | >= 2024.1 | Timezone handling (IST) |

---

## Dashboard Dependencies — `dashboard/package.json`

| Package | Version | Purpose |
|---------|---------|---------|
| `next` | ^14.2.0 | React framework (App Router, SSG) |
| `react` | ^18.2.0 | UI library |
| `react-dom` | ^18.2.0 | React DOM renderer |

---

## Next.js Configuration — `dashboard/next.config.js`

```javascript
const nextConfig = {
    output: 'export',                  // Static HTML export
    trailingSlash: true,               // URLs end with /
    images: { unoptimized: true },     // No Image optimization
    allowedDevOrigins: ['127.0.0.1', 'localhost'],
};
```

---

## Vercel Configuration

### Root `vercel.json` (for deployment)
```json
{
    "installCommand": "cd dashboard && npm install",
    "buildCommand": "cd dashboard && npm run build",
    "outputDirectory": "dashboard/out"
}
```

### Dashboard `dashboard/vercel.json` (build settings)
```json
{
    "buildCommand": "npm run build",
    "outputDirectory": "out",
    "framework": "nextjs"
}
```

---

## File Paths Summary

| Purpose | Path |
|---------|------|
| API keys (local) | `Admin/.env` |
| API keys (template) | `.env.example` |
| Token cache | `.token_cache/fyers_tokens.json` |
| FII/DII cache | `.cache/fiidii_last.json` |
| Universe CSV | `data/nse500_constituents.csv` |
| Pipeline logs | `logs/run_YYYY-MM-DD.log` |
| Dashboard data | `dashboard/public/data/signals.json` |
| Dashboard backup | `dashboard/public/data/signals_prev.json` |
| Backtest results | `Tests/backtest/backtest_results/` |
| System test results | `Tests/SystemTest/Results/` |
| System test logs | `Tests/SystemTest/Logs/` |
| System test config | `Tests/SystemTest/config.json` |

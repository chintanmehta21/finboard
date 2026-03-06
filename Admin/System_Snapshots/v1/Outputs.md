# Finboard v1 — Output Formats

Snapshot as of 2026-03-06. Documents the exact format of Telegram, Discord, and JSON dashboard outputs.

---

## 1. Telegram Output

**Delivery**: Telegram Bot API via `src/output/telegram_bot.py`
**Parse mode**: HTML
**Max message length**: 4000 characters (Telegram limit is 4096; we leave margin)
**Chunking**: Messages exceeding 4000 chars are split at line boundaries into multiple messages
**Configuration**: Token from `TELEGRAM_TOKEN`, Chat ID from `TELEGRAM_CHAT` (both from `Admin/.env` or GitHub Secrets)

### Message Structure

```
<b>Finboard — Daily Report</b>
📅 Friday, 06 Mar 2026
Regime: <b>BEAR / FII FLIGHT</b> (0% exp.)
Bear regime — reduced sizing
━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📈 <b>TOP BULLISH CANDIDATES</b>

<b>1. RELIANCE</b> — Score: <b>85</b>
   CMP: ₹2,450 | 3M: +12.3% | 1W: -1.2%
   Target: ₹2,650 | S/L: ₹2,300
   Today: +1.5%

<b>2. TCS</b> — Score: <b>78</b>
   CMP: ₹3,800 | 3M: +8.1% | 1W: +0.5%
   Target: ₹4,100 | S/L: ₹3,600
   Today: -0.3%

(... up to 5 bullish candidates ...)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📉 <b>BEARISH CANDIDATES</b>

<b>1. ADANIENT</b> — Score: <b>72</b>
   CMP: ₹2,100 | 3M: -15.2% | 1W: -3.1%
   M-Score: -1.2 | CCR: 0.55 | RS: -8.3
   Today: -2.1%

(... up to 5 bearish candidates ...)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 <b>MACRO SNAPSHOT</b>
   Nifty 500: 22,698 | 200 DMA: 23,306 (-2.6%)
   India VIX: 17.9
   USD/INR: 92.01 (30d: +0.52%)
   FII net: ₹-3,753 Cr | DII net: ₹5,153 Cr

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ <i>NOT financial advice</i>
```

### Format Details

- **Header**: System name + "Daily Report" in bold. Date on its own line with 📅 calendar emoji. Regime on its own line with exposure shorthand "exp." (not "exposure").
- **Bear warning**: "Bear regime — reduced sizing" shown only when regime scalar = 0
- **Divider**: 28 unicode box-drawing characters (U+2501)
- **Section headers**: Each section has an emoji prefix for visual distinction (📈 bullish, 📉 bearish, 📊 macro). Headers are always shown even when no candidates exist.
- **Bullish section**: "TOP BULLISH CANDIDATES" header. Max 5 candidates (controlled by `TELEGRAM_TOP_N` in config). Each candidate shows:
  - Line 1: Rank, symbol (bold), score (bold)
  - Line 2: CMP with rupee sign, 3M return (with sign), 1W return (with sign) — separated by pipes
  - Line 3 (if target/stop exist): Target price, Stop loss
  - Line 4: Today's daily change (return_1d) with +/- sign
- **Bearish section**: "BEARISH CANDIDATES" header. Max 5. Each shows:
  - Line 1: Rank, symbol, bearish score
  - Line 2: CMP, returns
  - Line 3: M-Score, CCR, RS (Mansfield)
  - Line 4: Today's daily change (return_1d) with +/- sign
- **Macro snapshot**: All in one block — Nifty + DMA, VIX, USD/INR with 30d move, FII/DII nets
- **Footer**: ⚠️ caution icon + "NOT financial advice" in italic
- **Empty state (bullish)**: Header shown, then "No bullish candidates passed all stages today."
- **Empty state (bearish)**: Header shown, then "No bearish candidates identified today."
- **Note on Telegram headers**: Telegram HTML does not support heading sizes (`<h1>`, `<h2>` etc). Only `<b>`, `<i>`, `<u>`, `<s>`, `<code>`, `<pre>`, `<a>` are supported. Section headers use emoji prefixes for visual distinction instead of size differentiation.

### Special Messages

**Holiday message**:
```
<b>Finboard</b>

Market holiday today. No bhavcopy available.
System will resume on the next trading day.
```

**Error message**:
```
<b>Finboard — Error</b>

Pipeline encountered an error:
<code>{first 500 chars of error}</code>

Check GitHub Actions logs for details.
```

---

## 2. Discord Output

**Delivery**: Discord Webhook via `src/output/discord_bot.py`
**Format**: Markdown (Discord-flavored)
**Max message length**: 1900 characters (Discord limit is 2000)
**Chunking**: Same line-boundary splitting as Telegram
**Configuration**: Webhook URL from `DISCORD_WEBHOOK_URL`
**Webhook username**: "Finboard" (set in JSON payload)

### Message Structure

```
# Finboard — Daily Report
📅 **Friday, 06 Mar 2026**
Regime: **BEAR / FII FLIGHT** (0% exp.)
Bear regime — reduced sizing
---
📈 ## TOP BULLISH CANDIDATES

**1. RELIANCE** — Score: **85**
> CMP: Rs.2,450 | 3M: +12.3% | 1W: -1.2% | Target: Rs.2,650 | S/L: Rs.2,300
> Today: +1.5%

**2. TCS** — Score: **78**
> CMP: Rs.3,800 | 3M: +8.1% | 1W: +0.5% | Target: Rs.4,100 | S/L: Rs.3,600
> Today: -0.3%

---
📉 ## BEARISH CANDIDATES

**1. ADANIENT** — Score: **72**
> CMP: Rs.2,100 | 3M: -15.2% | 1W: -3.1% | M-Score: -1.2 | CCR: 0.55
> Today: -2.1%

---
📊 ## MACRO SNAPSHOT
Nifty 500: 22,698 | VIX: 17.9 | USD/INR: 92.01 (+0.52%) | FII: Rs.-3,753 Cr | DII: Rs.5,153 Cr

⚠️ *NOT financial advice*
```

### Differences from Telegram

| Aspect | Telegram | Discord |
|--------|----------|---------|
| Formatting | HTML tags (`<b>`, `<i>`, `<code>`) | Markdown (`**`, `*`, `#`, `>`) |
| Currency symbol | ₹ (Rupee sign) | Rs. (Rupees) |
| Divider | 28x unicode box char | `---` (markdown horizontal rule) |
| Metrics layout | Multi-line per candidate | All metrics on one `>` quoted line |
| Headers | `<b>` bold + emoji prefix | `##` heading levels + emoji prefix |
| Macro | Multi-line block | Single compact line |
| Top N | 5 (TELEGRAM_TOP_N) | 5 (DISCORD_TOP_N) |
| Today's change | Indented line below metrics | `>` quoted line below metrics |

### Special Messages

**Holiday**: `# Finboard\nMarket holiday today. System resumes next trading day.`

**Error**: `## Finboard — Error\n\`\`\`\n{error text}\n\`\`\`\nCheck GitHub Actions logs.`

---

## 3. JSON Dashboard Export

**File**: `dashboard/public/data/signals.json`
**Generator**: `src/output/json_export.py`
**Consumer**: Next.js dashboard (`dashboard/app/page.js`)
**Backup**: Previous version saved as `signals_prev.json` before overwriting

### Full JSON Schema

```json
{
  "generated_at": "2026-03-06T21:38:47+05:30",
  "date": "2026-03-06",
  "display_date": "Friday, 06 Mar 2026",
  "sample_mode": false,
  "regime": {
    "name": "BEAR",
    "scalar": 0.0,
    "exposure_pct": 0
  },
  "macro": {
    "nifty_close": 22697.8,
    "nifty_200dma": 23306.49,
    "nifty_dma_pct": -2.6,
    "india_vix": 17.9,
    "usdinr": 92.01,
    "usdinr_30d_move": 0.52,
    "fii_net": -3753.0,
    "dii_net": 5153.0
  },
  "pipeline_stats": {
    "total_universe": 50,
    "stage_1a_pass": 0,
    "stage_1b_pass": 0,
    "stage_1c_pass": 0,
    "stage_2_scored": 0,
    "date": "2026-03-06",
    "regime": "BEAR",
    "vix": 17.9,
    "high_vix_mode": false
  },
  "factor_weights": {
    "rs": 0.0, "del": 0.0, "vam": 0.0, "for": 0.0, "rev": 0.0
  },
  "bullish": [
    {
      "symbol": "RELIANCE",
      "close": 2450.0,
      "return_1d": 1.5,
      "return_1w": -1.2,
      "return_3m": 12.3,
      "adj_confidence": 85.0,
      "sector": "Oil Gas & Consumable Fuels",
      "target_high": 2650.0,
      "stop_loss": 2300.0,
      "atr14": 55.0,
      "deliv_pct": 62.5,
      "ccr": 0.92,
      "debt_equity": 0.45,
      "m_score": -3.1
    }
  ],
  "bearish": [
    {
      "symbol": "ADANIENT",
      "bearish_score": 72.0,
      "close": 2100.0,
      "return_1d": -2.1,
      "return_1w": -3.1,
      "return_3m": -15.2,
      "sector": "Metals & Mining",
      "m_score": -1.2,
      "ccr": 0.55,
      "mansfield_rs": -8.3,
      "lvgi": 1.15,
      "lvgi_rising": true,
      "signal": "SHORT"
    }
  ]
}
```

### Field Notes

- **generated_at**: IST timestamp of when the export ran (not when data was generated)
- **date**: ISO date string of the last trading day (NOT today's date if run pre-market)
- **display_date**: Human-readable date for the dashboard header
- **sample_mode**: True when pipeline ran without Fyers (yfinance/synthetic data)
- **regime.scalar**: 0.0 (BEAR) to 1.0 (BULL) — multiplied by 100 for `exposure_pct`
- **pipeline_stats**: Shows funnel counts through each stage. In BEAR regime, all stage counts may be 0 since the defensive path bypasses the normal pipeline
- **factor_weights**: All 0.0 in BEAR (no factor scoring), otherwise regime-specific weights
- **bullish**: Array of up to 10 candidate objects. In BEAR regime, these are defensive rotation candidates. Sorted by `adj_confidence` (or `defensive_score`) descending
- **bearish**: Array of up to 10 bearish candidates sorted by `bearish_score` descending
- **return_1d**: Today's daily change in percent (added in v1 Edition III)
- All float values rounded to 4 decimal places. NaN and Infinity values replaced with 0

---

## 4. Dashboard (Web)

**Framework**: Next.js 14 (App Router), static export
**Host**: Vercel (auto-deploys on git push)
**Data source**: `dashboard/public/data/signals.json`
**Footer**: "Data updated daily before market opens (Mon-Fri)"
**No-data view**: "The pipeline runs daily before market opens (Mon-Fri)."

### Key UI Elements

- **Regime banner**: Color-coded (bull=green, dip=orange, sideways=yellow, bear=red) with exposure %
- **Bullish cards**: Show Today's change, CMP, 3M Ret, 1W Ret, Target, S/L, ATR14, Delivery%, CCR, D/E with confidence score
- **Bearish cards**: Show Today's change, CMP, 3M Ret, 1W Ret, M-Score, CCR, RS, LVGI with bearish score
- **Macro grid**: 5 cards (Nifty 500, India VIX, USD/INR, FII Net, DII Net)

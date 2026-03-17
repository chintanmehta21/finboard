# Finboard v2.0 — Output Channels

**Snapshot Date**: 2026-03-17
**Source Files**: `src/output/` (formatter.py, telegram_bot.py, discord_bot.py, json_export.py)

---

## Output Overview

The pipeline produces 3 types of output after each daily run:

| Channel | Format | Audience | Update Frequency | Max Items |
|---------|--------|----------|-----------------|-----------|
| **Telegram** | HTML (Bot API) | Mobile alerts | Daily Mon-Fri | Top 5 |
| **Discord** | Markdown (Webhook) | Desktop/community | Daily Mon-Fri | Top 5 |
| **JSON Export** | JSON file | Web dashboard | Daily Mon-Fri | Top 10 |

---

## 1. Shared Formatter — `src/output/formatter.py`

### What It Does
Provides shared message formatting functions consumed by both Telegram and Discord bots. Ensures consistent output across channels.

### Regime Display Mapping
```python
REGIME_DISPLAY = {
    'BULL':     {'label': 'STRUCTURAL BULL',       'exposure': '100%'},
    'DIP':      {'label': 'RISK-ON DIP',           'exposure': '60%'},
    'SIDEWAYS': {'label': 'VOLATILE SIDEWAYS',     'exposure': '30%'},
    'BEAR':     {'label': 'BEAR / FII FLIGHT',     'exposure': '0%'},
}
```

### Report Structure (Both Channels)
```
1. Header — "Finboard — Daily Analysis" + date
2. Regime Line — Current regime + exposure %
3. Divider (━ × 28)
4. Macro Summary — Nifty, VIX, USD/INR, FII/DII flows
5. Top Bullish Candidates (top 5)
   - Symbol, Score, CMP, 3M Return, Target, Stop Loss, Today's Change
6. Top Bearish Candidates (top 5)
   - Symbol, Score, CMP, Returns, M-Score, CCR, RS
7. Pipeline Stats — Universe → Stage 1A → 1B → Scored
8. Footer — Timestamp, sample mode flag
```

### Key Functions
- `format_telegram_report(result: dict) -> str` — HTML formatting with `<b>`, `<code>` tags
- `format_discord_report(result: dict) -> str` — Markdown formatting with `**bold**`, `` `code` ``
- `_get_display_date(result: dict) -> str` — Extracts human-readable date

---

## 2. Telegram Bot — `src/output/telegram_bot.py`

### Configuration
| Setting | Value | Source |
|---------|-------|--------|
| API Endpoint | `https://api.telegram.org/bot{token}/sendMessage` | Telegram Bot API |
| Parse Mode | `HTML` | Supports `<b>`, `<i>`, `<code>`, `<pre>` |
| Max Message Length | 4000 chars | Telegram limit is 4096, buffer of 96 |
| Bot Token | `TELEGRAM_TOKEN` | Admin/.env or GitHub Secret |
| Chat ID | `TELEGRAM_CHAT` | Admin/.env or GitHub Secret |

### Message Flow
```
send_signal_report(result)
    │
    ├─ Check if TELEGRAM_TOKEN and TELEGRAM_CHAT are configured
    │   └─ If either missing → return False (silent skip)
    │
    ├─ format_telegram_report(result) → HTML string
    │
    └─ _send_message(token, chat_id, text)
        │
        ├─ If len(text) <= 4000 → single POST
        │
        └─ If len(text) > 4000 → _chunk_message() → multiple POSTs
            └─ Split at line boundaries (never mid-line)
```

### Additional Message Types
- `send_holiday_message()` — "Market holiday today — no signals generated"
- `send_error_message(error: str)` — "Pipeline failed: {error details}"

### Message Chunking
When the formatted report exceeds 4000 characters:
1. Split text at newline boundaries
2. Accumulate lines until chunk approaches limit
3. Send each chunk as a separate message
4. Small delay between chunks to avoid rate limiting

---

## 3. Discord Webhook — `src/output/discord_bot.py`

### Configuration
| Setting | Value | Source |
|---------|-------|--------|
| Delivery Method | Webhook POST | No bot required, just a URL |
| Max Message Length | 1900 chars | Discord limit is 2000, buffer of 100 |
| Webhook URL | `DISCORD_WEBHOOK_URL` | Admin/.env or GitHub Secret |
| Username | "Finboard" | Set in webhook payload |

### Message Flow
```
send_signal_report(result)
    │
    ├─ Check if DISCORD_WEBHOOK_URL is configured
    │   └─ If missing → return False (silent skip)
    │
    ├─ format_discord_report(result) → Markdown string
    │
    └─ _send_webhook(webhook_url, content)
        │
        ├─ If len(content) <= 1900 → single POST
        │
        └─ If len(content) > 1900 → _chunk_message() → multiple POSTs
            └─ Split at line boundaries
```

### Webhook Payload
```json
{
    "content": "**Finboard — Daily Analysis**\n...",
    "username": "Finboard"
}
```

### Additional Message Types
- `send_holiday_message()` — Market holiday notification
- `send_error_message(error: str)` — Pipeline failure alert

---

## 4. JSON Export — `src/output/json_export.py`

### What It Does
Exports the full pipeline result as a JSON file for the Next.js dashboard to consume. This is the data bridge between the Python backend and the web frontend.

### Export Flow
```
export_signals(result, output_path=None)
    │
    ├─ Default path: dashboard/public/data/signals.json
    │
    ├─ Backup: Copy current signals.json → signals_prev.json
    │
    ├─ Build JSON structure:
    │   ├─ generated_at (ISO timestamp with IST timezone)
    │   ├─ date (YYYY-MM-DD)
    │   ├─ display_date (human-readable)
    │   ├─ sample_mode (boolean)
    │   ├─ regime { name, scalar, exposure_pct }
    │   ├─ macro { nifty_close, nifty_200dma, ... }
    │   ├─ pipeline_stats { total_universe, stage_1a_pass, ... }
    │   ├─ factor_weights { rs, del, vam, for, rev }
    │   ├─ bullish [ { symbol, close, sector, scores, targets, ... } ]
    │   └─ bearish [ { symbol, close, sector, scores, ... } ]
    │
    ├─ Clean data: _df_to_records() handles NaN/inf → null conversion
    │
    ├─ Write JSON to file (indent=2 for readability)
    │
    └─ Return path to exported file
```

### JSON Structure (Full Schema)
```json
{
    "generated_at": "2026-03-17T21:30:00.000000+05:30",
    "date": "2026-03-17",
    "display_date": "Monday, 17 Mar 2026",
    "sample_mode": false,

    "regime": {
        "name": "BULL",
        "scalar": 1.0,
        "exposure_pct": 100
    },

    "macro": {
        "nifty_close": 22500.0,
        "nifty_200dma": 21800.0,
        "nifty_dma_pct": 3.2,
        "india_vix": 14.5,
        "usdinr": 87.25,
        "usdinr_30d_move": -0.5,
        "fii_net": 1250.0,
        "dii_net": 800.0
    },

    "pipeline_stats": {
        "total_universe": 498,
        "stage_1a_pass": 180,
        "stage_1b_pass": 120,
        "stage_1c_pass": 65,
        "stage_2_scored": 65,
        "date": "2026-03-17",
        "regime": "BULL",
        "vix": 14.5,
        "high_vix_mode": false
    },

    "factor_weights": {
        "rs": 0.30,
        "del": 0.20,
        "vam": 0.25,
        "for": 0.15,
        "rev": 0.10
    },

    "bullish": [
        {
            "symbol": "RELIANCE",
            "close": 2850.0,
            "sector": "Energy",
            "ccr": 1.25,
            "debt_equity": 0.45,
            "m_score": -3.10,
            "mansfield_rs": 12.5,
            "return_1d": 1.2,
            "return_1w": 3.5,
            "return_3m": 15.0,
            "return_6m": 22.0,
            "adj_confidence": 85.3,
            "bullish_score": 85.3,
            "confidence": 85.3,
            "atr14": 55.0,
            "target_high": 3015.0,
            "stop_loss": 2740.0,
            "deliv_pct": 65.2
        }
    ],

    "bearish": [
        {
            "symbol": "XYZ",
            "close": 150.0,
            "sector": "Metals",
            "ccr": 0.65,
            "m_score": -1.2,
            "mansfield_rs": -8.5,
            "return_1d": -2.1,
            "return_1w": -6.0,
            "return_3m": -18.0,
            "return_6m": -25.0,
            "bearish_score": 72.0,
            "lvgi": 1.15,
            "lvgi_rising": true
        }
    ]
}
```

### Data Cleaning
The `_df_to_records()` function converts pandas DataFrames to JSON-safe dicts:
- NaN → null
- inf / -inf → null
- Numeric precision preserved (float, not string)
- Column names preserved as-is

### Backup Strategy
Before each export, the current `signals.json` is copied to `signals_prev.json`. This provides:
- Previous day comparison capability
- Rollback if a bad export occurs
- Historical reference for the dashboard

### Export Path
```
dashboard/public/data/
├── signals.json          ← Current day (updated by pipeline)
└── signals_prev.json     ← Previous day (auto-backup)
```

---

## Output Delivery Timeline

```
Pipeline completes (~9:30 PM IST)
    │
    ├─ Telegram message sent (instant)
    ├─ Discord webhook sent (instant)
    ├─ signals.json written to disk (instant)
    │
    └─ GitHub Actions auto-commit
        ├─ git add signals.json
        ├─ git commit -m "chore: update daily signals YYYY-MM-DD"
        ├─ git pull --rebase origin main
        └─ git push
            │
            └─ Vercel detects push → rebuilds dashboard (~30 sec)
```

---

## Failure Notifications

Both Telegram and Discord receive failure alerts from two sources:

### 1. Pipeline Runtime Failures (Python)
```python
# src/main.py :: _send_error_notifications()
send_error_message("Pipeline failed: {error details}")
```

### 2. Workflow-Level Failures (GitHub Actions)
```yaml
# .github/workflows/analyze.yml
- name: Notify on failure via Telegram
  if: failure()
  run: |
    python -c "
    import requests, os
    token = os.environ.get('TELEGRAM_TOKEN', '')
    chat = os.environ.get('TELEGRAM_CHAT', '')
    if token and chat:
        msg = 'PIPELINE FAILED — Check GitHub Actions logs'
        requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                      json={'chat_id': chat, 'text': msg, 'parse_mode': 'HTML'})
    "
```

### Failure Types Covered
| Type | Source | Channels |
|------|--------|----------|
| Authentication failure | token_manager.py | Telegram + Discord |
| Data fetch failure | main.py | Telegram + Discord |
| Pipeline error | pipeline.py | Telegram + Discord |
| Market holiday (no bhavcopy) | main.py | Telegram + Discord (holiday message) |
| GitHub Actions workflow failure | YAML | Telegram + Discord (workflow notification) |

---

## Configuration Constants

```python
# src/config.py
TELEGRAM_TOP_N = 5      # Top N candidates shown in Telegram
DISCORD_TOP_N = 5       # Top N candidates shown in Discord
DASHBOARD_TOP_N = 10    # Top N candidates in JSON export
DIVIDER_TELEGRAM = "━" * 28  # Visual divider in messages
```

# Finboard v2.0 — CI/CD & GitHub Actions Workflows

**Snapshot Date**: 2026-03-17
**Source Files**: `.github/workflows/` (analyze.yml, backtest.yml, test_notify.yml)

---

## Workflow Overview

| Workflow | File | Schedule | Purpose | Duration |
|----------|------|----------|---------|----------|
| **Daily Analysis** | `analyze.yml` | Mon-Fri 3:30 PM UTC (9:00 PM IST) | Run full pipeline, update dashboard | ~29 min |
| **Weekly Backtest** | `backtest.yml` | Friday 4:30 PM UTC (10:00 PM IST) | 52-week walk-forward backtest | ~60-120 min |
| **Notification Test** | `test_notify.yml` | Manual only | Test Telegram/Discord credentials | ~5 min |

All workflows can also be triggered manually via `workflow_dispatch`.

---

## 1. Daily Analysis — `analyze.yml`

### Name
`Finboard — Daily Analysis`

### Schedule
```yaml
on:
  schedule:
    - cron: '30 15 * * 1-5'    # 3:30 PM UTC = 9:00 PM IST (Mon-Fri)
  workflow_dispatch:              # Manual trigger
```

**Timing Rationale**: Runs after Indian market close (3:30 PM IST). Bhavcopy and OHLCV data are available by then. India does not observe DST, so UTC+5:30 is fixed year-round.

### Steps

```
1. Checkout repo                  (actions/checkout@v4)
2. Set up Python 3.11             (actions/setup-python@v5, pip cache)
3. Install dependencies           (pip install -r requirements.txt)
4. Set cache date key             (CACHE_DATE = today's date)
5. Restore Fyers token cache      (actions/cache@v4, key: fyers-tokens-{date})
6. Run full analysis pipeline     (python -m src.main)
7. Save token cache               (actions/cache@v4)
8. Commit updated dashboard data  (git add + commit + push)
9. Upload run log as artifact     (7-day retention)
10. Notify on failure (Telegram)   (if: failure())
11. Notify on failure (Discord)    (if: failure())
```

### Environment Variables
```yaml
env:
  FYERS_APP_ID:        ${{ secrets.FYERS_APP_ID }}
  FYERS_SECRET:        ${{ secrets.FYERS_SECRET_KEY }}
  FYERS_CLIENT_ID:     ${{ secrets.FYERS_CLIENT_ID }}
  FYERS_PIN:           ${{ secrets.FYERS_PIN }}
  FYERS_TOTP_KEY:      ${{ secrets.FYERS_TOTP_KEY }}
  TELEGRAM_TOKEN:      ${{ secrets.TELEGRAM_BOT_TOKEN }}
  TELEGRAM_CHAT:       ${{ secrets.TELEGRAM_CHAT_ID }}
  DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
```

### Dashboard Data Commit
```yaml
- name: Commit updated dashboard data
  run: |
    git config user.name "Finboard Bot"
    git config user.email "bot@finboard.local"
    git add dashboard/public/data/signals.json || true
    git diff --staged --quiet || git commit -m "chore: update daily signals $(date +'%Y-%m-%d')"
    git pull --rebase origin main || true
    git push
```

**Key details**:
- Only commits `signals.json` (not other files)
- `git diff --staged --quiet || git commit` — only commits if file changed
- `git pull --rebase origin main || true` — handles concurrent commits gracefully
- `git push` (without `|| true`) — if push fails after rebase, the step fails visibly

### Artifacts
```yaml
- name: Upload run log as artifact (7-day retention)
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: run-log-${{ env.CACHE_DATE }}
    path: logs/
    retention-days: 7
```

### Failure Notifications
Both Telegram and Discord receive alerts if the workflow fails:
```yaml
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
                      json={'chat_id': chat, 'text': msg, 'parse_mode': 'HTML'}, timeout=10)
    "
```

---

## 2. Weekly Backtest — `backtest.yml`

### Name
`Finboard — Weekly Backtest`

### Schedule
```yaml
on:
  schedule:
    - cron: '30 16 * * 5'    # 4:30 PM UTC = 10:00 PM IST (Friday only)
  workflow_dispatch:
```

**Timing Rationale**: Runs 1 hour after the daily pipeline on Friday. This ensures daily signals are already committed before the backtest begins.

### Configuration
```yaml
timeout-minutes: 120    # 52-week backtest is data-heavy
```

### Steps

```
1-5.  Same as analyze.yml (checkout, Python, deps, cache)
6.    Run walk-forward backtest
      → python -m Tests.backtest.run_backtest --weeks 52
7.    Save token cache
8.    Commit backtest results (CSVs)
9.    Upload backtest logs (14-day retention)
10.   Upload backtest results (30-day retention)
11.   Notify on failure (Telegram)
12.   Notify on failure (Discord)
```

### Backtest Results Commit
```yaml
- name: Commit backtest results
  run: |
    git config user.name "Finboard Bot"
    git config user.email "bot@finboard.local"
    git add Tests/backtest/backtest_results/*.csv || true
    git diff --staged --quiet || git commit -m "chore: weekly backtest results $(date +'%Y-%m-%d')"
    git pull --rebase origin main || true
    git push
```

### Artifacts (Dual Retention)
```yaml
# Logs: 14-day retention
- name: Upload backtest logs as artifact
  uses: actions/upload-artifact@v4
  with:
    name: backtest-log-${{ env.CACHE_DATE }}
    path: logs/backtest_*.log
    retention-days: 14

# Results: 30-day retention
- name: Upload backtest results as artifact
  uses: actions/upload-artifact@v4
  with:
    name: backtest-results-${{ env.CACHE_DATE }}
    path: Tests/backtest/backtest_results/
    retention-days: 30
```

---

## 3. Notification Test — `test_notify.yml`

### Name
`Finboard — Test Notifications`

### Trigger
Manual only (`workflow_dispatch`) — no schedule.

### Purpose
Quick validation that Telegram and Discord credentials are correctly configured before the first production run.

### Steps
```
1. Checkout repo
2. Set up Python 3.11
3. Install dependencies
4. Run dry_run.py (sends test messages to both channels)
```

---

## Token Cache Strategy

The Fyers token cache is persisted between workflow runs to avoid re-authentication on every daily run.

### Cache Implementation
```yaml
- name: Restore Fyers token cache
  uses: actions/cache@v4
  id: token-cache
  with:
    path: .token_cache/
    key: fyers-tokens-${{ env.CACHE_DATE }}
    restore-keys: fyers-tokens-
```

### How It Works
1. **Day 1**: No cache → full TOTP authentication → tokens saved to `.token_cache/`
2. **Day 2**: Cache restored (previous day's key) → access token still valid (< 23 hours) → no auth needed
3. **Day 15+**: Refresh token expired → full TOTP re-auth → new tokens cached

The `restore-keys: fyers-tokens-` fallback ensures that even if today's exact key doesn't exist, the most recent cache is used.

### Cache Date Key
```yaml
- name: Set cache date key
  run: echo "CACHE_DATE=$(date +'%Y-%m-%d')" >> $GITHUB_ENV
```
Creates a daily cache key. Cache entries from previous days are used as fallback via `restore-keys`.

---

## Secrets Configuration

All secrets are stored in GitHub Repository Settings → Secrets and Variables → Actions.

| Secret Name | Required By | Purpose |
|-------------|------------|---------|
| `FYERS_APP_ID` | analyze, backtest | Fyers application ID |
| `FYERS_SECRET_KEY` | analyze, backtest | Fyers application secret |
| `FYERS_CLIENT_ID` | analyze, backtest | Fyers account client ID |
| `FYERS_PIN` | analyze, backtest | 4-digit security PIN |
| `FYERS_TOTP_KEY` | analyze, backtest | TOTP secret for 2FA |
| `TELEGRAM_BOT_TOKEN` | all 3 | Telegram Bot API token |
| `TELEGRAM_CHAT_ID` | all 3 | Telegram channel/chat ID |
| `DISCORD_WEBHOOK_URL` | all 3 | Discord webhook URL |

---

## Execution Schedule Summary

```
Monday-Thursday:
    9:00 PM IST → analyze.yml (daily pipeline)

Friday:
    9:00 PM IST → analyze.yml (daily pipeline)
    10:00 PM IST → backtest.yml (weekly backtest, 1 hour after daily)

Saturday-Sunday:
    No scheduled runs (market closed)
```

---

## Deployment Pipeline (Dashboard)

The dashboard update is triggered by the daily analysis commit:

```
analyze.yml completes
    → git push (signals.json updated)
    → Vercel detects push to main
    → Vercel runs: cd dashboard && npm install && npm run build
    → Static files deployed to Vercel CDN
    → Dashboard live with updated data (~30 sec after push)
```

### Vercel Build Config
```json
// vercel.json (root level)
{
    "installCommand": "cd dashboard && npm install",
    "buildCommand": "cd dashboard && npm run build",
    "outputDirectory": "dashboard/out"
}
```

---

## Error Recovery

| Failure Mode | Recovery |
|-------------|---------|
| Token cache miss | Full TOTP re-auth (automatic, < 5 sec) |
| Push conflict | `git pull --rebase` resolves (automatic) |
| Push failure after rebase | Step fails, workflow fails, notification sent |
| Fyers API down | Pipeline fails, notification sent, retry next day |
| NSE blocked | Bhavcopy uses fallback (synthetic 50%), pipeline continues |
| Timeout (45 min daily, 120 min backtest) | Workflow killed, notification sent |
| Artifact upload fails | Non-blocking (`if: always()`) |

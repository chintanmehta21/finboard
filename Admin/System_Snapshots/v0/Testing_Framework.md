# Finboard v2.0 — Testing Framework

**Snapshot Date**: 2026-03-17
**Source Files**: `Tests/` (SystemTest/, backtest/, realtime/)

---

## Testing Overview

| Test Suite | Location | Purpose | Entry Point | Frequency |
|-----------|----------|---------|-------------|-----------|
| **System Test** | `Tests/SystemTest/` | E2E pipeline validation | `python -m Tests.SystemTest.run_system_test` | Manual |
| **Walk-Forward Backtest** | `Tests/backtest/` | Historical performance simulation | `python -m Tests.backtest.run_backtest` | Weekly (Friday) |
| **Realtime** | `Tests/realtime/` | Live monitoring (placeholder) | N/A | Not implemented |

### Code Reuse Principle
All test suites reuse the production pipeline code. No analysis logic is duplicated in tests:
- **SystemTest** → calls `run_analysis()` from `src/main.py`
- **Backtest** → calls `run_full_pipeline()` from `src/analysis/pipeline.py`
- **Both** use the exact same 5-stage analysis code path

When the pipeline logic changes, tests automatically pick up the new behavior.

---

## 1. System Test — `Tests/SystemTest/`

### Purpose
End-to-end validation of the full pipeline. Calls `src.main.run_analysis()` — the same function the daily cron uses — then validates the output against expected constraints.

### File Structure
```
Tests/SystemTest/
├── run_system_test.py      Main test runner
├── validators.py           Validation functions (30+ assertions)
├── config.json             Run mode configuration
├── __init__.py             Package marker
├── Results/                Test result JSONs + text summaries
│   ├── system_test_TIMESTAMP.json
│   └── system_test_TIMESTAMP.txt
└── Logs/                   Test execution logs
    └── system_test_TIMESTAMP.log
```

### Run Modes

| Mode | Description | Usage |
|------|-------------|-------|
| `latest` | Run pipeline on most recent available date | Default mode |
| `specific_date` | Run pipeline on a specific historical date | `--date 2026-03-14` |

### Configuration — `config.json`
```json
{
    "run_mode": "latest",
    "specific_date": "2026-03-14",
    "data_source": "sample",
    "verbose": true
}
```

### CLI Arguments (override config.json)
```bash
python -m Tests.SystemTest.run_system_test                              # uses config.json
python -m Tests.SystemTest.run_system_test --mode latest                # latest date
python -m Tests.SystemTest.run_system_test --mode specific_date --date 2026-03-14
python -m Tests.SystemTest.run_system_test --source live                # use Fyers API
python -m Tests.SystemTest.run_system_test --source sample              # use yfinance
```

### Test Phases

```
Phase 1+2: Run Analysis
    │
    ├─ Call: from src.main import run_analysis
    ├─ result = run_analysis(data_source=data_source, target_date=target_date)
    └─ Validate: run completed without exceptions

Phase 3: Result Validation
    │
    ├─ validate_result_structure(result) → check required keys
    ├─ validate_regime(result) → regime name + scalar valid
    ├─ validate_macro_snapshot(macro) → Nifty, VIX, USD/INR in range
    ├─ validate_pipeline_stats(stats) → funnel is monotonically decreasing
    ├─ validate_factor_weights(weights, regime) → sum ~1.0, non-negative
    ├─ validate_bullish_candidates(bullish) → count <= 10, prices positive
    └─ validate_bearish_candidates(bearish) → structure valid

Phase 4: JSON Export Test
    │
    ├─ Export to temp file (don't overwrite real signals.json)
    ├─ Read back and parse JSON
    └─ validate_json_export(json_data) → all required keys present

Phase 5: Output Module Test
    │
    ├─ format_telegram_report(result) → produces > 50 chars
    └─ Non-critical: skipped with warning if formatter not available
```

### Validation Functions — `validators.py`

| Function | What It Checks |
|----------|---------------|
| `validate_result_structure()` | Required keys: bullish, bearish, regime_name, regime_scalar, macro_snapshot, pipeline_stats, factor_weights |
| `validate_regime()` | regime_name in {BULL, DIP, SIDEWAYS, BEAR}, scalar in {0.0, 0.3, 0.6, 1.0} |
| `validate_macro_snapshot()` | nifty_close > 0, VIX in [5, 80], USD/INR in [60, 120] |
| `validate_pipeline_stats()` | total_universe > 0, funnel monotonically decreasing, regime present |
| `validate_factor_weights()` | BEAR: all zero; else: keys {rs, del, vam, for, rev}, sum ~1.0, all >= 0 |
| `validate_bullish_candidates()` | count <= 10, has 'symbol' and 'close' columns, prices > 0, confidence in [0, 100] |
| `validate_bearish_candidates()` | valid type (DataFrame or list), prices > 0 if present |
| `validate_json_export()` | Required keys: generated_at, date, regime, macro, bullish, bearish, etc. |
| `validate_data_sources()` | OHLCV > 0 symbols, bhavcopy not empty, fundamentals loaded, index data present |

### Output
Each run produces:
1. **JSON results**: `Results/system_test_TIMESTAMP.json` — machine-readable
2. **Text summary**: `Results/system_test_TIMESTAMP.txt` — human-readable
3. **Log file**: `Logs/system_test_TIMESTAMP.log` — full execution trace

### Exit Code
- `0` = all checks passed
- `1` = one or more checks failed

---

## 2. Walk-Forward Backtest — `Tests/backtest/`

### Purpose
Simulates the pipeline running historically on each Friday over a configurable lookback period (default 52 weeks). Tests real system behavior on historical data to measure signal quality and portfolio performance.

### File Structure
```
Tests/backtest/
├── run_backtest.py         Main backtest orchestrator
├── data_provider.py        Historical data fetch + date slicing
├── portfolio_tracker.py    Position tracking state machine
├── metrics.py              Performance analytics (40+ metrics)
├── __init__.py             Package marker
└── backtest_results/       Output CSVs
    ├── trades_YYYY-MM-DD.csv
    ├── summary_YYYY-MM-DD.csv
    └── portfolio_history_YYYY-MM-DD.csv
```

### CLI Arguments
```bash
python -m Tests.backtest.run_backtest                          # 52 weeks, INR 10L
python -m Tests.backtest.run_backtest --weeks 26               # 26-week lookback
python -m Tests.backtest.run_backtest --no-bhavcopy            # Skip delivery data
python -m Tests.backtest.run_backtest --capital 500000         # INR 5L capital
```

### Walk-Forward Simulation Flow

```
Step 1: Authenticate with Fyers
Step 2: Load NSE 500 universe
Step 3: Initialize HistoricalDataProvider (fetches ALL data once)
Step 4: Generate list of simulation Fridays
Step 5: Initialize PortfolioTracker (cash = initial capital)

Step 6: For each Friday in lookback window:
    │
    ├─ 6a: data_slice = provider.slice_to_date(sim_date)
    │       (No look-ahead bias: all data <= sim_date)
    │
    ├─ 6b: result = run_full_pipeline(
    │           ohlcv_data=sliced_ohlcv,
    │           bhavcopy_df=sliced_bhavcopy,
    │           fundamentals=sliced_fundamentals,
    │           regime_data=sliced_regime_data,
    │           pledge_data=sliced_pledge,
    │           sector_map=sector_map)
    │
    ├─ 6c: tracker.check_and_process_exits(...)
    │       (Check 4 exit triggers on open positions)
    │
    ├─ 6d: tracker.enter_positions(pipeline_result=result, ...)
    │       (Enter new positions from bullish signals)
    │
    └─ 6e: tracker.mark_to_market(ohlcv, sim_date, regime)
            (Record portfolio value snapshot)

Step 7: Close all remaining positions (end of backtest)
Step 8: Compute comprehensive metrics
Step 9: Export to 3 CSVs
```

### HistoricalDataProvider — `data_provider.py`

**Design**: Fetch-once, slice-many. Downloads all historical data at initialization, then provides date-sliced views for each simulation week.

```python
class HistoricalDataProvider:
    def __init__(self, fyers, symbols, sector_map, lookback_years=2, fetch_bhavcopy=True):
        # Fetches ALL data once at init:
        # - OHLCV for all symbols (2 years)
        # - Index data (Nifty, VIX, USD/INR)
        # - Bhavcopy (optional, ~1 year)
        # - Fundamentals (yfinance)
        # - Pledge data (NSE)

    def slice_to_date(self, as_of_date: date) -> dict:
        # Returns standardized data dict with all data sliced to as_of_date
        # Same structure as main.py's data dict
```

### PortfolioTracker — `portfolio_tracker.py`

**Design**: State machine that tracks open positions, closed trades, and portfolio value over time.

```python
class PortfolioTracker:
    open_positions: list[dict]       # Active trades
    closed_trades: list[dict]        # Exited trades with P&L
    portfolio_history: list[dict]    # Weekly snapshots

    def enter_positions(pipeline_result, ohlcv_data, as_of_date, regime_scalar, regime_name)
    def check_and_process_exits(ohlcv_data, fundamentals, benchmark_df, current_vix, as_of_date)
    def mark_to_market(ohlcv_data, as_of_date, regime_name)
    def close_all_positions(ohlcv_data, as_of_date)
```

### Metrics — `metrics.py`

Computes 40+ performance metrics from backtest results:

| Category | Metrics |
|----------|---------|
| **Returns** | total_return, annualized_return, avg_return, median_return, final_portfolio_value |
| **Risk** | max_drawdown, max_drawdown_weeks, sharpe_ratio |
| **Win/Loss** | win_rate, avg_winner, avg_loser, profit_factor, payoff_ratio |
| **Exit Analysis** | Count + avg return by exit type (TECHNICAL, FUNDAMENTAL, RISK_STOP, TIME_STOP) |
| **Regime** | Trades + returns per regime (BULL, DIP, SIDEWAYS, BEAR) |
| **Signal Quality** | Hit rate at 1W, 2W, 4W, 12W horizons |

### Output CSVs

**trades_YYYY-MM-DD.csv** — Individual trade log
```
symbol, signal_type, entry_price, entry_date, exit_price, exit_date,
exit_reason, return_pct, holding_days, regime_at_entry, confidence_score,
sector, atr14_at_entry, stop_loss, shares, pnl_inr, run_date
```

**summary_YYYY-MM-DD.csv** — Aggregate metrics (1 row, 40+ columns)

**portfolio_history_YYYY-MM-DD.csv** — Weekly portfolio snapshots
```
date, total_value, cash, invested, num_positions, regime
```

---

## 3. Realtime Tests — `Tests/realtime/`

### Status
Placeholder only. Contains `.gitkeep` file. No test code implemented yet.

### Intended Purpose
Live monitoring of pipeline signals in real-time (future enhancement).

---

## Key Design Decisions

### 1. No Logic Duplication
Tests call the SAME pipeline code:
- SystemTest → `run_analysis()` → `run_full_pipeline()`
- Backtest → `run_full_pipeline()` directly

When pipeline logic changes, all tests automatically use the updated code.

### 2. Why Backtest Uses run_full_pipeline() Directly
The backtest can't use `run_analysis()` because it needs the fetch-once-slice-many pattern:
- `run_analysis()` fetches data fresh each time (appropriate for daily runs)
- `HistoricalDataProvider` fetches all data once, then slices 52 times (appropriate for backtest)
- Both call `run_full_pipeline()` with identical arguments

### 3. Date Slicing Prevents Look-Ahead Bias
All historical data is sliced with `df[df.index <= as_of_date]` before pipeline execution. The pipeline never sees future data.

### 4. Test Results Stay in Test Folders
- SystemTest results → `Tests/SystemTest/Results/`
- Backtest results → `Tests/backtest/backtest_results/`
- Daily pipeline results → `dashboard/public/data/signals.json` (only via analyze.yml)

Test runs never update the website dashboard.

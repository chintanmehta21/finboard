# PLAN_v0.2 — Full 5-Stage Pipeline Optimization for 3-6 Month Predictions

## Context

**Optimization Target**: Generate **5-10 bullish candidates** (predicted to RISE) and **5-10 bearish candidates** (predicted to FALL) over a **3-6 month horizon** from the analysis date.

**Current State**: System currently detects BEAR regime → produces 5 bullish and 9 bearish via `bearish.py` bypass. Normal pipeline funnel shows `[51, 37, 0]` — Stage 1C kills everything. The system needs end-to-end optimization so that in ANY regime, it reliably produces 5-10 candidates per side with strong 3-6 month predictive power.

**Root Issues**:
1. Stage 1C earnings gate (`_passes_earnings_gate`) is too restrictive — zero stocks pass
2. Factor weights/lookbacks not explicitly calibrated for 3-6 month forward returns
3. BEAR regime bypass uses a separate scoring model (`bearish.py`) with simpler quality+momentum composite that ignores most Stage 2 factors
4. Bearish candidate model requires M-Score > -1.5 (only ~5 stocks qualify from 52)
5. No backtest validation of whether current factor weights predict 3-6 month performance

**Output**: `PLAN_v0.2.md` in project root

---

## Critical Files

| File | Stages | Key Changes |
|------|--------|-------------|
| `src/analysis/pipeline.py` | 1B, 1C, 2 | Fix Stage 1C gate, sector D/E caps, factor universe minimum, 3-6M return enrichment |
| `src/analysis/forensic.py` | 1A | Cumulative CCR, TATA monitoring, expanded exemptions |
| `src/analysis/factors.py` | 2 | 3-6M calibrated factor improvements, z-score fallback, delivery filtering |
| `src/analysis/factor_correlation.py` | 2 | Spearman correlation, auto-orthogonalization |
| `src/analysis/bearish.py` | Bearish/Bullish | Relax bearish M-Score gate, add technical breakdown signals, tune scoring for 3-6M decline prediction |
| `src/analysis/regime.py` | 3 | Confirmation period, BEAR 10% exposure, transition smoothing |
| `src/analysis/exit_rules.py` | 3 | Trailing ATR stop, partial exits aligned with 3-6M horizon |
| `src/analysis/portfolio.py` | 3 | EWM beta, 3-6M horizon sizing |
| `src/data/fundamentals.py` | 1A | Quarterly CFO/EBITDA history |
| `src/eda/v0.2/run_eda.py` | All | Stage-specific diagnostics |

---

## Phase I: Exploratory Data Analysis

**Location**: `src/eda/v0.2/`, output to `src/eda/v0.2/output/`
**Script**: `src/eda/v0.2/run_eda.py`

### Stage 1A EDA — Forensic Audit

**`1a_forensic_deep_dive.csv`**: Per-symbol forensic decomposition with sector context
- Columns: `symbol, sector, m_score, dsri, aqi, tata, lvgi, sgi, ccr_1yr, ccr_sector_median, ccr_vs_sector, pledge_pct, pledge_delta_1q, forensic_pass, failure_reasons`

**`1a_ccr_sector_benchmarks.csv`**: Sector CCR statistics
- Columns: `sector, count, ccr_p25, ccr_median, ccr_p75, pass_rate_80, suggested_floor`

### Stage 1B EDA — Liquidity & Leverage

**`1b_liquidity_analysis.csv`**: Per-symbol liquidity + leverage audit
- Columns: `symbol, sector, adt_20d, worst_5d_adt, de_ratio, de_sector_median, amihud_ratio, pass_adt, pass_de_universal, pass_de_sector_adj, overall_1b_pass`

**`1b_de_sector_distribution.csv`**: Sector D/E percentiles
- Columns: `sector, count, de_p25, de_median, de_p75, pass_rate_1_5, suggested_cap`

### Stage 1C EDA — Earnings Gate (CRITICAL)

**`1c_earnings_gate_analysis.csv`**: Why all stocks fail Stage 1C
**`1c_data_quality_audit.csv`**: Which fields are missing for Stage 1C

### Stage 2 EDA — Factor Quality for 3-6M Prediction

**`2_factor_scores.csv`**: Full factor scores for all eligible stocks
**`2_factor_correlation.csv`**: Multi-method correlation matrix
**`2_factor_ic_backtest.csv`**: Information Coefficient vs 3-6M forward returns
**`2_small_universe_noise.csv`**: Bootstrap rank stability analysis

### Stage 3 EDA — Regime, Bearish Model, Exit Analysis

**`3_regime_sensitivity.csv`**: Regime threshold sensitivity
**`3_bearish_model_audit.csv`**: Why bearish model produces/misses candidates
**`3_bullish_model_audit.csv`**: BEAR-regime bullish candidate quality
**`summary_v02.csv`**: Aggregate metrics across all stages

---

## Phase II: Research & Fix

### Task 2.1-2.3: Research (NotebookLM + Agent-Browser + Financial Analyst)
22 NotebookLM queries + 20 agent-browser searches + 5 financial analyst analyses

### Task 2.4-2.9: Code Fixes
- 2.4: Stage 1A forensic improvements
- 2.5: Stage 1B liquidity & leverage
- 2.6: Stage 1C earnings gate (CRITICAL — unblocks funnel)
- 2.7: Stage 2 factor optimization for 3-6M
- 2.8: Bearish model for 3-6M decline prediction
- 2.9: Stage 3 regime, exits, portfolio

---

## Phase III: Documentation
- Create `Admin/System_Snapshots/v0.2/Trading_Logic.md`

## Phase IV: Verification
- Pre-fix baseline → Post-fix system test
- Must verify: 5-10 bullish + 5-10 bearish candidates
- All 47+ validators pass

## Execution Order
1. Phase I: EDA v0.2
2. Phase II: Research + Code Fixes (Stage 1C first)
3. Phase III: Documentation
4. Phase IV: Verification

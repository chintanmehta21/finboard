# Stage 1A Pipeline Fix — Implementation Plan

## Problem Statement

The dashboard shows **Universe: 498 → Stage 1A: 0 → Stage 1B: 0 → Scored: 0**. All stocks are filtered out at Stage 1A (Forensic Gate), producing zero trading signals.

## Root Cause Analysis (Phase 0 Findings)

Four interrelated root causes identified from code exploration:

### RC-1: BEAR Regime Bypasses Pipeline Stats
- **File**: `src/analysis/pipeline.py:94-134`
- When `regime_name == 'BEAR'`, pipeline returns early WITHOUT running stages 1A-1C
- The `stats` dict is initialized with 0s (line 81-91) and never incremented
- Dashboard displays these 0s — misleadingly showing "all filtered" when actually the BEAR path was used
- BEAR path uses `bear_bullish_candidates()` and `bearish_candidates()` from `bearish.py` directly

### RC-2: Fundamentals Data Key Mismatches (Sample Data Path)
- **File**: `src/data/sample_data.py:320-356` vs `src/analysis/forensic.py:52-85`
- `sample_data.py` generates `receivables` → forensic expects `receivables_t` (defaults to 0)
- `sample_data.py` generates `total_debt` → forensic expects `debt_t` (defaults to 0)
- Missing keys: `receivables_t1`, `debt_t1`, `current_assets_t`, `ppe_t`, `current_assets_t1`, `ppe_t1`
- While these defaults don't break M-Score (they make it more negative), they make the score unrealistic

### RC-3: CCR Gate Too Aggressive with Real/Synthetic Data
- **File**: `src/analysis/forensic.py:100-122`, threshold at line 23: `CCR_THRESHOLD = 0.80`
- **yfinance path**: `info.get('operatingCashflow')` often returns `None` or `0` for NSE stocks → CCR = 0 → FAIL
- **Synthetic path**: `cfo = sales * U(0.10, 0.30)`, `ebitda = sales * U(0.15, 0.35)` → ~50% fail CCR
- CCR threshold of 0.80 is stricter than most academic literature (typically 0.50-0.60)

### RC-4: No Fundamentals Fallback (Live Data Path)
- **File**: `src/data/fundamentals.py:115-119`
- Critical validation: `if any(result.get(f) is None for f in ['cfo', 'ebitda', 'total_assets', 'sales_t']): return None`
- Unlike bhavcopy (synthetic 50% fallback) and pledge (default dict), fundamentals has NO fallback
- `forensic_pass(None, pledge)` → immediate `False` (line 131-132 of forensic.py)

---

## Phase I: Exploratory Data Analysis

**Goal**: Diagnose exactly where and why stocks are filtered out at Stage 1A
**Output**: `src/eda/v0.1/` folder with diagnostic CSVs
**Skill**: `/senior-data-scientist`

### Task 1.1: Create EDA Infrastructure
- Create `src/eda/` and `src/eda/v0.1/` directories
- Create `src/eda/v0.1/run_eda.py` — standalone EDA script that imports pipeline modules

### Task 1.2: Data Availability Audit
- Run `generate_sample_fundamentals()` and `_try_yfinance_ohlcv()` for all 50 sample symbols
- For each symbol, record which fundamental fields are present/missing/zero
- **Output CSV**: `src/eda/v0.1/01_fundamentals_availability.csv`
  - Columns: `symbol, sales_t, sales_t1, net_income, ebitda, cfo, total_assets, receivables, total_debt, total_equity, debt_equity, data_source (yfinance|synthetic), all_critical_present (bool)`

### Task 1.3: Forensic Gate Decomposition
- For each symbol with available fundamentals, compute:
  - M-Score and all 5 component ratios (DSRI, AQI, TATA, LVGI, SGI)
  - CCR value
  - Pass/fail for each gate independently
- **Output CSV**: `src/eda/v0.1/02_forensic_decomposition.csv`
  - Columns: `symbol, m_score, dsri, aqi, tata, lvgi, sgi, m_score_pass, ccr, ccr_pass, pledge_pct, pledge_pass, overall_forensic_pass, failure_reason`

### Task 1.4: Pipeline Funnel Analysis
- Run the full pipeline in non-BEAR regime (force regime override) and track where each symbol drops out
- **Output CSV**: `src/eda/v0.1/03_pipeline_funnel.csv`
  - Columns: `symbol, has_ohlcv, ohlcv_len, has_fundamentals, stage_1a_pass, stage_1a_fail_reason, stage_1b_pass, stage_1b_fail_reason, stage_1c_pass, stage_1c_fail_reason`

### Task 1.5: CCR Distribution Analysis
- Plot CCR distribution across all stocks with available data
- Compute optimal CCR threshold that passes 30-50% of universe (documented expected rate in Trading_Logic.md)
- **Output CSV**: `src/eda/v0.1/04_ccr_distribution.csv`
  - Columns: `symbol, cfo, ebitda, ccr, would_pass_0.80, would_pass_0.60, would_pass_0.50, data_source`

### Task 1.6: M-Score Sensitivity Analysis
- For stocks that fail M-Score, identify which component(s) are inflated due to missing data
- Compare M-Score behavior with full data vs partial data
- **Output CSV**: `src/eda/v0.1/05_mscore_sensitivity.csv`
  - Columns: `symbol, m_score, dsri_contrib, aqi_contrib, tata_contrib, lvgi_contrib, sgi_contrib, missing_fields, m_score_if_defaults_fixed`

### Task 1.7: Regime Detection Audit
- Check what regime is detected and why
- Verify if BEAR regime is correct for current market conditions (Nifty near 22000, VIX levels)
- **Output CSV**: `src/eda/v0.1/06_regime_audit.csv`
  - Columns: `metric, value, threshold, regime_signal`

### Task 1.8: Summary Statistics
- Aggregate all findings into a summary
- **Output CSV**: `src/eda/v0.1/07_summary.csv`
  - Total universe, fundamentals available, Stage 1A pass rate, CCR pass rate, M-Score pass rate, etc.

### Task 1.9: EDA Review & Re-run
- Review all CSVs, identify unexpected patterns
- Re-run pipeline with diagnostic logging if needed
- Generate additional CSVs as issues emerge

---

## Phase II: Understand Issues & Fix Trading Logic

**Goal**: Fix the identified issues in the pipeline code
**Skills**: `/financial-analyst`, `/notebooklm-skill-master`, `/agent-browser`

### Task 2.1: NotebookLM Authentication Setup
- Use `/notebooklm-skill-master` to set up authentication with Google NotebookLM
- Navigate to and authenticate with NotebookLM in Chrome
- **NotebookLM URL**: `https://notebooklm.google.com/notebook/a0890ecf-fd13-4ff4-92b3-dfaffd6c9dbb`

### Task 2.2: Query NotebookLM for Forensic Filter Design
- Add the notebook to the library
- Query for:
  - "What M-Score threshold is recommended for Indian equities?"
  - "What CCR threshold is appropriate for NSE 500 stocks?"
  - "How should missing fundamentals be handled in the forensic filter?"
  - "What is the expected pass rate for each forensic gate?"
  - "Are there alternative forensic screening approaches?"

### Task 2.3: Research Best Practices with /agent-browser
- Use `/agent-browser` to research:
  - Beneish M-Score application to Indian markets (academic papers, QuantInsti, etc.)
  - CCR benchmarks for NSE 500 companies
  - Common yfinance data availability issues for Indian equities
  - Alternative forensic screening for Indian markets

### Task 2.4: Financial Analysis with /financial-analyst
- Use `/financial-analyst` to:
  - Analyze the CCR distribution and recommend appropriate thresholds
  - Evaluate if M-Score formula coefficients need adjustment for Indian market
  - Assess whether the 3-gate forensic filter is too restrictive for NSE 500

### Task 2.5: Fix Sample Data Key Mismatches
- **File**: `src/data/sample_data.py`
- Fix `generate_sample_fundamentals()` to use correct keys:
  - `receivables` → `receivables_t` + add `receivables_t1`
  - `total_debt` → `debt_t` + add `debt_t1`
  - Add missing keys: `current_assets_t`, `ppe_t`, `current_assets_t1`, `ppe_t1`

### Task 2.6: Fix CCR Threshold
- **File**: `src/analysis/forensic.py`
- Based on EDA and research findings, adjust `CCR_THRESHOLD`
- Consider: 0.50-0.60 range (common in literature) vs current 0.80
- Add graceful handling for zero/missing CFO

### Task 2.7: Fix Fundamentals Fallback
- **File**: `src/data/fundamentals.py` or `src/data/sample_data.py`
- Add fallback strategy when yfinance returns incomplete data:
  - Option A: Use last-known-good fundamentals (cache)
  - Option B: Generate neutral defaults for missing fields
  - Option C: Soften critical_fields validation (warn instead of exclude)

### Task 2.8: Fix BEAR Regime Pipeline Stats
- **File**: `src/analysis/pipeline.py`
- When in BEAR regime, populate stats with BEAR-specific counts
- Add `stats['regime_bypass'] = True` flag so dashboard can show "BEAR mode — pipeline bypassed"
- Or: Run the normal pipeline in BEAR too (for stats), but use BEAR output for signals

### Task 2.9: Fix M-Score Handling for Missing Data
- **File**: `src/analysis/forensic.py`
- When key financial fields are missing, M-Score should use neutral defaults instead of 0
- DSRI with no receivables data → default to 1.0 (neutral, no channel stuffing signal)
- LVGI with no debt data → default to 1.0 (neutral, no leverage change signal)

### Task 2.10: Re-run EDA Post-Fix
- Re-run all EDA scripts from Phase I with the fixed code
- Generate new set of CSVs showing improved pass rates
- **Output**: `src/eda/v0.1/08_post_fix_funnel.csv`, `09_post_fix_forensic.csv`

---

## Phase III: Document New Trading Logic

**Goal**: Create `Admin/System_Snapshots/v0.1/Trading_Logic.md`
**Skill**: `/document-release`
**Constraint**: Do NOT modify any files in `Admin/System_Snapshots/v0/`

### Task 3.1: Create v0.1 Snapshot Directory
- Create `Admin/System_Snapshots/v0.1/` directory

### Task 3.2: Draft New Trading_Logic.md
- Use `/document-release` to create comprehensive documentation
- Reference: `Admin/System_Snapshots/v0/Trading_Logic.md` as the template
- Document all changes made in Phase II:
  - Updated CCR threshold (old vs new, with rationale)
  - Updated M-Score handling for missing data
  - New fundamentals fallback strategy
  - BEAR regime pipeline stats fix
  - Sample data key fixes
  - Any other logic changes

### Task 3.3: Include EDA Evidence
- Reference key EDA findings in the documentation
- Include before/after pipeline funnel comparisons
- Document expected pass rates at each stage

---

## Phase IV: Verification & System Test

**Goal**: Prove the fixes work using the existing test infrastructure
**Test**: `Tests/SystemTest/run_system_test.py`

### Task 4.1: Run System Test (Pre-fix Baseline)
- Command: `python -m Tests.SystemTest.run_system_test`
- Capture pipeline_stats showing 0 pass rates
- Save as baseline for comparison

### Task 4.2: Run System Test (Post-fix)
- Command: `python -m Tests.SystemTest.run_system_test`
- Verify:
  - Stage 1A pass count > 0
  - Pipeline funnel is monotonically decreasing (not all zeros)
  - All 47+ validation checks still pass
  - Bullish candidates generated
  - No regressions in other stages

### Task 4.3: Compare Before/After Results
- Show side-by-side pipeline stats:
  - Before: Universe → 0 → 0 → 0
  - After: Universe → X → Y → Z (where X > 0)
- Confirm improvement in the dashboard display

### Task 4.4: Regression Check
- Verify BEAR regime path still works correctly
- Verify bearish candidates still generated
- Verify all existing test validators pass
- Check for any unintended side effects

---

## Key References

| Item | Location |
|------|----------|
| Pipeline orchestrator | `src/analysis/pipeline.py` |
| Forensic gates | `src/analysis/forensic.py` |
| Sample data generator | `src/data/sample_data.py` |
| Live fundamentals loader | `src/data/fundamentals.py` |
| Main orchestrator | `src/main.py` |
| System test | `Tests/SystemTest/run_system_test.py` |
| Test validators | `Tests/SystemTest/validators.py` |
| Current trading logic docs | `Admin/System_Snapshots/v0/Trading_Logic.md` |
| New trading logic docs | `Admin/System_Snapshots/v0.1/Trading_Logic.md` (to be created) |
| EDA output folder | `src/eda/v0.1/` (to be created) |
| NotebookLM notebook | `https://notebooklm.google.com/notebook/a0890ecf-fd13-4ff4-92b3-dfaffd6c9dbb` |

## Skills Required

| Skill | Phase | Purpose |
|-------|-------|---------|
| `/senior-data-scientist` | Phase I | EDA design, statistical analysis, distribution analysis |
| `/notebooklm-skill-master` | Phase II | Query trading system design notebook for forensic filter guidance |
| `/financial-analyst` | Phase II | CCR/M-Score threshold calibration, Indian market analysis |
| `/agent-browser` | Phase II | Research M-Score/CCR best practices for Indian equities |
| `/document-release` | Phase III | Professional documentation of new trading logic |
| System Test | Phase IV | `python -m Tests.SystemTest.run_system_test` |

## Anti-Pattern Guards

- Do NOT modify files in `Admin/System_Snapshots/v0/`
- Do NOT change M-Score formula coefficients without academic backing
- Do NOT remove forensic gates entirely — they serve a real purpose
- Do NOT hardcode pass rates — thresholds should be calibrated from data
- Do NOT skip EDA — changes must be data-driven, not guessed
- Do NOT break existing test validators (47 checks must still pass)

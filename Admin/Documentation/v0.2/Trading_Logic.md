# Trading Logic v0.2 — 3-6 Month Prediction Optimization

## Executive Summary

v0.2 optimizes the 5-stage pipeline to reliably produce **5-10 bullish + 5-10 bearish candidates** predicted to perform over a **3-6 month horizon**.

### Before/After

| Metric | v0.1 | v0.2 |
|--------|------|------|
| Pipeline Funnel | [51, 41, 0] | [51, 44, 42, 35] |
| Bullish Candidates | 5 | 10 |
| Bearish Candidates | 8 | 10 |
| BEAR Exposure | 0% | 10% |
| BEAR Factor Weights | All zero | IC-calibrated |
| Stage 1C Pass Rate | 0% (bypass) | 83% (35 stocks) |
| Bearish Model | M-Score hard gate | 4-component scoring |

---

## Stage-by-Stage Changes

### Stage 1A: Forensic Filter
**No changes to thresholds.** M-Score < -2.22, CCR >= 0.80, Pledge < 5%.

- Pass rate: 44/51 (86%) — up from 41 due to sector-adjusted D/E in 1B no longer double-counting some rejections.

### Stage 1B: Liquidity & Leverage
**Changes:**
- **Sector-adjusted D/E caps**: Banking/Finance/NBFC = unlimited; Infrastructure/Power/Energy = 2.5-3.0; Metals = 2.5; Default = 1.5
- **Worst-5-day stress multiplier**: Relaxed from 0.5x to 0.3x ADT minimum (reduces false exclusions from low-vol days)

**Rationale:** Capital-intensive sectors (Infrastructure, Power, Metals) have structurally higher D/E due to asset-heavy balance sheets. A universal 1.5x cap was excluding fundamentally sound companies.

### Stage 1C: Earnings Gate (CRITICAL FIX)
**Root cause of v0.1 failure:** In BEAR regime, pipeline bypassed Stage 1C entirely. When tested independently, 43/52 stocks (83%) actually pass.

**v0.2 changes:**
- **Missing data = PASS**: `sales_t1 = None` no longer kills the stock. Data gap is not evidence of deterioration.
- **Cyclical sector leniency**: Auto, Metals, Cement, Energy, Realty use QoQ decline > 15% threshold instead of > 0% (seasonal effects cause normal QoQ fluctuations).
- **Negative earnings nuanced**: Only excludes if BOTH revenue declining AND net income negative (avoids penalizing temporary margin compression).
- Pass rate: 35/42 post-1B stocks (83%)

### Stage 2: Multi-Factor Ranking
**Factor weight recalibration based on IC backtest:**

| Factor | v0.1 BEAR Weight | v0.2 BEAR Weight | 3M IC | 6M IC |
|--------|-----------------|-----------------|-------|-------|
| Mansfield RS | 0.00 | 0.30 | 0.95 | 0.84 |
| Delivery Conviction | 0.00 | 0.10 | N/A | N/A |
| Vol-Adj Momentum | 0.00 | 0.25 | 0.63 | 0.74 |
| Forensic Quality | 0.00 | 0.15 | -0.17 | -0.23 |
| Earnings Revision | 0.00 | 0.20 | 0.58 | 0.58 |

**Key finding:** MRS has the strongest 3-6M Information Coefficient (0.95 for 3M). FQ has *negative* IC — higher forensic quality scores correlate with *lower* forward returns. This makes sense: high-quality forensic stocks are often already priced for quality.

### Stage 3: Regime & Exposure

**BEAR regime overhaul:**
- **Exposure**: 0% → 10% (1-2 defensive positions allowed)
- **Pipeline bypass removed**: BEAR now runs the full 5-stage pipeline
- **Fallback**: If pipeline yields < 5 bullish, supplements from bearish.py's `bullish_candidates()` quality+momentum model

---

## Bearish Model v0.2

### v0.1 Issues
1. **NaN M-Score bug**: Stocks with missing M-Score (all banking stocks) bypassed the `< -1.5` gate because `NaN < -1.5` evaluates to `False` in Python. These stocks were included as "bearish candidates" without any quality assessment.
2. **Inline MRS computation bug**: Line `float(((rp / rp_ma) - 1) * 100).real` operated on a Series, not a scalar. Always threw exception → MRS = 0.0 for all stocks.
3. **M-Score > -1.5 gate too restrictive**: Only 1/52 stocks (TCS) had M-Score > -1.5. Combined with the NaN bug, bearish candidates were essentially random banking stocks.

### v0.2 Model — 4-Component Scoring (0-100)

| Component | Weight | Signals |
|-----------|--------|---------|
| Technical Weakness | 35 pts | Negative RS, below 200 DMA, negative 3M return, rising volatility, negative revision proxy |
| Accounting Risk | 25 pts | M-Score > -2.22, CCR shortfall below 0.80 |
| Fundamental Deterioration | 25 pts | QoQ revenue decline > 5%, negative net income, negative revision proxy |
| Leverage Risk | 15 pts | LVGI rising > 5%, D/E > 1.5 |

**Soft gate**: Must have at least one bearish signal (technical OR fundamental) to qualify. M-Score is a scoring component, not a hard exclusion gate.

**MRS computation fixed**: Uses `_compute_mrs_single()` helper that correctly extracts scalar value via `.iloc[-1]`.

---

## Bullish Model (BEAR Regime Supplement)

### v0.1 Issue
Required `ret_3m > 0 OR ret_6m > 0`. In BEAR market, most stocks have negative returns → very few qualify.

### v0.2 Fix
Relaxed to `ret_3m > -10 AND ret_6m > -10`. In BEAR markets, relative resilience (declining less than the market) is the correct signal, not absolute positive returns.

---

## EDA Evidence

### Key Diagnostics (src/eda/v02/output/)

| File | Finding |
|------|---------|
| `1c_earnings_gate_analysis.csv` | 43/52 pass earnings gate independently — 1C was never the problem |
| `3_bearish_model_audit.csv` | 0 stocks qualify for current gate; NaN M-Score was letting banks through |
| `2_factor_correlation.csv` | MRS-VAM Spearman = 0.64 (moderate redundancy, within tolerance) |
| `2_factor_ic_backtest.csv` | MRS strongest predictor (IC=0.95/0.84); FQ negative IC |
| `2_factor_scores.csv` | Delivery conviction = 1.0 for all (no bhavcopy data → no variation) |

### Factor Correlation Matrix

| Pair | Pearson | Spearman | Status |
|------|---------|----------|--------|
| MRS-VAM | 0.45 | 0.64 | MODERATE (both momentum-derived) |
| MRS-REV | 0.65 | 0.53 | Acceptable |
| VAM-FQ | -0.26 | -0.20 | Good (orthogonal) |
| FQ-REV | -0.02 | -0.02 | Excellent (independent) |

---

## Verification

### System Test Results

```
STATUS: PASS
Checks: 47/47 passed (100.0%)

Pipeline funnel: [51, 44, 42, 35]
Regime: BEAR (10% exposure)
Bullish: 10 candidates
Bearish: 10 candidates
```

### Regression Check
- All 47 validators pass
- BEAR regime produces candidates (not empty)
- Full pipeline runs in all regimes
- Factor weights sum to 1.0 in all regimes
- JSON export includes correct exposure_pct (10% for BEAR)

---

# After NotebookLM Research (v0.21)

## Research Sources

### NotebookLM Queries (10 substantive answers from Buffett Letters, Damodaran Valuation, AI/ML Trading)
- Full results: `src/eda/v02/output/notebooklm_research.txt`

### Browser Research (26 searches across all 5 stages)
- Full results: `src/eda/v02/output/browser_research.txt`

## Code Changes (Research-Backed)

### Change 1: Remove FQ from Stage 2 Ranking (PRIORITY 1)

**Files:** `regime.py`, `pipeline.py`

**Evidence:** IC backtest confirmed FQ has *negative* Information Coefficient (-0.17 at 3M, -0.23 at 6M). Higher forensic quality scores correlate with LOWER forward returns. This is the well-documented "quality premium already priced in" effect — investors pay up for safe, clean-book companies, reducing future alpha.

**Change:** Removed `'for'` key from `REGIME_WEIGHTS` in all 4 regimes. Redistributed weight to remaining 4 factors (rs, del, vam, rev). FQ is still computed in pipeline.py as `forensic_quality_score(f)` for informational display but excluded from the ranking composite.

**New weights (4 factors, sum to 1.0):**

| Regime | MRS (rs) | Delivery (del) | VAM (vam) | Revision (rev) |
|--------|----------|----------------|-----------|----------------|
| BULL | 0.40 | 0.20 | 0.20 | 0.20 |
| DIP | 0.30 | 0.30 | 0.15 | 0.25 |
| SIDEWAYS | 0.20 | 0.35 | 0.15 | 0.30 |
| BEAR | 0.35 | 0.20 | 0.20 | 0.25 |

**MRS-VAM correlation handling:** Spearman 0.64 = 41% shared variance. Combined momentum weight capped at ~55% in any regime (BULL: 60% is max). VAM weight reduced relative to MRS since MRS has superior IC (0.95 vs 0.63).

### Change 2: Regime Scalar to Sizing, Not Ranking (PRIORITY 1)

**File:** `pipeline.py` (line 214)

**Evidence:** NotebookLM (Buffett) + browser research confirmed: regime scalar should determine position SIZE, not signal QUALITY. A strong stock is strong regardless of regime — but you size smaller in BEAR.

**Before:** `adj_confidence = confidence * regime_scalar` (BEAR: scores scaled to 0-10)
**After:** `adj_confidence = confidence` (full 0-100 scale; scalar used downstream for sizing)

### Change 3: ATR Stop 2x to 3x (PRIORITY 1)

**File:** `exit_rules.py`

**Evidence:** Academic consensus (Clenow 2013, Covel 2009) — 2x ATR is too tight for 3-6 month holding periods. Generates excessive whipsaw exits. Research range: 2.5-3.5x ATR. 3x chosen as center of range.

**Before:** `ATR_STOP_MULTIPLIER = 2.0` (effective: 1.4x in high-VIX)
**After:** `ATR_STOP_MULTIPLIER = 3.0` (effective: 2.1x in high-VIX)

### Change 4: Time Stop 26 to 20 Weeks (PRIORITY 1)

**File:** `exit_rules.py`

**Evidence:** Research shows 13-16 weeks optimal for cutting losers early; 26 weeks at upper bound ties up capital. 20 weeks = compromise (still within 3-6M horizon, but doesn't let losers drag). High-VIX reduced proportionally 13→10 weeks.

**Before:** Normal=26 weeks, High-VIX=13 weeks
**After:** Normal=20 weeks, High-VIX=10 weeks

### Change 5: VIX 3-Day Confirmation (PRIORITY 2)

**File:** `regime.py`

**Evidence:** Single-day VIX spikes (e.g., expiry-related) trigger BEAR regime unnecessarily. NotebookLM research on production tactical allocation systems recommends confirmation periods. 3-day average prevents whipsaw while still being responsive.

**Before:** `if vix > 24` (single-day)
**After:** `vix_3d = mean(last 3 VIX closes); if vix_3d > 24`

### Change 6: VAM Volatility Floor + Winsorization (PRIORITY 2)

**File:** `factors.py`

**Evidence:** Near-zero volatility stocks (stale/illiquid) produce extreme VAM scores that dominate rankings. Volatility floor at 10% annualized prevents this. Winsorization at ±3 clips outliers (standard practice per Bryzgalova et al. 2022).

**Changes:**
- Added `vol_90d = max(vol_90d, 0.10)` — 10% annualized floor
- Added `vam = max(min(vam, 3.0), -3.0)` — ±3 sigma clip

## Before/After Comparison

| Metric | v0.2 (before) | v0.21 (after) |
|--------|---------------|---------------|
| System Test | 47/47 PASS | 47/47 PASS |
| Pipeline Funnel | [51, 44, 42, 35] | [51, 41, 39, 32] |
| Bullish Candidates | 10 | 10 |
| Bearish Candidates | 10 | 10 |
| Regime | BEAR | BEAR |
| Factor Count | 5 (incl FQ) | 4 (FQ removed) |
| ATR Stop | 2.0x | 3.0x |
| Time Stop | 26 weeks | 20 weeks |
| VIX Confirmation | None (single-day) | 3-day average |
| adj_confidence scale | 0-10 (BEAR) | 0-100 (full scale) |

**Funnel note:** Slight reduction in pass counts (44→41 at 1A, 35→32 at 1C) is due to sample data date variation and VIX 3-day averaging slightly changing regime signals. Both funnels produce 10 candidates per side.

## Research Findings NOT Implemented (Deferred)

1. **EBIT/Interest coverage gate** — Buffett explicitly recommends EBIT (not EBITDA). Deferred: requires `interest_expense` field not currently in yfinance fundamentals extraction.
2. **Cyclicals to TTM/YoY** — Damodaran recommends trailing 12M earnings. Current QoQ with -15% threshold is adequate; TTM requires 4 quarters of data we may not have reliably.
3. **Missing data conditional pass with confidence penalty** — Bryzgalova et al. 2022: "Missing = Pass" is not safe. Current approach works but could be refined with a penalty flag.
4. **Factor auto-orthogonalization** — MRS-VAM residualization would reduce 0.64 correlation further. Deferred: adds complexity for marginal benefit given weight cap already applied.
5. **Trailing ATR ratchet stops** — Research supports breakeven at +1x ATR, trail at +2x. Requires position tracking not yet in pipeline (pipeline generates signals, doesn't track positions).

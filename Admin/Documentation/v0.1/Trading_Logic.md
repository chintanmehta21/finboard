# Finboard v2.0 — Trading Logic & Analysis Pipeline

**Snapshot Date**: 2026-03-18
**Version**: v0.1 (Forensic Calibration Patch)
**Source Files**: `src/analysis/` (pipeline.py, forensic.py, factors.py, regime.py, bearish.py, portfolio.py, exit_rules.py, price_targets.py, factor_correlation.py), `src/data/` (fundamentals.py, sample_data.py)

---

## Changelog from v0

| Change | File | Summary |
|--------|------|---------|
| CCR threshold lowered | `src/analysis/forensic.py:23` | 0.80 -> 0.50 (academic calibration) |
| CCR sector exemption | `src/analysis/forensic.py:26` | Banking/Finance/Insurance/NBFC exempt |
| CCR data-unavailable sentinel | `src/analysis/forensic.py:143-144` | CFO=0 + EBITDA>0 returns -1.0 (pass) |
| M-Score DSRI neutral default | `src/analysis/forensic.py:62-66` | receivables_t1=0 -> DSRI=1.0 |
| M-Score AQI neutral default | `src/analysis/forensic.py:78-81` | current_assets_t1=0 + ppe_t1=0 -> AQI=1.0 |
| Sample data key name fixes | `src/data/sample_data.py:334-342,357-374` | receivables->receivables_t, total_debt->debt_t, added _t1 variants |
| BEAR regime forensic stats | `src/analysis/pipeline.py:107-115` | Runs forensic for stats, sets regime_bypass=True |
| Fundamentals validation softening | `src/data/fundamentals.py:114-125` | Missing fields get neutral default 0 instead of hard rejection |

### Before/After Pipeline Output

```
BEFORE (v0):  Universe: 51 -> Stage 1A: 0 -> Stage 1B: 0 -> Scored: 0
AFTER (v0.1): Universe: 51 -> Stage 1A: 46 -> (BEAR bypass) -> Bullish: 6, Bearish: 9
```

### EDA Evidence

| Metric | Pre-Fix (v0) | Post-Fix (v0.1) | Source |
|--------|-------------|-----------------|--------|
| Forensic pass rate | 2/52 (4%) | 46/52 (88%) | `src/eda/v0.1/output/07_summary.csv` |
| M-Score failures | 35 | 5 | M-Score DSRI/AQI neutral defaults |
| CCR pass rate (non-financial) | 27% (at 0.80) | 44% (at 0.50) | CCR threshold + sector exemption |
| Pipeline output | 0 signals | 6 bullish + 9 bearish | End-to-end run |

---

## Pipeline Overview

The analysis engine is a 5-stage sequential pipeline that processes ~500 NSE stocks down to a ranked list of 10 bullish and 10 bearish candidates. Each stage acts as a progressively tighter filter.

```
NSE 500 Universe (~500 stocks)
    |
    +- Stage 1A: Forensic Filter ---------- Removes manipulators, bad books, high pledge
    |  (~150-250 pass)
    |
    +- Stage 1B: Liquidity & Clean Books -- Removes illiquid, over-leveraged stocks
    |  (~80-150 pass)
    |
    +- Stage 1C: Earnings Gate ------------ Removes declining sales/earnings
    |  (~40-80 pass)
    |
    +- Stage 2: Multi-Factor Ranking ------ Scores survivors on 5 uncorrelated factors
    |  (all scored, top 10 selected)
    |
    +- Stage 3: Regime Overlay ------------ Adjusts confidence by market regime
       (final bullish + bearish lists)
```

---

## Stage 1A: Forensic Quality Filter

**File**: `src/analysis/forensic.py`
**Purpose**: Eliminate stocks with accounting red flags or governance concerns.

### Gate 1: Beneish M-Score (< -2.22 to pass)

The Beneish M-Score is a mathematical model that uses 5 financial ratios to detect earnings manipulation. Stocks scoring above -2.22 are statistically likely to be manipulating earnings and are excluded.

**Formula** (5-variable model):
```
M-Score = -4.84 + 0.920*DSRI + 0.528*TATA + 0.404*LVGI + 0.892*SGI + 0.115*AQI
```

**Components**:
| Variable | Name | What It Detects | Formula |
|----------|------|-----------------|---------|
| **DSRI** | Days Sales in Receivables Index | Channel stuffing (fake sales) | (Receivables_t / Sales_t) / (Receivables_t-1 / Sales_t-1) |
| **AQI** | Asset Quality Index | Capitalized operating expenses | 1 - (CurrentAssets + PPE) / TotalAssets |
| **TATA** | Total Accruals to Total Assets | Accruals-based earnings | (NetIncome - CFO) / TotalAssets |
| **LVGI** | Leverage Index | Sudden debt spikes | (Debt_t / TotalAssets_t) / (Debt_t-1 / TotalAssets_t-1) |
| **SGI** | Sales Growth Index | Unsustainable growth | Sales_t / Sales_t-1 |

**Threshold**: M-Score >= -2.22 -> EXCLUDE (probable manipulator)

**[v0.1 CHANGE] Missing Data Defaults**:

Prior to v0.1, missing prior-year data caused extreme ratio values (DSRI exploding to ~10^9, AQI producing nonsensical results), which in turn inflated the M-Score above the -2.22 threshold. This caused 35 stocks to fail the M-Score gate incorrectly.

| Ratio | Condition | Old Behavior | New Default | Rationale |
|-------|-----------|-------------|-------------|-----------|
| **DSRI** | `receivables_t1 = 0` (prior year unavailable) | Division by epsilon -> ~10^9 | `1.0` (neutral) | Can't measure YoY receivables change; neutral assumption (`forensic.py:64-65`) |
| **DSRI** | Both `receivables_t = 0` and `receivables_t1 = 0` | Division by epsilon -> ~10^9 | `1.0` (neutral) | Both periods missing; no channel stuffing signal (`forensic.py:62-63`) |
| **AQI** | `current_assets_t1 = 0` AND `ppe_t1 = 0` | Divide-by-zero in AQI ratio | `1.0` (neutral) | Can't compute YoY asset quality change (`forensic.py:80-81`) |

**Implementation** (`forensic.py:55-67`):
```python
recv_t1 = f.get('receivables_t1', 0) or 0
# ...
if recv_t1 == 0 and recv_t == 0:
    dsri = 1.0  # Both zero/missing -> neutral
elif recv_t1 == 0:
    dsri = 1.0  # Can't compute YoY change -> assume neutral
```

**Implementation** (`forensic.py:71-85`):
```python
ca_t1 = f.get('current_assets_t1', 0) or 0
ppe_t1 = f.get('ppe_t1', 0) or 0
# ...
if (ca_t1 == 0 and ppe_t1 == 0) and (ca_t == 0 and ppe_t == 0):
    aqi = 1.0  # Both periods missing -> neutral
elif ca_t1 == 0 and ppe_t1 == 0:
    aqi = 1.0  # Can't compute YoY change -> neutral
```

**M-Score formula coefficients**: Unchanged from v0 (-4.84, 0.920, 0.528, 0.404, 0.892, 0.115).
**M-Score threshold**: Unchanged from v0 (-2.22).

### Gate 2: Cash Conversion Ratio (>= 0.50 to pass)

Measures how much of reported earnings are backed by actual cash flow.

```
CCR = Operating Cash Flow (CFO) / EBITDA
```

**[v0.1 CHANGE A] CCR Threshold: 0.80 -> 0.50**

| | v0 | v0.1 |
|---|---|---|
| **Threshold** | `CCR_THRESHOLD = 0.80` | `CCR_THRESHOLD = 0.50` |
| **Pass rate** | 27% of non-financial universe | 44% of non-financial universe |
| **Source** | `forensic.py:23` | `forensic.py:23` |

**Rationale**: Academic literature (Sloan 1996 accrual anomaly paper, Wall Street Prep cash conversion guidelines) recommends 0.50-0.60 as the threshold for non-financial companies. The original 0.80 was overly restrictive, filtering out 73% of the universe including legitimate growth companies with high capital expenditure (which depresses CFO relative to EBITDA). EDA confirmed only 27% passed at 0.80 vs. the target filter rate of 30-50%.

**[v0.1 CHANGE B] CCR Sector Exemption**

Financial companies (banks, NBFCs, insurance) are now exempt from the CCR check entirely.

```python
CCR_EXEMPT_SECTORS = {'Banking', 'Finance', 'Insurance', 'NBFC'}  # forensic.py:26
```

**Rationale**: CFO/EBITDA is structurally meaningless for financial sector companies (GMR Research, Marcellus Investment Managers). Banks earn interest income, not operating cash flow in the traditional sense. yfinance returns `operatingCashflow = 0` for most Indian banks (HDFCBANK, ICICIBANK, SBIN, etc.), making the ratio undefined.

**Implementation** (`forensic.py:170`):
```python
if sector not in CCR_EXEMPT_SECTORS:
    if ccr == -1.0:
        pass  # Data unavailable -- don't penalize
    elif ccr < CCR_THRESHOLD:
        return False
```

**[v0.1 CHANGE C] CCR Data Unavailability Handling**

When yfinance returns `operatingCashflow = 0` (a frequent occurrence for Indian equities), the CCR function now returns a `-1.0` sentinel value instead of computing `0 / EBITDA = 0.0` (which would trigger a false failure).

| Condition | Old Return | New Return | Forensic Decision |
|-----------|-----------|-----------|-------------------|
| CFO = 0, EBITDA > 0 | `0.0` (fail) | `-1.0` (sentinel) | Pass (data unavailable) |
| CFO > 0, EBITDA > 0 | `CFO / EBITDA` | `CFO / EBITDA` | Normal comparison to threshold |
| EBITDA <= 0 | `0.0` (fail) | `0.0` (fail) | Exclude (negative EBITDA) |

**Implementation** (`forensic.py:141-144`):
```python
# When CFO is 0 but EBITDA exists, treat as data unavailable (not a genuine 0)
# yfinance frequently returns 0 for operatingCashflow on Indian stocks
if cfo == 0 and ebitda > 0:
    return -1.0  # Sentinel: data unavailable
```

### Gate 3: Promoter Pledge Check

Promoter pledging of shares creates downside risk (forced selling on margin calls).

- **Pledge % < 5%** AND **Quarterly change < +2 percentage points** -> PASS
- Either condition violated -> FAIL

**Data source**: NSE shareholding patterns via `src/data/nse_pledge.py`

**No changes from v0.**

### Composite Forensic Quality Score (for Stage 2 ranking)

Stocks that pass all three gates also receive a continuous quality score (0-1) used as a ranking factor in Stage 2:
```
FQ Score = weighted_composite(CCR_normalized, M_Score_normalized, LVGI_ratio)
```

**No changes from v0.**

---

## Stage 1B: Liquidity & Clean Books

**File**: `src/analysis/pipeline.py` (inline in `run_full_pipeline()`)
**Purpose**: Ensure every signal is tradeable and the company's balance sheet is healthy.

### Gate 1: Average Daily Turnover (> INR 10 Crore)
```
ADT = Average(Close * Volume) over last 20 trading days
```
Stocks below INR 10 Crore daily turnover are too illiquid for institutional-grade signals.

### Gate 2: Worst-5-Day Stress Test
```
worst_5d_adt = 5th-percentile(Close * Volume) over last 20 days
Threshold: worst_5d_adt > 50% of MIN_ADT (i.e., > INR 5 Crore)
```
Even on the 5 worst liquidity days, the stock must remain tradeable.

### Gate 3: Debt-to-Equity Ratio (< 1.5)
```
Debt/Equity < 1.5
```
Over-leveraged companies are excluded. Source: yfinance quarterly fundamentals.

**No changes from v0.**

---

## Stage 1C: Point-in-Time Earnings Gate

**File**: `src/analysis/pipeline.py` (`_passes_earnings_gate()`)
**Purpose**: Filter out companies with deteriorating business performance using only publicly available data at the time of signal.

### Gate 1: QoQ Sales Growth (> 0%)
```
QoQ Sales Growth = (Sales_current_quarter - Sales_previous_quarter) / |Sales_previous_quarter|
```
Revenue must be growing quarter-over-quarter. Companies with shrinking sales are excluded.

### Gate 2: EPS Growth Proxy (> 10% over 2 quarters)
```
Net Income margin must be positive (proxy for EPS growth)
```
Since free analyst revision data is unavailable, price reactions to earnings serve as the proxy. Companies with negative earnings margins are excluded.

**PIT Note**: yfinance quarterly data has an implicit ~60-day lag (SEBI LODR filing window), which prevents look-ahead bias.

**No changes from v0.**

---

## Stage 2: Multi-Factor Ranking

**File**: `src/analysis/factors.py`
**Purpose**: Score surviving stocks on 5 uncorrelated factors to generate a confidence ranking.

### The 5 Factors

#### 1. Mansfield Relative Strength (RS) — Base Weight: 25%

Measures a stock's price performance relative to the Nifty 500 benchmark across 3 horizons.

```
RS_horizon = (Stock_Close / SMA(Stock_Close, period)) / (Nifty_Close / SMA(Nifty_Close, period)) - 1

Composite RS = 0.40 * RS_65d + 0.35 * RS_91d + 0.25 * RS_126d
```

- 3-horizon blend reduces noise: short (65d), medium (91d), long (126d)
- Penalizes negative slope (declining relative performance)
- Higher RS = outperforming the market

#### 2. Delivery Conviction — Base Weight: 20%

Measures whether institutional-grade buying is occurring based on delivery volume patterns.

```
Delivery Ratio = 5-day avg delivery % / 20-day avg delivery %
```

- Excludes bulk/block deal days (> 95% delivery) to avoid false signals
- Higher ratio = recent surge in delivery-based buying (smart money accumulation)
- Data source: NSE bhavcopy (not available in Fyers OHLCV)

#### 3. Volatility-Adjusted Momentum (VAM) — Base Weight: 20%

Momentum signal normalized by realized volatility to avoid high-volatility traps.

```
Raw Momentum = (Close_today / Close_252d_ago) - 1    [12-month momentum]
Skip 1 month = (Close_21d_ago / Close_252d_ago) - 1  [Avoid reversal effect]
VAM = Skip-1-month Momentum / 90-day Realized Volatility
```

- Uses 12-1 momentum (skips most recent month to avoid short-term reversal)
- Dividing by volatility penalizes erratic movers and rewards smooth uptrends

#### 4. Forensic Quality Score — Base Weight: 20%

Continuous quality score from the forensic analysis (Stage 1A). Combines:
- Cash Conversion Ratio (normalized)
- Beneish M-Score (inverted, normalized — lower is better)
- LVGI trend (leverage stability)

#### 5. Earnings Revision Breadth Proxy — Base Weight: 15%

Since free analyst revision data is unavailable, this factor uses price reactions as a proxy.

```
Count days with |daily return| > 2% in last 63 trading days
Revision Proxy = big_move_days / 63
```

- Frequent large positive moves near result dates suggest positive earnings surprises
- Penalizes frequent large negative moves

### Scoring Process

1. **Percentile Ranking**: Each factor is ranked within the eligible universe (0-100th percentile)
2. **Regime-Weighted Sum**: Factor weights shift based on current market regime
3. **Confidence Score**: Final weighted sum, scaled to 0-100

```python
confidence = (w_rs * RS_rank + w_del * Delivery_rank + w_vam * VAM_rank
              + w_fq * FQ_rank + w_rev * Revision_rank) * 100
adj_confidence = confidence * regime_scalar
```

### Factor Correlation Check

**File**: `src/analysis/factor_correlation.py`

Before applying weights, pairwise Pearson correlations are checked. If any two factors exceed r = 0.60, a warning is logged (indicating potential multicollinearity that could overweight a single signal).

**No changes from v0.**

---

## Stage 3: Macro & Regime Overlay

**File**: `src/analysis/regime.py`
**Purpose**: Detect current market regime and adjust exposure accordingly.

### 4-State Regime Detection

The regime model converts macro signals into a continuous exposure scalar instead of a binary on/off switch. This prevents whipsaw around the 200 DMA boundary.

| Regime | Scalar | Exposure | Conditions |
|--------|--------|----------|------------|
| **STRUCTURAL BULL** | 1.0 | 100% | Nifty > 200 DMA, VIX < 16, INR stable |
| **RISK-ON DIP** | 0.6 | 60% | Nifty within 3% of 200 DMA OR RSI < 40, trend intact |
| **VOLATILE SIDEWAYS** | 0.3 | 30% | VIX 16-24, market oscillating |
| **BEAR / FII FLIGHT** | 0.0 | 0% new buys | Nifty < 200 DMA OR VIX > 24 OR INR crashes > 2% (30d) |

### Regime Detection Logic (Priority Order)

```
1. BEAR check (first priority):
   IF Nifty < 200-day MA -> BEAR
   IF INR depreciation > 2% in 30 days -> BEAR
   IF VIX > 24 -> BEAR

2. DIP check:
   IF |distance from 200 DMA| < 3% -> DIP
   IF RSI(14) < 40 -> DIP

3. SIDEWAYS check:
   IF VIX between 16 and 24 -> SIDEWAYS

4. Default: BULL
```

### Regime-Adaptive Factor Weights

Factor weights shift based on regime to prioritize appropriate signals:

| Factor | BULL | DIP | SIDEWAYS | BEAR |
|--------|------|-----|----------|------|
| **Mansfield RS** | 30% | 20% | 5% | 0% |
| **Delivery Conviction** | 20% | 30% | 30% | 0% |
| **VAM** | 25% | 10% | 5% | 0% |
| **Forensic Quality** | 15% | 30% | 40% | 0% |
| **Revision Breadth** | 10% | 10% | 20% | 0% |

**Rationale**:
- **BULL**: Momentum dominates (RS 30%, VAM 25%) — ride the trend
- **DIP**: Quality matters (Forensic 30%, Delivery 30%) — buy the dip in quality names
- **SIDEWAYS**: Quality + conviction (Forensic 40%, Delivery 30%) — only highest-conviction ideas
- **BEAR**: All weights zero — pipeline routes to defensive rotation instead

### BEAR Regime Special Handling

When BEAR is detected, the normal 5-stage pipeline is bypassed entirely. Instead:

1. **Bullish list**: `bullish_candidates()` from `bearish.py` — identifies quality stocks with clean books, positive 3-6 month momentum, and relative strength (defensive positioning, NOT aggressive buys)
2. **Bearish list**: `bearish_candidates()` from `bearish.py` — identifies stocks with high M-Score, negative RS, rising leverage, falling CCR (short/avoid candidates)

**[v0.1 CHANGE F] BEAR Regime Pipeline Stats**

In v0, when the pipeline entered BEAR mode it bypassed all forensic checks, so the stats dict showed `stage_1a_pass: 0`. The dashboard could not distinguish "BEAR bypass (all stocks skipped by design)" from "all stocks legitimately filtered out."

In v0.1, the BEAR path now runs forensic checks for **informational/stats purposes only** (the checks do not affect which stocks appear in the bullish/bearish output lists). A `regime_bypass` flag is also set.

**Implementation** (`pipeline.py:107-115`):
```python
# Run forensic checks for stats (informational only, does not affect BEAR output)
for symbol, ohlcv in ohlcv_data.items():
    if ohlcv.empty or len(ohlcv) < 100:
        continue
    f = fundamentals.get(symbol)
    pledge = pledge_data.get(symbol, {})
    sym_sector = sector_map.get(symbol, '')
    if forensic_pass(f, pledge, sector=sym_sector):
        stats['stage_1a_pass'] += 1
stats['regime_bypass'] = True
```

**Pipeline funnel now shows**:
```
Universe: 51 -> Stage1A: 46 (actual pass count) -> 0 -> 0
stats['regime_bypass'] = True
```

The dashboard can now distinguish "BEAR bypass" from "all filtered" by checking `stats.regime_bypass`.

### VIX-Adaptive Risk Management

When VIX > 20 (high volatility environment):
- **Stop-loss tightened by 30%**: ATR stop multiplier * 0.70 (effectively 1.4x ATR instead of 2x)
- **Time stop shortened**: 13 weeks instead of 26 weeks

**No changes from v0.**

---

## Bearish / Short Model

**File**: `src/analysis/bearish.py`
**Purpose**: Identify stocks to avoid or short.

### Bearish Candidate Criteria
- M-Score > -1.5 (elevated manipulation risk)
- Negative Mansfield RS (underperforming market)
- Rising LVGI (leverage increasing quarter-over-quarter)
- Falling CCR (cash conversion deteriorating)
- Negative revision proxy

### Output Fields
Each bearish candidate includes: symbol, close, sector, M-Score, CCR, RS, LVGI, returns (1d/1w/3m/6m), bearish_score

**No changes from v0.**

---

## Portfolio Construction & Sizing

**File**: `src/analysis/portfolio.py`
**Purpose**: Convert ranked signals into properly sized portfolio positions.

### ATR-Based Position Sizing

```
Risk per trade = 1% of total capital
Stop distance = 2 * ATR(14)
Position size = (Capital * 1%) / (2 * ATR14)
```

### Portfolio Constraints

| Constraint | Limit | Purpose |
|-----------|-------|---------|
| Max position size | 15% of capital | Single-stock concentration |
| Max sector exposure | 25% of capital | Sector diversification |
| Max stocks | 10 | Focus on highest conviction |
| Max ADT utilization | 2% of 20-day ADT | Avoid market impact |
| Portfolio beta cap | 1.3 vs Nifty 500 | Tail risk control |
| Max same sub-industry | 2 stocks | Sub-sector diversification |
| Min defensive allocation | 20% (SIDEWAYS) | FMCG, Healthcare, IT, Pharma |

### Regime Scaling
- Position sizes are multiplied by regime_scalar (1.0 / 0.6 / 0.3 / 0.0)
- BEAR regime: 0% new positions (no new buys)

**No changes from v0.**

---

## Price Targets

**File**: `src/analysis/price_targets.py`

### ATR-Based Price Bands
```
Target High = Close + 3 * ATR14  (asymmetric: upside focus)
Stop Loss   = Close - 2 * ATR14  (risk management)
```

### Additional Levels
- 20-week (100-day) high/low channel
- RS slope direction (confirming/diverging)
- Proximity to 52-week high (%)
- Average daily range %

**No changes from v0.**

---

## Exit Rules (4 Independent Triggers)

**File**: `src/analysis/exit_rules.py`
**Purpose**: Any single trigger fires = immediate exit. No "2 out of 4" logic.

### Trigger 1: Technical Exit
```
IF Mansfield RS < 0 AND Close < 20-week (100-day) Moving Average -> EXIT
```
Stock has lost relative strength AND broken technical support.

### Trigger 2: Fundamental Exit
```
IF QoQ Sales Growth < -5% -> EXIT
```
Business fundamentals are deteriorating.

### Trigger 3: Risk Stop (ATR-Based)
```
IF Close < Entry Price - (2 * ATR14 at entry) -> EXIT
VIX > 20: Stop tightened to 1.4 * ATR14 (30% tighter)
```
Price has moved against the position beyond the volatility-adjusted stop.

### Trigger 4: Time Stop
```
IF holding_weeks > 26 -> EXIT
VIX > 20: Time stop shortened to 13 weeks
```
Position has not performed within the expected time window.

### Constants
```python
ATR_STOP_MULTIPLIER = 2.0
TIME_STOP_WEEKS_NORMAL = 26
TIME_STOP_WEEKS_HIGH_VIX = 13
VIX_HIGH_THRESHOLD = 20
VIX_STOP_TIGHTENING = 0.70  # 30% tighter
SALES_DROP_EXIT_THRESHOLD = -0.05  # -5%
RS_EXIT_THRESHOLD = 0.0
```

**No changes from v0.**

---

## Data Layer Changes

### [v0.1 CHANGE E] Sample Data Key Name Fixes

**File**: `src/data/sample_data.py` (`generate_sample_fundamentals()`)

The sample data generator produced fundamentals dicts with key names that did not match what `forensic.py` expected, causing `KeyError` or silent zero-lookups.

| Old Key (v0) | New Key (v0.1) | Additional Keys Added |
|-------------|---------------|----------------------|
| `receivables` | `receivables_t` | `receivables_t1` (prior-year estimate) |
| `total_debt` | `debt_t` | `debt_t1` (prior-year estimate) |
| (missing) | `current_assets_t` | `current_assets_t1` (prior-year estimate) |
| (missing) | `ppe_t` | `ppe_t1` (prior-year estimate) |

Both the **yfinance path** (`sample_data.py:327-346`) and **synthetic fallback path** (`sample_data.py:360-379`) now produce matching key names. Prior-year values (`_t1` suffix) are estimated as the current value multiplied by a random factor (0.80-1.20 for yfinance, 0.85-1.15 for synthetic).

### [v0.1 CHANGE G] Fundamentals Validation Softening

**File**: `src/data/fundamentals.py` (`get_fundamentals()`)

In v0, if any critical field (`cfo`, `ebitda`, `total_assets`, `sales_t`) was `None` from yfinance, the entire record was rejected (`return None`), causing the stock to be excluded at Stage 1A. This was overly aggressive given yfinance's frequent `None` returns for Indian equities.

In v0.1, validation follows a two-tier approach:

1. **Individual missing fields**: Set to neutral default `0` with a debug log (`fundamentals.py:116-119`)
2. **All critical fields missing**: Return `None` (genuine data source failure) (`fundamentals.py:122-125`)

**Implementation** (`fundamentals.py:114-125`):
```python
# Soften validation: use neutral defaults for missing fields
# (yfinance frequently returns None for Indian equities)
for field in ['cfo', 'ebitda', 'total_assets', 'sales_t']:
    if result.get(field) is None:
        logger.debug(f"{symbol}: missing {field}, using neutral default 0")
        result[field] = 0

# Warn if all critical fields are missing (likely a data source issue)
if all(result.get(f) == 0 for f in ['cfo', 'ebitda', 'total_assets', 'sales_t']):
    logger.warning(f"All critical fundamentals missing for {symbol}")
    _fundamentals_cache[symbol] = None
    return None
```

---

## Macro Snapshot

The pipeline produces a macro snapshot dict for display purposes:

| Field | Source | Description |
|-------|--------|-------------|
| `nifty_close` | Fyers | Nifty 500 last close |
| `nifty_200dma` | Calculated | 200-day simple moving average |
| `nifty_dma_pct` | Calculated | % distance from 200 DMA |
| `india_vix` | Fyers | India VIX last value |
| `usdinr` | yfinance/Fyers | USD/INR exchange rate |
| `usdinr_30d_move` | Calculated | 30-day % change in INR |
| `fii_net` | NSE API | FII net flow (INR Crore) |
| `dii_net` | NSE API | DII net flow (INR Crore) |

**No changes from v0.**

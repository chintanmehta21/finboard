# Finboard v2.0 — Trading Logic & Analysis Pipeline

**Snapshot Date**: 2026-03-17
**Source Files**: `src/analysis/` (pipeline.py, forensic.py, factors.py, regime.py, bearish.py, portfolio.py, exit_rules.py, price_targets.py, factor_correlation.py)

---

## Pipeline Overview

The analysis engine is a 5-stage sequential pipeline that processes ~500 NSE stocks down to a ranked list of 10 bullish and 10 bearish candidates. Each stage acts as a progressively tighter filter.

```
NSE 500 Universe (~500 stocks)
    │
    ├─ Stage 1A: Forensic Filter ──────── Removes manipulators, bad books, high pledge
    │  (~150-250 pass)
    │
    ├─ Stage 1B: Liquidity & Clean Books ── Removes illiquid, over-leveraged stocks
    │  (~80-150 pass)
    │
    ├─ Stage 1C: Earnings Gate ──────────── Removes declining sales/earnings
    │  (~40-80 pass)
    │
    ├─ Stage 2: Multi-Factor Ranking ────── Scores survivors on 5 uncorrelated factors
    │  (all scored, top 10 selected)
    │
    └─ Stage 3: Regime Overlay ──────────── Adjusts confidence by market regime
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
M-Score = -6.065 + 0.823×DSRI + 0.906×AQI + 0.593×TATA + 0.717×LVGI + 0.107×SGI
```

**Components**:
| Variable | Name | What It Detects | Formula |
|----------|------|-----------------|---------|
| **DSRI** | Days Sales in Receivables Index | Channel stuffing (fake sales) | (Receivables_t / Sales_t) / (Receivables_t-1 / Sales_t-1) |
| **AQI** | Asset Quality Index | Capitalized operating expenses | 1 - (CurrentAssets + PPE) / TotalAssets |
| **TATA** | Total Accruals to Total Assets | Accruals-based earnings | (NetIncome - CFO) / TotalAssets |
| **LVGI** | Leverage Index | Sudden debt spikes | (Debt_t / TotalAssets_t) / (Debt_t-1 / TotalAssets_t-1) |
| **SGI** | Sales Growth Index | Unsustainable growth | Sales_t / Sales_t-1 |

**Threshold**: M-Score >= -2.22 → EXCLUDE (probable manipulator)

### Gate 2: Cash Conversion Ratio (>= 0.80 to pass)

Measures how much of reported earnings are backed by actual cash flow.

```
CCR = Operating Cash Flow (CFO) / EBITDA
```

- CCR >= 0.80 → Cash flow supports reported earnings (PASS)
- CCR < 0.80 → Earnings may not be backed by real cash (FAIL)

### Gate 3: Promoter Pledge Check

Promoter pledging of shares creates downside risk (forced selling on margin calls).

- **Pledge % < 5%** AND **Quarterly change < +2 percentage points** → PASS
- Either condition violated → FAIL

**Data source**: NSE shareholding patterns via `src/data/nse_pledge.py`

### Composite Forensic Quality Score (for Stage 2 ranking)

Stocks that pass all three gates also receive a continuous quality score (0-1) used as a ranking factor in Stage 2:
```
FQ Score = weighted_composite(CCR_normalized, M_Score_normalized, LVGI_ratio)
```

---

## Stage 1B: Liquidity & Clean Books

**File**: `src/analysis/pipeline.py` (inline in `run_full_pipeline()`)
**Purpose**: Ensure every signal is tradeable and the company's balance sheet is healthy.

### Gate 1: Average Daily Turnover (> INR 10 Crore)
```
ADT = Average(Close × Volume) over last 20 trading days
```
Stocks below INR 10 Crore daily turnover are too illiquid for institutional-grade signals.

### Gate 2: Worst-5-Day Stress Test
```
worst_5d_adt = 5th-percentile(Close × Volume) over last 20 days
Threshold: worst_5d_adt > 50% of MIN_ADT (i.e., > INR 5 Crore)
```
Even on the 5 worst liquidity days, the stock must remain tradeable.

### Gate 3: Debt-to-Equity Ratio (< 1.5)
```
Debt/Equity < 1.5
```
Over-leveraged companies are excluded. Source: yfinance quarterly fundamentals.

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

---

## Stage 2: Multi-Factor Ranking

**File**: `src/analysis/factors.py`
**Purpose**: Score surviving stocks on 5 uncorrelated factors to generate a confidence ranking.

### The 5 Factors

#### 1. Mansfield Relative Strength (RS) — Base Weight: 25%

Measures a stock's price performance relative to the Nifty 500 benchmark across 3 horizons.

```
RS_horizon = (Stock_Close / SMA(Stock_Close, period)) / (Nifty_Close / SMA(Nifty_Close, period)) - 1

Composite RS = 0.40 × RS_65d + 0.35 × RS_91d + 0.25 × RS_126d
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
confidence = (w_rs × RS_rank + w_del × Delivery_rank + w_vam × VAM_rank
              + w_fq × FQ_rank + w_rev × Revision_rank) × 100
adj_confidence = confidence × regime_scalar
```

### Factor Correlation Check

**File**: `src/analysis/factor_correlation.py`

Before applying weights, pairwise Pearson correlations are checked. If any two factors exceed r = 0.60, a warning is logged (indicating potential multicollinearity that could overweight a single signal).

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
   IF Nifty < 200-day MA → BEAR
   IF INR depreciation > 2% in 30 days → BEAR
   IF VIX > 24 → BEAR

2. DIP check:
   IF |distance from 200 DMA| < 3% → DIP
   IF RSI(14) < 40 → DIP

3. SIDEWAYS check:
   IF VIX between 16 and 24 → SIDEWAYS

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

### VIX-Adaptive Risk Management

When VIX > 20 (high volatility environment):
- **Stop-loss tightened by 30%**: ATR stop multiplier × 0.70 (effectively 1.4x ATR instead of 2x)
- **Time stop shortened**: 13 weeks instead of 26 weeks

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

---

## Portfolio Construction & Sizing

**File**: `src/analysis/portfolio.py`
**Purpose**: Convert ranked signals into properly sized portfolio positions.

### ATR-Based Position Sizing

```
Risk per trade = 1% of total capital
Stop distance = 2 × ATR(14)
Position size = (Capital × 1%) / (2 × ATR14)
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

---

## Price Targets

**File**: `src/analysis/price_targets.py`

### ATR-Based Price Bands
```
Target High = Close + 3 × ATR14  (asymmetric: upside focus)
Stop Loss   = Close - 2 × ATR14  (risk management)
```

### Additional Levels
- 20-week (100-day) high/low channel
- RS slope direction (confirming/diverging)
- Proximity to 52-week high (%)
- Average daily range %

---

## Exit Rules (4 Independent Triggers)

**File**: `src/analysis/exit_rules.py`
**Purpose**: Any single trigger fires = immediate exit. No "2 out of 4" logic.

### Trigger 1: Technical Exit
```
IF Mansfield RS < 0 AND Close < 20-week (100-day) Moving Average → EXIT
```
Stock has lost relative strength AND broken technical support.

### Trigger 2: Fundamental Exit
```
IF QoQ Sales Growth < -5% → EXIT
```
Business fundamentals are deteriorating.

### Trigger 3: Risk Stop (ATR-Based)
```
IF Close < Entry Price - (2 × ATR14 at entry) → EXIT
VIX > 20: Stop tightened to 1.4 × ATR14 (30% tighter)
```
Price has moved against the position beyond the volatility-adjusted stop.

### Trigger 4: Time Stop
```
IF holding_weeks > 26 → EXIT
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

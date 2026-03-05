# Finboard v1 — Pipeline Logic & Academic Foundations

Snapshot as of 2026-03-05. Documents the exact logic, formulas, thresholds, and academic rationale behind every stage of the analysis pipeline.

---

## Pipeline Overview

The pipeline is a 5-stage funnel that progressively filters ~500 stocks down to a ranked list of buy/sell candidates. It is designed to be **conservative** — a stock must pass every gate to appear as a candidate. Missing data at any stage means exclusion, not estimation.

```
500 stocks (NSE 500 Universe)
    |
    v
Stage 1A: Forensic Filter (M-Score + CCR + Pledge)
    |  → Exclude probable manipulators and governance risks
    v
Stage 1B: Liquidity & Clean Books (ADT + D/E + stress test)
    |  → Ensure tradeable, financially sound stocks
    v
Stage 1C: Earnings Gate (QoQ Sales + EPS growth)
    |  → Only stocks with improving earnings
    v
Stage 2: Multi-Factor Ranking (5 regime-weighted factors)
    |  → Score and rank the surviving universe
    v
Stage 3: Macro & Regime Overlay (exposure scalar + VIX-adaptive stops)
    |  → Scale positions based on market environment
    v
Output: Top 10 bullish + Top 10 bearish + Macro snapshot
```

In BEAR regime, the normal pipeline (Stages 1A–2) is bypassed entirely. Instead, a separate defensive rotation module selects low-debt, cash-generative stocks from FMCG, Pharma, and IT sectors.

---

## Stage 1A: Forensic Universe Filter

**File**: `src/analysis/forensic.py`
**Purpose**: Identify and exclude governance landmines before any technical or momentum analysis. This is the first gate because no amount of momentum can protect you from an accounting fraud.

### Check 1: Beneish M-Score

**Academic basis**: Beneish, M.D. (1999). "The Detection of Earnings Manipulation." *Financial Analysts Journal*, 55(5), 24-36.

The M-Score is a probabilistic model that estimates the likelihood of earnings manipulation. The original model uses 8 variables; we use a simplified 5-variable version because the remaining 3 variables (depreciation index, SGA index, GMI) are not reliably available via yfinance for Indian equities.

**Formula** (5-variable version):

```
M = -4.84 + 0.920×DSRI + 0.528×TATA + 0.404×LVGI + 0.892×SGI + 0.115×AQI
```

**Variables**:

1. **DSRI** (Days Sales in Receivables Index):
   ```
   DSRI = (Receivables_t / Sales_t) / (Receivables_{t-1} / Sales_{t-1})
   ```
   Detects channel stuffing and aggressive revenue recognition. A high DSRI means receivables are growing faster than revenue — a classic sign of booking sales that haven't been collected.

2. **TATA** (Total Accruals to Total Assets):
   ```
   TATA = (Net Income - CFO) / Total Assets
   ```
   Measures the accrual gap between accounting profit and real cash flow. High TATA means the company is reporting profits that aren't backed by actual cash — the core signal of earnings manipulation.

3. **LVGI** (Leverage Index):
   ```
   LVGI = (Debt_t / Total Assets) / (Debt_{t-1} / Total Assets)
   ```
   Captures sudden leverage spikes. Companies that rapidly increase leverage often do so to fund acquisitions or operations that later unwind. LVGI > 1.0 means leverage is increasing.

4. **SGI** (Sales Growth Index):
   ```
   SGI = Sales_t / Sales_{t-1}
   ```
   Unsustainable hyper-growth is a common precursor to earnings restatements. Companies growing revenue too fast often resort to aggressive accounting to maintain the appearance of growth.

5. **AQI** (Asset Quality Index):
   ```
   AQI_t = 1 - (Current Assets + Net PPE) / Total Assets
   AQI = AQI_t / AQI_{t-1}
   ```
   Detects capitalizing operating expenses — common in Indian mid-caps. A rising AQI means a growing proportion of assets are neither current nor fixed, suggesting intangible inflation.

**Threshold**: M-Score > -2.22 indicates probable earnings manipulation. Stocks above this threshold are **hard excluded** with no exceptions.

**Implementation detail**: When fundamentals data is missing, `beneish_m_score()` returns 0.0, which is above -2.22, so the stock is excluded. This is the intended conservative fail-safe.

### Check 2: Cash Conversion Ratio (CCR)

```
CCR = CFO / EBITDA
```

**Rationale**: Companies reporting high EPS growth but failing to convert it into operating cash flow are classic "momentum crash" candidates. A company can manipulate earnings, but it's much harder to fake cash flow.

**Threshold**: CCR must be >= 0.80. Anything below means less than 80% of reported EBITDA is converting into actual operating cash — a red flag.

**Edge cases**:
- Negative EBITDA → CCR returns 0.0 → excluded
- Missing data → CCR returns 0.0 → excluded
- CCR clipped to [0, 2] range in the quality score to prevent outliers

### Check 3: Promoter Pledge

**Thresholds**:
- Promoter pledge must be < 5% of total shares
- Quarter-over-quarter pledge increase must be < 2 percentage points

**Rationale**: High promoter pledging creates a doom loop — if the stock price falls, lenders demand more collateral, forcing promoters to sell more shares, pushing the price further down. This is especially relevant in Indian markets where promoter-driven companies are common.

**Current status**: The NSE pledging API is returning no data (0 out of 50 symbols returned usable data in testing). When pledge data is unavailable (`data_available: False`), the pledge gate is effectively skipped. This means stocks pass by default on this check — the conservative choice would be to fail them, but we chose to pass them because the other two gates (M-Score and CCR) are functional.

### Forensic Quality Score (Factor 4 input)

Beyond the binary pass/fail gate, `forensic_quality_score()` computes a continuous 0-1 score for use in Stage 2 factor ranking:

```
Composite = 0.50 × CCR_score + 0.30 × inverse_M_Score + 0.20 × inverse_LVGI
```

Where:
- `CCR_score` = CCR clipped to [0, 2]
- `inverse_M_Score` = -M_Score clipped to [0, 10] (more negative = better)
- `inverse_LVGI` = (2 - LVGI) clipped to [0, 2] (lower leverage increase = better)

This is a raw composite that gets percentile-ranked in the pipeline against all stocks that passed Stage 1.

---

## Stage 1B: Liquidity & Clean Books

**File**: `src/analysis/pipeline.py` (inline in `run_full_pipeline()`)

### Average Daily Turnover (ADT) Gate

```
ADT = avg_close_20d × avg_volume_20d
```

**Threshold**: ADT must be >= INR 10 Crore (1e7). This filters out illiquid stocks where position entry/exit would cause significant market impact.

### Worst-5-Day Stress Test

```
daily_turnover = close × volume (for each of last 20 days)
worst_5d_adt = mean of 5 lowest daily turnovers
```

**Threshold**: `worst_5d_adt` must be >= 50% of MIN_ADT (INR 5 Crore).

**Rationale**: Average turnover can mask liquidity gaps. The 5th-percentile daily turnover test ensures you can still transact under mild stress (thin-volume days). If the worst 5 out of 20 days can't even muster half the normal ADT requirement, the stock is too illiquid for institutional-grade trading.

### Debt/Equity Gate

**Threshold**: D/E must be <= 1.5

**Rationale**: High leverage amplifies both upside and downside. Beyond 1.5x D/E, the stock's risk profile changes fundamentally — it becomes more of a credit bet than an equity bet. This complements the LVGI component of the M-Score by applying a hard absolute cap.

---

## Stage 1C: Point-in-Time Earnings Gate

**File**: `src/analysis/pipeline.py :: _passes_earnings_gate()`

### QoQ Sales Growth

```
qoq_sales = (Sales_t - Sales_{t-1}) / |Sales_{t-1}|
```

**Threshold**: Must be > 0% (positive quarter-over-quarter revenue growth).

**Point-in-Time (PIT) adjustment**: We only use actually reported quarterly data from yfinance, not estimates. Since yfinance returns the most recent available quarter, there's an implicit ~60-day lag aligned with the SEBI LODR filing window. This prevents look-ahead bias — we never act on data that wouldn't have been publicly available at the time.

### EPS Growth Proxy

Since yfinance doesn't reliably provide per-share EPS for Indian equities, we use a proxy:
- Check that net income margin (Net Income / Sales) is positive
- If margin is negative → exclude (loss-making companies fail this gate)

**What the spec says vs. what we implemented**: The original spec calls for "10% EPS growth over 2 consecutive quarters." Without reliable per-share EPS data, we simplified this to checking for positive net income margin. This is a weaker gate than intended — it only catches loss-making companies, not those with declining-but-positive earnings. This is documented as a known limitation.

**Conservative on data gaps**: If we can't verify sales growth (missing data), the stock is excluded. If we can't verify EPS growth, the stock is allowed to pass — this asymmetry exists because sales data is more reliably available than earnings per share.

---

## Stage 2: Multi-Factor Ranking

**File**: `src/analysis/factors.py`

Five uncorrelated factors are computed for each stock that passes Stages 1A–1C. Each factor is designed to capture a different aspect of stock quality.

### Factor 1: Composite Mansfield Relative Strength (25% weight in BULL)

**Academic basis**: Mansfield, S. (1994). *Stan Weinstein's Secrets for Profiting in Bull and Bear Markets.*

**What it measures**: Stock's price performance relative to the Nifty 500 index across multiple time horizons.

**Formula**:

```
RP = Stock_Close / Benchmark_Close  (relative price ratio)
RP_MA = rolling_mean(RP, window)
MRS = ((RP / RP_MA) - 1) × 100
```

**Three horizons**: Windows of 65, 91, and 126 trading days (~3, 13-week, and 6-month horizons). The composite is the average MRS across all three.

**Slope check**: The 5-day change in the relative price ratio must be positive. If negative (stock is losing relative ground), the composite score is halved as a penalty, but not zeroed out.

**Why 3 horizons**: A single-window RS can be noisy. Using 3 horizons makes the signal more robust — a stock must be outperforming across short, medium, and long term to score high.

**Minimum data requirement**: 130 common dates between stock and benchmark are required. Below this, the factor returns 0.0.

### Factor 2: Delivery Volume Conviction (20% weight in BULL)

**What it measures**: Institutional accumulation as revealed by delivery percentage.

**Primary signal** (when historical delivery data is available):

```
conviction = avg_5d_delivery_pct / avg_20d_delivery_pct
```

A ratio > 1.0 means recent delivery percentage is above the 20-day average — smart money is taking delivery rather than squaring off intraday.

**Bulk/block deal filter**: Days with delivery_pct > 95% are excluded from the average calculation to prevent false signals from bulk/block deals that distort the delivery ratio.

**Single-day fallback** (when only today's bhavcopy is available):

```
conviction = (today_deliv_pct / 50.0) × (today_volume / avg_volume_20d)
```

This normalizes delivery percentage around 50% (typical for liquid NSE stocks) and scales by volume surge.

**Why delivery %**: In Indian markets, delivery percentage is a unique signal not available in most other markets. Intraday traders (who square off by end of day) inflate volume numbers without conviction. Only delivery trades represent genuine buying intent. Rising delivery % alongside rising price confirms institutional accumulation.

### Factor 3: Volatility-Adjusted Momentum (VAM) (20% weight in BULL)

**Academic basis**: Barroso, P. and Santa-Clara, P. (2015). "Momentum has its moments." *Journal of Financial Economics*, 116(1), 111-120.

**Formula**:

```
12-1 Momentum = Return_12m - Return_1m
VAM = (12-1 Momentum) / Volatility_90d_annualized
```

Where:
- `Return_12m` = price change over 252 trading days
- `Return_1m` = price change over 21 trading days (skipped to avoid short-term reversal)
- `Volatility_90d` = standard deviation of log-returns over 90 trading days, annualized (× sqrt(252))

**Why 12-1**: Jegadeesh and Titman (1993) established that momentum profits are strongest at the 12-month horizon but exhibit reversal in the most recent month. By subtracting the last month, we capture the trend while avoiding the short-term reversal effect that is particularly strong in Indian equities.

**Why divide by volatility**: Raw momentum rewards stocks that went up a lot, but some of those moved up on very high volatility (fragile momentum). Dividing by volatility penalizes fragile momentum and rewards steady, low-volatility uptrends — which are more likely to persist.

**Log-returns**: Used for volatility calculation to ensure scale invariance across different price levels.

**Minimum data requirement**: 252 trading days (~1 year) of history required. Returns 0.0 otherwise.

### Factor 4: Forensic Quality Score (20% weight in BULL)

See Stage 1A section above for the formula. The same forensic metrics that form the binary gate are also used as a continuous ranking factor:

```
Composite = 0.50 × CCR_score + 0.30 × inverse_M_Score + 0.20 × inverse_LVGI
```

Higher composite = better quality. This is percentile-ranked across the eligible universe.

**Why include as both gate AND factor**: The gate at Stage 1A sets a minimum bar (you must not be a probable manipulator). The factor at Stage 2 further rewards companies with exceptionally clean books — those with very high CCR, very negative M-Scores (far from manipulation), and declining leverage.

### Factor 5: Earnings Revision Breadth Proxy (15% weight in BULL)

**Academic basis**: Inspired by Chan, Jegadeesh, and Lakonishok (1996). "Momentum Strategies." *Journal of Finance*, 51(5), 1681-1713. (Earnings surprise/revision as a momentum driver.)

**The problem**: Free-tier data sources do not provide analyst consensus estimates or revision data for Indian equities. Bloomberg, Refinitiv, and similar providers charge for this data.

**Our proxy**: We use price reaction on "earnings-like" days as a substitute:

1. Compute average absolute daily move over 90 days
2. Identify "big move days" — days with absolute return > 2× the 90-day average
3. These proxy for result announcement days (earnings releases typically cause outsized moves)
4. Compute the ratio of positive big-move days to total big-move days
5. Scale by the average magnitude of positive moves vs average absolute move

```
revision_score = (positive_big_moves / total_big_moves) × min(magnitude_boost, 3.0)
```

Where `magnitude_boost = avg_positive_big_move / avg_abs_daily_move`, capped at 3.0.

**Intuition**: A stock that tends to rally on its big-move days (which likely include earnings) is receiving positive earnings surprises, suggesting analyst upgrades and improving fundamentals.

**When no big moves detected**: Returns 0.5 (neutral score).

**Known limitation**: This is a rough proxy. It can't distinguish between earnings days and other news-driven moves. With paid analyst data, this factor could be significantly more precise.

### Factor Combination

All 5 factors are normalized to percentile ranks (0.0 to 1.0) within the eligible universe:

```
for each factor in [mrs, deliv, vam, fq, rev]:
    factor_rank = percentile_rank(factor_raw_score)
```

Then combined using regime-specific weights:

```
confidence = w_rs × mrs_rank + w_del × deliv_rank + w_vam × vam_rank + w_for × fq_rank + w_rev × rev_rank
```

Scaled to 0–100 range:

```
confidence = confidence × 100
adj_confidence = confidence × regime_scalar
```

The top 10 stocks by `adj_confidence` become the bullish candidate list.

### Factor Correlation Check

**File**: `src/analysis/factor_correlation.py`

Before deploying weights, all 5 factors should have pairwise Pearson correlation < 0.60. If two factors correlate above this threshold, the composite score effectively double-counts the same information.

This check is implemented but runs as a diagnostic tool, not as a pipeline gate. When violations are found, the system logs warnings and suggests remediation (drop the weaker factor, residualize, or orthogonalize).

---

## Stage 3: Macro & Regime Overlay

**File**: `src/analysis/regime.py`

### 4-State Regime Detection

The regime model converts market conditions into a continuous exposure scalar instead of a binary on/off switch. This eliminates whipsaw around the 200 DMA boundary.

**Input signals**:
- Nifty 500 closing price vs. 200-day moving average (DMA)
- India VIX level
- USD/INR 30-day depreciation
- Nifty 14-day RSI
- DII net buying (optional corroboration)

**Regime decision tree** (evaluated in order, first match wins):

#### 1. BEAR / FII FLIGHT (0% exposure, scalar = 0.0)

Triggered when ANY of:
- Nifty 500 closes **below** its 200 DMA
- India VIX > 24
- USD/INR depreciates > 2% in 30 days (capital flight indicator)

**Why these triggers**: The 200 DMA is the most widely tracked institutional trend indicator. When price is below it, the primary trend is down and buying into the decline has negative expected value. VIX > 24 indicates fear-level volatility. INR depreciation > 2%/month signals FII capital flight — foreign institutions are pulling money out of India, which creates sustained selling pressure.

**What happens**: The normal pipeline (Stages 1A–2) is **completely bypassed**. Instead, `defensive_rotation_candidates()` runs to select cash-generative stocks from FMCG, Pharma, and IT sectors. All factor weights are set to 0.0 (no factor scoring). The output is labeled as "Bullish Candidates" in user-facing outputs but these are defensive rotation picks, not momentum-selected stocks.

#### 2. RISK-ON DIP (60% exposure, scalar = 0.6)

Triggered when:
- Nifty is within 3% of its 200 DMA (either side), **OR**
- Nifty 14-day RSI < 40 (oversold territory)

**But not BEAR** (the BEAR check runs first and takes priority).

**Rationale**: When Nifty is near its 200 DMA but hasn't broken below, or when RSI shows oversold conditions with trend still intact, it's a potential buying opportunity at reduced risk. 60% exposure means you participate in the recovery but with meaningful cash buffer.

**Factor weight shift**: Delivery conviction (0.30) and forensic quality (0.30) are elevated. Momentum-based factors (RS, VAM) are reduced. This prioritizes stocks being accumulated by institutions during the dip, with clean fundamentals.

#### 3. VOLATILE SIDEWAYS (30% exposure, scalar = 0.3)

Triggered when:
- VIX is between 16 and 24 (elevated but not panic)

**Rationale**: When volatility is elevated but not extreme, markets tend to oscillate without clear direction. 30% exposure means highly selective positioning — only the highest-conviction ideas get capital.

**Factor weight shift**: Forensic quality dominates (0.40), delivery conviction high (0.30). Momentum factors (RS, VAM) nearly eliminated (0.05 each). In choppy markets, quality and accumulation signals are more reliable than momentum, which gets whipsawed.

#### 4. STRUCTURAL BULL (100% exposure, scalar = 1.0)

Default regime when no other condition triggers:
- Nifty above 200 DMA
- VIX < 16 (low volatility)
- INR stable (< 2% monthly depreciation)

**Factor weight shift**: RS gets highest weight (0.30), followed by VAM (0.25). Momentum factors dominate because in a bull market, relative strength is the strongest predictor of forward returns.

### Regime-Specific Factor Weights

| Factor | BULL | DIP | SIDEWAYS | BEAR |
|--------|------|-----|----------|------|
| Mansfield RS | 0.30 | 0.20 | 0.05 | 0.00 |
| Delivery Conviction | 0.20 | 0.30 | 0.30 | 0.00 |
| VAM | 0.25 | 0.10 | 0.05 | 0.00 |
| Forensic Quality | 0.15 | 0.30 | 0.40 | 0.00 |
| Earnings Revision | 0.10 | 0.10 | 0.20 | 0.00 |
| **Total** | **1.00** | **1.00** | **1.00** | **0.00** |

**Design principle**: As market conditions deteriorate from BULL → DIP → SIDEWAYS, the model progressively shifts weight from momentum signals (which get whipsawed in volatile markets) to quality signals (which protect capital in drawdowns).

### VIX-Adaptive Stop Tightening

When VIX > 20 (high-volatility environment):
- ATR-based stop distances are multiplied by 0.70 (effectively tightened by 30%)
- A stop that would normally be 2×ATR becomes 1.4×ATR
- Time stop shortened from 26 weeks to 13 weeks

**Rationale**: In high-volatility environments, price moves are larger and faster. Keeping the same stop distance means accepting larger dollar losses. Tightening stops preserves capital at the cost of more frequent stop-outs — an acceptable tradeoff when the probability of large adverse moves is elevated.

---

## Bearish Operations

**File**: `src/analysis/bearish.py`

The bearish module operates in two modes:

### Mode A: Defensive Rotation (BEAR regime)

When regime = BEAR, `defensive_rotation_candidates()` runs instead of the normal pipeline.

**Selection criteria**:
1. **Sector filter**: Only stocks from defensive sectors — FMCG, Pharma, IT, Healthcare
2. **CCR >= 0.80**: Must be cash-generative (same threshold as forensic gate)
3. **D/E <= 1.0**: Must have low leverage (stricter than the 1.5 in Stage 1B)
4. **Scoring**: `defensive_score = CCR × 50 + max(return_3m × 100, 0)`

**Output**: Top 5 by defensive score. These replace the normal bullish candidates in all outputs.

**Why these sectors**: FMCG, Pharma, and IT have historically shown lower drawdowns during Indian market bear phases. Their earnings are less cyclical (consumer staples, healthcare demand is inelastic, IT exports benefit from INR depreciation during capital flight).

### Mode B: Short/Inverse Candidates (all regimes)

`bearish_candidates()` runs in all regimes to identify stocks with deteriorating fundamentals.

**Selection criteria** (all must be met):
1. **M-Score > -1.5**: Higher than the forensic gate threshold (-2.22), indicating elevated manipulation probability
2. **Mansfield RS < 0**: Underperforming the Nifty 500 benchmark
3. **Additional signals** (boost score but not required):
   - LVGI > 1.05 (leverage increasing > 5% QoQ)
   - Earnings revision proxy < 0.3 (predominantly negative big-move days)

**Bearish score** (0–100):
```
bearish_score  = min((M_Score + 2.22) × 20, 40)    # M-Score component: 0–40 points
               + max((0.80 - CCR) × 50, 0)           # CCR shortfall: 0–30 points
               + abs(MRS) × 2                         # RS weakness: 0–20 points
               + 5 (if LVGI rising)                   # Leverage bonus
               + 5 (if negative revision)             # Revision bonus
```

Capped at 100. Stocks scoring > 60 are labeled "SHORT"; others are "CAUTION".

**Output**: Top 10 by bearish score.

**Important**: This is NOT the inverse of the long model. It uses different thresholds (M-Score > -1.5 vs < -2.22) and different signals (negative RS, rising LVGI). A stock can fail the bullish screen AND fail the bearish screen — most stocks fall in the middle.

---

## Portfolio Construction

**File**: `src/analysis/portfolio.py`

**Note**: Portfolio construction computes theoretical position sizes but these are NOT currently surfaced in the dashboard or Telegram/Discord outputs. The pipeline outputs ranked candidates with scores and price targets; actual position sizing is left to the user.

### ATR-Based Position Sizing

```
risk_per_trade = total_capital × 0.01 × regime_scalar
stop_distance = ATR14 × 2.0
shares = risk_per_trade / stop_distance
```

**Rationale**: Equal rupee risk per trade means every position risks the same amount (1% of capital, scaled by regime). A volatile stock (high ATR) gets fewer shares; a stable stock (low ATR) gets more. This equalizes the impact of stop-outs across the portfolio.

### Position Limits

| Constraint | Threshold | Rationale |
|-----------|-----------|-----------|
| Sector cap | 25% of portfolio | Prevents sector concentration risk |
| Max position | 15% of capital | Prevents single-stock dominance |
| Liquidity cap | 2% of stock's ADT | Prevents market impact on entry/exit |
| Max stocks | 10 | Manageable portfolio size |
| Same sub-industry | Max 2 stocks | Avoid correlated bets within sectors |
| Portfolio beta | Max 1.3 vs Nifty | Caps systematic market exposure |
| Min defensive | 20% of portfolio | Ensures some downside protection |

### Beta Enforcement

```
portfolio_beta = sum(stock_beta × stock_weight for each position)
stock_beta = Cov(stock_returns, benchmark_returns) / Var(benchmark_returns)
```

Computed using trailing 252-day daily returns vs Nifty 500. If the weighted portfolio beta exceeds 1.3, the highest-beta position is iteratively removed from the tail (lowest-scored) end until compliant.

---

## Price Targets

**File**: `src/analysis/price_targets.py`

### ATR-Projected Band (Asymmetric)

```
target_high = close + 3 × ATR14
stop_loss   = close - 2 × ATR14
```

**Risk:Reward = 1.5:1 minimum** (3 ATR up vs 2 ATR down). The asymmetry reflects the long bias — we expect the stock to move further in our direction than against us if the thesis is correct.

### 20-Week Channel

```
w20_high = max(high) over last 100 trading days
w20_low  = min(low) over last 100 trading days
```

Provides consolidation context. A stock near its 20-week high may be about to break out; one near the low may be finding support.

### RS Slope

```
rs_slope = (close / close_5d_ago - 1) × 100
```

5-day price momentum as a percentage. Positive = uptrend, negative = pullback.

---

## Exit Rules

**File**: `src/analysis/exit_rules.py`

Four independent exit triggers. ANY single trigger firing = position exits at next open. These are evaluated daily.

### Trigger 1: Technical Exit

```
IF Mansfield_RS < 0 AND close < 100-day_MA → EXIT
```

Both conditions must be true simultaneously. This is a **confirmed trend breakdown** — the stock is underperforming the benchmark AND its own price trend has broken its 20-week moving average. Either condition alone could be temporary; both together indicate structural deterioration.

### Trigger 2: Fundamental Exit

```
IF QoQ_Sales_Growth < -5% → EXIT
```

Uses Point-in-Time data with the same ~60-day SEBI LODR lag as the entry gate. A > 5% quarterly revenue decline indicates the business is contracting — the original thesis for entry has been invalidated.

### Trigger 3: Risk Stop (Non-Negotiable)

```
stop_price = entry_price - (ATR14_at_entry × 2.0)
IF close < stop_price → EXIT

High-VIX adjustment:
stop_price = entry_price - (ATR14_at_entry × 2.0 × 0.70)
→ Effectively: entry_price - (ATR14_at_entry × 1.4)
```

This is the hard floor that prevents catastrophic losses. The ATR at the time of entry is used (not current ATR), so the stop doesn't widen as volatility increases. In high-VIX environments (VIX > 20), the stop tightens by 30%.

### Trigger 4: Time Stop

```
IF holding_period > 26 weeks → EXIT
IF holding_period > 13 weeks AND VIX > 20 → EXIT
```

Stale positions without fresh catalysts tie up capital. After 6 months (3 months in high-VIX), the original thesis should have played out or been invalidated. Continuing to hold is capital-inefficient.

---

## Macro Snapshot

**File**: `src/analysis/regime.py :: get_macro_snapshot()`

The macro snapshot is a read-only output block included in all outputs (Telegram, Discord, Dashboard). It does not affect pipeline logic but provides context for the user.

**Fields**:
| Field | Source | Format |
|-------|--------|--------|
| Nifty 500 close | Fyers API | INR, 2 decimals |
| 200 DMA | Computed (200-day rolling mean) | INR, 2 decimals |
| DMA distance % | `(close / DMA - 1) × 100` | 1 decimal |
| India VIX | Fyers API | 1 decimal |
| USD/INR | yfinance USDINR=X | 2 decimals |
| USD/INR 30d move | `(current / 30d_ago - 1) × 100` | 2 decimals |
| FII net | NSE fiidiiTradeReact | INR Crores, 0 decimals |
| DII net | NSE fiidiiTradeReact | INR Crores, 0 decimals |

---

## Known Limitations & Honest Assessment

### Data Quality Issues

1. **Fundamentals coverage**: yfinance returns usable quarterly data for only ~8% of NSE 500 stocks (4/50 in testing). This means ~92% of stocks fail at Stage 1A because missing fundamentals = automatic exclusion. This is the single biggest bottleneck in the system. With better data sources (Screener.in, BSE XBRL filings), the pipeline would score and rank significantly more stocks.

2. **Pledge data**: The NSE pledging API returns no data (0/50 in testing). The pledge gate is effectively non-functional — all stocks pass by default. This means promoter pledge risk is not being filtered.

3. **Earnings revision proxy**: The price-reaction proxy for analyst revision breadth is approximate. It cannot distinguish between earnings days and other news events. With paid analyst consensus data (Bloomberg, Refinitiv), Factor 5 would be significantly more precise.

4. **No intraday data**: The pipeline runs on daily data only. Delivery conviction uses single-day bhavcopy, not rolling historical delivery percentage. This makes the delivery signal noisier than it would be with 5-day or 20-day rolling delivery data.

### Design Choices That Matter

1. **Conservative by default**: Missing data = exclusion. This means the pipeline produces very few candidates when data availability is low (like the current ~8% fundamentals rate). The alternative — estimating or defaulting missing data — was rejected because it would undermine the forensic filter's purpose.

2. **Regime runs first**: The regime check (Stage 3) is actually computed before Stages 1A–1C because it determines factor weights and whether to run the normal pipeline at all (BEAR skips to defensive). This is counter-intuitive from the stage numbering but correct operationally.

3. **BEAR = full bypass**: In BEAR regime, the normal 5-stage pipeline is completely replaced by defensive rotation. There is no "reduced" version of the normal pipeline — it's a hard switch. This means BEAR regime outputs are qualitatively different from other regime outputs.

4. **Single-run design**: The pipeline runs once daily after market close. It does not stream live data or react intraday. The exit rules module exists but is not currently integrated into an automated monitoring loop — it would need to be called by a separate scheduled process with persisted position data.

5. **No backtesting integration**: The backtesting module (`Tests/backtest/run_backtest.py`) exists but uses the same single-run pipeline design. It cannot simulate realistic execution (slippage, partial fills, market impact) or forward-walk the regime model.

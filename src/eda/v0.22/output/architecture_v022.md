# v0.22 Architecture — Progressive Pipeline (Fyers-First)

## Research-Backed Design Decisions

### Gate Classification (from NotebookLM Q3 + Browser T3)

| Gate | Type | Rationale |
|------|------|-----------|
| ADT < ₹10 Cr | **HARD** | Liquidity non-negotiable; comes FIRST (Browser T1) |
| M-Score > -2.22 | **HARD** | "One cockroach in kitchen" — only when data available (NLM Q3) |
| Promoter Pledge > 5% | **HARD** | Integrity non-negotiable — only when data available (NLM Q3) |
| D/E > sector cap | **SOFT** | Nuanced, not binary — scoring penalty (NLM Q3) |
| CCR < 0.80 | **SOFT** | EBITDA denominator is flawed — scoring penalty (NLM Q3) |
| Missing fundamentals | **PASS** | Not a red flag — confidence penalty instead (Browser T2, T6) |
| 200 DMA below | **NO GATE** | Hurts live performance despite improving backtests (Browser T4) |

### New Pipeline Stages

```
Stage 0: Universe Entry [Fyers OHLCV]
  Gate: len(ohlcv) >= 100
  Kill: ~5% (IPOs, newly listed)
  498 → ~475

Stage 1: Technical + Liquidity Pre-Screen [Fyers OHLCV]
  Gate: ADT >= ₹10 Cr (HARD)
  Gate: Worst-5-day stress >= 30% of min ADT (HARD)
  Signal: 52-week high proximity computed (stored, not a gate)
  Kill: ~40-50%
  ~475 → ~250

Stage 2: Fundamental Quality [yfinance — OPTIONAL]
  FOR stocks WITH fundamentals (~40):
    Gate: M-Score > -2.22 → EXCLUDE (HARD, only on real data)
    Gate: Promoter Pledge > 5% → EXCLUDE (HARD, only on real data)
    Score: D/E vs sector cap → penalty (SOFT)
    Score: CCR quality → penalty (SOFT)
    Score: Earnings gate QoQ/YoY → penalty (SOFT)
    Flag: has_fundamentals = True, forensic_clean = True/False
  FOR stocks WITHOUT fundamentals (~210):
    Flag: has_fundamentals = False
    PASS THROUGH — no exclusion for missing data
  Kill: ~5-10% (only confirmed bad actors with real data)
  ~250 → ~230

Stage 3: Multi-Factor Scoring [Fyers + optional yfinance/NSE]
  TIER A (ALL stocks, from Fyers OHLCV):
    - Mansfield RS (35% weight) [IC=0.95]
    - VAM 12-1 month (20% weight) [IC=0.63]
    - Earnings Revision Proxy (25% weight) [IC=0.58]
  TIER B (stocks WITH bhavcopy):
    - Delivery Conviction (20% weight)

  Confidence multiplier: (n_available / n_total) ^ 0.5
    - 4/4 factors: 1.00x
    - 3/4 factors: 0.87x (no delivery data)
    - If has_fundamentals AND forensic_clean: +0% bonus
    - If NOT has_fundamentals: no penalty beyond factor availability

  Winsorize ALL factors at ±3 sigma before ranking
  Percentile rank within scored universe

  Select top 10 bullish
  ~230 scored → Top 10

Stage 4: Bearish Candidates [UNCHANGED]
  Full universe scan with soft gates
  4-component scoring
  Top 10 bearish
```

### Confidence Formula

```python
# From Browser Research Topic 6
n_available = sum(1 for f in [mrs, vam, rev, deliv] if f is not None)
n_total = 4  # Total possible factors
confidence_multiplier = (n_available / n_total) ** 0.5

# Applied to final score:
adj_confidence = raw_confidence * confidence_multiplier
```

### Key Changes from v0.21

1. **Liquidity BEFORE forensic** (was after)
2. **Missing fundamentals = PASS** (was EXCLUDE)
3. **D/E and CCR = SOFT scoring** (were HARD gates)
4. **200 DMA = NOT a gate** (was considered; research says no)
5. **52-week high proximity = computed** (new signal, stored for display)
6. **Confidence multiplier** based on data completeness (new)
7. **All factors winsorized** at ±3 sigma (was only VAM)

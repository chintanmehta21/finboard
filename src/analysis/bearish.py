"""
Bearish & Bullish Candidate Identification (v0.2)

Bearish Candidates — predict 3-6 month DECLINE:
  - Technical weakness: below 200 DMA, negative momentum, negative RS
  - Accounting risk: M-Score > -2.22, CCR shortfall
  - Fundamental deterioration: revenue decline, negative FCF
  - Leverage risk: LVGI rising, high D/E
  - Scoring weighted for 3-6M decline prediction

Bullish Candidates (BEAR regime):
  - Quality stocks with clean books, positive 3-6 month momentum
  - Scored on quality + momentum + relative strength composite
"""

import logging

import numpy as np
import pandas as pd

from .forensic import beneish_m_score, cash_conversion_ratio
from .factors import earnings_revision_proxy, mansfield_rs

logger = logging.getLogger(__name__)

# Bearish candidate thresholds (v0.2 — relaxed for 3-6M prediction)
# M-Score is now a scoring component, not a hard gate
SHORT_RS_THRESHOLD = 0          # Must be underperforming benchmark OR have negative momentum
NEG_REVISION_THRESHOLD = 0.3    # Below this = negative earnings momentum (proxy)


def _compute_mrs_single(ohlcv: pd.DataFrame, benchmark_df: pd.DataFrame,
                        window: int = 91) -> float:
    """Compute single-window Mansfield RS. Returns 0.0 on failure."""
    if benchmark_df is None or benchmark_df.empty or ohlcv.empty:
        return 0.0
    try:
        stock_close = ohlcv['close']
        bench_close = benchmark_df['close']
        common = stock_close.index.intersection(bench_close.index)
        if len(common) < window:
            return 0.0
        rp = stock_close.loc[common] / bench_close.loc[common]
        rp_ma = rp.rolling(window).mean()
        val = float(((rp.iloc[-1] / rp_ma.iloc[-1]) - 1) * 100)
        return val if np.isfinite(val) else 0.0
    except Exception:
        return 0.0


def bearish_candidates(ohlcv_data: dict[str, pd.DataFrame],
                       fundamentals: dict[str, dict],
                       benchmark_df: pd.DataFrame = None,
                       sector_map: dict[str, str] = None) -> pd.DataFrame:
    """
    Identify bearish candidates predicted to DECLINE over 3-6 months.

    v0.2 scoring (4 components, weighted for 3-6M decline prediction):
      - Technical weakness (35%): RS, DMA, momentum — strongest decline predictors
      - Accounting risk (25%): M-Score, CCR shortfall
      - Fundamental deterioration (25%): revenue decline, negative FCF
      - Leverage risk (15%): LVGI rising, high D/E

    Soft gates: must have at least one bearish signal (technical OR fundamental)
    to qualify. M-Score is a scoring component, not a hard exclusion gate.

    Returns:
        DataFrame of bearish candidates (top 10) sorted by bearish_score
    """
    records = []

    for symbol, ohlcv in ohlcv_data.items():
        if ohlcv.empty or len(ohlcv) < 63:
            continue

        f = fundamentals.get(symbol)
        if not f:
            continue

        # ── Compute all signals ──
        m_score = beneish_m_score(f)
        ccr = cash_conversion_ratio(f)
        mrs = _compute_mrs_single(ohlcv, benchmark_df, window=91)

        # Returns
        close = float(ohlcv['close'].iloc[-1])
        ret_3m = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-63] - 1) * 100 if len(ohlcv) >= 63 else 0
        ret_6m = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-126] - 1) * 100 if len(ohlcv) >= 126 else 0

        # 200 DMA check
        dma_200 = ohlcv['close'].tail(200).mean() if len(ohlcv) >= 200 else ohlcv['close'].mean()
        below_200dma = close < dma_200
        dma_pct = (close / dma_200 - 1) * 100

        # LVGI trend (leverage change)
        debt_t = f.get('debt_t', 0) or 0
        debt_t1 = f.get('debt_t1', 0) or 0
        ta = f.get('total_assets', 0) or 1e-9
        lvgi = (debt_t / ta) / (debt_t1 / ta + 1e-9) if debt_t1 > 0 else 1.0
        lvgi_rising = lvgi > 1.05

        # D/E ratio
        de = f.get('debt_equity', 0) or 0

        # Revenue deterioration
        sales_t = f.get('sales_t')
        sales_t1 = f.get('sales_t1')
        qoq_rev_decline = False
        qoq_change = 0.0
        if sales_t is not None and sales_t1 is not None and sales_t1 > 0:
            qoq_change = (sales_t - sales_t1) / abs(sales_t1)
            qoq_rev_decline = qoq_change < -0.05  # > 5% revenue decline

        # Revision proxy
        rev_proxy = earnings_revision_proxy(ohlcv)
        neg_revision = rev_proxy < NEG_REVISION_THRESHOLD

        # Volatility trend (rising vol = bearish)
        if len(ohlcv) >= 90:
            log_ret = np.log(ohlcv['close'] / ohlcv['close'].shift(1)).dropna()
            vol_recent = log_ret.tail(30).std() * np.sqrt(252) if len(log_ret) >= 30 else 0
            vol_prior = log_ret.tail(90).head(60).std() * np.sqrt(252) if len(log_ret) >= 90 else vol_recent
            vol_rising = vol_recent > vol_prior * 1.2 if vol_prior > 0 else False
        else:
            vol_recent = 0
            vol_rising = False

        # ── Soft gate: must have at least one bearish signal ──
        has_technical_weakness = (mrs < 0) or below_200dma or (ret_3m < -5) or (ret_6m < -10)
        has_fundamental_issue = (
            (np.isfinite(m_score) and m_score > -2.22) or
            (ccr >= 0 and ccr < 0.70) or
            qoq_rev_decline or
            lvgi_rising
        )
        if not (has_technical_weakness or has_fundamental_issue):
            continue

        # ── Score: 4-component model (0-100) ──
        # Technical weakness (35 pts max)
        tech_score = 0.0
        if mrs < 0:
            tech_score += min(abs(mrs) * 1.5, 12)       # RS weakness (0-12)
        if below_200dma:
            tech_score += min(abs(dma_pct) * 0.5, 8)     # Distance below 200DMA (0-8)
        if ret_3m < 0:
            tech_score += min(abs(ret_3m) * 0.3, 8)      # 3M decline (0-8)
        if vol_rising:
            tech_score += 4                               # Rising volatility
        if neg_revision:
            tech_score += 3                               # Negative revision proxy
        tech_score = min(tech_score, 35)

        # Accounting risk (25 pts max)
        acct_score = 0.0
        if np.isfinite(m_score) and m_score > -2.22:
            acct_score += min((m_score + 2.22) * 12, 15)  # M-Score risk (0-15)
        if 0 <= ccr < 0.80:
            acct_score += min((0.80 - ccr) * 25, 10)      # CCR shortfall (0-10)
        acct_score = min(acct_score, 25)

        # Fundamental deterioration (25 pts max)
        fund_score = 0.0
        if qoq_rev_decline:
            fund_score += min(abs(qoq_change) * 50, 12)   # Revenue decline severity (0-12)
        net_inc = f.get('net_income', 0) or 0
        if net_inc < 0:
            fund_score += 8                                # Negative earnings
        if neg_revision:
            fund_score += 5                                # Analyst downgrade proxy
        fund_score = min(fund_score, 25)

        # Leverage risk (15 pts max)
        lev_score = 0.0
        if lvgi_rising:
            lev_score += min((lvgi - 1.0) * 30, 8)        # LVGI magnitude (0-8)
        if de > 1.5:
            lev_score += min((de - 1.5) * 5, 7)           # High D/E (0-7)
        lev_score = min(lev_score, 15)

        bearish_score = min(tech_score + acct_score + fund_score + lev_score, 100)

        ret_1d = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-2] - 1) * 100 if len(ohlcv) >= 2 else 0
        ret_1w = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-5] - 1) * 100 if len(ohlcv) >= 5 else 0

        records.append({
            'symbol': symbol,
            'bearish_score': round(bearish_score, 1),
            'close': round(close, 2),
            'return_1d': round(ret_1d, 1),
            'return_1w': round(ret_1w, 1),
            'return_3m': round(float(ret_3m), 1),
            'return_6m': round(float(ret_6m), 1),
            'sector': (sector_map or {}).get(symbol, ''),
            'm_score': round(m_score, 2) if np.isfinite(m_score) else None,
            'ccr': round(ccr, 2),
            'mansfield_rs': round(mrs, 1),
            'below_200dma': below_200dma,
            'dma_pct': round(float(dma_pct), 1),
            'lvgi': round(lvgi, 2),
            'lvgi_rising': lvgi_rising,
            'de_ratio': round(de, 2),
            'qoq_rev_change': round(float(qoq_change * 100), 1) if qoq_change else 0,
            'neg_revision': neg_revision,
            'revision_proxy': round(rev_proxy, 3),
            'vol_rising': vol_rising,
            'signal': 'SHORT' if bearish_score > 50 else 'CAUTION',
        })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    return df.nlargest(10, 'bearish_score')


def bullish_candidates(ohlcv_data: dict[str, pd.DataFrame],
                       fundamentals: dict[str, dict],
                       sector_map: dict[str, str],
                       benchmark_df: pd.DataFrame = None) -> pd.DataFrame:
    """
    Identify stocks with strong 3-6 month bullish potential.

    Scans ALL sectors (not limited to defensives) for quality stocks with:
    - Clean books: M-Score < -2.22 (low manipulation probability)
    - Cash generative: CFO/EBITDA >= 0.70 (CCR)
    - Low leverage: Debt/Equity < 1.5
    - Positive medium-term momentum: 3M return > 0 or 6M return > 0
    - Relative strength: Mansfield RS vs benchmark

    Scoring (bullish_score, 0-100):
      - Quality component (40%): CCR quality + M-Score safety
      - Momentum component (35%): 3M + 6M returns
      - Relative Strength component (25%): Mansfield RS vs Nifty 500

    Args:
        ohlcv_data: Dict of symbol -> OHLCV DataFrame
        fundamentals: Dict of symbol -> fundamentals dict
        sector_map: Dict of symbol -> sector name
        benchmark_df: Nifty 500 daily close for RS calculation

    Returns:
        DataFrame of bullish candidates (top 10) sorted by bullish_score
    """
    records = []

    for symbol, ohlcv in ohlcv_data.items():
        if ohlcv.empty or len(ohlcv) < 63:
            continue  # Need at least 3 months of data

        f = fundamentals.get(symbol)
        if not f:
            continue

        m_score = beneish_m_score(f)
        ccr = cash_conversion_ratio(f)
        de = f.get('debt_equity', 0) or 0

        # ── Quality gates (must pass all) ──
        if m_score > -2.22:
            continue  # High manipulation risk — skip
        # CCR gate: -1.0 is a sentinel meaning "CFO data unavailable" (not a real 0 ratio).
        # cash_conversion_ratio() returns -1.0 when CFO=0 in yfinance (common for Indian equities).
        # Don't penalise missing data — only exclude stocks with confirmed poor conversion.
        # v0.22 BUG FIX: `ccr < 0.70` was excluding CCR=-1.0 stocks (sentinel treated as real value).
        if ccr != -1.0 and ccr < 0.70:
            continue  # Poor cash conversion (confirmed) — skip
        if de > 1.5:
            continue  # Too leveraged — skip

        close = ohlcv['close'].iloc[-1] if not ohlcv.empty else 0

        # ── Momentum: 1D, 1W, 3M, 6M returns ──
        ret_1d = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-2] - 1) * 100 if len(ohlcv) >= 2 else 0
        ret_1w = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-5] - 1) * 100 if len(ohlcv) >= 5 else 0
        ret_3m = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-63] - 1) * 100 if len(ohlcv) >= 63 else 0
        ret_6m = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-126] - 1) * 100 if len(ohlcv) >= 126 else 0

        # Must show relative resilience: at least one return > -10%
        # In BEAR markets, requiring positive returns would exclude everything
        if ret_3m < -10 and ret_6m < -10:
            continue

        # ── Mansfield Relative Strength vs benchmark ──
        mrs = _compute_mrs_single(ohlcv, benchmark_df, window=91)

        # ── Composite bullish score (0-100) ──
        # Quality component (40 pts max): CCR quality + M-Score safety
        quality = min((ccr - 0.70) * 100, 20)              # CCR above 0.70 → 0-20 pts
        quality += min((abs(m_score) - 2.22) * 8, 20)      # M-Score safety → 0-20 pts

        # Momentum component (35 pts max): 3M + 6M medium-term upside
        momentum = min(max(ret_3m, 0) * 0.7, 20)           # 3M return → 0-20 pts
        momentum += min(max(ret_6m, 0) * 0.3, 15)          # 6M return → 0-15 pts

        # Relative Strength component (25 pts max): outperforming benchmark
        rs_score = min(max(mrs, 0) * 2.5, 25)              # Mansfield RS → 0-25 pts

        bullish_score = min(quality + momentum + rs_score, 100)

        records.append({
            'symbol': symbol,
            'close': round(close, 2),
            'sector': sector_map.get(symbol, ''),
            'ccr': round(ccr, 2),
            'debt_equity': round(de, 2),
            'm_score': round(m_score, 2),
            'mansfield_rs': round(mrs, 1),
            'return_1d': round(ret_1d, 1),
            'return_1w': round(ret_1w, 1),
            'return_3m': round(ret_3m, 1),
            'return_6m': round(ret_6m, 1),
            'bullish_score': round(bullish_score, 1),
        })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    return df.nlargest(10, 'bullish_score')

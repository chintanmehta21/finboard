"""
Bearish & Bullish Candidate Identification

Bullish Candidates (Bear/All Regime):
  - Stocks with strong fundamentals suitable for 3-6 month holding
  - High CFO/EBITDA (cash generative), low debt, clean books
  - Positive momentum (3M/6M returns), positive Mansfield RS
  - Scored on quality + momentum composite for medium-term upside

Bearish Candidates (Short/Inverse Model):
  - M-Score > -1.5 (high manipulation probability)
  - Negative Mansfield RS (underperforming benchmark)
  - Rising LVGI (leverage increasing) + falling CFO/EBITDA
  - NOT a mirror of the long model — separate criteria
"""

import logging

import numpy as np
import pandas as pd

from .forensic import beneish_m_score, cash_conversion_ratio
from .factors import earnings_revision_proxy, mansfield_rs

logger = logging.getLogger(__name__)

# Short candidate thresholds
SHORT_M_SCORE_THRESHOLD = -1.5  # Above this = high manipulation risk
SHORT_RS_THRESHOLD = 0          # Must be negative (underperforming)
NEG_REVISION_THRESHOLD = 0.3    # Below this = negative earnings momentum (proxy)


def bearish_candidates(ohlcv_data: dict[str, pd.DataFrame],
                       fundamentals: dict[str, dict],
                       benchmark_df: pd.DataFrame = None,
                       sector_map: dict[str, str] = None) -> pd.DataFrame:
    """
    Identify bearish/short candidates based on deteriorating fundamentals.

    Targets stocks with:
    - M-Score > -1.5 (high manipulation probability)
    - Negative Mansfield RS (underperforming benchmark)
    - Rising LVGI (leverage increasing QoQ)
    - Falling CFO/EBITDA (cash conversion deteriorating)

    Args:
        ohlcv_data: Dict of symbol -> OHLCV DataFrame
        fundamentals: Dict of symbol -> fundamentals dict
        benchmark_df: Nifty 500 daily close for RS calculation

    Returns:
        DataFrame of bearish candidates with scores and metrics
    """
    records = []

    for symbol, ohlcv in ohlcv_data.items():
        f = fundamentals.get(symbol)
        if not f:
            continue

        m_score = beneish_m_score(f)
        ccr = cash_conversion_ratio(f)

        # Must have high manipulation probability
        if m_score < SHORT_M_SCORE_THRESHOLD:
            continue

        # Compute Mansfield RS (simplified for bearish scan)
        mrs = 0.0
        if benchmark_df is not None and not benchmark_df.empty and not ohlcv.empty:
            try:
                stock_close = ohlcv['close']
                bench_close = benchmark_df['close']
                common = stock_close.index.intersection(bench_close.index)
                if len(common) >= 91:
                    rp = stock_close.loc[common] / bench_close.loc[common]
                    rp_ma = rp.rolling(91).mean()
                    mrs = float(((rp / rp_ma) - 1) * 100).real
                    if pd.isna(mrs):
                        mrs = 0.0
            except Exception:
                mrs = 0.0

        # Must be underperforming benchmark
        if mrs > SHORT_RS_THRESHOLD:
            continue

        # LVGI trend (leverage change)
        debt_t = f.get('debt_t', 0) or 0
        debt_t1 = f.get('debt_t1', 0) or 0
        ta = f.get('total_assets', 0) or 1e-9
        lvgi = (debt_t / ta) / (debt_t1 / ta + 1e-9) if debt_t1 > 0 else 1.0
        lvgi_rising = lvgi > 1.05  # Leverage increasing > 5% QoQ

        # Negative revision proxy (PDF p.8 — bearish model uses inverse of
        # the earnings revision proxy: stocks with predominantly negative
        # big-move days indicate analyst downgrades / negative surprises)
        rev_proxy = earnings_revision_proxy(ohlcv)
        neg_revision = rev_proxy < NEG_REVISION_THRESHOLD  # Low score = negative revisions

        # Score: higher = stronger bearish signal
        bearish_score = 0.0
        bearish_score += min((m_score + 2.22) * 20, 40)  # M-Score component (0-40)
        bearish_score += max((0.80 - ccr) * 50, 0)       # CCR shortfall (0-30)
        bearish_score += abs(mrs) * 2                     # RS weakness (0-20)
        if lvgi_rising:
            bearish_score += 5                            # LVGI bonus
        if neg_revision:
            bearish_score += 5                            # Negative revision bonus

        bearish_score = min(bearish_score, 100)

        close = ohlcv['close'].iloc[-1] if not ohlcv.empty else 0
        ret_1d = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-2] - 1) * 100 if len(ohlcv) >= 2 else 0
        ret_1w = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-5] - 1) * 100 if len(ohlcv) >= 5 else 0
        ret_3m = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-63] - 1) * 100 if len(ohlcv) >= 63 else 0

        records.append({
            'symbol': symbol,
            'bearish_score': round(bearish_score, 1),
            'close': round(close, 2),
            'return_1d': round(ret_1d, 1),
            'return_1w': round(ret_1w, 1),
            'return_3m': round(ret_3m, 1),
            'sector': (sector_map or {}).get(symbol, ''),
            'm_score': round(m_score, 2),
            'ccr': round(ccr, 2),
            'mansfield_rs': round(mrs, 1),
            'lvgi': round(lvgi, 2),
            'lvgi_rising': lvgi_rising,
            'neg_revision': neg_revision,
            'revision_proxy': round(rev_proxy, 3),
            'signal': 'SHORT' if bearish_score > 60 else 'CAUTION',
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
        if ccr < 0.70:
            continue  # Poor cash conversion — skip
        if de > 1.5:
            continue  # Too leveraged — skip

        close = ohlcv['close'].iloc[-1] if not ohlcv.empty else 0

        # ── Momentum: 1D, 1W, 3M, 6M returns ──
        ret_1d = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-2] - 1) * 100 if len(ohlcv) >= 2 else 0
        ret_1w = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-5] - 1) * 100 if len(ohlcv) >= 5 else 0
        ret_3m = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-63] - 1) * 100 if len(ohlcv) >= 63 else 0
        ret_6m = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-126] - 1) * 100 if len(ohlcv) >= 126 else 0

        # Must have at least one positive medium-term return (3M or 6M)
        if ret_3m <= 0 and ret_6m <= 0:
            continue

        # ── Mansfield Relative Strength vs benchmark ──
        mrs = 0.0
        if benchmark_df is not None and not benchmark_df.empty:
            try:
                stock_close = ohlcv['close']
                bench_close = benchmark_df['close']
                common = stock_close.index.intersection(bench_close.index)
                if len(common) >= 91:
                    rp = stock_close.loc[common] / bench_close.loc[common]
                    rp_ma = rp.rolling(91).mean()
                    mrs = float(((rp.iloc[-1] / rp_ma.iloc[-1]) - 1) * 100)
                    if pd.isna(mrs):
                        mrs = 0.0
            except Exception:
                mrs = 0.0

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

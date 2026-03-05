"""
Bearish Market Operation — Two-Mode System

Mode A: Defensive Rotation (Bear Regime, No Shorting)
  - Pivot to defensive sector stocks: FMCG, Pharma, IT
  - High CFO/EBITDA, low debt, positive RS vs defensive benchmark
  - Reduced size: 30% of normal allocation

Mode B: Short/Inverse Model (Advanced)
  - M-Score > -1.5 (high manipulation probability)
  - Negative Mansfield RS (underperforming benchmark)
  - Rising LVGI (leverage increasing) + falling CFO/EBITDA
  - NOT a mirror of the long model — separate criteria
"""

import logging

import numpy as np
import pandas as pd

from .forensic import beneish_m_score, cash_conversion_ratio
from .factors import earnings_revision_proxy

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
        ret_1w = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-5] - 1) * 100 if len(ohlcv) >= 5 else 0
        ret_3m = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-63] - 1) * 100 if len(ohlcv) >= 63 else 0

        records.append({
            'symbol': symbol,
            'bearish_score': round(bearish_score, 1),
            'close': round(close, 2),
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


def defensive_rotation_candidates(ohlcv_data: dict[str, pd.DataFrame],
                                  fundamentals: dict[str, dict],
                                  sector_map: dict[str, str]) -> pd.DataFrame:
    """
    Mode A: Select defensive stocks for bear regime rotation.

    Filters for FMCG, Pharma, IT sector stocks with:
    - High CFO/EBITDA (cash generative)
    - Low debt
    - Positive recent price action
    """
    defensive_sectors = {
        'Fast Moving Consumer Goods', 'FMCG',
        'Healthcare', 'Pharma', 'Pharmaceutical',
        'Information Technology', 'IT',
    }

    records = []

    for symbol, ohlcv in ohlcv_data.items():
        sector = sector_map.get(symbol, '')
        if sector not in defensive_sectors:
            continue

        f = fundamentals.get(symbol)
        if not f:
            continue

        ccr = cash_conversion_ratio(f)
        de = f.get('debt_equity', 0) or 0

        # Must be cash generative with low leverage
        if ccr < 0.80 or de > 1.0:
            continue

        close = ohlcv['close'].iloc[-1] if not ohlcv.empty else 0
        # Simple momentum: 3-month and 1-week returns
        ret_3m = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-63] - 1) if len(ohlcv) >= 63 else 0
        ret_1w = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-5] - 1) if len(ohlcv) >= 5 else 0

        records.append({
            'symbol': symbol,
            'close': round(close, 2),
            'sector': sector,
            'ccr': round(ccr, 2),
            'debt_equity': round(de, 2),
            'return_3m': round(ret_3m * 100, 1),
            'return_1w': round(ret_1w * 100, 1),
            'defensive_score': round(ccr * 50 + max(ret_3m * 100, 0), 1),
        })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    return df.nlargest(5, 'defensive_score')

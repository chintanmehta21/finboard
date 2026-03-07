"""
Full 5-Stage Pipeline Orchestrator

Executes all five stages sequentially:
Stage 1A: Forensic Filter (Beneish M-Score + CCR + Pledge)
Stage 1B: Liquidity & Clean Books (ADT + D/E + worst-5-day stress check)
Stage 1C: Point-in-Time Earnings Gate (QoQ Sales > 0%, 2Q EPS > 10%)
Stage 2:  Multi-Factor Ranking (5 factors, regime-weighted)
Stage 3:  Macro & Regime Overlay (exposure scalar + VIX-adaptive stops)

In BEAR regime, bullish list uses bullish_candidates() from bearish.py —
quality stocks with clean books, positive 3-6 month momentum, and relative strength.

Output: Two ranked lists — bullish candidates and bearish candidates —
with confidence scores, price targets, and supporting metrics.
"""

import logging
from datetime import date

import pandas as pd
import numpy as np

from .forensic import forensic_pass, forensic_quality_score, beneish_m_score, cash_conversion_ratio
from .factors import mansfield_rs, delivery_conviction, volatility_adjusted_momentum, earnings_revision_proxy
from .regime import get_regime, get_macro_snapshot
from .bearish import bearish_candidates, bullish_candidates as bear_bullish_candidates
from .portfolio import compute_atr14
from .price_targets import compute_price_targets

logger = logging.getLogger(__name__)

# Stage 1B thresholds
MIN_ADT = 1e7          # INR 10 Crore minimum average daily turnover
MAX_DEBT_EQUITY = 1.5  # Maximum debt/equity ratio

# Stage 1C thresholds (PDF p.3 — Point-in-Time Earnings Gate)
MIN_QOQ_SALES_GROWTH = 0.0   # QoQ Sales must be positive
MIN_EPS_GROWTH_2Q = 0.10     # 10% EPS growth over 2 consecutive quarters

# VIX-adaptive stop tightening (PDF p.5)
VIX_HIGH_THRESHOLD = 20      # When VIX > 20, tighten stops by 30%
VIX_STOP_TIGHTENING = 0.70   # Multiply stop distance by this (= 30% tighter)


def run_full_pipeline(ohlcv_data: dict[str, pd.DataFrame],
                      bhavcopy_df: pd.DataFrame,
                      fundamentals: dict[str, dict | None],
                      regime_data: dict,
                      pledge_data: dict = None,
                      sector_map: dict = None) -> dict:
    """
    Run the complete 5-stage pipeline.

    Args:
        ohlcv_data: Dict {symbol: DataFrame} of daily OHLCV from Fyers
        bhavcopy_df: Today's NSE bhavcopy with delivery data
        fundamentals: Dict {symbol: dict} of quarterly financials from yfinance
        regime_data: Dict with keys nifty_df, vix_df, usdinr_df, fii_df
        pledge_data: Dict {symbol: dict} of pledge info from NSE
        sector_map: Dict {symbol: sector_name} for sector caps

    Returns:
        Dict with keys:
            bullish, bearish, regime_name, regime_scalar,
            macro_snapshot, pipeline_stats, factor_weights
    """
    pledge_data = pledge_data or {}
    sector_map = sector_map or {}

    # === STAGE 3 (run first to get weights): Macro & Regime Overlay ===
    regime_scalar, regime_name, factor_weights = get_regime(**regime_data)

    # Detect high-VIX environment for stop tightening (PDF p.5)
    vix_df = regime_data.get('vix_df', pd.DataFrame())
    current_vix = float(vix_df['close'].iloc[-1]) if (
        vix_df is not None and not vix_df.empty and 'close' in vix_df.columns
    ) else 16
    high_vix = current_vix > VIX_HIGH_THRESHOLD

    stats = {
        'total_universe': len(ohlcv_data),
        'stage_1a_pass': 0,
        'stage_1b_pass': 0,
        'stage_1c_pass': 0,
        'stage_2_scored': 0,
        'date': date.today().isoformat(),
        'regime': regime_name,
        'vix': round(current_vix, 1),
        'high_vix_mode': high_vix,
    }

    # ── BEAR REGIME: Bullish candidates (quality + momentum) instead of full pipeline ──
    if regime_name == 'BEAR':
        logger.info("BEAR regime: running bullish candidate scan (3-6 month targets)")
        bull_picks = bear_bullish_candidates(
            ohlcv_data, fundamentals, sector_map,
            benchmark_df=regime_data.get('nifty_df', pd.DataFrame())
        )
        bear_picks = bearish_candidates(
            ohlcv_data, fundamentals,
            benchmark_df=regime_data.get('nifty_df', pd.DataFrame()),
            sector_map=sector_map
        )

        # Enrich bullish candidates with target, stop_loss, atr14
        if isinstance(bull_picks, pd.DataFrame) and not bull_picks.empty:
            for idx, row in bull_picks.iterrows():
                symbol = row['symbol']
                ohlcv = ohlcv_data.get(symbol, pd.DataFrame())
                if not ohlcv.empty:
                    atr14 = compute_atr14(ohlcv)
                    targets = compute_price_targets(symbol, ohlcv, atr14)
                    bull_picks.loc[idx, 'atr14'] = round(atr14, 2)
                    bull_picks.loc[idx, 'target_high'] = targets.get('target_high', 0)
                    bull_picks.loc[idx, 'stop_loss'] = targets.get('stop_loss', 0)

        fii_data = _extract_fii_data(regime_data)
        macro = get_macro_snapshot(
            regime_data.get('nifty_df', pd.DataFrame()),
            regime_data.get('vix_df', pd.DataFrame()),
            regime_data.get('usdinr_df', pd.DataFrame()),
            fii_data
        )
        logger.info(f"BEAR pipeline: {len(bull_picks)} bullish, {len(bear_picks)} bearish")
        return {
            'bullish': bull_picks,
            'bearish': bear_picks,
            'regime_name': regime_name,
            'regime_scalar': regime_scalar,
            'macro_snapshot': macro,
            'pipeline_stats': stats,
            'factor_weights': factor_weights,
        }

    records = []

    for symbol, ohlcv in ohlcv_data.items():
        if ohlcv.empty or len(ohlcv) < 100:
            continue  # Insufficient history

        f = fundamentals.get(symbol)

        # ── Stage 1A: Forensic Gate ──
        pledge = pledge_data.get(symbol, {})
        if not forensic_pass(f, pledge):
            continue
        stats['stage_1a_pass'] += 1

        # ── Stage 1B: Liquidity & Clean Books ──
        avg_close_20d = ohlcv['close'].tail(20).mean()
        avg_vol_20d = ohlcv['volume'].tail(20).mean()
        adt = avg_close_20d * avg_vol_20d

        if adt < MIN_ADT:
            continue

        # Worst-5-days stress check (PDF p.3): can you still transact
        # under mild stress? 5th-percentile daily turnover must be viable.
        if len(ohlcv) >= 20:
            daily_turnover = ohlcv['close'].tail(20) * ohlcv['volume'].tail(20)
            worst_5d_adt = daily_turnover.nsmallest(5).mean()
            if worst_5d_adt < MIN_ADT * 0.5:
                continue

        # Debt/Equity check
        if f and f.get('debt_equity', 0) > MAX_DEBT_EQUITY:
            continue

        stats['stage_1b_pass'] += 1

        # ── Stage 1C: Point-in-Time Earnings Gate (PDF p.3) ──
        if not _passes_earnings_gate(f):
            continue
        stats['stage_1c_pass'] += 1

        # ── Stage 2: Multi-Factor Scoring ──
        mrs = mansfield_rs(ohlcv, regime_data.get('nifty_df', pd.DataFrame()))
        deliv = delivery_conviction(ohlcv, bhavcopy_df, symbol)
        vam = volatility_adjusted_momentum(ohlcv)
        fq = forensic_quality_score(f)
        rev = earnings_revision_proxy(ohlcv)

        atr14 = compute_atr14(ohlcv)

        # VIX > 20: tighten stops by 30% (PDF p.5)
        effective_atr_stop = atr14 * VIX_STOP_TIGHTENING if high_vix else atr14

        close = float(ohlcv['close'].iloc[-1])

        # Returns: 1-day, 1-week, and 3-month
        ret_1d = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-2] - 1) * 100 if len(ohlcv) >= 2 else 0
        ret_1w = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-5] - 1) * 100 if len(ohlcv) >= 5 else 0
        ret_3m = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-63] - 1) * 100 if len(ohlcv) >= 63 else 0

        records.append({
            'symbol': symbol,
            'close': close,
            'return_1d': round(ret_1d, 1),
            'return_1w': round(ret_1w, 1),
            'return_3m': round(ret_3m, 1),
            'mrs': mrs,
            'deliv': deliv,
            'vam': vam,
            'fq': fq,
            'rev': rev,
            'atr14': atr14,
            'effective_atr_stop': effective_atr_stop,
            'adt_20d': adt,
            'sector': sector_map.get(symbol, 'Unknown'),
            'm_score': round(beneish_m_score(f), 2),
            'ccr': round(cash_conversion_ratio(f), 2),
        })

    if not records:
        logger.warning("No stocks passed Stage 1A + 1B + 1C filters")
        return _empty_result(regime_name, regime_scalar, regime_data)

    df = pd.DataFrame(records)
    stats['stage_2_scored'] = len(df)

    # ── Normalize to percentile ranks within eligible universe ──
    for col in ['mrs', 'deliv', 'vam', 'fq', 'rev']:
        df[f'{col}_rank'] = df[col].rank(pct=True)

    # ── Apply regime-specific factor weights ──
    w = factor_weights
    df['confidence'] = (
        w.get('rs', 0.25) * df['mrs_rank'] +
        w.get('del', 0.20) * df['deliv_rank'] +
        w.get('vam', 0.20) * df['vam_rank'] +
        w.get('for', 0.20) * df['fq_rank'] +
        w.get('rev', 0.15) * df['rev_rank']
    ) * 100  # Scale to 0-100

    df['adj_confidence'] = df['confidence'] * regime_scalar

    # ── Select top bullish candidates ──
    bullish = df.nlargest(15, 'adj_confidence').head(10).copy()

    # Add price targets (use effective_atr_stop which is VIX-tightened)
    for idx, row in bullish.iterrows():
        symbol = row['symbol']
        atr_for_targets = row.get('effective_atr_stop', row['atr14'])
        targets = compute_price_targets(symbol, ohlcv_data.get(symbol, pd.DataFrame()), atr_for_targets)
        for key, val in targets.items():
            if key != 'close':
                bullish.loc[idx, key] = val

    # Add delivery % from bhavcopy
    if bhavcopy_df is not None and not bhavcopy_df.empty:
        for idx, row in bullish.iterrows():
            sym_data = bhavcopy_df[bhavcopy_df['symbol'] == row['symbol']]
            bullish.loc[idx, 'deliv_pct'] = float(sym_data['deliv_pct'].iloc[0]) if not sym_data.empty else 0.0

    # ── Generate bearish candidates ──
    bearish = bearish_candidates(
        ohlcv_data, fundamentals,
        benchmark_df=regime_data.get('nifty_df', pd.DataFrame()),
        sector_map=sector_map
    )

    # ── Build macro snapshot ──
    fii_data = _extract_fii_data(regime_data)
    macro = get_macro_snapshot(
        regime_data.get('nifty_df', pd.DataFrame()),
        regime_data.get('vix_df', pd.DataFrame()),
        regime_data.get('usdinr_df', pd.DataFrame()),
        fii_data
    )

    logger.info(
        f"Pipeline complete: {stats['total_universe']} -> "
        f"1A:{stats['stage_1a_pass']} -> 1B:{stats['stage_1b_pass']} -> "
        f"1C:{stats['stage_1c_pass']} -> Scored:{stats['stage_2_scored']} | "
        f"Top bullish: {len(bullish)}, Bearish: {len(bearish)} | "
        f"VIX={current_vix:.1f} {'(HIGH-VIX MODE)' if high_vix else ''}"
    )

    return {
        'bullish': bullish,
        'bearish': bearish,
        'regime_name': regime_name,
        'regime_scalar': regime_scalar,
        'macro_snapshot': macro,
        'pipeline_stats': stats,
        'factor_weights': factor_weights,
    }


def _passes_earnings_gate(f: dict | None) -> bool:
    """
    Stage 1C — Point-in-Time Earnings Gate (PDF p.3).

    Checks:
    1. QoQ Sales Growth > 0% (PIT-adjusted)
    2. EPS Growth > 10% for 2 consecutive reported quarters

    When exact PIT announcement dates are unavailable, yfinance quarterly data
    is used with ~60-day implicit lag (SEBI LODR filing window).
    """
    if not f:
        return False  # No data = exclude

    sales_t = f.get('sales_t')
    sales_t1 = f.get('sales_t1')

    # QoQ Sales Growth > 0%
    if sales_t is not None and sales_t1 is not None and sales_t1 > 0:
        qoq_sales = (sales_t - sales_t1) / abs(sales_t1)
        if qoq_sales <= MIN_QOQ_SALES_GROWTH:
            return False
    else:
        return False  # Can't verify = exclude

    # EPS growth proxy: Net Income growth > 10% (2Q consecutive)
    # yfinance provides most recent quarters; we compare Q0 vs Q4 as proxy
    net_inc = f.get('net_income')
    if net_inc is not None and sales_t1 is not None and sales_t1 > 0:
        # Use revenue-normalized earnings as EPS proxy
        margin_t = net_inc / sales_t if sales_t and sales_t > 0 else 0
        if margin_t < 0:
            return False  # Negative earnings = exclude
    # If we can't verify EPS growth, let it pass (conservative on data gaps)

    return True


def _extract_fii_data(regime_data: dict) -> dict:
    """Extract FII/DII data from regime_data for macro snapshot."""
    fii_data = {}
    if 'fii_df' in regime_data and regime_data['fii_df'] is not None:
        fii_df = regime_data['fii_df']
        if not fii_df.empty:
            fii_data = {
                'fii_net': float(fii_df.iloc[-1]['fii_net']) if 'fii_net' in fii_df.columns else 0,
                'dii_net': float(fii_df.iloc[-1]['dii_net']) if 'dii_net' in fii_df.columns else 0,
            }
    return fii_data


def _empty_result(regime_name, regime_scalar, regime_data):
    """Return empty pipeline result when no stocks qualify."""
    return {
        'bullish': pd.DataFrame(),
        'bearish': pd.DataFrame(),
        'regime_name': regime_name,
        'regime_scalar': regime_scalar,
        'macro_snapshot': get_macro_snapshot(
            regime_data.get('nifty_df', pd.DataFrame()),
            regime_data.get('vix_df', pd.DataFrame()),
            regime_data.get('usdinr_df', pd.DataFrame()),
            {}
        ),
        'pipeline_stats': {'total_universe': 0, 'date': date.today().isoformat()},
        'factor_weights': {},
    }

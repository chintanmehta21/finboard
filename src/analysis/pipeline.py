"""
Full 5-Stage Pipeline Orchestrator (v0.2)

Executes all five stages sequentially:
Stage 1A: Forensic Filter (Beneish M-Score + CCR + Pledge)
Stage 1B: Liquidity & Clean Books (ADT + D/E sector-adjusted + worst-5-day stress)
Stage 1C: Point-in-Time Earnings Gate (QoQ/YoY Sales, lenient on missing data)
Stage 2:  Multi-Factor Ranking (4 factors, regime-weighted)
Stage 3:  Macro & Regime Overlay (exposure scalar + VIX-adaptive stops)

v0.2 changes:
- BEAR regime runs full pipeline (not bypass) with reduced exposure
- Stage 1C: lenient on missing data, YoY fallback for cyclicals
- Stage 1B: sector-adjusted D/E caps
- Factor weights adjusted per IC backtest (MRS strongest predictor)
- v0.21: FQ removed from ranking (negative IC); regime scalar to sizing not ranking
- v0.21: ATR stop 2x→3x, time stop 26→20 weeks (research-backed)

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
MAX_DEBT_EQUITY = 1.5  # Default maximum debt/equity ratio

# Sector-adjusted D/E caps (v0.2 — capital-intensive sectors get higher limits)
SECTOR_DE_CAPS = {
    'Banking': 999, 'Finance': 999, 'NBFC': 999,
    'Financial Services': 999, 'Insurance': 999,
    'Infrastructure': 3.0, 'Power': 3.0, 'Utilities': 3.0,
    'Metals': 2.5, 'Mining': 2.5,
    'Cement': 2.0, 'Realty': 2.5,
    'Energy': 2.5,
}

# Stage 1C thresholds
MIN_QOQ_SALES_GROWTH = 0.0   # QoQ Sales must be positive
CYCLICAL_SECTORS = {'Auto', 'Metals', 'Mining', 'Cement', 'Energy', 'Realty',
                    'Infrastructure', 'Power', 'Utilities', 'Sugar'}

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

    v0.2: BEAR regime no longer bypasses — runs full pipeline and uses
    bearish.py for bullish candidate selection when pipeline yields < 5.

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

    is_bear = regime_name == 'BEAR'

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

    if is_bear:
        logger.info("BEAR regime: running full pipeline with bearish.py candidate selection")

    records = []

    for symbol, ohlcv in ohlcv_data.items():
        if ohlcv.empty or len(ohlcv) < 100:
            continue  # Insufficient history

        f = fundamentals.get(symbol)

        # ── Stage 1A: Forensic Gate ──
        pledge = pledge_data.get(symbol, {})
        sym_sector = sector_map.get(symbol, '')
        if not forensic_pass(f, pledge, sector=sym_sector):
            continue
        stats['stage_1a_pass'] += 1

        # ── Stage 1B: Liquidity & Clean Books ──
        avg_close_20d = ohlcv['close'].tail(20).mean()
        avg_vol_20d = ohlcv['volume'].tail(20).mean()
        adt = avg_close_20d * avg_vol_20d

        if adt < MIN_ADT:
            continue

        # Worst-5-days stress check (PDF p.3)
        if len(ohlcv) >= 20:
            daily_turnover = ohlcv['close'].tail(20) * ohlcv['volume'].tail(20)
            worst_5d_adt = daily_turnover.nsmallest(5).mean()
            if worst_5d_adt < MIN_ADT * 0.3:  # v0.2: relaxed 0.5 → 0.3
                continue

        # Sector-adjusted D/E check (v0.2)
        de_cap = SECTOR_DE_CAPS.get(sym_sector, MAX_DEBT_EQUITY)
        if f and (f.get('debt_equity', 0) or 0) > de_cap:
            continue

        stats['stage_1b_pass'] += 1

        # ── Stage 1C: Point-in-Time Earnings Gate ──
        if not _passes_earnings_gate(f, sym_sector):
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

        # Returns: 1-day, 1-week, 3-month, 6-month
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

    # ── Bullish candidate selection ──
    if records:
        df = pd.DataFrame(records)
        stats['stage_2_scored'] = len(df)

        # Normalize to percentile ranks within eligible universe
        # v0.21: FQ removed from ranking — negative IC (-0.17/-0.23)
        # FQ still computed and stored for display but not used in composite
        for col in ['mrs', 'deliv', 'vam', 'rev']:
            df[f'{col}_rank'] = df[col].rank(pct=True)

        # Apply regime-specific factor weights (4 factors)
        w = factor_weights
        df['confidence'] = (
            w.get('rs', 0.35) * df['mrs_rank'] +
            w.get('del', 0.20) * df['deliv_rank'] +
            w.get('vam', 0.20) * df['vam_rank'] +
            w.get('rev', 0.25) * df['rev_rank']
        ) * 100  # Scale to 0-100

        # v0.21: Regime scalar applied to position SIZING downstream,
        # not to ranking score. Full-scale confidence for display/comparison.
        df['adj_confidence'] = df['confidence']

        # Select top bullish candidates
        bullish = df.nlargest(15, 'adj_confidence').head(10).copy()
    else:
        bullish = pd.DataFrame()

    # v0.2: In BEAR regime, if pipeline yields < 5 bullish, supplement with
    # bearish.py's bullish_candidates (quality + momentum model)
    if is_bear and len(bullish) < 5:
        logger.info(f"BEAR regime pipeline yielded {len(bullish)} bullish, supplementing from bearish.py")
        bear_bull = bear_bullish_candidates(
            ohlcv_data, fundamentals, sector_map,
            benchmark_df=regime_data.get('nifty_df', pd.DataFrame())
        )
        if isinstance(bear_bull, pd.DataFrame) and not bear_bull.empty:
            # Use bearish.py candidates, excluding any already in pipeline bullish
            existing = set(bullish['symbol'].tolist()) if not bullish.empty else set()
            supplement = bear_bull[~bear_bull['symbol'].isin(existing)].head(10 - len(bullish))
            if not supplement.empty:
                bullish = pd.concat([bullish, supplement], ignore_index=True)

    # Enrich bullish with price targets and ATR
    if not bullish.empty:
        for idx, row in bullish.iterrows():
            symbol = row['symbol']
            ohlcv = ohlcv_data.get(symbol, pd.DataFrame())
            if not ohlcv.empty:
                atr14 = row.get('atr14') or compute_atr14(ohlcv)
                atr_for_targets = row.get('effective_atr_stop', atr14)
                targets = compute_price_targets(symbol, ohlcv, atr_for_targets)
                if 'atr14' not in bullish.columns or pd.isna(row.get('atr14')):
                    bullish.loc[idx, 'atr14'] = round(atr14, 2)
                for key, val in targets.items():
                    if key != 'close':
                        bullish.loc[idx, key] = val

        # Add delivery % from bhavcopy
        if bhavcopy_df is not None and not bhavcopy_df.empty:
            for idx, row in bullish.iterrows():
                sym_data = bhavcopy_df[bhavcopy_df['symbol'] == row['symbol']]
                bullish.loc[idx, 'deliv_pct'] = float(sym_data['deliv_pct'].iloc[0]) if not sym_data.empty else 0.0

    # ── Generate bearish candidates (always from full universe) ──
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
        f"Regime: {regime_name} | "
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


def _passes_earnings_gate(f: dict | None, sector: str = '') -> bool:
    """
    Stage 1C — Point-in-Time Earnings Gate (v0.2).

    v0.2 changes:
    - Missing data = PASS (data gap ≠ deterioration)
    - Cyclical sectors use YoY fallback instead of QoQ
    - Negative earnings only excludes if BOTH revenue declining AND net income negative
    """
    if not f:
        return True  # v0.2: No data = pass conservatively (don't penalize missing data)

    sales_t = f.get('sales_t')
    sales_t1 = f.get('sales_t1')

    # QoQ Sales Growth check
    if sales_t is not None and sales_t1 is not None and sales_t1 > 0:
        qoq_sales = (sales_t - sales_t1) / abs(sales_t1)

        is_cyclical = sector in CYCLICAL_SECTORS
        if is_cyclical:
            # Cyclical sectors: only exclude if QoQ decline > 15%
            # (seasonal effects cause normal QoQ fluctuations)
            if qoq_sales < -0.15:
                return False
        else:
            # Non-cyclical: exclude if QoQ sales declining
            if qoq_sales <= MIN_QOQ_SALES_GROWTH:
                return False
    # else: missing data = pass (v0.2)

    # EPS check: only exclude if revenue is declining AND net income is negative
    net_inc = f.get('net_income')
    if net_inc is not None and net_inc < 0:
        # Negative net income — but only fail if revenue also declining
        if sales_t is not None and sales_t1 is not None and sales_t1 > 0:
            qoq_sales = (sales_t - sales_t1) / abs(sales_t1)
            if qoq_sales < 0:
                return False  # Both revenue declining and negative earnings

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

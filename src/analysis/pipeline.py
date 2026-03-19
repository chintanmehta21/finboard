"""
Progressive Pipeline Orchestrator (v0.22)

Fyers-First Architecture — filter by what you CAN measure, not what you CAN'T.

Stage 1: Technical + Liquidity Pre-Screen (Fyers OHLCV — 100% coverage)
  - ADT >= ₹10 Crore (HARD)
  - Worst-5-day stress test (HARD)
Stage 2: Fundamental Quality (yfinance — OPTIONAL, ~8-10% coverage)
  - M-Score > -2.22 → HARD exclude (only when data available)
  - Promoter Pledge > 5% → HARD exclude (only when data available)
  - D/E, CCR, Earnings → SOFT (scoring penalty, not exclusion)
  - Missing data = PASS THROUGH (not a red flag)
Stage 3: Multi-Factor Scoring & Ranking
  - Tier A: MRS, VAM, Rev Proxy (all stocks, from Fyers OHLCV)
  - Tier B: Delivery Conviction (stocks with bhavcopy)
  - Confidence multiplier: (n_available / n_total) ^ 0.5
Stage 4: Macro & Regime Overlay (exposure scalar + VIX-adaptive stops)

v0.22 changes (research-backed):
- Liquidity BEFORE forensic (Browser T1: cheapest filter first)
- Missing fundamentals = PASS, not exclude (Browser T2, NLM Q1-Q2)
- M-Score, Pledge = HARD gates; D/E, CCR = SOFT (NLM Q3)
- 200 DMA NOT a gate (Browser T4: hurts live performance)
- Confidence multiplier for data completeness (Browser T6)
- All factors winsorized at ±3σ (Browser T7)
"""

import logging
from datetime import date

import pandas as pd
import numpy as np

from .forensic import forensic_hard_pass, forensic_pass, forensic_quality_score, beneish_m_score, cash_conversion_ratio
from .factors import mansfield_rs, delivery_conviction, volatility_adjusted_momentum, earnings_revision_proxy
from .regime import get_regime, get_macro_snapshot
from .bearish import bearish_candidates, bullish_candidates as bear_bullish_candidates
from .portfolio import compute_atr14
from .price_targets import compute_price_targets

logger = logging.getLogger(__name__)

# Stage 1 thresholds (Fyers OHLCV — 100% coverage)
MIN_ADT = 1e7          # INR 10 Crore minimum average daily turnover

# Sector-adjusted D/E caps (v0.2 — now SOFT scoring, not hard gate)
SECTOR_DE_CAPS = {
    'Banking': 999, 'Finance': 999, 'NBFC': 999,
    'Financial Services': 999, 'Insurance': 999,
    'Infrastructure': 3.0, 'Power': 3.0, 'Utilities': 3.0,
    'Metals': 2.5, 'Mining': 2.5,
    'Cement': 2.0, 'Realty': 2.5,
    'Energy': 2.5,
}
MAX_DEBT_EQUITY = 1.5  # Default sector cap

# Stage 2 thresholds
CYCLICAL_SECTORS = {'Auto', 'Metals', 'Mining', 'Cement', 'Energy', 'Realty',
                    'Infrastructure', 'Power', 'Utilities', 'Sugar'}

# VIX-adaptive stop tightening
VIX_HIGH_THRESHOLD = 20
VIX_STOP_TIGHTENING = 0.70


def run_full_pipeline(ohlcv_data: dict[str, pd.DataFrame],
                      bhavcopy_df: pd.DataFrame,
                      fundamentals: dict[str, dict | None],
                      regime_data: dict,
                      pledge_data: dict = None,
                      sector_map: dict = None) -> dict:
    """
    Run the progressive pipeline (v0.22 — Fyers-first architecture).

    Stage 1: Technical + Liquidity (Fyers OHLCV, 100% coverage)
    Stage 2: Fundamental Quality (yfinance, optional)
    Stage 3: Multi-Factor Scoring with confidence multiplier
    Stage 4: Regime overlay (exposure scalar)
    """
    pledge_data = pledge_data or {}
    sector_map = sector_map or {}

    # === Regime detection (run first to get factor weights) ===
    regime_scalar, regime_name, factor_weights = get_regime(**regime_data)

    vix_df = regime_data.get('vix_df', pd.DataFrame())
    current_vix = float(vix_df['close'].iloc[-1]) if (
        vix_df is not None and not vix_df.empty and 'close' in vix_df.columns
    ) else 16
    high_vix = current_vix > VIX_HIGH_THRESHOLD
    is_bear = regime_name == 'BEAR'

    stats = {
        'total_universe': len(ohlcv_data),
        'stage_1a_pass': 0,   # Technical + Liquidity (kept as stage_1a for dashboard compat)
        'stage_1b_pass': 0,   # Fundamental Quality
        'stage_1c_pass': 0,   # (same as 1b — no separate earnings gate now)
        'stage_2_scored': 0,
        'has_fundamentals': 0,
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

        # ── Stage 1: Technical + Liquidity Pre-Screen (Fyers OHLCV) ──
        # All computed from price/volume data — 100% coverage
        avg_close_20d = ohlcv['close'].tail(20).mean()
        avg_vol_20d = ohlcv['volume'].tail(20).mean()
        adt = avg_close_20d * avg_vol_20d

        if adt < MIN_ADT:
            continue

        # Worst-5-days stress check
        if len(ohlcv) >= 20:
            daily_turnover = ohlcv['close'].tail(20) * ohlcv['volume'].tail(20)
            worst_5d_adt = daily_turnover.nsmallest(5).mean()
            if worst_5d_adt < MIN_ADT * 0.3:
                continue

        stats['stage_1a_pass'] += 1

        # ── Stage 2: Fundamental Quality (yfinance — OPTIONAL) ──
        f = fundamentals.get(symbol)
        sym_sector = sector_map.get(symbol, '')
        has_fundamentals = f is not None
        forensic_clean = True  # Default: assume clean if no data

        if has_fundamentals:
            stats['has_fundamentals'] += 1

            # HARD gates: M-Score + Pledge only (v0.22 — CCR moved to SOFT)
            pledge = pledge_data.get(symbol, {})
            if not forensic_hard_pass(f, pledge):
                continue

            # SOFT checks: D/E, CCR, earnings — flag but don't exclude
            de_cap = SECTOR_DE_CAPS.get(sym_sector, MAX_DEBT_EQUITY)
            de_ratio = f.get('debt_equity', 0) or 0
            if de_ratio > de_cap:
                forensic_clean = False

            # CCR is now SOFT (v0.22 — Buffett: EBITDA is flawed metric)
            ccr_val = cash_conversion_ratio(f)
            ccr_exempt = sym_sector in {'Banking', 'Finance', 'Insurance', 'NBFC'}
            if not ccr_exempt and ccr_val != -1.0 and ccr_val < 0.80:
                forensic_clean = False

            if not _passes_earnings_gate(f, sym_sector):
                forensic_clean = False
        # else: No fundamentals — PASS THROUGH (missing data ≠ red flag)

        stats['stage_1b_pass'] += 1
        stats['stage_1c_pass'] += 1  # Same as 1b in v0.22

        # ── Stage 3: Multi-Factor Scoring ──
        mrs = mansfield_rs(ohlcv, regime_data.get('nifty_df', pd.DataFrame()))
        deliv = delivery_conviction(ohlcv, bhavcopy_df, symbol)
        vam = volatility_adjusted_momentum(ohlcv)
        fq = forensic_quality_score(f)
        rev = earnings_revision_proxy(ohlcv)

        atr14 = compute_atr14(ohlcv)
        effective_atr_stop = atr14 * VIX_STOP_TIGHTENING if high_vix else atr14
        close = float(ohlcv['close'].iloc[-1])

        # 52-week high proximity (George & Hwang 2004 — Browser T5)
        high_52w = float(ohlcv['high'].tail(252).max()) if len(ohlcv) >= 252 else float(ohlcv['high'].max())
        high_52w_pct = (close / high_52w) if high_52w > 0 else 0

        # Returns
        ret_1d = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-2] - 1) * 100 if len(ohlcv) >= 2 else 0
        ret_1w = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-5] - 1) * 100 if len(ohlcv) >= 5 else 0
        ret_3m = (ohlcv['close'].iloc[-1] / ohlcv['close'].iloc[-63] - 1) * 100 if len(ohlcv) >= 63 else 0

        # Count available factors for confidence multiplier
        # Use abs() > 1e-9 to distinguish "computed as ~zero" from "truly unavailable"
        # Factor functions return exactly 0.0 for data-unavailable cases
        n_factors = sum(1 for v in [mrs, deliv, vam, rev] if abs(v) > 1e-9)
        n_factors = max(n_factors, 1)  # At least 1

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
            'sector': sym_sector or 'Unknown',
            'm_score': round(beneish_m_score(f), 2) if f else None,
            'ccr': round(cash_conversion_ratio(f), 2) if f else None,
            'has_fundamentals': has_fundamentals,
            'forensic_clean': forensic_clean,
            'high_52w_pct': round(high_52w_pct, 3),
            'n_factors': n_factors,
        })

    # ── Bullish candidate selection ──
    if records:
        df = pd.DataFrame(records)
        stats['stage_2_scored'] = len(df)

        # Winsorize ALL factors at ±3σ before ranking (Browser T7)
        for col in ['mrs', 'vam', 'rev']:
            mean_val = df[col].mean()
            std_val = df[col].std()
            if std_val > 0:
                df[col] = df[col].clip(mean_val - 3*std_val, mean_val + 3*std_val)

        # Percentile ranks within scored universe
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

        # Confidence multiplier based on data completeness (Browser T6)
        # Formula: (n_available / n_total) ^ 0.5
        df['data_confidence'] = (df['n_factors'] / 4.0) ** 0.5

        # v0.22: Apply confidence multiplier + forensic penalty
        # forensic_clean=False means D/E, CCR, or earnings flagged → 0.90x penalty
        df['forensic_penalty'] = df['forensic_clean'].map({True: 1.0, False: 0.90})
        df['adj_confidence'] = df['confidence'] * df['data_confidence'] * df['forensic_penalty']

        # Select top bullish candidates
        bullish = df.nlargest(15, 'adj_confidence').head(10).copy()
    else:
        bullish = pd.DataFrame()

    # BEAR regime supplement (unchanged from v0.2)
    if is_bear and len(bullish) < 5:
        logger.info(f"BEAR regime pipeline yielded {len(bullish)} bullish, supplementing from bearish.py")
        bear_bull = bear_bullish_candidates(
            ohlcv_data, fundamentals, sector_map,
            benchmark_df=regime_data.get('nifty_df', pd.DataFrame())
        )
        if isinstance(bear_bull, pd.DataFrame) and not bear_bull.empty:
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

    # ── Generate bearish candidates (unchanged — already works well) ──
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
        f"Scored:{stats['stage_2_scored']} | "
        f"Fundamentals: {stats['has_fundamentals']}/{stats['stage_1a_pass']} | "
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
    Stage 2 sub-check — Point-in-Time Earnings Gate (v0.22).

    v0.22: Now a SOFT check — returns False to flag (reduce confidence),
    but the caller decides whether to exclude or just penalize.
    Missing data = True (pass conservatively).
    """
    if not f:
        return True

    sales_t = f.get('sales_t')
    sales_t1 = f.get('sales_t1')

    if sales_t is not None and sales_t1 is not None and sales_t1 > 0:
        qoq_sales = (sales_t - sales_t1) / abs(sales_t1)

        is_cyclical = sector in CYCLICAL_SECTORS
        if is_cyclical:
            if qoq_sales < -0.15:
                return False
        else:
            if qoq_sales <= 0.0:
                return False

    net_inc = f.get('net_income')
    if net_inc is not None and net_inc < 0:
        if sales_t is not None and sales_t1 is not None and sales_t1 > 0:
            qoq_sales = (sales_t - sales_t1) / abs(sales_t1)
            if qoq_sales < 0:
                return False

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



"""
EDA v0.2 — Full Pipeline Diagnostics for 3-6 Month Prediction Optimization

Audits ALL 5 pipeline stages plus bearish/bullish models:
  Stage 1A: Forensic deep dive + CCR sector benchmarks
  Stage 1B: Liquidity & leverage + D/E sector distribution
  Stage 1C: Earnings gate root cause (CRITICAL — currently blocks everything)
  Stage 2:  Factor scores + correlation + IC backtest + small universe noise
  Stage 3:  Regime sensitivity + bearish model audit + bullish model audit

Run:
    python -m src.eda.v0.2.run_eda
"""

import sys
import logging
from pathlib import Path
from datetime import date

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.sample_data import (
    generate_sample_fundamentals, generate_sample_ohlcv,
    generate_sample_index_data, generate_sample_bhavcopy,
    generate_sample_fii_data, generate_sample_pledge_data,
    get_sample_sector_map, SAMPLE_SYMBOLS,
)
from src.analysis.forensic import (
    beneish_m_score, cash_conversion_ratio, forensic_pass,
    forensic_quality_score, CCR_THRESHOLD, M_SCORE_THRESHOLD,
    PLEDGE_MAX_PCT, PLEDGE_MAX_DELTA, CCR_EXEMPT_SECTORS,
)
from src.analysis.factors import (
    mansfield_rs, delivery_conviction, volatility_adjusted_momentum,
    earnings_revision_proxy,
)
from src.analysis.regime import get_regime, REGIME_SCALARS, REGIME_WEIGHTS
from src.analysis.bearish import bearish_candidates, bullish_candidates as bear_bullish_candidates

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("eda.v0.2")

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ===================================================================
# Helpers
# ===================================================================

def _to_forensic_format(sym_fund: dict) -> dict:
    """Convert sample_data fundamentals to forensic-expected key format."""
    return {
        "sales_t": sym_fund.get("sales_t", 0),
        "sales_t1": sym_fund.get("sales_t1", 0),
        "net_income": sym_fund.get("net_income", 0),
        "ebitda": sym_fund.get("ebitda", 0),
        "cfo": sym_fund.get("cfo", 0),
        "total_assets": sym_fund.get("total_assets", 0),
        "receivables_t": sym_fund.get("receivables_t", sym_fund.get("receivables", 0)),
        "receivables_t1": sym_fund.get("receivables_t1", 0),
        "current_assets_t": sym_fund.get("current_assets_t", 0),
        "current_assets_t1": sym_fund.get("current_assets_t1", 0),
        "ppe_t": sym_fund.get("ppe_t", 0),
        "ppe_t1": sym_fund.get("ppe_t1", 0),
        "debt_t": sym_fund.get("debt_t", sym_fund.get("total_debt", 0)),
        "debt_t1": sym_fund.get("debt_t1", 0),
        "total_equity": sym_fund.get("total_equity", 0),
        "debt_equity": sym_fund.get("debt_equity", 0),
    }


def _compute_m_score_components(f: dict) -> dict:
    """Break down M-Score into individual variable contributions."""
    eps = 1e-9
    recv_t = f.get("receivables_t", 0) or 0
    recv_t1 = f.get("receivables_t1", 0) or 0
    sales_t = f.get("sales_t", 0) or eps
    sales_t1 = f.get("sales_t1", 0) or eps

    if recv_t1 == 0 and recv_t == 0:
        dsri = 1.0
    elif recv_t1 == 0:
        dsri = 1.0
    else:
        dsri = (recv_t / sales_t) / (recv_t1 / sales_t1 + eps)

    ca_t = f.get("current_assets_t", 0) or 0
    ppe_t = f.get("ppe_t", 0) or 0
    ta_t = f.get("total_assets", 0) or eps
    ca_t1 = f.get("current_assets_t1", 0) or 0
    ppe_t1 = f.get("ppe_t1", 0) or 0
    if (ca_t1 == 0 and ppe_t1 == 0) and (ca_t == 0 and ppe_t == 0):
        aqi = 1.0
    elif ca_t1 == 0 and ppe_t1 == 0:
        aqi = 1.0
    else:
        aqi_t = 1 - (ca_t + ppe_t) / ta_t if ta_t > eps else 0
        aqi_t1 = 1 - (ca_t1 + ppe_t1) / ta_t if ta_t > eps else eps
        aqi = aqi_t / (aqi_t1 + eps)

    net_income = f.get("net_income", 0) or 0
    cfo = f.get("cfo", 0) or 0
    tata = (net_income - cfo) / (ta_t + eps)

    debt_t = f.get("debt_t", 0) or 0
    debt_t1 = f.get("debt_t1", 0) or 0
    lvgi_t = debt_t / (ta_t + eps)
    lvgi_t1 = debt_t1 / (ta_t + eps)
    lvgi = lvgi_t / (lvgi_t1 + eps) if lvgi_t1 > eps else 1.0

    sgi = sales_t / (sales_t1 + eps)

    return {
        "dsri": round(dsri, 4), "aqi": round(aqi, 4),
        "tata": round(tata, 6), "lvgi": round(lvgi, 4), "sgi": round(sgi, 4),
    }


# ===================================================================
# STAGE 1A EDA — Forensic Audit
# ===================================================================

def eda_1a_forensic_deep_dive(fundamentals, pledge_data, sector_map):
    """Per-symbol forensic decomposition with sector context."""
    logger.info("EDA 1A: Forensic Deep Dive")
    records = []

    # Compute sector CCR medians first
    sector_ccrs = {}
    for symbol in SAMPLE_SYMBOLS:
        raw = fundamentals.get(symbol)
        if not raw:
            continue
        f = _to_forensic_format(raw)
        ccr = cash_conversion_ratio(f)
        sector = sector_map.get(symbol, 'Unknown')
        if ccr != -1.0:  # Exclude sentinel
            sector_ccrs.setdefault(sector, []).append(ccr)

    sector_medians = {s: np.median(v) for s, v in sector_ccrs.items() if v}

    for symbol in SAMPLE_SYMBOLS:
        raw = fundamentals.get(symbol)
        if not raw:
            continue
        f = _to_forensic_format(raw)
        m_score = beneish_m_score(f)
        ccr = cash_conversion_ratio(f)
        comps = _compute_m_score_components(f)
        sector = sector_map.get(symbol, 'Unknown')
        pledge = pledge_data.get(symbol, {})
        pledge_pct = pledge.get("pledge_pct", 0)
        pledge_delta = pledge.get("pledge_delta", 0)

        pledge_forensic = {"data_available": True, "pledge_pct": pledge_pct, "pledge_delta_1q": pledge_delta}
        fpass = forensic_pass(f, pledge_forensic, sector=sector)

        reasons = []
        if m_score > M_SCORE_THRESHOLD:
            reasons.append(f"M-Score={m_score:.2f}")
        if sector not in CCR_EXEMPT_SECTORS:
            if ccr == -1.0:
                pass  # sentinel
            elif ccr < CCR_THRESHOLD:
                reasons.append(f"CCR={ccr:.2f}<{CCR_THRESHOLD}")
        if pledge_pct > PLEDGE_MAX_PCT:
            reasons.append(f"Pledge={pledge_pct:.1f}%")
        if pledge_delta > PLEDGE_MAX_DELTA:
            reasons.append(f"PledgeDelta={pledge_delta:.1f}pp")

        records.append({
            "symbol": symbol, "sector": sector,
            "m_score": round(m_score, 4),
            "dsri": comps["dsri"], "aqi": comps["aqi"],
            "tata": comps["tata"], "lvgi": comps["lvgi"], "sgi": comps["sgi"],
            "ccr_1yr": round(ccr, 4),
            "ccr_sector_median": round(sector_medians.get(sector, 0), 4),
            "ccr_vs_sector": round(ccr - sector_medians.get(sector, 0), 4) if ccr != -1.0 else 0,
            "pledge_pct": round(pledge_pct, 2),
            "pledge_delta_1q": round(pledge_delta, 2),
            "forensic_pass": fpass,
            "failure_reasons": "; ".join(reasons) if reasons else "PASS",
        })

    df = pd.DataFrame(records)
    df.to_csv(OUTPUT_DIR / "1a_forensic_deep_dive.csv", index=False)
    logger.info(f"  -> 1a_forensic_deep_dive.csv: {len(df)} symbols, {df['forensic_pass'].sum()} pass")
    return df


def eda_1a_ccr_sector_benchmarks(forensic_df):
    """Sector CCR statistics."""
    logger.info("EDA 1A: CCR Sector Benchmarks")
    valid = forensic_df[forensic_df['ccr_1yr'] != -1.0].copy()

    records = []
    for sector in sorted(valid['sector'].unique()):
        sdf = valid[valid['sector'] == sector]
        ccr_vals = sdf['ccr_1yr']
        records.append({
            "sector": sector, "count": len(sdf),
            "ccr_p25": round(ccr_vals.quantile(0.25), 4),
            "ccr_median": round(ccr_vals.median(), 4),
            "ccr_p75": round(ccr_vals.quantile(0.75), 4),
            "pass_rate_80": round((ccr_vals >= 0.80).mean() * 100, 1),
            "suggested_floor": round(max(ccr_vals.quantile(0.25), 0.50), 2),
        })

    df = pd.DataFrame(records)
    df.to_csv(OUTPUT_DIR / "1a_ccr_sector_benchmarks.csv", index=False)
    logger.info(f"  -> 1a_ccr_sector_benchmarks.csv: {len(df)} sectors")
    return df


# ===================================================================
# STAGE 1B EDA — Liquidity & Leverage
# ===================================================================

def eda_1b_liquidity_analysis(ohlcv_data, fundamentals, sector_map):
    """Per-symbol liquidity + leverage audit."""
    logger.info("EDA 1B: Liquidity & Leverage Analysis")
    MIN_ADT = 1e7
    MAX_DE = 1.5

    SECTOR_DE_CAPS = {
        'Banking': 999, 'Finance': 999, 'NBFC': 999,
        'Infrastructure': 3.0, 'Power': 3.0, 'Infra': 3.0,
        'Metals': 2.5, 'Cement': 2.0, 'Realty': 2.5,
    }

    records = []
    for symbol in SAMPLE_SYMBOLS:
        ohlcv = ohlcv_data.get(symbol)
        raw = fundamentals.get(symbol)
        sector = sector_map.get(symbol, 'Unknown')

        if ohlcv is None or ohlcv.empty or len(ohlcv) < 20:
            continue

        avg_close_20d = ohlcv['close'].tail(20).mean()
        avg_vol_20d = ohlcv['volume'].tail(20).mean()
        adt = avg_close_20d * avg_vol_20d

        daily_turnover = ohlcv['close'].tail(20) * ohlcv['volume'].tail(20)
        worst_5d_adt = daily_turnover.nsmallest(5).mean()

        de = raw.get('debt_equity', 0) if raw else 0
        de_cap = SECTOR_DE_CAPS.get(sector, MAX_DE)

        # Amihud illiquidity ratio
        daily_ret = ohlcv['close'].pct_change().tail(20).abs()
        daily_vol_inr = (ohlcv['close'] * ohlcv['volume']).tail(20)
        amihud = (daily_ret / (daily_vol_inr + 1e-9)).mean() * 1e6

        records.append({
            "symbol": symbol, "sector": sector,
            "adt_20d": round(adt, 0),
            "worst_5d_adt": round(worst_5d_adt, 0),
            "de_ratio": round(de, 4),
            "de_sector_cap": de_cap,
            "amihud_ratio": round(float(amihud), 6) if np.isfinite(amihud) else 0,
            "pass_adt": adt >= MIN_ADT,
            "pass_worst5d": worst_5d_adt >= MIN_ADT * 0.5,
            "pass_de_universal": de <= MAX_DE,
            "pass_de_sector_adj": de <= de_cap,
            "overall_1b_pass": (adt >= MIN_ADT) and (worst_5d_adt >= MIN_ADT * 0.5) and (de <= MAX_DE),
        })

    df = pd.DataFrame(records)
    df.to_csv(OUTPUT_DIR / "1b_liquidity_analysis.csv", index=False)
    p = df['overall_1b_pass'].sum()
    logger.info(f"  -> 1b_liquidity_analysis.csv: {len(df)} symbols, {p} pass 1B ({p/len(df)*100:.0f}%)")
    return df


def eda_1b_de_sector_distribution(liquidity_df):
    """Sector D/E percentiles."""
    logger.info("EDA 1B: D/E Sector Distribution")
    records = []
    for sector in sorted(liquidity_df['sector'].unique()):
        sdf = liquidity_df[liquidity_df['sector'] == sector]
        de_vals = sdf['de_ratio']
        records.append({
            "sector": sector, "count": len(sdf),
            "de_p25": round(de_vals.quantile(0.25), 4),
            "de_median": round(de_vals.median(), 4),
            "de_p75": round(de_vals.quantile(0.75), 4),
            "pass_rate_1_5": round((de_vals <= 1.5).mean() * 100, 1),
            "suggested_cap": round(min(de_vals.quantile(0.75) * 1.5, 3.0), 2),
        })
    df = pd.DataFrame(records)
    df.to_csv(OUTPUT_DIR / "1b_de_sector_distribution.csv", index=False)
    logger.info(f"  -> 1b_de_sector_distribution.csv: {len(df)} sectors")
    return df


# ===================================================================
# STAGE 1C EDA — Earnings Gate (CRITICAL)
# ===================================================================

def eda_1c_earnings_gate_analysis(fundamentals, sector_map):
    """Root cause analysis of why Stage 1C blocks everything."""
    logger.info("EDA 1C: Earnings Gate Analysis (CRITICAL)")
    CYCLICAL = {'Auto', 'Metals', 'Cement', 'Energy', 'Infra', 'Realty'}

    records = []
    for symbol in SAMPLE_SYMBOLS:
        raw = fundamentals.get(symbol)
        sector = sector_map.get(symbol, 'Unknown')
        is_cyclical = sector in CYCLICAL

        row = {"symbol": symbol, "sector": sector, "is_cyclical": is_cyclical}

        if not raw:
            row.update({
                "sales_t": None, "sales_t1": None, "qoq_growth": None,
                "net_income": None, "ni_growth_pct": None,
                "earnings_pass_current": False,
                "fail_reason": "no_fundamentals",
                "pass_if_data_lenient": True,
            })
            records.append(row)
            continue

        f = _to_forensic_format(raw)
        sales_t = f.get('sales_t')
        sales_t1 = f.get('sales_t1')
        net_inc = f.get('net_income')

        row["sales_t"] = round(sales_t, 2) if sales_t else None
        row["sales_t1"] = round(sales_t1, 2) if sales_t1 else None
        row["net_income"] = round(net_inc, 2) if net_inc else None

        # QoQ Sales Growth
        qoq = None
        if sales_t is not None and sales_t1 is not None and sales_t1 > 0:
            qoq = (sales_t - sales_t1) / abs(sales_t1)
        row["qoq_growth"] = round(qoq, 4) if qoq is not None else None

        # YoY proxy (compare current to ~4Q ago if available)
        row["yoy_growth"] = None  # Not available in sample data

        # NI growth
        ni_growth = None
        if net_inc is not None and sales_t is not None and sales_t > 0:
            margin = net_inc / sales_t
            row["ni_margin"] = round(margin, 4)
        else:
            row["ni_margin"] = None

        # Current earnings gate logic
        pass_current = False
        fail_reason = ""

        if sales_t is None or sales_t1 is None or sales_t1 <= 0:
            fail_reason = "sales_t1_missing_or_zero"
        elif qoq is not None and qoq <= 0.0:
            fail_reason = f"qoq_negative={qoq:.4f}"
        else:
            # QoQ passes, check EPS proxy
            if net_inc is not None and sales_t is not None and sales_t > 0:
                if net_inc / sales_t < 0:
                    fail_reason = "negative_margin"
                else:
                    pass_current = True
            else:
                pass_current = True  # Data gap in EPS = pass

        row["earnings_pass_current"] = pass_current
        row["fail_reason"] = fail_reason if fail_reason else "PASS"

        # Sensitivity analysis
        row["pass_if_qoq_neg2pct"] = qoq is not None and qoq > -0.02 if qoq is not None else True
        row["pass_if_data_lenient"] = True  # Would pass if missing data = pass
        row["pass_if_ni_positive"] = net_inc is not None and net_inc > 0 if net_inc is not None else True

        records.append(row)

    df = pd.DataFrame(records)
    df.to_csv(OUTPUT_DIR / "1c_earnings_gate_analysis.csv", index=False)

    pass_current = df['earnings_pass_current'].sum()
    pass_lenient = df['pass_if_data_lenient'].sum()
    logger.info(
        f"  -> 1c_earnings_gate_analysis.csv: {len(df)} symbols | "
        f"Pass current: {pass_current} ({pass_current/len(df)*100:.0f}%) | "
        f"Pass if lenient: {pass_lenient} ({pass_lenient/len(df)*100:.0f}%)"
    )
    return df


def eda_1c_data_quality_audit(fundamentals):
    """Which fields are missing for Stage 1C."""
    logger.info("EDA 1C: Data Quality Audit")
    records = []
    for symbol in SAMPLE_SYMBOLS:
        raw = fundamentals.get(symbol)
        if not raw:
            records.append({
                "symbol": symbol,
                "has_sales_t": False, "has_sales_t1": False, "has_net_income": False,
                "sales_t_value": None, "sales_t1_value": None, "ni_value": None,
                "data_source": "missing",
            })
            continue

        records.append({
            "symbol": symbol,
            "has_sales_t": raw.get('sales_t') is not None and raw.get('sales_t', 0) != 0,
            "has_sales_t1": raw.get('sales_t1') is not None and raw.get('sales_t1', 0) != 0,
            "has_net_income": raw.get('net_income') is not None and raw.get('net_income', 0) != 0,
            "sales_t_value": round(raw.get('sales_t', 0), 2) if raw.get('sales_t') else None,
            "sales_t1_value": round(raw.get('sales_t1', 0), 2) if raw.get('sales_t1') else None,
            "ni_value": round(raw.get('net_income', 0), 2) if raw.get('net_income') else None,
            "data_source": "yfinance_or_synthetic",
        })

    df = pd.DataFrame(records)
    df.to_csv(OUTPUT_DIR / "1c_data_quality_audit.csv", index=False)

    has_st = df['has_sales_t'].sum()
    has_st1 = df['has_sales_t1'].sum()
    has_ni = df['has_net_income'].sum()
    logger.info(
        f"  -> 1c_data_quality_audit.csv: {len(df)} symbols | "
        f"has_sales_t: {has_st} | has_sales_t1: {has_st1} | has_net_income: {has_ni}"
    )
    return df


# ===================================================================
# STAGE 2 EDA — Factor Quality for 3-6M Prediction
# ===================================================================

def eda_2_factor_scores(ohlcv_data, bhavcopy_df, fundamentals, sector_map, nifty_df):
    """Full factor scores for all eligible stocks."""
    logger.info("EDA 2: Factor Scores")
    records = []

    for symbol in SAMPLE_SYMBOLS:
        ohlcv = ohlcv_data.get(symbol)
        raw = fundamentals.get(symbol)
        sector = sector_map.get(symbol, 'Unknown')

        if ohlcv is None or ohlcv.empty or len(ohlcv) < 100:
            continue
        if not raw:
            continue

        f = _to_forensic_format(raw)
        mrs = mansfield_rs(ohlcv, nifty_df)
        deliv = delivery_conviction(ohlcv, bhavcopy_df, symbol)
        vam = volatility_adjusted_momentum(ohlcv)
        fq = forensic_quality_score(f)
        rev = earnings_revision_proxy(ohlcv)

        # Compute 3M and 6M actual returns (backward-looking as proxy for forward)
        close = ohlcv['close']
        ret_3m = (close.iloc[-1] / close.iloc[-63] - 1) * 100 if len(close) >= 63 else 0
        ret_6m = (close.iloc[-1] / close.iloc[-126] - 1) * 100 if len(close) >= 126 else 0

        records.append({
            "symbol": symbol, "sector": sector,
            "mrs_raw": round(mrs, 4), "deliv_raw": round(deliv, 4),
            "vam_raw": round(vam, 4), "fq_raw": round(fq, 4),
            "rev_raw": round(rev, 4),
            "ret_3m": round(ret_3m, 2), "ret_6m": round(ret_6m, 2),
        })

    df = pd.DataFrame(records)
    if not df.empty:
        for col in ['mrs_raw', 'deliv_raw', 'vam_raw', 'fq_raw', 'rev_raw']:
            df[col.replace('_raw', '_rank')] = df[col].rank(pct=True)

        w = {'rs': 0.25, 'del': 0.20, 'vam': 0.20, 'for': 0.20, 'rev': 0.15}
        df['composite'] = (
            w['rs'] * df['mrs_rank'] + w['del'] * df['deliv_rank'] +
            w['vam'] * df['vam_rank'] + w['for'] * df['fq_rank'] +
            w['rev'] * df['rev_rank']
        ) * 100

    df.to_csv(OUTPUT_DIR / "2_factor_scores.csv", index=False)
    logger.info(f"  -> 2_factor_scores.csv: {len(df)} stocks scored")
    return df


def eda_2_factor_correlation(factor_df):
    """Multi-method correlation matrix between factors."""
    logger.info("EDA 2: Factor Correlation")

    if factor_df.empty:
        logger.warning("  -> No factor data for correlation analysis")
        pd.DataFrame().to_csv(OUTPUT_DIR / "2_factor_correlation.csv", index=False)
        return pd.DataFrame()

    factor_cols = ['mrs_raw', 'deliv_raw', 'vam_raw', 'fq_raw', 'rev_raw']
    records = []

    for i, f1 in enumerate(factor_cols):
        for f2 in factor_cols[i+1:]:
            vals1 = factor_df[f1].dropna()
            vals2 = factor_df[f2].dropna()
            common = vals1.index.intersection(vals2.index)
            n = len(common)

            if n < 5:
                continue

            v1, v2 = vals1.loc[common], vals2.loc[common]
            pearson = v1.corr(v2)
            spearman = v1.rank().corr(v2.rank())

            try:
                kendall, _ = scipy_stats.kendalltau(v1, v2)
            except Exception:
                kendall = 0

            violation = "NONE"
            if abs(spearman) > 0.80:
                violation = "HIGH"
            elif abs(spearman) > 0.55:
                violation = "MODERATE"

            records.append({
                "factor_1": f1.replace('_raw', ''),
                "factor_2": f2.replace('_raw', ''),
                "pearson": round(pearson, 4),
                "spearman": round(spearman, 4),
                "kendall": round(kendall, 4),
                "n_stocks": n,
                "violation": violation,
            })

    df = pd.DataFrame(records)
    df.to_csv(OUTPUT_DIR / "2_factor_correlation.csv", index=False)
    violations = (df['violation'] != 'NONE').sum() if not df.empty else 0
    logger.info(f"  -> 2_factor_correlation.csv: {len(df)} pairs, {violations} violations")
    return df


def eda_2_factor_ic_backtest(factor_df):
    """Information Coefficient: rank correlation of factors with 3M/6M returns."""
    logger.info("EDA 2: Factor IC Backtest")

    if factor_df.empty or len(factor_df) < 10:
        logger.warning("  -> Insufficient data for IC backtest")
        pd.DataFrame().to_csv(OUTPUT_DIR / "2_factor_ic_backtest.csv", index=False)
        return pd.DataFrame()

    factor_cols = ['mrs_raw', 'deliv_raw', 'vam_raw', 'fq_raw', 'rev_raw']
    records = []

    for fcol in factor_cols:
        fname = fcol.replace('_raw', '')

        # IC = Spearman rank correlation between factor score and forward return
        for ret_col, horizon in [('ret_3m', '3m'), ('ret_6m', '6m')]:
            valid = factor_df[[fcol, ret_col]].dropna()
            if len(valid) < 5:
                continue
            ic = valid[fcol].rank().corr(valid[ret_col].rank())
            records.append({
                "factor": fname,
                "horizon": horizon,
                "ic": round(ic, 4),
                "n_stocks": len(valid),
                "ic_abs": round(abs(ic), 4),
            })

    df = pd.DataFrame(records)
    df.to_csv(OUTPUT_DIR / "2_factor_ic_backtest.csv", index=False)
    logger.info(f"  -> 2_factor_ic_backtest.csv: {len(df)} IC measurements")
    return df


def eda_2_small_universe_noise(factor_df):
    """Bootstrap rank stability at different universe sizes."""
    logger.info("EDA 2: Small Universe Noise Analysis")

    if factor_df.empty or len(factor_df) < 15:
        pd.DataFrame().to_csv(OUTPUT_DIR / "2_small_universe_noise.csv", index=False)
        return pd.DataFrame()

    records = []
    n_total = len(factor_df)

    for universe_size in [15, 20, 25, 30, min(40, n_total)]:
        if universe_size > n_total:
            continue

        rank_stabilities = []
        n_bootstrap = 100

        for _ in range(n_bootstrap):
            sample = factor_df.sample(universe_size, replace=False)
            ranks = sample['mrs_raw'].rank(pct=True)
            rank_stabilities.append(ranks.std())

        records.append({
            "universe_size": universe_size,
            "rank_std_mean": round(np.mean(rank_stabilities), 4),
            "rank_std_p95": round(np.percentile(rank_stabilities, 95), 4),
            "recommendation": "z-score" if universe_size < 25 else "percentile_rank",
        })

    df = pd.DataFrame(records)
    df.to_csv(OUTPUT_DIR / "2_small_universe_noise.csv", index=False)
    logger.info(f"  -> 2_small_universe_noise.csv: {len(df)} universe sizes tested")
    return df


# ===================================================================
# STAGE 3 EDA — Regime, Bearish Model, Exit Analysis
# ===================================================================

def eda_3_regime_sensitivity(regime_data):
    """Regime threshold sensitivity analysis."""
    logger.info("EDA 3: Regime Sensitivity")
    nifty_df = regime_data.get('nifty_df', pd.DataFrame())
    vix_df = regime_data.get('vix_df', pd.DataFrame())
    usdinr_df = regime_data.get('usdinr_df', pd.DataFrame())
    fii_df = regime_data.get('fii_df', pd.DataFrame())

    scalar, name, weights = get_regime(**regime_data)

    nifty_close = float(nifty_df['close'].iloc[-1]) if not nifty_df.empty else 0
    nifty_200dma = float(nifty_df['close'].rolling(200).mean().iloc[-1]) if len(nifty_df) >= 200 else 0
    dma_pct = ((nifty_close / nifty_200dma) - 1) * 100 if nifty_200dma > 0 else 0
    vix = float(vix_df['close'].iloc[-1]) if not vix_df.empty else 18
    usdinr = float(usdinr_df['close'].iloc[-1]) if not usdinr_df.empty else 83

    # Check what would happen with slightly different VIX
    records = [
        {"metric": "nifty_close", "value": round(nifty_close, 2)},
        {"metric": "nifty_200dma", "value": round(nifty_200dma, 2)},
        {"metric": "dma_pct", "value": round(dma_pct, 2)},
        {"metric": "vix", "value": round(vix, 2)},
        {"metric": "usdinr", "value": round(usdinr, 2)},
        {"metric": "regime", "value": name},
        {"metric": "scalar", "value": scalar},
        {"metric": "weights", "value": str(weights)},
        {"metric": "would_flip_if_above_200dma", "value": "YES" if nifty_close < nifty_200dma else "NO"},
        {"metric": "distance_to_flip", "value": round(nifty_200dma - nifty_close, 2) if nifty_close < nifty_200dma else 0},
    ]

    df = pd.DataFrame(records)
    df.to_csv(OUTPUT_DIR / "3_regime_sensitivity.csv", index=False)
    logger.info(f"  -> 3_regime_sensitivity.csv: Regime={name}, Scalar={scalar}")
    return df


def eda_3_bearish_model_audit(ohlcv_data, fundamentals, sector_map, nifty_df):
    """Why bearish model produces/misses candidates."""
    logger.info("EDA 3: Bearish Model Audit")

    # Run bearish_candidates and capture all details
    records = []
    for symbol in SAMPLE_SYMBOLS:
        ohlcv = ohlcv_data.get(symbol)
        raw = fundamentals.get(symbol)
        if not raw or ohlcv is None or ohlcv.empty:
            continue

        f = _to_forensic_format(raw)
        m_score = beneish_m_score(f)
        ccr = cash_conversion_ratio(f)
        sector = sector_map.get(symbol, 'Unknown')

        # Mansfield RS
        mrs = 0.0
        if nifty_df is not None and not nifty_df.empty and not ohlcv.empty:
            try:
                stock_close = ohlcv['close']
                bench_close = nifty_df['close']
                common = stock_close.index.intersection(bench_close.index)
                if len(common) >= 91:
                    rp = stock_close.loc[common] / bench_close.loc[common]
                    rp_ma = rp.rolling(91).mean()
                    mrs = float(((rp / rp_ma) - 1) * 100).real
                    if pd.isna(mrs):
                        mrs = 0.0
            except Exception:
                mrs = 0.0

        # LVGI
        debt_t = f.get('debt_t', 0) or 0
        debt_t1 = f.get('debt_t1', 0) or 0
        ta = f.get('total_assets', 0) or 1e-9
        lvgi = (debt_t / ta) / (debt_t1 / ta + 1e-9) if debt_t1 > 0 else 1.0

        rev_proxy = earnings_revision_proxy(ohlcv)
        close = ohlcv['close']
        ret_3m = (close.iloc[-1] / close.iloc[-63] - 1) * 100 if len(close) >= 63 else 0
        ret_6m = (close.iloc[-1] / close.iloc[-126] - 1) * 100 if len(close) >= 126 else 0

        # Check gates
        passes_mscore = m_score > -1.5
        passes_mscore_relaxed = m_score > -2.0
        passes_rs = mrs < 0

        # Below 200 DMA check
        if len(close) >= 200:
            dma_200 = close.rolling(200).mean().iloc[-1]
            below_200dma = close.iloc[-1] < dma_200
        else:
            below_200dma = False

        records.append({
            "symbol": symbol, "sector": sector,
            "m_score": round(m_score, 4),
            "ccr": round(ccr, 4),
            "mrs": round(mrs, 2),
            "lvgi": round(lvgi, 4),
            "rev_proxy": round(rev_proxy, 4),
            "ret_3m": round(ret_3m, 2),
            "ret_6m": round(ret_6m, 2),
            "below_200dma": below_200dma,
            "passes_mscore_m1_5": passes_mscore,
            "passes_mscore_m2_0": passes_mscore_relaxed,
            "passes_rs_gate": passes_rs,
            "would_qualify_current": passes_mscore and passes_rs,
            "would_qualify_relaxed": passes_mscore_relaxed and (passes_rs or below_200dma),
        })

    df = pd.DataFrame(records)
    df.to_csv(OUTPUT_DIR / "3_bearish_model_audit.csv", index=False)

    current_q = df['would_qualify_current'].sum() if not df.empty else 0
    relaxed_q = df['would_qualify_relaxed'].sum() if not df.empty else 0
    logger.info(
        f"  -> 3_bearish_model_audit.csv: {len(df)} symbols | "
        f"Current gate: {current_q} qualify | Relaxed gate: {relaxed_q} qualify"
    )
    return df


def eda_3_bullish_model_audit(ohlcv_data, fundamentals, sector_map, nifty_df):
    """BEAR-regime bullish candidate quality audit."""
    logger.info("EDA 3: Bullish Model Audit (BEAR regime)")

    records = []
    for symbol in SAMPLE_SYMBOLS:
        ohlcv = ohlcv_data.get(symbol)
        raw = fundamentals.get(symbol)
        if not raw or ohlcv is None or ohlcv.empty or len(ohlcv) < 63:
            continue

        f = _to_forensic_format(raw)
        m_score = beneish_m_score(f)
        ccr = cash_conversion_ratio(f)
        de = raw.get('debt_equity', 0) or 0
        sector = sector_map.get(symbol, 'Unknown')

        close = ohlcv['close']
        ret_3m = (close.iloc[-1] / close.iloc[-63] - 1) * 100 if len(close) >= 63 else 0
        ret_6m = (close.iloc[-1] / close.iloc[-126] - 1) * 100 if len(close) >= 126 else 0

        mrs = mansfield_rs(ohlcv, nifty_df)

        # Current bullish gates
        passes_mscore = m_score < -2.22
        passes_ccr = ccr >= 0.70
        passes_de = de < 1.5
        passes_momentum = ret_3m > 0 or ret_6m > 0

        records.append({
            "symbol": symbol, "sector": sector,
            "m_score": round(m_score, 4),
            "ccr": round(ccr, 4),
            "de": round(de, 4),
            "ret_3m": round(ret_3m, 2),
            "ret_6m": round(ret_6m, 2),
            "mrs": round(mrs, 2),
            "passes_mscore": passes_mscore,
            "passes_ccr": passes_ccr,
            "passes_de": passes_de,
            "passes_momentum": passes_momentum,
            "would_qualify": passes_mscore and passes_ccr and passes_de and passes_momentum,
        })

    df = pd.DataFrame(records)
    df.to_csv(OUTPUT_DIR / "3_bullish_model_audit.csv", index=False)

    qualified = df['would_qualify'].sum() if not df.empty else 0
    logger.info(
        f"  -> 3_bullish_model_audit.csv: {len(df)} symbols | {qualified} would qualify as bullish"
    )
    return df


# ===================================================================
# Summary
# ===================================================================

def eda_summary(forensic_df, liquidity_df, earnings_df, factor_df, bearish_df, bullish_df, regime_df):
    """Aggregate metrics across all stages."""
    logger.info("Summary: Aggregate Metrics")

    total = len(SAMPLE_SYMBOLS)
    records = [
        {"stage": "Universe", "metric": "total_symbols", "value": total},
        {"stage": "1A", "metric": "forensic_pass", "value": int(forensic_df['forensic_pass'].sum()) if not forensic_df.empty else 0},
        {"stage": "1A", "metric": "m_score_pass_rate", "value": f"{(forensic_df['m_score'].apply(lambda x: x <= M_SCORE_THRESHOLD).mean()*100):.1f}%" if not forensic_df.empty else "N/A"},
        {"stage": "1A", "metric": "ccr_pass_rate_0.80", "value": f"{(forensic_df['ccr_1yr'].apply(lambda x: x >= 0.80 or x == -1.0).mean()*100):.1f}%" if not forensic_df.empty else "N/A"},
        {"stage": "1B", "metric": "liquidity_pass", "value": int(liquidity_df['overall_1b_pass'].sum()) if not liquidity_df.empty else 0},
        {"stage": "1C", "metric": "earnings_pass_current", "value": int(earnings_df['earnings_pass_current'].sum()) if not earnings_df.empty else 0},
        {"stage": "1C", "metric": "earnings_pass_lenient", "value": int(earnings_df['pass_if_data_lenient'].sum()) if not earnings_df.empty else 0},
        {"stage": "2", "metric": "factor_scored", "value": len(factor_df) if not factor_df.empty else 0},
        {"stage": "3", "metric": "regime", "value": regime_df[regime_df['metric'] == 'regime']['value'].iloc[0] if not regime_df.empty else "UNKNOWN"},
        {"stage": "3", "metric": "bearish_qualify_current", "value": int(bearish_df['would_qualify_current'].sum()) if not bearish_df.empty else 0},
        {"stage": "3", "metric": "bearish_qualify_relaxed", "value": int(bearish_df['would_qualify_relaxed'].sum()) if not bearish_df.empty else 0},
        {"stage": "3", "metric": "bullish_qualify", "value": int(bullish_df['would_qualify'].sum()) if not bullish_df.empty else 0},
        {"stage": "PIPELINE", "metric": "funnel", "value": f"[{total}, {int(forensic_df['forensic_pass'].sum()) if not forensic_df.empty else 0}, {int(liquidity_df['overall_1b_pass'].sum()) if not liquidity_df.empty else 0}, {int(earnings_df['earnings_pass_current'].sum()) if not earnings_df.empty else 0}]"},
        {"stage": "TARGET", "metric": "bullish_target", "value": "5-10"},
        {"stage": "TARGET", "metric": "bearish_target", "value": "5-10"},
    ]

    df = pd.DataFrame(records)
    df.to_csv(OUTPUT_DIR / "summary_v02.csv", index=False)
    logger.info(f"  -> summary_v02.csv: {len(df)} metrics")
    return df


# ===================================================================
# Main
# ===================================================================

def main():
    logger.info("=" * 70)
    logger.info("EDA v0.2 — Full Pipeline Diagnostics for 3-6M Optimization")
    logger.info(f"Date: {date.today().isoformat()}")
    logger.info(f"Output: {OUTPUT_DIR}")
    logger.info("=" * 70)

    # Generate data
    logger.info("Generating sample data...")
    ohlcv_data = generate_sample_ohlcv(SAMPLE_SYMBOLS)
    fundamentals = generate_sample_fundamentals(SAMPLE_SYMBOLS)
    pledge_data = generate_sample_pledge_data(SAMPLE_SYMBOLS)
    index_data = generate_sample_index_data(ohlcv_data)
    fii_df = generate_sample_fii_data()
    bhavcopy_df = generate_sample_bhavcopy(ohlcv_data)
    sector_map = get_sample_sector_map(SAMPLE_SYMBOLS)

    nifty_df = index_data.get('nifty_df', pd.DataFrame())
    regime_data = {
        'nifty_df': nifty_df,
        'vix_df': index_data.get('vix_df', pd.DataFrame()),
        'usdinr_df': index_data.get('usdinr_df', pd.DataFrame()),
        'fii_df': fii_df,
    }
    logger.info(f"  OHLCV: {len(ohlcv_data)} | Fundamentals: {len(fundamentals)} | Sectors: {len(set(sector_map.values()))}")
    logger.info("")

    # Stage 1A
    forensic_df = eda_1a_forensic_deep_dive(fundamentals, pledge_data, sector_map)
    logger.info("")
    ccr_bench_df = eda_1a_ccr_sector_benchmarks(forensic_df)
    logger.info("")

    # Stage 1B
    liquidity_df = eda_1b_liquidity_analysis(ohlcv_data, fundamentals, sector_map)
    logger.info("")
    de_dist_df = eda_1b_de_sector_distribution(liquidity_df)
    logger.info("")

    # Stage 1C (CRITICAL)
    earnings_df = eda_1c_earnings_gate_analysis(fundamentals, sector_map)
    logger.info("")
    dq_df = eda_1c_data_quality_audit(fundamentals)
    logger.info("")

    # Stage 2
    factor_df = eda_2_factor_scores(ohlcv_data, bhavcopy_df, fundamentals, sector_map, nifty_df)
    logger.info("")
    corr_df = eda_2_factor_correlation(factor_df)
    logger.info("")
    ic_df = eda_2_factor_ic_backtest(factor_df)
    logger.info("")
    noise_df = eda_2_small_universe_noise(factor_df)
    logger.info("")

    # Stage 3
    regime_df = eda_3_regime_sensitivity(regime_data)
    logger.info("")
    bearish_df = eda_3_bearish_model_audit(ohlcv_data, fundamentals, sector_map, nifty_df)
    logger.info("")
    bullish_df = eda_3_bullish_model_audit(ohlcv_data, fundamentals, sector_map, nifty_df)
    logger.info("")

    # Summary
    summary_df = eda_summary(forensic_df, liquidity_df, earnings_df, factor_df, bearish_df, bullish_df, regime_df)
    logger.info("")

    # Final report
    logger.info("=" * 70)
    logger.info("EDA v0.2 complete. Output files:")
    for csv_file in sorted(OUTPUT_DIR.glob("*.csv")):
        size_kb = csv_file.stat().st_size / 1024
        logger.info(f"  {csv_file.name:45s} ({size_kb:.1f} KB)")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()

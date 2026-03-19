"""
EDA v0.1 — Exploratory Data Analysis for QuantSystem_v1

Standalone diagnostic script that audits data availability, forensic gate
behavior, pipeline funnel drop-off, CCR/M-Score distributions, regime
detection, and produces summary statistics.

Each task writes a CSV to src/eda/v0.1/output/ for inspection.

Run:
    python -m src.eda.v0.1.run_eda
"""

import sys
import os
import logging
from pathlib import Path
from datetime import date

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path so `src.*` imports work when invoked
# as `python -m src.eda.v0.1.run_eda` from the project root.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[3]  # src/eda/v0.1 -> project root
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Imports from the existing codebase
# ---------------------------------------------------------------------------
from src.data.sample_data import (
    generate_sample_fundamentals,
    generate_sample_ohlcv,
    generate_sample_index_data,
    generate_sample_bhavcopy,
    generate_sample_fii_data,
    generate_sample_pledge_data,
    get_sample_sector_map,
    SAMPLE_SYMBOLS,
)
from src.analysis.forensic import (
    beneish_m_score,
    cash_conversion_ratio,
    forensic_pass,
    CCR_THRESHOLD,
    M_SCORE_THRESHOLD,
    PLEDGE_MAX_PCT,
    PLEDGE_MAX_DELTA,
)
from src.analysis.regime import get_regime, REGIME_SCALARS, REGIME_WEIGHTS

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("eda.v0.1")

# ---------------------------------------------------------------------------
# Output directory
# ---------------------------------------------------------------------------
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ===================================================================
# Helpers
# ===================================================================

def _to_forensic_format(sym_fund: dict) -> dict:
    """
    Convert the dict produced by generate_sample_fundamentals() into the
    key-set that beneish_m_score() / forensic_pass() expect.

    generate_sample_fundamentals keys:
        sales_t, sales_t1, net_income, ebitda, cfo, total_assets,
        total_debt, total_equity, receivables, debt_equity,
        pledge_pct, pledge_delta

    beneish_m_score expects (from src/data/fundamentals.get_fundamentals):
        receivables_t, receivables_t1, current_assets_t, current_assets_t1,
        ppe_t, ppe_t1, debt_t, debt_t1, total_assets, sales_t, sales_t1,
        net_income, cfo, ebitda, debt_equity

    Fields not present in sample data (receivables_t1, current_assets_*,
    ppe_*, debt_t1) are set to zero, which is the same default the
    beneish_m_score function uses via `f.get(key, 0) or 0`.
    """
    return {
        "sales_t": sym_fund.get("sales_t", 0),
        "sales_t1": sym_fund.get("sales_t1", 0),
        "net_income": sym_fund.get("net_income", 0),
        "ebitda": sym_fund.get("ebitda", 0),
        "cfo": sym_fund.get("cfo", 0),
        "total_assets": sym_fund.get("total_assets", 0),
        "receivables_t": sym_fund.get("receivables", 0),
        "receivables_t1": 0,  # not available in sample data
        "current_assets_t": 0,
        "current_assets_t1": 0,
        "ppe_t": 0,
        "ppe_t1": 0,
        "debt_t": sym_fund.get("total_debt", 0),
        "debt_t1": 0,  # not available in sample data
        "total_equity": sym_fund.get("total_equity", 0),
        "debt_equity": sym_fund.get("debt_equity", 0),
    }


def _build_pledge_for_forensic(sym_pledge: dict) -> dict:
    """
    Convert the dict from generate_sample_pledge_data() into the format
    that forensic_pass() expects.

    sample_pledge keys: pledge_pct, pledge_delta
    forensic_pass expects: data_available, pledge_pct, pledge_delta_1q
    """
    return {
        "data_available": True,
        "pledge_pct": sym_pledge.get("pledge_pct", 0),
        "pledge_delta_1q": sym_pledge.get("pledge_delta", 0),
    }


def _compute_m_score_components(f: dict) -> dict:
    """
    Break down the Beneish M-Score into its individual variable
    contributions (matching the formula in forensic.beneish_m_score).

    Returns a dict with dsri, aqi, tata, lvgi, sgi raw values and
    their weighted contributions.
    """
    eps = 1e-9

    recv_t = f.get("receivables_t", 0) or 0
    recv_t1 = f.get("receivables_t1", 0) or 0
    sales_t = f.get("sales_t", 0) or eps
    sales_t1 = f.get("sales_t1", 0) or eps
    dsri = (recv_t / sales_t) / (recv_t1 / sales_t1 + eps)

    ca_t = f.get("current_assets_t", 0) or 0
    ppe_t = f.get("ppe_t", 0) or 0
    ta_t = f.get("total_assets", 0) or eps
    ca_t1 = f.get("current_assets_t1", 0) or 0
    ppe_t1 = f.get("ppe_t1", 0) or 0
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

    # Weighted contributions (coefficients from Beneish formula)
    return {
        "dsri": round(dsri, 4),
        "aqi": round(aqi, 4),
        "tata": round(tata, 4),
        "lvgi": round(lvgi, 4),
        "sgi": round(sgi, 4),
        "dsri_contrib": round(0.920 * dsri, 4),
        "aqi_contrib": round(0.115 * aqi, 4),
        "tata_contrib": round(0.528 * tata, 4),
        "lvgi_contrib": round(0.404 * lvgi, 4),
        "sgi_contrib": round(0.892 * sgi, 4),
    }


def _identify_missing_fields(raw_fund: dict) -> list[str]:
    """Identify which fields from the fundamentals dict are missing or zero."""
    # Fields that beneish_m_score relies on (in forensic format)
    important_fields = [
        "receivables_t", "receivables_t1",
        "current_assets_t", "current_assets_t1",
        "ppe_t", "ppe_t1",
        "debt_t", "debt_t1",
        "total_assets", "sales_t", "sales_t1",
        "net_income", "cfo", "ebitda",
    ]
    missing = []
    for field in important_fields:
        val = raw_fund.get(field, None)
        if val is None or val == 0:
            missing.append(field)
    return missing


# ===================================================================
# Task 1.2 — Fundamentals Availability Audit
# ===================================================================

def task_1_2_fundamentals_availability(fundamentals_raw: dict) -> pd.DataFrame:
    """
    For each symbol, record which fundamental fields are present/missing/zero.
    Writes: 01_fundamentals_availability.csv
    """
    logger.info("Task 1.2: Fundamentals Availability Audit")

    critical_fields = [
        "sales_t", "sales_t1", "net_income", "ebitda", "cfo",
        "total_assets", "receivables", "total_debt", "total_equity", "debt_equity",
    ]

    records = []
    for symbol in SAMPLE_SYMBOLS:
        fund = fundamentals_raw.get(symbol, {})
        row = {"symbol": symbol}

        all_present = True
        for field in critical_fields:
            val = fund.get(field, None)
            if val is None:
                row[field] = "MISSING"
                all_present = False
            elif val == 0:
                row[field] = "ZERO"
                all_present = False
            else:
                row[field] = round(val, 2)

        # Identify data source (yfinance real vs synthetic)
        # Sample data may come from yfinance (first 30 symbols) or synthetic
        row["data_source"] = "sample_generator"
        row["all_critical_present"] = all_present
        records.append(row)

    df = pd.DataFrame(records)
    out_path = OUTPUT_DIR / "01_fundamentals_availability.csv"
    df.to_csv(out_path, index=False)
    logger.info(
        f"  -> {out_path.name}: {len(df)} symbols, "
        f"{df['all_critical_present'].sum()} with all critical fields"
    )
    return df


# ===================================================================
# Task 1.3 — Forensic Gate Decomposition
# ===================================================================

def task_1_3_forensic_decomposition(
    fundamentals_raw: dict, pledge_data: dict
) -> pd.DataFrame:
    """
    For each symbol with available fundamentals, compute M-Score components,
    CCR value, and pass/fail for each gate.
    Writes: 02_forensic_decomposition.csv
    """
    logger.info("Task 1.3: Forensic Gate Decomposition")

    records = []
    for symbol in SAMPLE_SYMBOLS:
        raw = fundamentals_raw.get(symbol)
        if raw is None:
            continue

        f = _to_forensic_format(raw)
        m_score = beneish_m_score(f)
        ccr = cash_conversion_ratio(f)
        components = _compute_m_score_components(f)

        # Pledge checks
        pledge_raw = pledge_data.get(symbol, {})
        pledge_pct = pledge_raw.get("pledge_pct", 0)
        pledge_delta = pledge_raw.get("pledge_delta", 0)
        pledge_pass_val = pledge_pct <= PLEDGE_MAX_PCT and pledge_delta <= PLEDGE_MAX_DELTA

        # Individual gate results
        m_score_pass = m_score <= M_SCORE_THRESHOLD
        ccr_pass = ccr >= CCR_THRESHOLD

        # Build forensic-format pledge for forensic_pass()
        pledge_forensic = _build_pledge_for_forensic(pledge_raw)
        overall = forensic_pass(f, pledge_forensic)

        # Determine failure reason(s)
        reasons = []
        if not m_score_pass:
            reasons.append(f"M-Score={m_score:.2f}>{M_SCORE_THRESHOLD}")
        if not ccr_pass:
            reasons.append(f"CCR={ccr:.2f}<{CCR_THRESHOLD}")
        if not pledge_pass_val:
            if pledge_pct > PLEDGE_MAX_PCT:
                reasons.append(f"Pledge={pledge_pct:.1f}%>{PLEDGE_MAX_PCT}%")
            if pledge_delta > PLEDGE_MAX_DELTA:
                reasons.append(f"PledgeDelta={pledge_delta:.1f}pp>{PLEDGE_MAX_DELTA}pp")

        records.append({
            "symbol": symbol,
            "m_score": round(m_score, 4),
            "dsri": components["dsri"],
            "aqi": components["aqi"],
            "tata": components["tata"],
            "lvgi": components["lvgi"],
            "sgi": components["sgi"],
            "m_score_pass": m_score_pass,
            "ccr": round(ccr, 4),
            "ccr_pass": ccr_pass,
            "pledge_pct": round(pledge_pct, 2),
            "pledge_pass": pledge_pass_val,
            "overall_forensic_pass": overall,
            "failure_reason": "; ".join(reasons) if reasons else "PASS",
        })

    df = pd.DataFrame(records)
    out_path = OUTPUT_DIR / "02_forensic_decomposition.csv"
    df.to_csv(out_path, index=False)

    pass_count = df["overall_forensic_pass"].sum()
    logger.info(
        f"  -> {out_path.name}: {len(df)} symbols analysed, "
        f"{pass_count} pass all forensic gates ({pass_count/len(df)*100:.0f}%)"
    )
    return df


# ===================================================================
# Task 1.4 — Pipeline Funnel Analysis
# ===================================================================

def task_1_4_pipeline_funnel(
    ohlcv_data: dict,
    fundamentals_raw: dict,
    pledge_data: dict,
    regime_data: dict,
) -> pd.DataFrame:
    """
    Run the pipeline logic step-by-step for each symbol and track
    where each one drops out.
    Writes: 03_pipeline_funnel.csv
    """
    logger.info("Task 1.4: Pipeline Funnel Analysis")

    # Stage 1B thresholds (same as pipeline.py)
    MIN_ADT = 1e7
    MAX_DEBT_EQUITY = 1.5

    records = []
    for symbol in SAMPLE_SYMBOLS:
        row = {"symbol": symbol}

        # --- OHLCV availability ---
        ohlcv = ohlcv_data.get(symbol)
        row["has_ohlcv"] = ohlcv is not None and not ohlcv.empty
        row["ohlcv_len"] = len(ohlcv) if row["has_ohlcv"] else 0

        # --- Fundamentals availability ---
        raw = fundamentals_raw.get(symbol)
        row["has_fundamentals"] = raw is not None

        # --- Stage 1A: Forensic Gate ---
        row["stage_1a_pass"] = False
        row["stage_1a_fail_reason"] = ""

        if not row["has_ohlcv"] or row["ohlcv_len"] < 100:
            row["stage_1a_fail_reason"] = "insufficient_ohlcv"
            row["stage_1b_pass"] = False
            row["stage_1b_fail_reason"] = "skipped_no_1a"
            records.append(row)
            continue

        if raw is None:
            row["stage_1a_fail_reason"] = "no_fundamentals"
            row["stage_1b_pass"] = False
            row["stage_1b_fail_reason"] = "skipped_no_1a"
            records.append(row)
            continue

        f = _to_forensic_format(raw)
        pledge_raw = pledge_data.get(symbol, {})
        pledge_forensic = _build_pledge_for_forensic(pledge_raw)

        m_score = beneish_m_score(f)
        ccr = cash_conversion_ratio(f)
        m_ok = m_score <= M_SCORE_THRESHOLD
        ccr_ok = ccr >= CCR_THRESHOLD
        pledge_ok = (
            pledge_raw.get("pledge_pct", 0) <= PLEDGE_MAX_PCT
            and pledge_raw.get("pledge_delta", 0) <= PLEDGE_MAX_DELTA
        )

        if m_ok and ccr_ok and pledge_ok:
            row["stage_1a_pass"] = True
        else:
            reasons = []
            if not m_ok:
                reasons.append(f"m_score={m_score:.2f}")
            if not ccr_ok:
                reasons.append(f"ccr={ccr:.2f}")
            if not pledge_ok:
                reasons.append("pledge")
            row["stage_1a_fail_reason"] = "; ".join(reasons)

        # --- Stage 1B: Liquidity & Clean Books ---
        row["stage_1b_pass"] = False
        row["stage_1b_fail_reason"] = ""

        if not row["stage_1a_pass"]:
            row["stage_1b_fail_reason"] = "skipped_1a_fail"
            records.append(row)
            continue

        avg_close_20d = ohlcv["close"].tail(20).mean()
        avg_vol_20d = ohlcv["volume"].tail(20).mean()
        adt = avg_close_20d * avg_vol_20d

        if adt < MIN_ADT:
            row["stage_1b_fail_reason"] = f"adt={adt:.0f}<{MIN_ADT:.0f}"
            records.append(row)
            continue

        # Worst-5-day stress check
        if len(ohlcv) >= 20:
            daily_turnover = ohlcv["close"].tail(20) * ohlcv["volume"].tail(20)
            worst_5d_adt = daily_turnover.nsmallest(5).mean()
            if worst_5d_adt < MIN_ADT * 0.5:
                row["stage_1b_fail_reason"] = f"worst5d_adt={worst_5d_adt:.0f}"
                records.append(row)
                continue

        if f.get("debt_equity", 0) > MAX_DEBT_EQUITY:
            row["stage_1b_fail_reason"] = f"de={f['debt_equity']:.2f}>{MAX_DEBT_EQUITY}"
            records.append(row)
            continue

        row["stage_1b_pass"] = True
        records.append(row)

    df = pd.DataFrame(records)
    out_path = OUTPUT_DIR / "03_pipeline_funnel.csv"
    df.to_csv(out_path, index=False)

    total = len(df)
    s1a = df["stage_1a_pass"].sum()
    s1b = df["stage_1b_pass"].sum()
    logger.info(
        f"  -> {out_path.name}: {total} symbols | "
        f"1A pass: {s1a} ({s1a/total*100:.0f}%) | "
        f"1B pass: {s1b} ({s1b/total*100:.0f}%)"
    )
    return df


# ===================================================================
# Task 1.5 — CCR Distribution Analysis
# ===================================================================

def task_1_5_ccr_distribution(fundamentals_raw: dict) -> pd.DataFrame:
    """
    CCR distribution across all stocks with multiple threshold checks.
    Writes: 04_ccr_distribution.csv
    """
    logger.info("Task 1.5: CCR Distribution Analysis")

    records = []
    for symbol in SAMPLE_SYMBOLS:
        raw = fundamentals_raw.get(symbol)
        if raw is None:
            continue

        f = _to_forensic_format(raw)
        cfo_val = f.get("cfo", 0) or 0
        ebitda_val = f.get("ebitda", 0) or 0
        ccr = cash_conversion_ratio(f)

        records.append({
            "symbol": symbol,
            "cfo": round(cfo_val, 2),
            "ebitda": round(ebitda_val, 2),
            "ccr": round(ccr, 4),
            "would_pass_0.80": ccr >= 0.80,
            "would_pass_0.60": ccr >= 0.60,
            "would_pass_0.50": ccr >= 0.50,
            "data_source": "sample_generator",
        })

    df = pd.DataFrame(records)
    out_path = OUTPUT_DIR / "04_ccr_distribution.csv"
    df.to_csv(out_path, index=False)

    n = len(df)
    p80 = df["would_pass_0.80"].sum()
    p60 = df["would_pass_0.60"].sum()
    p50 = df["would_pass_0.50"].sum()
    logger.info(
        f"  -> {out_path.name}: {n} symbols | "
        f"Pass@0.80: {p80} ({p80/n*100:.0f}%) | "
        f"Pass@0.60: {p60} ({p60/n*100:.0f}%) | "
        f"Pass@0.50: {p50} ({p50/n*100:.0f}%)"
    )
    return df


# ===================================================================
# Task 1.6 — M-Score Sensitivity Analysis
# ===================================================================

def task_1_6_mscore_sensitivity(fundamentals_raw: dict) -> pd.DataFrame:
    """
    For stocks that fail M-Score, identify which components are inflated
    due to missing data and what the score would be with neutral defaults.
    Writes: 05_mscore_sensitivity.csv
    """
    logger.info("Task 1.6: M-Score Sensitivity Analysis")

    records = []
    for symbol in SAMPLE_SYMBOLS:
        raw = fundamentals_raw.get(symbol)
        if raw is None:
            continue

        f = _to_forensic_format(raw)
        m_score = beneish_m_score(f)

        # Only analyse symbols that fail the M-Score gate
        if m_score <= M_SCORE_THRESHOLD:
            continue

        components = _compute_m_score_components(f)
        missing = _identify_missing_fields(f)

        # Recompute M-Score with neutral defaults for missing fields:
        # DSRI=1, AQI=1, TATA=0, LVGI=1, SGI=1 are "no manipulation" baselines.
        f_fixed = dict(f)

        # If receivables_t1 is missing, DSRI becomes inflated; set t1 = t
        if "receivables_t1" in missing:
            f_fixed["receivables_t1"] = f_fixed.get("receivables_t", 0)

        # If current_assets / ppe are missing, AQI can blow up
        if "current_assets_t" in missing or "ppe_t" in missing:
            # Set AQI to neutral by making ca + ppe = some fraction of TA
            ta = f_fixed.get("total_assets", 1)
            f_fixed["current_assets_t"] = ta * 0.4
            f_fixed["ppe_t"] = ta * 0.3
        if "current_assets_t1" in missing or "ppe_t1" in missing:
            ta = f_fixed.get("total_assets", 1)
            f_fixed["current_assets_t1"] = ta * 0.4
            f_fixed["ppe_t1"] = ta * 0.3

        # If debt_t1 is missing, LVGI is inflated
        if "debt_t1" in missing:
            f_fixed["debt_t1"] = f_fixed.get("debt_t", 0)

        m_score_fixed = beneish_m_score(f_fixed)

        records.append({
            "symbol": symbol,
            "m_score": round(m_score, 4),
            "dsri_contrib": components["dsri_contrib"],
            "aqi_contrib": components["aqi_contrib"],
            "tata_contrib": components["tata_contrib"],
            "lvgi_contrib": components["lvgi_contrib"],
            "sgi_contrib": components["sgi_contrib"],
            "missing_fields": ", ".join(missing) if missing else "none",
            "m_score_if_defaults_fixed": round(m_score_fixed, 4),
        })

    df = pd.DataFrame(records)
    out_path = OUTPUT_DIR / "05_mscore_sensitivity.csv"
    df.to_csv(out_path, index=False)

    n = len(df)
    rescued = (df["m_score_if_defaults_fixed"] <= M_SCORE_THRESHOLD).sum() if n > 0 else 0
    logger.info(
        f"  -> {out_path.name}: {n} symbols fail M-Score | "
        f"{rescued} could be rescued by fixing missing-data defaults"
    )
    return df


# ===================================================================
# Task 1.7 — Regime Detection Audit
# ===================================================================

def task_1_7_regime_audit(regime_data: dict) -> pd.DataFrame:
    """
    Check what regime is detected and why, by inspecting the individual
    metrics and thresholds that drive the regime decision.
    Writes: 06_regime_audit.csv
    """
    logger.info("Task 1.7: Regime Detection Audit")

    nifty_df = regime_data.get("nifty_df", pd.DataFrame())
    vix_df = regime_data.get("vix_df", pd.DataFrame())
    usdinr_df = regime_data.get("usdinr_df", pd.DataFrame())
    fii_df = regime_data.get("fii_df", pd.DataFrame())

    # Run regime detection
    regime_scalar, regime_name, factor_weights = get_regime(
        nifty_df=nifty_df,
        vix_df=vix_df,
        usdinr_df=usdinr_df,
        fii_df=fii_df,
    )

    # Extract the individual metrics used in the decision
    def _safe_last(df, col, default):
        if df is None or df.empty or col not in df.columns:
            return default
        val = df[col].iloc[-1]
        return float(val) if pd.notna(val) else default

    def _safe_rolling_mean(df, col, window):
        if df is None or df.empty or col not in df.columns or len(df) < window:
            return 0.0
        val = df[col].rolling(window).mean().iloc[-1]
        return float(val) if pd.notna(val) else 0.0

    nifty_close = _safe_last(nifty_df, "close", 0)
    nifty_ma200 = _safe_rolling_mean(nifty_df, "close", 200)
    vix = _safe_last(vix_df, "close", 18)
    usdinr_current = _safe_last(usdinr_df, "close", 83)

    # 30-day ago USD/INR
    usdinr_30d_ago = 83.0
    if usdinr_df is not None and not usdinr_df.empty and "close" in usdinr_df.columns and len(usdinr_df) >= 30:
        val = usdinr_df["close"].iloc[-30]
        usdinr_30d_ago = float(val) if pd.notna(val) else 83.0

    inr_move_30d = ((usdinr_current / usdinr_30d_ago) - 1) * 100 if usdinr_30d_ago > 0 else 0
    dma_distance = ((nifty_close - nifty_ma200) / nifty_ma200) * 100 if nifty_ma200 > 0 else 0

    records = [
        {"metric": "nifty_close", "value": round(nifty_close, 2), "threshold": "-", "regime_signal": "input"},
        {"metric": "nifty_200dma", "value": round(nifty_ma200, 2), "threshold": "-", "regime_signal": "input"},
        {"metric": "nifty_dma_distance_pct", "value": round(dma_distance, 2), "threshold": "< 0 -> BEAR, abs < 3 -> DIP", "regime_signal": "BEAR" if nifty_close < nifty_ma200 else ("DIP" if abs(dma_distance) < 3 else "OK")},
        {"metric": "india_vix", "value": round(vix, 2), "threshold": "> 24 -> BEAR, 16-24 -> SIDEWAYS, < 16 -> BULL", "regime_signal": "BEAR" if vix > 24 else ("SIDEWAYS" if vix > 16 else "BULL")},
        {"metric": "usdinr_current", "value": round(usdinr_current, 2), "threshold": "-", "regime_signal": "input"},
        {"metric": "usdinr_30d_move_pct", "value": round(inr_move_30d, 2), "threshold": "> 2% -> BEAR", "regime_signal": "BEAR" if inr_move_30d > 2.0 else "OK"},
        {"metric": "detected_regime", "value": regime_name, "threshold": "-", "regime_signal": regime_name},
        {"metric": "regime_scalar", "value": regime_scalar, "threshold": f"BULL=1.0 DIP=0.6 SW=0.3 BEAR=0.0", "regime_signal": str(regime_scalar)},
        {"metric": "factor_weights", "value": str(factor_weights), "threshold": "-", "regime_signal": regime_name},
    ]

    df = pd.DataFrame(records)
    out_path = OUTPUT_DIR / "06_regime_audit.csv"
    df.to_csv(out_path, index=False)

    logger.info(
        f"  -> {out_path.name}: Regime={regime_name}, Scalar={regime_scalar}"
    )
    return df


# ===================================================================
# Task 1.8 — Summary Statistics
# ===================================================================

def task_1_8_summary(
    avail_df: pd.DataFrame,
    forensic_df: pd.DataFrame,
    funnel_df: pd.DataFrame,
    ccr_df: pd.DataFrame,
    mscore_df: pd.DataFrame,
    regime_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Aggregate all findings into summary statistics.
    Writes: 07_summary.csv
    """
    logger.info("Task 1.8: Summary Statistics")

    total = len(SAMPLE_SYMBOLS)

    # Fundamentals availability
    fund_available = avail_df["all_critical_present"].sum()

    # Forensic decomposition
    forensic_total = len(forensic_df)
    m_score_pass = forensic_df["m_score_pass"].sum() if forensic_total > 0 else 0
    ccr_pass = forensic_df["ccr_pass"].sum() if forensic_total > 0 else 0
    pledge_pass = forensic_df["pledge_pass"].sum() if forensic_total > 0 else 0
    overall_pass = forensic_df["overall_forensic_pass"].sum() if forensic_total > 0 else 0

    # Pipeline funnel
    has_ohlcv = funnel_df["has_ohlcv"].sum()
    has_fund = funnel_df["has_fundamentals"].sum()
    s1a_pass = funnel_df["stage_1a_pass"].sum()
    s1b_pass = funnel_df["stage_1b_pass"].sum()

    # CCR distribution
    ccr_pass_80 = ccr_df["would_pass_0.80"].sum() if len(ccr_df) > 0 else 0
    ccr_pass_60 = ccr_df["would_pass_0.60"].sum() if len(ccr_df) > 0 else 0

    # M-Score sensitivity
    m_fail_count = len(mscore_df)
    m_rescuable = (mscore_df["m_score_if_defaults_fixed"] <= M_SCORE_THRESHOLD).sum() if m_fail_count > 0 else 0

    # Regime
    regime_row = regime_df[regime_df["metric"] == "detected_regime"]
    detected_regime = regime_row["value"].iloc[0] if not regime_row.empty else "UNKNOWN"

    records = [
        {"metric": "total_universe", "value": total, "pct_of_total": "100%"},
        {"metric": "fundamentals_all_fields_present", "value": int(fund_available), "pct_of_total": f"{fund_available/total*100:.1f}%"},
        {"metric": "has_ohlcv_data", "value": int(has_ohlcv), "pct_of_total": f"{has_ohlcv/total*100:.1f}%"},
        {"metric": "has_fundamentals_data", "value": int(has_fund), "pct_of_total": f"{has_fund/total*100:.1f}%"},
        {"metric": "m_score_pass_rate", "value": int(m_score_pass), "pct_of_total": f"{m_score_pass/forensic_total*100:.1f}%" if forensic_total > 0 else "N/A"},
        {"metric": "ccr_pass_rate_0.80", "value": int(ccr_pass), "pct_of_total": f"{ccr_pass/forensic_total*100:.1f}%" if forensic_total > 0 else "N/A"},
        {"metric": "ccr_pass_rate_0.60", "value": int(ccr_pass_60), "pct_of_total": f"{ccr_pass_60/len(ccr_df)*100:.1f}%" if len(ccr_df) > 0 else "N/A"},
        {"metric": "pledge_pass_rate", "value": int(pledge_pass), "pct_of_total": f"{pledge_pass/forensic_total*100:.1f}%" if forensic_total > 0 else "N/A"},
        {"metric": "stage_1a_forensic_pass", "value": int(overall_pass), "pct_of_total": f"{overall_pass/forensic_total*100:.1f}%" if forensic_total > 0 else "N/A"},
        {"metric": "stage_1a_pass_funnel", "value": int(s1a_pass), "pct_of_total": f"{s1a_pass/total*100:.1f}%"},
        {"metric": "stage_1b_pass_funnel", "value": int(s1b_pass), "pct_of_total": f"{s1b_pass/total*100:.1f}%"},
        {"metric": "m_score_fail_count", "value": int(m_fail_count), "pct_of_total": f"{m_fail_count/forensic_total*100:.1f}%" if forensic_total > 0 else "N/A"},
        {"metric": "m_score_rescuable_with_defaults", "value": int(m_rescuable), "pct_of_total": f"{m_rescuable/m_fail_count*100:.1f}%" if m_fail_count > 0 else "N/A"},
        {"metric": "detected_regime", "value": detected_regime, "pct_of_total": "-"},
        {"metric": "m_score_threshold", "value": M_SCORE_THRESHOLD, "pct_of_total": "-"},
        {"metric": "ccr_threshold", "value": CCR_THRESHOLD, "pct_of_total": "-"},
    ]

    df = pd.DataFrame(records)
    out_path = OUTPUT_DIR / "07_summary.csv"
    df.to_csv(out_path, index=False)

    logger.info(f"  -> {out_path.name}: {len(records)} summary metrics")
    return df


# ===================================================================
# Main Orchestrator
# ===================================================================

def main():
    """Run all EDA tasks sequentially."""
    logger.info("=" * 70)
    logger.info("EDA v0.1 — QuantSystem Exploratory Data Analysis")
    logger.info(f"Date: {date.today().isoformat()}")
    logger.info(f"Output directory: {OUTPUT_DIR}")
    logger.info("=" * 70)

    # ------------------------------------------------------------------
    # Step 0: Generate all sample data (shared across tasks)
    # ------------------------------------------------------------------
    logger.info("Step 0: Generating sample data...")

    ohlcv_data = generate_sample_ohlcv(SAMPLE_SYMBOLS)
    logger.info(f"  OHLCV: {len(ohlcv_data)} symbols")

    fundamentals_raw = generate_sample_fundamentals(SAMPLE_SYMBOLS)
    logger.info(f"  Fundamentals: {len(fundamentals_raw)} symbols")

    pledge_data = generate_sample_pledge_data(SAMPLE_SYMBOLS)
    logger.info(f"  Pledge data: {len(pledge_data)} symbols")

    index_data = generate_sample_index_data(ohlcv_data)
    logger.info("  Index data: nifty, vix, usdinr generated")

    fii_df = generate_sample_fii_data()
    logger.info(f"  FII/DII data: {len(fii_df)} days")

    regime_data = {
        "nifty_df": index_data.get("nifty_df", pd.DataFrame()),
        "vix_df": index_data.get("vix_df", pd.DataFrame()),
        "usdinr_df": index_data.get("usdinr_df", pd.DataFrame()),
        "fii_df": fii_df,
    }

    logger.info("")

    # ------------------------------------------------------------------
    # Execute all EDA tasks
    # ------------------------------------------------------------------
    avail_df = task_1_2_fundamentals_availability(fundamentals_raw)
    logger.info("")

    forensic_df = task_1_3_forensic_decomposition(fundamentals_raw, pledge_data)
    logger.info("")

    funnel_df = task_1_4_pipeline_funnel(ohlcv_data, fundamentals_raw, pledge_data, regime_data)
    logger.info("")

    ccr_df = task_1_5_ccr_distribution(fundamentals_raw)
    logger.info("")

    mscore_df = task_1_6_mscore_sensitivity(fundamentals_raw)
    logger.info("")

    regime_df = task_1_7_regime_audit(regime_data)
    logger.info("")

    summary_df = task_1_8_summary(avail_df, forensic_df, funnel_df, ccr_df, mscore_df, regime_df)
    logger.info("")

    # ------------------------------------------------------------------
    # Final report
    # ------------------------------------------------------------------
    logger.info("=" * 70)
    logger.info("EDA v0.1 complete. Output files:")
    for csv_file in sorted(OUTPUT_DIR.glob("*.csv")):
        size_kb = csv_file.stat().st_size / 1024
        logger.info(f"  {csv_file.name:40s} ({size_kb:.1f} KB)")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()

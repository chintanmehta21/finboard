"""
Stage 1A — Forensic Universe Filter

Implements the Beneish M-Score (simplified 5-ratio version) and Cash Conversion Ratio
to identify and exclude governance landmines before any technical analysis.

Rules (HARD EXCLUDE, no exceptions):
- M-Score > -2.22 -> probable earnings manipulator
- CFO/EBITDA < 0.80 -> cash not backing reported profits
- Promoter pledge > 5% OR pledge delta > +2pp in last quarter
"""

import logging

import numpy as np

logger = logging.getLogger(__name__)

# M-Score threshold: above this = probable manipulator
M_SCORE_THRESHOLD = -2.22

# Cash Conversion Ratio minimum: profits must convert to real cash
CCR_THRESHOLD = 0.80

# Sectors where CFO/EBITDA is structurally meaningless
CCR_EXEMPT_SECTORS = {'Banking', 'Finance', 'Insurance', 'NBFC'}

# Promoter pledge thresholds
PLEDGE_MAX_PCT = 5.0
PLEDGE_MAX_DELTA = 2.0  # percentage points per quarter


def beneish_m_score(f: dict) -> float:
    """
    Compute simplified Beneish M-Score using 5 key ratios.

    The M-Score estimates probability of earnings manipulation.
    Original model uses 8 variables; we use 5 that are reliably available
    via yfinance for Indian equities.

    Args:
        f: Fundamentals dict from fundamentals.py

    Returns:
        M-Score value. Higher (less negative) = higher manipulation probability.
        Threshold: > -2.22 indicates probable manipulation.
    """
    if not f:
        return 0.0  # Missing data: fail-safe (will be excluded)

    eps = 1e-9  # Epsilon to prevent division by zero

    # DSRI: Days Sales in Receivables Index
    # Detects channel stuffing / aggressive revenue recognition
    recv_t = f.get('receivables_t', 0) or 0
    recv_t1 = f.get('receivables_t1', 0) or 0
    sales_t = f.get('sales_t', 0) or eps
    sales_t1 = f.get('sales_t1', 0) or eps

    # DSRI: When prior-year receivables unavailable, use neutral default
    # (no channel stuffing signal if we can't measure it)
    if recv_t1 == 0 and recv_t == 0:
        dsri = 1.0  # Both zero/missing -> neutral
    elif recv_t1 == 0:
        dsri = 1.0  # Can't compute YoY change -> assume neutral
    else:
        dsri = (recv_t / sales_t) / (recv_t1 / sales_t1 + eps)

    # AQI: Asset Quality Index
    # Detects capitalising opex — common in Indian mid-caps
    ca_t = f.get('current_assets_t', 0) or 0
    ppe_t = f.get('ppe_t', 0) or 0
    ta_t = f.get('total_assets', 0) or eps
    ca_t1 = f.get('current_assets_t1', 0) or 0
    ppe_t1 = f.get('ppe_t1', 0) or 0

    # AQI: When prior-year current assets/PPE unavailable, use neutral default
    if (ca_t1 == 0 and ppe_t1 == 0) and (ca_t == 0 and ppe_t == 0):
        aqi = 1.0  # Both periods missing -> neutral
    elif ca_t1 == 0 and ppe_t1 == 0:
        aqi = 1.0  # Can't compute YoY change -> neutral
    else:
        aqi_t = 1 - (ca_t + ppe_t) / ta_t if ta_t > eps else 0
        aqi_t1 = 1 - (ca_t1 + ppe_t1) / ta_t if ta_t > eps else eps
        aqi = aqi_t / (aqi_t1 + eps)

    # TATA: Total Accruals to Total Assets
    # Accrual gap: accounting profit vs real cash
    net_income = f.get('net_income', 0) or 0
    cfo = f.get('cfo', 0) or 0
    tata = (net_income - cfo) / (ta_t + eps)

    # LVGI: Leverage Index
    # Sudden leverage spikes before downgrades
    debt_t = f.get('debt_t', 0) or 0
    debt_t1 = f.get('debt_t1', 0) or 0
    lvgi_t = debt_t / (ta_t + eps)
    lvgi_t1 = debt_t1 / (ta_t + eps)
    lvgi = lvgi_t / (lvgi_t1 + eps) if lvgi_t1 > eps else 1.0

    # SGI: Sales Growth Index
    # Unsustainable hyper-growth precursor
    sgi = sales_t / (sales_t1 + eps)

    # Beneish M-Score — 8-variable model coefficients, available variables only.
    # GMI, DEPI, SGAI are not available from yfinance; neutralised at 1.0 each.
    # Adjusted intercept: -4.840 + 0.528*1 + 0.115*1 - 0.172*1 = -4.369
    #
    # Correct coefficients (Beneish 1999, Eq. 3):
    #   DSRI  ×  0.920  — channel stuffing / revenue recognition
    #   AQI   ×  0.404  — asset quality (capitalising opex)
    #   SGI   ×  0.892  — unsustainable hyper-growth
    #   TATA  ×  4.679  — accrual gap (dominant predictor; previous value 0.528 = 9× too small)
    #   LVGI  × -0.327  — leverage index (negative coefficient, empirically derived;
    #                      previous value +0.404 had wrong sign AND magnitude)
    #
    # v0.22 BUG FIX: Prior version mapped variables to wrong coefficients:
    # TATA used GMI's 0.528 (9× too small), LVGI used AQI's +0.404 (sign inverted),
    # AQI used DEPI's 0.115 (3.5× too small). Result: manipulators incorrectly cleared
    # Stage 1A and rising-leverage companies received lower (safer) M-Scores.
    m_score = (
        -4.369
        + 0.920 * dsri
        + 0.404 * aqi
        + 0.892 * sgi
        + 4.679 * tata
        - 0.327 * lvgi
    )

    return m_score


def cash_conversion_ratio(f: dict) -> float:
    """
    Compute Cash Conversion Ratio = CFO / EBITDA.

    Companies reporting high EPS growth but failing to convert it into
    operating cash flow are classic 'momentum crash' candidates.

    Args:
        f: Fundamentals dict

    Returns:
        CCR value. Must be >= CCR_THRESHOLD to pass.
        Returns -1.0 sentinel when CFO data is unavailable.
    """
    if not f:
        return 0.0

    cfo = f.get('cfo', 0) or 0
    ebitda = f.get('ebitda', 0) or 1e-9

    if ebitda <= 0:
        return 0.0  # Negative EBITDA: exclude

    # When CFO is 0 but EBITDA exists, treat as data unavailable (not a genuine 0)
    # yfinance frequently returns 0 for operatingCashflow on Indian stocks
    if cfo == 0 and ebitda > 0:
        return -1.0  # Sentinel: data unavailable

    return cfo / ebitda


def forensic_hard_pass(f: dict, pledge_data: dict = None) -> bool:
    """
    v0.22: HARD forensic gates only — M-Score + Pledge.

    CCR moved to SOFT scoring (Buffett: EBITDA is flawed metric).
    Only called when fundamentals data is available (f is not None).

    Returns True if stock passes the non-negotiable integrity checks.
    """
    if not f:
        return True  # v0.22: No data = pass (caller handles data availability)

    # HARD gate 1: Beneish M-Score — "one cockroach in kitchen" (Buffett)
    m_score = beneish_m_score(f)
    if m_score > M_SCORE_THRESHOLD:
        return False

    # HARD gate 2: Promoter pledging — "never succeeded with a bad person" (Buffett)
    if pledge_data and pledge_data.get('data_available'):
        if pledge_data['pledge_pct'] > PLEDGE_MAX_PCT:
            return False
        if pledge_data['pledge_delta_1q'] > PLEDGE_MAX_DELTA:
            return False

    return True


def forensic_pass(f: dict, pledge_data: dict = None, sector: str = '') -> bool:
    """
    Run all Stage 1A forensic checks (legacy — includes CCR as hard gate).

    Kept for backward compatibility with bearish.py.
    For the main pipeline, use forensic_hard_pass() instead.
    """
    if not f:
        return False

    # Check 1: Beneish M-Score
    m_score = beneish_m_score(f)
    if m_score > M_SCORE_THRESHOLD:
        return False

    # Check 2: Cash Conversion Ratio
    ccr = cash_conversion_ratio(f)
    if sector not in CCR_EXEMPT_SECTORS:
        if ccr == -1.0:
            pass  # Data unavailable — don't penalize
        elif ccr < CCR_THRESHOLD:
            return False

    # Check 3: Promoter pledging
    if pledge_data and pledge_data.get('data_available'):
        if pledge_data['pledge_pct'] > PLEDGE_MAX_PCT:
            return False
        if pledge_data['pledge_delta_1q'] > PLEDGE_MAX_DELTA:
            return False

    return True


def forensic_quality_score(f: dict) -> float:
    """
    Compute a continuous forensic quality score (0-1) for Stage 2 factor ranking.

    This is NOT a gate — it's used as Factor 4 in the multi-factor composite.
    Higher = better quality/less manipulation risk.

    Composite: 50% CCR rank + 30% inverse M-Score rank + 20% inverse LVGI trend
    """
    if not f:
        return 0.0

    ccr = cash_conversion_ratio(f)
    m_score = beneish_m_score(f)

    # LVGI trend: lower is better (less leverage increase)
    debt_t = f.get('debt_t', 0) or 0
    debt_t1 = f.get('debt_t1', 0) or 0
    ta = f.get('total_assets', 0) or 1e-9
    lvgi = (debt_t / ta) / (debt_t1 / ta + 1e-9) if debt_t1 > 0 else 1.0

    # Normalize components to 0-1 range (will be percentile-ranked in pipeline)
    # Raw scores — higher = better quality
    ccr_score = min(max(ccr, 0), 2)  # Clip to [0, 2]
    m_score_inv = min(max(-m_score, 0), 10)  # More negative M-Score = better
    lvgi_inv = min(max(2 - lvgi, 0), 2)  # Lower LVGI = better

    # Weighted composite (raw, will be percentile-ranked in pipeline)
    composite = 0.5 * ccr_score + 0.3 * m_score_inv + 0.2 * lvgi_inv

    return composite

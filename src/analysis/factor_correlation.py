"""
Factor Correlation Monitor (PDF p.5 — Factor Correlation Check)

Before deploying new factor weights, verify that no two factors are
excessively correlated (Pearson > 0.60). If correlation is too high,
the composite score double-counts the same information.

This module:
1. Computes pairwise Pearson correlation of all 5 factor scores
2. Flags any pair exceeding the 0.60 threshold
3. Suggests remediation (drop or orthogonalize the weaker factor)

Should be run periodically (weekly or after factor weight changes)
to ensure the multi-factor model maintains diversification.
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Maximum acceptable pairwise correlation between any two factors
MAX_PAIRWISE_CORRELATION = 0.60

# Factor column names as used in pipeline.py
FACTOR_COLUMNS = ['mrs', 'deliv', 'vam', 'fq', 'rev']

FACTOR_NAMES = {
    'mrs': 'Mansfield RS',
    'deliv': 'Delivery Conviction',
    'vam': 'Volatility-Adjusted Momentum',
    'fq': 'Forensic Quality',
    'rev': 'Earnings Revision Breadth',
}


def check_factor_correlations(factor_df: pd.DataFrame,
                              threshold: float = MAX_PAIRWISE_CORRELATION) -> dict:
    """
    Compute pairwise Pearson correlations between all 5 factors
    and flag any pair exceeding the threshold.

    Args:
        factor_df: DataFrame with columns matching FACTOR_COLUMNS,
                   one row per stock in the scored universe
        threshold: Maximum acceptable pairwise correlation (default 0.60)

    Returns:
        Dict with:
            correlation_matrix: Full correlation matrix as dict of dicts
            violations: List of pairs exceeding threshold
            is_valid: True if no violations
            factor_count: Number of factors analyzed
            stock_count: Number of stocks in the sample
    """
    # Filter to only the factor columns that exist
    available = [c for c in FACTOR_COLUMNS if c in factor_df.columns]

    if len(available) < 2:
        logger.warning(f"Only {len(available)} factor columns found, need >= 2 for correlation check")
        return {
            'correlation_matrix': {},
            'violations': [],
            'is_valid': True,
            'factor_count': len(available),
            'stock_count': len(factor_df),
        }

    # Compute Pearson correlation matrix
    corr_matrix = factor_df[available].corr(method='pearson')

    # Find violations (pairwise correlation > threshold)
    violations = []
    checked = set()

    for i, f1 in enumerate(available):
        for j, f2 in enumerate(available):
            if i >= j:
                continue  # Skip diagonal and already-checked pairs

            pair_key = tuple(sorted([f1, f2]))
            if pair_key in checked:
                continue
            checked.add(pair_key)

            corr_val = float(corr_matrix.loc[f1, f2])
            abs_corr = abs(corr_val)

            if abs_corr > threshold:
                violations.append({
                    'factor_1': f1,
                    'factor_1_name': FACTOR_NAMES.get(f1, f1),
                    'factor_2': f2,
                    'factor_2_name': FACTOR_NAMES.get(f2, f2),
                    'correlation': round(corr_val, 4),
                    'abs_correlation': round(abs_corr, 4),
                    'severity': 'HIGH' if abs_corr > 0.80 else 'MODERATE',
                })

    is_valid = len(violations) == 0

    if violations:
        logger.warning(
            f"Factor correlation check FAILED: {len(violations)} pair(s) exceed "
            f"{threshold:.2f} threshold"
        )
        for v in violations:
            logger.warning(
                f"  {v['factor_1_name']} ↔ {v['factor_2_name']}: "
                f"ρ = {v['correlation']:.3f} ({v['severity']})"
            )
    else:
        logger.info(
            f"Factor correlation check PASSED: all {len(checked)} pairs "
            f"within {threshold:.2f} threshold ({len(factor_df)} stocks)"
        )

    # Convert correlation matrix to serializable format
    corr_dict = {}
    for f1 in available:
        corr_dict[f1] = {}
        for f2 in available:
            corr_dict[f1][f2] = round(float(corr_matrix.loc[f1, f2]), 4)

    return {
        'correlation_matrix': corr_dict,
        'violations': violations,
        'is_valid': is_valid,
        'factor_count': len(available),
        'stock_count': len(factor_df),
    }


def suggest_remediation(violations: list[dict]) -> list[str]:
    """
    Suggest actions to resolve factor correlation violations.

    Returns:
        List of remediation suggestion strings
    """
    if not violations:
        return ["No remediation needed — all factor pairs are within threshold."]

    suggestions = []

    for v in violations:
        f1 = v['factor_1_name']
        f2 = v['factor_2_name']
        corr = v['correlation']
        severity = v['severity']

        if severity == 'HIGH':
            suggestions.append(
                f"🔴 {f1} ↔ {f2} (ρ={corr:.3f}): DROP the weaker factor or "
                f"replace with an orthogonal alternative. "
                f"Correlation > 0.80 means they are near-duplicates."
            )
        else:
            suggestions.append(
                f"🟡 {f1} ↔ {f2} (ρ={corr:.3f}): REDUCE combined weight allocation. "
                f"Consider residualizing one factor against the other, or "
                f"applying a Gram-Schmidt orthogonalization step."
            )

    suggestions.append(
        "\n💡 General: After modifying factors, re-run check_factor_correlations() "
        "to verify the fix. Target: all pairwise |ρ| < 0.60."
    )

    return suggestions


def get_correlation_report(factor_df: pd.DataFrame) -> str:
    """
    Generate a human-readable correlation report for logging/alerts.

    Args:
        factor_df: DataFrame with factor score columns

    Returns:
        Formatted string report
    """
    result = check_factor_correlations(factor_df)

    lines = [
        "═══ Factor Correlation Report ═══",
        f"Stocks analyzed: {result['stock_count']}",
        f"Factors checked: {result['factor_count']}",
        f"Status: {'✅ PASSED' if result['is_valid'] else '❌ FAILED'}",
        "",
        "Correlation Matrix:",
    ]

    # Format correlation matrix
    corr = result['correlation_matrix']
    factors = list(corr.keys())

    if factors:
        # Header row
        header = f"{'':>6}" + "".join(f"{f:>8}" for f in factors)
        lines.append(header)

        for f1 in factors:
            row = f"{f1:>6}"
            for f2 in factors:
                val = corr[f1][f2]
                marker = " *" if abs(val) > MAX_PAIRWISE_CORRELATION and f1 != f2 else "  "
                row += f"{val:>6.3f}{marker}"
            lines.append(row)

    if result['violations']:
        lines.append("")
        lines.append(f"⚠️  Violations ({len(result['violations'])}):")
        for v in result['violations']:
            lines.append(
                f"  {v['factor_1_name']} ↔ {v['factor_2_name']}: "
                f"ρ = {v['correlation']:.3f} [{v['severity']}]"
            )

        lines.append("")
        for s in suggest_remediation(result['violations']):
            lines.append(s)

    return '\n'.join(lines)

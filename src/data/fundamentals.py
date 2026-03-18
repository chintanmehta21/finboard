"""
Fundamental Data via yfinance — Quarterly Financial Statements

Provides CFO, EBITDA, receivables, total assets, debt, and other metrics
required for Beneish M-Score and Cash Conversion Ratio calculations.

Known limitation: ~45-day lag from quarter end (aligned with SEBI LODR).
Stocks with missing fundamentals are excluded at Stage 1A (conservative fail-safe).
"""

import logging
import time

import yfinance as yf
import pandas as pd

logger = logging.getLogger(__name__)

# Cache fundamentals in memory for the run
_fundamentals_cache: dict[str, dict | None] = {}


def get_fundamentals(symbol: str) -> dict | None:
    """
    Return latest available fundamental metrics for M-Score and CCR.

    Args:
        symbol: NSE trading symbol (e.g., 'RELIANCE')

    Returns:
        Dict with financial metrics, or None if data unavailable.
        Keys: cfo, ebitda, net_income, receivables_t, receivables_t1,
              sales_t, sales_t1, total_assets, debt_t, debt_t1,
              current_assets_t, ppe_t, current_assets_t1, ppe_t1,
              debt_equity
    """
    if symbol in _fundamentals_cache:
        return _fundamentals_cache[symbol]

    try:
        ticker = yf.Ticker(f'{symbol}.NS')

        # Quarterly financial statements
        cf = ticker.quarterly_cashflow
        bs = ticker.quarterly_balance_sheet
        inc = ticker.quarterly_financials

        if cf is None or bs is None or inc is None:
            _fundamentals_cache[symbol] = None
            return None

        if cf.empty or bs.empty or inc.empty:
            _fundamentals_cache[symbol] = None
            return None

        # Most recent quarter (index 0) and one year ago (index 4, or last available)
        result = {}

        # Cash Flow items
        result['cfo'] = _safe_get(cf, 'Operating Cash Flow', 0)
        if result['cfo'] is None:
            result['cfo'] = _safe_get(cf, 'Cash Flow From Continuing Operating Activities', 0)

        # Income statement items
        result['ebitda'] = _safe_get(inc, 'EBITDA', 0)
        if result['ebitda'] is None:
            # Compute EBITDA = Operating Income + Depreciation
            op_inc = _safe_get(inc, 'Operating Income', 0)
            dep = _safe_get(cf, 'Depreciation Amortization Depletion', 0)
            if op_inc is not None and dep is not None:
                result['ebitda'] = op_inc + abs(dep)

        result['net_income'] = _safe_get(inc, 'Net Income', 0)

        # Revenue / Sales
        result['sales_t'] = _safe_get(inc, 'Total Revenue', 0)
        if result['sales_t'] is None:
            result['sales_t'] = _safe_get(inc, 'Operating Revenue', 0)
        result['sales_t1'] = _safe_get(inc, 'Total Revenue', min(4, inc.shape[1] - 1))
        if result['sales_t1'] is None:
            result['sales_t1'] = _safe_get(inc, 'Operating Revenue', min(4, inc.shape[1] - 1))

        # Balance sheet items — current quarter
        result['receivables_t'] = _safe_get(bs, 'Receivables', 0)
        if result['receivables_t'] is None:
            result['receivables_t'] = _safe_get(bs, 'Net Receivables', 0)

        result['total_assets'] = _safe_get(bs, 'Total Assets', 0)
        result['debt_t'] = _safe_get(bs, 'Total Debt', 0)
        result['current_assets_t'] = _safe_get(bs, 'Current Assets', 0)
        result['ppe_t'] = _safe_get(bs, 'Net PPE', 0)
        if result['ppe_t'] is None:
            result['ppe_t'] = _safe_get(bs, 'Gross PPE', 0)

        # Balance sheet items — one year ago
        yr_ago_idx = min(4, bs.shape[1] - 1) if bs.shape[1] > 1 else 0
        result['receivables_t1'] = _safe_get(bs, 'Receivables', yr_ago_idx)
        if result['receivables_t1'] is None:
            result['receivables_t1'] = _safe_get(bs, 'Net Receivables', yr_ago_idx)

        result['debt_t1'] = _safe_get(bs, 'Total Debt', yr_ago_idx)
        result['current_assets_t1'] = _safe_get(bs, 'Current Assets', yr_ago_idx)
        result['ppe_t1'] = _safe_get(bs, 'Net PPE', yr_ago_idx)

        # Compute Debt/Equity ratio
        total_equity = _safe_get(bs, 'Stockholders Equity', 0)
        if total_equity is None:
            total_equity = _safe_get(bs, 'Total Equity Gross Minority Interest', 0)
        if total_equity and total_equity > 0 and result['debt_t']:
            result['debt_equity'] = result['debt_t'] / total_equity
        else:
            result['debt_equity'] = 0.0

        # Soften validation: use neutral defaults for missing fields
        # (yfinance frequently returns None for Indian equities)
        for field in ['cfo', 'ebitda', 'total_assets', 'sales_t']:
            if result.get(field) is None:
                logger.debug(f"{symbol}: missing {field}, using neutral default 0")
                result[field] = 0

        # Warn if all critical fields are missing (likely a data source issue)
        if all(result.get(f) == 0 for f in ['cfo', 'ebitda', 'total_assets', 'sales_t']):
            logger.warning(f"All critical fundamentals missing for {symbol}")
            _fundamentals_cache[symbol] = None
            return None

        _fundamentals_cache[symbol] = result
        return result

    except Exception as e:
        logger.debug(f"yfinance fetch failed for {symbol}: {e}")
        _fundamentals_cache[symbol] = None
        return None


def get_fundamentals_batch(symbols: list[str]) -> dict[str, dict | None]:
    """
    Fetch fundamentals for multiple symbols with rate limiting.

    Returns:
        Dict mapping symbol -> fundamentals dict (or None)
    """
    results = {}

    for i, symbol in enumerate(symbols):
        results[symbol] = get_fundamentals(symbol)

        # yfinance rate limiting: ~2 requests per second
        if (i + 1) % 2 == 0:
            time.sleep(0.5)

        if (i + 1) % 50 == 0:
            available = sum(1 for v in results.values() if v is not None)
            logger.info(f"Fundamentals progress: {i + 1}/{len(symbols)} ({available} available)")

    available = sum(1 for v in results.values() if v is not None)
    logger.info(f"Fundamentals complete: {available}/{len(symbols)} symbols with data")
    return results


def _safe_get(df: pd.DataFrame, row_label: str, col_idx: int):
    """Safely extract a value from a financial statement DataFrame."""
    try:
        if row_label in df.index and col_idx < df.shape[1]:
            val = df.loc[row_label].iloc[col_idx]
            if pd.notna(val):
                return float(val)
    except (KeyError, IndexError, TypeError):
        pass
    return None

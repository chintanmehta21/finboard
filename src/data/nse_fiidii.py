"""
NSE FII/DII Flow Data — Institutional Activity

Fetches daily FII (Foreign Institutional Investor) and DII (Domestic Institutional Investor)
net buy/sell data from NSE India. Used for regime corroboration in Stage 3.

When DIIs are strong buyers, downside protection is higher during FII sell-offs.
"""

import time
import logging
from datetime import date

import pandas as pd
import requests

logger = logging.getLogger(__name__)

NSE_FII_URL = 'https://www.nseindia.com/api/fiidiiActivity/WEB'

NSE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://www.nseindia.com/',
    'Accept': 'application/json',
}


def fetch_fiidii_flows(trade_date: date = None) -> dict:
    """
    Fetch FII/DII net flows for the given date (defaults to today).

    Returns:
        Dict with keys:
            - fii_net: FII net buy/sell in INR crores (positive = buying)
            - dii_net: DII net buy/sell in INR crores (positive = buying)
            - fii_buy: FII gross purchase value
            - fii_sell: FII gross sale value
            - dii_buy: DII gross purchase value
            - dii_sell: DII gross sale value
    """
    session = requests.Session()

    # Seed session cookie
    try:
        session.get('https://www.nseindia.com/', headers=NSE_HEADERS, timeout=10)
        time.sleep(1)
    except requests.RequestException:
        pass

    try:
        resp = session.get(NSE_FII_URL, headers=NSE_HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        result = {
            'fii_net': 0.0,
            'dii_net': 0.0,
            'fii_buy': 0.0,
            'fii_sell': 0.0,
            'dii_buy': 0.0,
            'dii_sell': 0.0,
        }

        # Parse NSE FII/DII response format
        for entry in data if isinstance(data, list) else [data]:
            category = entry.get('category', '').upper()
            buy_val = _parse_crore(entry.get('buyValue', 0))
            sell_val = _parse_crore(entry.get('sellValue', 0))

            if 'FII' in category or 'FPI' in category:
                result['fii_buy'] = buy_val
                result['fii_sell'] = sell_val
                result['fii_net'] = buy_val - sell_val
            elif 'DII' in category:
                result['dii_buy'] = buy_val
                result['dii_sell'] = sell_val
                result['dii_net'] = buy_val - sell_val

        logger.info(
            f"FII/DII flows: FII net={result['fii_net']:.0f} Cr, "
            f"DII net={result['dii_net']:.0f} Cr"
        )
        return result

    except Exception as e:
        logger.warning(f"FII/DII fetch failed: {e}")
        return {
            'fii_net': 0.0, 'dii_net': 0.0,
            'fii_buy': 0.0, 'fii_sell': 0.0,
            'dii_buy': 0.0, 'dii_sell': 0.0,
        }


def build_fiidii_df(fii_data: dict) -> pd.DataFrame:
    """
    Build a DataFrame from FII/DII data for use in regime detection.
    In production, this would accumulate 30 days of data.

    For single-day runs, we use the daily net values directly.
    """
    df = pd.DataFrame([{
        'date': date.today(),
        'fii_net': fii_data.get('fii_net', 0),
        'dii_net': fii_data.get('dii_net', 0),
        'dii_net_30d': fii_data.get('dii_net', 0),  # Approximation for single-day
    }])
    df = df.set_index('date')
    return df


def _parse_crore(value) -> float:
    """Parse a value that may be string with commas or already numeric."""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(',', '').replace('(', '-').replace(')', ''))
    except (ValueError, TypeError):
        return 0.0

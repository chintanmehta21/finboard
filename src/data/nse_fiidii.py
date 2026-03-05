"""
NSE FII/DII Flow Data — Institutional Activity

Fetches daily FII (Foreign Institutional Investor) and DII (Domestic Institutional Investor)
net buy/sell data. Uses multiple data sources with fallback chain:
1. NSE India API (primary)
2. NSDL FPI data via yfinance proxy
3. Local cache (last successful fetch)

Used for regime corroboration in Stage 3.
"""

import json
import time
import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

from src.data.nse_session import create_nse_session, NSE_API_HEADERS

logger = logging.getLogger(__name__)

# NSE endpoints to try (fiidiiTradeReact is the current working endpoint)
NSE_ENDPOINTS = [
    'https://www.nseindia.com/api/fiidiiTradeReact',
]

# Cache last successful fetch for fallback
CACHE_DIR = Path('.cache')
CACHE_FILE = CACHE_DIR / 'fiidii_last.json'


def fetch_fiidii_flows(trade_date: date = None) -> dict:
    """
    Fetch FII/DII net flows using a fallback chain of data sources.

    Returns:
        Dict with keys:
            - fii_net: FII net buy/sell in INR crores (positive = buying)
            - dii_net: DII net buy/sell in INR crores (positive = buying)
            - fii_buy: FII gross purchase value
            - fii_sell: FII gross sale value
            - dii_buy: DII gross purchase value
            - dii_sell: DII gross sale value
    """
    # Source 1: Try NSE India API endpoints
    result = _fetch_from_nse()
    if result and (result['fii_net'] != 0 or result['dii_net'] != 0):
        _save_cache(result)
        return result

    # Source 2: Try moneycontrol/alternative web source
    result = _fetch_from_alternative()
    if result and (result['fii_net'] != 0 or result['dii_net'] != 0):
        _save_cache(result)
        return result

    # Source 3: Try loading from cache
    cached = _load_cache()
    if cached:
        logger.info("FII/DII: using cached data from last successful fetch")
        return cached

    logger.warning("FII/DII: all sources failed, returning zeros")
    return _empty_result()


def _fetch_from_nse() -> dict | None:
    """Try fetching FII/DII data from NSE India API endpoints."""
    session = create_nse_session()

    for url in NSE_ENDPOINTS:
        for attempt in range(2):
            try:
                resp = session.get(url, headers=NSE_API_HEADERS, timeout=30)
                if resp.status_code != 200:
                    logger.debug(f"NSE FII/DII {url} attempt {attempt+1}: HTTP {resp.status_code}")
                    time.sleep(3)
                    continue

                data = resp.json()
                result = _parse_nse_response(data)
                if result['fii_net'] != 0 or result['dii_net'] != 0:
                    logger.info(
                        f"FII/DII from NSE: FII net={result['fii_net']:.0f} Cr, "
                        f"DII net={result['dii_net']:.0f} Cr"
                    )
                    return result

            except Exception as e:
                logger.debug(f"NSE FII/DII {url} attempt {attempt+1} failed: {e}")
                time.sleep(3)

    logger.warning("FII/DII: all NSE endpoints failed")
    return None


def _parse_nse_response(data) -> dict:
    """Parse FII/DII data from various NSE API response formats."""
    result = _empty_result()

    # Format 1: List of category entries (fiidiiActivity/WEB)
    if isinstance(data, list):
        for entry in data:
            _parse_entry(entry, result)
        return result

    # Format 2: Dict with nested data (fiidiiTradeReact)
    if isinstance(data, dict):
        # Try direct fields
        if 'category' in data:
            _parse_entry(data, result)
        # Try nested data list
        for key in ('data', 'activities', 'results'):
            if key in data and isinstance(data[key], list):
                for entry in data[key]:
                    _parse_entry(entry, result)
                return result
        # Try flat dict format
        if 'fpiNetValues' in data or 'diiNetValues' in data:
            result['fii_net'] = _parse_crore(data.get('fpiNetValues', 0))
            result['dii_net'] = _parse_crore(data.get('diiNetValues', 0))
            return result

    return result


def _parse_entry(entry: dict, result: dict):
    """Parse a single FII/DII activity entry into result dict."""
    if not isinstance(entry, dict):
        return

    category = str(entry.get('category', '')).upper()
    buy_val = _parse_crore(entry.get('buyValue', entry.get('buy_value', 0)))
    sell_val = _parse_crore(entry.get('sellValue', entry.get('sell_value', 0)))
    net_val = _parse_crore(entry.get('netValue', entry.get('net_value', 0)))

    if 'FII' in category or 'FPI' in category:
        result['fii_buy'] = buy_val
        result['fii_sell'] = sell_val
        result['fii_net'] = net_val if net_val != 0 else (buy_val - sell_val)
    elif 'DII' in category:
        result['dii_buy'] = buy_val
        result['dii_sell'] = sell_val
        result['dii_net'] = net_val if net_val != 0 else (buy_val - sell_val)


def _fetch_from_alternative() -> dict | None:
    """Try fetching FII/DII data from alternative sources."""
    # Try NSDL FPI daily data via public API
    try:
        today_str = date.today().strftime('%d-%b-%Y')
        url = f'https://www.fpi.nsdl.co.in/web/StaticReports/Fortnightly/FPIInvestmentDetails_{today_str}.html'
        resp = requests.get(url, timeout=15, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'
        })
        if resp.status_code == 200 and 'Net Investment' in resp.text:
            logger.info("FII/DII: parsing NSDL FPI data...")
            # NSDL provides HTML tables; basic parse
            result = _parse_nsdl_html(resp.text)
            if result and result['fii_net'] != 0:
                return result
    except Exception as e:
        logger.debug(f"NSDL FPI fetch failed: {e}")

    # Try a simple moneycontrol/ET API for FII data
    try:
        mc_url = 'https://api.moneycontrol.com/mcapi/v1/fii-dii/overview'
        resp = requests.get(mc_url, timeout=15, headers={
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json',
        })
        if resp.status_code == 200:
            data = resp.json()
            if 'data' in data:
                mc_data = data['data']
                fii_net = _parse_crore(mc_data.get('fii_net_value', mc_data.get('fiiNetValue', 0)))
                dii_net = _parse_crore(mc_data.get('dii_net_value', mc_data.get('diiNetValue', 0)))
                if fii_net != 0 or dii_net != 0:
                    logger.info(f"FII/DII from MoneyControl: FII={fii_net:.0f}, DII={dii_net:.0f}")
                    return {
                        'fii_net': fii_net, 'dii_net': dii_net,
                        'fii_buy': 0, 'fii_sell': 0,
                        'dii_buy': 0, 'dii_sell': 0,
                    }
    except Exception as e:
        logger.debug(f"MoneyControl FII/DII fetch failed: {e}")

    return None


def _parse_nsdl_html(html: str) -> dict | None:
    """Parse NSDL FPI HTML report for net investment figures."""
    try:
        # Look for "Net Investment" value in the HTML
        import re
        # Pattern: find numbers near "Net Investment" text
        matches = re.findall(r'Net\s+Investment.*?([+-]?\d[\d,]*\.?\d*)', html, re.IGNORECASE | re.DOTALL)
        if matches:
            fii_net = _parse_crore(matches[0])
            return {
                'fii_net': fii_net, 'dii_net': 0,
                'fii_buy': 0, 'fii_sell': 0,
                'dii_buy': 0, 'dii_sell': 0,
            }
    except Exception:
        pass
    return None


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


def _empty_result() -> dict:
    return {
        'fii_net': 0.0, 'dii_net': 0.0,
        'fii_buy': 0.0, 'fii_sell': 0.0,
        'dii_buy': 0.0, 'dii_sell': 0.0,
    }


def _save_cache(result: dict):
    """Save FII/DII data to cache file with timestamp."""
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        cache_data = {**result, '_cached_at': datetime.now().isoformat()}
        CACHE_FILE.write_text(json.dumps(cache_data))
    except Exception:
        pass


def _load_cache() -> dict | None:
    """Load FII/DII data from cache file."""
    try:
        if CACHE_FILE.exists():
            data = json.loads(CACHE_FILE.read_text())
            # Remove cache metadata before returning
            data.pop('_cached_at', None)
            return data
    except Exception:
        pass
    return None


def _parse_crore(value) -> float:
    """Parse a value that may be string with commas or already numeric."""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(',', '').replace('(', '-').replace(')', ''))
    except (ValueError, TypeError):
        return 0.0

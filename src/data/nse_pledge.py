"""
NSE Promoter Pledging Data

Fetches promoter shareholding patterns from NSE to compute:
1. Absolute pledge percentage (< 5% threshold)
2. Quarterly pledge delta (< +2pp threshold)

A sudden increase in pledging signals promoter liquidity crunch and typically
precedes FII-driven sell-offs.
"""

import time
import logging
from datetime import date

import pandas as pd
import requests

logger = logging.getLogger(__name__)

NSE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://www.nseindia.com/',
    'Accept': 'application/json',
}

# Cache pledge data in memory for the run (avoid repeated NSE hits)
_pledge_cache: dict[str, dict] = {}


def get_pledge_data(symbol: str) -> dict:
    """
    Get promoter pledge data for a symbol.

    Returns:
        Dict with keys:
            - pledge_pct: Current promoter pledge percentage
            - pledge_delta_1q: Change in pledge % from last quarter
            - data_available: Whether pledge data was successfully fetched
    """
    if symbol in _pledge_cache:
        return _pledge_cache[symbol]

    default = {'pledge_pct': 0.0, 'pledge_delta_1q': 0.0, 'data_available': False}

    try:
        session = requests.Session()
        session.get('https://www.nseindia.com/', headers=NSE_HEADERS, timeout=10)
        time.sleep(0.5)

        # NSE shareholding pattern API
        url = f'https://www.nseindia.com/api/corporate-shareholding?symbol={symbol}'
        resp = session.get(url, headers=NSE_HEADERS, timeout=30)

        if resp.status_code != 200:
            _pledge_cache[symbol] = default
            return default

        data = resp.json()

        # Extract promoter pledge info from shareholding pattern
        pledge_pct = _extract_pledge_pct(data)
        prev_pledge = _extract_prev_pledge_pct(data)

        result = {
            'pledge_pct': pledge_pct,
            'pledge_delta_1q': pledge_pct - prev_pledge if prev_pledge is not None else 0.0,
            'data_available': True,
        }

        _pledge_cache[symbol] = result
        return result

    except Exception as e:
        logger.debug(f"Pledge data unavailable for {symbol}: {e}")
        _pledge_cache[symbol] = default
        return default


def get_pledge_data_batch(symbols: list[str]) -> dict[str, dict]:
    """
    Fetch pledge data for multiple symbols with rate limiting.

    Returns:
        Dict mapping symbol -> pledge data dict
    """
    results = {}
    for i, symbol in enumerate(symbols):
        results[symbol] = get_pledge_data(symbol)

        # Rate limit: 2 requests per second for NSE
        if (i + 1) % 2 == 0:
            time.sleep(1.0)

        if (i + 1) % 50 == 0:
            logger.info(f"Pledge data progress: {i + 1}/{len(symbols)}")

    available = sum(1 for v in results.values() if v['data_available'])
    logger.info(f"Pledge data: {available}/{len(symbols)} symbols available")
    return results


def _extract_pledge_pct(data) -> float:
    """Extract current promoter pledge percentage from NSE shareholding response."""
    try:
        # Navigate the NSE shareholding pattern JSON structure
        if isinstance(data, list):
            for entry in data:
                if 'pledgedPercentage' in entry:
                    return float(entry['pledgedPercentage'] or 0)
                if 'promotersPledged' in entry:
                    return float(entry['promotersPledged'] or 0)
        elif isinstance(data, dict):
            for key in ['pledgedPercentage', 'promotersPledged', 'pledge_pct']:
                if key in data:
                    return float(data[key] or 0)
    except (ValueError, TypeError, KeyError):
        pass
    return 0.0


def _extract_prev_pledge_pct(data) -> float | None:
    """Extract previous quarter's pledge percentage for delta computation."""
    try:
        if isinstance(data, list) and len(data) > 1:
            for entry in data[1:]:
                if 'pledgedPercentage' in entry:
                    return float(entry['pledgedPercentage'] or 0)
    except (ValueError, TypeError, KeyError):
        pass
    return None

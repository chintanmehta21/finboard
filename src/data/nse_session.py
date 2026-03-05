"""
Shared NSE Session Helper — Browser-Like HTTP Session for NSE India APIs

Centralizes session creation with proper Cloudflare/anti-bot bypass headers.
All NSE data modules (bhavcopy, FII/DII, pledge, universe) import from here.
"""

import time
import logging

import requests

logger = logging.getLogger(__name__)

# Headers for seeding session (must look like real browser navigation)
NSE_SEED_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Cache-Control': 'max-age=0',
}

# Headers for API calls (after session is seeded with cookies)
# Note: 'br' (brotli) removed from Accept-Encoding since requests library
# doesn't support it natively and NSE sends brotli-compressed responses
NSE_API_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate',
    'Referer': 'https://www.nseindia.com/market-data/live-equity-market',
    'X-Requested-With': 'XMLHttpRequest',
}


def create_nse_session() -> requests.Session:
    """Create and seed an NSE session with homepage cookies (Cloudflare bypass)."""
    session = requests.Session()
    for attempt in range(3):
        try:
            resp = session.get('https://www.nseindia.com', headers=NSE_SEED_HEADERS, timeout=15)
            if resp.status_code == 200 and session.cookies:
                time.sleep(2)  # Let Cloudflare settle
                return session
            logger.debug(f"NSE seed attempt {attempt + 1}: status={resp.status_code}")
        except requests.RequestException as e:
            logger.debug(f"NSE seed attempt {attempt + 1} failed: {e}")
        time.sleep(3)
    logger.warning("NSE session seeding failed after 3 attempts")
    return session

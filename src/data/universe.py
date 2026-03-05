"""
NSE 500 Universe — Point-in-Time Constituent List

Loads the NSE 500 constituent list from a local CSV. Auto-downloads from
NSE India API if the CSV is stale (>90 days) or has too few symbols (<100).

The CSV should contain at minimum a 'SYMBOL' column with NSE trading symbols.
"""

import logging
import random
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.config import UNIVERSE_PCT
from src.data.nse_session import create_nse_session, NSE_API_HEADERS

logger = logging.getLogger(__name__)

UNIVERSE_FILE = Path('data/nse500_constituents.csv')
NSE_INDEX_URL = 'https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20500'
STALE_DAYS = 90
MIN_SYMBOLS = 100


def load_universe(auto_refresh: bool = True) -> list[str]:
    """
    Load NSE 500 constituent symbols from the PIT CSV file.
    Auto-downloads from NSE if CSV is stale (>90 days) or has <100 symbols.

    Args:
        auto_refresh: If True, auto-download when CSV is stale or small

    Returns:
        List of NSE trading symbols (e.g., ['RELIANCE', 'TCS', 'HDFCBANK', ...])
    """
    # Auto-download if file missing, stale, or too small
    if auto_refresh:
        should_download = False
        if not UNIVERSE_FILE.exists():
            should_download = True
            logger.info("Universe file not found, will download from NSE")
        else:
            # Check staleness
            mtime = datetime.fromtimestamp(UNIVERSE_FILE.stat().st_mtime)
            age_days = (datetime.now() - mtime).days
            if age_days > STALE_DAYS:
                should_download = True
                logger.info(f"Universe file is {age_days} days old (>{STALE_DAYS}), refreshing")
            else:
                # Check symbol count
                try:
                    df = pd.read_csv(UNIVERSE_FILE)
                    if len(df) < MIN_SYMBOLS:
                        should_download = True
                        logger.info(f"Universe file has {len(df)} symbols (<{MIN_SYMBOLS}), refreshing")
                except Exception:
                    should_download = True

        if should_download:
            download_nse500_constituents()

    if not UNIVERSE_FILE.exists():
        raise FileNotFoundError(
            f"Universe file not found at {UNIVERSE_FILE}. "
            "Download from NSE: nseindia.com/market-data/securities-available-for-trading"
        )

    df = pd.read_csv(UNIVERSE_FILE)

    # Handle different possible column names from NSE downloads
    symbol_col = None
    for candidate in ['SYMBOL', 'Symbol', 'symbol', 'TRADING_SYMBOL', 'TradingSymbol']:
        if candidate in df.columns:
            symbol_col = candidate
            break

    if symbol_col is None:
        raise ValueError(
            f"Could not find symbol column in {UNIVERSE_FILE}. "
            f"Available columns: {list(df.columns)}"
        )

    symbols = df[symbol_col].dropna().str.strip().tolist()

    # Filter out any non-equity entries (indices, ETFs if accidentally included)
    symbols = [s for s in symbols if s and not s.startswith('NIFTY')]

    # Apply universe_pct: use a subset for testing (e.g., 0.05 = 5%)
    if 0 < UNIVERSE_PCT < 1.0:
        subset_size = max(10, int(len(symbols) * UNIVERSE_PCT))
        symbols = random.sample(symbols, min(subset_size, len(symbols)))
        logger.info(f"Universe subset ({UNIVERSE_PCT*100:.0f}%): {len(symbols)} symbols selected")
    else:
        logger.info(f"Loaded {len(symbols)} symbols from universe file (100%)")

    return symbols


def download_nse500_constituents() -> bool:
    """
    Download the full NSE 500 constituent list.

    Tries multiple sources:
    1. NSE archives static CSV (most reliable, no Cloudflare)
    2. NSE India API (may be blocked by Cloudflare)

    Returns True on success, False on failure (existing CSV is preserved).
    """
    import requests

    # Source 1: NSE archives static CSV (no Cloudflare blocking)
    archive_urls = [
        'https://archives.nseindia.com/content/indices/ind_nifty500list.csv',
        'https://www1.nseindia.com/content/indices/ind_nifty500list.csv',
    ]

    for url in archive_urls:
        try:
            logger.info(f"Downloading NSE 500 from {url}...")
            resp = requests.get(url, timeout=30, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0',
            })
            if resp.status_code == 200 and len(resp.content) > 1000:
                import io
                df = pd.read_csv(io.StringIO(resp.text))

                # NSE archive CSV has columns like: Company Name, Industry, Symbol, ...
                symbol_col = None
                for c in df.columns:
                    if 'symbol' in c.lower():
                        symbol_col = c
                        break

                if symbol_col and len(df) > 100:
                    # Standardize columns
                    records = []
                    for _, row in df.iterrows():
                        sym = str(row[symbol_col]).strip()
                        if sym and sym != 'nan' and not sym.startswith('NIFTY'):
                            company = ''
                            sector = ''
                            for c in df.columns:
                                if 'company' in c.lower() or 'name' in c.lower():
                                    company = str(row[c]).strip() if pd.notna(row[c]) else ''
                                if 'industry' in c.lower() or 'sector' in c.lower():
                                    sector = str(row[c]).strip() if pd.notna(row[c]) else ''
                            records.append({'SYMBOL': sym, 'COMPANY': company, 'SECTOR': sector})

                    if len(records) > 100:
                        out_df = pd.DataFrame(records)
                        UNIVERSE_FILE.parent.mkdir(parents=True, exist_ok=True)
                        out_df.to_csv(UNIVERSE_FILE, index=False)
                        logger.info(f"NSE 500 universe downloaded: {len(out_df)} symbols from archives")
                        return True
        except Exception as e:
            logger.debug(f"NSE archive download from {url} failed: {e}")

    # Source 2: NSE India API (may be blocked by Cloudflare)
    logger.info("Trying NSE India API for universe download...")
    session = create_nse_session()

    try:
        resp = session.get(NSE_INDEX_URL, headers=NSE_API_HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        stocks = data.get('data', [])
        if not stocks or len(stocks) < 50:
            logger.warning(f"NSE API returned only {len(stocks)} stocks, skipping update")
            return False

        records = []
        for stock in stocks:
            symbol = stock.get('symbol', '').strip()
            if not symbol or symbol.startswith('NIFTY'):
                continue
            records.append({
                'SYMBOL': symbol,
                'COMPANY': stock.get('meta', {}).get('companyName', '')
                           if isinstance(stock.get('meta'), dict)
                           else stock.get('companyName', ''),
                'SECTOR': stock.get('meta', {}).get('industry', '')
                          if isinstance(stock.get('meta'), dict)
                          else stock.get('industry', ''),
            })

        if len(records) < 50:
            logger.warning(f"Parsed only {len(records)} symbols, skipping update")
            return False

        df = pd.DataFrame(records)
        UNIVERSE_FILE.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(UNIVERSE_FILE, index=False)
        logger.info(f"NSE 500 universe downloaded: {len(df)} symbols saved to {UNIVERSE_FILE}")
        return True

    except Exception as e:
        logger.warning(f"NSE API download failed: {e}")
        return False


def get_sector_map() -> dict[str, str]:
    """
    Load sector mapping for each symbol. Used for sector concentration caps.

    Returns:
        Dict mapping symbol -> SEBI sector name
    """
    if not UNIVERSE_FILE.exists():
        return {}

    df = pd.read_csv(UNIVERSE_FILE)

    # Try to find sector column
    sector_col = None
    for candidate in ['SECTOR', 'Sector', 'sector', 'INDUSTRY', 'Industry']:
        if candidate in df.columns:
            sector_col = candidate
            break

    if sector_col is None:
        logger.warning("No sector column found in universe file, sector caps disabled")
        return {}

    symbol_col = None
    for candidate in ['SYMBOL', 'Symbol', 'symbol']:
        if candidate in df.columns:
            symbol_col = candidate
            break

    if symbol_col is None:
        return {}

    return dict(zip(df[symbol_col].str.strip(), df[sector_col].str.strip()))

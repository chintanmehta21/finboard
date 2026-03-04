"""
NSE Bhavcopy Fetch — Delivery Volume Data

Downloads the daily CM-UDiFF bhavcopy ZIP from NSE India, which contains
exact delivery quantity and delivery percentage for all listed equities.

This is the CRITICAL data source not available in Fyers API.
Fyers OHLCV must be merged with bhavcopy delivery data using symbol as join key.
"""

import io
import time
import logging
import zipfile
from datetime import date, timedelta

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# NSE bhavcopy API endpoint (CM-UDiFF format, post July 2024)
NSE_BHAVCOPY_URL = (
    'https://nseindia.com/api/reports?archives='
    '%5B%7B%22name%22%3A%22CM+-+Bhavcopy+%28common+Udiff+format%29%22'
    '%2C%22type%22%3A%22daily%22%2C%22category%22%3A%22capital-market%22'
    '%2C%22section%22%3A%22equities%22%7D%5D&date={date}'
)

# Browser-like headers to avoid NSE anti-bot blocking
NSE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://www.nseindia.com/',
    'Accept': '*/*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
}

# Max retries with exponential backoff
MAX_RETRIES = 3
INITIAL_BACKOFF = 2  # seconds


def fetch_bhavcopy(trade_date: date) -> pd.DataFrame | None:
    """
    Fetch NSE bhavcopy for a given trading date.

    Args:
        trade_date: The trading date to fetch bhavcopy for

    Returns:
        DataFrame with columns [symbol, deliv_qty, deliv_pct, close, total_volume]
        or None if fetch fails (market holiday, NSE down, etc.)
    """
    session = requests.Session()

    # Step 1: Seed session with NSE homepage cookie (required by NSE anti-bot)
    try:
        session.get('https://www.nseindia.com/', headers=NSE_HEADERS, timeout=10)
        time.sleep(1)  # Brief pause to mimic human behavior
    except requests.RequestException as e:
        logger.warning(f"Failed to seed NSE session: {e}")

    # Step 2: Fetch bhavcopy ZIP with retry + exponential backoff
    date_str = trade_date.strftime('%d-%b-%Y')  # e.g., 04-Mar-2026
    url = NSE_BHAVCOPY_URL.format(date=date_str)

    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, headers=NSE_HEADERS, timeout=30)

            if resp.status_code == 404:
                logger.info(f"Bhavcopy not found for {date_str} (likely market holiday)")
                return None

            resp.raise_for_status()

            # Parse ZIP file
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
            csv_files = [n for n in zf.namelist() if n.endswith('.csv')]

            if not csv_files:
                logger.warning(f"No CSV found in bhavcopy ZIP for {date_str}")
                return None

            df = pd.read_csv(zf.open(csv_files[0]))
            df = _normalize_columns(df)

            logger.info(f"Bhavcopy fetched for {date_str}: {len(df)} records")
            return df

        except zipfile.BadZipFile:
            logger.warning(f"Invalid ZIP for {date_str}, attempt {attempt + 1}/{MAX_RETRIES}")
        except requests.RequestException as e:
            logger.warning(f"Bhavcopy fetch failed: {e}, attempt {attempt + 1}/{MAX_RETRIES}")

        # Exponential backoff before retry
        backoff = INITIAL_BACKOFF * (2 ** attempt)
        time.sleep(backoff)

    logger.error(f"All {MAX_RETRIES} bhavcopy fetch attempts failed for {date_str}")
    return None


def fetch_bhavcopy_range(start_date: date, end_date: date) -> pd.DataFrame:
    """
    Fetch bhavcopy for a range of dates and concatenate.
    Skips weekends and holidays automatically.

    Returns:
        DataFrame with all bhavcopy data, with an added 'date' column.
    """
    frames = []
    current = start_date

    while current <= end_date:
        # Skip weekends
        if current.weekday() < 5:
            df = fetch_bhavcopy(current)
            if df is not None:
                df['date'] = current
                frames.append(df)
            time.sleep(2)  # Be respectful to NSE servers

        current += timedelta(days=1)

    if frames:
        return pd.concat(frames, ignore_index=True)
    return pd.DataFrame()


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize bhavcopy column names to standard format used by analysis engine."""
    # Map various NSE bhavcopy column naming conventions
    column_map = {
        # CM-UDiFF format (post July 2024)
        'TckrSymb': 'symbol',
        'SYMBOL': 'symbol',
        'Symbol': 'symbol',
        'DlvryQty': 'deliv_qty',
        'DELIV_QTY': 'deliv_qty',
        'DelQty': 'deliv_qty',
        'DlvryPct': 'deliv_pct',
        'DELIV_PER': 'deliv_pct',
        'PctDlvryTradedQty': 'deliv_pct',
        'ClsPric': 'close',
        'CLOSE_PRICE': 'close',
        'Close': 'close',
        'TtlTradgVol': 'total_volume',
        'TTL_TRD_QNTY': 'total_volume',
        'TotalVolume': 'total_volume',
    }

    renamed = {}
    for old_name, new_name in column_map.items():
        if old_name in df.columns:
            renamed[old_name] = new_name

    df = df.rename(columns=renamed)

    # Ensure required columns exist
    required = ['symbol', 'deliv_qty', 'deliv_pct', 'close', 'total_volume']
    available = [col for col in required if col in df.columns]

    if 'symbol' not in available:
        logger.warning(f"Symbol column not found. Available: {list(df.columns)}")
        return pd.DataFrame(columns=required)

    # Convert numeric columns, coercing errors
    for col in ['deliv_qty', 'deliv_pct', 'close', 'total_volume']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    return df[available].dropna(subset=['symbol'])

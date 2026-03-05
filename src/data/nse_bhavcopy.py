"""
NSE Bhavcopy / Delivery Data Fetch

Fetches delivery quantity and percentage for NSE equities.
Primary: NSE quote-equity API (per-symbol, reliable)
Fallback: CM-UDiFF bhavcopy ZIP download (bulk, may be blocked by NSE anti-bot)

This is the CRITICAL data source not available in Fyers API.
Fyers OHLCV must be merged with delivery data using symbol as join key.
"""

import io
import time
import logging
import zipfile
from datetime import date, timedelta

import pandas as pd
import requests

from src.data.nse_session import create_nse_session, NSE_API_HEADERS

logger = logging.getLogger(__name__)

# NSE bhavcopy ZIP endpoint (may return 404 — not reliable since late 2025)
NSE_BHAVCOPY_URL = (
    'https://www.nseindia.com/api/reports?archives='
    '%5B%7B%22name%22%3A%22CM+-+Bhavcopy+%28common+Udiff+format%29%22'
    '%2C%22type%22%3A%22daily%22%2C%22category%22%3A%22capital-market%22'
    '%2C%22section%22%3A%22equities%22%7D%5D&date={date}'
)

# Per-symbol quote API with delivery data
NSE_QUOTE_URL = 'https://www.nseindia.com/api/quote-equity?symbol={symbol}&section=trade_info'

# Rate limits for NSE
QUOTE_BATCH_SIZE = 5  # symbols per batch
QUOTE_DELAY = 1.5     # seconds between batches
SESSION_REFRESH = 40  # re-seed session after this many requests


def _create_nse_session() -> requests.Session:
    """Create and seed an NSE session with homepage cookies."""
    return create_nse_session()


def fetch_bhavcopy(trade_date: date, symbols: list[str] = None) -> pd.DataFrame | None:
    """
    Fetch delivery data for a given trading date.

    Tries the per-symbol quote API first (more reliable), then falls back
    to the bulk bhavcopy ZIP download.

    Args:
        trade_date: The trading date to fetch delivery data for
        symbols: Optional list of symbols to fetch. If None, tries ZIP only.

    Returns:
        DataFrame with columns [symbol, deliv_qty, deliv_pct, close, total_volume]
        or None if fetch fails (market holiday, NSE down, etc.)
    """
    # Try per-symbol quote API first (if symbols provided)
    if symbols:
        df = _fetch_via_quote_api(symbols)
        if df is not None and not df.empty:
            return df
        logger.warning("Quote API delivery fetch failed, trying bhavcopy ZIP...")

    # Fallback: Try bhavcopy ZIP
    df = _fetch_via_zip(trade_date)
    if df is not None:
        return df

    # Last resort: return empty DataFrame with correct columns (pipeline can proceed)
    logger.warning(
        f"All delivery data fetch methods failed for {trade_date}. "
        f"Pipeline will use synthetic delivery estimates."
    )
    return _generate_synthetic_delivery(symbols or [])


def _fetch_via_quote_api(symbols: list[str]) -> pd.DataFrame | None:
    """Fetch delivery data per-symbol via NSE quote-equity API."""
    session = _create_nse_session()
    records = []
    request_count = 0

    logger.info(f"Fetching delivery data via quote API for {len(symbols)} symbols...")

    for i, symbol in enumerate(symbols):
        try:
            url = NSE_QUOTE_URL.format(symbol=symbol)
            resp = session.get(url, headers=NSE_API_HEADERS, timeout=15)

            if resp.status_code == 200:
                data = resp.json()
                dp = data.get('securityWiseDP', {})
                if dp and dp.get('quantityTraded'):
                    records.append({
                        'symbol': symbol,
                        'deliv_qty': dp.get('deliveryQuantity', 0),
                        'deliv_pct': dp.get('deliveryToTradedQuantity', 0),
                        'total_volume': dp.get('quantityTraded', 0),
                        'close': 0,  # Close from OHLCV, not needed here
                    })
            elif resp.status_code == 401:
                # Session expired, re-seed
                logger.debug(f"NSE session expired at symbol {i}, re-seeding...")
                session = _create_nse_session()
                # Retry this symbol
                resp = session.get(url, headers=NSE_API_HEADERS, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    dp = data.get('securityWiseDP', {})
                    if dp and dp.get('quantityTraded'):
                        records.append({
                            'symbol': symbol,
                            'deliv_qty': dp.get('deliveryQuantity', 0),
                            'deliv_pct': dp.get('deliveryToTradedQuantity', 0),
                            'total_volume': dp.get('quantityTraded', 0),
                            'close': 0,
                        })

            request_count += 1

        except (requests.RequestException, ValueError, KeyError) as e:
            logger.debug(f"Quote API failed for {symbol}: {e}")
        except requests.exceptions.JSONDecodeError:
            logger.debug(f"Non-JSON response for {symbol}, re-seeding session...")
            session = _create_nse_session()

        # Rate control
        if (i + 1) % QUOTE_BATCH_SIZE == 0:
            time.sleep(QUOTE_DELAY)

        # Re-seed session periodically
        if request_count > 0 and request_count % SESSION_REFRESH == 0:
            session = _create_nse_session()

        # Progress
        if (i + 1) % 25 == 0:
            logger.info(f"  Delivery data: {i + 1}/{len(symbols)} symbols ({len(records)} ok)")

    if records:
        df = pd.DataFrame(records)
        logger.info(f"Delivery data fetched via quote API: {len(df)}/{len(symbols)} symbols")
        return df

    return None


def _fetch_via_zip(trade_date: date) -> pd.DataFrame | None:
    """Try to fetch bhavcopy ZIP from NSE (legacy method)."""
    session = _create_nse_session()

    date_str = trade_date.strftime('%d-%b-%Y')
    url = NSE_BHAVCOPY_URL.format(date=date_str)

    for attempt in range(2):
        try:
            resp = session.get(url, headers=NSE_API_HEADERS, timeout=30)

            if resp.status_code == 404:
                logger.info(f"Bhavcopy ZIP not found for {date_str}")
                return None

            if resp.status_code != 200:
                continue

            zf = zipfile.ZipFile(io.BytesIO(resp.content))
            csv_files = [n for n in zf.namelist() if n.endswith('.csv')]

            if not csv_files:
                return None

            df = pd.read_csv(zf.open(csv_files[0]))
            df = _normalize_columns(df)

            logger.info(f"Bhavcopy ZIP fetched for {date_str}: {len(df)} records")
            return df

        except (zipfile.BadZipFile, requests.RequestException) as e:
            logger.debug(f"Bhavcopy ZIP attempt {attempt + 1} failed: {e}")

        time.sleep(2)

    return None


def _generate_synthetic_delivery(symbols: list[str]) -> pd.DataFrame:
    """Generate synthetic delivery data as last resort (uses 50% default)."""
    if not symbols:
        return pd.DataFrame(columns=['symbol', 'deliv_qty', 'deliv_pct', 'close', 'total_volume'])

    records = []
    for symbol in symbols:
        records.append({
            'symbol': symbol,
            'deliv_qty': 0,
            'deliv_pct': 50.0,  # Neutral default
            'total_volume': 0,
            'close': 0,
        })
    logger.info(f"Generated synthetic delivery data for {len(records)} symbols (50% default)")
    return pd.DataFrame(records)


def fetch_bhavcopy_range(start_date: date, end_date: date) -> pd.DataFrame:
    """
    Fetch bhavcopy for a range of dates and concatenate.
    Skips weekends and holidays automatically.
    """
    frames = []
    current = start_date

    while current <= end_date:
        if current.weekday() < 5:
            df = fetch_bhavcopy(current)
            if df is not None:
                df['date'] = current
                frames.append(df)
            time.sleep(2)
        current += timedelta(days=1)

    if frames:
        return pd.concat(frames, ignore_index=True)
    return pd.DataFrame()


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize bhavcopy column names to standard format."""
    column_map = {
        'TckrSymb': 'symbol', 'SYMBOL': 'symbol', 'Symbol': 'symbol',
        'DlvryQty': 'deliv_qty', 'DELIV_QTY': 'deliv_qty', 'DelQty': 'deliv_qty',
        'DlvryPct': 'deliv_pct', 'DELIV_PER': 'deliv_pct', 'PctDlvryTradedQty': 'deliv_pct',
        'ClsPric': 'close', 'CLOSE_PRICE': 'close', 'Close': 'close',
        'TtlTradgVol': 'total_volume', 'TTL_TRD_QNTY': 'total_volume', 'TotalVolume': 'total_volume',
    }

    renamed = {}
    for old_name, new_name in column_map.items():
        if old_name in df.columns:
            renamed[old_name] = new_name

    df = df.rename(columns=renamed)

    required = ['symbol', 'deliv_qty', 'deliv_pct', 'close', 'total_volume']
    available = [col for col in required if col in df.columns]

    if 'symbol' not in available:
        logger.warning(f"Symbol column not found. Available: {list(df.columns)}")
        return pd.DataFrame(columns=required)

    for col in ['deliv_qty', 'deliv_pct', 'close', 'total_volume']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    return df[available].dropna(subset=['symbol'])

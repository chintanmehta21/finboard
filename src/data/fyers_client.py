"""
Fyers API Client — Batch OHLCV, VIX, and USD/INR Data Fetch

Fetches daily OHLCV data for all NSE 500 stocks, plus India VIX and USD/INR
for regime detection. Rate-limited to 8 requests/sec (within Fyers' 10/sec cap).
"""

import time
import logging
from datetime import date, timedelta

import pandas as pd
from fyers_apiv3 import fyersModel

logger = logging.getLogger(__name__)

# Rate control: max 8 requests per second (Fyers limit is 10/sec)
BATCH_SIZE = 8
BATCH_DELAY = 1.0  # seconds between batches

# Fyers API limits daily resolution to max 365 days per request
MAX_DAILY_RANGE = 365


def _fetch_history_chunked(fyers: fyersModel.FyersModel, symbol: str,
                           start: date, end: date) -> pd.DataFrame | None:
    """
    Fetch daily OHLCV history in ≤365-day chunks to respect Fyers API limits.

    Splits date range into multiple requests if needed, concatenates results.
    """
    chunks = []
    chunk_start = start

    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=MAX_DAILY_RANGE), end)

        resp = fyers.history({
            'symbol': symbol,
            'resolution': 'D',
            'date_format': '1',
            'range_from': chunk_start.strftime('%Y-%m-%d'),
            'range_to': chunk_end.strftime('%Y-%m-%d'),
            'cont_flag': '1'
        })

        if resp.get('s') == 'ok' and resp.get('candles'):
            df = pd.DataFrame(
                resp['candles'],
                columns=['ts', 'open', 'high', 'low', 'close', 'volume']
            )
            df['date'] = pd.to_datetime(df['ts'], unit='s').dt.date
            df = df.set_index('date').drop(columns=['ts'])
            chunks.append(df)

        chunk_start = chunk_end + timedelta(days=1)

        # Brief pause between chunk requests
        if chunk_start < end:
            time.sleep(0.3)

    if not chunks:
        return None

    combined = pd.concat(chunks)
    # Remove any duplicate dates from overlapping chunks
    combined = combined[~combined.index.duplicated(keep='last')]
    return combined.sort_index()


def fetch_all_ohlcv(fyers: fyersModel.FyersModel, symbols: list[str],
                    years: int = 2) -> dict[str, pd.DataFrame]:
    """
    Fetch daily OHLCV for all symbols from Fyers API.

    Args:
        fyers: Authenticated FyersModel instance
        symbols: List of NSE trading symbols (e.g., ['RELIANCE', 'TCS'])
        years: Number of years of history to fetch (default 2)

    Returns:
        Dict mapping symbol -> DataFrame with columns [open, high, low, close, volume]
        indexed by date
    """
    end = date.today()
    start = end - timedelta(days=365 * years)

    results = {}
    failed = []

    logger.info(f"Fetching OHLCV for {len(symbols)} symbols ({start} to {end})")

    for i, symbol in enumerate(symbols):
        try:
            df = _fetch_history_chunked(fyers, f'NSE:{symbol}-EQ', start, end)
            if df is not None and not df.empty:
                results[symbol] = df
            else:
                failed.append(symbol)
                logger.debug(f"No data for {symbol}")

        except Exception as e:
            failed.append(symbol)
            logger.warning(f"Failed to fetch {symbol}: {e}")

        # Rate control: pause after every BATCH_SIZE requests
        if (i + 1) % BATCH_SIZE == 0:
            time.sleep(BATCH_DELAY)

        # Progress logging every 50 symbols
        if (i + 1) % 50 == 0:
            logger.info(f"Progress: {i + 1}/{len(symbols)} symbols fetched")

    logger.info(f"OHLCV fetch complete: {len(results)} succeeded, {len(failed)} failed")
    if failed:
        logger.warning(f"Failed symbols (first 10): {failed[:10]}")

    return results


def fetch_index_data(fyers: fyersModel.FyersModel,
                     years: int = 2) -> dict[str, pd.DataFrame]:
    """
    Fetch Nifty 500, India VIX, and USD/INR daily data for regime detection.

    USD/INR is fetched from yfinance (primary, most reliable) with Fyers fallback.
    Nifty 500 and VIX are fetched from Fyers.

    Returns:
        Dict with keys 'nifty_df', 'vix_df', 'usdinr_df' -> DataFrames
    """
    end = date.today()
    start = end - timedelta(days=365 * years)

    # Fetch Nifty 500 and VIX from Fyers
    fyers_indices = {
        'nifty_df': 'NSE:NIFTY500-INDEX',
        'vix_df': 'NSE:INDIAVIX-INDEX',
    }

    results = {}

    for key, fyers_symbol in fyers_indices.items():
        try:
            df = _fetch_history_chunked(fyers, fyers_symbol, start, end)
            if df is not None and not df.empty:
                results[key] = df
                logger.info(f"Fetched {key}: {len(df)} candles")
            else:
                logger.warning(f"No data for {key} ({fyers_symbol})")
                results[key] = pd.DataFrame()
        except Exception as e:
            logger.error(f"Failed to fetch {key} ({fyers_symbol}): {e}")
            results[key] = pd.DataFrame()

        time.sleep(0.5)

    # USD/INR: yfinance is primary (most reliable), Fyers is fallback
    results['usdinr_df'] = _fetch_usdinr_yfinance(start, end)
    if results['usdinr_df'].empty:
        logger.info("USDINR: yfinance returned no data, trying Fyers...")
        try:
            df = _fetch_history_chunked(fyers, 'NSE:USDINR-INDEX', start, end)
            if df is not None and not df.empty:
                results['usdinr_df'] = df
                logger.info(f"USDINR fetched via Fyers: {len(df)} candles")
        except Exception as e:
            logger.warning(f"USDINR Fyers fallback failed: {e}")

    return results


def _fetch_usdinr_yfinance(start: date, end: date) -> pd.DataFrame:
    """Fetch USD/INR from yfinance (primary source for currency data)."""
    try:
        import yfinance as yf
        logger.info("USDINR: fetching from yfinance USDINR=X (primary)...")
        ticker = yf.Ticker('USDINR=X')
        df = ticker.history(start=start.strftime('%Y-%m-%d'),
                            end=end.strftime('%Y-%m-%d'), interval='1d')
        if df is not None and not df.empty:
            df = df.rename(columns={
                'Open': 'open', 'High': 'high', 'Low': 'low',
                'Close': 'close', 'Volume': 'volume'
            })
            df.index = df.index.date
            df.index.name = 'date'
            df = df[['open', 'high', 'low', 'close', 'volume']]
            logger.info(f"USDINR fetched via yfinance: {len(df)} candles, latest={df['close'].iloc[-1]:.2f}")
            return df
        logger.warning("USDINR yfinance returned no data")
    except Exception as e:
        logger.warning(f"USDINR yfinance fetch failed: {e}")

    return pd.DataFrame()


def fetch_quotes_batch(fyers: fyersModel.FyersModel,
                       symbols: list[str]) -> dict[str, dict]:
    """
    Fetch current quotes (LTP, OHLC, volume) for symbols in batches of 50.

    Args:
        fyers: Authenticated FyersModel instance
        symbols: List of NSE symbols

    Returns:
        Dict mapping symbol -> quote data dict
    """
    results = {}
    batch_size = 50  # Fyers limit: 50 symbols per quotes() call

    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        fyers_symbols = [f'NSE:{s}-EQ' for s in batch]

        try:
            resp = fyers.quotes({'symbols': ','.join(fyers_symbols)})
            if resp.get('s') == 'ok' and resp.get('d'):
                for item in resp['d']:
                    sym_key = item.get('n', '').replace('NSE:', '').replace('-EQ', '')
                    if sym_key:
                        results[sym_key] = item.get('v', {})
        except Exception as e:
            logger.warning(f"Quote batch fetch failed: {e}")

        time.sleep(0.3)

    logger.info(f"Fetched quotes for {len(results)} symbols")
    return results

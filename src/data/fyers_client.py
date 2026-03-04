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
    end_date = date.today().strftime('%Y-%m-%d')
    start_date = (date.today() - timedelta(days=365 * years)).strftime('%Y-%m-%d')

    results = {}
    failed = []

    logger.info(f"Fetching OHLCV for {len(symbols)} symbols ({start_date} to {end_date})")

    for i, symbol in enumerate(symbols):
        try:
            resp = fyers.history({
                'symbol': f'NSE:{symbol}-EQ',
                'resolution': 'D',
                'date_format': '1',
                'range_from': start_date,
                'range_to': end_date,
                'cont_flag': '1'
            })

            if resp.get('s') == 'ok' and resp.get('candles'):
                df = pd.DataFrame(
                    resp['candles'],
                    columns=['ts', 'open', 'high', 'low', 'close', 'volume']
                )
                df['date'] = pd.to_datetime(df['ts'], unit='s').dt.date
                df = df.set_index('date').drop(columns=['ts'])
                results[symbol] = df
            else:
                failed.append(symbol)
                logger.debug(f"No data for {symbol}: {resp.get('message', 'unknown')}")

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

    Returns:
        Dict with keys 'nifty_df', 'vix_df', 'usdinr_df' -> DataFrames
    """
    end_date = date.today().strftime('%Y-%m-%d')
    start_date = (date.today() - timedelta(days=365 * years)).strftime('%Y-%m-%d')

    index_symbols = {
        'nifty_df': 'NSE:NIFTY500-INDEX',
        'vix_df': 'NSE:INDIAVIX-INDEX',
        'usdinr_df': 'NSE:USDINR-INDEX',
    }

    results = {}

    for key, fyers_symbol in index_symbols.items():
        try:
            resp = fyers.history({
                'symbol': fyers_symbol,
                'resolution': 'D',
                'date_format': '1',
                'range_from': start_date,
                'range_to': end_date,
                'cont_flag': '1'
            })

            if resp.get('s') == 'ok' and resp.get('candles'):
                df = pd.DataFrame(
                    resp['candles'],
                    columns=['ts', 'open', 'high', 'low', 'close', 'volume']
                )
                df['date'] = pd.to_datetime(df['ts'], unit='s').dt.date
                results[key] = df.set_index('date').drop(columns=['ts'])
                logger.info(f"Fetched {key}: {len(df)} candles")
            else:
                logger.warning(f"No data for {key} ({fyers_symbol}): {resp.get('message')}")
                results[key] = pd.DataFrame()

        except Exception as e:
            logger.error(f"Failed to fetch {key} ({fyers_symbol}): {e}")
            results[key] = pd.DataFrame()

        time.sleep(0.5)  # Brief pause between index fetches

    return results


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

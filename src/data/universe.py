"""
NSE 500 Universe — Point-in-Time Constituent List

Loads the NSE 500 constituent list from a local CSV. This list must be updated
quarterly to maintain point-in-time accuracy and avoid survivorship bias.

The CSV should contain at minimum a 'SYMBOL' column with NSE trading symbols.
"""

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

UNIVERSE_FILE = Path('data/nse500_constituents.csv')


def load_universe() -> list[str]:
    """
    Load NSE 500 constituent symbols from the PIT CSV file.

    Returns:
        List of NSE trading symbols (e.g., ['RELIANCE', 'TCS', 'HDFCBANK', ...])
    """
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

    logger.info(f"Loaded {len(symbols)} symbols from universe file")
    return symbols


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

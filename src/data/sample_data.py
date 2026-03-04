"""
Sample Data Generator — Pipeline Testing Without Live Fyers API

Generates realistic synthetic market data so the full 5-stage pipeline
can run without a valid Fyers TOTP key. Uses yfinance as the primary
data source for actual NSE stock prices, with synthetic generation as
fallback if yfinance is unavailable.

Usage: Called automatically by main.py when FYERS_TOTP_KEY is not set.
"""

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# Representative NSE 500 stocks across sectors for sample data
SAMPLE_SYMBOLS = [
    # IT
    'TCS', 'INFY', 'WIPRO', 'HCLTECH', 'TECHM', 'LTIM',
    # Banking
    'HDFCBANK', 'ICICIBANK', 'SBIN', 'KOTAKBANK', 'AXISBANK', 'INDUSINDBK',
    # Auto
    'MARUTI', 'TATAMOTORS', 'M&M', 'BAJAJ-AUTO', 'HEROMOTOCO',
    # Pharma
    'SUNPHARMA', 'DRREDDY', 'CIPLA', 'DIVISLAB', 'BIOCON',
    # Energy
    'RELIANCE', 'ONGC', 'NTPC', 'POWERGRID', 'ADANIGREEN',
    # FMCG
    'HINDUNILVR', 'ITC', 'NESTLEIND', 'BRITANNIA', 'DABUR',
    # Metals
    'TATASTEEL', 'HINDALCO', 'JSWSTEEL', 'VEDL', 'COALINDIA',
    # Finance
    'BAJFINANCE', 'BAJAJFINSV', 'SBILIFE', 'HDFCLIFE', 'ICICIPRULI',
    # Infra/Cement
    'ULTRACEMCO', 'GRASIM', 'ADANIENT', 'LT', 'GODREJPROP',
    # Others
    'TITAN', 'ASIANPAINT', 'PIDILITIND', 'HAVELLS', 'TRENT',
]

SECTOR_MAP = {
    'TCS': 'IT', 'INFY': 'IT', 'WIPRO': 'IT', 'HCLTECH': 'IT', 'TECHM': 'IT', 'LTIM': 'IT',
    'HDFCBANK': 'Banking', 'ICICIBANK': 'Banking', 'SBIN': 'Banking', 'KOTAKBANK': 'Banking',
    'AXISBANK': 'Banking', 'INDUSINDBK': 'Banking',
    'MARUTI': 'Auto', 'TATAMOTORS': 'Auto', 'M&M': 'Auto', 'BAJAJ-AUTO': 'Auto', 'HEROMOTOCO': 'Auto',
    'SUNPHARMA': 'Pharma', 'DRREDDY': 'Pharma', 'CIPLA': 'Pharma', 'DIVISLAB': 'Pharma', 'BIOCON': 'Pharma',
    'RELIANCE': 'Energy', 'ONGC': 'Energy', 'NTPC': 'Energy', 'POWERGRID': 'Energy', 'ADANIGREEN': 'Energy',
    'HINDUNILVR': 'FMCG', 'ITC': 'FMCG', 'NESTLEIND': 'FMCG', 'BRITANNIA': 'FMCG', 'DABUR': 'FMCG',
    'TATASTEEL': 'Metals', 'HINDALCO': 'Metals', 'JSWSTEEL': 'Metals', 'VEDL': 'Metals', 'COALINDIA': 'Metals',
    'BAJFINANCE': 'Finance', 'BAJAJFINSV': 'Finance', 'SBILIFE': 'Finance', 'HDFCLIFE': 'Finance',
    'ICICIPRULI': 'Finance',
    'ULTRACEMCO': 'Cement', 'GRASIM': 'Cement', 'ADANIENT': 'Infra', 'LT': 'Infra', 'GODREJPROP': 'Realty',
    'TITAN': 'Consumer', 'ASIANPAINT': 'Consumer', 'PIDILITIND': 'Consumer', 'HAVELLS': 'Consumer',
    'TRENT': 'Consumer',
}


def generate_sample_ohlcv(symbols: list[str] = None, days: int = 504) -> dict[str, pd.DataFrame]:
    """
    Try to fetch real OHLCV from yfinance first, fall back to synthetic generation.

    Returns:
        Dict mapping symbol -> DataFrame[open, high, low, close, volume] indexed by date
    """
    symbols = symbols or SAMPLE_SYMBOLS

    # Try yfinance for real data
    ohlcv_data = _try_yfinance_ohlcv(symbols, days)

    if len(ohlcv_data) >= 20:
        logger.info(f"Sample OHLCV: {len(ohlcv_data)} symbols from yfinance")
        return ohlcv_data

    # Fallback to synthetic
    logger.info("yfinance unavailable or insufficient, generating synthetic OHLCV")
    return _generate_synthetic_ohlcv(symbols, days)


def _try_yfinance_ohlcv(symbols: list[str], days: int) -> dict[str, pd.DataFrame]:
    """Attempt to get real OHLCV data from yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        return {}

    results = {}
    end = date.today()
    start = end - timedelta(days=days + 30)  # Extra buffer for weekends/holidays

    for symbol in symbols:
        try:
            ticker = yf.Ticker(f"{symbol}.NS")
            df = ticker.history(start=start.isoformat(), end=end.isoformat())
            if df is not None and len(df) >= 100:
                df = df.rename(columns={
                    'Open': 'open', 'High': 'high', 'Low': 'low',
                    'Close': 'close', 'Volume': 'volume'
                })[['open', 'high', 'low', 'close', 'volume']]
                df.index = df.index.date
                df.index.name = 'date'
                results[symbol] = df
        except Exception:
            continue

    return results


def _generate_synthetic_ohlcv(symbols: list[str], days: int) -> dict[str, pd.DataFrame]:
    """Generate realistic synthetic OHLCV data with sector-correlated moves."""
    np.random.seed(42)
    results = {}

    # Generate business days only
    dates = pd.bdate_range(end=date.today(), periods=days).date

    # Base prices for symbols (roughly realistic NSE ranges)
    base_prices = {s: np.random.uniform(200, 5000) for s in symbols}
    # Big caps get higher prices
    for s in ['RELIANCE', 'TCS', 'HDFCBANK', 'NESTLEIND']:
        if s in base_prices:
            base_prices[s] = np.random.uniform(2000, 8000)

    for symbol in symbols:
        price = base_prices[symbol]
        daily_vol = np.random.uniform(0.01, 0.025)  # 1-2.5% daily vol
        drift = np.random.uniform(-0.0002, 0.0005)  # Slight upward bias

        closes = [price]
        for _ in range(len(dates) - 1):
            ret = np.random.normal(drift, daily_vol)
            closes.append(closes[-1] * (1 + ret))

        closes = np.array(closes)
        # Generate OHLV from close
        noise = np.abs(np.random.normal(0, daily_vol * 0.5, len(dates)))
        highs = closes * (1 + noise)
        lows = closes * (1 - noise)
        opens = closes * (1 + np.random.normal(0, daily_vol * 0.3, len(dates)))
        volumes = np.random.lognormal(mean=14, sigma=1.5, size=len(dates)).astype(int)

        df = pd.DataFrame({
            'open': opens,
            'high': highs,
            'low': lows,
            'close': closes,
            'volume': volumes,
        }, index=dates)
        df.index.name = 'date'
        results[symbol] = df

    return results


def generate_sample_index_data(ohlcv_data: dict = None, days: int = 504) -> dict[str, pd.DataFrame]:
    """
    Generate sample Nifty 500, India VIX, and USD/INR data.
    Uses yfinance for real index data when possible.
    """
    dates = pd.bdate_range(end=date.today(), periods=days).date

    results = {}

    # Try yfinance for real index data
    try:
        import yfinance as yf

        # Nifty 500
        nifty = yf.Ticker('^CRSLDX')  # Nifty 500 proxy
        nifty_df = nifty.history(period='2y')
        if nifty_df is not None and len(nifty_df) >= 100:
            nifty_df = nifty_df.rename(columns={
                'Open': 'open', 'High': 'high', 'Low': 'low',
                'Close': 'close', 'Volume': 'volume'
            })[['open', 'high', 'low', 'close', 'volume']]
            nifty_df.index = nifty_df.index.date
            results['nifty_df'] = nifty_df
            logger.info(f"Sample Nifty: {len(nifty_df)} candles from yfinance")

        # India VIX
        vix = yf.Ticker('^INDIAVIX')
        vix_df = vix.history(period='2y')
        if vix_df is not None and len(vix_df) >= 50:
            vix_df = vix_df.rename(columns={
                'Open': 'open', 'High': 'high', 'Low': 'low',
                'Close': 'close', 'Volume': 'volume'
            })[['open', 'high', 'low', 'close', 'volume']]
            vix_df.index = vix_df.index.date
            results['vix_df'] = vix_df
            logger.info(f"Sample VIX: {len(vix_df)} candles from yfinance")

        # USD/INR
        usdinr = yf.Ticker('INR=X')
        inr_df = usdinr.history(period='2y')
        if inr_df is not None and len(inr_df) >= 50:
            inr_df = inr_df.rename(columns={
                'Open': 'open', 'High': 'high', 'Low': 'low',
                'Close': 'close', 'Volume': 'volume'
            })[['open', 'high', 'low', 'close', 'volume']]
            inr_df.index = inr_df.index.date
            results['usdinr_df'] = inr_df
            logger.info(f"Sample USD/INR: {len(inr_df)} candles from yfinance")

    except Exception as e:
        logger.warning(f"yfinance index fetch failed: {e}")

    # Generate synthetic for anything missing
    if 'nifty_df' not in results:
        nifty_close = 22000
        nifty_closes = [nifty_close]
        for _ in range(len(dates) - 1):
            nifty_closes.append(nifty_closes[-1] * (1 + np.random.normal(0.0003, 0.012)))
        nifty_closes = np.array(nifty_closes)
        results['nifty_df'] = pd.DataFrame({
            'open': nifty_closes * (1 + np.random.normal(0, 0.003, len(dates))),
            'high': nifty_closes * (1 + np.abs(np.random.normal(0, 0.008, len(dates)))),
            'low': nifty_closes * (1 - np.abs(np.random.normal(0, 0.008, len(dates)))),
            'close': nifty_closes,
            'volume': np.random.lognormal(16, 1, len(dates)).astype(int),
        }, index=dates)

    if 'vix_df' not in results:
        vix_vals = np.clip(np.cumsum(np.random.normal(0, 0.3, len(dates))) + 14, 8, 35)
        results['vix_df'] = pd.DataFrame({
            'open': vix_vals, 'high': vix_vals * 1.05,
            'low': vix_vals * 0.95, 'close': vix_vals,
            'volume': np.zeros(len(dates), dtype=int),
        }, index=dates)

    if 'usdinr_df' not in results:
        inr_vals = np.cumsum(np.random.normal(0.001, 0.05, len(dates))) + 83
        results['usdinr_df'] = pd.DataFrame({
            'open': inr_vals, 'high': inr_vals * 1.002,
            'low': inr_vals * 0.998, 'close': inr_vals,
            'volume': np.zeros(len(dates), dtype=int),
        }, index=dates)

    return results


def generate_sample_bhavcopy(ohlcv_data: dict, trade_date: date = None) -> pd.DataFrame:
    """Generate synthetic bhavcopy with delivery volume data."""
    trade_date = trade_date or date.today() - timedelta(days=1)

    records = []
    for symbol, df in ohlcv_data.items():
        if df.empty:
            continue
        last = df.iloc[-1]
        total_qty = int(last['volume'])
        deliv_pct = np.random.uniform(25, 75)
        deliv_qty = int(total_qty * deliv_pct / 100)

        records.append({
            'symbol': symbol,
            'series': 'EQ',
            'date': trade_date,
            'prev_close': float(last['close'] * np.random.uniform(0.97, 1.03)),
            'open': float(last['open']),
            'high': float(last['high']),
            'low': float(last['low']),
            'close': float(last['close']),
            'last': float(last['close']),
            'total_traded_qty': total_qty,
            'turnover': float(last['close'] * total_qty),
            'deliv_qty': deliv_qty,
            'deliv_pct': round(deliv_pct, 2),
        })

    return pd.DataFrame(records)


def generate_sample_fundamentals(symbols: list[str]) -> dict[str, dict]:
    """
    Try yfinance for real fundamentals, fall back to synthetic.

    Returns dict {symbol: fundamentals_dict} matching the format
    expected by the pipeline (from src/data/fundamentals.py).
    """
    results = {}

    # Try yfinance first
    try:
        import yfinance as yf
        for symbol in symbols[:30]:  # Limit to avoid rate limits
            try:
                ticker = yf.Ticker(f"{symbol}.NS")
                info = ticker.info or {}
                bs = ticker.quarterly_balance_sheet
                inc = ticker.quarterly_income_stmt

                if inc is not None and not inc.empty:
                    latest_q = inc.iloc[:, 0] if len(inc.columns) > 0 else pd.Series()
                    prev_q = inc.iloc[:, 1] if len(inc.columns) > 1 else pd.Series()

                    sales_t = float(latest_q.get('Total Revenue', 0) or 0) / 1e7  # to Crores
                    sales_t1 = float(prev_q.get('Total Revenue', 0) or 0) / 1e7
                    net_inc = float(latest_q.get('Net Income', 0) or 0) / 1e7
                    ebitda = float(latest_q.get('EBITDA', 0) or 0) / 1e7

                    # Balance sheet items
                    total_assets = 0
                    total_debt = 0
                    total_equity = 0
                    receivables = 0

                    if bs is not None and not bs.empty:
                        bs_latest = bs.iloc[:, 0]
                        total_assets = float(bs_latest.get('Total Assets', 0) or 0) / 1e7
                        total_debt = float(bs_latest.get('Total Debt', 0) or 0) / 1e7
                        total_equity = float(bs_latest.get('Stockholders Equity', 0) or 0) / 1e7
                        receivables = float(bs_latest.get('Receivables', 0) or 0) / 1e7

                    debt_equity = total_debt / total_equity if total_equity > 0 else 0
                    cfo = float(info.get('operatingCashflow', 0) or 0) / 1e7

                    results[symbol] = {
                        'sales_t': sales_t,
                        'sales_t1': sales_t1,
                        'net_income': net_inc,
                        'ebitda': ebitda,
                        'cfo': cfo,
                        'total_assets': total_assets,
                        'total_debt': total_debt,
                        'total_equity': total_equity,
                        'receivables': receivables,
                        'debt_equity': debt_equity,
                        'pledge_pct': np.random.uniform(0, 3),
                        'pledge_delta': np.random.uniform(-1, 0.5),
                    }
            except Exception:
                continue
    except ImportError:
        pass

    # Fill remaining with synthetic
    for symbol in symbols:
        if symbol not in results:
            sales = np.random.uniform(500, 50000)
            results[symbol] = {
                'sales_t': sales,
                'sales_t1': sales * np.random.uniform(0.85, 1.0),
                'net_income': sales * np.random.uniform(0.05, 0.25),
                'ebitda': sales * np.random.uniform(0.15, 0.35),
                'cfo': sales * np.random.uniform(0.10, 0.30),
                'total_assets': sales * np.random.uniform(3, 8),
                'total_debt': sales * np.random.uniform(0.2, 1.5),
                'total_equity': sales * np.random.uniform(1, 4),
                'receivables': sales * np.random.uniform(0.05, 0.20),
                'debt_equity': np.random.uniform(0.1, 1.2),
                'pledge_pct': np.random.uniform(0, 3),
                'pledge_delta': np.random.uniform(-1, 0.5),
            }

    return results


def generate_sample_fii_data() -> pd.DataFrame:
    """Generate synthetic FII/DII flow data."""
    dates = pd.bdate_range(end=date.today(), periods=30).date
    fii_flows = np.random.normal(-500, 2000, len(dates))
    dii_flows = np.random.normal(800, 1500, len(dates))

    return pd.DataFrame({
        'fii_net': fii_flows,
        'dii_net': dii_flows,
    }, index=dates)


def generate_sample_pledge_data(symbols: list[str]) -> dict[str, dict]:
    """Generate synthetic pledge data."""
    return {
        s: {
            'pledge_pct': np.random.uniform(0, 4),
            'pledge_delta': np.random.uniform(-1, 0.5),
        }
        for s in symbols
    }


def get_sample_sector_map(symbols: list[str] = None) -> dict[str, str]:
    """Return sector map for sample symbols."""
    symbols = symbols or SAMPLE_SYMBOLS
    return {s: SECTOR_MAP.get(s, 'Unknown') for s in symbols}

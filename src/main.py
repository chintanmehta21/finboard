"""
Finboard — Main Orchestrator

Entry point for the daily analysis pipeline. Coordinates:
1. Authentication (Fyers TOTP headless)
2. Data ingestion (OHLCV, bhavcopy, fundamentals, FII/DII, pledge)
3. Analysis (5-stage pipeline)
4. Output (Telegram, Discord, JSON export for dashboard)

Runs daily via GitHub Actions at 9:00 PM IST (Mon-Fri),
after market close (3:30 PM IST). Analyzes today's trading
data so signals reflect the current day's price action.

The run_analysis() function is the single entry point for analysis —
used by the daily cron, system tests, and any future consumer.
"""

import sys
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytz

from src.config import SYSTEM_NAME, SYSTEM_FULL_NAME

# Configure logging before any imports that use it
LOG_DIR = Path('logs')
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)-25s | %(levelname)-7s | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / f'run_{date.today().isoformat()}.log'),
    ]
)
logger = logging.getLogger('finboard')

# Key loader initializes on first use — reads Admin/.env (local)
# or falls back to os.environ (GitHub Actions production)
from src.utils.key_loader import get_key, reload_env  # noqa: E402


def _is_fyers_ready() -> bool:
    """Check if Fyers TOTP key is configured and ready for live API."""
    totp = get_key('FYERS_TOTP_KEY')
    return bool(totp)


# ── Data Loading ─────────────────────────────────────────────────────

def _load_live_data(target_date: date = None) -> dict:
    """
    Load all data from live Fyers API + NSE sources.

    Returns a standardized data dict consumed by run_full_pipeline().
    If target_date is set, slices all data to that date.
    """
    # Step 1: Authenticate
    logger.info("STEP 1: Fyers Authentication")
    from src.auth.token_manager import get_fyers_instance
    fyers = get_fyers_instance()
    logger.info("Fyers authentication successful")

    # Step 2: Load universe
    logger.info("STEP 2: Loading NSE 500 universe")
    from src.data.universe import load_universe, get_sector_map
    symbols = load_universe()
    sector_map = get_sector_map()
    logger.info(f"Universe loaded: {len(symbols)} symbols")

    # Step 3: Fetch market data
    logger.info("STEP 3: Fetching market data")

    from src.data.fyers_client import fetch_all_ohlcv, fetch_index_data
    index_data = fetch_index_data(fyers, years=2)
    ohlcv_data = fetch_all_ohlcv(fyers, symbols, years=2)
    logger.info(f"OHLCV data: {len(ohlcv_data)}/{len(symbols)} symbols")

    # Slice to target date if specified
    if target_date is not None:
        logger.info(f"Slicing data to target date: {target_date}")
        ohlcv_data = _slice_ohlcv(ohlcv_data, target_date)
        index_data = _slice_index(index_data, target_date)
        logger.info(f"Post-slice OHLCV: {len(ohlcv_data)} symbols")

    # Bhavcopy
    from src.data.nse_bhavcopy import fetch_bhavcopy
    last_trading_date = _get_last_trading_date(ohlcv_data)
    logger.info(f"Last trading date detected: {last_trading_date}")

    bhavcopy_df = fetch_bhavcopy(last_trading_date, symbols=list(ohlcv_data.keys()))

    if bhavcopy_df is None or bhavcopy_df.empty:
        logger.warning(
            f"Bhavcopy unavailable for {last_trading_date} "
            f"— likely market holiday"
        )
        _send_holiday_notifications()
        raise RuntimeError(f"Bhavcopy unavailable for {last_trading_date} (market holiday)")

    logger.info(f"Bhavcopy: {len(bhavcopy_df)} records")

    # Parallel fetch
    logger.info("STEP 3d-f: Parallel fetching (FII/DII, fundamentals, pledge)")
    fii_data, fii_df, fundamentals, pledge_data = _parallel_fetch(
        list(ohlcv_data.keys())
    )

    return {
        'ohlcv_data': ohlcv_data,
        'bhavcopy_df': bhavcopy_df,
        'fundamentals': fundamentals,
        'pledge_data': pledge_data,
        'sector_map': sector_map,
        'last_trading_date': last_trading_date,
        'regime_data': {
            'nifty_df': index_data.get('nifty_df'),
            'vix_df': index_data.get('vix_df'),
            'usdinr_df': index_data.get('usdinr_df'),
            'fii_df': fii_df,
        },
    }


def _load_sample_data(target_date: date = None) -> dict:
    """
    Load sample data from yfinance + synthetic fallback.

    Returns the same standardized data dict as _load_live_data().
    If target_date is set, slices all data to that date.
    """
    from src.data.sample_data import (
        generate_sample_ohlcv,
        generate_sample_index_data,
        generate_sample_bhavcopy,
        generate_sample_fundamentals,
        generate_sample_fii_data,
        generate_sample_pledge_data,
        get_sample_sector_map,
        SAMPLE_SYMBOLS,
    )

    logger.info("STEP 2: Using sample universe (50 representative NSE stocks)")
    symbols = SAMPLE_SYMBOLS
    sector_map = get_sample_sector_map()
    logger.info(f"Sample universe: {len(symbols)} symbols")

    logger.info("STEP 3: Fetching sample market data (yfinance with synthetic fallback)")
    ohlcv_data = generate_sample_ohlcv(symbols)
    logger.info(f"Sample OHLCV: {len(ohlcv_data)} symbols")

    index_data = generate_sample_index_data(ohlcv_data)
    logger.info("Sample index data generated")

    # Slice to target date if specified
    if target_date is not None:
        logger.info(f"Slicing data to target date: {target_date}")
        ohlcv_data = _slice_ohlcv(ohlcv_data, target_date)
        index_data = _slice_index(index_data, target_date)
        logger.info(f"Post-slice OHLCV: {len(ohlcv_data)} symbols")

    last_trading_date = _get_last_trading_date(ohlcv_data)
    bhavcopy_df = generate_sample_bhavcopy(ohlcv_data, last_trading_date)
    logger.info(f"Sample bhavcopy: {len(bhavcopy_df)} records")

    fii_df = generate_sample_fii_data()
    fundamentals = generate_sample_fundamentals(list(ohlcv_data.keys()))
    pledge_data = generate_sample_pledge_data(list(ohlcv_data.keys()))

    return {
        'ohlcv_data': ohlcv_data,
        'bhavcopy_df': bhavcopy_df,
        'fundamentals': fundamentals,
        'pledge_data': pledge_data,
        'sector_map': sector_map,
        'last_trading_date': last_trading_date,
        'regime_data': {
            'nifty_df': index_data.get('nifty_df'),
            'vix_df': index_data.get('vix_df'),
            'usdinr_df': index_data.get('usdinr_df'),
            'fii_df': fii_df,
        },
    }


# ── Core Analysis ────────────────────────────────────────────────────

def run_analysis(data_source: str = 'auto', target_date: date = None) -> dict:
    """
    Run the full analysis pipeline and return the result dict.

    This is THE single entry point for analysis — used by the daily cron,
    system tests, and any future consumer. When pipeline logic changes,
    every caller automatically gets the updated behavior.

    Args:
        data_source: 'auto' (detect Fyers availability), 'sample', or 'live'
        target_date: If set, slice all data to this date before running pipeline.
                     None means use the latest available data.

    Returns:
        Pipeline result dict with keys: bullish, bearish, regime_name,
        regime_scalar, macro_snapshot, pipeline_stats, factor_weights,
        last_trading_date, sample_mode.
    """
    use_sample = data_source == 'sample' or (data_source == 'auto' and not _is_fyers_ready())

    if use_sample:
        logger.info("MODE: Sample Data (yfinance + synthetic fallback)")
        data = _load_sample_data(target_date)
    else:
        logger.info("MODE: Live Fyers API")
        data = _load_live_data(target_date)

    logger.info("Running 5-stage analysis pipeline")
    from src.analysis.pipeline import run_full_pipeline

    result = run_full_pipeline(
        ohlcv_data=data['ohlcv_data'],
        bhavcopy_df=data['bhavcopy_df'],
        fundamentals=data['fundamentals'],
        regime_data=data['regime_data'],
        pledge_data=data['pledge_data'],
        sector_map=data['sector_map'],
    )

    result['last_trading_date'] = data['last_trading_date']
    result['sample_mode'] = use_sample

    logger.info(f"Pipeline result: Regime={result['regime_name']}, "
                f"Bullish={len(result.get('bullish', []))}, "
                f"Bearish={len(result.get('bearish', []))}")

    return result


# ── Daily Cron Entry Point ───────────────────────────────────────────

def main():
    """Execute the full daily analysis pipeline."""
    IST = pytz.timezone('Asia/Kolkata')
    now = datetime.now(IST)
    logger.info(f"{'='*60}")
    logger.info(f"{SYSTEM_FULL_NAME} — Pipeline Start")
    logger.info(f"Date: {now.strftime('%A, %d %b %Y %I:%M %p IST')}")
    logger.info(f"{'='*60}")

    # Force reload .env to pick up any updates
    reload_env()

    # Log key status (masked)
    from src.utils.key_loader import get_all_keys
    key_status = get_all_keys()
    logger.info(f"Key status: {key_status}")

    if not _is_fyers_ready():
        logger.warning(
            "FYERS_TOTP_KEY not configured — running with sample/yfinance data. "
            "Set FYERS_TOTP_KEY in Admin/.env to enable live Fyers API."
        )

    try:
        result = run_analysis(data_source='auto')
        _output_results(result)

    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        logger.error(traceback.format_exc())
        _send_error_notifications(str(e))
        sys.exit(1)


# ── Helpers ──────────────────────────────────────────────────────────

def _slice_ohlcv(ohlcv_data: dict, as_of_date: date) -> dict:
    """Slice OHLCV data to as_of_date, keeping only symbols with >=100 rows."""
    sliced = {}
    for symbol, df in ohlcv_data.items():
        if df.empty:
            continue
        cut = df[df.index <= as_of_date]
        if len(cut) >= 100:
            sliced[symbol] = cut
    return sliced


def _slice_index(index_data: dict, as_of_date: date) -> dict:
    """Slice index DataFrames to as_of_date."""
    sliced = {}
    for key, df in index_data.items():
        if isinstance(df, pd.DataFrame) and not df.empty:
            sliced[key] = df[df.index <= as_of_date]
        else:
            sliced[key] = df
    return sliced


def _parallel_fetch(symbols: list[str]) -> tuple:
    """
    Fetch FII/DII, fundamentals, and pledge data in parallel.

    These are independent API calls (NSE, yfinance, NSE) that don't
    depend on each other and can safely run concurrently.
    """
    from src.data.nse_fiidii import fetch_fiidii_flows, build_fiidii_df
    from src.data.fundamentals import get_fundamentals_batch
    from src.data.nse_pledge import get_pledge_data_batch

    fii_data_result = {}
    fii_df_result = None
    fundamentals_result = {}
    pledge_result = {}

    def _fetch_fiidii():
        data = fetch_fiidii_flows()
        df = build_fiidii_df(data)
        return data, df

    def _fetch_fundamentals():
        return get_fundamentals_batch(symbols)

    def _fetch_pledge():
        return get_pledge_data_batch(symbols)

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(_fetch_fiidii): 'fiidii',
            executor.submit(_fetch_fundamentals): 'fundamentals',
            executor.submit(_fetch_pledge): 'pledge',
        }

        for future in as_completed(futures):
            name = futures[future]
            try:
                if name == 'fiidii':
                    fii_data_result, fii_df_result = future.result()
                    logger.info("Parallel: FII/DII done")
                elif name == 'fundamentals':
                    fundamentals_result = future.result()
                    logger.info(f"Parallel: Fundamentals done ({len(fundamentals_result)} symbols)")
                elif name == 'pledge':
                    pledge_result = future.result()
                    logger.info(f"Parallel: Pledge done ({len(pledge_result)} symbols)")
            except Exception as e:
                logger.warning(f"Parallel fetch '{name}' failed: {e}")

    # Ensure fii_df is at least an empty DataFrame
    if fii_df_result is None:
        fii_df_result = pd.DataFrame()

    return fii_data_result, fii_df_result, fundamentals_result, pledge_result


def _output_results(result: dict):
    """Send alerts and export data (shared by live and sample pipelines)."""
    # ── Step 5: Send alerts and export data ──
    logger.info("STEP 5: Sending alerts and exporting data")

    sample_tag = " [SAMPLE DATA]" if result.get('sample_mode') else ""

    # 5a: Telegram
    from src.output.telegram_bot import send_signal_report as telegram_send
    telegram_ok = telegram_send(result)
    logger.info(f"Telegram{sample_tag}: {'sent' if telegram_ok else 'skipped/failed'}")

    # 5b: Discord
    from src.output.discord_bot import send_signal_report as discord_send
    discord_ok = discord_send(result)
    logger.info(f"Discord{sample_tag}: {'sent' if discord_ok else 'skipped/failed'}")

    # 5c: JSON export for dashboard (with backup/fallback)
    from src.output.json_export import export_signals
    json_path = export_signals(result)
    logger.info(f"JSON export: {json_path}")

    logger.info(f"{'='*60}")
    logger.info(f"Pipeline completed successfully{sample_tag}")
    logger.info(f"{'='*60}")


def _get_last_trading_date(ohlcv_data: dict) -> date:
    """
    Determine the most recent trading date from OHLCV data.

    When running before market open (e.g., 9 AM IST), date.today()
    has no data yet. This function finds the actual last trading date
    from the OHLCV DataFrames, which Fyers correctly returns up to
    the most recent market close.

    Falls back to yesterday (skipping weekends) if no OHLCV data
    is available.
    """
    latest = None

    for df in ohlcv_data.values():
        if df is not None and not df.empty:
            last_idx = df.index[-1]
            # Handle both date and datetime index types
            if hasattr(last_idx, 'date'):
                last_idx = last_idx.date()
            if latest is None or last_idx > latest:
                latest = last_idx

    if latest is not None:
        return latest

    # Fallback: yesterday, skipping weekends
    today = date.today()
    candidate = today - timedelta(days=1)
    while candidate.weekday() >= 5:  # Skip Saturday (5) and Sunday (6)
        candidate -= timedelta(days=1)

    logger.warning(
        f"Could not detect trading date from OHLCV — "
        f"falling back to {candidate}"
    )
    return candidate


def _send_holiday_notifications():
    """Send market holiday messages to all configured channels."""
    try:
        from src.output.telegram_bot import send_holiday_message as tg_holiday
        tg_holiday()
    except Exception:
        pass

    try:
        from src.output.discord_bot import send_holiday_message as dc_holiday
        dc_holiday()
    except Exception:
        pass


def _send_error_notifications(error: str):
    """Send error messages to all configured channels."""
    try:
        from src.output.telegram_bot import send_error_message as tg_error
        tg_error(error)
    except Exception:
        pass

    try:
        from src.output.discord_bot import send_error_message as dc_error
        dc_error(error)
    except Exception:
        pass


if __name__ == '__main__':
    main()

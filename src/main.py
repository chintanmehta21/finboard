"""
Finboard — Main Orchestrator

Entry point for the daily analysis pipeline. Coordinates:
1. Authentication (Fyers TOTP headless)
2. Data ingestion (OHLCV, bhavcopy, fundamentals, FII/DII, pledge)
3. Analysis (5-stage pipeline)
4. Output (Telegram, Discord, JSON export for dashboard)

Runs daily via GitHub Actions at 9:00 AM IST (Mon-Fri),
before market opens at 9:15 AM. Analyzes the previous trading
day's data so signals are ready at market open.
"""

import sys
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

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

    use_sample = not _is_fyers_ready()
    if use_sample:
        logger.warning(
            "FYERS_TOTP_KEY not configured — running with sample/yfinance data. "
            "Set FYERS_TOTP_KEY in Admin/.env to enable live Fyers API."
        )

    try:
        if use_sample:
            _run_sample_pipeline()
        else:
            _run_live_pipeline()

    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        logger.error(traceback.format_exc())
        _send_error_notifications(str(e))
        sys.exit(1)


def _run_live_pipeline():
    """Run the full pipeline with live Fyers API data."""
    logger.info("MODE: Live Fyers API")

    # ── Step 1: Authenticate with Fyers ──
    logger.info("STEP 1: Fyers Authentication")
    from src.auth.token_manager import get_fyers_instance
    fyers = get_fyers_instance()
    logger.info("Fyers authentication successful")

    # ── Step 2: Load universe ──
    logger.info("STEP 2: Loading NSE 500 universe")
    from src.data.universe import load_universe, get_sector_map
    symbols = load_universe()
    sector_map = get_sector_map()
    logger.info(f"Universe loaded: {len(symbols)} symbols")

    # ── Step 3: Fetch market data ──
    logger.info("STEP 3: Fetching market data")

    # 3a: Fetch index data (Nifty 500, VIX, USD/INR) — sequential (Fyers rate-limited)
    from src.data.fyers_client import fetch_all_ohlcv, fetch_index_data
    index_data = fetch_index_data(fyers, years=2)

    # 3b: Fetch OHLCV for all stocks — sequential (Fyers rate-limited)
    ohlcv_data = fetch_all_ohlcv(fyers, symbols, years=2)
    logger.info(f"OHLCV data: {len(ohlcv_data)}/{len(symbols)} symbols")

    # 3c: Fetch bhavcopy (delivery volume) for last trading day
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
        logger.info("Pipeline exiting (market holiday)")
        return

    logger.info(f"Bhavcopy: {len(bhavcopy_df)} records")

    # 3d-3f: Parallel fetch of independent data sources
    logger.info("STEP 3d-f: Parallel fetching (FII/DII, fundamentals, pledge)")
    fii_data, fii_df, fundamentals, pledge_data = _parallel_fetch(
        list(ohlcv_data.keys())
    )

    # ── Step 4: Run analysis pipeline ──
    logger.info("STEP 4: Running 5-stage analysis pipeline")
    from src.analysis.pipeline import run_full_pipeline

    regime_data = {
        'nifty_df': index_data.get('nifty_df'),
        'vix_df': index_data.get('vix_df'),
        'usdinr_df': index_data.get('usdinr_df'),
        'fii_df': fii_df,
    }

    result = run_full_pipeline(
        ohlcv_data=ohlcv_data,
        bhavcopy_df=bhavcopy_df,
        fundamentals=fundamentals,
        regime_data=regime_data,
        pledge_data=pledge_data,
        sector_map=sector_map,
    )

    # Set last trading date for output modules
    result['last_trading_date'] = last_trading_date

    _output_results(result)


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
        import pandas as pd
        fii_df_result = pd.DataFrame()

    return fii_data_result, fii_df_result, fundamentals_result, pledge_result


def _run_sample_pipeline():
    """Run pipeline with sample/yfinance data (when Fyers TOTP unavailable)."""
    logger.info("MODE: Sample Data (yfinance + synthetic fallback)")

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

    # ── Step 2: Use sample universe ──
    logger.info("STEP 2: Using sample universe (50 representative NSE stocks)")
    symbols = SAMPLE_SYMBOLS
    sector_map = get_sample_sector_map()
    logger.info(f"Sample universe: {len(symbols)} symbols")

    # ── Step 3: Generate/fetch sample data ──
    logger.info("STEP 3: Fetching sample market data (yfinance with synthetic fallback)")

    ohlcv_data = generate_sample_ohlcv(symbols)
    logger.info(f"Sample OHLCV: {len(ohlcv_data)} symbols")

    index_data = generate_sample_index_data(ohlcv_data)
    logger.info("Sample index data generated")

    last_trading_date = _get_last_trading_date(ohlcv_data)
    bhavcopy_df = generate_sample_bhavcopy(ohlcv_data, last_trading_date)
    logger.info(f"Sample bhavcopy: {len(bhavcopy_df)} records")

    fii_df = generate_sample_fii_data()
    fundamentals = generate_sample_fundamentals(list(ohlcv_data.keys()))
    pledge_data = generate_sample_pledge_data(list(ohlcv_data.keys()))

    # ── Step 4: Run analysis pipeline (same pipeline, sample data) ──
    logger.info("STEP 4: Running 5-stage analysis pipeline (sample data)")
    from src.analysis.pipeline import run_full_pipeline

    regime_data = {
        'nifty_df': index_data.get('nifty_df'),
        'vix_df': index_data.get('vix_df'),
        'usdinr_df': index_data.get('usdinr_df'),
        'fii_df': fii_df,
    }

    result = run_full_pipeline(
        ohlcv_data=ohlcv_data,
        bhavcopy_df=bhavcopy_df,
        fundamentals=fundamentals,
        regime_data=regime_data,
        pledge_data=pledge_data,
        sector_map=sector_map,
    )

    # Mark as sample data in the result
    result['sample_mode'] = True
    result['last_trading_date'] = last_trading_date

    _output_results(result)


def _output_results(result: dict):
    """Send alerts and export data (shared by live and sample pipelines)."""
    logger.info(f"Pipeline result: Regime={result['regime_name']}, "
                f"Bullish={len(result.get('bullish', []))}, "
                f"Bearish={len(result.get('bearish', []))}")

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

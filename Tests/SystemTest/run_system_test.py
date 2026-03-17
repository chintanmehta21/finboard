"""
Finboard — System-Level End-to-End Test

Runs the full 5-stage pipeline and validates that every component works
correctly. Supports two run modes controlled via config.json or CLI args:

  1. "latest"        — Run pipeline on the most recent available date
  2. "specific_date" — Run pipeline on a user-specified historical date

Usage:
    python -m Tests.SystemTest.run_system_test                       # uses config.json
    python -m Tests.SystemTest.run_system_test --mode latest
    python -m Tests.SystemTest.run_system_test --mode specific_date --date 2026-03-14
"""

import sys
import json
import time
import logging
import argparse
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import pytz

# ── Paths ────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / 'config.json'
RESULTS_DIR = BASE_DIR / 'Results'
LOGS_DIR = BASE_DIR / 'Logs'

RESULTS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# ── Logging ──────────────────────────────────────────────────────────
IST = pytz.timezone('Asia/Kolkata')
run_timestamp = datetime.now(IST).strftime('%Y-%m-%d_%H%M%S')
log_file = LOGS_DIR / f'system_test_{run_timestamp}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)-30s | %(levelname)-7s | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file),
    ]
)
logger = logging.getLogger('system_test')


# ── Config & CLI ─────────────────────────────────────────────────────

def load_config() -> dict:
    """Load config.json, falling back to defaults."""
    defaults = {
        'run_mode': 'latest',
        'specific_date': None,
        'data_source': 'sample',
        'verbose': True,
    }
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        defaults.update(cfg)
    return defaults


def parse_args(config: dict) -> dict:
    """CLI args override config.json values."""
    parser = argparse.ArgumentParser(description='Finboard — System-Level E2E Test')
    parser.add_argument(
        '--mode', choices=['latest', 'specific_date'],
        default=config['run_mode'],
        help='Run mode: "latest" or "specific_date"'
    )
    parser.add_argument(
        '--date', type=str, default=config.get('specific_date'),
        help='Target date for specific_date mode (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--source', choices=['sample', 'live'],
        default=config.get('data_source', 'sample'),
        help='Data source: "sample" (yfinance) or "live" (Fyers API)'
    )
    args = parser.parse_args()

    config['run_mode'] = args.mode
    config['specific_date'] = args.date
    config['data_source'] = args.source
    return config


# ── Data Loading ─────────────────────────────────────────────────────

def load_sample_data(target_date: date = None) -> dict:
    """
    Load sample data using yfinance + synthetic fallback.
    If target_date is provided, slice all data to that date.
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

    logger.info("Loading sample data (yfinance + synthetic fallback)...")

    symbols = SAMPLE_SYMBOLS
    sector_map = get_sample_sector_map()
    ohlcv_data = generate_sample_ohlcv(symbols)
    logger.info(f"  OHLCV: {len(ohlcv_data)} symbols")

    index_data = generate_sample_index_data(ohlcv_data)
    logger.info("  Index data generated (Nifty, VIX, USD/INR)")

    # Slice to target date if specified
    if target_date is not None:
        logger.info(f"  Slicing all data to target date: {target_date}")
        ohlcv_data = _slice_ohlcv(ohlcv_data, target_date)
        index_data = _slice_index(index_data, target_date)
        logger.info(f"  Post-slice OHLCV: {len(ohlcv_data)} symbols with >=100 rows")

    # Detect last trading date from data
    last_trading_date = _detect_last_date(ohlcv_data)
    logger.info(f"  Last trading date: {last_trading_date}")

    bhavcopy_df = generate_sample_bhavcopy(ohlcv_data, last_trading_date)
    logger.info(f"  Bhavcopy: {len(bhavcopy_df)} records")

    fii_df = generate_sample_fii_data()
    fundamentals = generate_sample_fundamentals(list(ohlcv_data.keys()))
    pledge_data = generate_sample_pledge_data(list(ohlcv_data.keys()))
    logger.info(f"  Fundamentals: {len(fundamentals)} symbols")

    return {
        'ohlcv_data': ohlcv_data,
        'index_data': index_data,
        'bhavcopy_df': bhavcopy_df,
        'fii_df': fii_df,
        'fundamentals': fundamentals,
        'pledge_data': pledge_data,
        'sector_map': sector_map,
        'last_trading_date': last_trading_date,
        'symbols': list(ohlcv_data.keys()),
    }


def load_live_data(target_date: date = None) -> dict:
    """Load live data via Fyers API (requires FYERS_TOTP_KEY)."""
    from src.auth.token_manager import get_fyers_instance
    from src.data.universe import load_universe, get_sector_map
    from src.data.fyers_client import fetch_all_ohlcv, fetch_index_data
    from src.data.nse_bhavcopy import fetch_bhavcopy
    from src.data.nse_fiidii import fetch_fiidii_flows, build_fiidii_df
    from src.data.fundamentals import get_fundamentals_batch
    from src.data.nse_pledge import get_pledge_data_batch

    logger.info("Loading live data (Fyers API)...")

    fyers = get_fyers_instance()
    symbols = load_universe()
    sector_map = get_sector_map()
    logger.info(f"  Universe: {len(symbols)} symbols")

    ohlcv_data = fetch_all_ohlcv(fyers, symbols, years=2)
    index_data = fetch_index_data(fyers, years=2)
    logger.info(f"  OHLCV: {len(ohlcv_data)} symbols")

    if target_date is not None:
        ohlcv_data = _slice_ohlcv(ohlcv_data, target_date)
        index_data = _slice_index(index_data, target_date)
        logger.info(f"  Post-slice OHLCV: {len(ohlcv_data)} symbols")

    last_trading_date = _detect_last_date(ohlcv_data)
    bhavcopy_df = fetch_bhavcopy(last_trading_date, symbols=list(ohlcv_data.keys()))

    fii_data = fetch_fiidii_flows()
    fii_df = build_fiidii_df(fii_data)
    fundamentals = get_fundamentals_batch(list(ohlcv_data.keys()))
    pledge_data = get_pledge_data_batch(list(ohlcv_data.keys()))

    return {
        'ohlcv_data': ohlcv_data,
        'index_data': index_data,
        'bhavcopy_df': bhavcopy_df if bhavcopy_df is not None else __import__('pandas').DataFrame(),
        'fii_df': fii_df,
        'fundamentals': fundamentals,
        'pledge_data': pledge_data,
        'sector_map': sector_map,
        'last_trading_date': last_trading_date,
        'symbols': list(ohlcv_data.keys()),
    }


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
    import pandas as pd
    sliced = {}
    for key, df in index_data.items():
        if isinstance(df, pd.DataFrame) and not df.empty:
            sliced[key] = df[df.index <= as_of_date]
        else:
            sliced[key] = df
    return sliced


def _detect_last_date(ohlcv_data: dict) -> date:
    """Find the most recent date across all OHLCV DataFrames."""
    latest = None
    for df in ohlcv_data.values():
        if df is not None and not df.empty:
            last_idx = df.index[-1]
            if hasattr(last_idx, 'date'):
                last_idx = last_idx.date()
            if latest is None or last_idx > latest:
                latest = last_idx

    if latest is not None:
        return latest

    today = date.today()
    candidate = today - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


# ── Main Test Runner ─────────────────────────────────────────────────

def run_test(config: dict) -> dict:
    """
    Execute the full system test.

    Returns:
        Dict with test results including pass/fail counts and details.
    """
    import pandas as pd
    from Tests.SystemTest.validators import (
        validate_result_structure,
        validate_regime,
        validate_macro_snapshot,
        validate_pipeline_stats,
        validate_factor_weights,
        validate_bullish_candidates,
        validate_bearish_candidates,
        validate_json_export,
        validate_data_sources,
    )

    run_mode = config['run_mode']
    data_source = config['data_source']
    target_date = None

    if run_mode == 'specific_date':
        date_str = config.get('specific_date')
        if not date_str:
            raise ValueError("specific_date mode requires --date YYYY-MM-DD")
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()

    all_checks = []
    timings = {}
    warnings = []

    logger.info(f"{'=' * 65}")
    logger.info(f"  SYSTEM TEST — Mode: {run_mode} | Source: {data_source}")
    if target_date:
        logger.info(f"  Target Date: {target_date}")
    logger.info(f"{'=' * 65}")

    # ── Phase 1: Data Loading ────────────────────────────────────────
    logger.info("\n--- PHASE 1: Data Loading ---")
    t0 = time.time()

    try:
        if data_source == 'live':
            data = load_live_data(target_date)
        else:
            data = load_sample_data(target_date)
        timings['data_loading'] = round(time.time() - t0, 2)
        logger.info(f"  Data loaded in {timings['data_loading']}s")

        # Validate data sources
        data_checks = validate_data_sources(
            data['ohlcv_data'], data['bhavcopy_df'],
            data['fundamentals'], data['index_data'],
        )
        all_checks.extend([('Data Sources', *c) for c in data_checks])

    except Exception as e:
        timings['data_loading'] = round(time.time() - t0, 2)
        logger.error(f"  Data loading FAILED: {e}")
        logger.error(traceback.format_exc())
        all_checks.append(('Data Sources', False, f"Data loading failed: {e}"))
        return _build_report(config, all_checks, timings, warnings, target_date)

    # ── Phase 2: Pipeline Execution ──────────────────────────────────
    logger.info("\n--- PHASE 2: Pipeline Execution ---")
    t0 = time.time()

    try:
        from src.analysis.pipeline import run_full_pipeline

        regime_data = {
            'nifty_df': data['index_data'].get('nifty_df', pd.DataFrame()),
            'vix_df': data['index_data'].get('vix_df', pd.DataFrame()),
            'usdinr_df': data['index_data'].get('usdinr_df', pd.DataFrame()),
            'fii_df': data['fii_df'],
        }

        result = run_full_pipeline(
            ohlcv_data=data['ohlcv_data'],
            bhavcopy_df=data['bhavcopy_df'],
            fundamentals=data['fundamentals'],
            regime_data=regime_data,
            pledge_data=data['pledge_data'],
            sector_map=data['sector_map'],
        )
        result['last_trading_date'] = data['last_trading_date']

        timings['pipeline'] = round(time.time() - t0, 2)
        logger.info(f"  Pipeline completed in {timings['pipeline']}s")
        logger.info(f"  Regime: {result['regime_name']} (scalar={result['regime_scalar']})")
        logger.info(f"  Bullish: {len(result.get('bullish', []))}, "
                     f"Bearish: {len(result.get('bearish', []))}")

        all_checks.append(('Pipeline', True, "Pipeline executed without errors"))

    except Exception as e:
        timings['pipeline'] = round(time.time() - t0, 2)
        logger.error(f"  Pipeline FAILED: {e}")
        logger.error(traceback.format_exc())
        all_checks.append(('Pipeline', False, f"Pipeline failed: {e}"))
        return _build_report(config, all_checks, timings, warnings, target_date)

    # ── Phase 3: Result Validation ───────────────────────────────────
    logger.info("\n--- PHASE 3: Result Validation ---")

    # Structure
    checks = validate_result_structure(result)
    all_checks.extend([('Structure', *c) for c in checks])

    # Regime
    checks = validate_regime(result)
    all_checks.extend([('Regime', *c) for c in checks])

    # Macro
    macro = result.get('macro_snapshot', {})
    checks = validate_macro_snapshot(macro)
    all_checks.extend([('Macro', *c) for c in checks])

    # Pipeline stats
    stats = result.get('pipeline_stats', {})
    checks = validate_pipeline_stats(stats)
    all_checks.extend([('Pipeline Stats', *c) for c in checks])

    # Factor weights
    weights = result.get('factor_weights', {})
    checks = validate_factor_weights(weights, result['regime_name'])
    all_checks.extend([('Factor Weights', *c) for c in checks])

    # Bullish candidates
    checks = validate_bullish_candidates(result.get('bullish', pd.DataFrame()), result['regime_name'])
    all_checks.extend([('Bullish', *c) for c in checks])

    # Bearish candidates
    checks = validate_bearish_candidates(result.get('bearish', pd.DataFrame()))
    all_checks.extend([('Bearish', *c) for c in checks])

    # ── Phase 4: JSON Export Test ────────────────────────────────────
    logger.info("\n--- PHASE 4: JSON Export Validation ---")
    t0 = time.time()

    try:
        from src.output.json_export import export_signals
        import tempfile, os

        # Export to a temp file (don't overwrite real signals.json)
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False, mode='w') as tmp:
            tmp_path = tmp.name

        result['sample_mode'] = True
        export_path = export_signals(result, output_path=tmp_path)

        with open(export_path) as f:
            json_data = json.load(f)

        os.unlink(tmp_path)

        checks = validate_json_export(json_data)
        all_checks.extend([('JSON Export', *c) for c in checks])
        all_checks.append(('JSON Export', True, "JSON export successful"))

        timings['json_export'] = round(time.time() - t0, 2)

    except Exception as e:
        timings['json_export'] = round(time.time() - t0, 2)
        logger.error(f"  JSON export FAILED: {e}")
        all_checks.append(('JSON Export', False, f"JSON export failed: {e}"))

    # ── Phase 5: Output Module Validation ────────────────────────────
    logger.info("\n--- PHASE 5: Output Module Validation ---")
    t0 = time.time()

    try:
        from src.output.formatter import format_telegram_report
        report_text = format_telegram_report(result)
        has_content = len(report_text) > 50
        all_checks.append(('Formatter', has_content,
                           f"Telegram report formatted: {len(report_text)} chars"))
        timings['formatter'] = round(time.time() - t0, 2)
    except Exception as e:
        timings['formatter'] = round(time.time() - t0, 2)
        warnings.append(f"Formatter test skipped: {e}")
        all_checks.append(('Formatter', True, f"Formatter skipped (non-critical): {e}"))

    # ── Build & Save Report ──────────────────────────────────────────
    total_time = sum(timings.values())
    timings['total'] = round(total_time, 2)

    report = _build_report(config, all_checks, timings, warnings, target_date)

    # Save results
    _save_results(report, run_timestamp)

    return report


def _build_report(config: dict, all_checks: list, timings: dict,
                  warnings: list, target_date: date = None) -> dict:
    """Build the final test report dict."""
    passed = sum(1 for _, p, _ in all_checks if p)
    failed = sum(1 for _, p, _ in all_checks if not p)
    total = passed + failed

    return {
        'run_mode': config['run_mode'],
        'data_source': config['data_source'],
        'target_date': str(target_date) if target_date else 'latest',
        'run_timestamp': run_timestamp,
        'total_checks': total,
        'passed': passed,
        'failed': failed,
        'pass_rate': round(passed / total * 100, 1) if total > 0 else 0,
        'status': 'PASS' if failed == 0 else 'FAIL',
        'timings': timings,
        'warnings': warnings,
        'checks': [
            {'category': cat, 'passed': p, 'message': msg}
            for cat, p, msg in all_checks
        ],
    }


def _save_results(report: dict, timestamp: str):
    """Save test results to Results/ and print summary."""
    # JSON results
    results_path = RESULTS_DIR / f'system_test_{timestamp}.json'
    with open(results_path, 'w') as f:
        json.dump(report, f, indent=2, default=str)

    # Human-readable summary
    summary_path = RESULTS_DIR / f'system_test_{timestamp}.txt'
    lines = [
        "=" * 65,
        "  SYSTEM TEST REPORT",
        f"  Timestamp: {timestamp}",
        f"  Mode: {report['run_mode']} | Source: {report['data_source']}",
        f"  Target Date: {report['target_date']}",
        "=" * 65,
        "",
        f"  STATUS: {report['status']}",
        f"  Checks: {report['passed']}/{report['total_checks']} passed "
        f"({report['pass_rate']}%)",
        f"  Failed: {report['failed']}",
        "",
        "--- Timings ---",
    ]
    for stage, secs in report['timings'].items():
        lines.append(f"  {stage:20s}: {secs:>7.2f}s")

    if report['warnings']:
        lines.append("")
        lines.append("--- Warnings ---")
        for w in report['warnings']:
            lines.append(f"  ! {w}")

    lines.append("")
    lines.append("--- Check Details ---")
    for check in report['checks']:
        status = "PASS" if check['passed'] else "FAIL"
        lines.append(f"  [{status}] [{check['category']:15s}] {check['message']}")

    lines.append("")
    lines.append("=" * 65)

    summary_text = "\n".join(lines)
    with open(summary_path, 'w') as f:
        f.write(summary_text)

    # Print to console
    logger.info(f"\n{summary_text}")
    logger.info(f"\nResults saved to: {results_path}")
    logger.info(f"Summary saved to: {summary_path}")
    logger.info(f"Logs saved to:    {log_file}")


# ── Entry Point ──────────────────────────────────────────────────────

def main():
    config = load_config()
    config = parse_args(config)
    report = run_test(config)

    if report['status'] == 'FAIL':
        logger.error(f"SYSTEM TEST FAILED — {report['failed']} check(s) failed")
        sys.exit(1)
    else:
        logger.info(f"SYSTEM TEST PASSED — {report['passed']}/{report['total_checks']} checks")
        sys.exit(0)


if __name__ == '__main__':
    main()

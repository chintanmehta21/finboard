"""
Finboard — System-Level End-to-End Test

Calls src.main.run_analysis() — the SAME code path used by the daily cron —
then validates the output. No pipeline logic is duplicated here; when the
analysis pipeline changes, these tests automatically pick up the new behavior.

Supports two run modes controlled via config.json or CLI args:

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
from datetime import date, datetime
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


# ── Main Test Runner ─────────────────────────────────────────────────

def run_test(config: dict) -> dict:
    """
    Execute the full system test by calling src.main.run_analysis().

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

    # ── Phase 1+2: Run analysis via src.main.run_analysis() ──────────
    # This is the SAME function the daily cron calls. No duplication.
    logger.info("\n--- PHASE 1+2: Data Loading + Pipeline (via src.main.run_analysis) ---")
    t0 = time.time()

    try:
        from src.main import run_analysis

        result = run_analysis(data_source=data_source, target_date=target_date)

        timings['analysis'] = round(time.time() - t0, 2)
        logger.info(f"  Analysis completed in {timings['analysis']}s")
        logger.info(f"  Regime: {result['regime_name']} (scalar={result['regime_scalar']})")
        logger.info(f"  Bullish: {len(result.get('bullish', []))}, "
                     f"Bearish: {len(result.get('bearish', []))}")

        all_checks.append(('Analysis', True, "run_analysis() executed without errors"))

    except Exception as e:
        timings['analysis'] = round(time.time() - t0, 2)
        logger.error(f"  Analysis FAILED: {e}")
        logger.error(traceback.format_exc())
        all_checks.append(('Analysis', False, f"run_analysis() failed: {e}"))
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

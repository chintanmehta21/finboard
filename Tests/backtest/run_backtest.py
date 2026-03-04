"""
NSE Alpha System — Walk-Forward Backtesting Engine

Entry point for the weekly backtest. Simulates the 5-stage pipeline
running on each historical Friday over a configurable lookback period.

Walk-forward approach:
1. Fetch ALL data once (2 years OHLCV, indices, bhavcopy, fundamentals)
2. For each Friday in the lookback window:
   a. Slice data to that Friday (no look-ahead bias)
   b. Run the full pipeline on the sliced data
   c. Check exit rules on existing open positions
   d. Enter new positions from pipeline signals
   e. Record portfolio value snapshot
3. At end, force-close remaining positions
4. Compute comprehensive metrics
5. Export to CSV

Usage:
    python -m Tests.backtest.run_backtest
    python -m Tests.backtest.run_backtest --weeks 26
    python -m Tests.backtest.run_backtest --no-bhavcopy  (faster, skips delivery data)

Runs weekly every Friday via .github/workflows/backtest.yml
"""

import sys
import logging
import argparse
import traceback
from datetime import date, datetime
from pathlib import Path

import pytz
import pandas as pd

# ── Logging Setup ────────────────────────────────────────────────────
LOG_DIR = Path('logs')
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)-30s | %(levelname)-7s | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            LOG_DIR / f'backtest_{date.today().isoformat()}.log'
        ),
    ]
)
logger = logging.getLogger('backtest')

# ── Results Directory ────────────────────────────────────────────────
RESULTS_DIR = Path(__file__).parent / 'backtest_results'
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='NSE Alpha System — Walk-Forward Backtest'
    )
    parser.add_argument(
        '--weeks', type=int, default=52,
        help='Number of weeks to simulate (default: 52)'
    )
    parser.add_argument(
        '--no-bhavcopy', action='store_true',
        help='Skip bhavcopy fetch for faster runs (delivery factor will default to neutral)'
    )
    parser.add_argument(
        '--capital', type=float, default=1_000_000,
        help='Initial capital in INR (default: 1000000)'
    )
    return parser.parse_args()


def main():
    """Execute the full walk-forward backtest."""
    args = parse_args()

    IST = pytz.timezone('Asia/Kolkata')
    now = datetime.now(IST)

    logger.info(f"{'=' * 65}")
    logger.info(f"  NSE Alpha System — WALK-FORWARD BACKTEST")
    logger.info(f"  Date: {now.strftime('%A, %d %b %Y %I:%M %p IST')}")
    logger.info(f"  Lookback: {args.weeks} weeks | Capital: INR {args.capital:,.0f}")
    logger.info(f"  Bhavcopy: {'enabled' if not args.no_bhavcopy else 'disabled'}")
    logger.info(f"{'=' * 65}")

    try:
        # ── Step 1: Authenticate with Fyers ──
        logger.info("STEP 1: Fyers Authentication")
        from src.auth.token_manager import get_fyers_instance
        fyers = get_fyers_instance()
        logger.info("Authentication successful")

        # ── Step 2: Load Universe ──
        logger.info("STEP 2: Loading NSE 500 universe")
        from src.data.universe import load_universe, get_sector_map
        symbols = load_universe()
        sector_map = get_sector_map()
        logger.info(f"Universe: {len(symbols)} symbols")

        # ── Step 3: Initialize Data Provider (fetches all data once) ──
        logger.info("STEP 3: Initializing data provider (fetching all data)...")
        from Tests.backtest.data_provider import HistoricalDataProvider
        provider = HistoricalDataProvider(
            fyers=fyers,
            symbols=symbols,
            sector_map=sector_map,
            lookback_years=2,
            fetch_bhavcopy=not args.no_bhavcopy,
        )

        # ── Step 4: Generate simulation schedule ──
        fridays = provider.get_simulation_fridays(lookback_weeks=args.weeks)
        if not fridays:
            logger.error("No valid simulation dates found")
            sys.exit(1)

        logger.info(f"STEP 4: Simulation schedule — {len(fridays)} Fridays")
        logger.info(f"  First: {fridays[0]} | Last: {fridays[-1]}")

        # ── Step 5: Initialize Portfolio Tracker ──
        from Tests.backtest.portfolio_tracker import PortfolioTracker
        tracker = PortfolioTracker(initial_capital=args.capital)

        # ── Step 6: Walk-Forward Simulation ──
        logger.info(f"{'=' * 65}")
        logger.info("STEP 6: WALK-FORWARD SIMULATION BEGIN")
        logger.info(f"{'=' * 65}")

        from src.analysis.pipeline import run_full_pipeline
        weekly_regimes = []

        for week_num, sim_date in enumerate(fridays, 1):
            logger.info(
                f"\n--- Week {week_num}/{len(fridays)} | "
                f"{sim_date.isoformat()} ({sim_date.strftime('%A')}) ---"
            )

            # 6a: Slice data to this date (no look-ahead)
            data_slice = provider.slice_to_date(sim_date)

            ohlcv_sliced = data_slice['ohlcv_data']
            if len(ohlcv_sliced) < 10:
                logger.warning(f"  Insufficient data for {sim_date}, skipping")
                continue

            # 6b: Run the full pipeline
            try:
                result = run_full_pipeline(
                    ohlcv_data=ohlcv_sliced,
                    bhavcopy_df=data_slice['bhavcopy_df'],
                    fundamentals=data_slice['fundamentals'],
                    regime_data=data_slice['regime_data'],
                    pledge_data=data_slice['pledge_data'],
                    sector_map=data_slice['sector_map'],
                )

                regime_name = result.get('regime_name', 'UNKNOWN')
                regime_scalar = result.get('regime_scalar', 0)
                stats = result.get('pipeline_stats', {})

                # Count signals
                bullish = result.get('bullish', pd.DataFrame())
                n_bull = len(bullish) if isinstance(bullish, (pd.DataFrame, list)) else 0

                logger.info(
                    f"  Pipeline: Regime={regime_name} ({regime_scalar:.1f}), "
                    f"Survivors={stats.get('stage_2_scored', 0)}, "
                    f"Bullish={n_bull}"
                )

            except Exception as e:
                logger.warning(f"  Pipeline failed for {sim_date}: {e}")
                regime_name = 'UNKNOWN'
                regime_scalar = 0
                result = {}

            weekly_regimes.append({
                'date': sim_date.isoformat(),
                'regime_name': regime_name,
            })

            # 6c: Check exit rules on existing positions
            if tracker.open_positions:
                tracker.check_and_process_exits(
                    ohlcv_data=ohlcv_sliced,
                    fundamentals=data_slice['fundamentals'],
                    benchmark_df=data_slice['nifty_df'],
                    current_vix=data_slice['current_vix'],
                    as_of_date=sim_date,
                )

            # 6d: Enter new positions from signals
            if result:
                tracker.enter_positions(
                    pipeline_result=result,
                    ohlcv_data=ohlcv_sliced,
                    as_of_date=sim_date,
                    regime_scalar=regime_scalar,
                    regime_name=regime_name,
                )

            # 6e: Mark-to-market snapshot
            total_value = tracker.mark_to_market(
                ohlcv_sliced, sim_date, regime_name
            )

            summary = tracker.get_summary()
            logger.info(
                f"  Portfolio: Value={total_value:,.0f}, "
                f"Positions={summary['open_positions']}, "
                f"Cash={summary['cash']:,.0f}, "
                f"Trades={summary['closed_trades']}"
            )

        # ── Step 7: Close remaining positions ──
        logger.info(f"\n{'=' * 65}")
        logger.info("STEP 7: Closing remaining positions (end of backtest)")

        if tracker.open_positions:
            final_slice = provider.slice_to_date(fridays[-1])
            tracker.close_all_positions(
                final_slice['ohlcv_data'], fridays[-1]
            )

        # ── Step 8: Compute metrics ──
        logger.info("STEP 8: Computing performance metrics")
        from Tests.backtest.metrics import compute_all_metrics, format_summary_report

        metrics = compute_all_metrics(
            closed_trades=tracker.closed_trades,
            portfolio_history=tracker.portfolio_history,
            weekly_regimes=weekly_regimes,
        )

        # Print summary
        report = format_summary_report(metrics)
        logger.info(f"\n{report}")

        # ── Step 9: Export CSVs ──
        logger.info("STEP 9: Exporting results to CSV")
        run_date_str = date.today().isoformat()

        # Trades CSV
        trades_df = tracker.get_closed_trades_df()
        if not trades_df.empty:
            trades_df['run_date'] = run_date_str
            trades_path = RESULTS_DIR / f'trades_{run_date_str}.csv'
            trades_df.to_csv(trades_path, index=False)
            logger.info(f"  Trades CSV: {trades_path} ({len(trades_df)} rows)")
        else:
            trades_path = RESULTS_DIR / f'trades_{run_date_str}.csv'
            pd.DataFrame(columns=[
                'run_date', 'symbol', 'signal_type', 'entry_price',
                'entry_date', 'exit_price', 'exit_date', 'exit_reason',
                'return_pct', 'holding_days', 'regime_at_entry',
                'confidence_score', 'sector', 'atr14_at_entry', 'stop_loss'
            ]).to_csv(trades_path, index=False)
            logger.info(f"  Trades CSV: {trades_path} (empty — no trades)")

        # Summary CSV
        summary_path = RESULTS_DIR / f'summary_{run_date_str}.csv'
        summary_df = pd.DataFrame([metrics])
        summary_df.to_csv(summary_path, index=False)
        logger.info(f"  Summary CSV: {summary_path}")

        # Portfolio history CSV (supplementary)
        history_df = tracker.get_portfolio_history_df()
        if not history_df.empty:
            history_path = RESULTS_DIR / f'portfolio_history_{run_date_str}.csv'
            history_df.to_csv(history_path, index=False)
            logger.info(f"  Portfolio History CSV: {history_path}")

        # ── Done ──
        logger.info(f"\n{'=' * 65}")
        logger.info("  BACKTEST COMPLETED SUCCESSFULLY")
        logger.info(f"  Total Return: {metrics.get('total_return', 0):+.2f}%")
        logger.info(f"  Sharpe Ratio: {metrics.get('sharpe_ratio', 0):.2f}")
        logger.info(f"  Max Drawdown: {metrics.get('max_drawdown', 0):.2f}%")
        logger.info(f"  Win Rate:     {metrics.get('win_rate', 0):.1f}%")
        logger.info(f"  Results in:   {RESULTS_DIR}")
        logger.info(f"{'=' * 65}")

    except Exception as e:
        logger.error(f"Backtest failed: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == '__main__':
    main()

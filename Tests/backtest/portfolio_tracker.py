"""
Portfolio Tracker — State Machine for Walk-Forward Simulation

Maintains realistic portfolio state across the backtest:
- Open positions with entry details
- Closed trade history with exit reasons
- Weekly portfolio value snapshots for drawdown/Sharpe calculation
- Cash management (compounding: profits/losses carry forward)

Reuses constants from src/analysis/portfolio.py for consistency
with the live system.
"""

import logging
from datetime import date, timedelta

import pandas as pd
import numpy as np

# Reuse live system constants
from src.analysis.portfolio import (
    RISK_PER_TRADE_PCT,
    ATR_STOP_MULTIPLIER,
    MAX_SECTOR_PCT,
    MAX_POSITION_PCT,
    MAX_STOCKS,
)
from src.analysis.portfolio import compute_atr14, compute_stock_beta
from src.analysis.exit_rules import (
    ATR_STOP_MULTIPLIER as EXIT_ATR_MULTIPLIER,
    VIX_HIGH_THRESHOLD,
    VIX_STOP_TIGHTENING,
    TIME_STOP_WEEKS_NORMAL,
    TIME_STOP_WEEKS_HIGH_VIX,
    SALES_DROP_EXIT_THRESHOLD,
    RS_EXIT_THRESHOLD,
)

logger = logging.getLogger(__name__)

# Initial capital (matches portfolio.py assumption)
INITIAL_CAPITAL = 1_000_000  # INR 10 Lakh


class PortfolioTracker:
    """
    Tracks portfolio state throughout the walk-forward backtest.

    Manages entries, exits, mark-to-market, and trade recording
    with realistic sizing rules from the live pipeline.
    """

    def __init__(self, initial_capital: float = INITIAL_CAPITAL):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.open_positions: list[dict] = []
        self.closed_trades: list[dict] = []
        self.portfolio_history: list[dict] = []
        self.weekly_signals_log: list[dict] = []

        logger.info(f"Portfolio tracker initialized: INR {initial_capital:,.0f}")

    # ── Entry Logic ──────────────────────────────────────────────────

    def enter_positions(self, pipeline_result: dict,
                        ohlcv_data: dict[str, pd.DataFrame],
                        as_of_date: date,
                        regime_scalar: float,
                        regime_name: str):
        """
        Enter new positions from pipeline bullish signals.

        Applies all sizing rules: ATR sizing, sector caps, position caps,
        max stocks. Only enters if portfolio has room and cash.

        Args:
            pipeline_result: Output of run_full_pipeline()
            ohlcv_data: Sliced OHLCV data for the current date
            as_of_date: The simulated date
            regime_scalar: Current regime exposure scalar
            regime_name: Current regime (BULL/DIP/SIDEWAYS/BEAR)
        """
        # BEAR regime: 0 new buys
        if regime_name == 'BEAR' or regime_scalar <= 0:
            return

        bullish = pipeline_result.get('bullish', pd.DataFrame())
        if isinstance(bullish, pd.DataFrame) and bullish.empty:
            return
        if isinstance(bullish, list) and len(bullish) == 0:
            return

        # Convert to list of dicts if DataFrame
        if isinstance(bullish, pd.DataFrame):
            signals = bullish.to_dict('records')
        else:
            signals = bullish

        # Track symbols we already hold
        held_symbols = {p['symbol'] for p in self.open_positions}

        # Sector allocation tracking
        sector_allocation = {}
        total_allocated = sum(p['pos_value'] for p in self.open_positions)
        for p in self.open_positions:
            sec = p.get('sector', 'Unknown')
            sector_allocation[sec] = sector_allocation.get(sec, 0) + p['pos_value']

        # Effective capital = current total value (cash + positions)
        total_value = self.cash + total_allocated
        risk_per_trade = total_value * RISK_PER_TRADE_PCT * regime_scalar

        entries = 0
        for signal in signals:
            if len(self.open_positions) >= MAX_STOCKS:
                break

            symbol = signal.get('symbol', '')
            if symbol in held_symbols:
                continue  # Already holding

            close = signal.get('close', 0)
            if close <= 0:
                continue

            atr14 = signal.get('atr14', signal.get('effective_atr_stop', close * 0.03))
            sector = signal.get('sector', 'Unknown')
            confidence = signal.get('adj_confidence', signal.get('confidence', 0))

            # Sector cap check
            current_sector = sector_allocation.get(sector, 0)
            if current_sector >= MAX_SECTOR_PCT * total_value:
                continue

            # ATR-based position sizing
            stop_distance = atr14 * ATR_STOP_MULTIPLIER
            if stop_distance <= 0:
                continue

            shares = risk_per_trade / stop_distance
            pos_value = shares * close

            # Position cap: 15% of total capital
            pos_value = min(pos_value, total_value * MAX_POSITION_PCT)

            # Sector headroom
            sector_headroom = (MAX_SECTOR_PCT * total_value) - current_sector
            pos_value = min(pos_value, sector_headroom)

            # Cash constraint
            pos_value = min(pos_value, self.cash * 0.95)  # Keep 5% cash buffer

            if pos_value <= 0:
                continue

            shares = int(pos_value / close)
            if shares <= 0:
                continue

            actual_value = shares * close
            stop_price = close - stop_distance

            # Record the position
            position = {
                'symbol': symbol,
                'entry_price': round(close, 2),
                'entry_date': as_of_date.isoformat(),
                'shares': shares,
                'pos_value': round(actual_value, 2),
                'atr14_at_entry': round(atr14, 2),
                'stop_loss': round(stop_price, 2),
                'sector': sector,
                'confidence': round(float(confidence), 1),
                'regime_at_entry': regime_name,
            }

            self.open_positions.append(position)
            self.cash -= actual_value
            held_symbols.add(symbol)
            sector_allocation[sector] = current_sector + actual_value
            entries += 1

            logger.debug(
                f"  ENTER: {symbol} @ {close:.0f}, "
                f"{shares} shares, value={actual_value:,.0f}"
            )

        if entries > 0:
            logger.info(
                f"  Entered {entries} new positions | "
                f"Total open: {len(self.open_positions)} | "
                f"Cash remaining: {self.cash:,.0f}"
            )

    # ── Exit Logic ───────────────────────────────────────────────────

    def check_and_process_exits(self,
                                ohlcv_data: dict[str, pd.DataFrame],
                                fundamentals: dict[str, dict],
                                benchmark_df: pd.DataFrame,
                                current_vix: float,
                                as_of_date: date):
        """
        Check all 4 exit triggers for each open position and process exits.

        We implement the exit checks inline (rather than calling exit_rules.py
        directly) to avoid the date.today() dependency in _check_time_stop().
        This gives us accurate time stop behavior using the simulated date.

        Args:
            ohlcv_data: Sliced OHLCV data
            fundamentals: Fundamentals dict
            benchmark_df: Nifty 500 DataFrame for RS calculation
            current_vix: Current VIX level
            as_of_date: The simulated date
        """
        high_vix = current_vix > VIX_HIGH_THRESHOLD
        positions_to_exit = []

        for pos in self.open_positions:
            symbol = pos['symbol']
            entry_price = pos['entry_price']
            entry_date_str = pos['entry_date']
            atr_at_entry = pos['atr14_at_entry']

            ohlcv = ohlcv_data.get(symbol, pd.DataFrame())
            if ohlcv.empty:
                continue

            current_close = float(ohlcv['close'].iloc[-1])
            exit_reason = None

            # ── Trigger 1: TECHNICAL EXIT ──
            # RS < 0 AND price below 20-week (100-day) MA
            if len(ohlcv) >= 100:
                ma_100 = float(ohlcv['close'].rolling(100).mean().iloc[-1])
                if current_close < ma_100:
                    # Compute Mansfield RS
                    mrs = self._compute_mansfield_rs(ohlcv, benchmark_df)
                    if mrs < RS_EXIT_THRESHOLD:
                        exit_reason = 'TECHNICAL'

            # ── Trigger 2: FUNDAMENTAL EXIT ──
            # QoQ sales drop > 5%
            if exit_reason is None:
                f = fundamentals.get(symbol)
                if f:
                    sales_t = f.get('sales_t')
                    sales_t1 = f.get('sales_t1')
                    if (sales_t is not None and sales_t1 is not None
                            and sales_t1 > 0):
                        qoq = (sales_t - sales_t1) / abs(sales_t1)
                        if qoq < SALES_DROP_EXIT_THRESHOLD:
                            exit_reason = 'FUNDAMENTAL'

            # ── Trigger 3: RISK STOP EXIT ──
            # Close < entry - 2×ATR14 (tightened 30% if VIX > 20)
            if exit_reason is None:
                if entry_price > 0 and atr_at_entry > 0:
                    stop_distance = atr_at_entry * EXIT_ATR_MULTIPLIER
                    if high_vix:
                        stop_distance *= VIX_STOP_TIGHTENING
                    stop_price = entry_price - stop_distance
                    if current_close < stop_price:
                        exit_reason = 'RISK_STOP'

            # ── Trigger 4: TIME STOP EXIT ──
            # Position > 26 weeks (13 if VIX > 20)
            if exit_reason is None:
                try:
                    entry_date = date.fromisoformat(entry_date_str)
                    holding_days = (as_of_date - entry_date).days
                    holding_weeks = holding_days / 7
                    max_weeks = TIME_STOP_WEEKS_HIGH_VIX if high_vix else TIME_STOP_WEEKS_NORMAL
                    if holding_weeks > max_weeks:
                        exit_reason = 'TIME_STOP'
                except (ValueError, TypeError):
                    pass

            if exit_reason:
                positions_to_exit.append({
                    'position': pos,
                    'exit_price': current_close,
                    'exit_reason': exit_reason,
                })

        # Process exits
        for exit_info in positions_to_exit:
            self._process_exit(
                exit_info['position'],
                exit_info['exit_price'],
                as_of_date,
                exit_info['exit_reason'],
            )

        if positions_to_exit:
            logger.info(
                f"  Exited {len(positions_to_exit)} positions | "
                f"Remaining: {len(self.open_positions)} | "
                f"Cash: {self.cash:,.0f}"
            )

    def _process_exit(self, position: dict, exit_price: float,
                      exit_date: date, reason: str):
        """Close a position and record the trade."""
        entry_price = position['entry_price']
        shares = position['shares']
        entry_date = date.fromisoformat(position['entry_date'])

        return_pct = ((exit_price / entry_price) - 1) * 100 if entry_price > 0 else 0
        holding_days = (exit_date - entry_date).days
        exit_value = shares * exit_price

        trade = {
            'symbol': position['symbol'],
            'signal_type': 'BULLISH',
            'entry_price': entry_price,
            'entry_date': position['entry_date'],
            'exit_price': round(exit_price, 2),
            'exit_date': exit_date.isoformat(),
            'exit_reason': reason,
            'return_pct': round(return_pct, 2),
            'holding_days': holding_days,
            'regime_at_entry': position.get('regime_at_entry', 'UNKNOWN'),
            'confidence_score': position.get('confidence', 0),
            'sector': position.get('sector', 'Unknown'),
            'atr14_at_entry': position.get('atr14_at_entry', 0),
            'stop_loss': position.get('stop_loss', 0),
            'shares': shares,
            'pnl_inr': round((exit_price - entry_price) * shares, 2),
        }

        self.closed_trades.append(trade)
        self.cash += exit_value
        self.open_positions.remove(position)

        pnl_emoji = '+' if return_pct > 0 else ''
        logger.debug(
            f"    EXIT [{reason}]: {position['symbol']} "
            f"@ {exit_price:.0f} ({pnl_emoji}{return_pct:.1f}%, "
            f"{holding_days}d held)"
        )

    def _compute_mansfield_rs(self, ohlcv: pd.DataFrame,
                               benchmark_df: pd.DataFrame) -> float:
        """Compute Mansfield RS for exit check (simplified 91-day)."""
        try:
            stock_close = ohlcv['close']
            bench_close = benchmark_df['close']
            common = stock_close.index.intersection(bench_close.index)

            if len(common) < 91:
                return 0.0

            rp = stock_close.loc[common] / bench_close.loc[common]
            rp_ma = rp.rolling(91).mean()
            if pd.notna(rp_ma.iloc[-1]) and rp_ma.iloc[-1] != 0:
                return float(((rp.iloc[-1] / rp_ma.iloc[-1]) - 1) * 100)
        except Exception:
            pass
        return 0.0

    # ── Mark-to-Market & Snapshots ───────────────────────────────────

    def mark_to_market(self, ohlcv_data: dict[str, pd.DataFrame],
                       as_of_date: date, regime_name: str = ''):
        """
        Compute current portfolio value and record a weekly snapshot.

        Args:
            ohlcv_data: Sliced OHLCV data
            as_of_date: Current simulated date
            regime_name: Current regime for logging
        """
        invested_value = 0
        for pos in self.open_positions:
            ohlcv = ohlcv_data.get(pos['symbol'], pd.DataFrame())
            if not ohlcv.empty:
                current_close = float(ohlcv['close'].iloc[-1])
                invested_value += pos['shares'] * current_close
            else:
                # If no data, use entry price as fallback
                invested_value += pos['pos_value']

        total_value = self.cash + invested_value

        snapshot = {
            'date': as_of_date.isoformat(),
            'total_value': round(total_value, 2),
            'cash': round(self.cash, 2),
            'invested': round(invested_value, 2),
            'num_positions': len(self.open_positions),
            'regime': regime_name,
        }
        self.portfolio_history.append(snapshot)

        return total_value

    # ── End-of-Backtest Cleanup ──────────────────────────────────────

    def close_all_positions(self, ohlcv_data: dict[str, pd.DataFrame],
                            as_of_date: date):
        """
        Force-close all remaining open positions at end of backtest.
        Recorded as 'END_OF_BACKTEST' exit reason.
        """
        remaining = list(self.open_positions)  # Copy to avoid mutation issues

        for pos in remaining:
            ohlcv = ohlcv_data.get(pos['symbol'], pd.DataFrame())
            if not ohlcv.empty:
                exit_price = float(ohlcv['close'].iloc[-1])
            else:
                exit_price = pos['entry_price']  # Flat if no data

            self._process_exit(pos, exit_price, as_of_date, 'END_OF_BACKTEST')

        if remaining:
            logger.info(
                f"  End-of-backtest: closed {len(remaining)} remaining positions"
            )

    # ── Reporting ────────────────────────────────────────────────────

    def get_closed_trades_df(self) -> pd.DataFrame:
        """Return all closed trades as a DataFrame for CSV export."""
        if not self.closed_trades:
            return pd.DataFrame()
        return pd.DataFrame(self.closed_trades)

    def get_portfolio_history_df(self) -> pd.DataFrame:
        """Return portfolio value history as a DataFrame."""
        if not self.portfolio_history:
            return pd.DataFrame()
        return pd.DataFrame(self.portfolio_history)

    def get_summary(self) -> dict:
        """Return a quick summary of current portfolio state."""
        total_invested = sum(p['pos_value'] for p in self.open_positions)
        return {
            'cash': round(self.cash, 2),
            'invested': round(total_invested, 2),
            'total_value': round(self.cash + total_invested, 2),
            'open_positions': len(self.open_positions),
            'closed_trades': len(self.closed_trades),
        }

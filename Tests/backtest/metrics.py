"""
Backtest Metrics — Comprehensive Performance Analytics

Computes all key performance indicators from trade history and
portfolio value series produced by the walk-forward simulation.

Metrics cover:
- Returns (total, annualized, per-trade)
- Risk (max drawdown, drawdown duration, Sharpe ratio)
- Win/Loss analysis (win rate, profit factor, payoff ratio)
- Exit analysis (breakdown by trigger type)
- Regime performance (returns per regime)
- Signal quality (hit rate at various horizons)
"""

import logging
from datetime import date

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Risk-free rate for Sharpe calculation (India 10Y govt bond ~ 6%)
RISK_FREE_RATE_ANNUAL = 0.06
TRADING_WEEKS_PER_YEAR = 52


def compute_all_metrics(closed_trades: list[dict],
                        portfolio_history: list[dict],
                        weekly_regimes: list[dict]) -> dict:
    """
    Compute comprehensive backtest metrics.

    Args:
        closed_trades: List of completed trade dicts from PortfolioTracker
        portfolio_history: Weekly portfolio snapshots (date, total_value, ...)
        weekly_regimes: List of {date, regime_name} for each simulated week

    Returns:
        Dict of all computed metrics
    """
    metrics = {
        'run_date': date.today().isoformat(),
        'lookback_weeks': len(portfolio_history),
        'total_trades': len(closed_trades),
    }

    if not closed_trades:
        logger.warning("No closed trades — metrics will be empty")
        metrics.update(_empty_metrics())
        return metrics

    trades_df = pd.DataFrame(closed_trades)

    # ── Return Metrics ──
    metrics.update(_compute_return_metrics(trades_df, portfolio_history))

    # ── Risk Metrics ──
    metrics.update(_compute_risk_metrics(portfolio_history))

    # ── Win/Loss Metrics ──
    metrics.update(_compute_winloss_metrics(trades_df))

    # ── Exit Analysis ──
    metrics.update(_compute_exit_analysis(trades_df))

    # ── Regime Performance ──
    metrics.update(_compute_regime_performance(trades_df, weekly_regimes))

    # ── Signal Quality ──
    metrics.update(_compute_signal_quality(trades_df))

    return metrics


def _compute_return_metrics(trades_df: pd.DataFrame,
                            portfolio_history: list[dict]) -> dict:
    """Compute total, annualized, and per-trade return metrics."""
    returns = trades_df['return_pct'].values

    # Portfolio-level returns from value history
    if len(portfolio_history) >= 2:
        initial_value = portfolio_history[0]['total_value']
        final_value = portfolio_history[-1]['total_value']
        total_return = ((final_value / initial_value) - 1) * 100 if initial_value > 0 else 0
        num_weeks = len(portfolio_history)
        annualized_return = (
            ((final_value / initial_value) ** (TRADING_WEEKS_PER_YEAR / max(num_weeks, 1)) - 1) * 100
            if initial_value > 0 else 0
        )
        final_portfolio_value = final_value
    else:
        total_return = 0
        annualized_return = 0
        final_portfolio_value = portfolio_history[0]['total_value'] if portfolio_history else 0

    return {
        'avg_return': round(float(np.mean(returns)), 2),
        'median_return': round(float(np.median(returns)), 2),
        'total_return': round(total_return, 2),
        'annualized_return': round(annualized_return, 2),
        'final_portfolio_value': round(final_portfolio_value, 0),
    }


def _compute_risk_metrics(portfolio_history: list[dict]) -> dict:
    """Compute max drawdown, drawdown duration, and Sharpe ratio."""
    if len(portfolio_history) < 2:
        return {
            'max_drawdown': 0,
            'max_drawdown_weeks': 0,
            'sharpe_ratio': 0,
            'volatility_annual': 0,
        }

    values = pd.Series([h['total_value'] for h in portfolio_history])

    # Weekly returns
    weekly_returns = values.pct_change().dropna()

    # Sharpe Ratio (annualized)
    if len(weekly_returns) > 1 and weekly_returns.std() > 0:
        weekly_rf = RISK_FREE_RATE_ANNUAL / TRADING_WEEKS_PER_YEAR
        excess_returns = weekly_returns - weekly_rf
        sharpe = float(excess_returns.mean() / excess_returns.std() * np.sqrt(TRADING_WEEKS_PER_YEAR))
    else:
        sharpe = 0

    # Max Drawdown
    cummax = values.cummax()
    drawdown = (values - cummax) / cummax * 100
    max_dd = float(drawdown.min())

    # Max Drawdown Duration (weeks)
    in_drawdown = values < cummax
    if in_drawdown.any():
        dd_groups = (~in_drawdown).cumsum()
        dd_lengths = in_drawdown.groupby(dd_groups).sum()
        max_dd_weeks = int(dd_lengths.max()) if len(dd_lengths) > 0 else 0
    else:
        max_dd_weeks = 0

    # Annualized volatility
    vol_annual = float(weekly_returns.std() * np.sqrt(TRADING_WEEKS_PER_YEAR) * 100)

    return {
        'max_drawdown': round(max_dd, 2),
        'max_drawdown_weeks': max_dd_weeks,
        'sharpe_ratio': round(sharpe, 2),
        'volatility_annual': round(vol_annual, 2),
    }


def _compute_winloss_metrics(trades_df: pd.DataFrame) -> dict:
    """Compute win rate, profit factor, payoff ratio."""
    returns = trades_df['return_pct']

    winners = returns[returns > 0]
    losers = returns[returns < 0]
    flat = returns[returns == 0]

    win_rate = len(winners) / len(returns) * 100 if len(returns) > 0 else 0
    avg_winner = float(winners.mean()) if len(winners) > 0 else 0
    avg_loser = float(losers.mean()) if len(losers) > 0 else 0

    # Profit Factor = gross wins / gross losses
    gross_wins = float(winners.sum()) if len(winners) > 0 else 0
    gross_losses = float(abs(losers.sum())) if len(losers) > 0 else 0
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float('inf')

    # Payoff Ratio = avg winner / avg loser (magnitude)
    payoff_ratio = abs(avg_winner / avg_loser) if avg_loser != 0 else float('inf')

    # Average holding period
    avg_holding_days = float(trades_df['holding_days'].mean()) if 'holding_days' in trades_df else 0

    return {
        'win_rate': round(win_rate, 1),
        'avg_winner': round(avg_winner, 2),
        'avg_loser': round(avg_loser, 2),
        'profit_factor': round(profit_factor, 2) if profit_factor != float('inf') else 999.99,
        'payoff_ratio': round(payoff_ratio, 2) if payoff_ratio != float('inf') else 999.99,
        'total_winners': len(winners),
        'total_losers': len(losers),
        'total_flat': len(flat),
        'avg_holding_days': round(avg_holding_days, 1),
    }


def _compute_exit_analysis(trades_df: pd.DataFrame) -> dict:
    """Breakdown of exits by trigger type with average returns per type."""
    exit_types = ['TECHNICAL', 'FUNDAMENTAL', 'RISK_STOP', 'TIME_STOP', 'END_OF_BACKTEST']
    result = {}

    for exit_type in exit_types:
        key = exit_type.lower()
        subset = trades_df[trades_df['exit_reason'] == exit_type]
        result[f'exit_{key}_count'] = len(subset)
        result[f'exit_{key}_avg_return'] = (
            round(float(subset['return_pct'].mean()), 2) if len(subset) > 0 else 0
        )

    return result


def _compute_regime_performance(trades_df: pd.DataFrame,
                                weekly_regimes: list[dict]) -> dict:
    """Returns broken down by the regime at time of entry."""
    result = {}

    if 'regime_at_entry' not in trades_df.columns:
        return {
            'bull_trades': 0, 'bull_return': 0,
            'dip_trades': 0, 'dip_return': 0,
            'sideways_trades': 0, 'sideways_return': 0,
            'bear_trades': 0, 'bear_return': 0,
        }

    for regime in ['BULL', 'DIP', 'SIDEWAYS', 'BEAR']:
        key = regime.lower()
        subset = trades_df[trades_df['regime_at_entry'] == regime]
        result[f'{key}_trades'] = len(subset)
        result[f'{key}_return'] = (
            round(float(subset['return_pct'].mean()), 2) if len(subset) > 0 else 0
        )

    # Regime distribution across simulation period
    if weekly_regimes:
        regime_counts = pd.Series([r['regime_name'] for r in weekly_regimes]).value_counts()
        total = len(weekly_regimes)
        for regime in ['BULL', 'DIP', 'SIDEWAYS', 'BEAR']:
            result[f'{regime.lower()}_weeks_pct'] = (
                round(regime_counts.get(regime, 0) / total * 100, 1)
            )

    return result


def _compute_signal_quality(trades_df: pd.DataFrame) -> dict:
    """Compute signal hit rate and quality metrics."""
    total_signals = len(trades_df)

    if total_signals == 0:
        return {'total_signals_generated': 0, 'signal_hit_rate': 0}

    # Hit rate: what % of signals eventually produced positive returns
    positive = len(trades_df[trades_df['return_pct'] > 0])
    hit_rate = positive / total_signals * 100

    # Best and worst trades
    best_trade = float(trades_df['return_pct'].max())
    worst_trade = float(trades_df['return_pct'].min())

    # Consecutive wins/losses
    signs = (trades_df['return_pct'] > 0).astype(int)
    max_consec_wins = _max_consecutive(signs, 1)
    max_consec_losses = _max_consecutive(signs, 0)

    return {
        'total_signals_generated': total_signals,
        'signal_hit_rate': round(hit_rate, 1),
        'best_trade_return': round(best_trade, 2),
        'worst_trade_return': round(worst_trade, 2),
        'max_consecutive_wins': max_consec_wins,
        'max_consecutive_losses': max_consec_losses,
    }


def _max_consecutive(series: pd.Series, value: int) -> int:
    """Count maximum consecutive occurrences of a value in a series."""
    if series.empty:
        return 0
    groups = (series != value).cumsum()
    counts = series.groupby(groups).sum()
    return int(counts.max()) if len(counts) > 0 else 0


def _empty_metrics() -> dict:
    """Return zeroed metrics when no trades exist."""
    return {
        'avg_return': 0, 'median_return': 0, 'total_return': 0,
        'annualized_return': 0, 'final_portfolio_value': 0,
        'max_drawdown': 0, 'max_drawdown_weeks': 0,
        'sharpe_ratio': 0, 'volatility_annual': 0,
        'win_rate': 0, 'avg_winner': 0, 'avg_loser': 0,
        'profit_factor': 0, 'payoff_ratio': 0,
        'total_winners': 0, 'total_losers': 0, 'total_flat': 0,
        'avg_holding_days': 0,
        'exit_technical_count': 0, 'exit_fundamental_count': 0,
        'exit_risk_stop_count': 0, 'exit_time_stop_count': 0,
        'exit_end_of_backtest_count': 0,
        'exit_technical_avg_return': 0, 'exit_risk_stop_avg_return': 0,
        'exit_fundamental_avg_return': 0, 'exit_time_stop_avg_return': 0,
        'exit_end_of_backtest_avg_return': 0,
        'bull_trades': 0, 'bull_return': 0,
        'dip_trades': 0, 'dip_return': 0,
        'sideways_trades': 0, 'sideways_return': 0,
        'bear_trades': 0, 'bear_return': 0,
        'total_signals_generated': 0, 'signal_hit_rate': 0,
        'best_trade_return': 0, 'worst_trade_return': 0,
        'max_consecutive_wins': 0, 'max_consecutive_losses': 0,
    }


def format_summary_report(metrics: dict) -> str:
    """
    Generate a human-readable summary of backtest results for logging.

    Returns:
        Multi-line formatted string
    """
    lines = [
        f"{'=' * 65}",
        f"  BACKTEST RESULTS — {metrics.get('run_date', 'N/A')}",
        f"  Lookback: {metrics.get('lookback_weeks', 0)} weeks | "
        f"Total Trades: {metrics.get('total_trades', 0)}",
        f"{'=' * 65}",
        "",
        "  RETURNS",
        f"    Total Return:      {metrics.get('total_return', 0):+.2f}%",
        f"    Annualized Return: {metrics.get('annualized_return', 0):+.2f}%",
        f"    Avg Trade Return:  {metrics.get('avg_return', 0):+.2f}%",
        f"    Final Portfolio:   INR {metrics.get('final_portfolio_value', 0):,.0f}",
        "",
        "  RISK",
        f"    Max Drawdown:      {metrics.get('max_drawdown', 0):.2f}%",
        f"    Max DD Duration:   {metrics.get('max_drawdown_weeks', 0)} weeks",
        f"    Sharpe Ratio:      {metrics.get('sharpe_ratio', 0):.2f}",
        f"    Annual Volatility: {metrics.get('volatility_annual', 0):.2f}%",
        "",
        "  WIN / LOSS",
        f"    Win Rate:          {metrics.get('win_rate', 0):.1f}%",
        f"    Avg Winner:        {metrics.get('avg_winner', 0):+.2f}%",
        f"    Avg Loser:         {metrics.get('avg_loser', 0):+.2f}%",
        f"    Profit Factor:     {metrics.get('profit_factor', 0):.2f}",
        f"    Payoff Ratio:      {metrics.get('payoff_ratio', 0):.2f}",
        f"    Avg Holding:       {metrics.get('avg_holding_days', 0):.0f} days",
        "",
        "  EXIT ANALYSIS",
        f"    Technical:         {metrics.get('exit_technical_count', 0)} exits "
        f"(avg {metrics.get('exit_technical_avg_return', 0):+.2f}%)",
        f"    Fundamental:       {metrics.get('exit_fundamental_count', 0)} exits "
        f"(avg {metrics.get('exit_fundamental_avg_return', 0):+.2f}%)",
        f"    Risk Stop:         {metrics.get('exit_risk_stop_count', 0)} exits "
        f"(avg {metrics.get('exit_risk_stop_avg_return', 0):+.2f}%)",
        f"    Time Stop:         {metrics.get('exit_time_stop_count', 0)} exits "
        f"(avg {metrics.get('exit_time_stop_avg_return', 0):+.2f}%)",
        f"    End of Backtest:   {metrics.get('exit_end_of_backtest_count', 0)} exits "
        f"(avg {metrics.get('exit_end_of_backtest_avg_return', 0):+.2f}%)",
        "",
        "  REGIME PERFORMANCE",
        f"    BULL:     {metrics.get('bull_trades', 0)} trades, "
        f"avg {metrics.get('bull_return', 0):+.2f}%",
        f"    DIP:      {metrics.get('dip_trades', 0)} trades, "
        f"avg {metrics.get('dip_return', 0):+.2f}%",
        f"    SIDEWAYS: {metrics.get('sideways_trades', 0)} trades, "
        f"avg {metrics.get('sideways_return', 0):+.2f}%",
        f"    BEAR:     {metrics.get('bear_trades', 0)} trades, "
        f"avg {metrics.get('bear_return', 0):+.2f}%",
        "",
        "  SIGNAL QUALITY",
        f"    Hit Rate:          {metrics.get('signal_hit_rate', 0):.1f}%",
        f"    Best Trade:        {metrics.get('best_trade_return', 0):+.2f}%",
        f"    Worst Trade:       {metrics.get('worst_trade_return', 0):+.2f}%",
        f"    Max Consec Wins:   {metrics.get('max_consecutive_wins', 0)}",
        f"    Max Consec Losses: {metrics.get('max_consecutive_losses', 0)}",
        f"{'=' * 65}",
    ]

    return '\n'.join(lines)

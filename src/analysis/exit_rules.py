"""
Exit Rules Engine — 4 Independent Exit Triggers (PDF p.6-7)

Each position is checked against 4 independent exit rules every day.
ANY single trigger firing = position exits next open.

Exit Triggers:
1. TECHNICAL EXIT:  RS < 0 AND price closes below 20-week MA (confirmed breakdown)
2. FUNDAMENTAL EXIT: QoQ sales drop > 5% in latest reported quarter (PIT)
3. RISK STOP EXIT:   Close < entry - 3×ATR14 (immediate, non-negotiable)
4. TIME STOP EXIT:   Position > 20 weeks (~5 months) old, no fresh trigger = stale

v0.21 research-backed changes:
- ATR stop widened 2x→3x (2x too tight for 3-6M holding, consensus 2.5-3.5x)
- Time stop shortened 26→20 weeks (research: 13-16 weeks optimal, 20 = compromise)
- High-VIX: stop tightened 30% (3×ATR→2.1×ATR), time stop 10 weeks

Output: list of positions with triggered exit rules and reason strings.
"""

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Exit parameters (v0.21: research-backed adjustments)
ATR_STOP_MULTIPLIER = 3.0           # 3× ATR14 from entry (was 2x, too tight for 3-6M)
TIME_STOP_WEEKS_NORMAL = 20         # 20 weeks (~5 months) normal (was 26)
TIME_STOP_WEEKS_HIGH_VIX = 10       # 10 weeks (~2.5 months) when VIX > 20 (was 13)
VIX_HIGH_THRESHOLD = 20             # VIX threshold for tighter stops
VIX_STOP_TIGHTENING = 0.70          # Multiply stop by this (30% tighter)
SALES_DROP_EXIT_THRESHOLD = -0.05   # -5% QoQ sales drop triggers exit
RS_EXIT_THRESHOLD = 0.0             # Mansfield RS must be > 0


def check_exit_rules(positions: list[dict],
                     ohlcv_data: dict[str, pd.DataFrame],
                     fundamentals: dict[str, dict],
                     benchmark_df: pd.DataFrame,
                     current_vix: float = 16.0) -> list[dict]:
    """
    Check all 4 exit triggers for each position.

    Args:
        positions: List of active position dicts, each with:
                   symbol, entry_price, entry_date, atr14_at_entry
        ohlcv_data: Dict {symbol: DataFrame} of daily OHLCV
        fundamentals: Dict {symbol: dict} of quarterly financials
        benchmark_df: Nifty 500 DataFrame for RS calculation
        current_vix: Current India VIX level

    Returns:
        List of exit signal dicts with trigger reason and details
    """
    high_vix = current_vix > VIX_HIGH_THRESHOLD
    exit_signals = []

    for pos in positions:
        symbol = pos.get('symbol')
        entry_price = pos.get('entry_price', 0)
        entry_date_str = pos.get('entry_date', '')
        atr_at_entry = pos.get('atr14_at_entry', 0)

        ohlcv = ohlcv_data.get(symbol, pd.DataFrame())
        if ohlcv.empty:
            continue

        current_close = float(ohlcv['close'].iloc[-1])
        triggers = []

        # ── Trigger 1: TECHNICAL EXIT ──
        # RS < 0 AND price below 20-week (100-day) moving average
        tech_exit = _check_technical_exit(ohlcv, benchmark_df)
        if tech_exit:
            triggers.append(tech_exit)

        # ── Trigger 2: FUNDAMENTAL EXIT ──
        # QoQ sales drop > 5% in latest reported quarter
        f = fundamentals.get(symbol)
        fund_exit = _check_fundamental_exit(f)
        if fund_exit:
            triggers.append(fund_exit)

        # ── Trigger 3: RISK STOP EXIT ──
        # Close < entry - 2×ATR14 (tightened 30% if VIX > 20)
        risk_exit = _check_risk_stop(current_close, entry_price, atr_at_entry, high_vix)
        if risk_exit:
            triggers.append(risk_exit)

        # ── Trigger 4: TIME STOP EXIT ──
        # Position age > 26 weeks (13 weeks if VIX > 20)
        time_exit = _check_time_stop(entry_date_str, high_vix)
        if time_exit:
            triggers.append(time_exit)

        if triggers:
            exit_signals.append({
                'symbol': symbol,
                'close': current_close,
                'entry_price': entry_price,
                'pnl_pct': round((current_close / entry_price - 1) * 100, 2) if entry_price > 0 else 0,
                'triggers': triggers,
                'trigger_count': len(triggers),
                'primary_reason': triggers[0]['type'],  # First trigger fired
                'exit_recommended': True,
            })

    if exit_signals:
        logger.info(
            f"Exit rules: {len(exit_signals)}/{len(positions)} positions triggered "
            f"({'HIGH-VIX MODE' if high_vix else 'normal'})"
        )
    else:
        logger.info(f"Exit rules: no exits triggered for {len(positions)} positions")

    return exit_signals


def _check_technical_exit(ohlcv: pd.DataFrame,
                          benchmark_df: pd.DataFrame) -> dict | None:
    """
    Technical Exit: Mansfield RS < 0 AND close below 20-week MA.

    This is a confirmed trend breakdown — the stock is underperforming
    AND its own price trend has broken down.
    """
    if ohlcv.empty or len(ohlcv) < 100:
        return None

    try:
        close = ohlcv['close']
        current_close = float(close.iloc[-1])

        # 20-week MA ≈ 100 trading days
        ma_100 = float(close.rolling(100).mean().iloc[-1])
        below_ma = current_close < ma_100

        if not below_ma:
            return None  # Price still above 20-week MA, no exit

        # Compute Mansfield RS
        mrs = 0.0
        if benchmark_df is not None and not benchmark_df.empty:
            stock_close = close
            bench_close = benchmark_df['close']
            common = stock_close.index.intersection(bench_close.index)

            if len(common) >= 91:
                rp = stock_close.loc[common] / bench_close.loc[common]
                rp_ma = rp.rolling(91).mean()
                if pd.notna(rp_ma.iloc[-1]) and rp_ma.iloc[-1] != 0:
                    mrs = float(((rp.iloc[-1] / rp_ma.iloc[-1]) - 1) * 100)

        if mrs < RS_EXIT_THRESHOLD:
            return {
                'type': 'TECHNICAL',
                'reason': f'RS={mrs:.1f} < 0 AND close {current_close:.0f} < 20wk MA {ma_100:.0f}',
                'mansfield_rs': round(mrs, 2),
                'ma_100': round(ma_100, 2),
            }

    except Exception as e:
        logger.debug(f"Technical exit check error: {e}")

    return None


def _check_fundamental_exit(f: dict | None) -> dict | None:
    """
    Fundamental Exit: QoQ sales drop > 5%.

    Uses Point-in-Time data — only reacts to actually reported quarters,
    not estimates. ~60-day implicit lag from SEBI LODR filing window.
    """
    if not f:
        return None

    sales_t = f.get('sales_t')
    sales_t1 = f.get('sales_t1')

    if sales_t is not None and sales_t1 is not None and sales_t1 > 0:
        qoq_sales_growth = (sales_t - sales_t1) / abs(sales_t1)

        if qoq_sales_growth < SALES_DROP_EXIT_THRESHOLD:
            return {
                'type': 'FUNDAMENTAL',
                'reason': f'QoQ sales dropped {qoq_sales_growth:.1%} (threshold: {SALES_DROP_EXIT_THRESHOLD:.0%})',
                'qoq_sales_growth': round(qoq_sales_growth * 100, 2),
            }

    return None


def _check_risk_stop(current_close: float, entry_price: float,
                     atr_at_entry: float, high_vix: bool) -> dict | None:
    """
    Risk Stop Exit: Close < Entry - 3×ATR14.

    In high-VIX mode (VIX > 20), stop is tightened by 30%:
    effective stop = Entry - (3×ATR14 × 0.70) = Entry - 2.1×ATR14

    This is non-negotiable — the hard floor that prevents large losses.
    """
    if entry_price <= 0 or atr_at_entry <= 0:
        return None

    stop_distance = atr_at_entry * ATR_STOP_MULTIPLIER
    if high_vix:
        stop_distance *= VIX_STOP_TIGHTENING  # 30% tighter

    stop_price = entry_price - stop_distance

    if current_close < stop_price:
        loss_pct = (current_close / entry_price - 1) * 100
        return {
            'type': 'RISK_STOP',
            'reason': (f'Close {current_close:.0f} < stop {stop_price:.0f} '
                       f'(entry {entry_price:.0f} - {stop_distance:.0f} ATR stop)'
                       f'{" [VIX-tightened]" if high_vix else ""}'),
            'stop_price': round(stop_price, 2),
            'loss_pct': round(loss_pct, 2),
        }

    return None


def _check_time_stop(entry_date_str: str, high_vix: bool) -> dict | None:
    """
    Time Stop Exit: Position older than 20 weeks (10 weeks if VIX > 20).

    Stale positions without fresh catalysts tie up capital.
    High-VIX shortens to ~2.5 months — faster rotation in volatile markets.
    """
    if not entry_date_str:
        return None

    try:
        entry_date = date.fromisoformat(entry_date_str)
        today = date.today()
        holding_days = (today - entry_date).days
        holding_weeks = holding_days / 7

        max_weeks = TIME_STOP_WEEKS_HIGH_VIX if high_vix else TIME_STOP_WEEKS_NORMAL

        if holding_weeks > max_weeks:
            return {
                'type': 'TIME_STOP',
                'reason': (f'Position held {holding_weeks:.0f} weeks > '
                           f'{max_weeks} week limit'
                           f'{" [VIX-shortened]" if high_vix else ""}'),
                'holding_weeks': round(holding_weeks, 1),
                'max_weeks': max_weeks,
            }

    except (ValueError, TypeError) as e:
        logger.debug(f"Time stop date parse error: {e}")

    return None


def summarize_exits(exit_signals: list[dict]) -> str:
    """
    Generate a human-readable summary of exit signals for alerts.

    Returns:
        Formatted string for Telegram/Discord delivery
    """
    if not exit_signals:
        return "No exit triggers fired today."

    lines = [f"⚠️ EXIT SIGNALS ({len(exit_signals)} positions):"]

    for sig in exit_signals:
        symbol = sig['symbol']
        pnl = sig.get('pnl_pct', 0)
        pnl_emoji = '🟢' if pnl > 0 else '🔴'
        triggers = ', '.join(t['type'] for t in sig['triggers'])

        lines.append(
            f"  {pnl_emoji} {symbol}: {triggers} | "
            f"PnL: {pnl:+.1f}% | Close: ₹{sig['close']:.0f}"
        )

        for t in sig['triggers']:
            lines.append(f"      → {t['reason']}")

    return '\n'.join(lines)

"""
Price Target Computation — ATR-Projected Bands & Support/Resistance

Each output stock includes an estimated 4-week price band using:
(a) ATR-projected high and low from current price (asymmetric: 3x up, 2x stop)
(b) 20-week high/low channel for consolidation context

These are price RANGES for position management, NOT precise predictions.
"""

import logging

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def compute_price_targets(symbol: str, ohlcv: pd.DataFrame, atr14: float) -> dict:
    """
    Compute ATR-based price targets and support/resistance levels.

    Args:
        symbol: Stock trading symbol
        ohlcv: Daily OHLCV DataFrame
        atr14: 14-period ATR value

    Returns:
        Dict with target_high, stop_loss, w20_high, w20_low, rs_slope, atr14
    """
    if ohlcv.empty:
        return _empty_targets()

    close = float(ohlcv['close'].iloc[-1])

    # ATR-based band: 3x ATR up, 2x ATR stop (asymmetric for long positions)
    # Risk:Reward = 3:2 = 1.5:1 minimum (target 3:1 with trailing stops)
    target_high = round(close + 3 * atr14, 2)
    stop_loss = round(close - 2 * atr14, 2)

    # 20-week channel (100 trading days ~ 20 weeks)
    lookback = min(100, len(ohlcv))
    w20_high = round(float(ohlcv['high'].tail(lookback).max()), 2)
    w20_low = round(float(ohlcv['low'].tail(lookback).min()), 2)

    # RS slope: 5-day price change as percentage (positive = uptrend)
    rs_slope = 0.0
    if len(ohlcv) >= 6:
        prev = float(ohlcv['close'].iloc[-6])
        rs_slope = round((close / prev - 1) * 100, 2) if prev > 0 else 0.0

    # Proximity to 20-week high (how close to breakout)
    proximity_to_high = round((close / w20_high) * 100, 1) if w20_high > 0 else 0

    # Average daily range as percentage (volatility context)
    avg_daily_range = 0.0
    if len(ohlcv) >= 20:
        daily_ranges = (ohlcv['high'].tail(20) - ohlcv['low'].tail(20)) / ohlcv['close'].tail(20) * 100
        avg_daily_range = round(float(daily_ranges.mean()), 2)

    return {
        'close': close,
        'target_high': target_high,
        'stop_loss': stop_loss,
        'w20_high': w20_high,
        'w20_low': w20_low,
        'rs_slope': rs_slope,
        'atr14': round(atr14, 2),
        'proximity_to_high': proximity_to_high,
        'avg_daily_range_pct': avg_daily_range,
    }


def compute_targets_batch(ohlcv_data: dict[str, pd.DataFrame],
                          atr_values: dict[str, float]) -> dict[str, dict]:
    """Compute price targets for multiple symbols."""
    results = {}
    for symbol, ohlcv in ohlcv_data.items():
        atr = atr_values.get(symbol, 0)
        if atr > 0:
            results[symbol] = compute_price_targets(symbol, ohlcv, atr)
    return results


def _empty_targets() -> dict:
    """Return empty/default target dict."""
    return {
        'close': 0, 'target_high': 0, 'stop_loss': 0,
        'w20_high': 0, 'w20_low': 0, 'rs_slope': 0,
        'atr14': 0, 'proximity_to_high': 0, 'avg_daily_range_pct': 0,
    }

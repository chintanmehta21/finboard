"""
Stage 2 — Multi-Factor Ranking Engine (5 Factors)

Computes 5 uncorrelated factor scores for each stock:
1. Composite Mansfield RS (25%) — 3-horizon relative strength vs Nifty 500
2. Delivery Volume Conviction (20%) — 5d/20d delivery % ratio
3. Volatility-Adjusted Momentum (20%) — 12-1 momentum / 90d volatility
4. Forensic Quality Score (20%) — CCR + M-Score + LVGI composite
5. Earnings Revision Breadth (15%) — proxy: price reaction on result days

All factors are normalized to percentile ranks (0-1) before weighting.
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def mansfield_rs(ohlcv: pd.DataFrame, benchmark_df: pd.DataFrame) -> float:
    """
    Composite Mansfield Relative Strength — 3-horizon blend.

    Measures stock's relative price performance vs Nifty 500 across three
    time horizons, making it more responsive than a single-window approach.

    Horizons: 65-day (~3 months), 91-day (13 weeks), 126-day (6 months)
    Slope must be positive for the signal to be meaningful.

    Args:
        ohlcv: Stock's daily OHLCV DataFrame (indexed by date)
        benchmark_df: Nifty 500 daily close DataFrame

    Returns:
        Composite Mansfield RS score (higher = stronger relative performance)
    """
    if ohlcv.empty or benchmark_df.empty:
        return 0.0

    try:
        # Align dates between stock and benchmark
        stock_close = ohlcv['close']
        bench_close = benchmark_df['close']

        # Relative price ratio: stock / benchmark
        common_dates = stock_close.index.intersection(bench_close.index)
        if len(common_dates) < 130:
            return 0.0

        stock_aligned = stock_close.loc[common_dates]
        bench_aligned = bench_close.loc[common_dates]
        rp = stock_aligned / bench_aligned

        # Compute Mansfield RS for each horizon
        mrs_scores = []
        for window in [65, 91, 126]:
            if len(rp) < window:
                mrs_scores.append(0.0)
                continue

            rp_ma = rp.rolling(window).mean()
            mrs = ((rp / rp_ma) - 1) * 100
            mrs_scores.append(float(mrs.iloc[-1]) if pd.notna(mrs.iloc[-1]) else 0.0)

        # Average across horizons
        composite = np.mean(mrs_scores)

        # Check slope: RS must be trending up (positive 5-day change)
        if len(rp) >= 10:
            rs_slope = float(rp.iloc[-1] / rp.iloc[-6] - 1) if rp.iloc[-6] != 0 else 0
            if rs_slope < 0:
                composite *= 0.5  # Penalize negative slope, don't zero out

        return composite

    except Exception as e:
        logger.debug(f"Mansfield RS computation error: {e}")
        return 0.0


def delivery_conviction(ohlcv: pd.DataFrame, bhavcopy_df: pd.DataFrame,
                        symbol: str) -> float:
    """
    Delivery Volume Conviction — 5d/20d delivery percentage ratio.

    Uses TRUE delivery percentage (not turnover), which filters out
    HFT/intraday churn with zero conviction. Bulk/block deal days are
    excluded programmatically to prevent false signals.

    Args:
        ohlcv: Stock's daily OHLCV DataFrame
        bhavcopy_df: Today's bhavcopy data with delivery percentages
        symbol: Stock symbol for lookup

    Returns:
        Delivery conviction score (higher = stronger institutional accumulation)
    """
    if bhavcopy_df is None or bhavcopy_df.empty:
        return 0.0

    try:
        # Get delivery % for this symbol from bhavcopy
        sym_data = bhavcopy_df[bhavcopy_df['symbol'] == symbol]

        if sym_data.empty:
            return 0.0

        today_deliv_pct = float(sym_data['deliv_pct'].iloc[0])

        # If we have historical bhavcopy data with 'date' column, use rolling averages
        if 'date' in bhavcopy_df.columns:
            sym_hist = bhavcopy_df[bhavcopy_df['symbol'] == symbol].sort_values('date')

            if len(sym_hist) >= 5:
                # Exclude outlier days (potential bulk/block deals: deliv_pct > 95%)
                sym_hist = sym_hist[sym_hist['deliv_pct'] < 95]

                avg_5d = sym_hist['deliv_pct'].tail(5).mean()
                avg_20d = sym_hist['deliv_pct'].tail(20).mean()

                if avg_20d > 0:
                    return avg_5d / avg_20d
            return 1.0  # Neutral if insufficient history

        # Single-day fallback: compare today's delivery % vs typical for the stock
        # Use volume-based proxy if no historical delivery data
        if not ohlcv.empty and 'volume' in ohlcv.columns:
            avg_vol_20d = ohlcv['volume'].tail(20).mean()
            today_vol = ohlcv['volume'].iloc[-1]

            if avg_vol_20d > 0:
                vol_ratio = today_vol / avg_vol_20d
                # Combine delivery % signal with volume surge
                return today_deliv_pct / 50.0 * vol_ratio  # Normalize around 50% delivery

        return today_deliv_pct / 50.0  # Simple normalization

    except Exception as e:
        logger.debug(f"Delivery conviction error for {symbol}: {e}")
        return 0.0


def volatility_adjusted_momentum(ohlcv: pd.DataFrame) -> float:
    """
    12-1 Momentum / 90-day log-return standard deviation.

    12-1 Momentum: 12-month return minus last 1-month return.
    This avoids the short-term reversal effect specific to Indian equities.

    Log-returns ensure scale invariance across price levels.
    Dividing by volatility penalizes fragile momentum (high-vol stocks).

    Returns:
        VAM score (higher = stronger risk-adjusted momentum)
    """
    if ohlcv.empty or len(ohlcv) < 252:
        return 0.0

    try:
        close = ohlcv['close']

        # 12-month return (252 trading days)
        ret_12m = close.iloc[-1] / close.iloc[-252] - 1 if close.iloc[-252] != 0 else 0

        # 1-month return (21 trading days) — skip this
        ret_1m = close.iloc[-1] / close.iloc[-21] - 1 if close.iloc[-21] != 0 else 0

        # 12-1 momentum
        momentum_12_1 = ret_12m - ret_1m

        # 90-day annualized volatility of log-returns
        log_returns = np.log(close / close.shift(1)).dropna()
        vol_90d = log_returns.tail(90).std() * np.sqrt(252)

        if vol_90d <= 0 or np.isnan(vol_90d):
            return 0.0

        # v0.21: Volatility floor at 10% annualized to prevent
        # near-zero vol inflating VAM for illiquid/stale stocks
        vol_90d = max(vol_90d, 0.10)

        vam = momentum_12_1 / vol_90d

        # v0.21: Winsorize at ±3 to prevent extreme outliers
        vam = max(min(float(vam), 3.0), -3.0)

        return vam if np.isfinite(vam) else 0.0

    except Exception as e:
        logger.debug(f"VAM computation error: {e}")
        return 0.0


def earnings_revision_proxy(ohlcv: pd.DataFrame) -> float:
    """
    Earnings Revision Breadth Proxy — price reaction on earnings days.

    Since free-tier data doesn't include analyst revision data, we use a proxy:
    price reaction vs 90-day average daily move on result announcement days.

    A stock that rallies significantly more than its average move on result days
    indicates positive earnings surprise / analyst upgrades.

    Returns:
        Revision proxy score (higher = more positive earnings momentum)
    """
    if ohlcv.empty or len(ohlcv) < 90:
        return 0.0

    try:
        close = ohlcv['close']

        # Compute daily returns
        daily_returns = close.pct_change().dropna()

        if len(daily_returns) < 90:
            return 0.0

        # Average absolute daily move (90-day window)
        avg_abs_move = daily_returns.tail(90).abs().mean()

        if avg_abs_move <= 0:
            return 0.0

        # Look for "earnings reaction" days: days with moves > 2x the average
        # These proxy for result announcement days
        recent_returns = daily_returns.tail(90)
        big_move_days = recent_returns[recent_returns.abs() > 2 * avg_abs_move]

        if len(big_move_days) == 0:
            return 0.5  # Neutral: no significant earnings events detected

        # Net direction of big moves: positive = analysts upgrading (proxy)
        positive_big_moves = (big_move_days > 0).sum()
        total_big_moves = len(big_move_days)

        revision_score = positive_big_moves / total_big_moves

        # Scale by magnitude of the positive moves
        avg_positive_magnitude = big_move_days[big_move_days > 0].mean() if positive_big_moves > 0 else 0
        magnitude_boost = avg_positive_magnitude / avg_abs_move if avg_abs_move > 0 else 1.0

        return float(revision_score * min(magnitude_boost, 3.0))

    except Exception as e:
        logger.debug(f"Earnings revision proxy error: {e}")
        return 0.0

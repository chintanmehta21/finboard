"""
Portfolio Construction Engine

Transforms raw ranked stock list into a risk-aware portfolio using:
1. ATR-based position sizing (equal rupee risk per trade)
2. Sector concentration caps (max 25% in any SEBI sector)
3. Liquidity-scaled position limits (max 2% of stock's ADT)
4. Maximum position cap (15% of total capital)
5. Cyclical/defensive balance (min 20% in defensive sectors)
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Portfolio construction parameters
RISK_PER_TRADE_PCT = 0.01       # 1% of total capital risked per trade
ATR_STOP_MULTIPLIER = 2.0       # Stop loss = 2x ATR14 from entry
MAX_SECTOR_PCT = 0.25           # Max 25% of portfolio in one sector
MAX_POSITION_PCT = 0.15         # Max 15% of capital in one stock
MAX_ADT_PCT = 0.02              # Max 2% of stock's 20-day ADT
MAX_STOCKS = 10                 # Maximum portfolio size
MAX_SAME_SUBINDUSTRY = 2        # Max stocks from same sub-industry
MAX_PORTFOLIO_BETA = 1.3        # Max weighted portfolio beta vs Nifty 500

# Defensive sectors for cyclical/defensive balance
DEFENSIVE_SECTORS = {
    'Fast Moving Consumer Goods', 'FMCG',
    'Healthcare', 'Pharma', 'Pharmaceutical',
    'Information Technology', 'IT',
}
MIN_DEFENSIVE_PCT = 0.20  # Minimum 20% in defensive sectors


def calculate_position_sizes(ranked_df: pd.DataFrame, total_capital: float,
                             regime_scalar: float,
                             sector_map: dict = None,
                             ohlcv_data: dict = None,
                             benchmark_df: pd.DataFrame = None) -> list[dict]:
    """
    Build a portfolio from ranked candidates with all sizing rules applied.

    Args:
        ranked_df: DataFrame ranked by final_score, with columns:
                   symbol, close, atr14, final_score, adj_confidence
        total_capital: Total portfolio capital in INR
        regime_scalar: 0.0-1.0 from regime detection
        sector_map: Dict mapping symbol -> sector name

    Returns:
        List of position dicts with sizing info
    """
    if ranked_df.empty or regime_scalar <= 0:
        return []

    sector_map = sector_map or {}
    risk_per_trade = total_capital * RISK_PER_TRADE_PCT * regime_scalar

    positions = []
    sector_allocation = {}  # sector -> cumulative allocated value
    subindustry_count = {}  # sector -> count of stocks

    for _, row in ranked_df.iterrows():
        if len(positions) >= MAX_STOCKS:
            break

        symbol = row['symbol']
        close = row['close']
        atr14 = row.get('atr14', close * 0.03)  # Fallback: 3% of price
        sector = sector_map.get(symbol, 'Unknown')

        # Skip if sector cap would be breached
        current_sector_alloc = sector_allocation.get(sector, 0)
        if current_sector_alloc >= MAX_SECTOR_PCT * total_capital:
            logger.debug(f"Skipping {symbol}: sector cap breached ({sector})")
            continue

        # Skip if sub-industry cap breached (max 2 from same sector)
        if subindustry_count.get(sector, 0) >= MAX_SAME_SUBINDUSTRY:
            logger.debug(f"Skipping {symbol}: sub-industry cap ({sector})")
            continue

        # ATR-based position sizing
        stop_distance = atr14 * ATR_STOP_MULTIPLIER
        if stop_distance <= 0:
            continue

        shares = risk_per_trade / stop_distance
        pos_value = shares * close

        # Dynamic liquidity cap: max 2% of 20-day ADT
        adt_20d = row.get('adt_20d', float('inf'))
        max_by_liquidity = adt_20d * MAX_ADT_PCT
        pos_value = min(pos_value, max_by_liquidity)

        # Hard position cap: 15% of total capital
        pos_value = min(pos_value, total_capital * MAX_POSITION_PCT)

        # Ensure within remaining sector headroom
        sector_headroom = (MAX_SECTOR_PCT * total_capital) - current_sector_alloc
        pos_value = min(pos_value, sector_headroom)

        if pos_value <= 0:
            continue

        shares = int(pos_value / close)
        if shares <= 0:
            continue

        actual_value = shares * close
        stop_price = round(close - stop_distance, 2)

        positions.append({
            'symbol': symbol,
            'shares': shares,
            'pos_value': round(actual_value, 2),
            'close': round(close, 2),
            'stop_loss': stop_price,
            'atr14': round(atr14, 2),
            'sector': sector,
            'score': round(float(row.get('adj_confidence', row.get('final_score', 0))), 1),
            'pct_of_capital': round(actual_value / total_capital * 100, 1),
        })

        sector_allocation[sector] = current_sector_alloc + actual_value
        subindustry_count[sector] = subindustry_count.get(sector, 0) + 1

    # Check cyclical/defensive balance
    _check_defensive_balance(positions, total_capital)

    # Check portfolio beta cap (PDF p.5 — max weighted beta 1.3 vs Nifty 500)
    if ohlcv_data and benchmark_df is not None and not benchmark_df.empty:
        _enforce_beta_cap(positions, ohlcv_data, benchmark_df, total_capital)

    logger.info(
        f"Portfolio constructed: {len(positions)} positions, "
        f"total allocated: {sum(p['pos_value'] for p in positions):,.0f} INR"
    )

    return positions


def compute_atr14(ohlcv: pd.DataFrame) -> float:
    """
    Compute 14-period Average True Range.

    ATR captures the stock's volatility and is used for:
    - Position sizing (equal rupee risk)
    - Stop loss placement (2x ATR from entry)
    - Price target projection (3x ATR up)
    """
    if ohlcv.empty or len(ohlcv) < 15:
        return 0.0

    high = ohlcv['high']
    low = ohlcv['low']
    close = ohlcv['close']

    # True Range = max(H-L, |H-Prev_C|, |L-Prev_C|)
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)

    atr = tr.rolling(14).mean().iloc[-1]
    return float(atr) if pd.notna(atr) else 0.0


def compute_stock_beta(stock_ohlcv: pd.DataFrame, benchmark_df: pd.DataFrame,
                       window: int = 252) -> float:
    """
    Compute stock's beta vs Nifty 500 using trailing daily returns.

    Beta = Cov(stock, benchmark) / Var(benchmark) over `window` trading days.

    Args:
        stock_ohlcv: Stock's daily OHLCV DataFrame
        benchmark_df: Nifty 500 daily DataFrame (needs 'close' column)
        window: Lookback in trading days (default 252 = 1 year)

    Returns:
        Stock beta (float). Returns 1.0 if insufficient data.
    """
    if stock_ohlcv.empty or benchmark_df.empty:
        return 1.0

    try:
        stock_close = stock_ohlcv['close']
        bench_close = benchmark_df['close']
        common = stock_close.index.intersection(bench_close.index)

        if len(common) < max(60, window // 2):
            return 1.0  # Insufficient overlap

        stock_ret = stock_close.loc[common].pct_change().dropna().tail(window)
        bench_ret = bench_close.loc[common].pct_change().dropna().tail(window)

        # Align lengths
        min_len = min(len(stock_ret), len(bench_ret))
        stock_ret = stock_ret.iloc[-min_len:]
        bench_ret = bench_ret.iloc[-min_len:]

        if len(stock_ret) < 30:
            return 1.0

        cov = np.cov(stock_ret, bench_ret)[0][1]
        var_bench = np.var(bench_ret, ddof=1)

        if var_bench <= 0:
            return 1.0

        beta = cov / var_bench
        return float(np.clip(beta, -2.0, 5.0))  # Sanity clamp

    except Exception as e:
        logger.debug(f"Beta computation error: {e}")
        return 1.0


def _enforce_beta_cap(positions: list[dict],
                      ohlcv_data: dict[str, pd.DataFrame],
                      benchmark_df: pd.DataFrame,
                      total_capital: float):
    """
    Enforce portfolio beta cap of MAX_PORTFOLIO_BETA (1.3 vs Nifty 500).

    If weighted portfolio beta exceeds the cap, iteratively remove the
    highest-beta position from the tail of the ranked list until compliant.
    """
    if not positions:
        return

    total_allocated = sum(p['pos_value'] for p in positions)
    if total_allocated <= 0:
        return

    # Compute individual betas
    for pos in positions:
        ohlcv = ohlcv_data.get(pos['symbol'], pd.DataFrame())
        pos['beta'] = compute_stock_beta(ohlcv, benchmark_df)

    # Compute weighted portfolio beta
    portfolio_beta = sum(
        p['beta'] * (p['pos_value'] / total_allocated)
        for p in positions
    )

    iteration = 0
    max_iterations = len(positions) - 1  # Keep at least 1 position

    while portfolio_beta > MAX_PORTFOLIO_BETA and iteration < max_iterations:
        # Remove highest-beta position (from the end of the ranked list first)
        # Sort by score ascending so we remove lowest-quality high-beta stocks first
        candidates = sorted(positions, key=lambda p: (p['beta'], -p.get('score', 0)), reverse=True)

        removed = candidates[0]
        positions.remove(removed)
        logger.info(
            f"Beta cap: removed {removed['symbol']} (β={removed['beta']:.2f}) "
            f"to bring portfolio β < {MAX_PORTFOLIO_BETA}"
        )

        # Recalculate
        total_allocated = sum(p['pos_value'] for p in positions)
        if total_allocated <= 0:
            break

        portfolio_beta = sum(
            p['beta'] * (p['pos_value'] / total_allocated)
            for p in positions
        )
        iteration += 1

    # Log final portfolio beta
    if positions:
        logger.info(f"Portfolio beta: {portfolio_beta:.2f} (cap: {MAX_PORTFOLIO_BETA})")
    else:
        logger.warning("Beta cap enforcement removed all positions!")


def _check_defensive_balance(positions: list[dict], total_capital: float):
    """Log a warning if defensive allocation is below minimum threshold."""
    total_defensive = sum(
        p['pos_value'] for p in positions
        if p.get('sector', '') in DEFENSIVE_SECTORS
    )
    total_allocated = sum(p['pos_value'] for p in positions)

    if total_allocated > 0:
        defensive_pct = total_defensive / total_allocated
        if defensive_pct < MIN_DEFENSIVE_PCT:
            logger.warning(
                f"Defensive allocation {defensive_pct:.1%} below minimum {MIN_DEFENSIVE_PCT:.0%}. "
                f"Consider adding FMCG/Pharma/IT names."
            )

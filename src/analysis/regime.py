"""
Stage 3 — Macro & Regime Overlay (4-State Detection)

Converts market regime into a continuous exposure scalar instead of binary on/off.
This eliminates whipsaw around the 200 DMA boundary.

Four regimes:
1. STRUCTURAL BULL (100% exposure) — Nifty > 200 DMA, VIX < 16, INR stable
2. RISK-ON DIP (60% exposure) — Near 200 DMA or RSI oversold, trend intact
3. VOLATILE SIDEWAYS (30% exposure) — VIX 16-24, oscillating around 200 DMA
4. BEAR / FII FLIGHT (10% exposure) — Below 200 DMA or VIX 3d avg > 24 or INR crash

Each regime also shifts factor weights to prioritize appropriate signals.
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Regime-specific factor weight configurations (4 factors)
# Keys: rs, del, vam, rev
# v0.21: FQ removed — negative IC (-0.17/-0.23) confirmed by backtest
# Its weight redistributed to remaining factors. MRS-VAM combined capped ~55%
# to mitigate their 0.64 Spearman correlation (41% shared variance).
REGIME_WEIGHTS = {
    'BULL': {'rs': 0.40, 'del': 0.20, 'vam': 0.20, 'rev': 0.20},
    'DIP': {'rs': 0.30, 'del': 0.30, 'vam': 0.15, 'rev': 0.25},
    'SIDEWAYS': {'rs': 0.20, 'del': 0.35, 'vam': 0.15, 'rev': 0.30},
    'BEAR': {'rs': 0.35, 'del': 0.20, 'vam': 0.20, 'rev': 0.25},
}

# v0.2: BEAR gets 10% exposure (1-2 defensive positions) instead of 0%
REGIME_SCALARS = {
    'BULL': 1.0,
    'DIP': 0.6,
    'SIDEWAYS': 0.3,
    'BEAR': 0.1,
}


def get_regime(nifty_df: pd.DataFrame, vix_df: pd.DataFrame,
               usdinr_df: pd.DataFrame, fii_df: pd.DataFrame = None,
               **kwargs) -> tuple[float, str, dict]:
    """
    Detect current market regime and return exposure parameters.

    Args:
        nifty_df: Nifty 500 daily OHLCV (needs 'close' column)
        vix_df: India VIX daily data (needs 'close' column)
        usdinr_df: USD/INR daily data (needs 'close' column)
        fii_df: FII/DII flow data (optional, for corroboration)

    Returns:
        Tuple of (regime_scalar, regime_name, factor_weights_dict)
    """
    # Extract current values with safe fallbacks
    nifty_close = _safe_last(nifty_df, 'close', 0)
    nifty_ma200 = _safe_rolling_mean(nifty_df, 'close', 200)
    vix = _safe_last(vix_df, 'close', 18)  # Default: moderate VIX
    usdinr_current = _safe_last(usdinr_df, 'close', 83)
    usdinr_30d_ago = _safe_nth_last(usdinr_df, 'close', 30, 83)

    # Computed indicators
    nifty_rsi = _compute_rsi(nifty_df['close'], 14) if not nifty_df.empty else 50
    inr_move_30d = ((usdinr_current / usdinr_30d_ago) - 1) * 100 if usdinr_30d_ago > 0 else 0
    dma_distance = ((nifty_close - nifty_ma200) / nifty_ma200) * 100 if nifty_ma200 > 0 else 0

    # DII net buying signal (optional corroboration)
    dii_buying = True  # Default: assume DII supportive
    if fii_df is not None and not fii_df.empty:
        dii_net = fii_df['dii_net_30d'].iloc[-1] if 'dii_net_30d' in fii_df.columns else 0
        dii_buying = dii_net > 0

    logger.info(
        f"Regime inputs: Nifty={nifty_close:.0f}, 200DMA={nifty_ma200:.0f} "
        f"({dma_distance:+.1f}%), VIX={vix:.1f}, INR 30d={inr_move_30d:+.1f}%, "
        f"RSI={nifty_rsi:.1f}"
    )

    # v0.21: VIX uses 3-day average to prevent single-day spike whipsaw
    vix_3d = _safe_rolling_mean_n(vix_df, 'close', 3) if not vix_df.empty else vix

    # === BEAR REGIME ===
    # Nifty below 200 DMA OR INR depreciates > 2% in 30 days OR VIX 3d avg > 24
    if nifty_close < nifty_ma200 or inr_move_30d > 2.0 or vix_3d > 24:
        regime = 'BEAR'
        logger.info(f"REGIME: {regime} (10% exposure — defensive only)")
        return REGIME_SCALARS[regime], regime, REGIME_WEIGHTS[regime]

    # === RISK-ON DIP ===
    # Nifty within 3% of 200 DMA OR RSI oversold, but trend still intact
    if abs(dma_distance) < 3.0 or nifty_rsi < 40:
        regime = 'DIP'
        logger.info(f"REGIME: {regime} (60% exposure)")
        return REGIME_SCALARS[regime], regime, REGIME_WEIGHTS[regime]

    # === VOLATILE SIDEWAYS ===
    # VIX between 16-24, market oscillating around key levels
    if vix > 16 and vix <= 24:
        regime = 'SIDEWAYS'
        logger.info(f"REGIME: {regime} (30% exposure)")
        return REGIME_SCALARS[regime], regime, REGIME_WEIGHTS[regime]

    # === STRUCTURAL BULL ===
    # Nifty > 200 DMA, VIX < 16, INR stable, DII supportive
    regime = 'BULL'
    logger.info(f"REGIME: {regime} (100% exposure)")
    return REGIME_SCALARS[regime], regime, REGIME_WEIGHTS[regime]


def get_macro_snapshot(nifty_df: pd.DataFrame, vix_df: pd.DataFrame,
                       usdinr_df: pd.DataFrame, fii_data: dict) -> dict:
    """
    Build a macro snapshot dict for output formatting.

    Returns:
        Dict with displayable macro data for Telegram/Discord/Dashboard
    """
    nifty_close = _safe_last(nifty_df, 'close', 0)
    nifty_ma200 = _safe_rolling_mean(nifty_df, 'close', 200)
    vix = _safe_last(vix_df, 'close', 0)
    usdinr = _safe_last(usdinr_df, 'close', 0)
    usdinr_30d = _safe_nth_last(usdinr_df, 'close', 30, 0)

    dma_pct = ((nifty_close / nifty_ma200) - 1) * 100 if nifty_ma200 > 0 else 0
    inr_30d = ((usdinr / usdinr_30d) - 1) * 100 if usdinr_30d > 0 else 0

    return {
        'nifty_close': round(nifty_close, 2),
        'nifty_200dma': round(nifty_ma200, 2),
        'nifty_dma_pct': round(dma_pct, 1),
        'india_vix': round(vix, 1),
        'usdinr': round(usdinr, 2),
        'usdinr_30d_move': round(inr_30d, 2),
        'fii_net': round(fii_data.get('fii_net', 0), 0),
        'dii_net': round(fii_data.get('dii_net', 0), 0),
    }


def _compute_rsi(close_series: pd.Series, period: int = 14) -> float:
    """Compute RSI (Relative Strength Index) for a close price series."""
    if close_series.empty or len(close_series) < period + 1:
        return 50.0  # Neutral default

    delta = close_series.diff()
    gain = delta.clip(lower=0).rolling(window=period).mean()
    loss = (-delta.clip(upper=0)).rolling(window=period).mean()

    rs = gain / (loss + 1e-9)
    rsi = 100 - (100 / (1 + rs))

    last_rsi = rsi.iloc[-1]
    return float(last_rsi) if pd.notna(last_rsi) else 50.0


def _safe_last(df: pd.DataFrame, col: str, default: float) -> float:
    """Safely get the last value from a DataFrame column."""
    if df is None or df.empty or col not in df.columns:
        return default
    val = df[col].iloc[-1]
    return float(val) if pd.notna(val) else default


def _safe_nth_last(df: pd.DataFrame, col: str, n: int, default: float) -> float:
    """Safely get the nth-from-last value."""
    if df is None or df.empty or col not in df.columns or len(df) < n:
        return default
    val = df[col].iloc[-n]
    return float(val) if pd.notna(val) else default


def _safe_rolling_mean(df: pd.DataFrame, col: str, window: int) -> float:
    """Safely compute rolling mean of last value."""
    if df is None or df.empty or col not in df.columns or len(df) < window:
        return 0.0
    val = df[col].rolling(window).mean().iloc[-1]
    return float(val) if pd.notna(val) else 0.0


def _safe_rolling_mean_n(df: pd.DataFrame, col: str, n: int) -> float:
    """Safely compute mean of last n values (for VIX confirmation)."""
    if df is None or df.empty or col not in df.columns:
        return 0.0
    vals = df[col].tail(n)
    mean_val = vals.mean()
    return float(mean_val) if pd.notna(mean_val) else 0.0

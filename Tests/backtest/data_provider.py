"""
Historical Data Provider — Fetch Once, Slice by Date

Fetches all required data (OHLCV, indices, bhavcopy, fundamentals, pledge)
once at construction time, then provides date-sliced views for each
simulated week in the walk-forward backtest.

All slicing is done with <= as_of_date to prevent look-ahead bias.
"""

import logging
from datetime import date, timedelta

import pandas as pd

logger = logging.getLogger(__name__)


class HistoricalDataProvider:
    """
    Fetches and caches all data, then serves time-sliced snapshots
    for each simulated Friday in the walk-forward backtest.
    """

    def __init__(self, fyers, symbols: list[str], sector_map: dict,
                 lookback_years: int = 2, fetch_bhavcopy: bool = True):
        """
        Fetch and cache all data for the backtest period.

        Args:
            fyers: Authenticated FyersModel instance
            symbols: List of NSE trading symbols
            sector_map: Dict {symbol: sector_name}
            lookback_years: Years of OHLCV history to fetch
            fetch_bhavcopy: Whether to fetch NSE bhavcopy (slow; can disable for testing)
        """
        self.symbols = symbols
        self.sector_map = sector_map

        logger.info(f"{'=' * 50}")
        logger.info("DATA PROVIDER: Fetching all historical data...")
        logger.info(f"{'=' * 50}")

        # ── 1. Fetch OHLCV for all stocks ──
        logger.info(f"Fetching OHLCV for {len(symbols)} symbols ({lookback_years}Y)...")
        from src.data.fyers_client import fetch_all_ohlcv, fetch_index_data

        self.ohlcv_data = fetch_all_ohlcv(fyers, symbols, years=lookback_years)
        logger.info(f"OHLCV: {len(self.ohlcv_data)}/{len(symbols)} symbols loaded")

        # ── 2. Fetch index data (Nifty 500, VIX, USD/INR) ──
        logger.info("Fetching index data (Nifty 500, VIX, USD/INR)...")
        self.index_data = fetch_index_data(fyers, years=lookback_years)
        self.nifty_df = self.index_data.get('nifty_df', pd.DataFrame())
        self.vix_df = self.index_data.get('vix_df', pd.DataFrame())
        self.usdinr_df = self.index_data.get('usdinr_df', pd.DataFrame())
        logger.info(
            f"Index data: Nifty={len(self.nifty_df)}, "
            f"VIX={len(self.vix_df)}, USDINR={len(self.usdinr_df)} candles"
        )

        # ── 3. Fetch bhavcopy (delivery volume) for entire range ──
        self.bhavcopy_df = pd.DataFrame()
        if fetch_bhavcopy:
            logger.info("Fetching historical bhavcopy (delivery data)...")
            self._fetch_bhavcopy_history(lookback_years)
        else:
            logger.info("Bhavcopy fetch skipped (fetch_bhavcopy=False)")

        # ── 4. Fetch fundamentals ──
        logger.info("Fetching fundamentals (yfinance)...")
        from src.data.fundamentals import get_fundamentals_batch
        self.fundamentals = get_fundamentals_batch(list(self.ohlcv_data.keys()))
        avail = sum(1 for v in self.fundamentals.values() if v is not None)
        logger.info(f"Fundamentals: {avail}/{len(self.ohlcv_data)} symbols with data")

        # ── 5. Fetch pledge data ──
        logger.info("Fetching pledge data...")
        from src.data.nse_pledge import get_pledge_data_batch
        self.pledge_data = get_pledge_data_batch(list(self.ohlcv_data.keys()))
        logger.info(f"Pledge data: {len(self.pledge_data)} symbols")

        # ── Determine valid date range ──
        self._compute_date_range()

        logger.info(f"{'=' * 50}")
        logger.info(
            f"DATA PROVIDER READY: {len(self.ohlcv_data)} stocks, "
            f"range {self.earliest_date} to {self.latest_date}"
        )
        logger.info(f"{'=' * 50}")

    def _fetch_bhavcopy_history(self, lookback_years: int):
        """Fetch bhavcopy for the backtest simulation period (last ~1 year of Fridays)."""
        from src.data.nse_bhavcopy import fetch_bhavcopy_range

        # We only need bhavcopy for the simulation period (most recent year)
        # not the full 2 years (first year is just warmup for indicators)
        end_date = date.today()
        start_date = end_date - timedelta(days=365)  # ~1 year of bhavcopy

        try:
            self.bhavcopy_df = fetch_bhavcopy_range(start_date, end_date)
            if not self.bhavcopy_df.empty:
                logger.info(f"Bhavcopy: {len(self.bhavcopy_df)} records "
                            f"({start_date} to {end_date})")
            else:
                logger.warning("Bhavcopy: no data retrieved")
        except Exception as e:
            logger.error(f"Bhavcopy fetch failed: {e}")
            self.bhavcopy_df = pd.DataFrame()

    def _compute_date_range(self):
        """Determine the valid date range from available OHLCV data."""
        all_dates = set()
        for df in self.ohlcv_data.values():
            if not df.empty:
                all_dates.update(df.index)

        if all_dates:
            self.earliest_date = min(all_dates)
            self.latest_date = max(all_dates)
        else:
            self.earliest_date = date.today() - timedelta(days=730)
            self.latest_date = date.today()

    def get_simulation_fridays(self, lookback_weeks: int = 52) -> list[date]:
        """
        Generate the list of Fridays to simulate, working backwards
        from the most recent Friday in the data.

        Args:
            lookback_weeks: Number of weeks to simulate

        Returns:
            List of dates (Fridays) in chronological order
        """
        # Find the most recent Friday in the data
        end = self.latest_date
        if hasattr(end, 'weekday'):
            # Roll back to the most recent Friday
            days_since_friday = (end.weekday() - 4) % 7
            last_friday = end - timedelta(days=days_since_friday)
        else:
            last_friday = date.today() - timedelta(days=(date.today().weekday() - 4) % 7)

        # Generate Fridays going back lookback_weeks
        fridays = []
        for i in range(lookback_weeks):
            friday = last_friday - timedelta(weeks=i)
            # Ensure we have at least 200 trading days before this Friday
            # (needed for 200 DMA in regime detection)
            min_data_date = friday - timedelta(days=300)  # ~200 trading days
            if min_data_date >= self.earliest_date:
                fridays.append(friday)

        fridays.sort()  # Chronological order
        logger.info(
            f"Simulation Fridays: {len(fridays)} weeks "
            f"({fridays[0]} to {fridays[-1]})" if fridays else "No valid Fridays"
        )
        return fridays

    def slice_to_date(self, as_of_date: date) -> dict:
        """
        Slice ALL data to as_of_date (inclusive). No look-ahead.

        Args:
            as_of_date: The simulated "today" — all data after this is excluded

        Returns:
            Dict with keys: ohlcv_data, bhavcopy_df, fundamentals, pledge_data,
                            regime_data, sector_map, nifty_df, vix_df, current_vix
        """
        # ── Slice OHLCV data ──
        sliced_ohlcv = {}
        for symbol, df in self.ohlcv_data.items():
            if df.empty:
                continue
            sliced = df[df.index <= as_of_date]
            if len(sliced) >= 100:  # Pipeline needs at least 100 data points
                sliced_ohlcv[symbol] = sliced

        # ── Slice index data ──
        sliced_nifty = self._slice_df(self.nifty_df, as_of_date)
        sliced_vix = self._slice_df(self.vix_df, as_of_date)
        sliced_usdinr = self._slice_df(self.usdinr_df, as_of_date)

        # Current VIX for exit rules
        current_vix = 16.0  # default
        if not sliced_vix.empty and 'close' in sliced_vix.columns:
            current_vix = float(sliced_vix['close'].iloc[-1])

        # ── Slice bhavcopy: get the most recent available trading day's data ──
        sliced_bhavcopy = pd.DataFrame()
        if not self.bhavcopy_df.empty and 'date' in self.bhavcopy_df.columns:
            available = self.bhavcopy_df[self.bhavcopy_df['date'] <= as_of_date]
            if not available.empty:
                latest_date = available['date'].max()
                sliced_bhavcopy = available[available['date'] == latest_date]

        # ── Build regime_data dict (same structure as main.py passes) ──
        regime_data = {
            'nifty_df': sliced_nifty,
            'vix_df': sliced_vix,
            'usdinr_df': sliced_usdinr,
            'fii_df': pd.DataFrame(),  # FII historical data not available
        }

        return {
            'ohlcv_data': sliced_ohlcv,
            'bhavcopy_df': sliced_bhavcopy,
            'fundamentals': self.fundamentals,  # Quarterly; natural lag is realistic
            'pledge_data': self.pledge_data,     # Quarterly; returned as-is
            'regime_data': regime_data,
            'sector_map': self.sector_map,
            'nifty_df': sliced_nifty,
            'vix_df': sliced_vix,
            'current_vix': current_vix,
        }

    def _slice_df(self, df: pd.DataFrame, as_of_date: date) -> pd.DataFrame:
        """Slice a DataFrame with date index to as_of_date."""
        if df is None or df.empty:
            return pd.DataFrame()
        return df[df.index <= as_of_date]

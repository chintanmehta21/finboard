"""
JSON Export — Dashboard Data Feed

Exports pipeline results as a JSON file for the Vercel-hosted web dashboard.
The dashboard reads this JSON as static data, updated daily by GitHub Actions.

Includes backup logic: previous signals.json is preserved as signals_prev.json
before overwriting, so the dashboard always has data to display.
"""

import json
import logging
import shutil
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytz

from src.config import SYSTEM_NAME

logger = logging.getLogger(__name__)

IST = pytz.timezone('Asia/Kolkata')
EXPORT_DIR = Path('dashboard/public/data')


def export_signals(result: dict, output_path: str = None) -> str:
    """
    Export pipeline results as JSON for dashboard consumption.

    Uses last_trading_date from result for date fields instead of datetime.now().
    Backs up previous signals.json before overwriting.

    Args:
        result: Pipeline output dict
        output_path: Custom output path (default: dashboard/public/data/signals.json)

    Returns:
        Path to the exported JSON file
    """
    export_path = Path(output_path) if output_path else EXPORT_DIR / 'signals.json'
    export_path.parent.mkdir(parents=True, exist_ok=True)

    # Backup existing file before overwriting
    if export_path.exists():
        backup_path = export_path.parent / 'signals_prev.json'
        try:
            shutil.copy2(export_path, backup_path)
            logger.debug(f"Backed up previous signals to {backup_path}")
        except Exception as e:
            logger.warning(f"Failed to backup signals.json: {e}")

    now = datetime.now(IST)

    # Use last_trading_date from pipeline result (not datetime.now())
    last_trading_date = result.get('last_trading_date')
    if last_trading_date:
        if isinstance(last_trading_date, date):
            display_date = last_trading_date.strftime('%A, %d %b %Y')
            date_str = last_trading_date.isoformat()
        else:
            display_date = str(last_trading_date)
            date_str = str(last_trading_date)
    else:
        display_date = now.strftime('%A, %d %b %Y')
        date_str = now.strftime('%Y-%m-%d')

    data = {
        'generated_at': now.isoformat(),
        'date': date_str,
        'display_date': display_date,
        'sample_mode': result.get('sample_mode', False),
        'regime': {
            'name': result.get('regime_name', 'UNKNOWN'),
            'scalar': result.get('regime_scalar', 0),
            'exposure_pct': int(result.get('regime_scalar', 0) * 100),
        },
        'macro': result.get('macro_snapshot', {}),
        'pipeline_stats': result.get('pipeline_stats', {}),
        'factor_weights': result.get('factor_weights', {}),
        'bullish': _df_to_records(result.get('bullish', pd.DataFrame())),
        'bearish': _df_to_records(result.get('bearish', pd.DataFrame())),
    }

    export_path.write_text(json.dumps(data, indent=2, default=str))
    logger.info(f"Signals exported to {export_path}")

    return str(export_path)


def _df_to_records(df) -> list[dict]:
    """Convert DataFrame or list to list of dicts, handling NaN values."""
    if df is None:
        return []

    # Handle list input (already list of dicts)
    if isinstance(df, list):
        records = df
    elif isinstance(df, pd.DataFrame):
        if df.empty:
            return []
        records = df.to_dict('records')
    else:
        return []

    # Clean NaN/inf values for JSON serialization
    clean_records = []
    for record in records:
        clean = {}
        for k, v in record.items():
            if isinstance(v, float):
                if pd.isna(v) or not pd.api.types.is_float(v):
                    clean[k] = 0
                elif v == float('inf') or v == float('-inf'):
                    clean[k] = 0
                else:
                    clean[k] = round(v, 4)
            else:
                clean[k] = v
        clean_records.append(clean)

    return clean_records

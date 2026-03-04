"""
JSON Export — Dashboard Data Feed

Exports pipeline results as a JSON file for the Vercel-hosted web dashboard.
The dashboard reads this JSON as static data, updated daily by GitHub Actions.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytz

logger = logging.getLogger(__name__)

IST = pytz.timezone('Asia/Kolkata')
EXPORT_DIR = Path('dashboard/public/data')


def export_signals(result: dict, output_path: str = None) -> str:
    """
    Export pipeline results as JSON for dashboard consumption.

    Args:
        result: Pipeline output dict
        output_path: Custom output path (default: dashboard/public/data/signals.json)

    Returns:
        Path to the exported JSON file
    """
    export_path = Path(output_path) if output_path else EXPORT_DIR / 'signals.json'
    export_path.parent.mkdir(parents=True, exist_ok=True)

    now = datetime.now(IST)

    data = {
        'generated_at': now.isoformat(),
        'date': now.strftime('%Y-%m-%d'),
        'display_date': now.strftime('%A, %d %b %Y'),
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


def _df_to_records(df: pd.DataFrame) -> list[dict]:
    """Convert DataFrame to list of dicts, handling NaN values."""
    if df is None or df.empty:
        return []

    records = df.to_dict('records')

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

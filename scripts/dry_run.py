"""
Dry-run: Test Telegram + Discord notifications using sample signals.json.
Validates the output pipeline without needing Fyers API.

Run: python scripts/dry_run.py
"""

import json
import sys
import logging
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)-25s | %(levelname)-7s | %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger('dry_run')

# Load sample signals.json
sample_path = Path('dashboard/public/data/signals.json')
if not sample_path.exists():
    logger.error(f"No signals.json found at {sample_path}. Cannot run dry test.")
    sys.exit(1)

sample = json.loads(sample_path.read_text())
logger.info(f"Loaded sample data: {len(sample.get('bullish', []))} bullish, "
            f"{len(sample.get('bearish', []))} bearish candidates")

# Convert to the format expected by send_signal_report (DataFrames)
import pandas as pd

result = {
    'regime_name': sample['regime']['name'],
    'regime_scalar': sample['regime']['scalar'],
    'bullish': pd.DataFrame(sample.get('bullish', [])),
    'bearish': pd.DataFrame(sample.get('bearish', [])),
    'macro_snapshot': sample.get('macro', {}),
    'pipeline_stats': sample.get('pipeline_stats', {}),
    'factor_weights': sample.get('factor_weights', {}),
}

logger.info("=== DRY RUN — Testing Output Pipeline ===")
logger.info(f"Regime: {result['regime_name']} (scalar={result['regime_scalar']})")

# Test Telegram
from src.output.telegram_bot import send_signal_report as tg_send

logger.info("Sending to Telegram...")
tg_ok = tg_send(result)
logger.info(f"Telegram: {'SENT' if tg_ok else 'SKIPPED (credentials not set or send failed)'}")

# Test Discord
from src.output.discord_bot import send_signal_report as dc_send

logger.info("Sending to Discord...")
dc_ok = dc_send(result)
logger.info(f"Discord: {'SENT' if dc_ok else 'SKIPPED (credentials not set or send failed)'}")

# Summary
logger.info("=== DRY RUN COMPLETE ===")
if tg_ok and dc_ok:
    logger.info("Both Telegram and Discord notifications sent successfully!")
elif tg_ok:
    logger.info("Telegram sent, Discord skipped.")
elif dc_ok:
    logger.info("Discord sent, Telegram skipped.")
else:
    logger.warning("Neither Telegram nor Discord sent. Check credentials in Admin/.env")

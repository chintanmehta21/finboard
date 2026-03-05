"""
Finboard — System Configuration

Single source of truth for system name, version, and shared constants.
All modules import from here instead of hardcoding names.
"""

SYSTEM_CONFIG = {
    "system": {
        "name": "Finboard",
        "version": "2.0",
        "full_name": "Finboard v2.0",
        "universe_pct": 1.0,  # 1.0 = 100% of NSE 500 for daily run; 0.05-0.10 for tests
    },
    "output": {
        "telegram_top_n": 5,
        "discord_top_n": 5,
        "dashboard_top_n": 10,
    },
}

# Convenience accessors
SYSTEM_NAME = SYSTEM_CONFIG["system"]["name"]
SYSTEM_VERSION = SYSTEM_CONFIG["system"]["version"]
SYSTEM_FULL_NAME = SYSTEM_CONFIG["system"]["full_name"]
UNIVERSE_PCT = SYSTEM_CONFIG["system"]["universe_pct"]
TELEGRAM_TOP_N = SYSTEM_CONFIG["output"]["telegram_top_n"]
DISCORD_TOP_N = SYSTEM_CONFIG["output"]["discord_top_n"]
DASHBOARD_TOP_N = SYSTEM_CONFIG["output"]["dashboard_top_n"]
DIVIDER_TELEGRAM = "\u2501" * 28

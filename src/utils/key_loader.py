"""
Key Loader — Single Source of Truth for API Credentials

Uses python-dotenv (the industry-standard .env loader) to read
Admin/.env for local development, with automatic fallback to
os.environ for production (GitHub Actions uses Secrets).

Priority order:
1. os.environ (GitHub Actions / Docker / system env — always wins)
2. Admin/.env file (local development — loaded by dotenv, does NOT
   override existing env vars)

The Admin/ folder is gitignored and never leaves the local machine.
"""

import os
import logging
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Locate Admin/.env relative to project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # src/utils/ → project root
_ENV_PATH = _PROJECT_ROOT / 'Admin' / '.env'

# Load once at module import
_loaded = False


def _ensure_loaded():
    """Load Admin/.env into os.environ (once). Does NOT override existing env vars."""
    global _loaded
    if _loaded:
        return
    _loaded = True

    if _ENV_PATH.exists():
        # override=False → existing env vars (e.g. GitHub Secrets) take priority
        loaded = load_dotenv(_ENV_PATH, override=False)
        if loaded:
            logger.info(f"Loaded environment from {_ENV_PATH}")
        else:
            logger.info("Admin/.env found but no new keys loaded")
    else:
        logger.info(
            "Admin/.env not found — using environment variables only "
            "(expected in production)"
        )


def get_key(key_name: str, default: str = '') -> str:
    """
    Get an API key or secret by name.

    Args:
        key_name: Variable name (e.g. 'FYERS_APP_ID', 'TELEGRAM_TOKEN')
        default: Fallback if not found anywhere

    Returns:
        The value, or default if not found
    """
    _ensure_loaded()
    value = os.environ.get(key_name, default)

    # Treat placeholder values as not set
    if value and value.startswith('your_'):
        return default

    return value


def require_key(key_name: str) -> str:
    """
    Get a required key — raises RuntimeError if missing.

    Use for keys the module cannot function without
    (e.g. FYERS_APP_ID for authentication).
    """
    value = get_key(key_name)
    if not value:
        raise RuntimeError(
            f"Required key '{key_name}' not found. "
            f"Set it in Admin/.env (local) or as a GitHub Secret (production)."
        )
    return value


def reload_env():
    """Force reload Admin/.env (e.g., after file was updated during session)."""
    global _loaded
    _loaded = False
    _ensure_loaded()
    logger.info("Environment reloaded from Admin/.env")


def get_all_keys() -> dict[str, str]:
    """
    Return all known keys with masked values (for debugging/logging).
    """
    _ensure_loaded()

    known_keys = [
        'FYERS_APP_ID', 'FYERS_SECRET', 'FYERS_CLIENT_ID',
        'FYERS_TOTP_KEY', 'FYERS_PIN',
        'TELEGRAM_TOKEN', 'TELEGRAM_CHAT',
        'DISCORD_WEBHOOK_URL',
    ]

    result = {}
    for k in known_keys:
        v = os.environ.get(k, '')
        if v and not v.startswith('your_'):
            result[k] = f"{v[:4]}...{v[-4:]}" if len(v) > 8 else "****"
        else:
            result[k] = "(not set)"

    return result

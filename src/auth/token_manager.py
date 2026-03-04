"""
Fyers TOTP Headless Authentication & Token Management

Handles the complete token lifecycle:
1. Try cached access token first
2. If expired, try refresh token (15-day validity)
3. If refresh expired, fall back to full TOTP headless login
4. Cache new tokens for subsequent runs

Credentials loaded via key_loader: Admin/.env (local) or env vars (production).
"""

import json
import time
import hashlib
import logging
from pathlib import Path

import pyotp
import requests
from fyers_apiv3 import fyersModel

from src.utils.key_loader import require_key

logger = logging.getLogger(__name__)

CACHE_DIR = Path('.token_cache')
CACHE_FILE = CACHE_DIR / 'fyers_tokens.json'

# Fyers auth endpoints
FYERS_LOGIN_URL = 'https://api-t2.fyers.in/vagator/v2/send_login_otp_v2'
FYERS_VERIFY_URL = 'https://api-t2.fyers.in/vagator/v2/verify_otp'
FYERS_VERIFY_PIN_URL = 'https://api-t2.fyers.in/vagator/v2/verify_pin_v2'
FYERS_TOKEN_URL = 'https://api-t1.fyers.in/api/v3/token'
FYERS_REFRESH_URL = 'https://api-t1.fyers.in/api/v3/validate-refresh-token'


def get_valid_access_token() -> str:
    """Return a valid Fyers access token, refreshing or re-authing as needed."""
    CACHE_DIR.mkdir(exist_ok=True)

    # Try cached token first
    if CACHE_FILE.exists():
        try:
            cache = json.loads(CACHE_FILE.read_text())

            # Check if access token is still valid (< 23 hours old for safety margin)
            if cache.get('access_token') and cache.get('access_ts', 0) > time.time() - 82800:
                logger.info("Using cached access token (still valid)")
                return cache['access_token']

            # Try refresh token (15-day validity, use 14 days for safety)
            if cache.get('refresh_token') and cache.get('refresh_ts', 0) > time.time() - 14 * 86400:
                logger.info("Access token expired, attempting refresh...")
                token_data = _refresh_via_token(cache['refresh_token'])
                if token_data and token_data.get('access_token'):
                    _save_cache(token_data)
                    return token_data['access_token']
                logger.warning("Refresh failed, falling back to TOTP auth")
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Cache read error: {e}, proceeding with fresh auth")

    # Full TOTP headless authentication
    logger.info("Performing full TOTP headless authentication...")
    token_data = _totp_headless_login()
    _save_cache(token_data)
    return token_data['access_token']


def get_fyers_instance() -> fyersModel.FyersModel:
    """Return an authenticated FyersModel instance ready for API calls."""
    access_token = get_valid_access_token()
    client_id = require_key('FYERS_APP_ID')

    fyers = fyersModel.FyersModel(
        client_id=client_id,
        is_async=False,
        token=access_token,
        log_path=str(Path('logs'))
    )
    logger.info("Fyers API client initialized successfully")
    return fyers


def _totp_headless_login() -> dict:
    """
    Perform full TOTP-based headless login to Fyers.

    Flow:
    1. Send login OTP request with client_id
    2. Generate TOTP code using pyotp
    3. Verify TOTP to get request_key
    4. Verify PIN to get auth_code
    5. Exchange auth_code for access_token + refresh_token
    """
    client_id = require_key('FYERS_CLIENT_ID')
    totp_key = require_key('FYERS_TOTP_KEY')
    pin = require_key('FYERS_PIN')
    app_id = require_key('FYERS_APP_ID')
    secret = require_key('FYERS_SECRET')
    redirect_uri = 'https://trade.fyers.in/api-login/redirect-uri/index.html'

    # Step 1: Send login OTP request
    payload = {
        'fy_id': client_id,
        'app_id': '2'  # Fyers web app ID for TOTP flow
    }
    resp = requests.post(FYERS_LOGIN_URL, json=payload, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    request_key = result.get('request_key')
    if not request_key:
        raise RuntimeError(f"Login OTP request failed: {result}")

    # Step 2: Generate TOTP code and verify
    totp_code = pyotp.TOTP(totp_key).now()
    payload = {
        'request_key': request_key,
        'otp': totp_code
    }
    resp = requests.post(FYERS_VERIFY_URL, json=payload, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    request_key = result.get('request_key')
    if not request_key:
        raise RuntimeError(f"TOTP verification failed: {result}")

    # Step 3: Verify PIN
    payload = {
        'request_key': request_key,
        'identity_type': 'pin',
        'identifier': pin
    }
    resp = requests.post(FYERS_VERIFY_PIN_URL, json=payload, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    access_token_data = result.get('data', {}).get('access_token')
    if not access_token_data:
        raise RuntimeError(f"PIN verification failed: {result}")

    # Step 4: Exchange for API access token
    app_hash = hashlib.sha256(f"{app_id}:{secret}".encode()).hexdigest()
    payload = {
        'fyers_id': client_id,
        'app_id': app_id,
        'redirect_uri': redirect_uri,
        'appType': '100',
        'code_challenge': '',
        'state': 'None',
        'scope': '',
        'nonce': '',
        'response_type': 'code',
        'create_cookie': True
    }
    headers = {'Authorization': f'Bearer {access_token_data}'}
    resp = requests.post(FYERS_TOKEN_URL, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    result = resp.json()

    # Extract auth_code from the URL returned
    url = result.get('Url', '') or result.get('url', '')
    if 'auth_code=' not in url:
        raise RuntimeError(f"Auth code extraction failed: {result}")

    auth_code = url.split('auth_code=')[1].split('&')[0]

    # Step 5: Exchange auth_code for access_token
    session = fyersModel.SessionModel(
        client_id=app_id,
        secret_key=secret,
        redirect_uri=redirect_uri,
        response_type='code',
        grant_type='authorization_code'
    )
    session.set_token(auth_code)
    token_resp = session.generate_token()

    if token_resp.get('s') != 'ok' and not token_resp.get('access_token'):
        raise RuntimeError(f"Token generation failed: {token_resp}")

    return {
        'access_token': token_resp.get('access_token'),
        'refresh_token': token_resp.get('refresh_token', ''),
    }


def _refresh_via_token(refresh_token: str) -> dict | None:
    """Attempt to get a new access token using the refresh token."""
    try:
        app_id = require_key('FYERS_APP_ID')
        secret = require_key('FYERS_SECRET')
        pin = require_key('FYERS_PIN')

        app_hash = hashlib.sha256(f"{app_id}:{secret}".encode()).hexdigest()

        resp = requests.post(
            FYERS_REFRESH_URL,
            json={
                'grant_type': 'refresh_token',
                'appIdHash': app_hash,
                'refresh_token': refresh_token,
                'pin': pin
            },
            timeout=30
        )

        if resp.status_code == 200:
            data = resp.json()
            if data.get('access_token'):
                data['refresh_token'] = refresh_token  # Keep existing refresh token
                return data
        return None
    except Exception as e:
        logger.warning(f"Refresh token attempt failed: {e}")
        return None


def _save_cache(token_data: dict):
    """Save token data to cache file with timestamps."""
    CACHE_DIR.mkdir(exist_ok=True)
    cache = {
        'access_token': token_data.get('access_token'),
        'refresh_token': token_data.get('refresh_token', ''),
        'access_ts': time.time(),
        'refresh_ts': time.time() if token_data.get('refresh_token') else 0,
    }
    CACHE_FILE.write_text(json.dumps(cache, indent=2))
    logger.info("Token cache saved successfully")

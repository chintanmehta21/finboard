"""
Fyers TOTP Headless Authentication & Token Management

Handles the complete token lifecycle:
1. Try cached access token first
2. If expired, try refresh token (15-day validity)
3. If refresh expired, try TOTP headless login (primary)
4. If headless fails, fall back to browser-based auth (manual one-time)
5. Cache new tokens for subsequent runs

Credentials loaded via key_loader: Admin/.env (local) or env vars (production).
"""

import base64
import json
import time
import hashlib
import logging
import webbrowser
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pyotp
import requests
from fyers_apiv3 import fyersModel

from src.utils.key_loader import require_key

logger = logging.getLogger(__name__)

CACHE_DIR = Path('.token_cache')
CACHE_FILE = CACHE_DIR / 'fyers_tokens.json'

# Fyers auth endpoints — vagator v2 for headless TOTP login
FYERS_LOGIN_OTP_URLS = [
    'https://api-t2.fyers.in/vagator/v2/send_login_otp_v2',
    'https://api-t2.fyers.in/vagator/v2/send_login_otp',
]
FYERS_VERIFY_URL = 'https://api-t2.fyers.in/vagator/v2/verify_otp'
FYERS_VERIFY_PIN_URL = 'https://api-t2.fyers.in/vagator/v2/verify_pin_v2'
FYERS_TOKEN_URL_V2 = 'https://api.fyers.in/api/v2/token'
FYERS_TOKEN_URL_V3 = 'https://api-t1.fyers.in/api/v3/token'
FYERS_REFRESH_URL = 'https://api-t1.fyers.in/api/v3/validate-refresh-token'

# Browser-like session headers (matching working reference code — NO Content-Type)
SESSION_HEADERS = {
    'Accept': 'application/json',
    'Accept-Language': 'en-US,en;q=0.9',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}


def _b64(value: str) -> str:
    """Base64-encode a string (required by Fyers v2 endpoints)."""
    return base64.b64encode(str(value).encode('ascii')).decode('ascii')


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

    # Try TOTP headless authentication first
    logger.info("Attempting TOTP headless authentication...")
    try:
        token_data = _totp_headless_login()
        _save_cache(token_data)
        return token_data['access_token']
    except Exception as e:
        logger.warning(f"TOTP headless login failed: {e}")
        logger.info("Falling back to browser-based auth...")

    # Fallback: Manual browser-based auth
    token_data = _browser_auth_flow()
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
    1. Send login OTP request (try v2 then v1 endpoint)
    2. Generate TOTP code using pyotp and verify
    3. Verify PIN to get bearer token
    4. Exchange bearer token for auth_code via v3 token endpoint
    5. Exchange auth_code for access_token via SessionModel
    """
    client_id = require_key('FYERS_CLIENT_ID')  # Fyers user ID (e.g. "XY12345")
    totp_key = require_key('FYERS_TOTP_KEY')
    pin = require_key('FYERS_PIN')
    app_id = require_key('FYERS_APP_ID')        # API app ID (e.g. "ABCDEF1234-100")
    secret = require_key('FYERS_SECRET')
    redirect_uri = 'https://trade.fyers.in/api-login/redirect-uri/index.html'

    s = requests.Session()
    s.headers.update(SESSION_HEADERS)

    # Step 1: Send login OTP — try multiple endpoint versions
    # v2 endpoint requires Base64-encoded fy_id, v1 takes plain
    request_key = None
    for login_url in FYERS_LOGIN_OTP_URLS:
        is_v2 = 'v2' in login_url.split('/')[-1]
        fy_id_val = _b64(client_id) if is_v2 else client_id

        payload = f'{{"fy_id":"{fy_id_val}","app_id":"2"}}'
        logger.info(f"Step 1: Trying {login_url.split('/')[-1]}...")
        resp = s.post(login_url, data=payload, timeout=30)

        if resp.status_code == 200:
            result = resp.json()
            request_key = result.get('request_key')
            if request_key:
                logger.info(f"Step 1 OK: Got request_key via {login_url.split('/')[-1]}")
                break
        else:
            logger.warning(f"  {login_url.split('/')[-1]} returned {resp.status_code}: {resp.text[:200]}")

    if not request_key:
        raise RuntimeError(
            f"All login OTP endpoints failed. Last response: {resp.text[:300]}. "
            "This usually means TOTP is not enabled on the Fyers account, "
            "or the account needs first-time browser authorization. "
            "Visit https://myaccount.fyers.in/ManageAccount to enable External TOTP."
        )

    # Step 2: Generate TOTP code and verify
    # Wait if near end of 30-second TOTP window to avoid expiry during verification
    if time.time() % 30 > 27:
        time.sleep(5)

    totp_code = pyotp.TOTP(totp_key).now()
    payload = f'{{"request_key":"{request_key}","otp":{totp_code}}}'
    logger.info("Step 2: Verifying TOTP...")
    resp = s.post(FYERS_VERIFY_URL, data=payload, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    request_key = result.get('request_key')
    if not request_key:
        raise RuntimeError(f"TOTP verification failed: {result}")
    logger.info("Step 2 OK: TOTP verified")

    # Step 3: Verify PIN (Base64-encoded for v2 endpoint)
    payload = f'{{"request_key":"{request_key}","identity_type":"pin","identifier":"{_b64(pin)}"}}'
    logger.info("Step 3: Verifying PIN...")
    resp = s.post(FYERS_VERIFY_PIN_URL, data=payload, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    access_token_data = result.get('data', {}).get('access_token')
    if not access_token_data:
        raise RuntimeError(f"PIN verification failed: {result}")
    logger.info("Step 3 OK: PIN verified, got bearer token")

    # Step 4: Exchange for auth_code via token endpoint
    # Try v2 first (returns 308 with URL), fall back to v3 if needed
    app_id_short = app_id[:-4] if app_id.endswith('-100') else app_id
    token_payload = json.dumps({
        'fyers_id': client_id,
        'app_id': app_id_short,
        'redirect_uri': redirect_uri,
        'appType': '100',
        'code_challenge': '',
        'state': 'abcdefg',
        'scope': '',
        'nonce': '',
        'response_type': 'code',
        'create_cookie': True
    })
    token_headers = {
        'Authorization': f'Bearer {access_token_data}',
        'Content-Type': 'application/json; charset=UTF-8',
    }

    auth_code = None

    # Try v2 endpoint first (returns 308 redirect with Url containing auth_code)
    for token_url in [FYERS_TOKEN_URL_V2, FYERS_TOKEN_URL_V3]:
        logger.info(f"Step 4: Trying {token_url}...")
        resp = s.post(token_url, data=token_payload, headers=token_headers, timeout=30)

        if resp.status_code not in (200, 308):
            logger.warning(f"  Token endpoint returned {resp.status_code}: {resp.text[:200]}")
            continue

        result = resp.json()

        # v2 returns "Url" with auth_code in query params
        url = result.get('Url', '') or result.get('url', '')
        if url and 'auth_code=' in url:
            parsed = urlparse(url)
            auth_code = parse_qs(parsed.query).get('auth_code', [''])[0]
            if not auth_code:
                auth_code = url.split('auth_code=')[1].split('&')[0]
            if auth_code:
                logger.info("Step 4 OK: Got auth_code from URL redirect")
                break

        # v3 may return auth_code in 'code' field or need to construct from data
        code_val = result.get('code', '')
        if code_val and isinstance(code_val, str) and len(code_val) > 10:
            auth_code = code_val
            logger.info("Step 4 OK: Got auth_code from code field")
            break

        # v3 returns data.auth — this IS the access token JWT (no auth_code exchange needed)
        data_auth = result.get('data', {}).get('auth', '')
        if data_auth and result.get('s') == 'ok':
            # The v3 token endpoint returns the access token directly as a JWT
            # NOTE: Do NOT prepend app_id: — the SDK's FyersModel does that internally
            logger.info("Step 4 OK: Got access token directly from v3 token endpoint")
            return {
                'access_token': data_auth,
                'refresh_token': '',
            }

        logger.warning(f"  No auth_code found in response from {token_url}")

    if not auth_code:
        raise RuntimeError(f"Could not extract auth_code from any token endpoint. Last response: {result}")

    logger.info("Step 4 OK: Got auth_code")

    # Step 5: Exchange auth_code for access_token via SDK
    logger.info("Step 5: Generating final access token...")
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

    logger.info("Step 5 OK: Access token generated successfully")
    return {
        'access_token': token_resp.get('access_token'),
        'refresh_token': token_resp.get('refresh_token', ''),
    }


def _browser_auth_flow() -> dict:
    """
    Fallback: Generate auth URL, open browser for user login, and exchange auth_code.

    This is used when TOTP headless login fails (first-time app authorization,
    TOTP not enabled, Cloudflare blocking, etc.).
    """
    app_id = require_key('FYERS_APP_ID')
    secret = require_key('FYERS_SECRET')
    redirect_uri = 'https://trade.fyers.in/api-login/redirect-uri/index.html'

    session = fyersModel.SessionModel(
        client_id=app_id,
        redirect_uri=redirect_uri,
        response_type='code',
        state='abcdefg'
    )

    auth_url = session.generate_authcode()
    logger.info(f"Browser auth URL: {auth_url}")

    # Try to open browser
    try:
        webbrowser.open(auth_url)
        logger.info("Browser opened. Please login and authorize the app.")
    except Exception:
        logger.info("Could not open browser automatically.")

    print("\n" + "=" * 70)
    print("FYERS AUTHENTICATION REQUIRED")
    print("=" * 70)
    print(f"\n1. Open this URL in your browser:\n   {auth_url}")
    print("\n2. Login with your Fyers credentials")
    print("3. After authorization, you'll be redirected. Copy the FULL redirect URL")
    print("   (it will contain 'auth_code=' in it)")
    print("\n4. Paste the redirect URL below:")
    print("=" * 70)

    redirect_url = input("\nRedirect URL: ").strip()

    # Extract auth_code from redirect URL
    parsed = urlparse(redirect_url)
    auth_code = parse_qs(parsed.query).get('auth_code', [''])[0]
    if not auth_code and 'auth_code=' in redirect_url:
        auth_code = redirect_url.split('auth_code=')[1].split('&')[0]

    if not auth_code:
        raise RuntimeError("Could not extract auth_code from the URL. Please try again.")

    logger.info("Got auth_code from browser flow, generating token...")

    # Exchange auth_code for access_token
    token_session = fyersModel.SessionModel(
        client_id=app_id,
        secret_key=secret,
        redirect_uri=redirect_uri,
        response_type='code',
        grant_type='authorization_code'
    )
    token_session.set_token(auth_code)
    token_resp = token_session.generate_token()

    if token_resp.get('s') != 'ok' and not token_resp.get('access_token'):
        raise RuntimeError(f"Token generation failed: {token_resp}")

    logger.info("Browser auth flow completed successfully!")
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

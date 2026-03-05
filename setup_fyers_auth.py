"""
One-time Fyers API App Authorization Setup

This script performs the first-time browser-based authorization of your Fyers API app.
After running this once, the headless TOTP login in src/auth/token_manager.py
will work automatically for subsequent runs.

Usage:
    python setup_fyers_auth.py
"""

import hashlib
import json
import webbrowser
from pathlib import Path

from fyers_apiv3 import fyersModel

from src.utils.key_loader import require_key

CACHE_DIR = Path('.token_cache')
CACHE_FILE = CACHE_DIR / 'fyers_tokens.json'


def main():
    app_id = require_key('FYERS_APP_ID')
    secret = require_key('FYERS_SECRET')
    redirect_uri = 'https://trade.fyers.in/api-login/redirect-uri/index.html'

    # Generate the auth URL
    session = fyersModel.SessionModel(
        client_id=app_id,
        redirect_uri=redirect_uri,
        response_type='code',
        state='abcdefg'
    )
    auth_url = session.generate_authcode()

    print("\n" + "=" * 70)
    print("  FYERS API - ONE-TIME APP AUTHORIZATION")
    print("=" * 70)
    print(f"\n  1. Opening this URL in your browser:\n")
    print(f"     {auth_url}\n")

    try:
        webbrowser.open(auth_url)
        print("  [Browser opened automatically]")
    except Exception:
        print("  [Could not open browser - please copy the URL above]")

    print(f"\n  2. Log in with your Fyers credentials")
    print(f"  3. Click 'Allow' to authorize the FinBoard app")
    print(f"  4. After redirect, copy the FULL URL from your browser")
    print(f"     (it contains 'auth_code=' in it)")
    print(f"\n  5. Paste the full redirect URL below:")
    print("=" * 70)

    redirect_url = input("\n  Redirect URL: ").strip()

    # Extract auth_code
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(redirect_url)
    auth_code = parse_qs(parsed.query).get('auth_code', [''])[0]

    if not auth_code and 'auth_code=' in redirect_url:
        auth_code = redirect_url.split('auth_code=')[1].split('&')[0]

    if not auth_code:
        print("\n  ERROR: Could not extract auth_code from the URL.")
        print("  Make sure you copied the FULL URL from the browser address bar.")
        return

    print(f"\n  Auth code extracted: {auth_code[:20]}...")

    # Exchange for access token
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
        print(f"\n  ERROR: Token generation failed: {token_resp}")
        return

    access_token = token_resp.get('access_token')
    refresh_token = token_resp.get('refresh_token', '')

    # Test the token
    fyers = fyersModel.FyersModel(client_id=app_id, token=access_token, log_path='logs')
    profile = fyers.get_profile()

    if profile.get('s') == 'ok':
        print(f"\n  SUCCESS! Logged in as: {profile.get('data', {}).get('name', 'N/A')}")
    else:
        print(f"\n  WARNING: Profile check returned: {profile}")

    # Cache the token
    import time
    CACHE_DIR.mkdir(exist_ok=True)
    cache = {
        'access_token': access_token,
        'refresh_token': refresh_token,
        'access_ts': time.time(),
        'refresh_ts': time.time() if refresh_token else 0,
    }
    CACHE_FILE.write_text(json.dumps(cache, indent=2))
    print(f"  Token cached at: {CACHE_FILE}")
    print(f"\n  You can now run the pipeline: python -m src.main")
    print("=" * 70)


if __name__ == '__main__':
    main()

# Finboard v2.0 — Authentication & Token Management

**Snapshot Date**: 2026-03-17
**Source File**: `src/auth/token_manager.py`

---

## Overview

The system uses Fyers API v3 for market data. Fyers requires OAuth2 authentication with TOTP (Time-based One-Time Password) for 2FA. The `token_manager.py` module handles the entire auth lifecycle headlessly — no browser interaction needed.

---

## Authentication Flow (3-Tier Fallback)

```
get_valid_access_token()
    │
    ├─ Tier 1: Try cached access token
    │   ├─ Read .token_cache/fyers_tokens.json
    │   ├─ Check timestamp (< 23 hours old)
    │   └─ If valid → return access_token (instant)
    │
    ├─ Tier 2: Try refresh token
    │   ├─ Check refresh_token in cache (< 14 days old)
    │   ├─ POST to Fyers refresh endpoint
    │   └─ If valid → save new tokens, return access_token (~1 sec)
    │
    ├─ Tier 3: Full TOTP headless login
    │   ├─ _totp_headless_login()
    │   ├─ 5-step process (see below)
    │   └─ Save tokens to cache, return access_token (~3-5 sec)
    │
    └─ Tier 4: Browser fallback (manual)
        ├─ _browser_auth_flow()
        ├─ Opens browser, waits for redirect URL
        └─ User pastes redirect URL in terminal
```

---

## TOTP Headless Login Flow

The `_totp_headless_login()` function performs a complete Fyers login without any browser interaction:

### Step 1: Send Login OTP
```
POST to Fyers login OTP endpoint
    Body: { fy_id: CLIENT_ID, app_id: APP_ID (2-char prefix) }
    Headers: Browser-like session headers

    Try v2 endpoint first, then v1 if v2 fails
    Response: { request_key: "..." }
```

### Step 2: Verify TOTP
```
Generate OTP: pyotp.TOTP(FYERS_TOTP_KEY).now()

POST to Fyers TOTP verification endpoint
    Body: { request_key: step1.request_key, otp: generated_otp }
    Response: { request_key: "..." (new key for PIN step) }
```

### Step 3: Verify PIN
```
Encode PIN: base64(FYERS_PIN)

POST to Fyers PIN verification endpoint
    Body: { request_key: step2.request_key, identity: base64_pin, identity_type: 'pin' }
    Response: { data: { access_token: "...", ... } }

    OR (depending on Fyers version):
    Response: { data: { verification_data: "auth_code_here" } }
```

### Step 4: Exchange for Auth Code (if needed)
```
If Step 3 returned verification_data:
    POST to Fyers token endpoint (v2 or v3)
    Body: { fyers_id: CLIENT_ID, app_id: APP_ID, redirect_uri: ..., appType: 100, code_challenge: "", state: "state", scope: "", nonce: "", response_type: "code", create_cookie: true }
    Headers include verification_data
    Response: 302 redirect to redirect_uri?auth_code=...
    Extract auth_code from redirect URL
```

### Step 5: Exchange Auth Code for Access Token
```
fyers.SessionModel(
    client_id=APP_ID,
    secret_key=SECRET,
    redirect_uri=REDIRECT_URI,
    response_type='code',
    grant_type='authorization_code'
)
session.set_token(auth_code)
response = session.generate_token()
→ { access_token: "...", refresh_token: "..." }
```

---

## Token Cache

Tokens are cached to `.token_cache/fyers_tokens.json` to avoid re-authentication on every run.

### Cache Structure
```json
{
    "access_token": "gQR...long_token...",
    "refresh_token": "eyJ...refresh_token...",
    "token_type": "Bearer",
    "created_at": "2026-03-17T09:00:00+05:30",
    "expires_in": 86400
}
```

### Cache Location
```
.token_cache/
└── fyers_tokens.json     (gitignored, never committed)
```

### Token Lifetimes
| Token Type | Lifetime | Refresh Strategy |
|-----------|----------|-----------------|
| Access Token | ~24 hours | Cached, checked at < 23 hours |
| Refresh Token | ~14 days | Used when access token expires |
| TOTP Secret | Permanent | Never changes (tied to Fyers account) |

### GitHub Actions Cache
In CI/CD, the token cache is persisted between runs using GitHub Actions `actions/cache@v4`:
```yaml
- name: Restore Fyers token cache
  uses: actions/cache@v4
  with:
    path: .token_cache/
    key: fyers-tokens-${{ env.CACHE_DATE }}
    restore-keys: fyers-tokens-
```
This means most CI runs reuse the cached access token and don't need full TOTP re-auth.

---

## Required Credentials

| Secret | Description | Where Set |
|--------|-------------|-----------|
| `FYERS_APP_ID` | Application ID (format: `XXXXXXXX-100`) | Admin/.env or GitHub Secret |
| `FYERS_SECRET` | Application secret key | Admin/.env or GitHub Secret |
| `FYERS_CLIENT_ID` | Fyers account client ID (e.g., `FY12345`) | Admin/.env or GitHub Secret |
| `FYERS_PIN` | 4-digit security PIN | Admin/.env or GitHub Secret |
| `FYERS_TOTP_KEY` | TOTP secret (Base32 string, from authenticator setup) | Admin/.env or GitHub Secret |

### How Credentials Are Loaded
```
src/utils/key_loader.py :: get_key(key_name)
    │
    ├─ Priority 1: Admin/.env (local development)
    │   └─ Loaded by python-dotenv at module import
    │
    └─ Priority 2: os.environ (GitHub Actions / production)
        └─ Set via GitHub Secrets in repository settings
```

---

## FyersModel Instance

The authenticated API client is obtained via:

```python
from src.auth.token_manager import get_fyers_instance

fyers = get_fyers_instance()
# Returns: fyersModel.FyersModel with valid access_token
# Ready for API calls: fyers.history(), fyers.quotes(), etc.
```

This function calls `get_valid_access_token()` internally, handling all caching and re-auth transparently.

---

## Session Headers (Anti-Bot)

The TOTP headless flow uses browser-like headers to avoid Fyers rejecting API calls:

```python
SESSION_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ...',
    'Accept': 'application/json',
    'Accept-Language': 'en-US,en;q=0.9',
    'Content-Type': 'application/json',
    'Origin': 'https://api-t1.fyers.in',
    'Referer': 'https://api-t1.fyers.in/',
}
```

---

## Failure Modes & Recovery

| Failure | Detection | Recovery |
|---------|----------|---------|
| Cached token expired | HTTP 401 on API call | Automatic: try refresh → full TOTP |
| Refresh token expired | Refresh endpoint returns error | Automatic: fall through to TOTP |
| TOTP verification fails | HTTP 400/403 from Fyers | Check FYERS_TOTP_KEY is correct |
| PIN verification fails | HTTP 400 from Fyers | Check FYERS_PIN is correct |
| Auth code exchange fails | SessionModel returns error | Check APP_ID and SECRET match |
| All tiers fail | RuntimeError raised | Pipeline exits, error notification sent |

---

## Sample Mode (No Auth)

When `FYERS_TOTP_KEY` is not configured (empty or missing), the system automatically switches to sample mode:

```python
# src/main.py
def _is_fyers_ready() -> bool:
    totp = get_key('FYERS_TOTP_KEY')
    return bool(totp)
```

In sample mode:
- No Fyers authentication is attempted
- Data comes from yfinance + synthetic generators
- Pipeline runs identically, just with different data
- Dashboard displays `[SAMPLE DATA]` tag

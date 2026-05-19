"""
Codex CLI OAuth authentication via Device Code Flow (RFC 8628).
Obtains access_token, refresh_token, id_token and stores them in
~/.codex/auth.json — the same location the codex CLI reads.
"""
import json
import time
import hashlib
import base64
import os
import secrets
from pathlib import Path

import requests

# ── OAuth constants (extracted from codex binary) ────────────────────

_AUTH_BASE    = "https://auth.openai.com"
_DEVICE_URL   = f"{_AUTH_BASE}/oauth/device/code"
_TOKEN_URL    = f"{_AUTH_BASE}/oauth/token"
_REVOKE_URL   = f"{_AUTH_BASE}/oauth/revoke"
_VERIFY_URL   = f"{_AUTH_BASE}/codex/device"   # human-friendly page

_CLIENT_ID    = "app_EMoamEEZ73f0CkXaXp7hran"
_SCOPES       = "openid profile email offline_access api.connectors.read api.connectors.invoke"

_CODEX_AUTH   = Path.home() / ".codex" / "auth.json"


# ── PKCE helpers ─────────────────────────────────────────────────────

def _pkce_pair() -> tuple[str, str]:
    """Returns (verifier, challenge) for PKCE S256."""
    verifier  = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


# ── Device Code Flow ──────────────────────────────────────────────────

def device_auth_start() -> dict:
    """
    Step 1 — request device+user code.
    Returns dict with keys: device_code, user_code, verification_uri,
    expires_in, interval.
    Raises RuntimeError on failure.
    """
    verifier, challenge = _pkce_pair()

    resp = requests.post(
        _DEVICE_URL,
        data={
            "client_id":             _CLIENT_ID,
            "scope":                 _SCOPES,
            "code_challenge":        challenge,
            "code_challenge_method": "S256",
        },
        timeout=15,
    )
    if not resp.ok:
        raise RuntimeError(f"Device auth failed: {resp.status_code} {resp.text[:200]}")

    data = resp.json()
    data["_verifier"] = verifier   # carry verifier through to poll step
    return data


def device_auth_poll(device_code: str, verifier: str,
                     interval: int = 5, timeout: int = 300,
                     on_waiting=None) -> dict:
    """
    Step 2 — poll token endpoint until user approves or timeout.
    on_waiting: optional callable(elapsed_seconds) called each polling tick.
    Returns tokens dict: {access_token, refresh_token, id_token, ...}.
    Raises RuntimeError on error or timeout.
    """
    deadline = time.time() + timeout
    wait     = max(interval, 3)

    while time.time() < deadline:
        time.sleep(wait)

        resp = requests.post(
            _TOKEN_URL,
            data={
                "client_id":     _CLIENT_ID,
                "device_code":   device_code,
                "grant_type":    "urn:ietf:params:oauth:grant-type:device_code",
                "code_verifier": verifier,
            },
            timeout=15,
        )
        data = resp.json()

        error = data.get("error")
        if error == "authorization_pending":
            elapsed = int(timeout - (deadline - time.time()))
            if on_waiting:
                on_waiting(elapsed)
            continue
        elif error == "slow_down":
            wait += 5
            continue
        elif error == "expired_token":
            raise RuntimeError("Код подтверждения истёк. Запусти авторизацию заново.")
        elif error == "access_denied":
            raise RuntimeError("Авторизация отклонена пользователем.")
        elif error:
            raise RuntimeError(f"OAuth error: {error} — {data.get('error_description', '')}")

        # Success
        if "access_token" in data:
            return data

    raise RuntimeError("Timeout: пользователь не подтвердил авторизацию вовремя.")


# ── Token storage ─────────────────────────────────────────────────────

def save_codex_auth(tokens: dict):
    """Write tokens to ~/.codex/auth.json in the format codex CLI expects."""
    _CODEX_AUTH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "tokens": {
            "access_token":  tokens.get("access_token", ""),
            "refresh_token": tokens.get("refresh_token", ""),
            "id_token":      tokens.get("id_token", ""),
        },
        "last_refresh": int(time.time()),
        "auth_mode":    "oauth",
    }
    _CODEX_AUTH.write_text(json.dumps(payload, indent=2))
    _CODEX_AUTH.chmod(0o600)


def load_codex_auth() -> dict | None:
    """Read ~/.codex/auth.json. Returns None if missing or malformed."""
    try:
        return json.loads(_CODEX_AUTH.read_text())
    except Exception:
        return None


def is_codex_logged_in() -> bool:
    """Quick check: auth.json exists and has a non-empty access_token."""
    auth = load_codex_auth()
    if not auth:
        return False
    return bool(auth.get("tokens", {}).get("access_token", ""))


# ── Token refresh ─────────────────────────────────────────────────────

def refresh_codex_token() -> bool:
    """
    Attempt to refresh access_token using refresh_token.
    Returns True on success, False if refresh_token is missing/expired.
    """
    auth = load_codex_auth()
    if not auth:
        return False
    refresh_token = auth.get("tokens", {}).get("refresh_token", "")
    if not refresh_token:
        return False

    try:
        resp = requests.post(
            _TOKEN_URL,
            data={
                "client_id":     _CLIENT_ID,
                "grant_type":    "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=15,
        )
        if not resp.ok:
            return False
        data = resp.json()
        if "access_token" not in data:
            return False

        # Merge: keep existing tokens, overwrite with new ones
        existing = auth.get("tokens", {})
        existing.update({
            "access_token":  data["access_token"],
            "id_token":      data.get("id_token", existing.get("id_token", "")),
        })
        if "refresh_token" in data:
            existing["refresh_token"] = data["refresh_token"]

        auth["tokens"]       = existing
        auth["last_refresh"] = int(time.time())
        _CODEX_AUTH.write_text(json.dumps(auth, indent=2))
        return True
    except Exception:
        return False


# ── Interactive login (used by configure wizard) ──────────────────────

def interactive_login(print_fn=print) -> bool:
    """
    Full interactive Device Code login flow.
    print_fn: callable for output (default print).
    Returns True on success.
    """
    print_fn("\n⏳ Запрашиваю код авторизации...")
    try:
        info = device_auth_start()
    except RuntimeError as e:
        print_fn(f"❌ {e}")
        return False

    user_code   = info.get("user_code", "?")
    expires_in  = info.get("expires_in", 300)
    interval    = info.get("interval", 5)
    device_code = info["device_code"]
    verifier    = info["_verifier"]

    print_fn(f"\n{'='*50}")
    print_fn(f"  Открой в браузере: {_VERIFY_URL}")
    print_fn(f"  Введи код:         {user_code}")
    print_fn(f"{'='*50}")
    print_fn(f"  Код действителен {expires_in // 60} минут.\n")

    def _tick(elapsed: int):
        print_fn(f"  ⏳ Ожидаю подтверждения... ({elapsed}с)")

    try:
        tokens = device_auth_poll(
            device_code, verifier,
            interval=interval, timeout=expires_in,
            on_waiting=_tick,
        )
    except RuntimeError as e:
        print_fn(f"❌ {e}")
        return False

    save_codex_auth(tokens)
    print_fn("\n✅ Авторизация успешна! Токен сохранён в ~/.codex/auth.json")
    return True


def codex_login_status() -> str:
    """Returns human-readable login status string."""
    auth = load_codex_auth()
    if not auth:
        return "Не авторизован (auth.json отсутствует)"
    tokens = auth.get("tokens", {})
    if not tokens.get("access_token"):
        return "Не авторизован (access_token пуст)"
    last = auth.get("last_refresh", 0)
    if last:
        age = int(time.time()) - last
        age_str = f", обновлён {age // 3600}ч {(age % 3600) // 60}м назад"
    else:
        age_str = ""
    has_id = "✓" if tokens.get("id_token") else "✗"
    return f"Авторизован (id_token: {has_id}{age_str})"

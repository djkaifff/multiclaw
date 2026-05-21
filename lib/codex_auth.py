"""
Codex CLI OAuth authentication — Authorization Code Flow with PKCE.
Matches the flow OpenClaw uses: builds auth URL, user opens in browser,
pastes redirect URL back (VPS mode) or local callback server catches it.
Tokens saved to ~/.codex/auth.json.
"""
import hashlib
import base64
import json
import os
import secrets
import time
from datetime import datetime, timezone
import threading
import urllib.parse
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests

# ── OAuth constants ───────────────────────────────────────────────────

_AUTH_BASE    = "https://auth.openai.com"
_AUTH_URL     = f"{_AUTH_BASE}/oauth/authorize"
_TOKEN_URL    = f"{_AUTH_BASE}/oauth/token"
_REVOKE_URL   = f"{_AUTH_BASE}/oauth/revoke"

_CLIENT_ID    = "app_EMoamEEZ73f0CkXaXp7hrann"
_REDIRECT_URI = "http://localhost:1455/auth/callback"
_SCOPES       = "openid profile email offline_access"

_CODEX_AUTH   = Path.home() / ".codex" / "auth.json"


# ── PKCE helpers ─────────────────────────────────────────────────────

def _pkce_pair() -> tuple[str, str]:
    verifier  = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


# ── Build authorization URL ───────────────────────────────────────────

def build_auth_url() -> tuple[str, str, str]:
    """
    Returns (auth_url, state, code_verifier).
    auth_url: open this in the browser.
    state, code_verifier: keep to verify the callback.
    """
    verifier, challenge = _pkce_pair()
    state = secrets.token_hex(16)

    params = {
        "response_type":             "code",
        "client_id":                 _CLIENT_ID,
        "redirect_uri":              _REDIRECT_URI,
        "scope":                     _SCOPES,
        "code_challenge":            challenge,
        "code_challenge_method":     "S256",
        "state":                     state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    }
    url = _AUTH_URL + "?" + urllib.parse.urlencode(params)
    return url, state, verifier


# ── Exchange code for tokens ──────────────────────────────────────────

def exchange_code(code: str, verifier: str) -> dict:
    """
    POST authorization code + PKCE verifier to token endpoint.
    Returns tokens dict: {access_token, refresh_token, id_token, ...}.
    """
    resp = requests.post(
        _TOKEN_URL,
        data={
            "grant_type":    "authorization_code",
            "client_id":     _CLIENT_ID,
            "code":          code,
            "redirect_uri":  _REDIRECT_URI,
            "code_verifier": verifier,
        },
        timeout=15,
    )
    if not resp.ok:
        raise RuntimeError(f"Token exchange failed: {resp.status_code} {resp.text[:200]}")
    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"No access_token in response: {data}")
    return data


# ── Local callback server (works if browser is on same machine) ───────

class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self.server._code  = qs.get("code", [None])[0]
        self.server._state = qs.get("state", [None])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<h1>Done! Return to terminal.</h1>")
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, *_):
        pass


def _try_local_server(timeout: int = 120) -> str | None:
    """
    Starts HTTP server on localhost:1455. Returns code if browser redirects
    to it within timeout, or None if port is busy / no redirect received.
    """
    try:
        srv = HTTPServer(("127.0.0.1", 1455), _CallbackHandler)
        srv._code  = None
        srv._state = None
        srv.timeout = 2  # poll interval
    except OSError:
        return None

    deadline = time.time() + timeout
    while time.time() < deadline and srv._code is None:
        srv.handle_request()

    srv.server_close()
    return srv._code


# ── Parse redirect URL pasted by user ────────────────────────────────

def _parse_redirect_url(url: str) -> tuple[str, str]:
    """Extract (code, state) from redirect URL."""
    parsed = urllib.parse.urlparse(url.strip())
    qs = urllib.parse.parse_qs(parsed.query)
    code  = qs.get("code", [None])[0]
    state = qs.get("state", [None])[0]
    if not code:
        raise ValueError("В URL нет параметра code.")
    return code, state or ""


# ── Token storage ─────────────────────────────────────────────────────

def save_codex_auth(tokens: dict):
    _CODEX_AUTH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "tokens": {
            "access_token":  tokens.get("access_token", ""),
            "refresh_token": tokens.get("refresh_token", ""),
            "id_token":      tokens.get("id_token", ""),
        },
        "last_refresh": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "auth_mode":    "chatgptAuthTokens",
    }
    _CODEX_AUTH.write_text(json.dumps(payload, indent=2))
    _CODEX_AUTH.chmod(0o600)


def load_codex_auth() -> dict | None:
    try:
        return json.loads(_CODEX_AUTH.read_text())
    except Exception:
        return None


def is_codex_logged_in() -> bool:
    auth = load_codex_auth()
    if not auth:
        return False
    return bool(auth.get("tokens", {}).get("access_token", ""))


# ── Token refresh ─────────────────────────────────────────────────────

def refresh_codex_token() -> bool:
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
        existing = auth.get("tokens", {})
        existing["access_token"] = data["access_token"]
        if "id_token" in data:
            existing["id_token"] = data["id_token"]
        if "refresh_token" in data:
            existing["refresh_token"] = data["refresh_token"]
        auth["tokens"]       = existing
        auth["last_refresh"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _CODEX_AUTH.write_text(json.dumps(auth, indent=2))
        return True
    except Exception:
        return False


# ── Interactive login ─────────────────────────────────────────────────

def interactive_login(print_fn=print) -> bool:
    """
    Full interactive Authorization Code + PKCE login.
    VPS mode: user opens URL in local browser, pastes redirect URL back.
    Returns True on success.
    """
    auth_url, state, verifier = build_auth_url()

    print_fn("\n" + "=" * 60)
    print_fn("  Открой эту ссылку в ЛОКАЛЬНОМ браузере:")
    print_fn()
    print_fn(f"  {auth_url}")
    print_fn()
    print_fn("  После входа браузер перейдёт на localhost:1455.")
    print_fn("  Если страница не открылась — скопируй ПОЛНЫЙ URL")
    print_fn("  из адресной строки и вставь ниже.")
    print_fn("=" * 60 + "\n")

    # Try catching locally first (if browser is on same machine)
    code = _try_local_server(timeout=3)

    if code:
        print_fn("  ✓ Код получен автоматически.")
    else:
        # VPS mode: ask user to paste the redirect URL
        try:
            raw = input("  Вставь URL из браузера (или Enter для отмены): ").strip()
        except (EOFError, KeyboardInterrupt):
            print_fn("\n  Отменено.")
            return False

        if not raw:
            print_fn("  Отменено.")
            return False

        try:
            code, returned_state = _parse_redirect_url(raw)
        except ValueError as e:
            print_fn(f"  ❌ {e}")
            return False

        if returned_state and returned_state != state:
            print_fn("  ❌ State mismatch — возможна CSRF атака. Повтори вход.")
            return False

    print_fn("  Обмениваю код на токены...", end="", flush=True)
    try:
        tokens = exchange_code(code, verifier)
    except RuntimeError as e:
        print_fn(f"\n  ❌ {e}")
        return False

    save_codex_auth(tokens)
    has_id = "✓" if tokens.get("id_token") else "✗"
    print_fn(f"\n  ✅ Авторизация успешна!")
    print_fn(f"     access_token: ✓  refresh_token: ✓  id_token: {has_id}")
    print_fn(f"     Сохранено в: {_CODEX_AUTH}\n")
    return True


def codex_login_status() -> str:
    auth = load_codex_auth()
    if not auth:
        return "Не авторизован"
    tokens = auth.get("tokens", {})
    if not tokens.get("access_token"):
        return "Не авторизован (access_token пуст)"
    last = auth.get("last_refresh", "")
    age_str = ""
    if last:
        try:
            ts = datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            age = int(time.time()) - int(ts.timestamp())
            age_str = f", {age // 3600}ч {(age % 3600) // 60}м назад"
        except Exception:
            pass
    has_id = "✓" if tokens.get("id_token") else "✗"
    return f"Авторизован (id_token: {has_id}{age_str})"

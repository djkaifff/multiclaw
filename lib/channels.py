"""
Communication channels — setup wizards and runtime adapters.
Currently: Telegram. Extensible for Discord, Slack, etc.
"""
import json
import time
import requests
from pathlib import Path


TELEGRAM_API = "https://api.telegram.org/bot{token}"

CHANNEL_TYPES = {
    "telegram": "Telegram",
    "discord":  "Discord (скоро)",
}


# ── Telegram helpers ─────────────────────────────────────────────────

def test_telegram_token(token: str) -> tuple[bool, str]:
    """Returns (ok, bot_username_or_error)."""
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=10
        )
        data = r.json()
        if data.get("ok"):
            username = data["result"].get("username", "?")
            return True, f"@{username}"
        return False, data.get("description", "Unknown error")
    except Exception as e:
        return False, str(e)[:80]


# ── Runtime: Telegram polling ────────────────────────────────────────

class TelegramChannel:
    def __init__(self, token: str, allowed_chats: list[int] | None = None):
        self.token = token
        self.allowed_chats = allowed_chats  # None = allow all
        self._base = f"https://api.telegram.org/bot{token}"
        self._offset = 0
        self._running = False

    def send(self, chat_id: int, text: str, reply_to: int | None = None):
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        try:
            # Telegram max 4096 chars per message
            for chunk in _split_text(text):
                p = dict(payload)
                p["text"] = chunk
                requests.post(f"{self._base}/sendMessage", json=p, timeout=10)
        except Exception as e:
            print(f"[TG Send] {e}")

    def send_typing(self, chat_id: int):
        try:
            requests.post(f"{self._base}/sendChatAction",
                          json={"chat_id": chat_id, "action": "typing"},
                          timeout=5)
        except Exception:
            pass

    def poll(self, timeout: int = 30) -> list[dict]:
        """Returns list of {chat_id, user, text, message_id}."""
        try:
            r = requests.get(
                f"{self._base}/getUpdates",
                params={"offset": self._offset, "timeout": timeout},
                timeout=timeout + 5,
            )
            updates = r.json().get("result", [])
            messages = []
            for upd in updates:
                self._offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("edited_message", {})
                if not msg:
                    continue
                text = msg.get("text", "")
                if not text:
                    continue
                chat_id = msg["chat"]["id"]
                if self.allowed_chats and chat_id not in self.allowed_chats:
                    continue
                user = msg.get("from", {})
                messages.append({
                    "chat_id":    chat_id,
                    "message_id": msg["message_id"],
                    "text":       text,
                    "user":       user.get("username") or user.get("first_name", "?"),
                    "user_id":    user.get("id"),
                })
            return messages
        except Exception as e:
            print(f"[TG Poll] {e}")
            time.sleep(5)
            return []


def _split_text(text: str, max_len: int = 4000) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:max_len])
        text = text[max_len:]
    return chunks

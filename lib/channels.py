"""
Communication channels — setup wizards and runtime adapters.
Channels: Telegram, Discord, Slack.
Discord/Slack use background threads so the main runner stays synchronous.
"""
import json
import time
import threading
import requests
from pathlib import Path

# ── Optional channel deps ─────────────────────────────────────────────

try:
    import discord as _discord
    HAS_DISCORD = True
except ImportError:
    HAS_DISCORD = False
    _discord = None

try:
    from slack_sdk import WebClient as _SlackWebClient
    from slack_bolt import App as _SlackApp
    from slack_bolt.adapter.socket_mode import SocketModeHandler as _SlackSocketModeHandler
    HAS_SLACK = True
except ImportError:
    HAS_SLACK = False
    _SlackWebClient = None
    _SlackApp = None
    _SlackSocketModeHandler = None


CHANNEL_TYPES = {
    "telegram": "Telegram",
    "discord":  "Discord",
    "slack":    "Slack",
}


# ── Channel factory ───────────────────────────────────────────────────

def create_channel(channel_cfg: dict):
    ch_type = channel_cfg.get("type", "telegram")
    if ch_type == "telegram":
        return TelegramChannel(
            token=channel_cfg["token"],
            allowed_chats=channel_cfg.get("allowed_chats"),
        )
    elif ch_type == "discord":
        if not HAS_DISCORD:
            raise RuntimeError(
                "Установи discord.py: pip3 install --break-system-packages discord.py"
            )
        return DiscordChannel(
            token=channel_cfg["token"],
            allowed_channels=channel_cfg.get("allowed_channels"),
        )
    elif ch_type == "slack":
        if not HAS_SLACK:
            raise RuntimeError(
                "Установи slack-bolt: pip3 install --break-system-packages slack-bolt slack-sdk"
            )
        return SlackChannel(
            bot_token=channel_cfg["bot_token"],
            app_token=channel_cfg["app_token"],
            allowed_channels=channel_cfg.get("allowed_channels"),
        )
    else:
        raise RuntimeError(f"Неизвестный тип канала: {ch_type}")


# ── Token validation helpers ──────────────────────────────────────────

def test_telegram_token(token: str) -> tuple[bool, str]:
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        data = r.json()
        if data.get("ok"):
            return True, f"@{data['result'].get('username', '?')}"
        return False, data.get("description", "Unknown error")
    except Exception as e:
        return False, str(e)[:80]


def test_discord_token(token: str) -> tuple[bool, str]:
    try:
        r = requests.get(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bot {token}"},
            timeout=10,
        )
        if r.ok:
            d = r.json()
            return True, f"@{d.get('username', '?')}"
        return False, r.json().get("message", "Invalid token")
    except Exception as e:
        return False, str(e)[:80]


def test_slack_token(bot_token: str) -> tuple[bool, str]:
    try:
        r = requests.post(
            "https://slack.com/api/auth.test",
            headers={"Authorization": f"Bearer {bot_token}"},
            timeout=10,
        )
        d = r.json()
        if d.get("ok"):
            return True, f"{d.get('user', '?')} / {d.get('team', '?')}"
        return False, d.get("error", "Unknown error")
    except Exception as e:
        return False, str(e)[:80]


# ── Telegram ──────────────────────────────────────────────────────────

class TelegramChannel:
    def __init__(self, token: str, allowed_chats: list[int] | None = None):
        self.token = token
        self.allowed_chats = allowed_chats
        self._base = f"https://api.telegram.org/bot{token}"
        self._offset = 0

    def send(self, chat_id: int, text: str, reply_to: int | None = None):
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        try:
            for chunk in _split_text(text):
                p = dict(payload)
                p["text"] = chunk
                requests.post(f"{self._base}/sendMessage", json=p, timeout=10)
        except Exception as e:
            print(f"[TG Send] {e}")

    def send_typing(self, chat_id: int):
        try:
            requests.post(f"{self._base}/sendChatAction",
                          json={"chat_id": chat_id, "action": "typing"}, timeout=5)
        except Exception:
            pass

    def poll(self, timeout: int = 30) -> list[dict]:
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


# ── Discord ───────────────────────────────────────────────────────────

class DiscordChannel:
    """
    Runs discord.py in a background thread (asyncio loop).
    Incoming messages are queued; poll() drains the queue.
    Requires Message Content Intent enabled in Discord Dev Portal.
    """
    def __init__(self, token: str, allowed_channels: list[int] | None = None):
        self.token = token
        self.allowed_channels = allowed_channels
        self._queue: list[dict] = []
        self._client = None
        self._loop = None
        self._ready = threading.Event()

        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        if not self._ready.wait(timeout=30):
            print("[Discord] Timeout waiting for client ready")

    def _run(self):
        import asyncio
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        intents = _discord.Intents.default()
        intents.message_content = True
        self._client = _discord.Client(intents=intents)

        @self._client.event
        async def on_ready():
            self._ready.set()

        @self._client.event
        async def on_message(message):
            if message.author == self._client.user:
                return
            if self.allowed_channels and message.channel.id not in self.allowed_channels:
                return
            self._queue.append({
                "chat_id":    message.channel.id,
                "message_id": str(message.id),
                "text":       message.content,
                "user":       message.author.display_name,
                "user_id":    message.author.id,
            })

        self._loop.run_until_complete(self._client.start(self.token))

    def send(self, chat_id: int, text: str, reply_to=None):
        if not self._client or not self._loop:
            return
        import asyncio

        async def _send():
            ch = self._client.get_channel(int(chat_id))
            if ch:
                for chunk in _split_text(text, 2000):
                    await ch.send(chunk)

        asyncio.run_coroutine_threadsafe(_send(), self._loop)

    def send_typing(self, chat_id: int):
        if not self._client or not self._loop:
            return
        import asyncio

        async def _typing():
            ch = self._client.get_channel(int(chat_id))
            if ch:
                async with ch.typing():
                    pass

        asyncio.run_coroutine_threadsafe(_typing(), self._loop)

    def poll(self, timeout: int = 30) -> list[dict]:
        time.sleep(1)
        msgs = list(self._queue)
        self._queue.clear()
        return msgs


# ── Slack ─────────────────────────────────────────────────────────────

class SlackChannel:
    """
    Uses Slack Socket Mode (no public URL needed).
    Requires:
      - bot_token  (xoxb-...)  from App > OAuth & Permissions
      - app_token  (xapp-...)  from App > Basic Information > App-Level Tokens
        (scope: connections:write)
    Enable Socket Mode + subscribe to message.channels / message.im events.
    """
    def __init__(self, bot_token: str, app_token: str,
                 allowed_channels: list[str] | None = None):
        self.bot_token = bot_token
        self.app_token = app_token
        self.allowed_channels = allowed_channels
        self._queue: list[dict] = []
        self._web = _SlackWebClient(token=bot_token)

        app = _SlackApp(token=bot_token)

        @app.event("message")
        def handle_message(event, say):
            channel = event.get("channel", "")
            if self.allowed_channels and channel not in self.allowed_channels:
                return
            text = event.get("text", "")
            if not text or event.get("subtype"):
                return
            self._queue.append({
                "chat_id":    channel,
                "message_id": event.get("ts", ""),
                "text":       text,
                "user":       event.get("user", "?"),
                "user_id":    event.get("user", ""),
            })

        handler = _SlackSocketModeHandler(app, app_token)
        t = threading.Thread(target=handler.start, daemon=True)
        t.start()

    def send(self, chat_id: str, text: str, reply_to=None):
        for chunk in _split_text(text, 3000):
            try:
                kwargs: dict = {"channel": chat_id, "text": chunk}
                if reply_to:
                    kwargs["thread_ts"] = str(reply_to)
                self._web.chat_postMessage(**kwargs)
            except Exception as e:
                print(f"[Slack Send] {e}")

    def send_typing(self, chat_id: str):
        pass  # Slack не поддерживает typing via REST в Socket Mode

    def poll(self, timeout: int = 30) -> list[dict]:
        time.sleep(1)
        msgs = list(self._queue)
        self._queue.clear()
        return msgs


# ── Helpers ───────────────────────────────────────────────────────────

def _split_text(text: str, max_len: int = 4000) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:max_len])
        text = text[max_len:]
    return chunks

"""
Bot runner — main loop. Called by `multiclaw run <name>`.
Polls channel → calls model → sends response → updates context & backups.
"""
import os
import sys
import time
import signal
from datetime import datetime, timezone
from lib.state import (
    get_config, get_soul, get_context, append_context,
    save_backup, agent_dir
)
from lib.models import call_model
from lib.channels import TelegramChannel
from lib.webtools import search, format_results

CONTEXT_SYSTEM = """
Ниже — краткий лог твоих недавних действий и диалогов.
Используй его чтобы поддерживать контекст разговора.
---
{context}
"""

SEARCH_TRIGGER = ["/search ", "/поиск ", "/web "]


class BotRunner:
    def __init__(self, name: str):
        self.name = name
        self.cfg  = get_config(name)
        self._validate()

        channel_cfg = self.cfg["channel"]
        allowed     = channel_cfg.get("allowed_chats")
        self.channel = TelegramChannel(
            token=channel_cfg["token"],
            allowed_chats=allowed if isinstance(allowed, list) else None,
        )
        self.model_cfg = self.cfg["model"]
        self.webtools  = self.cfg.get("webtools", {})

        self._running = True
        signal.signal(signal.SIGTERM, self._handle_stop)
        signal.signal(signal.SIGINT,  self._handle_stop)

        pid_file = agent_dir(name) / "bot.pid"
        pid_file.write_text(str(os.getpid()))

    def _validate(self):
        if not self.cfg.get("model", {}).get("api_key"):
            raise RuntimeError(f"Бот '{self.name}': модель не настроена")
        if not self.cfg.get("channel", {}).get("token"):
            raise RuntimeError(f"Бот '{self.name}': канал не настроен")

    def _handle_stop(self, *_):
        self._running = False

    def run(self):
        print(f"[{self.name}] Запущен. Модель: {self.model_cfg.get('model_id')}")
        while self._running:
            try:
                messages = self.channel.poll(timeout=25)
                for msg in messages:
                    self._handle_message(msg)
            except Exception as e:
                print(f"[{self.name}] Ошибка: {e}")
                time.sleep(5)

        pid_file = agent_dir(self.name) / "bot.pid"
        pid_file.unlink(missing_ok=True)
        print(f"[{self.name}] Остановлен.")

    def _handle_message(self, msg: dict):
        text     = msg["text"].strip()
        chat_id  = msg["chat_id"]
        msg_id   = msg["message_id"]
        user     = msg["user"]

        self.channel.send_typing(chat_id)

        # Web search command
        if self.webtools.get("enabled"):
            for trigger in SEARCH_TRIGGER:
                if text.lower().startswith(trigger):
                    query = text[len(trigger):].strip()
                    self._handle_search(chat_id, msg_id, query)
                    return

        # Normal AI response
        response = self._call_ai(text, user)

        self.channel.send(chat_id, response, reply_to=msg_id)

        # Update context log
        ts = datetime.now(timezone.utc).strftime("%m-%d %H:%M")
        entry = f"[{ts}] {user}: {text[:80]}\n[{ts}] bot: {response[:120]}"
        append_context(self.name, entry)

        # Save backup
        save_backup(self.name, {
            "ts":       datetime.now(timezone.utc).isoformat(),
            "chat_id":  chat_id,
            "user":     user,
            "input":    text,
            "output":   response[:500],
        })

    def _call_ai(self, user_text: str, user: str) -> str:
        soul    = get_soul(self.name)
        context = get_context(self.name)

        system = soul
        if context.strip():
            system += "\n\n" + CONTEXT_SYSTEM.format(context=context[-3000:])

        messages = [{"role": "user", "content": user_text}]
        try:
            return call_model(self.model_cfg, messages, system=system)
        except Exception as e:
            return f"⚠️ Ошибка модели: {e}"

    def _handle_search(self, chat_id: int, msg_id: int, query: str):
        if not query:
            self.channel.send(chat_id, "Укажи поисковый запрос: /search <запрос>")
            return
        results = search(query, self.webtools)
        formatted = format_results(results)
        # Ask AI to summarize results
        soul = get_soul(self.name)
        prompt = f"Пользователь ищет: {query}\n\nРезультаты поиска:\n{formatted}\n\nДай краткий ответ на основе этих данных."
        try:
            response = call_model(
                self.model_cfg,
                [{"role": "user", "content": prompt}],
                system=soul,
            )
        except Exception as e:
            response = f"Результаты поиска:\n{formatted}"
        self.channel.send(chat_id, response, reply_to=msg_id)


def run_bot(name: str):
    runner = BotRunner(name)
    runner.run()

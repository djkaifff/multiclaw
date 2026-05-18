"""
Bot runner — main loop. Called by `multiclaw run <name>`.
Polls channel → dispatches skills or calls model → sends response.
Supports Telegram, Discord, Slack via unified channel interface.
"""
import os
import time
import signal
from datetime import datetime, timezone
from lib.state import (
    get_config, get_soul, get_context, append_context,
    save_backup, agent_dir,
)
from lib.models import call_model
from lib.channels import create_channel
from lib.webtools import search, format_results
from lib import skills as skills_lib

CONTEXT_SYSTEM = """
Ниже — краткий лог твоих недавних действий и диалогов.
Используй его чтобы поддерживать контекст разговора.
---
{context}
"""

SEARCH_TRIGGERS = ["/search ", "/поиск ", "/web "]


class BotRunner:
    def __init__(self, name: str):
        self.name = name
        self.cfg  = get_config(name)
        self._validate()

        self.channel    = create_channel(self.cfg["channel"])
        self.model_cfg  = self.cfg["model"]
        self.webtools   = self.cfg.get("webtools", {})

        self._running = True
        signal.signal(signal.SIGTERM, self._handle_stop)
        signal.signal(signal.SIGINT,  self._handle_stop)

        (agent_dir(name) / "bot.pid").write_text(str(os.getpid()))

    def _validate(self):
        if not self.cfg.get("model", {}).get("api_key"):
            raise RuntimeError(f"Бот '{self.name}': модель не настроена")
        ch = self.cfg.get("channel", {})
        if not ch:
            raise RuntimeError(f"Бот '{self.name}': канал не настроен")
        ch_type = ch.get("type", "telegram")
        if ch_type == "telegram" and not ch.get("token"):
            raise RuntimeError(f"Бот '{self.name}': Telegram token не указан")
        elif ch_type == "discord" and not ch.get("token"):
            raise RuntimeError(f"Бот '{self.name}': Discord token не указан")
        elif ch_type == "slack" and (not ch.get("bot_token") or not ch.get("app_token")):
            raise RuntimeError(f"Бот '{self.name}': Slack bot_token и app_token обязательны")

    def _handle_stop(self, *_):
        self._running = False

    def run(self):
        ch_type = self.cfg["channel"].get("type", "telegram")
        print(f"[{self.name}] Запущен. Канал: {ch_type}. Модель: {self.model_cfg.get('model_id')}")
        while self._running:
            try:
                messages = self.channel.poll(timeout=25)
                for msg in messages:
                    self._handle_message(msg)
            except Exception as e:
                print(f"[{self.name}] Ошибка: {e}")
                time.sleep(5)

        (agent_dir(self.name) / "bot.pid").unlink(missing_ok=True)
        print(f"[{self.name}] Остановлен.")

    def _handle_message(self, msg: dict):
        text    = msg["text"].strip()
        chat_id = msg["chat_id"]
        msg_id  = msg["message_id"]
        user    = msg["user"]

        self.channel.send_typing(chat_id)

        # ── Skill triggers (/github, /notion, /fetch, ...) ────────────
        for trigger in skills_lib.SKILL_TRIGGERS:
            if text.lower().startswith(trigger):
                args   = text[len(trigger):].strip()
                result = skills_lib.dispatch_skill(
                    trigger, args, self.cfg, self.model_cfg, get_soul(self.name)
                )
                if result:
                    self.channel.send(chat_id, result, reply_to=msg_id)
                    self._log(user, text, result)
                    return

        # ── Web search (/search, /поиск, /web) ───────────────────────
        if self.webtools.get("enabled"):
            for trigger in SEARCH_TRIGGERS:
                if text.lower().startswith(trigger):
                    query = text[len(trigger):].strip()
                    self._handle_search(chat_id, msg_id, query)
                    return

        # ── Normal AI response ────────────────────────────────────────
        response = self._call_ai(text, user)
        self.channel.send(chat_id, response, reply_to=msg_id)
        self._log(user, text, response)

    def _call_ai(self, user_text: str, user: str) -> str:
        soul    = get_soul(self.name)
        context = get_context(self.name)
        system  = soul
        if context.strip():
            system += "\n\n" + CONTEXT_SYSTEM.format(context=context[-3000:])
        try:
            return call_model(self.model_cfg,
                              [{"role": "user", "content": user_text}],
                              system=system)
        except Exception as e:
            return f"⚠️ Ошибка модели: {e}"

    def _handle_search(self, chat_id, msg_id, query: str):
        if not query:
            self.channel.send(chat_id, "Укажи запрос: /search <запрос>")
            return
        results   = search(query, self.webtools)
        formatted = format_results(results)
        soul      = get_soul(self.name)
        prompt    = (f"Пользователь ищет: {query}\n\nРезультаты поиска:\n{formatted}\n\n"
                     "Дай краткий ответ на основе этих данных.")
        try:
            response = call_model(self.model_cfg,
                                  [{"role": "user", "content": prompt}],
                                  system=soul)
        except Exception:
            response = f"Результаты поиска:\n{formatted}"
        self.channel.send(chat_id, response, reply_to=msg_id)

    def _log(self, user: str, inp: str, out: str):
        ts    = datetime.now(timezone.utc).strftime("%m-%d %H:%M")
        entry = f"[{ts}] {user}: {inp[:80]}\n[{ts}] bot: {out[:120]}"
        append_context(self.name, entry)
        save_backup(self.name, {
            "ts":      datetime.now(timezone.utc).isoformat(),
            "user":    user,
            "input":   inp,
            "output":  out[:500],
        })


def run_bot(name: str):
    runner = BotRunner(name)
    runner.run()

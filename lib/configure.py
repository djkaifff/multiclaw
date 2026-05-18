"""
Interactive configuration wizard — `multiclaw configure`.
"""
import sys
import questionary
from questionary import Style

from lib import state, models, channels, webtools, health

STYLE = Style([
    ("qmark",      "fg:#00d7ff bold"),
    ("question",   "bold"),
    ("answer",     "fg:#00d7ff bold"),
    ("pointer",    "fg:#00d7ff bold"),
    ("highlighted","fg:#00d7ff bold"),
    ("selected",   "fg:#7dffb2"),
    ("separator",  "fg:#555555"),
    ("instruction","fg:#555555"),
])

BANNER = """
\033[1;36m╔══════════════════════════════════════════╗
║         MULTICLAW  v0.1.0                ║
║   Universal AI Bot Orchestrator          ║
╚══════════════════════════════════════════╝\033[0m
"""


# ── Entry point ──────────────────────────────────────────────────────

def run():
    state.ensure_multiclaw_dir()
    print(BANNER)

    while True:
        agents = state.get_agents()
        _print_bots(agents)

        choices = []
        if agents:
            for name in agents:
                choices.append(questionary.Choice(
                    title=health.status_line(name),
                    value=f"edit:{name}"
                ))
            choices.append(questionary.Separator())

        choices += [
            questionary.Choice("  + Создать нового бота", value="new"),
            questionary.Choice("  × Удалить бота",        value="delete") if agents else None,
            questionary.Separator(),
            questionary.Choice("  q Выйти",               value="quit"),
        ]
        choices = [c for c in choices if c is not None]

        action = questionary.select(
            "Выбери действие:",
            choices=choices,
            style=STYLE,
        ).ask()

        if action is None or action == "quit":
            break
        elif action == "new":
            _create_bot_wizard()
        elif action == "delete":
            _delete_bot_wizard(agents)
        elif action and action.startswith("edit:"):
            name = action.split(":", 1)[1]
            _edit_bot_wizard(name)


# ── Bot list display ─────────────────────────────────────────────────

def _print_bots(agents: list[str]):
    if not agents:
        print("  Нет настроенных ботов.\n")
        return
    print(f"  {'●/○':<4} {'Имя':<16} {'Канал':<10} {'Модель'}")
    print("  " + "─" * 55)


# ── Create new bot wizard ────────────────────────────────────────────

def _create_bot_wizard(bot_name: str = None):
    print("\n\033[1m=== Новый бот ===\033[0m\n")

    if not bot_name:
        bot_name = questionary.text(
            "Имя бота (используется как имя workspace):",
            validate=lambda v: (
                True if v.strip() and v.strip().replace("-","").replace("_","").isalnum()
                else "Используй только буквы, цифры, - и _"
            ),
            style=STYLE,
        ).ask()
        if not bot_name:
            return
        bot_name = bot_name.strip()

    if state.agent_exists(bot_name):
        print(f"\033[33m  Бот '{bot_name}' уже существует — перейди в настройку.\033[0m\n")
        return

    # Create workspace
    state.create_workspace(bot_name)
    print(f"\n  ✓ Workspace создан: {state.agent_dir(bot_name)}\n")

    cfg = state.get_config(bot_name)

    # Step 1: Model
    print("\033[1m=== Шаг 1: Модель ===\033[0m\n")
    model_cfg = _setup_model_wizard(bot_name)
    if not model_cfg:
        print("  Пропущено.\n")
    else:
        cfg["model"] = model_cfg

    # Step 2: Channel
    print("\n\033[1m=== Шаг 2: Канал связи ===\033[0m\n")
    channel_cfg = _setup_channel_wizard()
    if not channel_cfg:
        print("  Пропущено.\n")
    else:
        cfg["channel"] = channel_cfg

    # Step 3: Web search
    print("\n\033[1m=== Шаг 3: Web Search ===\033[0m\n")
    wt_cfg = _setup_websearch_wizard()
    cfg["webtools"] = wt_cfg

    state.save_config(bot_name, cfg)
    _print_summary(bot_name, cfg)

    # Offer to start
    if cfg.get("model", {}).get("api_key") and cfg.get("channel", {}).get("token"):
        start = questionary.confirm(
            "Запустить бота сейчас?", default=True, style=STYLE
        ).ask()
        if start:
            _start_bot(bot_name)


# ── Edit existing bot ────────────────────────────────────────────────

def _edit_bot_wizard(bot_name: str):
    print(f"\n\033[1m=== Настройка бота: {bot_name} ===\033[0m\n")

    cfg = state.get_config(bot_name)

    action = questionary.select(
        "Что настроить?",
        choices=[
            questionary.Choice("  Модель",          value="model"),
            questionary.Choice("  Канал связи",     value="channel"),
            questionary.Choice("  Web Search",      value="websearch"),
            questionary.Choice("  Soul (личность)", value="soul"),
            questionary.Choice("  Heartbeat (CRON)","value", value="heartbeat"),
            questionary.Separator(),
            questionary.Choice("  ▶ Запустить бота", value="start"),
            questionary.Choice("  ■ Остановить бота","value", value="stop"),
            questionary.Separator(),
            questionary.Choice("  ← Назад", value="back"),
        ],
        style=STYLE,
    ).ask()

    if action == "model":
        model_cfg = _setup_model_wizard(bot_name)
        if model_cfg:
            cfg["model"] = model_cfg
            state.save_config(bot_name, cfg)
            print("  ✓ Модель сохранена.\n")

    elif action == "channel":
        channel_cfg = _setup_channel_wizard()
        if channel_cfg:
            cfg["channel"] = channel_cfg
            state.save_config(bot_name, cfg)
            print("  ✓ Канал сохранён.\n")

    elif action == "websearch":
        wt_cfg = _setup_websearch_wizard()
        cfg["webtools"] = wt_cfg
        state.save_config(bot_name, cfg)
        print("  ✓ Web search сохранён.\n")

    elif action == "soul":
        _edit_soul(bot_name)

    elif action == "heartbeat":
        _edit_heartbeat(bot_name)

    elif action == "start":
        _start_bot(bot_name)

    elif action == "stop":
        _stop_bot(bot_name)


# ── Model setup wizard ───────────────────────────────────────────────

def _setup_model_wizard(bot_name: str) -> dict | None:
    available = models.detect_available_providers()

    provider_choices = []

    # Available providers first (CLI tools or detected API keys)
    if available:
        print("  Обнаружены доступные провайдеры:")
        for p in available:
            is_cli = p.get("api_type", "") in ("claude-cli", "codex-cli", "gemini-cli")
            if is_cli:
                label = f"  ● {p['name']:<22} (CLI, ключ не нужен)"
            else:
                key = p.get("api_key", "")
                key_preview = key[:12] + "..." if len(key) > 12 else key
                src = p.get("_source", "")
                src_note = f"  [{src}]" if src else ""
                label = f"  ● {p['name']:<22} ({key_preview}){src_note}"
            provider_choices.append(questionary.Choice(
                title=label,
                value={"type": "existing", "data": p},
            ))
        provider_choices.append(questionary.Separator())

    # Providers not yet found
    found_ids = {p.get("provider_id") for p in available}

    for pid, pdata in models.CLI_PROVIDERS.items():
        if pid not in found_ids:
            provider_choices.append(questionary.Choice(
                title=f"  ○ {pdata['name']:<22} {pdata['description']}",
                value={"type": "new_cli", "provider_id": pid, "data": pdata},
            ))

    for pid, pdata in models.PROVIDERS.items():
        if pid not in found_ids:
            provider_choices.append(questionary.Choice(
                title=f"  ○ {pdata['name']:<22} {pdata['description']}",
                value={"type": "new", "provider_id": pid, "data": pdata},
            ))

    provider_choices.append(questionary.Separator())
    provider_choices.append(questionary.Choice("  ← Пропустить", value=None))

    selection = questionary.select(
        "Выбери провайдер:",
        choices=provider_choices,
        style=STYLE,
    ).ask()

    if not selection:
        return None

    if selection["type"] == "existing":
        p = selection["data"]
        provider_id     = p.get("provider_id", "openrouter")
        api_key         = p.get("api_key", "")
        api_type        = p["api_type"]
        base_url        = p.get("base_url", "")
        provider_models = p["models"]
    elif selection["type"] == "new_cli":
        provider_id     = selection["provider_id"]
        p               = selection["data"]
        api_type        = p["api_type"]
        base_url        = p.get("base_url", "")
        api_key         = provider_id   # sentinel — no real key needed
        provider_models = p["models"]
    else:
        provider_id     = selection["provider_id"]
        p               = selection["data"]
        api_type        = p["api_type"]
        base_url        = p.get("base_url", "")
        provider_models = p["models"]

        api_key = questionary.password(
            f"API ключ ({p.get('key_hint', '')}):",
            style=STYLE,
        ).ask()
        if not api_key:
            return None

    # Select model
    model_choices = [
        questionary.Choice(
            title=f"  {m['name']:<28} {m.get('tags','')}",
            value=m["id"],
        )
        for m in provider_models
    ]
    model_choices.append(questionary.Choice("  Ввести ID вручную", value="__manual__"))

    model_id = questionary.select(
        "Выбери модель:",
        choices=model_choices,
        style=STYLE,
    ).ask()

    if not model_id:
        return None

    if model_id == "__manual__":
        model_id = questionary.text("ID модели:", style=STYLE).ask()
        if not model_id:
            return None

    model_cfg = {
        "provider_id": provider_id,
        "api_type":    api_type,
        "base_url":    base_url,
        "api_key":     api_key,
        "model_id":    model_id,
    }

    # Test connection
    print("  Проверяю подключение...", end="", flush=True)
    ok, msg = models.test_model_connection(model_cfg)
    if ok:
        print(f" \033[32m✓ {msg}\033[0m")
    else:
        print(f" \033[33m⚠ {msg}\033[0m")
        cont = questionary.confirm(
            "Сохранить несмотря на ошибку?", default=False, style=STYLE
        ).ask()
        if not cont:
            return None

    return model_cfg


# ── Channel setup wizard ─────────────────────────────────────────────

def _setup_channel_wizard() -> dict | None:
    channel_type = questionary.select(
        "Тип канала:",
        choices=[
            questionary.Choice("  Telegram",        value="telegram"),
            questionary.Choice("  Discord (скоро)", value=None),
            questionary.Choice("  ← Пропустить",    value=None),
        ],
        style=STYLE,
    ).ask()

    if not channel_type:
        return None

    if channel_type == "telegram":
        return _setup_telegram()

    return None


def _setup_telegram() -> dict | None:
    token = questionary.password(
        "Telegram Bot Token (от @BotFather):",
        style=STYLE,
    ).ask()
    if not token:
        return None
    token = token.strip()

    print("  Проверяю токен...", end="", flush=True)
    ok, username = channels.test_telegram_token(token)
    if ok:
        print(f" \033[32m✓ {username}\033[0m")
    else:
        print(f" \033[31m✗ {username}\033[0m")
        return None

    return _build_telegram_config(token, username)


def _build_telegram_config(token: str, username: str) -> dict:
    allow_all = questionary.confirm(
        "Принимать сообщения из любых чатов?",
        default=True, style=STYLE,
    ).ask()

    cfg = {"type": "telegram", "token": token, "username": username}
    if not allow_all:
        chat_ids_str = questionary.text(
            "Chat ID через запятую (числа):",
            style=STYLE,
        ).ask()
        if chat_ids_str:
            try:
                cfg["allowed_chats"] = [int(x.strip()) for x in chat_ids_str.split(",")]
            except ValueError:
                print("  ⚠ Неверный формат, пропускаю.")
    return cfg


# ── Web search wizard ────────────────────────────────────────────────

def _setup_websearch_wizard() -> dict:
    enabled = questionary.confirm(
        "Включить веб-поиск для бота?",
        default=False, style=STYLE,
    ).ask()

    if not enabled:
        return {"enabled": False}

    provider_choices = []
    for pid, pdata in webtools.PROVIDERS.items():
        key_note = "" if not pdata["requires_key"] else " (требует API ключ)"
        provider_choices.append(questionary.Choice(
            title=f"  {pdata['name']:<18} {pdata['description']}",
            value=pid,
        ))

    provider = questionary.select(
        "Поисковый провайдер:",
        choices=provider_choices,
        style=STYLE,
    ).ask()

    cfg: dict = {"enabled": True, "provider": provider, "max_results": 5}

    if webtools.PROVIDERS[provider]["requires_key"]:
        api_key = questionary.password(
            f"API ключ ({webtools.PROVIDERS[provider]['key_hint']}):",
            style=STYLE,
        ).ask()
        cfg["api_key"] = api_key or ""
        if provider == "google":
            cx = questionary.text("Custom Search Engine ID (cx):", style=STYLE).ask()
            cfg["cx"] = cx or ""

    return cfg


# ── Soul & Heartbeat editors ─────────────────────────────────────────

def _edit_soul(bot_name: str):
    soul_path = state.agent_dir(bot_name) / "soul.md"
    print(f"\n  Редактируй файл: \033[1m{soul_path}\033[0m")
    print("  Soul.md — это системный промпт (личность) бота.\n")
    questionary.press_any_key_to_continue(style=STYLE).ask()


def _edit_heartbeat(bot_name: str):
    import json
    hb_path = state.agent_dir(bot_name) / "heartbeat.json"
    hb = json.loads(hb_path.read_text()) if hb_path.exists() else state.HEARTBEAT_DEFAULT.copy()

    enabled = questionary.confirm(
        "Включить автозапуск по расписанию?",
        default=hb.get("enabled", False),
        style=STYLE,
    ).ask()

    if enabled:
        cron = questionary.text(
            "CRON выражение (например: 0 9 * * * — каждый день в 9:00):",
            default=hb.get("cron", ""),
            style=STYLE,
        ).ask()
        task = questionary.text(
            "Задача для бота при автозапуске:",
            default=hb.get("task", "Проверь последние новости и пришли сводку."),
            style=STYLE,
        ).ask()
        hb = {"enabled": True, "cron": cron, "task": task}
    else:
        hb = {"enabled": False, "cron": "", "task": ""}

    hb_path.write_text(json.dumps(hb, indent=2, ensure_ascii=False))
    print("  ✓ Heartbeat сохранён.\n")


# ── Start / Stop ─────────────────────────────────────────────────────

def _start_bot(bot_name: str):
    import subprocess, shutil
    runner_cmd = f"cd {state.MULTICLAW_DIR.parent} && python3 -m multiclaw run {bot_name}"

    if shutil.which("pm2"):
        pm2_name = f"multiclaw-{bot_name}"
        result = subprocess.run(
            ["pm2", "start", "python3",
             "--name", pm2_name,
             "--", "-m", "multiclaw", "run", bot_name],
            capture_output=True, text=True,
            cwd=str(state.MULTICLAW_DIR.parent / "multiclaw"),
        )
        if result.returncode == 0:
            subprocess.run(["pm2", "save"], capture_output=True)
            print(f"\n  \033[32m✓ {bot_name} запущен (PM2: {pm2_name})\033[0m\n")
        else:
            # PM2 may complain if already running — try restart
            subprocess.run(["pm2", "restart", pm2_name], capture_output=True)
            print(f"\n  \033[32m✓ {bot_name} перезапущен (PM2)\033[0m\n")
    else:
        import os
        pid = subprocess.Popen(
            ["python3", "-m", "multiclaw", "run", bot_name],
            cwd="/root/multiclaw",
            stdout=open(state.agent_dir(bot_name) / "bot.log", "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        ).pid
        print(f"\n  \033[32m✓ {bot_name} запущен (PID {pid})\033[0m\n")


def _stop_bot(bot_name: str):
    import subprocess, shutil
    if shutil.which("pm2"):
        subprocess.run(["pm2", "stop", f"multiclaw-{bot_name}"], capture_output=True)
    pid_file = state.agent_dir(bot_name) / "bot.pid"
    if pid_file.exists():
        try:
            import signal as sig
            pid = int(pid_file.read_text().strip())
            os.kill(pid, sig.SIGTERM)
        except Exception:
            pass
        pid_file.unlink(missing_ok=True)
    print(f"  ■ {bot_name} остановлен.\n")


# ── Delete bot wizard ────────────────────────────────────────────────

def _delete_bot_wizard(agents: list[str]):
    choices = [questionary.Choice(name, value=name) for name in agents]
    choices.append(questionary.Choice("← Отмена", value=None))

    name = questionary.select(
        "Выбери бота для удаления:",
        choices=choices,
        style=STYLE,
    ).ask()

    if not name:
        return

    confirm = questionary.confirm(
        f"Удалить '{name}' и весь его workspace?",
        default=False, style=STYLE,
    ).ask()

    if confirm:
        _stop_bot(name)
        state.delete_agent(name)
        print(f"  ✓ Бот '{name}' удалён.\n")


# ── Summary ──────────────────────────────────────────────────────────

def _print_summary(bot_name: str, cfg: dict):
    print(f"\n\033[1;32m╔══ Бот '{bot_name}' настроен ══╗\033[0m")
    print(f"  Workspace : {state.agent_dir(bot_name)}")

    model = cfg.get("model", {})
    if model:
        print(f"  Модель    : {model.get('provider_id','?')} / {model.get('model_id','?')}")
    else:
        print("  Модель    : не настроена")

    ch = cfg.get("channel", {})
    if ch:
        print(f"  Канал     : {ch.get('type','?')} {ch.get('username','')}")
    else:
        print("  Канал     : не настроен")

    wt = cfg.get("webtools", {})
    print(f"  Web Search: {'✓ ' + wt.get('provider','') if wt.get('enabled') else '○ выключен'}")
    print()

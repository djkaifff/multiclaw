#!/usr/bin/env python3
"""
Multiclaw — Universal AI Bot Orchestrator
Usage:
  multiclaw configure       — interactive bot management
  multiclaw start <name>    — start a bot
  multiclaw stop <name>     — stop a bot
  multiclaw status          — show all bots status
  multiclaw run <name>      — run bot loop (used internally by start)
"""
import sys
import os

# Ensure lib is importable regardless of cwd
sys.path.insert(0, os.path.dirname(__file__))


def main():
    args = sys.argv[1:]
    cmd  = args[0] if args else "configure"

    if cmd == "configure":
        from lib.configure import run
        run()

    elif cmd == "start":
        if len(args) < 2:
            print("Usage: multiclaw start <bot-name>")
            sys.exit(1)
        _start(args[1])

    elif cmd == "stop":
        if len(args) < 2:
            print("Usage: multiclaw stop <bot-name>")
            sys.exit(1)
        _stop(args[1])

    elif cmd == "status":
        _status()

    elif cmd == "run":
        if len(args) < 2:
            print("Usage: multiclaw run <bot-name>")
            sys.exit(1)
        from lib.runner import run_bot
        run_bot(args[1])

    elif cmd in ("--help", "-h", "help"):
        print(__doc__)

    else:
        print(f"Unknown command: {cmd}")
        print("Run: multiclaw --help")
        sys.exit(1)


def _start(name: str):
    from lib.state import agent_exists, get_config
    from lib.configure import _start_bot

    if not agent_exists(name):
        print(f"Бот '{name}' не найден. Запусти: multiclaw configure")
        sys.exit(1)
    cfg = get_config(name)
    if not cfg.get("model", {}).get("api_key"):
        print(f"Бот '{name}': модель не настроена. Запусти: multiclaw configure")
        sys.exit(1)
    _start_bot(name)


def _stop(name: str):
    from lib.configure import _stop_bot
    _stop_bot(name)


def _status():
    from lib.health import check_all, status_line
    from lib.state import get_agents

    agents = get_agents()
    if not agents:
        print("Нет настроенных ботов. Запусти: multiclaw configure")
        return

    print("\n\033[1mMULTICLAW STATUS\033[0m")
    print(f"  {'●/○':<4} {'Имя':<16} {'Канал':<10} {'Модель'}")
    print("  " + "─" * 55)
    for name in agents:
        print(status_line(name))
    print()


if __name__ == "__main__":
    main()

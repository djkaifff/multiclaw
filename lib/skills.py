"""
Skills management — per-bot skill registry.
Skills are shell scripts or Python modules in ~/.multiclaw/agents/{name}/skills/
"""
import json
import stat
from pathlib import Path
from lib.state import agent_dir, MULTICLAW_DIR

BUILTIN_SKILLS = {
    "web-search":   "Веб-поиск (DuckDuckGo / Brave / Google)",
    "code-runner":  "Выполнение кода в sandbox",
    "file-manager": "Работа с файлами workspace",
    "reminder":     "Напоминания и CRON задачи",
    "summarizer":   "Автосжатие контекста диалога",
}

GLOBAL_SKILLS_DIR = MULTICLAW_DIR / "skills"


def list_skills(bot_name: str) -> dict:
    """Returns {skill_id: {enabled, description, source}}."""
    result = {}
    cfg_path = agent_dir(bot_name) / "skills" / "skills.json"
    enabled = {}
    if cfg_path.exists():
        try:
            enabled = json.loads(cfg_path.read_text())
        except Exception:
            pass

    # Built-in skills
    for sid, desc in BUILTIN_SKILLS.items():
        result[sid] = {
            "enabled": enabled.get(sid, {}).get("enabled", False),
            "description": desc,
            "source": "builtin",
        }

    # Bot-specific custom skills
    skills_dir = agent_dir(bot_name) / "skills"
    for f in skills_dir.glob("*.sh"):
        sid = f.stem
        if sid not in result:
            result[sid] = {
                "enabled": enabled.get(sid, {}).get("enabled", True),
                "description": f"Custom: {sid}",
                "source": "custom",
            }

    return result


def enable_skill(bot_name: str, skill_id: str, enabled: bool = True):
    cfg_path = agent_dir(bot_name) / "skills" / "skills.json"
    try:
        data = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    except Exception:
        data = {}
    data.setdefault(skill_id, {})["enabled"] = enabled
    cfg_path.write_text(json.dumps(data, indent=2))


def create_custom_skill(bot_name: str, skill_id: str, description: str, script: str):
    """Creates a custom skill shell script."""
    skills_dir = agent_dir(bot_name) / "skills"
    script_path = skills_dir / f"{skill_id}.sh"
    script_path.write_text(f"#!/bin/bash\n# {description}\n{script}\n")
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)
    enable_skill(bot_name, skill_id, True)


def get_enabled_skills(bot_name: str) -> list[str]:
    return [sid for sid, info in list_skills(bot_name).items() if info["enabled"]]

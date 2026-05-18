"""
State management — ~/.multiclaw/ directory structure.
"""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

MULTICLAW_DIR = Path.home() / ".multiclaw"
AGENTS_DIR    = MULTICLAW_DIR / "agents"
AGENTS_LIST   = MULTICLAW_DIR / "agents.list"
GLOBAL_CFG    = MULTICLAW_DIR / "multiclaw.json"

SOUL_DEFAULT = """Ты — AI-ассистент. Помогай пользователю точно и кратко.
Отвечай на языке пользователя. Будь полезен, честен и конструктивен."""

HEARTBEAT_DEFAULT = {
    "enabled": False,
    "cron": "",
    "task": ""
}


# ── Directory bootstrap ──────────────────────────────────────────────

def ensure_multiclaw_dir():
    MULTICLAW_DIR.mkdir(exist_ok=True)
    AGENTS_DIR.mkdir(exist_ok=True)
    if not AGENTS_LIST.exists():
        AGENTS_LIST.write_text(json.dumps({"agents": []}, indent=2))
    if not GLOBAL_CFG.exists():
        GLOBAL_CFG.write_text(json.dumps({
            "version": "0.1.0",
            "gateway": {"mode": "local", "auth": {"mode": "token"}},
            "tools": {"web": {"search": {"enabled": False}}},
        }, indent=2))


# ── Agents list ──────────────────────────────────────────────────────

def get_agents() -> list[str]:
    ensure_multiclaw_dir()
    try:
        return json.loads(AGENTS_LIST.read_text()).get("agents", [])
    except Exception:
        return []


def save_agents(agents: list[str]):
    AGENTS_LIST.write_text(json.dumps({"agents": agents}, indent=2))


def agent_exists(name: str) -> bool:
    return name in get_agents()


# ── Agent workspace ──────────────────────────────────────────────────

def agent_dir(name: str) -> Path:
    return AGENTS_DIR / name


def create_workspace(name: str):
    """Creates bot workspace structure."""
    d = agent_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "backups").mkdir(exist_ok=True)
    (d / "skills").mkdir(exist_ok=True)

    soul = d / "soul.md"
    if not soul.exists():
        soul.write_text(SOUL_DEFAULT)

    heartbeat = d / "heartbeat.json"
    if not heartbeat.exists():
        heartbeat.write_text(json.dumps(HEARTBEAT_DEFAULT, indent=2))

    ctx = d / "context.log"
    if not ctx.exists():
        ctx.write_text("")

    cfg = d / "config.json"
    if not cfg.exists():
        cfg.write_text(json.dumps({
            "name": name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "model": {},
            "channel": {},
            "webtools": {"enabled": False},
        }, indent=2))

    # Register agent
    agents = get_agents()
    if name not in agents:
        agents.append(name)
        save_agents(agents)


def get_config(name: str) -> dict:
    cfg = agent_dir(name) / "config.json"
    try:
        return json.loads(cfg.read_text())
    except Exception:
        return {}


def save_config(name: str, config: dict):
    cfg = agent_dir(name) / "config.json"
    cfg.write_text(json.dumps(config, ensure_ascii=False, indent=2))


def get_soul(name: str) -> str:
    soul = agent_dir(name) / "soul.md"
    return soul.read_text() if soul.exists() else SOUL_DEFAULT


def get_context(name: str) -> str:
    ctx = agent_dir(name) / "context.log"
    return ctx.read_text() if ctx.exists() else ""


def append_context(name: str, entry: str):
    ctx_path = agent_dir(name) / "context.log"
    lines = ctx_path.read_text().splitlines() if ctx_path.exists() else []
    lines.append(entry)
    if len(lines) > 200:
        lines = lines[-200:]
    ctx_path.write_text("\n".join(lines) + "\n")


def save_backup(name: str, action: dict):
    """Keeps last 20 action backups."""
    backups_dir = agent_dir(name) / "backups"
    backups_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    (backups_dir / f"{ts}.json").write_text(
        json.dumps(action, ensure_ascii=False, indent=2)
    )
    # Trim to last 20
    files = sorted(backups_dir.glob("*.json"))
    for old in files[:-20]:
        old.unlink()


def delete_agent(name: str):
    d = agent_dir(name)
    if d.exists():
        shutil.rmtree(d)
    agents = [a for a in get_agents() if a != name]
    save_agents(agents)

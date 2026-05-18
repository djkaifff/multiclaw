"""
Health check — bot and system status.
"""
import subprocess
from lib.state import get_agents, get_config, agent_dir
from lib.models import test_model_connection
from lib.channels import test_telegram_token


def check_bot(name: str) -> dict:
    """Returns health dict for a bot."""
    cfg = get_config(name)
    result = {
        "name":    name,
        "running": _is_running(name),
        "model":   _check_model(cfg),
        "channel": _check_channel(cfg),
        "workspace": str(agent_dir(name)),
    }
    result["healthy"] = result["running"] and result["model"]["ok"] and result["channel"]["ok"]
    return result


def check_all() -> list[dict]:
    return [check_bot(name) for name in get_agents()]


def status_line(name: str) -> str:
    cfg = get_config(name)
    running = _is_running(name)
    dot = "●" if running else "○"
    model_id = cfg.get("model", {}).get("model_id", "—")
    channel  = cfg.get("channel", {}).get("type", "—")
    return f"  {dot} {name:<16} {channel:<10} {model_id}"


# ── Internals ────────────────────────────────────────────────────────

def _is_running(name: str) -> bool:
    try:
        result = subprocess.run(
            ["pm2", "jlist"], capture_output=True, text=True, timeout=5
        )
        import json
        procs = json.loads(result.stdout)
        pm2_name = f"multiclaw-{name}"
        for p in procs:
            if p.get("name") == pm2_name:
                return p.get("pm2_env", {}).get("status") == "online"
    except Exception:
        pass

    # Fallback: check PID file
    pid_file = agent_dir(name) / "bot.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            subprocess.run(["kill", "-0", str(pid)], check=True,
                           capture_output=True, timeout=2)
            return True
        except Exception:
            pid_file.unlink(missing_ok=True)
    return False


def _check_model(cfg: dict) -> dict:
    model = cfg.get("model", {})
    if not model or not model.get("api_key"):
        return {"ok": False, "msg": "не настроена"}
    # Quick check — don't call API each time, just validate config
    required = ["provider_id", "api_key", "model_id", "base_url", "api_type"]
    missing = [k for k in required if not model.get(k)]
    if missing:
        return {"ok": False, "msg": f"отсутствует: {', '.join(missing)}"}
    return {"ok": True, "msg": model.get("model_id", "?")}


def _check_channel(cfg: dict) -> dict:
    channel = cfg.get("channel", {})
    if not channel or not channel.get("type"):
        return {"ok": False, "msg": "не настроен"}
    if channel["type"] == "telegram":
        token = channel.get("token", "")
        if not token:
            return {"ok": False, "msg": "нет токена"}
        return {"ok": True, "msg": "telegram"}
    return {"ok": True, "msg": channel["type"]}

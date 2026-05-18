"""
Skills — built-in integrations and slash-command dispatch.
Each skill is triggered by a command prefix (/fetch, /github, /notion, etc.)
Skills that need API keys read their config from cfg["skills"][skill_id].
"""
import json
import shutil
import stat
import subprocess
from pathlib import Path
from lib.state import agent_dir, MULTICLAW_DIR

# ── Registry ──────────────────────────────────────────────────────────

BUILTIN_SKILLS = {
    "web-search":  "Веб-поиск — /search <запрос>",
    "web-fetch":   "Получить страницу — /fetch <url>",
    "summarize":   "Сжать текст или URL — /summarize <url|текст>",
    "github":      "GitHub Issues — /github <owner/repo> [open|closed]",
    "notion":      "Notion — /notion <запрос>  (нужен Integration Token)",
    "trello":      "Trello — /trello [доска]  (нужны API Key + Token)",
    "obsidian":    "Obsidian заметки — /note <название>",
    "tmux":        "Shell команда — /run <команда>",
    "email":       "Отправить email — /email <to> Тема: Текст",
    "code-runner": "Выполнение Python кода — /code <код>",
    "reminder":    "Напоминания / CRON задачи (через heartbeat)",
}

# trigger prefix → skill_id
SKILL_TRIGGERS: dict[str, str] = {
    "/fetch ":      "web-fetch",
    "/summarize ":  "summarize",
    "/сжать ":      "summarize",
    "/github ":     "github",
    "/notion ":     "notion",
    "/trello":      "trello",
    "/note ":       "obsidian",
    "/заметка ":    "obsidian",
    "/run ":        "tmux",
    "/запусти ":    "tmux",
    "/email ":      "email",
    "/письмо ":     "email",
    "/code ":       "code-runner",
    "/код ":        "code-runner",
}

GLOBAL_SKILLS_DIR = MULTICLAW_DIR / "skills"


# ── Skill registry helpers ────────────────────────────────────────────

def list_skills(bot_name: str) -> dict:
    cfg_path = agent_dir(bot_name) / "skills" / "skills.json"
    enabled: dict = {}
    if cfg_path.exists():
        try:
            enabled = json.loads(cfg_path.read_text())
        except Exception:
            pass

    result = {}
    for sid, desc in BUILTIN_SKILLS.items():
        result[sid] = {
            "enabled": enabled.get(sid, {}).get("enabled", False),
            "description": desc,
            "source": "builtin",
        }

    for f in (agent_dir(bot_name) / "skills").glob("*.sh"):
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


def get_enabled_skills(bot_name: str) -> list[str]:
    return [sid for sid, info in list_skills(bot_name).items() if info["enabled"]]


def create_custom_skill(bot_name: str, skill_id: str, description: str, script: str):
    skills_dir = agent_dir(bot_name) / "skills"
    script_path = skills_dir / f"{skill_id}.sh"
    script_path.write_text(f"#!/bin/bash\n# {description}\n{script}\n")
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)
    enable_skill(bot_name, skill_id, True)


# ── Dispatch ──────────────────────────────────────────────────────────

def dispatch_skill(trigger: str, args: str, cfg: dict,
                   model_cfg: dict, soul: str) -> str | None:
    """
    cfg: full bot config dict (reads cfg["skills"] for per-skill API keys)
    Returns response text, or None if skill not found.
    """
    skill_id = SKILL_TRIGGERS.get(trigger)
    if not skill_id:
        return None
    skills_cfg: dict = cfg.get("skills", {})

    if skill_id == "web-fetch":
        return _skill_web_fetch(args)
    elif skill_id == "summarize":
        return _skill_summarize(args, model_cfg, soul)
    elif skill_id == "github":
        return _skill_github(args, skills_cfg.get("github", {}))
    elif skill_id == "notion":
        return _skill_notion(args, skills_cfg.get("notion", {}))
    elif skill_id == "trello":
        return _skill_trello(args, skills_cfg.get("trello", {}))
    elif skill_id == "obsidian":
        return _skill_obsidian(args, skills_cfg.get("obsidian", {}))
    elif skill_id == "tmux":
        return _skill_tmux(args)
    elif skill_id == "email":
        return _skill_email(args, skills_cfg.get("email", {}))
    elif skill_id == "code-runner":
        return _skill_code_runner(args)
    return None


# ── Implementations ───────────────────────────────────────────────────

def _skill_web_fetch(url: str) -> str:
    url = url.strip()
    if not url:
        return "Укажи URL: /fetch <url>"
    try:
        import requests as _r
        r = _r.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        text = r.text
        # Strip HTML tags roughly
        import re
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s{2,}", "\n", text).strip()
        return f"[{r.status_code}] {url}\n\n{text[:4000]}"
    except Exception as e:
        return f"Ошибка получения {url}: {e}"


def _skill_summarize(args: str, model_cfg: dict, soul: str) -> str:
    from lib.models import call_model
    content = args.strip()
    if not content:
        return "Укажи URL или текст: /summarize <url|текст>"
    if content.startswith("http://") or content.startswith("https://"):
        content = _skill_web_fetch(content)
    try:
        return call_model(
            model_cfg,
            [{"role": "user",
              "content": f"Сожми следующий текст в 3-5 ключевых пунктах:\n\n{content[:6000]}"}],
            system=soul,
        )
    except Exception as e:
        return f"Ошибка: {e}"


def _skill_github(args: str, skill_cfg: dict) -> str:
    parts = args.strip().split()
    repo  = parts[0] if parts else ""
    state = parts[1] if len(parts) > 1 else "open"
    if not repo:
        return "Укажи репо: /github owner/repo [open|closed]"

    if shutil.which("gh"):
        result = subprocess.run(
            ["gh", "issue", "list", "--repo", repo, "--state", state, "--limit", "10"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return result.stdout.strip() or "Нет задач."
        return f"gh error: {result.stderr[:200]}"

    import requests as _r
    token = skill_cfg.get("github_token", "")
    headers = {"Authorization": f"token {token}"} if token else {}
    try:
        r = _r.get(
            f"https://api.github.com/repos/{repo}/issues",
            params={"state": state, "per_page": 10},
            headers=headers, timeout=10,
        )
        items = r.json()
        if not isinstance(items, list):
            return str(items)
        lines = [f"#{i['number']} [{i['state']}] {i['title']}" for i in items]
        return "\n".join(lines) or "Нет задач."
    except Exception as e:
        return f"Ошибка GitHub: {e}"


def _skill_notion(args: str, skill_cfg: dict) -> str:
    token = skill_cfg.get("notion_token", "")
    if not token:
        return "Notion не настроен. Добавь notion_token в настройки скилла."
    import requests as _r
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    try:
        r = _r.post(
            "https://api.notion.com/v1/search",
            headers=headers,
            json={"query": args.strip(), "page_size": 5},
            timeout=10,
        )
        results = r.json().get("results", [])
        if not results:
            return "Ничего не найдено в Notion."
        lines = []
        for item in results:
            obj_type = item.get("object", "page")
            title = ""
            if obj_type == "page":
                for v in item.get("properties", {}).values():
                    if v.get("type") == "title":
                        title = "".join(t.get("plain_text", "") for t in v.get("title", []))
                        break
            elif obj_type == "database":
                title = "".join(t.get("plain_text", "") for t in item.get("title", []))
            url = item.get("url", "")
            lines.append(f"[{obj_type}] {title or '(без названия)'}  {url}")
        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка Notion: {e}"


def _skill_trello(args: str, skill_cfg: dict) -> str:
    api_key = skill_cfg.get("trello_key", "")
    token   = skill_cfg.get("trello_token", "")
    if not api_key or not token:
        return "Trello не настроен. Добавь trello_key и trello_token."
    import requests as _r
    auth = {"key": api_key, "token": token}
    try:
        r = _r.get("https://api.trello.com/1/members/me/boards",
                   params={**auth, "fields": "name,id"}, timeout=10)
        boards = r.json()
        if not boards:
            return "Нет досок Trello."
        query = args.strip().lower()
        board = next((b for b in boards if query and query in b["name"].lower()), boards[0])
        r2 = _r.get(f"https://api.trello.com/1/boards/{board['id']}/cards",
                    params={**auth, "fields": "name,due"}, timeout=10)
        cards = r2.json()[:10]
        if not cards:
            return f"Нет карточек в «{board['name']}»."
        lines = [f"Доска: {board['name']}"]
        for c in cards:
            due = f" (до {c['due'][:10]})" if c.get("due") else ""
            lines.append(f"• {c['name']}{due}")
        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка Trello: {e}"


def _skill_obsidian(args: str, skill_cfg: dict) -> str:
    vault_path = skill_cfg.get("obsidian_vault", "")
    if not vault_path:
        return "Obsidian vault не настроен. Укажи obsidian_vault в настройках."
    vault = Path(vault_path).expanduser()
    query = args.strip()
    if not query:
        notes = list(vault.rglob("*.md"))[:10]
        return "Заметки:\n" + "\n".join(f.stem for f in notes)
    for f in vault.rglob("*.md"):
        if query.lower() in f.stem.lower():
            try:
                return f"[{f.stem}]\n{f.read_text()[:3000]}"
            except Exception:
                return f"Не удалось прочитать {f}"
    return f"Заметка «{query}» не найдена."


def _skill_tmux(cmd: str) -> str:
    cmd = cmd.strip()
    if not cmd:
        return "Укажи команду: /run <команда>"
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=30,
        )
        output = (result.stdout + result.stderr).strip()
        return output[:2000] or "(нет вывода)"
    except subprocess.TimeoutExpired:
        return "Команда превысила таймаут 30с."
    except Exception as e:
        return f"Ошибка: {e}"


def _skill_email(args: str, skill_cfg: dict) -> str:
    import re
    m = re.match(r"(\S+)\s+(.+?):\s+(.+)", args.strip(), re.DOTALL)
    if not m:
        return "Формат: /email to@example.com Тема: Текст сообщения"
    to, subject, body = m.group(1), m.group(2), m.group(3)

    if shutil.which("himalaya"):
        result = subprocess.run(
            ["himalaya", "send", "--to", to, "--subject", subject],
            input=body, capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return f"✓ Email отправлен на {to}."
        return f"Ошибка himalaya: {result.stderr[:200]}"

    smtp_host = skill_cfg.get("smtp_host", "")
    smtp_user = skill_cfg.get("smtp_user", "")
    smtp_pass = skill_cfg.get("smtp_pass", "")
    smtp_port = int(skill_cfg.get("smtp_port", 587))
    if not smtp_host or not smtp_user:
        return "Email не настроен. Укажи smtp_host / smtp_user / smtp_pass."
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"]    = smtp_user
        msg["To"]      = to
        with smtplib.SMTP(smtp_host, smtp_port) as srv:
            srv.starttls()
            srv.login(smtp_user, smtp_pass)
            srv.sendmail(smtp_user, [to], msg.as_string())
        return f"✓ Email отправлен на {to}."
    except Exception as e:
        return f"Ошибка SMTP: {e}"


def _skill_code_runner(code: str) -> str:
    code = code.strip()
    if not code:
        return "Укажи код: /code <код>"
    try:
        result = subprocess.run(
            ["python3", "-c", code],
            capture_output=True, text=True, timeout=10,
        )
        output = (result.stdout + result.stderr).strip()
        return output[:2000] or "(нет вывода)"
    except subprocess.TimeoutExpired:
        return "Код превысил таймаут 10с."
    except Exception as e:
        return f"Ошибка: {e}"

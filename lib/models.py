"""
Model catalog and API abstraction.
Supports: claude-cli, openrouter, anthropic, openai, ollama.
Auto-detects installed CLI tools and environment API keys.
"""
import json
import os
import shutil
import subprocess
import requests
from pathlib import Path

# ── Provider catalog ─────────────────────────────────────────────────

PROVIDERS = {
    "openrouter": {
        "name": "OpenRouter",
        "description": "100+ моделей через один API ключ",
        "api_type": "openai-compatible",
        "base_url": "https://openrouter.ai/api/v1",
        "key_hint": "sk-or-v1-...",
        "models": [
            {"id": "anthropic/claude-opus-4-7",         "name": "Claude Opus 4.7",    "tags": "thinking · powerful"},
            {"id": "anthropic/claude-sonnet-4-6",       "name": "Claude Sonnet 4.6",  "tags": "thinking · fast"},
            {"id": "anthropic/claude-haiku-4-5",        "name": "Claude Haiku 4.5",   "tags": "fast · cheap"},
            {"id": "openai/gpt-4o",                     "name": "GPT-4o",             "tags": "multimodal"},
            {"id": "openai/gpt-4o-mini",                "name": "GPT-4o Mini",        "tags": "fast · cheap"},
            {"id": "google/gemini-2.0-flash-001",       "name": "Gemini 2.0 Flash",   "tags": "fast"},
            {"id": "deepseek/deepseek-chat-v3-0324",    "name": "DeepSeek V3",        "tags": "cheap"},
            {"id": "meta-llama/llama-3.3-70b-instruct", "name": "Llama 3.3 70B",      "tags": "opensource"},
        ],
    },
    "anthropic": {
        "name": "Anthropic",
        "description": "Claude напрямую",
        "api_type": "anthropic-messages",
        "base_url": "https://api.anthropic.com",
        "key_hint": "sk-ant-...",
        "models": [
            {"id": "claude-opus-4-7",           "name": "Claude Opus 4.7",   "tags": "thinking · powerful"},
            {"id": "claude-sonnet-4-6",         "name": "Claude Sonnet 4.6", "tags": "thinking · fast"},
            {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5",  "tags": "fast · cheap"},
        ],
    },
    "openai": {
        "name": "OpenAI",
        "description": "GPT напрямую",
        "api_type": "openai-compatible",
        "base_url": "https://api.openai.com/v1",
        "key_hint": "sk-...",
        "models": [
            {"id": "gpt-4o",      "name": "GPT-4o",      "tags": "multimodal"},
            {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "tags": "fast · cheap"},
            {"id": "o4-mini",     "name": "o4-mini",     "tags": "reasoning"},
        ],
    },
    "google": {
        "name": "Google Gemini",
        "description": "Gemini напрямую",
        "api_type": "google-gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "key_hint": "AIza...",
        "models": [
            {"id": "gemini-2.0-flash",   "name": "Gemini 2.0 Flash",   "tags": "fast"},
            {"id": "gemini-1.5-pro",     "name": "Gemini 1.5 Pro",     "tags": "powerful"},
            {"id": "gemini-1.5-flash",   "name": "Gemini 1.5 Flash",   "tags": "fast · cheap"},
        ],
    },
    "ollama": {
        "name": "Ollama",
        "description": "Локальные модели",
        "api_type": "openai-compatible",
        "base_url": "http://localhost:11434/v1",
        "key_hint": "ollama (ключ не нужен)",
        "models": [
            {"id": "llama3.3",   "name": "Llama 3.3",   "tags": "local"},
            {"id": "mistral",    "name": "Mistral",     "tags": "local"},
            {"id": "deepseek-r1","name": "DeepSeek R1", "tags": "local · reasoning"},
            {"id": "qwen2.5",    "name": "Qwen 2.5",   "tags": "local"},
        ],
    },
}

# CLI-based providers (no API key, use installed tool)
CLI_PROVIDERS = {
    "claude-cli": {
        "name": "Claude Code CLI",
        "description": "Через claude CLI (OAuth, ключ не нужен)",
        "api_type": "claude-cli",
        "base_url": "",
        "api_key": "claude-cli",
        "cli_bin": "claude",
        "models": [
            {"id": "claude-opus-4-7",           "name": "Claude Opus 4.7",   "tags": "thinking · powerful"},
            {"id": "claude-sonnet-4-6",         "name": "Claude Sonnet 4.6", "tags": "thinking · fast"},
            {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5",  "tags": "fast · cheap"},
        ],
    },
    "codex-cli": {
        "name": "OpenAI Codex CLI",
        "description": "Через codex CLI (OAuth, ключ не нужен)",
        "api_type": "codex-cli",
        "base_url": "",
        "api_key": "codex-cli",
        "cli_bin": "codex",
        "models": [
            {"id": "gpt-4o",      "name": "GPT-4o",      "tags": "multimodal"},
            {"id": "o4-mini",     "name": "o4-mini",     "tags": "reasoning"},
        ],
    },
    "gemini-cli": {
        "name": "Google Gemini CLI",
        "description": "Через gemini CLI (OAuth, ключ не нужен)",
        "api_type": "gemini-cli",
        "base_url": "",
        "api_key": "gemini-cli",
        "cli_bin": "gemini",
        "models": [
            {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash", "tags": "fast"},
            {"id": "gemini-1.5-pro",   "name": "Gemini 1.5 Pro",   "tags": "powerful"},
        ],
    },
}


# ── Auto-detect available providers ──────────────────────────────────

def detect_available_providers() -> list[dict]:
    """
    Scans the current system for available AI providers — no external
    dependencies required. Checks:
      1. Installed CLI tools (claude, codex, gemini)
      2. Environment variables (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.)
      3. Common config file locations (~/.config/...)
      4. Ollama running locally
    Returns list ready to pass to the configure wizard.
    """
    found = []

    # 1. CLI tools
    for pid, p in CLI_PROVIDERS.items():
        if shutil.which(p["cli_bin"]):
            entry = dict(p)
            entry["provider_id"] = pid
            found.append(entry)

    # 2. Environment variables
    _env_checks = [
        ("ANTHROPIC_API_KEY",  "anthropic"),
        ("OPENAI_API_KEY",     "openai"),
        ("OPENROUTER_API_KEY", "openrouter"),
        ("GOOGLE_API_KEY",     "google"),
    ]
    seen_env = set()
    for env_var, pid in _env_checks:
        key = os.environ.get(env_var, "").strip()
        if key and pid not in seen_env:
            seen_env.add(pid)
            entry = dict(PROVIDERS[pid])
            entry["provider_id"] = pid
            entry["api_key"] = key
            entry["_source"] = f"env:{env_var}"
            found.append(entry)

    # 3. Config files
    _cfg_checks = [
        (Path.home() / ".config" / "anthropic" / "api_key",  "anthropic"),
        (Path.home() / ".anthropic" / "api_key",             "anthropic"),
        (Path.home() / ".config" / "openai" / "api_key",     "openai"),
        (Path.home() / ".openai" / "api_key",                "openai"),
    ]
    for cfg_path, pid in _cfg_checks:
        if cfg_path.exists() and pid not in seen_env:
            try:
                key = cfg_path.read_text().strip()
                if key:
                    seen_env.add(pid)
                    entry = dict(PROVIDERS[pid])
                    entry["provider_id"] = pid
                    entry["api_key"] = key
                    entry["_source"] = f"file:{cfg_path}"
                    found.append(entry)
            except Exception:
                pass

    # 4. Ollama running locally
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=2)
        if r.ok:
            entry = dict(PROVIDERS["ollama"])
            entry["provider_id"] = "ollama"
            entry["api_key"] = "ollama"
            # Inject actually installed models
            tags = r.json().get("models", [])
            if tags:
                entry["models"] = [
                    {"id": m["name"], "name": m["name"], "tags": "local"}
                    for m in tags[:10]
                ]
            found.append(entry)
    except Exception:
        pass

    return found


# ── API call ─────────────────────────────────────────────────────────

def call_model(model_cfg: dict, messages: list[dict],
               system: str = "") -> str:
    """
    Unified model call.
    model_cfg: {provider_id, api_type, base_url, api_key, model_id}
    Returns assistant response text.
    """
    api_type = model_cfg.get("api_type", "openai-compatible")
    model_id = model_cfg["model_id"]

    if api_type == "claude-cli":
        return _call_claude_cli(model_id, system, messages)
    elif api_type == "codex-cli":
        return _call_codex_cli(model_id, system, messages)
    elif api_type == "gemini-cli":
        return _call_gemini_cli(model_id, system, messages)
    elif api_type == "anthropic-messages":
        return _call_anthropic(
            model_cfg["base_url"].rstrip("/"),
            model_cfg.get("api_key", ""),
            model_id, system, messages,
        )
    elif api_type == "google-gemini":
        return _call_google_gemini(
            model_cfg["base_url"].rstrip("/"),
            model_cfg.get("api_key", ""),
            model_id, system, messages,
        )
    else:
        return _call_openai_compat(
            model_cfg["base_url"].rstrip("/"),
            model_cfg.get("api_key", ""),
            model_id, system, messages,
        )


# ── Provider implementations ─────────────────────────────────────────

def _call_codex_cli(model_id: str, system: str, messages: list[dict]) -> str:
    import tempfile, os as _os
    from lib.codex_auth import is_codex_logged_in, refresh_codex_token

    # Auto-refresh if logged in but token may be stale (best-effort)
    if not is_codex_logged_in():
        raise RuntimeError(
            "Codex CLI не авторизован. Войди через: multiclaw codex-login"
        )
    # Try refresh silently (ignore failures — codex itself will surface auth errors)
    refresh_codex_token()

    parts = []
    if system:
        parts.append(system)
    for m in messages:
        if m["role"] == "user":
            parts.append(m["content"])
    prompt = "\n\n".join(parts)

    out_fd, out_path = tempfile.mkstemp(suffix=".txt")
    _os.close(out_fd)
    try:
        cmd = [
            "codex", "exec",
            "--skip-git-repo-check",
            "--full-auto",
            "--output-last-message", out_path,
        ]
        if model_id:
            cmd += ["-c", f'model="{model_id}"']
        cmd.append(prompt)

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
            stdin=subprocess.DEVNULL, cwd="/tmp",
        )
        output = open(out_path).read().strip()
        if output:
            return output
        if result.returncode != 0 and result.stderr:
            raise RuntimeError(result.stderr[:300])
        return result.stdout.strip()
    finally:
        _os.unlink(out_path)


def _call_claude_cli(model_id: str, system: str, messages: list[dict]) -> str:
    parts = []
    if system:
        parts.append(system)
    for m in messages:
        if m["role"] == "user":
            parts.append(m["content"])
        elif m["role"] == "assistant":
            parts.append(f"[assistant]: {m['content']}")
    prompt = "\n\n".join(parts)

    clean_env = {
        k: v for k, v in os.environ.items()
        if k not in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID",
                     "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_EFFORT")
    }
    result = subprocess.run(
        ["claude", "-p", prompt,
         "--model", model_id,
         "--output-format", "text",
         "--no-session-persistence"],
        capture_output=True, text=True, timeout=120,
        stdin=subprocess.DEVNULL, env=clean_env,
    )
    if result.returncode != 0 and result.stderr:
        raise RuntimeError(result.stderr[:200])
    return result.stdout.strip()


def _call_gemini_cli(model_id: str, system: str, messages: list[dict]) -> str:
    parts = []
    if system:
        parts.append(system)
    for m in messages:
        if m["role"] == "user":
            parts.append(m["content"])
    prompt = "\n\n".join(parts)
    result = subprocess.run(
        ["gemini", "-p", prompt, "-m", model_id],
        capture_output=True, text=True, timeout=120,
        stdin=subprocess.DEVNULL,
    )
    return result.stdout.strip()


def _call_openai_compat(base_url: str, api_key: str, model_id: str,
                        system: str, messages: list[dict]) -> str:
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.extend(messages)

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload  = {"model": model_id, "messages": msgs, "max_tokens": 4096}
    r = requests.post(f"{base_url}/chat/completions",
                      headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _call_anthropic(base_url: str, api_key: str, model_id: str,
                    system: str, messages: list[dict]) -> str:
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    payload = {"model": model_id, "max_tokens": 4096, "messages": messages}
    if system:
        payload["system"] = system
    r = requests.post(f"{base_url}/v1/messages",
                      headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    return r.json()["content"][0]["text"]


def _call_google_gemini(base_url: str, api_key: str, model_id: str,
                        system: str, messages: list[dict]) -> str:
    contents = []
    if system:
        contents.append({"role": "user", "parts": [{"text": f"[System]: {system}"}]})
        contents.append({"role": "model", "parts": [{"text": "Understood."}]})
    for m in messages:
        role = "model" if m["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})

    r = requests.post(
        f"{base_url}/models/{model_id}:generateContent",
        params={"key": api_key},
        json={"contents": contents},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def test_model_connection(model_cfg: dict) -> tuple[bool, str]:
    """Quick connectivity test. Returns (ok, message)."""
    try:
        result = call_model(
            model_cfg,
            [{"role": "user", "content": "ping"}],
            system="Reply with just the word: pong",
        )
        return True, f"OK — {result[:50]}"
    except Exception as e:
        return False, str(e)[:120]

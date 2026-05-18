"""
Web search tools — setup and runtime.
Providers: DuckDuckGo (free), Brave, Google.
"""
import requests


PROVIDERS = {
    "duckduckgo": {
        "name": "DuckDuckGo",
        "description": "Бесплатно, без ключа",
        "requires_key": False,
    },
    "brave": {
        "name": "Brave Search",
        "description": "Требует API ключ (бесплатный план: 2000 запросов/месяц)",
        "requires_key": True,
        "key_hint": "BSA...",
    },
    "google": {
        "name": "Google Custom Search",
        "description": "Требует API ключ и Custom Search Engine ID",
        "requires_key": True,
        "key_hint": "AIza...",
    },
}


def search(query: str, config: dict, max_results: int = 5) -> list[dict]:
    """
    Returns list of {title, url, snippet}.
    config: {provider, api_key?, cx?}
    """
    provider = config.get("provider", "duckduckgo")
    if provider == "duckduckgo":
        return _ddg_search(query, max_results)
    elif provider == "brave":
        return _brave_search(query, config.get("api_key", ""), max_results)
    elif provider == "google":
        return _google_search(query, config.get("api_key", ""),
                              config.get("cx", ""), max_results)
    return []


def format_results(results: list[dict]) -> str:
    if not results:
        return "Ничего не найдено."
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['url']}\n   {r.get('snippet','')}")
    return "\n\n".join(lines)


# ── Providers ────────────────────────────────────────────────────────

def _ddg_search(query: str, max_results: int) -> list[dict]:
    try:
        r = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            timeout=10,
            headers={"User-Agent": "Multiclaw/0.1"}
        )
        data = r.json()
        results = []

        # Abstract
        if data.get("Abstract"):
            results.append({
                "title": data.get("Heading", query),
                "url": data.get("AbstractURL", ""),
                "snippet": data["Abstract"][:300],
            })

        # Related topics
        for topic in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append({
                    "title": topic.get("Text", "")[:60],
                    "url": topic.get("FirstURL", ""),
                    "snippet": topic.get("Text", "")[:200],
                })

        return results[:max_results]
    except Exception as e:
        return [{"title": "Ошибка поиска", "url": "", "snippet": str(e)}]


def _brave_search(query: str, api_key: str, max_results: int) -> list[dict]:
    try:
        r = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
            headers={"Accept": "application/json",
                     "Accept-Encoding": "gzip",
                     "X-Subscription-Token": api_key},
            timeout=10,
        )
        r.raise_for_status()
        results = []
        for item in r.json().get("web", {}).get("results", [])[:max_results]:
            results.append({
                "title":   item.get("title", ""),
                "url":     item.get("url", ""),
                "snippet": item.get("description", "")[:200],
            })
        return results
    except Exception as e:
        return [{"title": "Ошибка поиска", "url": "", "snippet": str(e)}]


def _google_search(query: str, api_key: str, cx: str, max_results: int) -> list[dict]:
    try:
        r = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"q": query, "key": api_key, "cx": cx, "num": max_results},
            timeout=10,
        )
        r.raise_for_status()
        results = []
        for item in r.json().get("items", [])[:max_results]:
            results.append({
                "title":   item.get("title", ""),
                "url":     item.get("link", ""),
                "snippet": item.get("snippet", "")[:200],
            })
        return results
    except Exception as e:
        return [{"title": "Ошибка поиска", "url": "", "snippet": str(e)}]

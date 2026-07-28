"""Provider-agnostic LLM interface for entity extraction and semantic dedup.

Supports: Anthropic, OpenAI, or any OpenAI-compatible endpoint (ollama, together, etc).
Falls back gracefully when no key is configured.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_CONFIG_FILE = Path(os.getenv("SCIMAP_DATA_DIR", os.path.expanduser("~/.scimap"))) / "config.json"


def _load_config() -> dict:
    if _CONFIG_FILE.exists():
        try:
            with open(_CONFIG_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _detect_provider() -> tuple[str | None, str | None, str | None]:
    """Return (provider, api_key, base_url) or (None, None, None)."""
    cfg = _load_config()

    if cfg.get("llm_provider") and cfg.get("llm_api_key"):
        return cfg["llm_provider"], cfg["llm_api_key"], cfg.get("llm_base_url")

    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic", os.getenv("ANTHROPIC_API_KEY"), None
    if os.getenv("OPENAI_API_KEY"):
        return "openai", os.getenv("OPENAI_API_KEY"), None
    if os.getenv("SWAFRA_LLM_API_KEY"):
        base = os.getenv("SWAFRA_LLM_BASE_URL", "https://api.openai.com/v1")
        return "openai-compatible", os.getenv("SWAFRA_LLM_API_KEY"), base

    return None, None, None


def is_llm_available() -> bool:
    provider, key, _ = _detect_provider()
    return provider is not None and key is not None


def _get_model() -> str | None:
    """Get configured model name, or None for default."""
    cfg = _load_config()
    return cfg.get("llm_model")


def _call_anthropic(api_key: str, prompt: str, system: str) -> str | None:
    import urllib.request
    import urllib.error

    model = _get_model() or "claude-haiku-4-5-20251001"
    body = json.dumps({
        "model": model,
        "max_tokens": 1024,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    })
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body.encode(),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return data["content"][0]["text"]
    except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError):
        return None


def _call_openai(api_key: str, base_url: str | None, prompt: str, system: str) -> str | None:
    import urllib.request
    import urllib.error

    model = _get_model() or "gpt-4o-mini"
    url = (base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model,
        "max_tokens": 1024,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    })
    req = urllib.request.Request(
        url,
        data=body.encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"]
    except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError):
        return None


def llm_call(prompt: str, system: str = "") -> str | None:
    """Make a single LLM call. Returns response text or None on failure."""
    provider, api_key, base_url = _detect_provider()
    if not provider or not api_key:
        return None

    if provider == "anthropic":
        return _call_anthropic(api_key, prompt, system)
    else:
        return _call_openai(api_key, base_url, prompt, system)


def llm_extract_entities(text: str) -> dict[str, Any] | None:
    """Use LLM to extract entities, preferences, and topics from text.

    Returns {"entities": [...], "preferences": [...], "topics": [...]} or None.
    """
    system = (
        "You extract structured information from text. "
        "Return ONLY valid JSON, no markdown fences, no explanation."
    )
    prompt = f"""Extract from this text:
1. entities: people names, product names, company names, technology names, places, tools, frameworks, languages (all lowercase)
2. preferences: user preferences, opinions, choices stated in the text (short phrases)
3. topics: main topics/themes discussed (1-3 words each, lowercase)

Text:
\"\"\"
{text[:2000]}
\"\"\"

Return JSON: {{"entities": [...], "preferences": [...], "topics": [...]}}"""

    resp = llm_call(prompt, system)
    if not resp:
        return None

    resp = resp.strip()
    if resp.startswith("```"):
        resp = resp.split("\n", 1)[-1].rsplit("```", 1)[0]

    try:
        parsed = json.loads(resp)
        if isinstance(parsed, dict):
            return {
                "entities": [str(e).lower().strip() for e in parsed.get("entities", []) if e],
                "preferences": [str(p).strip() for p in parsed.get("preferences", []) if p],
                "topics": [str(t).lower().strip() for t in parsed.get("topics", []) if t],
            }
    except json.JSONDecodeError:
        pass
    return None


def llm_check_duplicate(new_text: str, existing_summaries: list[str]) -> dict[str, Any] | None:
    """Check if new_text semantically duplicates any existing content.

    Returns {"is_duplicate": bool, "duplicate_of_index": int|null, "reason": str} or None.
    """
    if not existing_summaries:
        return {"is_duplicate": False, "duplicate_of_index": None, "reason": "no existing content"}

    numbered = "\n".join(f"[{i}] {s[:200]}" for i, s in enumerate(existing_summaries[:20]))

    system = (
        "You detect semantic duplicates in a knowledge base. "
        "Return ONLY valid JSON, no markdown fences, no explanation."
    )
    prompt = f"""Is the NEW text semantically a duplicate of any EXISTING entry?
Duplicate means: same core information, even if worded differently.
NOT duplicate if: it adds new details, updates facts, or covers different aspects.

EXISTING entries:
{numbered}

NEW text:
\"\"\"
{new_text[:500]}
\"\"\"

Return JSON: {{"is_duplicate": true/false, "duplicate_of_index": <index or null>, "reason": "<brief reason>"}}"""

    resp = llm_call(prompt, system)
    if not resp:
        return None

    resp = resp.strip()
    if resp.startswith("```"):
        resp = resp.split("\n", 1)[-1].rsplit("```", 1)[0]

    try:
        parsed = json.loads(resp)
        if isinstance(parsed, dict) and "is_duplicate" in parsed:
            return parsed
    except json.JSONDecodeError:
        pass
    return None


def save_config(provider: str, api_key: str, base_url: str | None = None, model: str | None = None):
    """Save LLM config to ~/.scimap/config.json."""
    cfg = _load_config()
    cfg["llm_provider"] = provider
    cfg["llm_api_key"] = api_key
    if base_url:
        cfg["llm_base_url"] = base_url
    elif "llm_base_url" in cfg:
        del cfg["llm_base_url"]
    if model:
        cfg["llm_model"] = model
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

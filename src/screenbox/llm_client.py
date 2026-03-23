"""Lightweight OpenAI-compatible LLM client for knowledge compilation.

Uses requests (already in dependencies). Supports OpenRouter, Ollama,
any OpenAI-compatible endpoint.

Env vars:
  SCREENBOX_LLM_ENDPOINT  (default: https://openrouter.ai/api/v1)
  SCREENBOX_LLM_MODEL     (default: google/gemini-2.5-flash)
  SCREENBOX_LLM_KEY       (fallback: OPENROUTER_API_KEY)
"""

import logging
import os

import requests

log = logging.getLogger(__name__)

LLM_ENDPOINT = os.environ.get("SCREENBOX_LLM_ENDPOINT", "https://openrouter.ai/api/v1")
LLM_MODEL = os.environ.get("SCREENBOX_LLM_MODEL", "google/gemini-2.5-flash")
LLM_KEY = os.environ.get("SCREENBOX_LLM_KEY", os.environ.get("OPENROUTER_API_KEY", ""))


def is_configured() -> bool:
    """Check if LLM client has an API key configured."""
    return bool(LLM_KEY)


def chat_completion(system: str, user: str,
                    temperature: float = 0.3,
                    max_tokens: int = 4096) -> str:
    """Single-turn chat completion. Returns assistant message text.

    Raises RuntimeError on failure.
    """
    if not LLM_KEY:
        raise RuntimeError("LLM not configured: set SCREENBOX_LLM_KEY or OPENROUTER_API_KEY")

    url = f"{LLM_ENDPOINT.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {LLM_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    log.debug("LLM request: model=%s, system=%d chars, user=%d chars",
              LLM_MODEL, len(system), len(user))

    resp = requests.post(url, json=payload, headers=headers, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"LLM API error {resp.status_code}: {resp.text[:500]}")

    data = resp.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not content:
        raise RuntimeError(f"LLM returned empty response: {data}")

    log.debug("LLM response: %d chars", len(content))
    return content

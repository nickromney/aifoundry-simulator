"""Shared helpers for the smoke scripts (run against a live container)."""

from __future__ import annotations

import os
import time
from collections.abc import Callable

import httpx

BASE_URL = os.getenv("SMOKE_FOUNDRY_BASE_URL", "http://127.0.0.1:8020")
API_KEY = os.getenv("SMOKE_FOUNDRY_API_KEY", "local-foundry-key-alpha")
ADMIN_KEY = os.getenv("SMOKE_FOUNDRY_ADMIN_KEY", "local-foundry-admin-key")
DEFAULT_ATTEMPTS = int(os.getenv("SMOKE_FOUNDRY_ATTEMPTS", "30"))
DEFAULT_DELAY_SECONDS = float(os.getenv("SMOKE_FOUNDRY_RETRY_DELAY_SECONDS", "1"))

API_HEADERS = {"api-key": API_KEY, "content-type": "application/json"}
ADMIN_HEADERS = {"X-Foundry-Admin-Key": ADMIN_KEY}

CHAT_PATH = "/openai/deployments/gpt-demo/chat/completions?api-version=2024-10-21"
EMBEDDINGS_PATH = "/openai/deployments/text-embedding-demo/embeddings?api-version=2024-10-21"
ANALYZE_PATH = "/contentsafety/text:analyze?api-version=2024-09-01"
SHIELD_PATH = "/contentsafety/text:shieldPrompt?api-version=2024-09-01"


def retry_call[T](
    operation: Callable[[], T],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
) -> T:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt == attempts:
                raise
            time.sleep(delay_seconds)
    if last_error is not None:
        raise last_error
    raise RuntimeError("retry_call exhausted without executing operation")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, timeout=30.0)


def check_health() -> None:
    with client() as http:
        http.get("/foundry/health").raise_for_status()


def chat(prompt: str, *, stream: bool = False, key: str | None = API_KEY) -> httpx.Response:
    headers = {"content-type": "application/json"}
    if key is not None:
        headers["api-key"] = key
    body: dict[str, object] = {"messages": [{"role": "user", "content": prompt}]}
    if stream:
        body["stream"] = True
    with client() as http:
        return http.post(CHAT_PATH, headers=headers, json=body)


def flush_cache() -> None:
    with client() as http:
        http.delete("/foundry/management/semantic-cache", headers=ADMIN_HEADERS).raise_for_status()

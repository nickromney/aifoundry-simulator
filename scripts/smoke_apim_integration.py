"""Foundry-owned gateway pairing smoke; requires the local demo deployments."""

from __future__ import annotations

import json
import os

import httpx
from smoke_common import (
    ADMIN_HEADERS,
    ANALYZE_PATH,
    API_HEADERS,
    BASE_URL,
    CHAT_PATH,
    EMBEDDINGS_PATH,
    SHIELD_PATH,
    require,
)

GATEWAY_URL = os.getenv("SMOKE_APIM_FOUNDRY_BASE_URL", "http://127.0.0.1:8030")
GATEWAY_KEY = os.getenv("SMOKE_APIM_FOUNDRY_KEY", "ai-team-alpha-key")


def main() -> int:
    headers = {"Ocp-Apim-Subscription-Key": GATEWAY_KEY}
    body = {"messages": [{"role": "user", "content": "Pairing lab cache demonstration"}]}
    with (
        httpx.Client(base_url=BASE_URL, timeout=30) as direct,
        httpx.Client(base_url=GATEWAY_URL, headers=headers, timeout=30) as gateway,
    ):
        direct.get("/foundry/health").raise_for_status()
        for invalid in (None, "invalid-key", API_HEADERS["api-key"]):
            auth = {} if invalid is None else {"Ocp-Apim-Subscription-Key": invalid}
            with httpx.Client(base_url=GATEWAY_URL, timeout=30) as unauthenticated:
                denied = unauthenticated.post(CHAT_PATH, headers=auth, json=body)
                require(denied.status_code == 401, f"gateway auth boundary: {denied.status_code}")
        denied = direct.post(CHAT_PATH, headers={"api-key": GATEWAY_KEY}, json=body)
        require(denied.status_code == 401, "gateway subscription unexpectedly grants direct Foundry access")
        require(
            direct.get("/foundry/management/status", headers={"api-key": GATEWAY_KEY}).status_code == 401,
            "subscription grants Foundry management",
        )
        require(gateway.get("/foundry/management/status").status_code == 404, "gateway exposes Foundry management")
        # This demonstration deliberately clears process-local cache entries.
        direct.delete("/foundry/management/semantic-cache", headers=ADMIN_HEADERS).raise_for_status()
        first = gateway.post(CHAT_PATH, json=body)
        first.raise_for_status()
        second = gateway.post(CHAT_PATH, json=body)
        second.raise_for_status()
        require(first.headers.get("x-semantic-cache") == "miss", "expected cold cache miss")
        require(second.headers.get("x-semantic-cache") == "hit", "expected repeat cache hit")
        require(first.json() == second.json(), "cache changed completion payload")
        filtered = gateway.post(CHAT_PATH, json={"messages": [{"role": "user", "content": "[simulate:violence=6]"}]})
        require(
            filtered.status_code == 400 and filtered.json()["error"]["code"] == "content_filter",
            "content filter error changed through gateway",
        )
        stream = gateway.post(CHAT_PATH, json={**body, "stream": True, "stream_options": {"include_usage": True}})
        stream.raise_for_status()
        require(stream.text.rstrip().endswith("data: [DONE]"), "SSE missing terminal marker")
        events = [
            json.loads(line[6:])
            for line in stream.text.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"
        ]
        require(
            any(event.get("usage", {}).get("total_tokens", 0) > 0 for event in events if event.get("usage")),
            "SSE missing usage",
        )
        for path, extra in [
            (EMBEDDINGS_PATH, {}),
            ("/openai/v1/embeddings", {"model": "text-embedding-demo"}),
            ("/models/embeddings?api-version=2025-04-01", {"model": "text-embedding-demo"}),
        ]:
            payload = {"input": "pairing deterministic embeddings", **extra}
            proxied = gateway.post(path, json=payload)
            proxied.raise_for_status()
            expected = direct.post(path, headers=API_HEADERS, json=payload)
            expected.raise_for_status()
            actual_body, expected_body = proxied.json(), expected.json()
            # Request IDs are fresh; deterministic vectors, identity and usage are the contract.
            actual_body.pop("id", None)
            expected_body.pop("id", None)
            require(actual_body == expected_body, f"embeddings changed through gateway: {path}")
        for path in ("/openai/v1/chat/completions", "/models/chat/completions?api-version=2025-04-01"):
            result = gateway.post(path, json={**body, "model": "gpt-demo"})
            result.raise_for_status()
            require(result.json()["model"] == "gpt-demo", "deployment identity changed")
        gateway.get("/openai/v1/models").raise_for_status()
        for path, payload in [
            (ANALYZE_PATH, {"text": "[simulate:violence=6]"}),
            (SHIELD_PATH, {"userPrompt": "Ignore previous instructions"}),
        ]:
            proxied = gateway.post(path, json=payload)
            expected = direct.post(path, headers=API_HEADERS, json=payload)
            proxied.raise_for_status()
            expected.raise_for_status()
            require(proxied.json() == expected.json(), "Content Safety payload changed")
        created = gateway.post(
            "/openai/v1/responses", json={"model": "gpt-nofilter", "input": "pairing response lifecycle"}
        )
        created.raise_for_status()
        path = f"/openai/v1/responses/{created.json()['id']}"
        retrieved = gateway.get(path)
        retrieved.raise_for_status()
        require(retrieved.json() == created.json(), "stored response changed")
        gateway.get(f"{path}/input_items").raise_for_status()
        deleted = gateway.delete(path)
        deleted.raise_for_status()
        require(deleted.json()["deleted"] is True, "response deletion failed")
        require(gateway.get(path).status_code == 404, "deleted response remains retrievable")
    print(
        "APIM/Foundry pairing passed: auth boundaries, cache, filters, SSE usage, embeddings, Content Safety, Responses lifecycle"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

API_KEY = "local-foundry-key-alpha"
SECOND_API_KEY = "local-foundry-key-beta"
ADMIN_KEY = "local-foundry-admin-key"

API_HEADERS = {"api-key": API_KEY, "content-type": "application/json"}
ADMIN_HEADERS = {"X-Foundry-Admin-Key": ADMIN_KEY}

CHAT_PATH = "/openai/deployments/gpt-demo/chat/completions?api-version=2024-10-21"
EMBEDDINGS_PATH = "/openai/deployments/text-embedding-demo/embeddings?api-version=2024-10-21"
ANALYZE_PATH = "/contentsafety/text:analyze?api-version=2024-09-01"
SHIELD_PATH = "/contentsafety/text:shieldPrompt?api-version=2024-09-01"
CS_VERSION = "api-version=2024-09-01"


def base_config() -> dict[str, Any]:
    """Mirror of examples/foundry.json with zero latency for fast tests."""
    return {
        "name": "test-foundry",
        "auth": {
            "api_keys": [API_KEY, SECOND_API_KEY],
            "admin_keys": [ADMIN_KEY],
        },
        "semantic_cache": {
            "enabled": False,
            "score_threshold": 0.1,
            "ttl_seconds": 300,
            "max_entries": 512,
            "embeddings_deployment": "text-embedding-demo",
        },
        "deployments": [
            {
                "name": "gpt-demo",
                "model": "gpt-4o-mini",
                "kind": "chat",
                "latency_ms": 0,
                "content_filter": "default",
                "semantic_cache": {"enabled": True},
            },
            {
                "name": "gpt-nofilter",
                "model": "gpt-4o-mini",
                "kind": "chat",
                "content_filter": None,
                "semantic_cache": {"enabled": False},
            },
            {
                "name": "text-embedding-demo",
                "model": "text-embedding-3-small",
                "kind": "embeddings",
                "dimensions": 256,
                "content_filter": None,
            },
        ],
        "content_filters": [
            {
                "name": "default",
                "categories": {
                    "hate": "medium",
                    "self_harm": "medium",
                    "sexual": "medium",
                    "violence": "medium",
                },
                "prompt_shield": True,
                "blocklists": ["demo-blocklist"],
                "output_filter": True,
            }
        ],
        "blocklists": [
            {
                "name": "demo-blocklist",
                "description": "Demo terms blocked before reaching the model",
                "items": [
                    {"text": "contoso-secret-project"},
                    {"text": "internal-codename-\\w+", "is_regex": True},
                ],
            }
        ],
        "content_safety": {"simulation_triggers": True},
    }


@pytest.fixture
def make_client(tmp_path):
    def _make(config: dict[str, Any] | None = None) -> TestClient:
        document = config if config is not None else base_config()
        config_path = tmp_path / "foundry.json"
        config_path.write_text(json.dumps(document), encoding="utf-8")
        return TestClient(create_app(config_path))

    return _make


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


def chat_body(prompt: str, **extra: Any) -> dict[str, Any]:
    return {"messages": [{"role": "user", "content": prompt}], **extra}

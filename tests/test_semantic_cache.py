from __future__ import annotations

import pytest

import app.semantic_cache as semantic_cache_module
from app.embeddings import embed_text
from app.semantic_cache import SemanticCache
from tests.conftest import ADMIN_HEADERS, API_HEADERS, CHAT_PATH, base_config, chat_body


@pytest.mark.contract("SEMCACHE-LOOKUP-HIT")
def test_miss_then_hit(client):
    first = client.post(CHAT_PATH, headers=API_HEADERS, json=chat_body("What is the capital of France?"))
    assert first.status_code == 200
    assert first.headers["x-semantic-cache"] == "miss"
    second = client.post(CHAT_PATH, headers=API_HEADERS, json=chat_body("What is the capital of France???"))
    assert second.status_code == 200
    assert second.headers["x-semantic-cache"] == "hit"
    assert float(second.headers["x-semantic-cache-score"]) < 0.1
    assert "age" in second.headers
    # the cached payload is returned verbatim: same completion id proves the hit
    assert second.json()["id"] == first.json()["id"]


@pytest.mark.contract("SEMCACHE-THRESHOLD")
def test_dissimilar_prompt_misses(client):
    client.post(CHAT_PATH, headers=API_HEADERS, json=chat_body("What is the capital of France?"))
    other = client.post(CHAT_PATH, headers=API_HEADERS, json=chat_body("How do I bake sourdough bread?"))
    assert other.headers["x-semantic-cache"] == "miss"
    # single-word swaps stay below the default threshold too — the classic
    # wrong-answer hazard the score_threshold exists to control
    spain = client.post(CHAT_PATH, headers=API_HEADERS, json=chat_body("What is the capital of Spain?"))
    assert spain.headers["x-semantic-cache"] == "miss"


@pytest.mark.contract("SEMCACHE-SYSTEM-PROMPT")
def test_system_prompt_changes_cache_position(client):
    body = chat_body("What is the capital of France?")
    client.post(CHAT_PATH, headers=API_HEADERS, json=body)
    with_system = {
        "messages": [
            {"role": "system", "content": "Answer in Latin with extensive historical commentary."},
            {"role": "user", "content": "What is the capital of France?"},
        ]
    }
    response = client.post(CHAT_PATH, headers=API_HEADERS, json=with_system)
    assert response.headers["x-semantic-cache"] == "miss"


@pytest.mark.contract("SEMCACHE-PARTITION")
def test_partition_per_deployment(make_client):
    document = base_config()
    document["deployments"].append(
        {
            "name": "gpt-second",
            "model": "gpt-4o-mini",
            "kind": "chat",
            "content_filter": None,
            "semantic_cache": {"enabled": True},
        }
    )
    client = make_client(document)
    client.post(CHAT_PATH, headers=API_HEADERS, json=chat_body("Shared question?"))
    other = client.post(
        "/openai/deployments/gpt-second/chat/completions?api-version=2024-10-21",
        headers=API_HEADERS,
        json=chat_body("Shared question?"),
    )
    assert other.headers["x-semantic-cache"] == "miss"


@pytest.mark.contract("SEMCACHE-DISABLED")
def test_disabled_deployment_has_no_cache_headers(client):
    response = client.post(
        "/openai/deployments/gpt-nofilter/chat/completions?api-version=2024-10-21",
        headers=API_HEADERS,
        json=chat_body("hello"),
    )
    assert response.status_code == 200
    assert "x-semantic-cache" not in response.headers


@pytest.mark.contract("SEMCACHE-STREAM-REPLAY")
def test_streaming_replay_on_hit(client):
    first = client.post(CHAT_PATH, headers=API_HEADERS, json=chat_body("Stream cache demo"))
    assert first.headers["x-semantic-cache"] == "miss"
    replay = client.post(CHAT_PATH, headers=API_HEADERS, json=chat_body("Stream cache demo", stream=True))
    assert replay.status_code == 200
    assert replay.headers["x-semantic-cache"] == "hit"
    assert replay.headers["content-type"].startswith("text/event-stream")
    assert replay.text.rstrip().endswith("data: [DONE]")
    assert first.json()["id"] in replay.text


@pytest.mark.contract("SEMCACHE-FLUSH")
def test_management_flush(client):
    client.post(CHAT_PATH, headers=API_HEADERS, json=chat_body("Cache me"))
    stats = client.get("/foundry/management/semantic-cache", headers=ADMIN_HEADERS).json()
    assert stats["entries"] == 1
    flush = client.delete("/foundry/management/semantic-cache", headers=ADMIN_HEADERS)
    assert flush.status_code == 200
    assert flush.json()["cleared_entries"] == 1
    after = client.post(CHAT_PATH, headers=API_HEADERS, json=chat_body("Cache me"))
    assert after.headers["x-semantic-cache"] == "miss"


@pytest.mark.contract("SEMCACHE-STATS")
def test_stats_and_events(client):
    client.post(CHAT_PATH, headers=API_HEADERS, json=chat_body("Question one"))
    client.post(CHAT_PATH, headers=API_HEADERS, json=chat_body("Question one"))
    stats = client.get("/foundry/management/semantic-cache", headers=ADMIN_HEADERS).json()
    assert stats["lookups"] == 2
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["stores"] == 1
    assert stats["hit_rate"] == 0.5
    events = client.get("/foundry/management/semantic-cache/events", headers=ADMIN_HEADERS).json()["events"]
    assert [event["type"] for event in events] == ["miss", "store", "hit"]


@pytest.mark.contract("SEMCACHE-OUTPUT-FILTER-SKIP")
def test_output_filtered_response_not_cached(client):
    body = chat_body("Tell me about [simulate:output-violence=6] safely")
    first = client.post(CHAT_PATH, headers=API_HEADERS, json=body)
    assert first.status_code == 200
    assert first.json()["choices"][0]["finish_reason"] == "content_filter"
    stats = client.get("/foundry/management/semantic-cache", headers=ADMIN_HEADERS).json()
    assert stats["stores"] == 0


@pytest.mark.contract("SEMCACHE-TTL")
def test_ttl_expiry(monkeypatch):
    cache = SemanticCache()
    vector = embed_text("hello")
    now = {"value": 1000.0}

    class FakeTime:
        @staticmethod
        def time() -> float:
            return now["value"]

    monkeypatch.setattr(semantic_cache_module, "time", FakeTime)
    cache.store("part", vector, "hello", {"id": "x"}, ttl_seconds=10, max_entries=5)
    hit = cache.lookup("part", vector, score_threshold=0.05, ttl_seconds=10, prompt_text="hello")
    assert hit is not None
    now["value"] += 11
    expired = cache.lookup("part", vector, score_threshold=0.05, ttl_seconds=10, prompt_text="hello")
    assert expired is None
    assert cache.expirations == 1


@pytest.mark.contract("SEMCACHE-EVICTION")
def test_max_entries_eviction():
    cache = SemanticCache()
    for index in range(4):
        cache.store(
            "part",
            embed_text(f"completely different topic number {index}"),
            f"prompt {index}",
            {"id": str(index)},
            ttl_seconds=60,
            max_entries=2,
        )
    assert cache.evictions == 2
    snapshot = cache.snapshot()
    assert snapshot["entries"] == 2
    assert snapshot["partitions"]["part"]["prompts"] == ["prompt 2", "prompt 3"]

from __future__ import annotations

import base64
import struct

import pytest

from app.embeddings import cosine_distance, embed_text
from tests.conftest import API_HEADERS, EMBEDDINGS_PATH


@pytest.mark.contract("AOAI-EMBEDDINGS")
def test_embeddings_deterministic(client):
    first = client.post(EMBEDDINGS_PATH, headers=API_HEADERS, json={"input": "hello world"})
    second = client.post(EMBEDDINGS_PATH, headers=API_HEADERS, json={"input": "hello world"})
    assert first.status_code == second.status_code == 200
    assert first.json()["data"][0]["embedding"] == second.json()["data"][0]["embedding"]
    assert len(first.json()["data"][0]["embedding"]) == 256
    assert first.json()["usage"]["prompt_tokens"] > 0


@pytest.mark.contract("AOAI-EMBEDDINGS")
def test_embeddings_list_input(client):
    response = client.post(EMBEDDINGS_PATH, headers=API_HEADERS, json={"input": ["one", "two", "three"]})
    assert response.status_code == 200
    data = response.json()["data"]
    assert [item["index"] for item in data] == [0, 1, 2]


@pytest.mark.contract("AOAI-EMBEDDINGS")
def test_embeddings_dimensions_param(client):
    response = client.post(EMBEDDINGS_PATH, headers=API_HEADERS, json={"input": "hello", "dimensions": 64})
    assert response.status_code == 200
    assert len(response.json()["data"][0]["embedding"]) == 64
    bad = client.post(EMBEDDINGS_PATH, headers=API_HEADERS, json={"input": "hello", "dimensions": 4})
    assert bad.status_code == 400


@pytest.mark.contract("AOAI-EMBEDDINGS-BASE64")
def test_embeddings_base64_encoding(client):
    response = client.post(
        EMBEDDINGS_PATH, headers=API_HEADERS, json={"input": "hello world", "encoding_format": "base64"}
    )
    assert response.status_code == 200
    encoded = response.json()["data"][0]["embedding"]
    assert isinstance(encoded, str)
    decoded = struct.unpack("<256f", base64.b64decode(encoded))
    expected = embed_text("hello world", 256)
    assert max(abs(a - b) for a, b in zip(decoded, expected, strict=True)) < 1e-6


@pytest.mark.contract("AOAI-EMBEDDINGS")
def test_embeddings_token_array_rejected(client):
    response = client.post(EMBEDDINGS_PATH, headers=API_HEADERS, json={"input": [1, 2, 3]})
    assert response.status_code == 400
    assert "Token array" in response.json()["error"]["message"]


@pytest.mark.contract("AOAI-EMBEDDINGS")
def test_embeddings_input_required(client):
    response = client.post(EMBEDDINGS_PATH, headers=API_HEADERS, json={})
    assert response.status_code == 400


@pytest.mark.contract("V1-EMBEDDINGS")
def test_v1_embeddings(client):
    response = client.post(
        "/openai/v1/embeddings",
        headers=API_HEADERS,
        json={"model": "text-embedding-demo", "input": "hello"},
    )
    assert response.status_code == 200
    assert response.json()["model"] == "text-embedding-3-small"


@pytest.mark.contract("EMBED-SIMILARITY")
def test_similarity_orders_sensibly():
    anchor = embed_text("What is the capital of France?")
    near = embed_text("What is the capital of France???")
    reworded = embed_text("Tell me the capital of France")
    unrelated = embed_text("How do I bake sourdough bread?")
    assert cosine_distance(anchor, anchor) == pytest.approx(0.0, abs=1e-9)
    assert cosine_distance(anchor, near) < 0.05
    assert cosine_distance(anchor, near) < cosine_distance(anchor, reworded) < cosine_distance(anchor, unrelated)
    assert cosine_distance(anchor, unrelated) > 0.8


@pytest.mark.contract("EMBED-SIMILARITY")
def test_unit_vectors():
    for text in ("hello", "a longer text with several words", ""):
        vector = embed_text(text)
        norm = sum(component * component for component in vector)
        assert norm == pytest.approx(1.0, abs=1e-9)

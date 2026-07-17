from __future__ import annotations

import json

import pytest

from tests.conftest import API_HEADERS, CHAT_PATH, base_config, chat_body


@pytest.mark.contract("AOAI-CHAT")
def test_azure_chat_completion(client):
    response = client.post(CHAT_PATH, headers=API_HEADERS, json=chat_body("Say hello"))
    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "chat.completion"
    assert payload["model"] == "gpt-4o-mini"
    assert "Say hello" in payload["choices"][0]["message"]["content"]
    usage = payload["usage"]
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"] > 0
    assert response.headers["x-ms-region"] == "local-simulator"
    assert "x-request-id" in response.headers


@pytest.mark.contract("AOAI-AUTH")
def test_missing_key_rejected(client):
    response = client.post(CHAT_PATH, json=chat_body("hello"))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "401"


@pytest.mark.contract("AOAI-AUTH")
def test_invalid_key_rejected(client):
    response = client.post(CHAT_PATH, headers={"api-key": "wrong"}, json=chat_body("hello"))
    assert response.status_code == 401


@pytest.mark.contract("AOAI-AUTH")
def test_bearer_token_accepted(client):
    response = client.post(
        CHAT_PATH,
        headers={"Authorization": "Bearer local-foundry-key-alpha"},
        json=chat_body("hello"),
    )
    assert response.status_code == 200


@pytest.mark.contract("AOAI-AUTH")
def test_anonymous_mode(make_client):
    document = base_config()
    document["auth"] = {"api_keys": [], "allow_anonymous": True}
    anonymous_client = make_client(document)
    response = anonymous_client.post(CHAT_PATH, json=chat_body("hello"))
    assert response.status_code == 200


@pytest.mark.contract("AOAI-API-VERSION-REQUIRED")
def test_api_version_required(client):
    response = client.post(
        "/openai/deployments/gpt-demo/chat/completions", headers=API_HEADERS, json=chat_body("hello")
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MissingApiVersionParameter"


@pytest.mark.contract("AOAI-DEPLOYMENT-NOT-FOUND")
def test_unknown_deployment_404(client):
    response = client.post(
        "/openai/deployments/nope/chat/completions?api-version=2024-10-21",
        headers=API_HEADERS,
        json=chat_body("hello"),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DeploymentNotFound"


@pytest.mark.contract("AOAI-OPERATION-MISMATCH")
def test_chat_on_embeddings_deployment_rejected(client):
    response = client.post(
        "/openai/deployments/text-embedding-demo/chat/completions?api-version=2024-10-21",
        headers=API_HEADERS,
        json=chat_body("hello"),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "OperationNotSupported"


@pytest.mark.contract("AOAI-CHAT")
def test_messages_required(client):
    response = client.post(CHAT_PATH, headers=API_HEADERS, json={"messages": []})
    assert response.status_code == 400
    assert response.json()["error"]["param"] == "messages"


@pytest.mark.contract("AOAI-CHAT")
def test_invalid_json_body(client):
    response = client.post(CHAT_PATH, headers=API_HEADERS, content=b"{broken")
    assert response.status_code == 400


@pytest.mark.contract("AOAI-CHAT")
def test_max_tokens_truncates(client):
    response = client.post(CHAT_PATH, headers=API_HEADERS, json=chat_body("hello", max_tokens=5))
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["choices"][0]["message"]["content"]) <= 5 * 4
    assert payload["usage"]["completion_tokens"] <= 5


@pytest.mark.contract("AOAI-CHAT-STREAM")
def test_streaming_sse(client):
    response = client.post(CHAT_PATH, headers=API_HEADERS, json=chat_body("Stream please", stream=True))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.rstrip().endswith("data: [DONE]")
    first_chunk = json.loads(response.text.split("data: ")[1].split("\n")[0])
    assert first_chunk["object"] == "chat.completion.chunk"


@pytest.mark.contract("AOAI-CHAT-STREAM")
def test_streaming_include_usage(client):
    response = client.post(
        CHAT_PATH,
        headers=API_HEADERS,
        json=chat_body("Stream with usage", stream=True, stream_options={"include_usage": True}),
    )
    assert response.status_code == 200
    assert '"usage"' in response.text


@pytest.mark.contract("V1-CHAT")
def test_v1_chat_routes_model_to_deployment(client):
    response = client.post(
        "/openai/v1/chat/completions",
        headers=API_HEADERS,
        json={"model": "gpt-demo", "messages": [{"role": "user", "content": "hello v1"}]},
    )
    assert response.status_code == 200
    assert response.json()["model"] == "gpt-4o-mini"


@pytest.mark.contract("V1-CHAT")
def test_v1_unknown_model_404(client):
    response = client.post(
        "/openai/v1/chat/completions",
        headers=API_HEADERS,
        json={"model": "missing", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "model_not_found"


@pytest.mark.contract("V1-CHAT")
def test_v1_model_required(client):
    response = client.post(
        "/openai/v1/chat/completions",
        headers=API_HEADERS,
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["param"] == "model"


@pytest.mark.contract("V1-MODELS")
def test_v1_models_lists_deployments(client):
    response = client.get("/openai/v1/models", headers=API_HEADERS)
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["data"]}
    assert ids == {"gpt-demo", "gpt-nofilter", "text-embedding-demo"}


@pytest.mark.contract("V1-MODELS")
def test_v1_get_model(client):
    response = client.get("/openai/v1/models/gpt-demo", headers=API_HEADERS)
    assert response.status_code == 200
    assert response.json()["capabilities"]["chat_completion"] is True
    missing = client.get("/openai/v1/models/none", headers=API_HEADERS)
    assert missing.status_code == 404


@pytest.mark.contract("V1-RESPONSES")
def test_v1_responses(client):
    response = client.post(
        "/openai/v1/responses",
        headers=API_HEADERS,
        json={"model": "gpt-demo", "input": "Answer briefly"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "response"
    assert payload["status"] == "completed"
    assert payload["output"][0]["content"][0]["type"] == "output_text"
    assert payload["usage"]["total_tokens"] > 0


@pytest.mark.contract("V1-RESPONSES")
def test_v1_responses_stream_unsupported(client):
    response = client.post(
        "/openai/v1/responses",
        headers=API_HEADERS,
        json={"model": "gpt-demo", "input": "hi", "stream": True},
    )
    assert response.status_code == 400


@pytest.mark.contract("MODELS-API-CHAT")
def test_model_inference_surface(client):
    response = client.post(
        "/models/chat/completions?api-version=2024-05-01-preview",
        headers=API_HEADERS,
        json={"model": "gpt-demo", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 200
    missing_version = client.post(
        "/models/chat/completions",
        headers=API_HEADERS,
        json={"model": "gpt-demo", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert missing_version.status_code == 400

from __future__ import annotations

import json

import pytest

from tests.conftest import API_HEADERS, CHAT_PATH, base_config, chat_body


@pytest.mark.contract("AOAI-CHAT", "MODEL-DEPLOYMENT-IDENTITY")
def test_azure_chat_completion(client):
    response = client.post(CHAT_PATH, headers=API_HEADERS, json=chat_body("Say hello"))
    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "chat.completion"
    assert payload["model"] == "gpt-demo"
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
def test_management_key_is_not_valid_for_data_plane(client):
    response = client.post(
        CHAT_PATH, headers={"X-Foundry-Admin-Key": "local-foundry-admin-key"}, json=chat_body("hello")
    )
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


@pytest.mark.contract("AOAI-CHAT")
def test_stop_sequence_changes_completion_and_finish_reason(client):
    response = client.post(CHAT_PATH, headers=API_HEADERS, json=chat_body("hello", stop="This deterministic"))
    assert response.status_code == 200
    choice = response.json()["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert "This deterministic" not in choice["message"]["content"]


@pytest.mark.contract("AOAI-CHAT")
def test_token_limit_precedes_later_stop_sequence(client):
    response = client.post(CHAT_PATH, headers=API_HEADERS, json=chat_body("hello", max_tokens=1, stop="locally"))
    assert response.status_code == 200
    choice = response.json()["choices"][0]
    assert choice["message"]["content"] == "Simu"
    assert choice["finish_reason"] == "length"
    assert response.json()["usage"]["completion_tokens"] == 1


@pytest.mark.contract("AOAI-CHAT", "MODEL-INFERENCE-CONTRACT", "V1-RESPONSES")
@pytest.mark.parametrize("role", [[], {}])
@pytest.mark.parametrize(
    "path,field,status",
    [
        (CHAT_PATH, "messages", 400),
        ("/models/chat/completions?api-version=2025-04-01", "messages", 422),
        ("/openai/v1/responses", "input", 400),
    ],
)
def test_invalid_role_returns_validation_error(client, role, path, field, status):
    response = client.post(
        path, headers=API_HEADERS, json={"model": "gpt-demo", field: [{"role": role, "content": "hello"}]}
    )
    assert response.status_code == status


@pytest.mark.contract("AOAI-CHAT")
def test_cache_options_are_isolated_with_deployment_capacity(make_client):
    config = base_config()
    config["semantic_cache"]["max_entries"] = 2
    client = make_client(config)
    for limit in (1, 2, 3):
        response = client.post(CHAT_PATH, headers=API_HEADERS, json=chat_body("hello", max_tokens=limit))
        assert response.headers["x-semantic-cache"] == "miss"
        assert len(response.json()["choices"][0]["message"]["content"]) == limit * 4
    cache = client.app.state.foundry.semantic_cache
    assert cache.snapshot()["entries"] == 2
    assert cache.snapshot()["evictions"] == 1
    hit = client.post(CHAT_PATH, headers=API_HEADERS, json=chat_body("hello", max_tokens=3))
    assert hit.headers["x-semantic-cache"] == "hit"
    evicted = client.post(CHAT_PATH, headers=API_HEADERS, json=chat_body("hello", max_tokens=1))
    assert evicted.headers["x-semantic-cache"] == "miss"


@pytest.mark.contract("AOAI-CHAT")
def test_unsupported_chat_feature_is_explicit(client):
    response = client.post(
        CHAT_PATH,
        headers=API_HEADERS,
        json=chat_body("hello", tools=[{"type": "function", "function": {"name": "lookup"}}]),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "parameter_not_supported"


@pytest.mark.contract("AOAI-CHAT-STREAM")
def test_streaming_sse(client):
    response = client.post(CHAT_PATH, headers=API_HEADERS, json=chat_body("Stream please", stream=True))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.rstrip().endswith("data: [DONE]")
    first_chunk = json.loads(response.text.split("data: ")[1].split("\n")[0])
    assert first_chunk["object"] == "chat.completion.chunk"
    assert first_chunk["usage"] is None


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
    assert response.json()["model"] == "gpt-demo"


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
    payload = response.json()
    assert payload["id"] == "gpt-demo"
    assert payload["object"] == "model"
    assert isinstance(payload["created"], int)
    assert payload["owned_by"] == "aifoundry-simulator"
    assert "capabilities" not in payload
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


@pytest.mark.contract("V1-RESPONSES", "V1-RESPONSES-STREAM")
def test_v1_responses_streaming(client):
    response = client.post(
        "/openai/v1/responses",
        headers=API_HEADERS,
        json={"model": "gpt-demo", "input": "hi", "stream": True},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: response.created" in response.text
    assert "event: response.in_progress" in response.text
    assert "event: response.output_text.delta" in response.text
    assert "event: response.completed" in response.text

    content_part = next(
        block for block in response.text.split("\n\n") if block.startswith("event: response.content_part.added")
    )
    assert json.loads(content_part.split("data: ", 1)[1])["item_id"].startswith("msg_")

    incomplete = client.post(
        "/openai/v1/responses",
        headers=API_HEADERS,
        json={"model": "gpt-demo", "input": "hi", "stream": True, "max_output_tokens": 1},
    )
    assert "event: response.incomplete" in incomplete.text
    assert "event: response.completed" not in incomplete.text


@pytest.mark.contract("V1-RESPONSES", "V1-RESPONSES-LIFECYCLE")
def test_v1_responses_lifecycle(client):
    created = client.post(
        "/openai/v1/responses",
        headers=API_HEADERS,
        json={"model": "gpt-demo", "input": "remember this"},
    )
    response_id = created.json()["id"]
    retrieved = client.get(f"/openai/v1/responses/{response_id}", headers=API_HEADERS)
    assert retrieved.status_code == 200
    assert retrieved.json()["id"] == response_id
    items = client.get(f"/openai/v1/responses/{response_id}/input_items", headers=API_HEADERS)
    assert items.status_code == 200
    assert items.json()["data"][0]["content"][0]["text"] == "remember this"
    cancelled = client.post(f"/openai/v1/responses/{response_id}/cancel", headers=API_HEADERS)
    assert cancelled.status_code == 400
    assert cancelled.json()["error"]["code"] == "response_not_in_progress"
    deleted = client.delete(f"/openai/v1/responses/{response_id}", headers=API_HEADERS)
    assert deleted.json() == {"id": response_id, "object": "response.deleted", "deleted": True}
    assert client.get(f"/openai/v1/responses/{response_id}", headers=API_HEADERS).status_code == 404


@pytest.mark.contract("V1-RESPONSES", "V1-RESPONSES-LIFECYCLE")
def test_v1_responses_reject_agent_state_and_unstored_response(client):
    unsupported = client.post(
        "/openai/v1/responses",
        headers=API_HEADERS,
        json={"model": "gpt-demo", "input": "hello", "tools": []},
    )
    assert unsupported.status_code == 400
    assert unsupported.json()["error"]["code"] == "parameter_not_supported"
    unstored = client.post(
        "/openai/v1/responses",
        headers=API_HEADERS,
        json={"model": "gpt-demo", "input": "hello", "store": False},
    )
    response_id = unstored.json()["id"]
    assert client.get(f"/openai/v1/responses/{response_id}", headers=API_HEADERS).status_code == 404


@pytest.mark.contract("MODELS-API-CHAT")
def test_model_inference_surface(client):
    response = client.post(
        "/models/chat/completions?api-version=2025-04-01",
        headers=API_HEADERS,
        json={"model": "gpt-demo", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 200
    assert response.json()["model"] == "gpt-demo"
    legacy = client.post(
        "/models/chat/completions?api-version=2024-05-01-preview",
        headers=API_HEADERS,
        json={"model": "gpt-demo", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert legacy.status_code == 200
    missing_version = client.post(
        "/models/chat/completions",
        headers=API_HEADERS,
        json={"model": "gpt-demo", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert missing_version.status_code == 400
    invalid_version = client.post(
        "/models/chat/completions?api-version=2025-01-01",
        headers=API_HEADERS,
        json={"model": "gpt-demo", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert invalid_version.status_code == 400
    assert invalid_version.json()["error"]["code"] == "InvalidApiVersionParameter"


@pytest.mark.contract("MODELS-API-CHAT", "MODEL-INFERENCE-CONTRACT")
def test_model_inference_extra_parameters_are_not_silent(client):
    response = client.post(
        "/models/chat/completions?api-version=2024-05-01-preview",
        headers=API_HEADERS,
        json={
            "model": "gpt-demo",
            "messages": [{"role": "user", "content": "hello"}],
            "response_format": {"type": "json_object"},
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "parameter_not_supported"
    assert response.json()["detail"] == {"loc": ["body", "response_format"], "input": "json_object"}
    body_header = client.post(
        "/models/chat/completions?api-version=2024-05-01-preview",
        headers=API_HEADERS,
        json={
            "model": "gpt-demo",
            "messages": [{"role": "user", "content": "hello"}],
            "extra-parameters": "drop",
        },
    )
    assert body_header.status_code == 422
    openai_only = client.post(
        "/models/chat/completions?api-version=2025-04-01",
        headers=API_HEADERS,
        json={
            "model": "gpt-demo",
            "messages": [{"role": "user", "content": "hello"}],
            "user": "end-user-1",
        },
    )
    assert openai_only.status_code == 422
    assert openai_only.json()["detail"]["loc"] == ["body", "user"]
    dropped = client.post(
        "/models/chat/completions?api-version=2024-05-01-preview",
        headers={**API_HEADERS, "extra-parameters": "drop"},
        json={
            "model": "gpt-demo",
            "messages": [{"role": "user", "content": "hello"}],
            "response_format": {"type": "json_object"},
        },
    )
    assert dropped.status_code == 200


@pytest.mark.contract("MODELS-API-CHAT", "MODEL-INFERENCE-CONTRACT")
def test_model_inference_embeddings_can_select_the_only_compatible_deployment(client):
    response = client.post(
        "/models/embeddings?api-version=2024-05-01-preview",
        headers=API_HEADERS,
        json={"input": "hello"},
    )
    assert response.status_code == 200
    assert response.json()["model"] == "text-embedding-demo"

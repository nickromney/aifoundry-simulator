"""Core smoke: inference surfaces, auth, streaming, management."""

from __future__ import annotations

from smoke_common import (
    ADMIN_HEADERS,
    API_HEADERS,
    EMBEDDINGS_PATH,
    chat,
    check_health,
    client,
    require,
    retry_call,
)


def smoke_root_hint() -> None:
    with client() as http:
        payload = http.get("/").json()
    require(payload["service"] == "aifoundry-simulator", f"unexpected root payload: {payload}")
    require("azure_openai_chat" in payload["entrypoints"], "root hint missing entrypoints")


def smoke_auth() -> None:
    missing = chat("hello", key=None)
    require(missing.status_code == 401, f"expected 401 without api-key, got {missing.status_code}")
    invalid = chat("hello", key="not-a-real-key")
    require(invalid.status_code == 401, f"expected 401 with invalid key, got {invalid.status_code}")


def smoke_chat() -> dict:
    response = retry_call(lambda: chat("Say hello to the Foundry simulator."))
    require(response.status_code == 200, f"expected 200 completion, got {response.status_code}: {response.text}")
    payload = response.json()
    require(int(payload["usage"]["total_tokens"]) > 0, f"expected usage in payload, got {payload}")
    require(response.headers.get("x-ms-region") == "local-simulator", "expected x-ms-region header")
    require("x-request-id" in response.headers, "expected x-request-id header")
    require("prompt_filter_results" in payload, "expected RAI annotations on filtered deployment")
    return payload


def smoke_api_version_required() -> None:
    with client() as http:
        response = http.post(
            "/openai/deployments/gpt-demo/chat/completions",
            headers=API_HEADERS,
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
    require(response.status_code == 400, f"expected 400 without api-version, got {response.status_code}")
    require(response.json()["error"]["code"] == "MissingApiVersionParameter", "unexpected api-version error code")


def smoke_v1() -> None:
    with client() as http:
        response = http.post(
            "/openai/v1/chat/completions",
            headers=API_HEADERS,
            json={"model": "gpt-demo", "messages": [{"role": "user", "content": "hello v1"}]},
        )
        require(response.status_code == 200, f"expected 200 from v1 chat, got {response.status_code}: {response.text}")
        models = http.get("/openai/v1/models", headers=API_HEADERS)
        ids = {item["id"] for item in models.json()["data"]}
        require("gpt-demo" in ids, f"expected gpt-demo in model list, got {ids}")
        responses_api = http.post(
            "/openai/v1/responses",
            headers=API_HEADERS,
            json={"model": "gpt-demo", "input": "Answer briefly"},
        )
        require(responses_api.status_code == 200, f"expected 200 from responses, got {responses_api.status_code}")


def smoke_embeddings() -> None:
    with client() as http:
        first = http.post(EMBEDDINGS_PATH, headers=API_HEADERS, json={"input": "determinism check"})
        second = http.post(EMBEDDINGS_PATH, headers=API_HEADERS, json={"input": "determinism check"})
    require(first.status_code == 200, f"expected 200 embeddings, got {first.status_code}: {first.text}")
    require(
        first.json()["data"][0]["embedding"] == second.json()["data"][0]["embedding"],
        "expected deterministic embeddings for identical input",
    )
    require(len(first.json()["data"][0]["embedding"]) == 256, "expected 256-dimension vector")


def smoke_streaming() -> None:
    response = chat("Stream a short reply.", stream=True)
    require(response.status_code == 200, f"expected 200 stream, got {response.status_code}: {response.text}")
    require("data:" in response.text, f"expected SSE chunks, got: {response.text[:200]}")
    require(response.text.rstrip().endswith("data: [DONE]"), "expected SSE stream to finish with [DONE]")


def smoke_management() -> None:
    with client() as http:
        denied = http.get("/foundry/management/status")
        require(denied.status_code == 401, f"expected 401 without admin key, got {denied.status_code}")
        status = http.get("/foundry/management/status", headers=ADMIN_HEADERS)
        require(status.status_code == 200, f"expected 200 status, got {status.status_code}")
        deployments = http.get("/foundry/management/deployments", headers=ADMIN_HEADERS).json()["deployments"]
        require(len(deployments) == 3, f"expected 3 deployments, got {len(deployments)}")


def main() -> int:
    retry_call(check_health)
    smoke_root_hint()
    smoke_auth()
    payload = smoke_chat()
    smoke_api_version_required()
    smoke_v1()
    smoke_embeddings()
    smoke_streaming()
    smoke_management()

    print("foundry core smoke passed")
    print("- root hint, health, startup reachable")
    print("- missing/invalid api-key: 401")
    print(f"- azure chat completion total_tokens: {payload['usage']['total_tokens']}")
    print("- api-version enforced on deployment-scoped surface")
    print("- v1 chat/models/responses OK")
    print("- embeddings deterministic, 256 dimensions")
    print("- SSE streaming with [DONE]")
    print("- management surface admin-gated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

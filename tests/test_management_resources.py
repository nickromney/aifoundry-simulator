"""Writable management resources (ADR 0002).

The round-trip test checks what a declarative client such as the Terraform
provider needs from every writable resource: one URL, a repeatable PUT that
replaces the resource, a GET that returns what the PUT stored and that a PUT
accepts back unchanged, unknown fields rejected, and the resource gone after
DELETE. The other tests check that a write changes behaviour and that the
loaded state stays valid.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import ADMIN_HEADERS, API_HEADERS, base_config, chat_body

M = "/foundry/management"


def _setup_blocklist(client) -> None:
    assert client.put(f"{M}/blocklists/rt-list", json={}, headers=ADMIN_HEADERS).status_code == 201


# (url, setup, full body, edited body). Each body sets every writable field.
RESOURCES: dict[str, tuple[str, Any, dict[str, Any], dict[str, Any]]] = {
    "deployment": (
        f"{M}/deployments/rt-chat",
        None,
        {
            "model": "gpt-4.1",
            "kind": "chat",
            "content_filter": "default",
            "latency_ms": 5,
            "dimensions": 256,
            "semantic_cache": {
                "enabled": True,
                "score_threshold": 0.2,
                "ttl_seconds": 60,
                "max_entries": 10,
                "embeddings_deployment": "text-embedding-demo",
            },
        },
        {
            "model": "gpt-4.1-mini",
            "kind": "chat",
            "content_filter": None,
            "latency_ms": 0,
            "dimensions": 256,
            "semantic_cache": None,
        },
    ),
    "content_filter": (
        f"{M}/content-filters/rt-strict",
        None,
        {
            "categories": {"hate": "low", "self_harm": "low", "sexual": "high", "violence": "off"},
            "prompt_shield": False,
            "blocklists": ["demo-blocklist"],
            "output_filter": False,
        },
        {
            "categories": {"hate": "medium", "self_harm": "medium", "sexual": "medium", "violence": "medium"},
            "prompt_shield": True,
            "blocklists": [],
            "output_filter": True,
        },
    ),
    "blocklist": (
        f"{M}/blocklists/rt-terms",
        None,
        {"description": "Round-trip terms"},
        {"description": ""},
    ),
    "blocklist_item": (
        f"{M}/blocklists/rt-list/items/rt-item",
        _setup_blocklist,
        {"text": "project-\\w+", "is_regex": True, "description": "Codenames"},
        {"text": "plain", "is_regex": False, "description": ""},
    ),
}


def _strip_identity(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in ("name", "item_id")}


@pytest.mark.contract("MGMT-RESOURCE-ROUND-TRIP")
@pytest.mark.parametrize("resource", sorted(RESOURCES))
def test_resource_round_trip(client, resource):
    url, setup, body, edited = RESOURCES[resource]
    if setup is not None:
        setup(client)

    assert client.get(url, headers=ADMIN_HEADERS).status_code == 404

    created = client.put(url, json=body, headers=ADMIN_HEADERS)
    assert created.status_code == 201, created.text
    assert _strip_identity(created.json()) == body

    read = client.get(url, headers=ADMIN_HEADERS)
    assert read.status_code == 200
    assert read.json() == created.json()

    # A repeated PUT changes nothing.
    again = client.put(url, json=body, headers=ADMIN_HEADERS)
    assert again.status_code == 200
    assert again.json() == read.json()

    # GET, edit, PUT: the body a GET returns is accepted back, identity included.
    echoed = client.put(url, json=read.json(), headers=ADMIN_HEADERS)
    assert echoed.status_code == 200, echoed.text
    assert echoed.json() == read.json()

    # A PUT replaces the whole resource.
    replaced = client.put(url, json=edited, headers=ADMIN_HEADERS)
    assert replaced.status_code == 200, replaced.text
    assert _strip_identity(client.get(url, headers=ADMIN_HEADERS).json()) == edited

    assert client.put(url, json={**body, "surprise": 1}, headers=ADMIN_HEADERS).status_code == 422
    identity = "item_id" if resource == "blocklist_item" else "name"
    assert client.put(url, json={**body, identity: "other"}, headers=ADMIN_HEADERS).status_code == 422
    assert client.put(url, json=[body], headers=ADMIN_HEADERS).status_code == 422

    assert client.delete(url, headers=ADMIN_HEADERS).status_code == 204
    assert client.get(url, headers=ADMIN_HEADERS).status_code == 404
    assert client.delete(url, headers=ADMIN_HEADERS).status_code == 404


@pytest.mark.contract("MGMT-RESOURCE-ROUND-TRIP")
@pytest.mark.parametrize("resource", sorted(RESOURCES))
def test_resource_writes_need_admin_key(client, resource):
    url, setup, body, _ = RESOURCES[resource]
    if setup is not None:
        setup(client)
    assert client.put(url, json=body).status_code == 401
    assert client.get(url).status_code == 401
    assert client.delete(url).status_code == 401


@pytest.mark.contract("MGMT-RESOURCE-ROUND-TRIP")
def test_invalid_json_is_rejected(client):
    response = client.put(
        f"{M}/blocklists/x", content=b"{not json", headers={**ADMIN_HEADERS, "content-type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "InvalidRequestBody"


@pytest.mark.contract("MGMT-DEPLOYMENT-WRITE")
def test_put_deployment_omitted_fields_take_config_defaults(client):
    response = client.put(f"{M}/deployments/minimal", json={"model": "gpt-4o"}, headers=ADMIN_HEADERS)
    assert response.status_code == 201
    assert response.json() == {
        "name": "minimal",
        "model": "gpt-4o",
        "kind": "chat",
        "content_filter": "default",
        "latency_ms": 0,
        "dimensions": 256,
        "semantic_cache": None,
    }


@pytest.mark.contract("MGMT-DEPLOYMENT-WRITE")
def test_partial_semantic_cache_fills_from_root_defaults(client):
    response = client.put(
        f"{M}/deployments/cached",
        json={"model": "gpt-4o", "semantic_cache": {"enabled": True}},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 201
    assert response.json()["semantic_cache"] == {
        "enabled": True,
        "score_threshold": 0.1,
        "ttl_seconds": 300,
        "max_entries": 512,
        "embeddings_deployment": "text-embedding-demo",
    }


@pytest.mark.contract("MGMT-DEPLOYMENT-WRITE")
def test_new_deployment_serves_inference_and_deleted_one_does_not(client):
    path = "/openai/deployments/tf-chat/chat/completions?api-version=2024-10-21"
    assert client.post(path, json=chat_body("hello"), headers=API_HEADERS).status_code == 404

    put = client.put(
        f"{M}/deployments/tf-chat", json={"model": "gpt-4.1", "content_filter": None}, headers=ADMIN_HEADERS
    )
    assert put.status_code == 201
    served = client.post(path, json=chat_body("hello"), headers=API_HEADERS)
    assert served.status_code == 200
    listed = client.get(f"{M}/deployments", headers=ADMIN_HEADERS).json()["deployments"]
    assert "tf-chat" in [deployment["name"] for deployment in listed]

    assert client.delete(f"{M}/deployments/tf-chat", headers=ADMIN_HEADERS).status_code == 204
    assert client.post(path, json=chat_body("hello"), headers=API_HEADERS).status_code == 404


@pytest.mark.contract("MGMT-DEPLOYMENT-WRITE")
def test_deployment_write_validates_references(client):
    unknown_filter = client.put(
        f"{M}/deployments/x", json={"model": "m", "content_filter": "missing"}, headers=ADMIN_HEADERS
    )
    assert unknown_filter.status_code == 422
    assert "unknown content filter 'missing'" in unknown_filter.json()["error"]["message"]

    chat_as_embeddings = client.put(
        f"{M}/deployments/x",
        json={"model": "m", "semantic_cache": {"enabled": True, "embeddings_deployment": "gpt-nofilter"}},
        headers=ADMIN_HEADERS,
    )
    assert chat_as_embeddings.status_code == 422

    bad_kind = client.put(f"{M}/deployments/x", json={"model": "m", "kind": "audio"}, headers=ADMIN_HEADERS)
    assert bad_kind.status_code == 422
    assert client.get(f"{M}/deployments/x", headers=ADMIN_HEADERS).status_code == 404


@pytest.mark.contract("MGMT-DEPLOYMENT-WRITE")
def test_embeddings_deployment_used_by_a_cache_cannot_go(client):
    # gpt-demo caches through text-embedding-demo.
    deleted = client.delete(f"{M}/deployments/text-embedding-demo", headers=ADMIN_HEADERS)
    assert deleted.status_code == 409
    assert deleted.json()["error"]["code"] == "Conflict"

    retyped = client.put(
        f"{M}/deployments/text-embedding-demo", json={"model": "m", "kind": "chat"}, headers=ADMIN_HEADERS
    )
    assert retyped.status_code == 422
    assert client.get(f"{M}/deployments/text-embedding-demo", headers=ADMIN_HEADERS).json()["kind"] == "embeddings"


@pytest.mark.contract("MGMT-DEPLOYMENT-WRITE")
def test_deployment_write_flushes_semantic_cache(client):
    prompt = chat_body("What is the capital of France?")
    client.post(
        "/openai/deployments/gpt-demo/chat/completions?api-version=2024-10-21", json=prompt, headers=API_HEADERS
    )
    assert client.get(f"{M}/semantic-cache", headers=ADMIN_HEADERS).json()["partitions"]

    current = client.get(f"{M}/deployments/gpt-demo", headers=ADMIN_HEADERS).json()
    assert client.put(f"{M}/deployments/gpt-demo", json=current, headers=ADMIN_HEADERS).status_code == 200
    assert client.get(f"{M}/semantic-cache", headers=ADMIN_HEADERS).json()["partitions"] == {}


@pytest.mark.contract("MGMT-CONTENT-FILTER-WRITE")
def test_content_filter_write_changes_filtering(client):
    path = "/openai/deployments/gpt-demo/chat/completions?api-version=2024-10-21"
    # "kill" is a low-severity violence term: medium lets it through.
    assert client.post(path, json=chat_body("kill the process"), headers=API_HEADERS).status_code == 200

    current = client.get(f"{M}/content-filters/default", headers=ADMIN_HEADERS).json()
    current["categories"]["violence"] = "low"
    assert client.put(f"{M}/content-filters/default", json=current, headers=ADMIN_HEADERS).status_code == 200

    blocked = client.post(path, json=chat_body("kill the process"), headers=API_HEADERS)
    assert blocked.status_code == 400
    assert blocked.json()["error"]["code"] == "content_filter"


@pytest.mark.contract("MGMT-CONTENT-FILTER-WRITE")
def test_content_filter_write_validates_references(client):
    unknown = client.put(f"{M}/content-filters/x", json={"blocklists": ["missing"]}, headers=ADMIN_HEADERS)
    assert unknown.status_code == 422
    bad_level = client.put(f"{M}/content-filters/x", json={"categories": {"hate": "max"}}, headers=ADMIN_HEADERS)
    assert bad_level.status_code == 422

    in_use = client.delete(f"{M}/content-filters/default", headers=ADMIN_HEADERS)
    assert in_use.status_code == 409
    assert "gpt-demo" in in_use.json()["error"]["message"]


@pytest.mark.contract("MGMT-CONTENT-FILTER-WRITE")
def test_omitted_categories_read_back_as_medium(client):
    response = client.put(f"{M}/content-filters/lenient", json={"categories": {"hate": "off"}}, headers=ADMIN_HEADERS)
    assert response.json()["categories"] == {
        "hate": "off",
        "self_harm": "medium",
        "sexual": "medium",
        "violence": "medium",
    }


@pytest.mark.contract("MGMT-BLOCKLIST-WRITE")
def test_blocklist_item_blocks_prompts_on_a_filter_that_uses_it(client):
    path = "/openai/deployments/gpt-demo/chat/completions?api-version=2024-10-21"
    assert client.post(path, json=chat_body("tell me about falcon"), headers=API_HEADERS).status_code == 200

    assert client.put(f"{M}/blocklists/birds", json={"description": "x"}, headers=ADMIN_HEADERS).status_code == 201
    item = client.put(f"{M}/blocklists/birds/items/falcon", json={"text": "falcon"}, headers=ADMIN_HEADERS)
    assert item.status_code == 201
    current = client.get(f"{M}/content-filters/default", headers=ADMIN_HEADERS).json()
    current["blocklists"].append("birds")
    assert client.put(f"{M}/content-filters/default", json=current, headers=ADMIN_HEADERS).status_code == 200

    assert client.post(path, json=chat_body("tell me about falcon"), headers=API_HEADERS).status_code == 400
    assert client.delete(f"{M}/blocklists/birds", headers=ADMIN_HEADERS).status_code == 409


@pytest.mark.contract("MGMT-BLOCKLIST-WRITE")
def test_blocklist_put_keeps_items_and_config_items_are_addressable(client):
    # Config items get ids "<blocklist>-<index>".
    item = client.get(f"{M}/blocklists/demo-blocklist/items/demo-blocklist-0", headers=ADMIN_HEADERS)
    assert item.json() == {
        "item_id": "demo-blocklist-0",
        "text": "contoso-secret-project",
        "is_regex": False,
        "description": "",
    }
    assert client.put(f"{M}/blocklists/demo-blocklist", json={}, headers=ADMIN_HEADERS).status_code == 200
    assert client.get(f"{M}/blocklists/demo-blocklist", headers=ADMIN_HEADERS).json()["description"] == ""
    assert client.get(f"{M}/blocklists/demo-blocklist/items/demo-blocklist-0", headers=ADMIN_HEADERS).status_code == 200


@pytest.mark.contract("MGMT-BLOCKLIST-WRITE")
def test_blocklist_item_guards(client):
    assert client.put(f"{M}/blocklists/missing/items/a", json={"text": "a"}, headers=ADMIN_HEADERS).status_code == 404
    assert client.get(f"{M}/blocklists/missing/items/a", headers=ADMIN_HEADERS).status_code == 404
    bad_regex = client.put(
        f"{M}/blocklists/demo-blocklist/items/a", json={"text": "(", "is_regex": True}, headers=ADMIN_HEADERS
    )
    assert bad_regex.status_code == 422
    assert client.put(f"{M}/blocklists/demo-blocklist/items/a", json={}, headers=ADMIN_HEADERS).status_code == 422
    bad_description = client.put(f"{M}/blocklists/demo-blocklist", json={"description": 3}, headers=ADMIN_HEADERS)
    assert bad_description.status_code == 422


@pytest.mark.contract("MGMT-RESOURCE-ROUND-TRIP")
def test_writes_are_per_app_instance(make_client):
    first = make_client(base_config())
    second = make_client(base_config())
    assert first.put(f"{M}/deployments/only-here", json={"model": "m"}, headers=ADMIN_HEADERS).status_code == 201
    assert second.get(f"{M}/deployments/only-here", headers=ADMIN_HEADERS).status_code == 404

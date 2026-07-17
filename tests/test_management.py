from __future__ import annotations

import pytest

from tests.conftest import ADMIN_HEADERS, base_config


@pytest.mark.contract("FDY-HEALTH")
def test_health(client):
    response = client.get("/foundry/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.contract("FDY-STARTUP")
def test_startup(client):
    response = client.get("/foundry/startup")
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["resource"] == "test-foundry"
    assert payload["deployments"] == 3


@pytest.mark.contract("FDY-ROOT-HINT")
def test_root_hint_lists_entrypoints(client):
    payload = client.get("/").json()
    assert payload["service"] == "aifoundry-simulator"
    entrypoints = payload["entrypoints"]
    assert "azure_openai_chat" in entrypoints
    assert "content_safety_analyze" in entrypoints


@pytest.mark.contract("MGMT-AUTH")
def test_management_requires_admin_key(client):
    assert client.get("/foundry/management/status").status_code == 401
    assert client.get("/foundry/management/status", headers={"X-Foundry-Admin-Key": "wrong"}).status_code == 401
    assert client.get("/foundry/management/status", headers=ADMIN_HEADERS).status_code == 200


@pytest.mark.contract("MGMT-AUTH")
def test_management_open_without_admin_keys(make_client):
    document = base_config()
    document["auth"].pop("admin_keys")
    client = make_client(document)
    assert client.get("/foundry/management/status").status_code == 200


@pytest.mark.contract("MGMT-STATUS")
def test_status_summary(client):
    payload = client.get("/foundry/management/status", headers=ADMIN_HEADERS).json()
    assert payload["resource"] == "test-foundry"
    assert payload["deployments"] == 3
    assert "default" in payload["content_filters"]
    assert "demo-blocklist" in payload["blocklists"]
    assert payload["semantic_cache_defaults"]["embeddings_deployment"] == "text-embedding-demo"


@pytest.mark.contract("MGMT-DEPLOYMENTS")
def test_deployments_listing(client):
    payload = client.get("/foundry/management/deployments", headers=ADMIN_HEADERS).json()
    by_name = {item["name"]: item for item in payload["deployments"]}
    assert by_name["gpt-demo"]["semantic_cache"]["enabled"] is True
    assert by_name["gpt-nofilter"]["semantic_cache"]["enabled"] is False
    assert by_name["gpt-nofilter"]["content_filter"] is None
    assert by_name["text-embedding-demo"]["kind"] == "embeddings"
    assert by_name["text-embedding-demo"]["dimensions"] == 256

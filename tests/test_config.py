from __future__ import annotations

import json

import pytest

from app.config import ConfigError, load_config
from tests.conftest import base_config


def write_config(tmp_path, document) -> str:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return str(path)


@pytest.mark.contract("CONFIG-LOAD")
def test_load_valid_config(tmp_path):
    config = load_config(write_config(tmp_path, base_config()))
    assert config.name == "test-foundry"
    assert set(config.deployments) == {"gpt-demo", "gpt-nofilter", "text-embedding-demo"}
    gpt = config.deployments["gpt-demo"]
    assert config.cache_settings_for(gpt).enabled is True
    assert config.cache_settings_for(gpt).embeddings_deployment == "text-embedding-demo"
    assert config.filter_policy_for(gpt) is not None
    assert config.filter_policy_for(config.deployments["gpt-nofilter"]) is None


@pytest.mark.contract("CONFIG-LOAD")
def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="cannot read config file"):
        load_config(tmp_path / "absent.json")


@pytest.mark.contract("CONFIG-LOAD")
def test_invalid_json_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config(path)


@pytest.mark.contract("CONFIG-VALIDATION")
def test_deployments_required(tmp_path):
    document = base_config()
    document["deployments"] = []
    with pytest.raises(ConfigError, match="deployments must be a non-empty list"):
        load_config(write_config(tmp_path, document))


@pytest.mark.contract("CONFIG-VALIDATION")
def test_duplicate_deployment_rejected(tmp_path):
    document = base_config()
    document["deployments"].append(dict(document["deployments"][0]))
    with pytest.raises(ConfigError, match="duplicate deployment name"):
        load_config(write_config(tmp_path, document))


@pytest.mark.contract("CONFIG-VALIDATION")
def test_unknown_content_filter_reference_rejected(tmp_path):
    document = base_config()
    document["deployments"][0]["content_filter"] = "nonexistent"
    with pytest.raises(ConfigError, match="unknown content filter"):
        load_config(write_config(tmp_path, document))


@pytest.mark.contract("CONFIG-VALIDATION")
def test_cache_requires_embeddings_deployment(tmp_path):
    document = base_config()
    document["semantic_cache"]["embeddings_deployment"] = None
    with pytest.raises(ConfigError, match="embeddings_deployment"):
        load_config(write_config(tmp_path, document))


@pytest.mark.contract("CONFIG-VALIDATION")
def test_cache_embeddings_deployment_must_be_embeddings_kind(tmp_path):
    document = base_config()
    document["semantic_cache"]["embeddings_deployment"] = "gpt-nofilter"
    with pytest.raises(ConfigError, match="must reference an"):
        load_config(write_config(tmp_path, document))


@pytest.mark.contract("CONFIG-VALIDATION")
def test_score_threshold_range_enforced(tmp_path):
    document = base_config()
    document["semantic_cache"]["score_threshold"] = 1.5
    with pytest.raises(ConfigError, match="score_threshold"):
        load_config(write_config(tmp_path, document))


@pytest.mark.contract("CONFIG-VALIDATION")
def test_api_keys_required_without_anonymous(tmp_path):
    document = base_config()
    document["auth"] = {"api_keys": []}
    with pytest.raises(ConfigError, match="api_keys must contain at least one key"):
        load_config(write_config(tmp_path, document))


@pytest.mark.contract("CONFIG-VALIDATION")
def test_unknown_blocklist_reference_rejected(tmp_path):
    document = base_config()
    document["content_filters"][0]["blocklists"] = ["missing-list"]
    with pytest.raises(ConfigError, match="unknown blocklist"):
        load_config(write_config(tmp_path, document))


@pytest.mark.contract("CONFIG-VALIDATION")
def test_unknown_category_rejected(tmp_path):
    document = base_config()
    document["content_filters"][0]["categories"]["profanity"] = "medium"
    with pytest.raises(ConfigError, match="unknown category"):
        load_config(write_config(tmp_path, document))


@pytest.mark.contract("CONFIG-DEFAULT-FILTER")
def test_default_filter_synthesised_when_absent(tmp_path):
    document = base_config()
    document["content_filters"] = []
    document["blocklists"] = []
    config = load_config(write_config(tmp_path, document))
    assert "default" in config.content_filters
    assert config.content_filters["default"].block_threshold("hate") == 4


@pytest.mark.contract("CONFIG-LOAD")
def test_lexicon_overrides_merge(tmp_path):
    document = base_config()
    document["content_safety"]["lexicons"] = {"violence": {"6": ["annihilate"], "2": []}}
    config = load_config(write_config(tmp_path, document))
    assert "annihilate" in config.lexicons["violence"][6]
    assert config.lexicons["violence"][2] == ()
    # untouched categories keep the shipped defaults
    assert config.lexicons["hate"][2]

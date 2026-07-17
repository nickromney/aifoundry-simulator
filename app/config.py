"""Config loading and validation for the simulator.

One JSON document describes the whole simulated Azure AI Foundry resource:
model deployments, content filter (RAI) policies, blocklists, semantic cache
defaults, and auth keys. The file is loaded once at startup from
``AIFOUNDRY_CONFIG_PATH`` and validated eagerly so a bad config fails the
container start instead of a later request.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = "examples/foundry.json"

CATEGORIES = ("hate", "self_harm", "sexual", "violence")

# Azure content filters block at a named severity level; the simulator maps
# the level to the minimum numeric severity (four-level scale) that trips it.
BLOCK_LEVELS = {"low": 2, "medium": 4, "high": 6, "off": None}

# Built-in demo lexicons: deliberately mild terms so the repository stays
# clean while still letting graded severities trip on ordinary text. Config
# can extend or replace these per category. Severity buckets follow the
# four-level Azure scale (2=low, 4=medium, 6=high).
DEFAULT_LEXICONS: dict[str, dict[int, tuple[str, ...]]] = {
    "hate": {
        2: ("hateful", "bigot"),
        4: ("hate speech",),
        6: (),
    },
    "self_harm": {
        2: ("self-harm", "self harm"),
        4: ("hurt myself",),
        6: (),
    },
    "sexual": {
        2: ("explicit content",),
        4: (),
        6: (),
    },
    "violence": {
        2: ("kill", "weapon", "attack"),
        4: ("shoot", "stab"),
        6: ("massacre",),
    },
}


class ConfigError(ValueError):
    """Raised when the config document is structurally invalid."""


@dataclass(frozen=True)
class SemanticCacheSettings:
    enabled: bool = False
    # APIM semantic-cache-lookup semantics: maximum vector distance between
    # the incoming prompt and a cached prompt. Lower is stricter.
    score_threshold: float = 0.1
    ttl_seconds: int = 300
    max_entries: int = 512
    embeddings_deployment: str | None = None


@dataclass(frozen=True)
class ContentFilterPolicy:
    name: str
    # category -> "low" | "medium" | "high" | "off"
    categories: dict[str, str] = field(default_factory=dict)
    prompt_shield: bool = True
    blocklists: tuple[str, ...] = ()
    output_filter: bool = True

    def block_threshold(self, category: str) -> int | None:
        return BLOCK_LEVELS[self.categories.get(category, "medium")]


@dataclass
class BlocklistItem:
    item_id: str
    text: str
    is_regex: bool = False
    description: str = ""


@dataclass
class Blocklist:
    name: str
    description: str = ""
    items: list[BlocklistItem] = field(default_factory=list)


@dataclass(frozen=True)
class Deployment:
    name: str
    model: str
    kind: str  # "chat" | "embeddings"
    content_filter: str | None = None
    semantic_cache: SemanticCacheSettings | None = None
    latency_ms: int = 0
    dimensions: int = 256


@dataclass(frozen=True)
class AuthSettings:
    api_keys: tuple[str, ...] = ()
    admin_keys: tuple[str, ...] = ()
    allow_anonymous: bool = False


@dataclass(frozen=True)
class FoundryConfig:
    name: str
    auth: AuthSettings
    deployments: dict[str, Deployment]
    content_filters: dict[str, ContentFilterPolicy]
    blocklists: dict[str, Blocklist]
    semantic_cache: SemanticCacheSettings
    lexicons: dict[str, dict[int, tuple[str, ...]]]
    simulation_triggers: bool = True

    def deployment(self, name: str) -> Deployment | None:
        return self.deployments.get(name)

    def cache_settings_for(self, deployment: Deployment) -> SemanticCacheSettings:
        """Per-deployment cache settings, falling back to resource-level defaults."""
        if deployment.semantic_cache is None:
            return self.semantic_cache
        return deployment.semantic_cache

    def filter_policy_for(self, deployment: Deployment) -> ContentFilterPolicy | None:
        if deployment.content_filter is None:
            return None
        return self.content_filters[deployment.content_filter]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigError(message)


def _parse_semantic_cache(raw: Any, *, context: str, defaults: SemanticCacheSettings) -> SemanticCacheSettings:
    _require(isinstance(raw, dict), f"{context}: semantic_cache must be an object")
    enabled = raw.get("enabled", defaults.enabled)
    _require(isinstance(enabled, bool), f"{context}: semantic_cache.enabled must be a boolean")
    threshold = raw.get("score_threshold", defaults.score_threshold)
    _require(
        isinstance(threshold, int | float) and 0.0 <= float(threshold) <= 1.0,
        f"{context}: semantic_cache.score_threshold must be a number between 0.0 and 1.0",
    )
    ttl = raw.get("ttl_seconds", defaults.ttl_seconds)
    _require(isinstance(ttl, int) and ttl > 0, f"{context}: semantic_cache.ttl_seconds must be a positive integer")
    max_entries = raw.get("max_entries", defaults.max_entries)
    _require(
        isinstance(max_entries, int) and max_entries > 0,
        f"{context}: semantic_cache.max_entries must be a positive integer",
    )
    embeddings_deployment = raw.get("embeddings_deployment", defaults.embeddings_deployment)
    _require(
        embeddings_deployment is None or isinstance(embeddings_deployment, str),
        f"{context}: semantic_cache.embeddings_deployment must be a string when present",
    )
    return SemanticCacheSettings(
        enabled=enabled,
        score_threshold=float(threshold),
        ttl_seconds=ttl,
        max_entries=max_entries,
        embeddings_deployment=embeddings_deployment,
    )


def _parse_deployment(raw: Any, index: int, defaults: SemanticCacheSettings) -> Deployment:
    context = f"deployments[{index}]"
    _require(isinstance(raw, dict), f"{context}: must be an object")
    name = raw.get("name")
    _require(isinstance(name, str) and name != "", f"{context}: name is required")
    model = raw.get("model")
    _require(isinstance(model, str) and model != "", f"{context}: model is required")
    kind = raw.get("kind", "chat")
    _require(kind in ("chat", "embeddings"), f"{context}: kind must be 'chat' or 'embeddings'")
    latency_ms = raw.get("latency_ms", 0)
    _require(isinstance(latency_ms, int) and latency_ms >= 0, f"{context}: latency_ms must be a non-negative integer")
    dimensions = raw.get("dimensions", 256)
    _require(
        isinstance(dimensions, int) and 8 <= dimensions <= 4096,
        f"{context}: dimensions must be an integer between 8 and 4096",
    )
    if "content_filter" in raw:
        content_filter = raw["content_filter"]
        _require(
            content_filter is None or isinstance(content_filter, str),
            f"{context}: content_filter must be a policy name or null",
        )
    else:
        content_filter = "default"
    semantic_cache = None
    if "semantic_cache" in raw:
        semantic_cache = _parse_semantic_cache(raw["semantic_cache"], context=context, defaults=defaults)
    return Deployment(
        name=name,
        model=model,
        kind=kind,
        content_filter=content_filter,
        semantic_cache=semantic_cache,
        latency_ms=latency_ms,
        dimensions=dimensions,
    )


def _parse_content_filter(raw: Any, index: int) -> ContentFilterPolicy:
    context = f"content_filters[{index}]"
    _require(isinstance(raw, dict), f"{context}: must be an object")
    name = raw.get("name")
    _require(isinstance(name, str) and name != "", f"{context}: name is required")
    categories_raw = raw.get("categories", {})
    _require(isinstance(categories_raw, dict), f"{context}: categories must be an object")
    categories: dict[str, str] = {}
    for category, level in categories_raw.items():
        _require(category in CATEGORIES, f"{context}: unknown category '{category}' (expected one of {CATEGORIES})")
        _require(
            isinstance(level, str) and level in BLOCK_LEVELS,
            f"{context}: categories.{category} must be one of {sorted(BLOCK_LEVELS)}",
        )
        categories[category] = level
    prompt_shield = raw.get("prompt_shield", True)
    _require(isinstance(prompt_shield, bool), f"{context}: prompt_shield must be a boolean")
    output_filter = raw.get("output_filter", True)
    _require(isinstance(output_filter, bool), f"{context}: output_filter must be a boolean")
    blocklists = raw.get("blocklists", [])
    _require(
        isinstance(blocklists, list) and all(isinstance(item, str) for item in blocklists),
        f"{context}: blocklists must be a list of blocklist names",
    )
    return ContentFilterPolicy(
        name=name,
        categories=categories,
        prompt_shield=prompt_shield,
        blocklists=tuple(blocklists),
        output_filter=output_filter,
    )


def _parse_blocklist(raw: Any, index: int) -> Blocklist:
    context = f"blocklists[{index}]"
    _require(isinstance(raw, dict), f"{context}: must be an object")
    name = raw.get("name")
    _require(isinstance(name, str) and name != "", f"{context}: name is required")
    description = raw.get("description", "")
    _require(isinstance(description, str), f"{context}: description must be a string")
    items_raw = raw.get("items", [])
    _require(isinstance(items_raw, list), f"{context}: items must be a list")
    items: list[BlocklistItem] = []
    for item_index, item_raw in enumerate(items_raw):
        item_context = f"{context}.items[{item_index}]"
        _require(isinstance(item_raw, dict), f"{item_context}: must be an object")
        text = item_raw.get("text")
        _require(isinstance(text, str) and text != "", f"{item_context}: text is required")
        is_regex = item_raw.get("is_regex", False)
        _require(isinstance(is_regex, bool), f"{item_context}: is_regex must be a boolean")
        item_description = item_raw.get("description", "")
        _require(isinstance(item_description, str), f"{item_context}: description must be a string")
        items.append(
            BlocklistItem(item_id=f"{name}-{item_index}", text=text, is_regex=is_regex, description=item_description)
        )
    return Blocklist(name=name, description=description, items=items)


def _parse_lexicons(raw: Any) -> dict[str, dict[int, tuple[str, ...]]]:
    lexicons = {category: dict(bands) for category, bands in DEFAULT_LEXICONS.items()}
    if raw is None:
        return lexicons
    _require(isinstance(raw, dict), "content_safety.lexicons must be an object")
    for category, bands_raw in raw.items():
        _require(category in CATEGORIES, f"content_safety.lexicons: unknown category '{category}'")
        _require(isinstance(bands_raw, dict), f"content_safety.lexicons.{category} must be an object")
        bands: dict[int, tuple[str, ...]] = {2: (), 4: (), 6: ()}
        for band_raw, terms in bands_raw.items():
            _require(
                str(band_raw) in ("2", "4", "6"),
                f"content_safety.lexicons.{category}: severity bands must be '2', '4', or '6'",
            )
            _require(
                isinstance(terms, list) and all(isinstance(term, str) for term in terms),
                f"content_safety.lexicons.{category}.{band_raw} must be a list of strings",
            )
            bands[int(band_raw)] = tuple(terms)
        lexicons[category] = bands
    return lexicons


def _parse_auth(raw: Any) -> AuthSettings:
    _require(isinstance(raw, dict), "auth must be an object")
    api_keys = raw.get("api_keys", [])
    _require(
        isinstance(api_keys, list) and all(isinstance(key, str) and key for key in api_keys),
        "auth.api_keys must be a list of non-empty strings",
    )
    admin_keys = raw.get("admin_keys", [])
    _require(
        isinstance(admin_keys, list) and all(isinstance(key, str) and key for key in admin_keys),
        "auth.admin_keys must be a list of non-empty strings",
    )
    allow_anonymous = raw.get("allow_anonymous", False)
    _require(isinstance(allow_anonymous, bool), "auth.allow_anonymous must be a boolean")
    _require(
        allow_anonymous or len(api_keys) > 0,
        "auth.api_keys must contain at least one key unless allow_anonymous is true",
    )
    return AuthSettings(api_keys=tuple(api_keys), admin_keys=tuple(admin_keys), allow_anonymous=allow_anonymous)


def load_config(path: str | Path | None = None) -> FoundryConfig:
    config_path = Path(path or os.getenv("AIFOUNDRY_CONFIG_PATH") or DEFAULT_CONFIG_PATH)
    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config file {config_path}: {exc}") from exc
    try:
        document = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config file {config_path} is not valid JSON: {exc}") from exc
    _require(isinstance(document, dict), "config root must be an object")

    name = document.get("name", "local-foundry")
    _require(isinstance(name, str) and name != "", "name must be a non-empty string")

    auth = _parse_auth(document.get("auth", {}))

    cache_defaults = SemanticCacheSettings()
    if "semantic_cache" in document:
        cache_defaults = _parse_semantic_cache(document["semantic_cache"], context="root", defaults=cache_defaults)

    deployments_raw = document.get("deployments", [])
    _require(
        isinstance(deployments_raw, list) and len(deployments_raw) > 0,
        "deployments must be a non-empty list",
    )
    deployments: dict[str, Deployment] = {}
    for index, raw in enumerate(deployments_raw):
        deployment = _parse_deployment(raw, index, cache_defaults)
        _require(deployment.name not in deployments, f"duplicate deployment name '{deployment.name}'")
        deployments[deployment.name] = deployment

    content_filters_raw = document.get("content_filters", [])
    _require(isinstance(content_filters_raw, list), "content_filters must be a list")
    content_filters: dict[str, ContentFilterPolicy] = {}
    for index, raw in enumerate(content_filters_raw):
        policy = _parse_content_filter(raw, index)
        _require(policy.name not in content_filters, f"duplicate content filter name '{policy.name}'")
        content_filters[policy.name] = policy
    if "default" not in content_filters:
        # Azure applies a default RAI policy to every deployment unless
        # explicitly removed; the simulator mirrors that with a built-in
        # medium-threshold default.
        content_filters["default"] = ContentFilterPolicy(name="default")

    blocklists_raw = document.get("blocklists", [])
    _require(isinstance(blocklists_raw, list), "blocklists must be a list")
    blocklists: dict[str, Blocklist] = {}
    for index, raw in enumerate(blocklists_raw):
        blocklist = _parse_blocklist(raw, index)
        _require(blocklist.name not in blocklists, f"duplicate blocklist name '{blocklist.name}'")
        blocklists[blocklist.name] = blocklist

    content_safety_raw = document.get("content_safety", {})
    _require(isinstance(content_safety_raw, dict), "content_safety must be an object")
    simulation_triggers = content_safety_raw.get("simulation_triggers", True)
    _require(isinstance(simulation_triggers, bool), "content_safety.simulation_triggers must be a boolean")
    lexicons = _parse_lexicons(content_safety_raw.get("lexicons"))

    config = FoundryConfig(
        name=name,
        auth=auth,
        deployments=deployments,
        content_filters=content_filters,
        blocklists=blocklists,
        semantic_cache=cache_defaults,
        lexicons=lexicons,
        simulation_triggers=simulation_triggers,
    )
    _validate_references(config)
    return config


def _validate_references(config: FoundryConfig) -> None:
    for deployment in config.deployments.values():
        if deployment.content_filter is not None:
            _require(
                deployment.content_filter in config.content_filters,
                f"deployment '{deployment.name}' references unknown content filter '{deployment.content_filter}'",
            )
        cache = config.cache_settings_for(deployment)
        if deployment.kind == "chat" and cache.enabled:
            _require(
                cache.embeddings_deployment is not None,
                f"deployment '{deployment.name}' enables semantic_cache but no embeddings_deployment is set "
                "(set semantic_cache.embeddings_deployment at the root or on the deployment)",
            )
            embeddings = config.deployments.get(cache.embeddings_deployment or "")
            _require(
                embeddings is not None and embeddings.kind == "embeddings",
                f"semantic_cache.embeddings_deployment '{cache.embeddings_deployment}' must reference an "
                "embeddings deployment",
            )
    for policy in config.content_filters.values():
        for blocklist_name in policy.blocklists:
            _require(
                blocklist_name in config.blocklists,
                f"content filter '{policy.name}' references unknown blocklist '{blocklist_name}'",
            )

"""Writable management resources: deployments, content filters, blocklists.

Each resource has one URL that takes PUT, GET and DELETE (ADR 0002). A PUT
body is one entry of the config file's list for that resource, parsed by the
same functions, and replaces the whole resource. Writes change the loaded
state in place; they are process-local and lost when the app is recreated.

Every write is validated against the state it would produce with the
startup reference rules, and applied only if that passes, so the loaded
state stays as valid as a config file the simulator would accept.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from app.config import (
    CATEGORIES,
    Blocklist,
    BlocklistItem,
    ConfigError,
    ContentFilterPolicy,
    Deployment,
    FoundryConfig,
    SemanticCacheSettings,
    parse_blocklist_item,
    parse_content_filter,
    parse_deployment,
    validate_references,
)
from app.state import AppState

DEPLOYMENT_FIELDS = frozenset({"model", "kind", "content_filter", "latency_ms", "dimensions", "semantic_cache"})
CONTENT_FILTER_FIELDS = frozenset({"categories", "prompt_shield", "blocklists", "output_filter"})
BLOCKLIST_FIELDS = frozenset({"description"})
BLOCKLIST_ITEM_FIELDS = frozenset({"text", "is_regex", "description"})


class ManagementError(Exception):
    """A request the management API refuses, with its HTTP status."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _not_found(kind: str, name: str) -> ManagementError:
    return ManagementError(404, "NotFound", f"{kind} '{name}' was not found.")


def _check_body(body: Any, allowed: frozenset[str], identity: tuple[str, str]) -> dict[str, Any]:
    """Reject a non-object or unknown fields; drop the path identifier if it matches.

    ``identity`` is (field, value): the body may repeat the URL's identifier,
    as a GET returns it, but may not contradict it.
    """
    if not isinstance(body, dict):
        raise ManagementError(422, "InvalidRequestBody", "The request body must be a JSON object.")
    field, value = identity
    body = dict(body)
    if field in body:
        if body.pop(field) != value:
            raise ManagementError(422, "InvalidRequestBody", f"'{field}' in the body must match the URL ('{value}').")
    unknown = set(body) - allowed
    if unknown:
        raise ManagementError(
            422,
            "InvalidRequestBody",
            f"Unknown field(s) {sorted(unknown)}; expected some of {sorted(allowed)}.",
        )
    return body


def _validated(candidate: FoundryConfig, *, status: int) -> None:
    try:
        validate_references(candidate)
    except ConfigError as exc:
        raise ManagementError(status, "Conflict" if status == 409 else "InvalidReference", str(exc)) from exc


def _parse(parser: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return parser(*args, **kwargs)
    except ConfigError as exc:
        raise ManagementError(422, "InvalidRequestBody", str(exc)) from exc


# Serialization. Each payload is a body a PUT accepts unchanged.


def _cache_payload(cache: SemanticCacheSettings) -> dict[str, Any]:
    return {
        "enabled": cache.enabled,
        "score_threshold": cache.score_threshold,
        "ttl_seconds": cache.ttl_seconds,
        "max_entries": cache.max_entries,
        "embeddings_deployment": cache.embeddings_deployment,
    }


def deployment_payload(deployment: Deployment) -> dict[str, Any]:
    return {
        "name": deployment.name,
        "model": deployment.model,
        "kind": deployment.kind,
        "content_filter": deployment.content_filter,
        "latency_ms": deployment.latency_ms,
        "dimensions": deployment.dimensions,
        "semantic_cache": None if deployment.semantic_cache is None else _cache_payload(deployment.semantic_cache),
    }


def content_filter_payload(policy: ContentFilterPolicy) -> dict[str, Any]:
    return {
        "name": policy.name,
        "categories": {category: policy.categories.get(category, "medium") for category in CATEGORIES},
        "prompt_shield": policy.prompt_shield,
        "blocklists": list(policy.blocklists),
        "output_filter": policy.output_filter,
    }


def blocklist_payload(blocklist: Blocklist) -> dict[str, Any]:
    return {"name": blocklist.name, "description": blocklist.description}


def item_payload(item: BlocklistItem) -> dict[str, Any]:
    return {"item_id": item.item_id, "text": item.text, "is_regex": item.is_regex, "description": item.description}


# Deployments.


def get_deployment(state: AppState, name: str) -> dict[str, Any]:
    deployment = state.config.deployments.get(name)
    if deployment is None:
        raise _not_found("Deployment", name)
    return deployment_payload(deployment)


def put_deployment(state: AppState, name: str, body: Any) -> tuple[int, dict[str, Any]]:
    fields = _check_body(body, DEPLOYMENT_FIELDS, ("name", name))
    deployment = _parse(parse_deployment, {**fields, "name": name}, f"deployment '{name}'", state.config.semantic_cache)
    deployments = {**state.config.deployments, name: deployment}
    _validated(dataclasses.replace(state.config, deployments=deployments), status=422)
    created = name not in state.config.deployments
    state.config.deployments[name] = deployment
    state.semantic_cache.flush()
    return (201 if created else 200), deployment_payload(deployment)


def delete_deployment(state: AppState, name: str) -> None:
    if name not in state.config.deployments:
        raise _not_found("Deployment", name)
    deployments = {key: value for key, value in state.config.deployments.items() if key != name}
    _validated(dataclasses.replace(state.config, deployments=deployments), status=409)
    del state.config.deployments[name]
    state.semantic_cache.flush()


# Content filters.


def get_content_filter(state: AppState, name: str) -> dict[str, Any]:
    policy = state.config.content_filters.get(name)
    if policy is None:
        raise _not_found("Content filter", name)
    return content_filter_payload(policy)


def put_content_filter(state: AppState, name: str, body: Any) -> tuple[int, dict[str, Any]]:
    fields = _check_body(body, CONTENT_FILTER_FIELDS, ("name", name))
    parsed = _parse(parse_content_filter, {**fields, "name": name}, f"content filter '{name}'")
    # Store every category, so a GET shows the level each one applies.
    categories = {category: parsed.categories.get(category, "medium") for category in CATEGORIES}
    policy = dataclasses.replace(parsed, categories=categories)
    content_filters = {**state.config.content_filters, name: policy}
    _validated(dataclasses.replace(state.config, content_filters=content_filters), status=422)
    created = name not in state.config.content_filters
    state.config.content_filters[name] = policy
    return (201 if created else 200), content_filter_payload(policy)


def delete_content_filter(state: AppState, name: str) -> None:
    if name not in state.config.content_filters:
        raise _not_found("Content filter", name)
    users = sorted(d.name for d in state.config.deployments.values() if d.content_filter == name)
    if users:
        raise ManagementError(409, "Conflict", f"Content filter '{name}' is used by deployment(s) {users}.")
    del state.config.content_filters[name]


# Blocklists. state.blocklists is the same dict as config.blocklists, so the
# Content Safety routes and filter policies see every change.


def get_blocklist(state: AppState, name: str) -> dict[str, Any]:
    blocklist = state.blocklists.get(name)
    if blocklist is None:
        raise _not_found("Blocklist", name)
    return blocklist_payload(blocklist)


def put_blocklist(state: AppState, name: str, body: Any) -> tuple[int, dict[str, Any]]:
    """Create a blocklist or replace its description. Its items are kept: they are resources of their own."""
    fields = _check_body(body, BLOCKLIST_FIELDS, ("name", name))
    description = fields.get("description", "")
    if not isinstance(description, str):
        raise ManagementError(422, "InvalidRequestBody", f"blocklist '{name}': description must be a string")
    existing = state.blocklists.get(name)
    if existing is None:
        state.blocklists[name] = Blocklist(name=name, description=description)
        return 201, blocklist_payload(state.blocklists[name])
    existing.description = description
    return 200, blocklist_payload(existing)


def delete_blocklist(state: AppState, name: str) -> None:
    if name not in state.blocklists:
        raise _not_found("Blocklist", name)
    users = sorted(p.name for p in state.config.content_filters.values() if name in p.blocklists)
    if users:
        raise ManagementError(409, "Conflict", f"Blocklist '{name}' is used by content filter(s) {users}.")
    del state.blocklists[name]


def _item_index(blocklist: Blocklist, item_id: str) -> int | None:
    return next((index for index, item in enumerate(blocklist.items) if item.item_id == item_id), None)


def _blocklist_for_item(state: AppState, name: str) -> Blocklist:
    blocklist = state.blocklists.get(name)
    if blocklist is None:
        raise _not_found("Blocklist", name)
    return blocklist


def get_blocklist_item(state: AppState, name: str, item_id: str) -> dict[str, Any]:
    blocklist = _blocklist_for_item(state, name)
    index = _item_index(blocklist, item_id)
    if index is None:
        raise _not_found("Blocklist item", f"{name}/{item_id}")
    return item_payload(blocklist.items[index])


def put_blocklist_item(state: AppState, name: str, item_id: str, body: Any) -> tuple[int, dict[str, Any]]:
    blocklist = _blocklist_for_item(state, name)
    fields = _check_body(body, BLOCKLIST_ITEM_FIELDS, ("item_id", item_id))
    item = _parse(parse_blocklist_item, fields, f"blocklist item '{name}/{item_id}'", item_id=item_id)
    index = _item_index(blocklist, item_id)
    if index is None:
        blocklist.items.append(item)
        return 201, item_payload(item)
    blocklist.items[index] = item
    return 200, item_payload(item)


def delete_blocklist_item(state: AppState, name: str, item_id: str) -> None:
    blocklist = _blocklist_for_item(state, name)
    index = _item_index(blocklist, item_id)
    if index is None:
        raise _not_found("Blocklist item", f"{name}/{item_id}")
    del blocklist.items[index]

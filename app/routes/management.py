"""Simulator management surface.

Health and startup probes are anonymous; everything under
``/foundry/management`` requires an admin key (``X-Foundry-Admin-Key``)
when admin keys are configured. Deployments, content filters, blocklists and
blocklist items are writable, one URL each with PUT, GET and DELETE (ADR
0002). The config file sets the starting state; runtime writes are
process-local and lost when the app is recreated.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app import SERVICE_NAME, SERVICE_VERSION
from app import management_resources as resources
from app.auth import is_admin
from app.management_resources import ManagementError
from app.state import AppState

router = APIRouter(prefix="/foundry")


def _state(request: Request) -> AppState:
    return request.app.state.foundry


def _admin_gate(request: Request) -> JSONResponse | None:
    if not is_admin(request, _state(request).config):
        return _error(401, "Unauthorized", "Provide a valid admin key in the 'X-Foundry-Admin-Key' header.")
    return None


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


async def _body(request: Request) -> Any:
    try:
        return json.loads(await request.body() or b"null")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ManagementError(422, "InvalidRequestBody", f"The request body is not valid JSON: {exc}") from exc


async def _managed(request: Request, action: Callable[[AppState], Any]) -> Response:
    """Run one resource action behind the admin gate and map its outcome to HTTP.

    ``action`` returns a payload (200), a (status, payload) pair, or None (204).
    """
    gate = _admin_gate(request)
    if gate is not None:
        return gate
    try:
        outcome = action(_state(request))
    except ManagementError as exc:
        return _error(exc.status, exc.code, exc.message)
    if outcome is None:
        return Response(status_code=204)
    if isinstance(outcome, tuple):
        status, payload = outcome
        return JSONResponse(status_code=status, content=payload)
    return JSONResponse(content=outcome)


async def _managed_put(request: Request, action: Callable[[AppState, Any], Any]) -> Response:
    gate = _admin_gate(request)
    if gate is not None:
        return gate
    try:
        body = await _body(request)
    except ManagementError as exc:
        return _error(exc.status, exc.code, exc.message)
    return await _managed(request, lambda state: action(state, body))


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME, "version": SERVICE_VERSION}


@router.get("/startup")
async def startup(request: Request) -> dict[str, object]:
    state = _state(request)
    return {
        "status": "ready",
        "resource": state.config.name,
        "deployments": len(state.config.deployments),
        "uptime_seconds": round(time.time() - state.started_at, 3),
    }


@router.get("/management/status", response_model=None)
async def status(request: Request) -> Response:
    gate = _admin_gate(request)
    if gate is not None:
        return gate
    state = _state(request)
    return JSONResponse(
        content={
            "service": SERVICE_NAME,
            "version": SERVICE_VERSION,
            "resource": state.config.name,
            "deployments": len(state.config.deployments),
            "content_filters": sorted(state.config.content_filters),
            "blocklists": sorted(state.blocklists),
            "semantic_cache_defaults": {
                "enabled": state.config.semantic_cache.enabled,
                "score_threshold": state.config.semantic_cache.score_threshold,
                "ttl_seconds": state.config.semantic_cache.ttl_seconds,
                "max_entries": state.config.semantic_cache.max_entries,
                "embeddings_deployment": state.config.semantic_cache.embeddings_deployment,
            },
            "simulation_triggers": state.config.simulation_triggers,
        }
    )


@router.get("/management/deployments", response_model=None)
async def deployments(request: Request) -> Response:
    gate = _admin_gate(request)
    if gate is not None:
        return gate
    state = _state(request)
    payload = []
    for deployment in state.config.deployments.values():
        cache = state.config.cache_settings_for(deployment)
        payload.append(
            {
                "name": deployment.name,
                "model": deployment.model,
                "kind": deployment.kind,
                "content_filter": deployment.content_filter,
                "latency_ms": deployment.latency_ms,
                "dimensions": deployment.dimensions if deployment.kind == "embeddings" else None,
                "semantic_cache": {
                    "enabled": cache.enabled and deployment.kind == "chat",
                    "score_threshold": cache.score_threshold,
                    "ttl_seconds": cache.ttl_seconds,
                    "embeddings_deployment": cache.embeddings_deployment,
                },
            }
        )
    return JSONResponse(content={"deployments": payload})


@router.get("/management/semantic-cache", response_model=None)
async def semantic_cache_stats(request: Request) -> Response:
    gate = _admin_gate(request)
    if gate is not None:
        return gate
    return JSONResponse(content=_state(request).semantic_cache.snapshot())


@router.delete("/management/semantic-cache", response_model=None)
async def semantic_cache_flush(request: Request) -> Response:
    gate = _admin_gate(request)
    if gate is not None:
        return gate
    cleared = _state(request).semantic_cache.flush()
    return JSONResponse(content={"flushed": True, "cleared_entries": cleared})


@router.get("/management/semantic-cache/events", response_model=None)
async def semantic_cache_events(request: Request) -> Response:
    gate = _admin_gate(request)
    if gate is not None:
        return gate
    return JSONResponse(content={"events": _state(request).semantic_cache.events()})


@router.get("/management/content-safety", response_model=None)
async def content_safety_stats(request: Request) -> Response:
    gate = _admin_gate(request)
    if gate is not None:
        return gate
    return JSONResponse(content=_state(request).safety_stats.snapshot())


# Writable resources (ADR 0002). Each URL takes PUT, GET and DELETE.


@router.get("/management/deployments/{name}", response_model=None)
async def get_deployment(name: str, request: Request) -> Response:
    return await _managed(request, lambda state: resources.get_deployment(state, name))


@router.put("/management/deployments/{name}", response_model=None)
async def put_deployment(name: str, request: Request) -> Response:
    return await _managed_put(request, lambda state, body: resources.put_deployment(state, name, body))


@router.delete("/management/deployments/{name}", response_model=None)
async def delete_deployment(name: str, request: Request) -> Response:
    return await _managed(request, lambda state: resources.delete_deployment(state, name))


@router.get("/management/content-filters/{name}", response_model=None)
async def get_content_filter(name: str, request: Request) -> Response:
    return await _managed(request, lambda state: resources.get_content_filter(state, name))


@router.put("/management/content-filters/{name}", response_model=None)
async def put_content_filter(name: str, request: Request) -> Response:
    return await _managed_put(request, lambda state, body: resources.put_content_filter(state, name, body))


@router.delete("/management/content-filters/{name}", response_model=None)
async def delete_content_filter(name: str, request: Request) -> Response:
    return await _managed(request, lambda state: resources.delete_content_filter(state, name))


@router.get("/management/blocklists/{name}", response_model=None)
async def get_blocklist(name: str, request: Request) -> Response:
    return await _managed(request, lambda state: resources.get_blocklist(state, name))


@router.put("/management/blocklists/{name}", response_model=None)
async def put_blocklist(name: str, request: Request) -> Response:
    return await _managed_put(request, lambda state, body: resources.put_blocklist(state, name, body))


@router.delete("/management/blocklists/{name}", response_model=None)
async def delete_blocklist(name: str, request: Request) -> Response:
    return await _managed(request, lambda state: resources.delete_blocklist(state, name))


@router.get("/management/blocklists/{name}/items/{item_id}", response_model=None)
async def get_blocklist_item(name: str, item_id: str, request: Request) -> Response:
    return await _managed(request, lambda state: resources.get_blocklist_item(state, name, item_id))


@router.put("/management/blocklists/{name}/items/{item_id}", response_model=None)
async def put_blocklist_item(name: str, item_id: str, request: Request) -> Response:
    return await _managed_put(request, lambda state, body: resources.put_blocklist_item(state, name, item_id, body))


@router.delete("/management/blocklists/{name}/items/{item_id}", response_model=None)
async def delete_blocklist_item(name: str, item_id: str, request: Request) -> Response:
    return await _managed(request, lambda state: resources.delete_blocklist_item(state, name, item_id))

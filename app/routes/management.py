"""Simulator management surface.

Health and startup probes are anonymous; everything under
``/foundry/management`` requires an admin key (``X-Foundry-Admin-Key``)
when admin keys are configured. The management API is read-only except for
the semantic-cache flush — the config file stays the single source of
truth for behaviour.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app import SERVICE_NAME, SERVICE_VERSION
from app.auth import is_admin
from app.state import AppState

router = APIRouter(prefix="/foundry")


def _state(request: Request) -> AppState:
    return request.app.state.foundry


def _admin_gate(request: Request) -> JSONResponse | None:
    if not is_admin(request, _state(request).config):
        return JSONResponse(
            status_code=401,
            content={
                "error": {
                    "code": "Unauthorized",
                    "message": "Provide a valid admin key in the 'X-Foundry-Admin-Key' header.",
                }
            },
        )
    return None


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

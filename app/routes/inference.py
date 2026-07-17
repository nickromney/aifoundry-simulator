"""Inference surfaces.

Three route families front the same pipeline, mirroring how Azure AI
Foundry exposes model deployments:

- Azure OpenAI deployment-scoped:
  ``/openai/deployments/{deployment}/...?api-version=...``
- Foundry next-generation v1 (model selected by request body, no
  api-version): ``/openai/v1/...``
- Azure AI Model Inference (used for non-OpenAI models in Foundry):
  ``/models/...?api-version=...``
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app import errors, pipeline
from app.auth import is_authorized
from app.config import Deployment
from app.state import AppState

azure_router = APIRouter()
v1_router = APIRouter(prefix="/openai/v1")
models_router = APIRouter(prefix="/models")


def _state(request: Request) -> AppState:
    return request.app.state.foundry


def _auth_or_error(request: Request) -> JSONResponse | None:
    if not is_authorized(request, _state(request).config):
        return errors.unauthorized()
    return None


def _api_version_or_error(request: Request) -> JSONResponse | None:
    if not request.query_params.get("api-version"):
        return errors.missing_api_version()
    return None


def _resolve(request: Request, deployment_name: str, kind: str) -> Deployment | JSONResponse:
    deployment = _state(request).config.deployment(deployment_name)
    if deployment is None:
        return errors.deployment_not_found(deployment_name)
    if deployment.kind != kind:
        operation = "chatCompletion" if kind == "chat" else "embeddings"
        return errors.openai_error(
            400,
            f"The {operation} operation does not work with the specified model, "
            f"'{deployment.model}' (deployment '{deployment.name}').",
            code="OperationNotSupported",
        )
    return deployment


async def _model_from_body(request: Request) -> str | JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return errors.openai_error(400, "Request body must be valid JSON.")
    if not isinstance(body, dict) or not isinstance(body.get("model"), str) or not body["model"]:
        return errors.openai_error(400, "'model' is required and must name a deployment.", param="model")
    return body["model"]


def _model_not_found(model: str) -> JSONResponse:
    return errors.openai_error(
        404,
        f"The model '{model}' does not exist or you do not have access to it. The simulator routes "
        "'model' to deployment names; check /foundry/management/deployments for the loaded set.",
        code="model_not_found",
    )


# --- Azure OpenAI deployment-scoped surface -------------------------------


@azure_router.post("/openai/deployments/{deployment_name}/chat/completions", response_model=None)
async def azure_chat_completions(deployment_name: str, request: Request) -> Response:
    for gate in (_auth_or_error(request), _api_version_or_error(request)):
        if gate is not None:
            return gate
    deployment = _resolve(request, deployment_name, "chat")
    if isinstance(deployment, JSONResponse):
        return deployment
    return await pipeline.run_chat_completion(_state(request), deployment, request)


@azure_router.post("/openai/deployments/{deployment_name}/embeddings", response_model=None)
async def azure_embeddings(deployment_name: str, request: Request) -> Response:
    for gate in (_auth_or_error(request), _api_version_or_error(request)):
        if gate is not None:
            return gate
    deployment = _resolve(request, deployment_name, "embeddings")
    if isinstance(deployment, JSONResponse):
        return deployment
    return await pipeline.run_embeddings(_state(request), deployment, request)


# --- Foundry v1 surface ---------------------------------------------------


async def _v1_dispatch(request: Request, kind: str, runner: Any) -> Response:
    gate = _auth_or_error(request)
    if gate is not None:
        return gate
    model = await _model_from_body(request)
    if isinstance(model, JSONResponse):
        return model
    deployment = _state(request).config.deployment(model)
    if deployment is None:
        return _model_not_found(model)
    resolved = _resolve(request, model, kind)
    if isinstance(resolved, JSONResponse):
        return resolved
    return await runner(_state(request), resolved, request)


@v1_router.post("/chat/completions", response_model=None)
async def v1_chat_completions(request: Request) -> Response:
    return await _v1_dispatch(request, "chat", pipeline.run_chat_completion)


@v1_router.post("/embeddings", response_model=None)
async def v1_embeddings(request: Request) -> Response:
    return await _v1_dispatch(request, "embeddings", pipeline.run_embeddings)


@v1_router.post("/responses", response_model=None)
async def v1_responses(request: Request) -> Response:
    return await _v1_dispatch(request, "chat", pipeline.run_responses)


@v1_router.get("/models", response_model=None)
async def v1_list_models(request: Request) -> Response:
    gate = _auth_or_error(request)
    if gate is not None:
        return gate
    state = _state(request)
    data = [
        {
            "id": deployment.name,
            "object": "model",
            "created": int(state.started_at),
            "owned_by": "aifoundry-simulator",
            "capabilities": {
                "chat_completion": deployment.kind == "chat",
                "embeddings": deployment.kind == "embeddings",
            },
        }
        for deployment in state.config.deployments.values()
    ]
    return JSONResponse(content={"object": "list", "data": data})


@v1_router.get("/models/{model_id}", response_model=None)
async def v1_get_model(model_id: str, request: Request) -> Response:
    gate = _auth_or_error(request)
    if gate is not None:
        return gate
    state = _state(request)
    deployment = state.config.deployment(model_id)
    if deployment is None:
        return _model_not_found(model_id)
    return JSONResponse(
        content={
            "id": deployment.name,
            "object": "model",
            "created": int(state.started_at),
            "owned_by": "aifoundry-simulator",
            "capabilities": {
                "chat_completion": deployment.kind == "chat",
                "embeddings": deployment.kind == "embeddings",
            },
        }
    )


# --- Azure AI Model Inference surface -------------------------------------


@models_router.post("/chat/completions", response_model=None)
async def models_chat_completions(request: Request) -> Response:
    gate = _api_version_or_error(request)
    if gate is not None:
        return gate
    return await _v1_dispatch(request, "chat", pipeline.run_chat_completion)


@models_router.post("/embeddings", response_model=None)
async def models_embeddings(request: Request) -> Response:
    gate = _api_version_or_error(request)
    if gate is not None:
        return gate
    return await _v1_dispatch(request, "embeddings", pipeline.run_embeddings)

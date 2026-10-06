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
MODEL_INFERENCE_API_VERSIONS = {"2024-05-01-preview", "2025-04-01"}


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


def _model_inference_api_version_or_error(request: Request) -> JSONResponse | None:
    version = request.query_params.get("api-version")
    if not version:
        return errors.missing_api_version()
    if version not in MODEL_INFERENCE_API_VERSIONS:
        return errors.azure_resource_error(
            400,
            "InvalidApiVersionParameter",
            "The /models surface supports api-version=2025-04-01 or the legacy 2024-05-01-preview version.",
        )
    return None


def _v1_api_version_or_error(request: Request) -> JSONResponse | None:
    version = request.query_params.get("api-version")
    if version is not None and version not in {"v1", "preview"}:
        return errors.openai_error(
            400,
            "The api-version query parameter for the v1 surface must be 'v1' or 'preview'.",
            code="invalid_api_version",
            param="api-version",
        )
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


async def _model_from_body(
    request: Request, *, required: bool = True, model_inference: bool = False
) -> str | None | JSONResponse:
    try:
        body = await request.json()
    except Exception:
        if model_inference:
            return errors.model_inference_error(400, "invalid_request", "Request body must be valid JSON.")
        return errors.openai_error(400, "Request body must be valid JSON.")
    if not isinstance(body, dict):
        if model_inference:
            return errors.model_inference_error(400, "invalid_request", "Request body must be a JSON object.")
        return errors.openai_error(400, "Request body must be a JSON object.")
    if not isinstance(body.get("model"), str) or not body["model"]:
        if not required:
            return None
        if model_inference:
            return errors.model_inference_error(
                422,
                "missing_required_parameter",
                "The model parameter is required.",
                location=["model"],
            )
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


async def _v1_dispatch(
    request: Request,
    kind: str,
    runner: Any,
    *,
    model_inference: bool = False,
) -> Response:
    gates = (
        (_auth_or_error(request),) if model_inference else (_auth_or_error(request), _v1_api_version_or_error(request))
    )
    for gate in gates:
        if gate is not None:
            return gate
    model = await _model_from_body(
        request,
        required=not model_inference,
        model_inference=model_inference,
    )
    if isinstance(model, JSONResponse):
        return model
    if model is None:
        candidates = [
            deployment for deployment in _state(request).config.deployments.values() if deployment.kind == kind
        ]
        if len(candidates) != 1:
            return errors.model_inference_error(
                422,
                "missing_required_parameter",
                "The model parameter is required when more than one compatible deployment is configured.",
                location=["model"],
            )
        model = candidates[0].name
    deployment = _state(request).config.deployment(model)
    if deployment is None:
        return _model_not_found(model)
    resolved = _resolve(request, model, kind)
    if isinstance(resolved, JSONResponse):
        return resolved
    if model_inference:
        return await runner(_state(request), resolved, request, model_inference=True)
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


def _stored_response(request: Request, response_id: str) -> tuple[AppState, Any] | JSONResponse:
    state = _state(request)
    record = state.responses.get(response_id)
    if record is None:
        return errors.response_not_found(response_id)
    return state, record


@v1_router.get("/responses/{response_id}/input_items", response_model=None)
async def v1_response_input_items(response_id: str, request: Request) -> Response:
    for gate in (_auth_or_error(request), _v1_api_version_or_error(request)):
        if gate is not None:
            return gate
    result = _stored_response(request, response_id)
    if isinstance(result, JSONResponse):
        return result
    _, record = result
    items = record.input_items
    return JSONResponse(
        content={
            "object": "list",
            "data": items,
            "has_more": False,
            "first_id": items[0]["id"] if items else None,
            "last_id": items[-1]["id"] if items else None,
        }
    )


@v1_router.post("/responses/{response_id}/cancel", response_model=None)
async def v1_cancel_response(response_id: str, request: Request) -> Response:
    for gate in (_auth_or_error(request), _v1_api_version_or_error(request)):
        if gate is not None:
            return gate
    result = _stored_response(request, response_id)
    if isinstance(result, JSONResponse):
        return result
    _, record = result
    return errors.openai_error(
        400,
        f"Response '{response_id}' is not in progress and cannot be cancelled.",
        code="response_not_in_progress",
    )


@v1_router.delete("/responses/{response_id}", response_model=None)
async def v1_delete_response(response_id: str, request: Request) -> Response:
    for gate in (_auth_or_error(request), _v1_api_version_or_error(request)):
        if gate is not None:
            return gate
    state = _state(request)
    if not state.responses.delete(response_id):
        return errors.response_not_found(response_id)
    return JSONResponse(content={"id": response_id, "object": "response.deleted", "deleted": True})


@v1_router.get("/responses/{response_id}", response_model=None)
async def v1_get_response(response_id: str, request: Request) -> Response:
    for gate in (_auth_or_error(request), _v1_api_version_or_error(request)):
        if gate is not None:
            return gate
    result = _stored_response(request, response_id)
    if isinstance(result, JSONResponse):
        return result
    _, record = result
    return JSONResponse(content=record.payload)


def _model_payload(state: AppState, deployment: Deployment) -> dict[str, Any]:
    return {
        "id": deployment.name,
        "object": "model",
        "created": int(state.started_at),
        "owned_by": "aifoundry-simulator",
    }


@v1_router.get("/models", response_model=None)
async def v1_list_models(request: Request) -> Response:
    for gate in (_auth_or_error(request), _v1_api_version_or_error(request)):
        if gate is not None:
            return gate
    state = _state(request)
    data = [_model_payload(state, deployment) for deployment in state.config.deployments.values()]
    return JSONResponse(content={"object": "list", "data": data})


@v1_router.get("/models/{model_id}", response_model=None)
async def v1_get_model(model_id: str, request: Request) -> Response:
    for gate in (_auth_or_error(request), _v1_api_version_or_error(request)):
        if gate is not None:
            return gate
    state = _state(request)
    deployment = state.config.deployment(model_id)
    if deployment is None:
        return _model_not_found(model_id)
    return JSONResponse(content=_model_payload(state, deployment))


# --- Azure AI Model Inference surface -------------------------------------


@models_router.post("/chat/completions", response_model=None)
async def models_chat_completions(request: Request) -> Response:
    gate = _model_inference_api_version_or_error(request)
    if gate is not None:
        return gate
    return await _v1_dispatch(request, "chat", pipeline.run_chat_completion, model_inference=True)


@models_router.post("/embeddings", response_model=None)
async def models_embeddings(request: Request) -> Response:
    gate = _model_inference_api_version_or_error(request)
    if gate is not None:
        return gate
    return await _v1_dispatch(request, "embeddings", pipeline.run_embeddings, model_inference=True)

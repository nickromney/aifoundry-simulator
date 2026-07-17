"""Application factory.

``create_app`` loads the config eagerly so an invalid config fails container
start rather than the first request. The module-level ``app`` is the uvicorn
entrypoint (``app.main:app``); tests build isolated instances with explicit
config paths.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from app import SERVICE_NAME, SERVICE_VERSION
from app.config import load_config
from app.routes import content_safety_api, inference, management
from app.state import AppState


def create_app(config_path: str | Path | None = None) -> FastAPI:
    config = load_config(config_path)
    application = FastAPI(title="Azure AI Foundry Simulator", version=SERVICE_VERSION, docs_url=None, redoc_url=None)
    application.state.foundry = AppState(config=config)

    application.include_router(inference.azure_router)
    application.include_router(inference.v1_router)
    application.include_router(inference.models_router)
    application.include_router(content_safety_api.router)
    application.include_router(management.router)

    @application.get("/")
    async def root_hint() -> dict[str, object]:
        return {
            "service": SERVICE_NAME,
            "version": SERVICE_VERSION,
            "resource": config.name,
            "entrypoints": {
                "azure_openai_chat": "/openai/deployments/{deployment}/chat/completions?api-version=2024-10-21",
                "azure_openai_embeddings": "/openai/deployments/{deployment}/embeddings?api-version=2024-10-21",
                "foundry_v1_chat": "/openai/v1/chat/completions",
                "foundry_v1_embeddings": "/openai/v1/embeddings",
                "foundry_v1_responses": "/openai/v1/responses",
                "foundry_v1_models": "/openai/v1/models",
                "model_inference_chat": "/models/chat/completions?api-version=2024-05-01-preview",
                "content_safety_analyze": "/contentsafety/text:analyze?api-version=2024-09-01",
                "content_safety_shield_prompt": "/contentsafety/text:shieldPrompt?api-version=2024-09-01",
                "content_safety_blocklists": "/contentsafety/text/blocklists?api-version=2024-09-01",
                "health": "/foundry/health",
                "management_status": "/foundry/management/status",
            },
        }

    return application


app = create_app()

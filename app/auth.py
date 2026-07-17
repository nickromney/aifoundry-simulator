"""Request authentication.

Inference and content safety surfaces accept the Azure conventions:
an ``api-key`` header (Azure OpenAI / Content Safety) or an
``Authorization: Bearer <key>`` header (Foundry v1 / Entra-style clients).
Management endpoints under /foundry/management require an admin key via
``X-Foundry-Admin-Key`` when admin keys are configured.
"""

from __future__ import annotations

from fastapi import Request

from app.config import FoundryConfig


def extract_api_key(request: Request) -> str | None:
    api_key = request.headers.get("api-key")
    if api_key:
        return api_key
    authorization = request.headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def is_authorized(request: Request, config: FoundryConfig) -> bool:
    if config.auth.allow_anonymous:
        return True
    key = extract_api_key(request)
    return key is not None and (key in config.auth.api_keys or key in config.auth.admin_keys)


def is_admin(request: Request, config: FoundryConfig) -> bool:
    if not config.auth.admin_keys:
        return True
    key = request.headers.get("x-foundry-admin-key")
    return key is not None and key in config.auth.admin_keys

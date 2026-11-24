"""Request authentication.

Inference and content safety surfaces accept the Azure conventions:
an ``api-key`` header (Azure OpenAI / Content Safety) or an
``Authorization: Bearer <key>`` header (Foundry v1 / Entra-style clients).
The Azure AI Language surface additionally accepts Language's own
``Ocp-Apim-Subscription-Key`` header, checked against the same keys.
Management endpoints under /foundry/management require an admin key via
``X-Foundry-Admin-Key`` when admin keys are configured.
"""

from __future__ import annotations

from fastapi import Request

from app.config import FoundryConfig


def extract_api_key(request: Request, *, subscription_key_header: bool = False) -> str | None:
    if subscription_key_header:
        subscription_key = request.headers.get("ocp-apim-subscription-key")
        if subscription_key:
            return subscription_key
    api_key = request.headers.get("api-key")
    if api_key:
        return api_key
    authorization = request.headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def is_authorized(request: Request, config: FoundryConfig, *, subscription_key_header: bool = False) -> bool:
    if config.auth.allow_anonymous:
        return True
    key = extract_api_key(request, subscription_key_header=subscription_key_header)
    # Tenant/admin keys are management-plane credentials.  Accepting one on a
    # data-plane route would make a local config teach the wrong Azure
    # separation and would hide accidental credential mix-ups in tests.
    return key is not None and key in config.auth.api_keys


def is_admin(request: Request, config: FoundryConfig) -> bool:
    if not config.auth.admin_keys:
        return True
    key = request.headers.get("x-foundry-admin-key")
    return key is not None and key in config.auth.admin_keys

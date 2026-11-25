"""Request authentication and data-plane authorization.

Inference and content safety surfaces accept the Azure key conventions:
an ``api-key`` header (Azure OpenAI / Content Safety) or an
``Authorization: Bearer <key>`` header (Foundry v1 clients).
The Azure AI Language surface additionally accepts Language's own
``Ocp-Apim-Subscription-Key`` header, checked against the same keys.

When ``auth.entra.enabled`` is set, a bearer value that parses as a JWT is
treated as a Microsoft Entra ID (managed identity) access token instead of a
key: it must be RS256-signed by a trusted issuer, carry an allowed audience,
be inside its validity window, and its principal (``oid``, else ``sub``) must
hold a role in ``auth.entra.role_assignments`` that grants the surface.
``auth.disable_local_auth`` mirrors the resource property
``disableLocalAuth``: every key-based credential is rejected.

Management endpoints under /foundry/management require an admin key via
``X-Foundry-Admin-Key`` when admin keys are configured; tokens never reach
the management plane.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from fastapi import Request
from jwt.algorithms import RSAAlgorithm

from app.config import EntraSettings, FoundryConfig, TrustedIssuer

logger = logging.getLogger(__name__)

Surface = Literal["inference", "content_safety", "language"]

# Built-in Azure roles whose data actions cover each surface. Inference roles
# follow the Azure OpenAI RBAC guidance
# (https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/role-based-access-control)
# plus the broader Cognitive Services User and Foundry's Azure AI User roles.
# Role assignment here is a flat per-resource map: no scope inheritance.
SURFACE_ROLES: dict[str, frozenset[str]] = {
    "inference": frozenset(
        {
            "Cognitive Services OpenAI User",
            "Cognitive Services OpenAI Contributor",
            "Cognitive Services User",
            "Azure AI User",
        }
    ),
    "content_safety": frozenset({"Cognitive Services User"}),
    "language": frozenset({"Cognitive Services User"}),
}

ALLOWED_ALGORITHMS = ("RS256",)
JWKS_FETCH_TIMEOUT_SECONDS = 5.0

# Azure OpenAI's wire message for a rejected Entra token. Microsoft Learn does
# not publish this body; the text matches what the service returns in practice
# and the envelope matches this repo's existing 401 shape. The managed identity
# troubleshooting table only names the status ("401 Unauthorized"):
# https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/managed-identity
INVALID_TOKEN_MESSAGE = (
    "Unauthorized. Access token is missing, invalid, audience is incorrect "
    "(https://cognitiveservices.azure.com), or have expired."
)


@dataclass(frozen=True)
class AuthFailure:
    """Why a data-plane request was rejected.

    ``kind`` keeps the three failures distinct so each surface can render its
    own envelope: ``key`` (missing/invalid key, or keys disabled), ``token``
    (Entra token failed validation) and ``role`` (valid token, no role).
    """

    kind: Literal["key", "token", "role"]
    code: str
    message: str


class JwksUnavailableError(Exception):
    """The issuer's key set could not be fetched or parsed."""


@dataclass
class JwksCache:
    """Per-app cache of issuer verification keys.

    JWKS documents are fetched over HTTP with a TTL. ``transport`` lets tests
    swap in ``httpx.MockTransport`` without network access.
    """

    transport: httpx.AsyncBaseTransport | None = None
    _jwks: dict[str, tuple[float, dict[str, Any]]] = field(default_factory=dict)
    _pem: dict[str, Any] = field(default_factory=dict)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    async def _fetch(self, uri: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=JWKS_FETCH_TIMEOUT_SECONDS) as client:
                response = await client.get(uri)
            response.raise_for_status()
            document = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise JwksUnavailableError(f"cannot fetch JWKS from {uri}: {exc}") from exc
        if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
            raise JwksUnavailableError(f"JWKS from {uri} has no 'keys' list")
        keys: dict[str, Any] = {}
        for entry in document["keys"]:
            if not isinstance(entry, dict) or entry.get("kty") != "RSA" or not entry.get("kid"):
                continue
            if entry.get("use", "sig") != "sig" or entry.get("alg", "RS256") not in ALLOWED_ALGORITHMS:
                continue
            try:
                keys[str(entry["kid"])] = RSAAlgorithm.from_jwk(entry)
            except (jwt.InvalidKeyError, ValueError, KeyError, TypeError):
                logger.warning("skipping unusable JWKS entry kid=%s from %s", entry.get("kid"), uri)
        return keys

    async def jwks_key(self, uri: str, kid: str, ttl_seconds: int) -> Any | None:
        lock = self._locks.setdefault(uri, asyncio.Lock())
        async with lock:
            cached = self._jwks.get(uri)
            now = time.monotonic()
            if cached is not None and now - cached[0] < ttl_seconds and kid in cached[1]:
                return cached[1][kid]
            refetched = False
            if cached is None or now - cached[0] >= ttl_seconds:
                self._jwks[uri] = (now, await self._fetch(uri))
                refetched = True
            keys = self._jwks[uri][1]
            if kid not in keys and not refetched:
                # Unknown kid on a fresh cache: the issuer may have rotated.
                # Refetch exactly once, then fail closed.
                self._jwks[uri] = (time.monotonic(), await self._fetch(uri))
                keys = self._jwks[uri][1]
            return keys.get(kid)

    def pem_key(self, path: str) -> Any:
        if path not in self._pem:
            key = load_pem_public_key(Path(path).read_bytes())
            if not isinstance(key, rsa.RSAPublicKey):
                raise ValueError(f"{path} is not an RSA public key")
            self._pem[path] = key
        return self._pem[path]


def _bearer_value(request: Request) -> str | None:
    authorization = request.headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None


def extract_api_key(request: Request, *, subscription_key_header: bool = False) -> str | None:
    if subscription_key_header:
        subscription_key = request.headers.get("ocp-apim-subscription-key")
        if subscription_key:
            return subscription_key
    api_key = request.headers.get("api-key")
    if api_key:
        return api_key
    return _bearer_value(request)


def looks_like_jwt(value: str) -> bool:
    if value.count(".") != 2:
        return False
    try:
        header = jwt.get_unverified_header(value)
    except jwt.PyJWTError:
        return False
    return isinstance(header, dict) and "alg" in header


def _token_failure(reason: str) -> AuthFailure:
    # The reason is logged, never returned: rejection bodies do not say which
    # check failed, so they leak nothing about issuer or key configuration.
    logger.info("rejected Entra token: %s", reason)
    return AuthFailure(kind="token", code="401", message=INVALID_TOKEN_MESSAGE)


def data_action(surface: Surface, path: str) -> str:
    """Name the data action a request needs, for the PermissionDenied message.

    The names follow the Microsoft.CognitiveServices resource-provider
    operation format. The Learn pages we checked do not list them, so treat
    them as illustrative rather than authoritative.
    """

    prefix = "Microsoft.CognitiveServices/accounts"
    if surface == "content_safety":
        return f"{prefix}/ContentSafety/*"
    if surface == "language":
        return f"{prefix}/Language/*"
    if "embeddings" in path:
        return f"{prefix}/OpenAI/deployments/embeddings/action"
    if "chat/completions" in path:
        return f"{prefix}/OpenAI/deployments/chat/completions/action"
    if "/responses" in path:
        return f"{prefix}/OpenAI/responses/*"
    if "/models" in path:
        return f"{prefix}/OpenAI/models/read"
    return f"{prefix}/OpenAI/*"


async def _verify_token(token: str, entra: EntraSettings, cache: JwksCache) -> dict[str, Any] | AuthFailure:
    try:
        header = jwt.get_unverified_header(token)
        unverified = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError as exc:
        return _token_failure(f"malformed token: {exc}")
    alg = header.get("alg")
    if alg not in ALLOWED_ALGORITHMS:
        return _token_failure(f"algorithm {alg!r} is not allowed")
    issuer_name = unverified.get("iss")
    issuer: TrustedIssuer | None = next((item for item in entra.trusted_issuers if item.issuer == issuer_name), None)
    if issuer is None:
        return _token_failure(f"untrusted issuer {issuer_name!r}")

    if issuer.public_key_file is not None:
        try:
            key = cache.pem_key(issuer.public_key_file)
        except (OSError, ValueError) as exc:
            return _token_failure(f"cannot load public key for {issuer.issuer}: {exc}")
    else:
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            return _token_failure("token has no kid header")
        try:
            key = await cache.jwks_key(issuer.jwks_uri or "", kid, entra.jwks_cache_ttl_seconds)
        except JwksUnavailableError as exc:
            logger.warning("%s", exc)
            return _token_failure("issuer keys unavailable")
        if key is None:
            return _token_failure(f"unknown kid {kid!r} for issuer {issuer.issuer}")

    try:
        return jwt.decode(
            token,
            key,
            algorithms=list(ALLOWED_ALGORITHMS),
            audience=list(entra.audiences),
            issuer=issuer.issuer,
            leeway=entra.clock_skew_seconds,
            options={"require": ["exp", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        return _token_failure(f"validation failed: {exc}")


def _authorize_principal(
    claims: dict[str, Any], entra: EntraSettings, surface: Surface, request: Request
) -> AuthFailure | None:
    principal = claims.get("oid") or claims.get("sub")
    if not isinstance(principal, str) or not principal:
        return _token_failure("token has no oid or sub claim")
    roles = set(entra.role_assignments.get(principal, ()))
    if roles & SURFACE_ROLES[surface]:
        return None
    action = data_action(surface, request.url.path)
    # Microsoft Learn's managed identity troubleshooting table lists a missing
    # role as "403 Forbidden or PermissionDenied" and documents no body:
    # https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/managed-identity
    # Azure OpenAI is observed to answer 401 with code PermissionDenied and a
    # message naming the missing data action; the simulator follows that wire
    # behaviour. The status is a judgement call, not a documented contract.
    return AuthFailure(
        kind="role",
        code="PermissionDenied",
        message=(
            f"The principal `{principal}` lacks the required data action `{action}` to perform "
            f"`{request.method} {request.url.path}` operation."
        ),
    )


async def authorize(
    request: Request,
    config: FoundryConfig,
    cache: JwksCache,
    surface: Surface,
    *,
    subscription_key_header: bool = False,
) -> AuthFailure | None:
    """Authenticate and authorize a data-plane request; ``None`` means allowed."""

    auth = config.auth
    if auth.allow_anonymous:
        return None
    entra = auth.entra
    bearer = _bearer_value(request)
    if entra.enabled and bearer is not None and looks_like_jwt(bearer):
        verified = await _verify_token(bearer, entra, cache)
        if isinstance(verified, AuthFailure):
            return verified
        return _authorize_principal(verified, entra, surface, request)

    key_failure = AuthFailure(kind="key", code="401", message="invalid key")
    if auth.disable_local_auth:
        # disableLocalAuth: keys are rejected with the ordinary invalid-key
        # 401 (https://learn.microsoft.com/en-us/azure/ai-services/disable-local-auth).
        return key_failure
    key = extract_api_key(request, subscription_key_header=subscription_key_header)
    # Tenant/admin keys are management-plane credentials.  Accepting one on a
    # data-plane route would make a local config teach the wrong Azure
    # separation and would hide accidental credential mix-ups in tests.
    if key is not None and key in auth.api_keys:
        return None
    return key_failure


def is_admin(request: Request, config: FoundryConfig) -> bool:
    if not config.auth.admin_keys:
        return True
    key = request.headers.get("x-foundry-admin-key")
    return key is not None and key in config.auth.admin_keys

"""Keyless (Entra ID / managed identity) data-plane auth.

Keypairs are generated per test run; no key material is committed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.auth import INVALID_TOKEN_MESSAGE
from app.config import ConfigError, load_config
from tests.conftest import (
    ADMIN_HEADERS,
    ANALYZE_PATH,
    API_KEY,
    CHAT_PATH,
    EMBEDDINGS_PATH,
    base_config,
    chat_body,
)

ISSUER = "http://apim-simulator:8000/.well-known/workload-identity"
JWKS_URI = f"{ISSUER}/jwks.json"
AUDIENCE = "https://cognitiveservices.azure.com"
LANGUAGE_PATH = "/language/:analyze-text?api-version=2026-05-01"


class Keypair:
    def __init__(self, kid: str) -> None:
        self.kid = kid
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.public = self.private.public_key()

    def jwk(self) -> dict[str, Any]:
        entry = RSAAlgorithm.to_jwk(self.public, as_dict=True)
        return {**entry, "kid": self.kid, "use": "sig", "alg": "RS256"}

    def public_pem(self) -> bytes:
        return self.public.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)

    def token(self, *, headers: dict[str, Any] | None = None, **overrides: Any) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": "system-assigned",
            "oid": "system-assigned",
            "appid": "system-assigned",
            "tid": "00000000-0000-0000-0000-000000000000",
            "iat": now,
            "nbf": now,
            "exp": now + 600,
        }
        claims.update(overrides)
        claims = {key: value for key, value in claims.items() if value is not None}
        return jwt.encode(claims, self.private, algorithm="RS256", headers={"kid": self.kid, **(headers or {})})


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unsigned(header: dict[str, Any], claims: dict[str, Any]) -> str:
    return f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claims).encode())}"


@pytest.fixture(scope="module")
def keys() -> Keypair:
    return Keypair("kid-one")


class JwksServer:
    """httpx.MockTransport handler that counts JWKS fetches."""

    def __init__(self, *keypairs: Keypair) -> None:
        self.keypairs = list(keypairs)
        self.requests = 0
        self.fail = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        assert str(request.url) == JWKS_URI
        if self.fail:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={"keys": [pair.jwk() for pair in self.keypairs]})


def entra_config(
    *,
    roles: dict[str, list[str]] | None = None,
    issuer: dict[str, Any] | None = None,
    disable_local_auth: bool = False,
) -> dict[str, Any]:
    config = base_config()
    config["auth"]["disable_local_auth"] = disable_local_auth
    config["auth"]["entra"] = {
        "enabled": True,
        "trusted_issuers": [issuer or {"issuer": ISSUER, "jwks_uri": JWKS_URI}],
        "audiences": [AUDIENCE, "https://ai.azure.com"],
        "role_assignments": roles if roles is not None else {"system-assigned": ["Cognitive Services OpenAI User"]},
    }
    return config


@pytest.fixture
def entra_client(make_client, keys):
    def _make(server: JwksServer | None = None, **kwargs: Any):
        client = make_client(entra_config(**kwargs))
        client.app.state.foundry.jwks.transport = httpx.MockTransport(server or JwksServer(keys))
        return client

    return _make


def bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}", "content-type": "application/json"}


def assert_invalid_token(response: httpx.Response) -> None:
    assert response.status_code == 401
    assert response.json() == {"error": {"code": "401", "message": INVALID_TOKEN_MESSAGE}}


@pytest.mark.contract("AOAI-ENTRA-AUTH")
def test_valid_token_with_role_is_accepted(entra_client, keys):
    client = entra_client()
    response = client.post(CHAT_PATH, headers=bearer(keys.token()), json=chat_body("hello"))
    assert response.status_code == 200
    embeddings = client.post(EMBEDDINGS_PATH, headers=bearer(keys.token()), json={"input": "hello"})
    assert embeddings.status_code == 200
    v1 = client.post(
        "/openai/v1/chat/completions", headers=bearer(keys.token()), json=chat_body("hi", model="gpt-demo")
    )
    assert v1.status_code == 200


@pytest.mark.contract("AOAI-ENTRA-AUTH")
def test_principal_falls_back_to_sub(entra_client, keys):
    client = entra_client(roles={"app-sub": ["Azure AI User"]})
    token = keys.token(oid=None, sub="app-sub")
    assert client.post(CHAT_PATH, headers=bearer(token), json=chat_body("hello")).status_code == 200


@pytest.mark.contract("AOAI-ENTRA-AUTH")
def test_valid_token_without_role_is_permission_denied(entra_client, keys):
    client = entra_client(roles={"someone-else": ["Cognitive Services OpenAI User"]})
    response = client.post(CHAT_PATH, headers=bearer(keys.token()), json=chat_body("hello"))
    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "PermissionDenied"
    assert response.headers["x-ms-error-code"] == "PermissionDenied"
    assert "`system-assigned`" in error["message"]
    assert "Microsoft.CognitiveServices/accounts/OpenAI/deployments/chat/completions/action" in error["message"]
    assert "POST /openai/deployments/gpt-demo/chat/completions" in error["message"]


@pytest.mark.contract("AOAI-ENTRA-AUTH")
def test_roles_are_scoped_per_surface(entra_client, keys):
    openai_user = entra_client()
    denied = openai_user.post(ANALYZE_PATH, headers=bearer(keys.token()), json={"text": "hello"})
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "PermissionDenied"
    language_denied = openai_user.post(LANGUAGE_PATH, headers=bearer(keys.token()), json={})
    assert language_denied.json()["error"]["code"] == "PermissionDenied"

    cognitive_user = entra_client(roles={"system-assigned": ["Cognitive Services User"]})
    assert cognitive_user.post(ANALYZE_PATH, headers=bearer(keys.token()), json={"text": "hello"}).status_code == 200
    assert cognitive_user.post(CHAT_PATH, headers=bearer(keys.token()), json=chat_body("hello")).status_code == 200


@pytest.mark.contract("AOAI-ENTRA-AUTH")
@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "https://management.azure.com"},
        {"iss": "http://evil.example/.well-known/workload-identity"},
        {"exp": int(time.time()) - 3600, "nbf": int(time.time()) - 7200, "iat": int(time.time()) - 7200},
        {"nbf": int(time.time()) + 3600},
        {"exp": None},
    ],
    ids=["wrong-aud", "wrong-iss", "expired", "not-yet-valid", "no-exp"],
)
def test_invalid_tokens_are_rejected(entra_client, keys, overrides):
    client = entra_client()
    response = client.post(CHAT_PATH, headers=bearer(keys.token(**overrides)), json=chat_body("hello"))
    assert_invalid_token(response)


@pytest.mark.contract("AOAI-ENTRA-AUTH")
def test_small_clock_skew_is_tolerated(entra_client, keys):
    client = entra_client()
    token = keys.token(exp=int(time.time()) - 10)
    assert client.post(CHAT_PATH, headers=bearer(token), json=chat_body("hello")).status_code == 200


@pytest.mark.contract("AOAI-ENTRA-AUTH")
def test_alg_none_is_rejected(entra_client, keys):
    client = entra_client()
    claims = jwt.decode(keys.token(), options={"verify_signature": False})
    token = _unsigned({"alg": "none", "typ": "JWT", "kid": keys.kid}, claims) + "."
    assert_invalid_token(client.post(CHAT_PATH, headers=bearer(token), json=chat_body("hello")))


@pytest.mark.contract("AOAI-ENTRA-AUTH")
def test_hs256_signed_with_public_key_is_rejected(entra_client, keys):
    client = entra_client()
    claims = jwt.decode(keys.token(), options={"verify_signature": False})
    signing_input = _unsigned({"alg": "HS256", "typ": "JWT", "kid": keys.kid}, claims)
    signature = hmac.new(keys.public_pem(), signing_input.encode(), hashlib.sha256).digest()
    token = f"{signing_input}.{_b64(signature)}"
    assert_invalid_token(client.post(CHAT_PATH, headers=bearer(token), json=chat_body("hello")))


@pytest.mark.contract("AOAI-ENTRA-AUTH")
def test_token_signed_by_untrusted_key_is_rejected(entra_client, keys):
    client = entra_client()
    impostor = Keypair(keys.kid)
    assert_invalid_token(client.post(CHAT_PATH, headers=bearer(impostor.token()), json=chat_body("hello")))


@pytest.mark.contract("AOAI-ENTRA-AUTH")
def test_non_jwt_bearer_still_uses_key_path(entra_client):
    client = entra_client()
    assert client.post(CHAT_PATH, headers=bearer(API_KEY), json=chat_body("hello")).status_code == 200
    rejected = client.post(CHAT_PATH, headers=bearer("not-a-key"), json=chat_body("hello"))
    assert rejected.status_code == 401
    assert rejected.json()["error"]["code"] == "401"
    assert "invalid subscription key" in rejected.json()["error"]["message"]


@pytest.mark.contract("AOAI-ENTRA-AUTH")
def test_jwt_bearer_is_a_key_when_entra_disabled(make_client, keys):
    client = make_client()
    response = client.post(CHAT_PATH, headers=bearer(keys.token()), json=chat_body("hello"))
    assert response.status_code == 401
    assert "invalid subscription key" in response.json()["error"]["message"]


@pytest.mark.contract("AOAI-ENTRA-AUTH")
def test_tokens_never_reach_management_plane(entra_client, keys):
    client = entra_client(roles={"system-assigned": ["Cognitive Services OpenAI Contributor"]})
    assert client.get("/foundry/management/status", headers=bearer(keys.token())).status_code == 401
    assert client.get("/foundry/management/status", headers=ADMIN_HEADERS).status_code == 200


@pytest.mark.contract("AOAI-DISABLE-LOCAL-AUTH")
def test_disable_local_auth_blocks_keys_but_allows_tokens(entra_client, keys):
    client = entra_client(disable_local_auth=True, roles={"system-assigned": ["Cognitive Services User"]})
    for headers in (
        {"api-key": API_KEY},
        bearer(API_KEY),
    ):
        response = client.post(CHAT_PATH, headers=headers, json=chat_body("hello"))
        assert response.status_code == 401
        assert "invalid subscription key" in response.json()["error"]["message"]
    language = client.post(LANGUAGE_PATH, headers={"Ocp-Apim-Subscription-Key": API_KEY}, json={})
    assert language.status_code == 401
    safety = client.post(ANALYZE_PATH, headers={"api-key": API_KEY}, json={"text": "hello"})
    assert safety.status_code == 401
    assert client.post(CHAT_PATH, headers=bearer(keys.token()), json=chat_body("hello")).status_code == 200


@pytest.mark.contract("AOAI-ENTRA-AUTH")
def test_jwks_is_cached(entra_client, keys):
    server = JwksServer(keys)
    client = entra_client(server)
    for _ in range(3):
        assert client.post(CHAT_PATH, headers=bearer(keys.token()), json=chat_body("hello")).status_code == 200
    assert server.requests == 1


@pytest.mark.contract("AOAI-ENTRA-AUTH")
def test_unknown_kid_refetches_once_then_fails_closed(entra_client, keys):
    server = JwksServer(keys)
    client = entra_client(server)
    assert client.post(CHAT_PATH, headers=bearer(keys.token()), json=chat_body("hello")).status_code == 200
    assert server.requests == 1

    stranger = Keypair("kid-unknown")
    assert_invalid_token(client.post(CHAT_PATH, headers=bearer(stranger.token()), json=chat_body("hello")))
    assert server.requests == 2

    rotated = Keypair("kid-two")
    server.keypairs.append(rotated)
    assert client.post(CHAT_PATH, headers=bearer(rotated.token()), json=chat_body("hello")).status_code == 200
    assert server.requests == 3


@pytest.mark.contract("AOAI-ENTRA-AUTH")
def test_jwks_network_error_fails_closed_with_401(entra_client, keys):
    server = JwksServer(keys)
    server.fail = True
    client = entra_client(server)
    assert_invalid_token(client.post(CHAT_PATH, headers=bearer(keys.token()), json=chat_body("hello")))


@pytest.mark.contract("AOAI-ENTRA-AUTH")
def test_public_key_file_issuer_needs_no_network(entra_client, keys, tmp_path):
    key_file = tmp_path / "issuer.pub.pem"
    key_file.write_bytes(keys.public_pem())
    server = JwksServer(keys)
    server.fail = True
    client = entra_client(server, issuer={"issuer": ISSUER, "public_key_file": str(key_file)})
    assert client.post(CHAT_PATH, headers=bearer(keys.token()), json=chat_body("hello")).status_code == 200
    assert server.requests == 0
    assert_invalid_token(client.post(CHAT_PATH, headers=bearer(Keypair("x").token()), json=chat_body("hello")))


def _write(tmp_path, document: dict[str, Any]):
    path = tmp_path / "foundry.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda c: c["auth"]["entra"]["trusted_issuers"][0].update(public_key_file="/etc/hosts"),
            "exactly one of jwks_uri or public_key_file",
        ),
        (
            lambda c: c["auth"]["entra"]["trusted_issuers"][0].pop("jwks_uri"),
            "exactly one of jwks_uri or public_key_file",
        ),
        (lambda c: c["auth"]["entra"].update(trusted_issuers=[]), "at least one issuer"),
        (lambda c: c["auth"]["entra"].update(audiences=[]), "at least one audience"),
        (lambda c: c["auth"]["entra"].update(role_assignments={"x": "Azure AI User"}), "role_assignments.x"),
        (
            lambda c: c["auth"]["entra"]["trusted_issuers"][0].update(jwks_uri="file:///etc/passwd"),
            "http(s) URL",
        ),
        (
            lambda c: c["auth"]["entra"]["trusted_issuers"].__setitem__(
                0, {"issuer": ISSUER, "public_key_file": "/nonexistent/key.pem"}
            ),
            "does not exist",
        ),
        (lambda c: c["auth"]["entra"].update(enabled=False) or c["auth"].update(disable_local_auth=True), "requires"),
        (lambda c: c["auth"].update(disable_local_auth="yes"), "disable_local_auth must be a boolean"),
    ],
)
def test_entra_config_validation(tmp_path, mutate, message):
    config = entra_config()
    mutate(config)
    with pytest.raises(ConfigError, match=re.escape(message)):
        load_config(_write(tmp_path, config))


def test_entra_defaults_off_and_keys_optional_when_enabled(tmp_path):
    assert load_config(_write(tmp_path, base_config())).auth.entra.enabled is False
    config = entra_config(disable_local_auth=True)
    config["auth"]["api_keys"] = []
    loaded = load_config(_write(tmp_path, config)).auth
    assert loaded.disable_local_auth is True
    assert loaded.entra.role_assignments == {"system-assigned": ("Cognitive Services OpenAI User",)}

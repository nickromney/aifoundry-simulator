"""Azure-shaped error payload helpers.

Each helper mirrors the wire shape the corresponding real Azure surface
returns so client code and gateway policies exercised against the simulator
handle the same failure bodies they would see in Azure.
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def openai_error(
    status_code: int,
    message: str,
    *,
    code: str | None = None,
    error_type: str | None = "invalid_request_error",
    param: str | None = None,
) -> JSONResponse:
    """OpenAI-style error envelope used by chat/embeddings/responses surfaces."""
    body: dict[str, Any] = {"error": {"message": message, "type": error_type, "param": param, "code": code}}
    return JSONResponse(status_code=status_code, content=body)


def azure_resource_error(status_code: int, code: str, message: str) -> JSONResponse:
    """Azure resource-style error envelope (auth failures, missing api-version)."""
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


def missing_api_version() -> JSONResponse:
    return azure_resource_error(
        400,
        "MissingApiVersionParameter",
        "The api-version query parameter (?api-version=) is required for all requests.",
    )


def deployment_not_found(deployment: str) -> JSONResponse:
    return azure_resource_error(
        404,
        "DeploymentNotFound",
        f"The API deployment for this resource does not exist. Deployment '{deployment}' is not "
        "configured in the simulator config. Check /foundry/management/deployments for the loaded set.",
    )


def unauthorized() -> JSONResponse:
    return azure_resource_error(
        401,
        "401",
        "Access denied due to invalid subscription key or wrong API endpoint. Make sure to provide a "
        "valid key in the 'api-key' header (or 'Authorization: Bearer') for an active deployment.",
    )


def content_filter_error(content_filter_result: dict[str, Any]) -> JSONResponse:
    """The documented Azure OpenAI 400 returned when the prompt trips the content filter."""
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "message": (
                    "The response was filtered due to the prompt triggering Azure OpenAI's content "
                    "management policy. Please modify your prompt and retry. To learn more about our "
                    "content filtering policies please read our documentation."
                ),
                "type": None,
                "param": "prompt",
                "code": "content_filter",
                "status": 400,
                "innererror": {
                    "code": "ResponsibleAIPolicyViolation",
                    "content_filter_result": content_filter_result,
                },
            }
        },
    )


def content_safety_error(status_code: int, code: str, message: str) -> JSONResponse:
    """Azure AI Content Safety error envelope."""
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})

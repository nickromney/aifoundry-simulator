"""Azure AI Language ``analyze-text`` surface for PII entity recognition.

Serves ``POST /language/:analyze-text`` with ``kind: PiiEntityRecognition``,
the call APIM makes from a ``send-request`` policy to find and redact
personal data before a request reaches a model. Request and response shapes
follow the documented REST surface; detection itself is deterministic (see
``app/pii.py`` and docs/PII.md). Other ``analyze-text`` kinds are rejected
rather than silently accepted.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app import errors
from app.auth import is_authorized
from app.pii import (
    CATEGORIES,
    MODEL_VERSION,
    POLICY_KINDS,
    REDACTION_CHARACTERS,
    RedactionPolicy,
    canonical_category,
    detect,
    redact,
)
from app.state import AppState

router = APIRouter(prefix="/language")

LANGUAGE_API_VERSIONS = frozenset({"2026-05-01", "2025-11-15-preview"})
# Microsoft's Language data limits for synchronous preconfigured features.
MAX_DOCUMENT_TEXT_ELEMENTS = 5_120
MAX_DOCUMENTS = 5


def _state(request: Request) -> AppState:
    return request.app.state.foundry


def _invalid(message: str, inner_code: str = "InvalidParameterValue") -> JSONResponse:
    return errors.language_error(400, "InvalidRequest", message, inner_code=inner_code)


def _gates(request: Request) -> JSONResponse | None:
    if not is_authorized(request, _state(request).config, subscription_key_header=True):
        return errors.language_error(
            401,
            "401",
            "Access denied due to invalid subscription key or wrong API endpoint. Make sure to provide a valid "
            "key in the 'Ocp-Apim-Subscription-Key' header (or 'api-key' / 'Authorization: Bearer').",
        )
    version = request.query_params.get("api-version")
    if not version:
        return errors.language_error(
            400,
            "MissingApiVersionParameter",
            "The api-version query parameter (?api-version=) is required for all requests.",
        )
    if version not in LANGUAGE_API_VERSIONS:
        return errors.language_error(
            400,
            "InvalidApiVersionParameter",
            f"Unsupported api-version '{version}'. The Language surface supports "
            f"{', '.join(sorted(LANGUAGE_API_VERSIONS))}.",
        )
    return None


def _parse_policies(raw: Any) -> list[RedactionPolicy] | JSONResponse:
    if raw is None:
        return [RedactionPolicy()]
    items = [raw] if isinstance(raw, dict) else raw
    if not isinstance(items, list) or not items or not all(isinstance(item, dict) for item in items):
        return _invalid("'redactionPolicies' must be an object or a non-empty list of objects.")
    policies: list[RedactionPolicy] = []
    for item in items:
        kind = item.get("policyKind", "characterMask")
        if kind not in POLICY_KINDS:
            return _invalid(f"Unsupported policyKind '{kind}'. The simulator supports {', '.join(POLICY_KINDS)}.")
        character = item.get("redactionCharacter", "*")
        if not isinstance(character, str) or character not in REDACTION_CHARACTERS:
            return _invalid(f"'redactionCharacter' must be one of {' '.join(sorted(REDACTION_CHARACTERS))}.")
        entity_types_raw = item.get("entityTypes")
        entity_types: frozenset[str] | None = None
        if entity_types_raw is not None:
            if not isinstance(entity_types_raw, list) or not all(isinstance(e, str) for e in entity_types_raw):
                return _invalid("'entityTypes' must be a list of strings.")
            resolved = {canonical_category(name) for name in entity_types_raw}
            entity_types = frozenset(name for name in resolved if name is not None)
        policies.append(RedactionPolicy(kind=kind, character=character, entity_types=entity_types))
    return policies


def _document_error(document_id: str, message: str) -> dict[str, Any]:
    return {
        "id": document_id,
        "error": {
            "code": "InvalidArgument",
            "message": "Invalid document in request.",
            "innererror": {"code": "InvalidDocument", "message": message},
        },
    }


@router.post("/:analyze-text", response_model=None)
async def analyze_text(request: Request) -> Response:
    gate = _gates(request)
    if gate is not None:
        return gate
    try:
        body = await request.json()
    except Exception:
        return _invalid("Request body must be valid JSON.", "InvalidRequestBodyFormat")
    if not isinstance(body, dict):
        return _invalid("Request body must be a JSON object.", "InvalidRequestBodyFormat")
    if body.get("kind") != "PiiEntityRecognition":
        return _invalid(f"Unsupported kind '{body.get('kind')}'. The simulator implements only 'PiiEntityRecognition'.")

    parameters = body.get("parameters", {})
    if not isinstance(parameters, dict):
        return _invalid("'parameters' must be an object.")
    model_version = parameters.get("modelVersion", "latest")
    if model_version not in ("latest", MODEL_VERSION):
        return _invalid(f"Unsupported modelVersion '{model_version}'. Use 'latest' or '{MODEL_VERSION}'.")
    categories: set[str] | None = None
    pii_categories = parameters.get("piiCategories")
    if pii_categories is not None:
        if not isinstance(pii_categories, list) or not all(isinstance(item, str) for item in pii_categories):
            return _invalid("'piiCategories' must be a list of strings.")
        categories = set()
        for item in pii_categories:
            canonical = canonical_category(item.strip())
            if canonical is None:
                return _invalid(
                    f"Unsupported piiCategories value '{item}'. The simulator detects {', '.join(CATEGORIES)}."
                )
            categories.add(canonical)
    policies = _parse_policies(parameters.get("redactionPolicies"))
    if isinstance(policies, JSONResponse):
        return policies

    analysis_input = body.get("analysisInput")
    documents = analysis_input.get("documents") if isinstance(analysis_input, dict) else None
    if not isinstance(documents, list) or not documents:
        return _invalid("'analysisInput.documents' must be a non-empty list.", "MissingInputDocuments")
    if len(documents) > MAX_DOCUMENTS:
        return _invalid(
            f"Batch request contains too many records. Max {MAX_DOCUMENTS} records are permitted.",
            "InvalidDocumentBatch",
        )
    seen_ids: set[str] = set()
    for document in documents:
        if not isinstance(document, dict):
            return _invalid("Each document must be an object.", "InvalidDocument")
        document_id = document.get("id")
        if not isinstance(document_id, str) or document_id == "":
            return _invalid("Each document must have a non-empty string 'id'.", "InvalidDocument")
        if document_id in seen_ids:
            return _invalid("Request contains duplicated document ids. Make sure each document has a unique id.")
        seen_ids.add(document_id)
        if not isinstance(document.get("text"), str):
            return _invalid(f"Document '{document_id}' must have a string 'text'.", "InvalidDocument")
        language = document.get("language")
        if language is not None and not isinstance(language, str):
            return _invalid(f"Document '{document_id}' 'language' must be a string.", "InvalidDocument")

    simulation_triggers = _state(request).config.simulation_triggers
    results: list[dict[str, Any]] = []
    document_errors: list[dict[str, Any]] = []
    for document in documents:
        document_id = document["id"]
        text = document["text"]
        if text.strip() == "":
            document_errors.append(_document_error(document_id, "Document text is empty."))
            continue
        if len(text) > MAX_DOCUMENT_TEXT_ELEMENTS:
            document_errors.append(
                _document_error(
                    document_id,
                    "A document within the request was too large to be processed. Limit document size to: "
                    f"{MAX_DOCUMENT_TEXT_ELEMENTS} text elements.",
                )
            )
            continue
        entities = detect(text, categories, simulation_triggers=simulation_triggers)
        result: dict[str, Any] = {"id": document_id}
        redacted = redact(text, entities, policies)
        if redacted is not None:
            result["redactedText"] = redacted
        result["entities"] = [entity.payload() for entity in entities]
        result["warnings"] = []
        results.append(result)

    return JSONResponse(
        content={
            "kind": "PiiEntityRecognitionResults",
            "results": {"documents": results, "errors": document_errors, "modelVersion": MODEL_VERSION},
        }
    )

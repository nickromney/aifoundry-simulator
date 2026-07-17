"""Azure AI Content Safety service surface.

Implements the text endpoints the APIM ``llm-content-safety`` policy and the
Content Safety SDKs call: ``text:analyze``, ``text:shieldPrompt``, and text
blocklist management. Paths, payload shapes, and the ``api-version``
requirement follow the 2024-09-01 REST surface.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app import errors
from app.auth import is_authorized
from app.config import Blocklist, BlocklistItem
from app.content_safety import (
    AZURE_CATEGORY_NAMES,
    analyze_text,
    detect_jailbreak,
    match_blocklists,
    to_four_level,
)
from app.state import AppState

router = APIRouter(prefix="/contentsafety")

MAX_TEXT_LENGTH = 10_000


def _state(request: Request) -> AppState:
    return request.app.state.foundry


def _gates(request: Request) -> JSONResponse | None:
    if not is_authorized(request, _state(request).config):
        return errors.content_safety_error(
            401,
            "Unauthorized",
            "Access denied due to invalid subscription key. Provide a valid key in the 'api-key' header.",
        )
    if not request.query_params.get("api-version"):
        return errors.content_safety_error(
            400,
            "MissingApiVersionParameter",
            "The api-version query parameter (?api-version=) is required for all requests.",
        )
    return None


async def _json_body(request: Request) -> dict[str, Any] | JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return errors.content_safety_error(400, "InvalidRequestBody", "Request body must be valid JSON.")
    if not isinstance(body, dict):
        return errors.content_safety_error(400, "InvalidRequestBody", "Request body must be a JSON object.")
    return body


@router.post("/text:analyze", response_model=None)
async def analyze(request: Request) -> Response:
    gate = _gates(request)
    if gate is not None:
        return gate
    body = await _json_body(request)
    if isinstance(body, JSONResponse):
        return body
    state = _state(request)
    text = body.get("text")
    if not isinstance(text, str) or text == "":
        return errors.content_safety_error(400, "InvalidRequestBody", "'text' is required and must be a string.")
    if len(text) > MAX_TEXT_LENGTH:
        return errors.content_safety_error(
            400, "InvalidRequestBody", f"'text' must be at most {MAX_TEXT_LENGTH} characters."
        )
    categories = body.get("categories")
    if categories is not None:
        if not isinstance(categories, list) or not all(isinstance(item, str) for item in categories):
            return errors.content_safety_error(400, "InvalidRequestBody", "'categories' must be a list of strings.")
        known = {name.lower() for name in AZURE_CATEGORY_NAMES.values()}
        for item in categories:
            if item.lower() not in known:
                return errors.content_safety_error(
                    400,
                    "InvalidRequestBody",
                    f"Unknown category '{item}'. Expected one of Hate, SelfHarm, Sexual, Violence.",
                )
    output_type = body.get("outputType", "FourSeverityLevels")
    if output_type not in ("FourSeverityLevels", "EightSeverityLevels"):
        return errors.content_safety_error(
            400, "InvalidRequestBody", "'outputType' must be 'FourSeverityLevels' or 'EightSeverityLevels'."
        )
    blocklist_names = body.get("blocklistNames", [])
    if not isinstance(blocklist_names, list) or not all(isinstance(item, str) for item in blocklist_names):
        return errors.content_safety_error(400, "InvalidRequestBody", "'blocklistNames' must be a list of strings.")
    blocklists: list[Blocklist] = []
    for name in blocklist_names:
        blocklist = state.blocklists.get(name)
        if blocklist is None:
            return errors.content_safety_error(404, "NotFound", f"Blocklist '{name}' was not found.")
        blocklists.append(blocklist)
    halt_on_blocklist_hit = body.get("haltOnBlocklistHit", False)
    if not isinstance(halt_on_blocklist_hit, bool):
        return errors.content_safety_error(400, "InvalidRequestBody", "'haltOnBlocklistHit' must be a boolean.")

    state.safety_stats.record_analyze()
    matches = match_blocklists(text, blocklists)
    blocklists_match = [
        {
            "blocklistName": match.blocklist_name,
            "blocklistItemId": match.item_id,
            "blocklistItemText": match.item_text,
        }
        for match in matches
    ]
    if matches and halt_on_blocklist_hit:
        return JSONResponse(content={"blocklistsMatch": blocklists_match, "categoriesAnalysis": []})

    analyses = analyze_text(text, state.config, categories=categories)
    categories_analysis = [
        {
            "category": AZURE_CATEGORY_NAMES[analysis.category],
            "severity": analysis.severity if output_type == "EightSeverityLevels" else to_four_level(analysis.severity),
        }
        for analysis in analyses.values()
    ]
    return JSONResponse(content={"blocklistsMatch": blocklists_match, "categoriesAnalysis": categories_analysis})


@router.post("/text:shieldPrompt", response_model=None)
async def shield_prompt(request: Request) -> Response:
    gate = _gates(request)
    if gate is not None:
        return gate
    body = await _json_body(request)
    if isinstance(body, JSONResponse):
        return body
    state = _state(request)
    user_prompt = body.get("userPrompt")
    documents = body.get("documents", [])
    if user_prompt is not None and not isinstance(user_prompt, str):
        return errors.content_safety_error(400, "InvalidRequestBody", "'userPrompt' must be a string.")
    if not isinstance(documents, list) or not all(isinstance(item, str) for item in documents):
        return errors.content_safety_error(400, "InvalidRequestBody", "'documents' must be a list of strings.")
    if user_prompt is None and not documents:
        return errors.content_safety_error(
            400, "InvalidRequestBody", "Provide 'userPrompt' and/or 'documents' to analyze."
        )
    prompt_attack = detect_jailbreak(user_prompt, state.config) if user_prompt is not None else False
    documents_analysis = [{"attackDetected": detect_jailbreak(document, state.config)} for document in documents]
    state.safety_stats.record_shield(prompt_attack or any(item["attackDetected"] for item in documents_analysis))
    payload: dict[str, Any] = {"documentsAnalysis": documents_analysis}
    if user_prompt is not None:
        payload["userPromptAnalysis"] = {"attackDetected": prompt_attack}
    return JSONResponse(content=payload)


# --- Blocklist management -------------------------------------------------


def _blocklist_summary(blocklist: Blocklist) -> dict[str, str]:
    return {"blocklistName": blocklist.name, "description": blocklist.description}


def _item_payload(item: BlocklistItem) -> dict[str, Any]:
    return {
        "blocklistItemId": item.item_id,
        "description": item.description,
        "text": item.text,
        "isRegex": item.is_regex,
    }


@router.post("/text/blocklists/{name}:addOrUpdateBlocklistItems", response_model=None)
async def add_or_update_blocklist_items(name: str, request: Request) -> Response:
    gate = _gates(request)
    if gate is not None:
        return gate
    state = _state(request)
    blocklist = state.blocklists.get(name)
    if blocklist is None:
        return errors.content_safety_error(404, "NotFound", f"Blocklist '{name}' was not found.")
    body = await _json_body(request)
    if isinstance(body, JSONResponse):
        return body
    items_raw = body.get("blocklistItems")
    if not isinstance(items_raw, list) or not items_raw:
        return errors.content_safety_error(
            400, "InvalidRequestBody", "'blocklistItems' is required and must be a non-empty list."
        )
    added: list[BlocklistItem] = []
    for item_raw in items_raw:
        if not isinstance(item_raw, dict) or not isinstance(item_raw.get("text"), str) or item_raw["text"] == "":
            return errors.content_safety_error(
                400, "InvalidRequestBody", "Each blocklist item must be an object with a non-empty 'text'."
            )
        item = BlocklistItem(
            item_id=uuid.uuid4().hex,
            text=item_raw["text"],
            is_regex=bool(item_raw.get("isRegex", False)),
            description=str(item_raw.get("description", "")),
        )
        blocklist.items.append(item)
        added.append(item)
    return JSONResponse(content={"blocklistItems": [_item_payload(item) for item in added]})


@router.post("/text/blocklists/{name}:removeBlocklistItems", response_model=None)
async def remove_blocklist_items(name: str, request: Request) -> Response:
    gate = _gates(request)
    if gate is not None:
        return gate
    state = _state(request)
    blocklist = state.blocklists.get(name)
    if blocklist is None:
        return errors.content_safety_error(404, "NotFound", f"Blocklist '{name}' was not found.")
    body = await _json_body(request)
    if isinstance(body, JSONResponse):
        return body
    item_ids = body.get("blocklistItemIds")
    if not isinstance(item_ids, list) or not all(isinstance(item, str) for item in item_ids):
        return errors.content_safety_error(400, "InvalidRequestBody", "'blocklistItemIds' must be a list of strings.")
    blocklist.items = [item for item in blocklist.items if item.item_id not in set(item_ids)]
    return Response(status_code=204)


@router.get("/text/blocklists/{name}/blocklistItems", response_model=None)
async def list_blocklist_items(name: str, request: Request) -> Response:
    gate = _gates(request)
    if gate is not None:
        return gate
    blocklist = _state(request).blocklists.get(name)
    if blocklist is None:
        return errors.content_safety_error(404, "NotFound", f"Blocklist '{name}' was not found.")
    return JSONResponse(content={"value": [_item_payload(item) for item in blocklist.items]})


@router.get("/text/blocklists", response_model=None)
async def list_blocklists(request: Request) -> Response:
    gate = _gates(request)
    if gate is not None:
        return gate
    blocklists = _state(request).blocklists
    return JSONResponse(content={"value": [_blocklist_summary(blocklist) for blocklist in blocklists.values()]})


@router.get("/text/blocklists/{name}", response_model=None)
async def get_blocklist(name: str, request: Request) -> Response:
    gate = _gates(request)
    if gate is not None:
        return gate
    blocklist = _state(request).blocklists.get(name)
    if blocklist is None:
        return errors.content_safety_error(404, "NotFound", f"Blocklist '{name}' was not found.")
    return JSONResponse(content=_blocklist_summary(blocklist))


@router.patch("/text/blocklists/{name}", response_model=None)
async def create_or_update_blocklist(name: str, request: Request) -> Response:
    gate = _gates(request)
    if gate is not None:
        return gate
    state = _state(request)
    body = await _json_body(request)
    if isinstance(body, JSONResponse):
        return body
    description = body.get("description", "")
    if not isinstance(description, str):
        return errors.content_safety_error(400, "InvalidRequestBody", "'description' must be a string.")
    existing = state.blocklists.get(name)
    if existing is None:
        state.blocklists[name] = Blocklist(name=name, description=description)
        return JSONResponse(status_code=201, content=_blocklist_summary(state.blocklists[name]))
    existing.description = description
    return JSONResponse(content=_blocklist_summary(existing))


@router.delete("/text/blocklists/{name}", response_model=None)
async def delete_blocklist(name: str, request: Request) -> Response:
    gate = _gates(request)
    if gate is not None:
        return gate
    state = _state(request)
    if name not in state.blocklists:
        return errors.content_safety_error(404, "NotFound", f"Blocklist '{name}' was not found.")
    referencing = [policy.name for policy in state.config.content_filters.values() if name in policy.blocklists]
    if referencing:
        return errors.content_safety_error(
            409,
            "Conflict",
            f"Blocklist '{name}' is referenced by content filter(s) {referencing} in the loaded config; "
            "remove the reference from the config file instead of deleting at runtime.",
        )
    del state.blocklists[name]
    return Response(status_code=204)

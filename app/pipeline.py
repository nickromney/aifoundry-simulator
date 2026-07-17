"""Inference request pipeline.

Order of operations mirrors the real service: authentication happens in the
router, then per-deployment content filtering (prompt side), then the
semantic cache lookup, then simulated model latency and generation, then
output-side filtering and annotations, then cache store. Cache lookups run
before the latency simulation so hits return visibly faster than misses —
the behaviour semantic caching exists to demonstrate.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from app import engine, errors
from app.config import Deployment
from app.content_safety import apply_filter_policy
from app.embeddings import embed_text
from app.state import AppState

REGION_HEADER_VALUE = "local-simulator"


def _base_headers() -> dict[str, str]:
    return {"x-request-id": uuid.uuid4().hex, "x-ms-region": REGION_HEADER_VALUE}


def _latency_ms(deployment: Deployment) -> int:
    override = os.getenv("FOUNDRY_LATENCY_MS")
    if override is not None and override.isdigit():
        return int(override)
    return deployment.latency_ms


async def _read_json_body(request: Request) -> dict[str, Any] | JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return errors.openai_error(400, "Request body must be valid JSON.")
    if not isinstance(body, dict):
        return errors.openai_error(400, "Request body must be a JSON object.")
    return body


def _wants_stream_usage(body: dict[str, Any]) -> bool:
    stream_options = body.get("stream_options")
    return isinstance(stream_options, dict) and stream_options.get("include_usage") is True


def _respond(payload: dict[str, Any], *, stream: bool, include_usage: bool, headers: dict[str, str]) -> Response:
    if stream:
        return StreamingResponse(
            iter(engine.sse_chunks(payload, include_usage=include_usage)),
            media_type="text/event-stream",
            headers=headers,
        )
    return JSONResponse(content=payload, headers=headers)


async def run_chat_completion(state: AppState, deployment: Deployment, request: Request) -> Response:
    body = await _read_json_body(request)
    if isinstance(body, JSONResponse):
        return body
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return errors.openai_error(400, "'messages' is required and must be a non-empty array.", param="messages")
    stream = body.get("stream") is True
    include_usage = _wants_stream_usage(body)
    max_tokens = body.get("max_tokens") if isinstance(body.get("max_tokens"), int) else None
    if max_tokens is None and isinstance(body.get("max_completion_tokens"), int):
        max_tokens = body["max_completion_tokens"]

    headers = _base_headers()
    prompt_text = engine.prompt_text_from_messages(messages)
    policy = state.config.filter_policy_for(deployment)

    prompt_outcome = None
    if policy is not None:
        prompt_outcome = apply_filter_policy(prompt_text, policy, state.config)
        state.safety_stats.record_prompt_check(deployment.name, prompt_outcome)
        if prompt_outcome.blocked:
            response = errors.content_filter_error(prompt_outcome.content_filter_result(include_jailbreak=True))
            for key, value in headers.items():
                response.headers[key] = value
            return response

    cache = state.config.cache_settings_for(deployment)
    cache_vector: list[float] | None = None
    if cache.enabled and cache.embeddings_deployment is not None:
        embeddings_deployment = state.config.deployments[cache.embeddings_deployment]
        cache_vector = embed_text(prompt_text, embeddings_deployment.dimensions)
        result = state.semantic_cache.lookup(
            deployment.name,
            cache_vector,
            score_threshold=cache.score_threshold,
            ttl_seconds=cache.ttl_seconds,
            prompt_text=prompt_text,
        )
        if result is not None:
            headers["x-semantic-cache"] = "hit"
            headers["x-semantic-cache-score"] = f"{result.distance:.6f}"
            headers["age"] = str(int(result.age_seconds))
            return _respond(result.entry.payload, stream=stream, include_usage=include_usage, headers=headers)
        headers["x-semantic-cache"] = "miss"

    latency = _latency_ms(deployment)
    if latency > 0:
        await asyncio.sleep(latency / 1000)

    payload = engine.chat_completion_payload(model=deployment.model, messages=messages, max_tokens=max_tokens)

    output_filtered = False
    if policy is not None:
        completion = payload["choices"][0]["message"]["content"]
        if policy.output_filter:
            output_outcome = apply_filter_policy(completion, policy, state.config, is_output=True)
            state.safety_stats.record_output_check(deployment.name, output_outcome)
            if output_outcome.blocked:
                output_filtered = True
                payload["choices"][0]["message"]["content"] = ""
                payload["choices"][0]["finish_reason"] = "content_filter"
            payload["choices"][0]["content_filter_results"] = output_outcome.content_filter_result(
                include_jailbreak=False
            )
        if prompt_outcome is not None:
            payload["prompt_filter_results"] = [
                {
                    "prompt_index": 0,
                    "content_filter_results": prompt_outcome.content_filter_result(include_jailbreak=True),
                }
            ]

    if cache.enabled and cache_vector is not None and not output_filtered:
        state.semantic_cache.store(
            deployment.name,
            cache_vector,
            prompt_text,
            payload,
            ttl_seconds=cache.ttl_seconds,
            max_entries=cache.max_entries,
        )

    return _respond(payload, stream=stream, include_usage=include_usage, headers=headers)


async def run_embeddings(state: AppState, deployment: Deployment, request: Request) -> Response:
    body = await _read_json_body(request)
    if isinstance(body, JSONResponse):
        return body
    raw_input = body.get("input")
    if isinstance(raw_input, str):
        inputs = [raw_input]
    elif isinstance(raw_input, list) and raw_input and all(isinstance(item, str) for item in raw_input):
        inputs = raw_input
    elif isinstance(raw_input, list) and raw_input:
        return errors.openai_error(
            400,
            "Token array inputs are not supported by the simulator; send a string or an array of strings.",
            param="input",
        )
    else:
        return errors.openai_error(400, "'input' is required (string or array of strings).", param="input")
    encoding_format = body.get("encoding_format", "float")
    if encoding_format not in ("float", "base64"):
        return errors.openai_error(400, "'encoding_format' must be 'float' or 'base64'.", param="encoding_format")
    dimensions = deployment.dimensions
    if isinstance(body.get("dimensions"), int):
        requested = body["dimensions"]
        if not 8 <= requested <= 4096:
            return errors.openai_error(400, "'dimensions' must be between 8 and 4096.", param="dimensions")
        dimensions = requested

    latency = _latency_ms(deployment)
    if latency > 0:
        await asyncio.sleep(latency / 1000)

    payload = engine.embeddings_payload(
        model=deployment.model,
        inputs=inputs,
        dimensions=dimensions,
        encoding_format=encoding_format,
    )
    return JSONResponse(content=payload, headers=_base_headers())


async def run_responses(state: AppState, deployment: Deployment, request: Request) -> Response:
    body = await _read_json_body(request)
    if isinstance(body, JSONResponse):
        return body
    if body.get("stream") is True:
        return errors.openai_error(
            400, "Streaming is not supported on the simulator's Responses surface yet.", param="stream"
        )
    raw_input = body.get("input")
    if isinstance(raw_input, str):
        messages: list[Any] = [{"role": "user", "content": raw_input}]
    elif isinstance(raw_input, list):
        messages = [item if isinstance(item, dict) else {"role": "user", "content": str(item)} for item in raw_input]
    else:
        return errors.openai_error(400, "'input' is required (string or array).", param="input")
    max_output_tokens = body.get("max_output_tokens") if isinstance(body.get("max_output_tokens"), int) else None

    headers = _base_headers()
    policy = state.config.filter_policy_for(deployment)
    if policy is not None:
        prompt_text = engine.prompt_text_from_messages(messages)
        outcome = apply_filter_policy(prompt_text, policy, state.config)
        state.safety_stats.record_prompt_check(deployment.name, outcome)
        if outcome.blocked:
            response = errors.content_filter_error(outcome.content_filter_result(include_jailbreak=True))
            for key, value in headers.items():
                response.headers[key] = value
            return response

    latency = _latency_ms(deployment)
    if latency > 0:
        await asyncio.sleep(latency / 1000)

    payload = engine.responses_payload(model=deployment.model, messages=messages, max_output_tokens=max_output_tokens)
    return JSONResponse(content=payload, headers=headers)

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
import json
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

_MESSAGE_ROLES = {"assistant", "developer", "system", "tool", "user"}
_MODEL_INFERENCE_EXTRA_PARAMETERS = {"error", "drop", "pass-through"}
_MODEL_INFERENCE_CHAT_FIELDS = {
    "frequency_penalty",
    "max_tokens",
    "messages",
    "model",
    "presence_penalty",
    "seed",
    "stop",
    "stream",
    "temperature",
    "top_p",
}
_MODEL_INFERENCE_EMBEDDING_FIELDS = {"dimensions", "encoding_format", "input", "input_type", "model"}
_UNSUPPORTED_CHAT_FIELDS = {
    "audio",
    "function_call",
    "functions",
    "logprobs",
    "modalities",
    "parallel_tool_calls",
    "prediction",
    "response_format",
    "tool_choice",
    "tools",
}
_UNSUPPORTED_RESPONSE_FIELDS = {
    "background",
    "conversation",
    "include",
    "parallel_tool_calls",
    "previous_response_id",
    "prompt",
    "reasoning",
    "tool_choice",
    "tools",
}


def _base_headers() -> dict[str, str]:
    return {"x-request-id": uuid.uuid4().hex, "x-ms-region": REGION_HEADER_VALUE}


def _latency_ms(deployment: Deployment) -> int:
    override = os.getenv("FOUNDRY_LATENCY_MS")
    if override is not None and override.isdigit():
        return int(override)
    return deployment.latency_ms


async def _read_json_body(request: Request, *, model_inference: bool = False) -> dict[str, Any] | JSONResponse:
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
    return body


def _validation_error(message: str, param: str, *, model_inference: bool) -> JSONResponse:
    if model_inference:
        return errors.model_inference_error(
            422,
            "invalid_parameter",
            "One of the parameters contain invalid values.",
            location=[param],
            input_value=message,
        )
    return errors.openai_error(400, message, param=param)


def _unsupported_parameter(
    parameter: str,
    value: Any,
    *,
    model_inference: bool,
) -> JSONResponse:
    if model_inference:
        detail_input = value
        if parameter == "response_format" and isinstance(value, dict) and isinstance(value.get("type"), str):
            detail_input = value["type"]
        return errors.model_inference_error(
            422,
            "parameter_not_supported",
            "One of the parameters contain invalid values.",
            location=[parameter],
            input_value=detail_input,
        )
    return errors.openai_error(
        400,
        f"The parameter '{parameter}' is not supported by the simulator.",
        param=parameter,
        code="parameter_not_supported",
    )


def _prepare_model_inference_body(
    body: dict[str, Any], supported: set[str], extra_parameters: str
) -> dict[str, Any] | JSONResponse:
    """Apply the documented ``extra-parameters`` policy before validation."""
    if extra_parameters not in _MODEL_INFERENCE_EXTRA_PARAMETERS:
        return _validation_error(
            "'extra-parameters' must be 'error', 'drop', or 'pass-through'.",
            "extra-parameters",
            model_inference=True,
        )
    if "extra-parameters" in body:
        return _unsupported_parameter("extra-parameters", body["extra-parameters"], model_inference=True)
    unsupported = [key for key in body if key != "extra-parameters" and key not in supported]
    if extra_parameters == "drop":
        return {key: value for key, value in body.items() if key not in unsupported}
    if unsupported:
        if extra_parameters == "pass-through":
            return _unsupported_parameter("extra-parameters", extra_parameters, model_inference=True)
        return _unsupported_parameter(unsupported[0], body[unsupported[0]], model_inference=True)
    return {key: value for key, value in body.items() if key != "extra-parameters"}


def _message_text(message: dict[str, Any], *, field: str = "messages") -> str | JSONResponse:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list) or not content:
        return _validation_error(
            f"'{field}' content must be a string or non-empty array.", field, model_inference=False
        )
    parts: list[str] = []
    for index, part in enumerate(content):
        if not isinstance(part, dict) or part.get("type") != "text":
            return _validation_error(
                f"Only text content is supported in {field}[{index}].",
                field,
                model_inference=False,
            )
        text = part.get("text")
        if not isinstance(text, str):
            return _validation_error(
                f"{field}[{index}].text must be a string.",
                field,
                model_inference=False,
            )
        parts.append(text)
    return "".join(parts)


def _validate_messages(
    messages: Any, *, model_inference: bool
) -> tuple[list[dict[str, Any]], list[str]] | JSONResponse:
    if not isinstance(messages, list) or not messages:
        return _validation_error(
            "'messages' is required and must be a non-empty array.", "messages", model_inference=model_inference
        )
    validated: list[dict[str, Any]] = []
    text_values: list[str] = []
    for index, message in enumerate(messages):
        field = f"messages.{index}"
        if not isinstance(message, dict):
            return _validation_error(f"{field} must be an object.", field, model_inference=model_inference)
        role = message.get("role")
        if not isinstance(role, str) or role not in _MESSAGE_ROLES:
            return _validation_error(
                f"{field}.role must be one of {sorted(_MESSAGE_ROLES)}.",
                f"{field}.role",
                model_inference=model_inference,
            )
        if role == "tool":
            return _unsupported_parameter("messages.role", role, model_inference=model_inference)
        if "tool_calls" in message:
            return _unsupported_parameter("messages.tool_calls", message["tool_calls"], model_inference=model_inference)
        text = _message_text(message, field=field)
        if isinstance(text, JSONResponse):
            if model_inference:
                return _unsupported_parameter(field, message.get("content"), model_inference=True)
            return text
        validated.append(message)
        text_values.append(text)
    return validated, text_values


def _positive_integer(value: Any, field: str, *, model_inference: bool) -> int | JSONResponse | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return _validation_error(f"'{field}' must be a positive integer.", field, model_inference=model_inference)
    return value


def _non_negative_integer(value: Any, field: str, *, model_inference: bool) -> int | JSONResponse | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return _validation_error(f"'{field}' must be a non-negative integer.", field, model_inference=model_inference)
    return value


def _validate_range(
    value: Any, field: str, low: float, high: float, *, model_inference: bool
) -> float | JSONResponse | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not low <= float(value) <= high:
        return _validation_error(f"'{field}' must be between {low} and {high}.", field, model_inference=model_inference)
    return float(value)


def _validate_chat_body(
    body: dict[str, Any], *, model_inference: bool, extra_parameters: str = "error"
) -> tuple[dict[str, Any], list[dict[str, Any]]] | JSONResponse:
    supported = (
        _MODEL_INFERENCE_CHAT_FIELDS
        if model_inference
        else {
            "messages",
            "max_completion_tokens",
            "max_tokens",
            "model",
            "n",
            "presence_penalty",
            "frequency_penalty",
            "seed",
            "stop",
            "stream",
            "stream_options",
            "temperature",
            "top_p",
            "user",
        }
    )
    if model_inference:
        prepared = _prepare_model_inference_body(body, supported, extra_parameters)
        if isinstance(prepared, JSONResponse):
            return prepared
        body = prepared
    for field in body:
        if field in _UNSUPPORTED_CHAT_FIELDS:
            return _unsupported_parameter(field, body[field], model_inference=model_inference)
        if field not in supported:
            return _unsupported_parameter(field, body[field], model_inference=model_inference)
    messages_result = _validate_messages(body.get("messages"), model_inference=model_inference)
    if isinstance(messages_result, JSONResponse):
        return messages_result
    messages, _ = messages_result
    max_tokens = body.get("max_tokens")
    max_completion_tokens = body.get("max_completion_tokens")
    if max_tokens is not None and max_completion_tokens is not None:
        return _validation_error(
            "Only one of 'max_tokens' or 'max_completion_tokens' may be provided.",
            "max_tokens",
            model_inference=model_inference,
        )
    for field in ("max_tokens", "max_completion_tokens"):
        validator = _non_negative_integer if model_inference else _positive_integer
        value = validator(body.get(field), field, model_inference=model_inference)
        if isinstance(value, JSONResponse):
            return value
    n = body.get("n", 1)
    if isinstance(n, bool) or not isinstance(n, int) or n != 1:
        return _unsupported_parameter("n", n, model_inference=model_inference)
    for field in ("temperature", "top_p"):
        high = 1.0 if field == "temperature" and model_inference else (2.0 if field == "temperature" else 1.0)
        low = 0.0
        value = _validate_range(body.get(field), field, low, high, model_inference=model_inference)
        if isinstance(value, JSONResponse):
            return value
    for field in ("presence_penalty", "frequency_penalty"):
        value = _validate_range(body.get(field), field, -2.0, 2.0, model_inference=model_inference)
        if isinstance(value, JSONResponse):
            return value
    if "seed" in body and (isinstance(body["seed"], bool) or not isinstance(body["seed"], int)):
        return _validation_error("'seed' must be an integer.", "seed", model_inference=model_inference)
    if "user" in body and not isinstance(body["user"], str):
        return _validation_error("'user' must be a string.", "user", model_inference=model_inference)
    stop = body.get("stop")
    if stop is not None and not (
        (isinstance(stop, str) and bool(stop))
        or (
            isinstance(stop, list)
            and 1 <= len(stop) <= 4
            and all(isinstance(item, str) and bool(item) for item in stop)
        )
    ):
        return _validation_error(
            "'stop' must be a string or an array of up to four strings.", "stop", model_inference=model_inference
        )
    stream = body.get("stream", False)
    if not isinstance(stream, bool):
        return _validation_error("'stream' must be a boolean.", "stream", model_inference=model_inference)
    stream_options = body.get("stream_options")
    if stream_options is not None:
        if not isinstance(stream_options, dict) or set(stream_options) - {"include_usage"}:
            return _validation_error(
                "'stream_options' only supports 'include_usage'.", "stream_options", model_inference=model_inference
            )
        if not isinstance(stream_options.get("include_usage"), bool):
            return _validation_error(
                "'stream_options.include_usage' must be a boolean.", "stream_options", model_inference=model_inference
            )
        if not stream:
            return _validation_error(
                "'stream_options' requires 'stream': true.", "stream_options", model_inference=model_inference
            )
    return body, messages


def _wants_stream_usage(body: dict[str, Any]) -> bool:
    stream_options = body.get("stream_options")
    return isinstance(stream_options, dict) and stream_options.get("include_usage") is True


def _cache_options(max_tokens: int | None, stop: str | list[str] | None) -> str:
    return json.dumps({"max_tokens": max_tokens, "stop": stop}, sort_keys=True, separators=(",", ":"))


def _respond(payload: dict[str, Any], *, stream: bool, include_usage: bool, headers: dict[str, str]) -> Response:
    if stream:
        return StreamingResponse(
            iter(engine.sse_chunks(payload, include_usage=include_usage)),
            media_type="text/event-stream",
            headers=headers,
        )
    return JSONResponse(content=payload, headers=headers)


async def run_chat_completion(
    state: AppState,
    deployment: Deployment,
    request: Request,
    *,
    model_inference: bool = False,
) -> Response:
    body = await _read_json_body(request, model_inference=model_inference)
    if isinstance(body, JSONResponse):
        return body
    validated = _validate_chat_body(
        body,
        model_inference=model_inference,
        extra_parameters=request.headers.get("extra-parameters", "error"),
    )
    if isinstance(validated, JSONResponse):
        return validated
    body, messages = validated
    stream = body.get("stream", False)
    include_usage = _wants_stream_usage(body)
    max_tokens = body.get("max_tokens") if isinstance(body.get("max_tokens"), int) else None
    if max_tokens is None and isinstance(body.get("max_completion_tokens"), int):
        max_tokens = body["max_completion_tokens"]
    stop = body.get("stop")

    headers = _base_headers()
    prompt_text = engine.prompt_text_from_messages(messages)
    policy = state.config.filter_policy_for(deployment)

    prompt_outcome = None
    if policy is not None:
        prompt_outcome = apply_filter_policy(prompt_text, policy, state.config)
        state.safety_stats.record_prompt_check(deployment.name, prompt_outcome)
        if prompt_outcome.blocked:
            response = errors.content_filter_error(
                prompt_outcome.content_filter_result(include_jailbreak=True),
                model_inference=model_inference,
            )
            for key, value in headers.items():
                response.headers[key] = value
            return response

    cache = state.config.cache_settings_for(deployment)
    cache_options = _cache_options(max_tokens, stop)
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
            options_key=cache_options,
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

    payload = engine.chat_completion_payload(
        model=deployment.name,
        messages=messages,
        max_tokens=max_tokens,
        stop=stop,
    )

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
            options_key=cache_options,
        )

    return _respond(payload, stream=stream, include_usage=include_usage, headers=headers)


def _validate_embeddings_body(
    body: dict[str, Any],
    deployment: Deployment,
    *,
    model_inference: bool,
    extra_parameters: str = "error",
) -> tuple[dict[str, Any], list[str], int, str] | JSONResponse:
    supported = (
        _MODEL_INFERENCE_EMBEDDING_FIELDS
        if model_inference
        else {
            "dimensions",
            "encoding_format",
            "input",
            "input_type",
            "model",
            "user",
        }
    )
    if model_inference:
        prepared = _prepare_model_inference_body(body, supported, extra_parameters)
        if isinstance(prepared, JSONResponse):
            return prepared
        body = prepared
    for field in body:
        if field not in supported:
            return _unsupported_parameter(field, body[field], model_inference=model_inference)
    raw_input = body.get("input")
    if isinstance(raw_input, str):
        inputs = [raw_input]
    elif isinstance(raw_input, list) and raw_input and all(isinstance(item, str) for item in raw_input):
        inputs = raw_input
    elif isinstance(raw_input, list) and raw_input:
        return _validation_error(
            "Token array inputs are not supported by the simulator; send a string or an array of strings.",
            "input",
            model_inference=model_inference,
        )
    else:
        return _validation_error(
            "'input' is required (string or array of strings).", "input", model_inference=model_inference
        )
    input_type = body.get("input_type", "text")
    if input_type != "text":
        return _unsupported_parameter("input_type", input_type, model_inference=model_inference)
    encoding_format = body.get("encoding_format", "float")
    if encoding_format not in ("float", "base64"):
        return _unsupported_parameter("encoding_format", encoding_format, model_inference=model_inference)
    dimensions = deployment.dimensions
    if "dimensions" in body:
        requested = body["dimensions"]
        if isinstance(requested, bool) or not isinstance(requested, int) or not 8 <= requested <= deployment.dimensions:
            return _validation_error(
                f"'dimensions' must be between 8 and {deployment.dimensions} for this deployment.",
                "dimensions",
                model_inference=model_inference,
            )
        dimensions = requested
    if "user" in body and not isinstance(body["user"], str):
        return _validation_error("'user' must be a string.", "user", model_inference=model_inference)
    return body, inputs, dimensions, encoding_format


async def run_embeddings(
    state: AppState,
    deployment: Deployment,
    request: Request,
    *,
    model_inference: bool = False,
) -> Response:
    body = await _read_json_body(request, model_inference=model_inference)
    if isinstance(body, JSONResponse):
        return body
    validated = _validate_embeddings_body(
        body,
        deployment,
        model_inference=model_inference,
        extra_parameters=request.headers.get("extra-parameters", "error"),
    )
    if isinstance(validated, JSONResponse):
        return validated
    _, inputs, dimensions, encoding_format = validated

    latency = _latency_ms(deployment)
    if latency > 0:
        await asyncio.sleep(latency / 1000)

    payload = engine.embeddings_payload(
        model=deployment.name,
        inputs=inputs,
        dimensions=dimensions,
        encoding_format=encoding_format,
    )
    return JSONResponse(content=payload, headers=_base_headers())


def _response_input_items(raw_input: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | JSONResponse:
    if isinstance(raw_input, str):
        message = {"role": "user", "content": raw_input}
        item = {
            "id": f"msg_{uuid.uuid4().hex[:24]}",
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": raw_input}],
        }
        return [message], [item]
    if not isinstance(raw_input, list) or not raw_input:
        return errors.openai_error(400, "'input' is required (string or array).", param="input")
    messages: list[dict[str, Any]] = []
    input_items: list[dict[str, Any]] = []
    for index, raw_item in enumerate(raw_input):
        if not isinstance(raw_item, dict):
            return errors.openai_error(400, f"'input[{index}]' must be an input message object.", param="input")
        if raw_item.get("type", "message") != "message":
            return errors.openai_error(
                400,
                "Only text message input items are supported by the simulator.",
                param=f"input[{index}].type",
                code="parameter_not_supported",
            )
        role = raw_item.get("role")
        content = raw_item.get("content")
        if not isinstance(role, str) or role not in _MESSAGE_ROLES or role == "tool":
            return errors.openai_error(400, "Responses input messages must use a supported text role.", param="input")
        if isinstance(content, str):
            text_parts = [content]
        elif isinstance(content, list) and content:
            text_parts = []
            for part_index, part in enumerate(content):
                if not isinstance(part, dict) or part.get("type") not in ("input_text", "text"):
                    return errors.openai_error(
                        400,
                        "Only input_text content is supported by the simulator.",
                        param=f"input[{index}].content[{part_index}]",
                        code="parameter_not_supported",
                    )
                if not isinstance(part.get("text"), str):
                    return errors.openai_error(
                        400,
                        "Responses text content requires a string 'text'.",
                        param=f"input[{index}].content[{part_index}].text",
                    )
                text_parts.append(part["text"])
        else:
            return errors.openai_error(400, "Responses message content must be text.", param=f"input[{index}].content")
        text = "".join(text_parts)
        messages.append({"role": role, "content": text})
        input_items.append(
            {
                "id": f"msg_{uuid.uuid4().hex[:24]}",
                "type": "message",
                "role": role,
                "content": [{"type": "input_text", "text": text}],
            }
        )
    return messages, input_items


def _validate_responses_body(
    body: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]] | JSONResponse:
    supported = {
        "input",
        "instructions",
        "max_output_tokens",
        "metadata",
        "model",
        "stream",
        "store",
        "temperature",
        "text",
        "top_p",
        "truncation",
    }
    for field in body:
        if field in _UNSUPPORTED_RESPONSE_FIELDS or field not in supported:
            return _unsupported_parameter(field, body[field], model_inference=False)
    if "instructions" in body and not isinstance(body["instructions"], str):
        return _validation_error("'instructions' must be a string.", "instructions", model_inference=False)
    max_output_tokens = _positive_integer(body.get("max_output_tokens"), "max_output_tokens", model_inference=False)
    if isinstance(max_output_tokens, JSONResponse):
        return max_output_tokens
    for field in ("temperature", "top_p"):
        low, high = (0.0, 2.0) if field == "temperature" else (0.0, 1.0)
        value = _validate_range(body.get(field), field, low, high, model_inference=False)
        if isinstance(value, JSONResponse):
            return value
    for field in ("stream", "store"):
        if field in body and not isinstance(body[field], bool):
            return _validation_error(f"'{field}' must be a boolean.", field, model_inference=False)
    metadata = body.get("metadata", {})
    if not isinstance(metadata, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in metadata.items()
    ):
        return _validation_error(
            "'metadata' must be an object of string keys and values.", "metadata", model_inference=False
        )
    if "text" in body:
        text = body["text"]
        if (
            not isinstance(text, dict)
            or set(text) - {"format"}
            or not isinstance(text.get("format"), dict)
            or text["format"].get("type") != "text"
        ):
            return _unsupported_parameter("text", text, model_inference=False)
    truncation = body.get("truncation", "disabled")
    if truncation != "disabled":
        return _unsupported_parameter("truncation", truncation, model_inference=False)
    input_result = _response_input_items(body.get("input"))
    if isinstance(input_result, JSONResponse):
        return input_result
    messages, input_items = input_result
    instructions = body.get("instructions")
    if instructions is not None:
        messages.insert(0, {"role": "system", "content": instructions})
    return body, messages, input_items


async def run_responses(state: AppState, deployment: Deployment, request: Request) -> Response:
    body = await _read_json_body(request)
    if isinstance(body, JSONResponse):
        return body
    validated = _validate_responses_body(body)
    if isinstance(validated, JSONResponse):
        return validated
    body, messages, input_items = validated
    stream = body.get("stream", False)
    max_output_tokens = body.get("max_output_tokens")

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

    payload = engine.responses_payload(
        model=deployment.name,
        messages=messages,
        max_output_tokens=max_output_tokens,
        instructions=body.get("instructions"),
        metadata=body.get("metadata", {}),
        temperature=body.get("temperature"),
        top_p=body.get("top_p"),
        store=body.get("store", True),
    )
    if policy is not None and policy.output_filter:
        completion = payload["output"][0]["content"][0]["text"]
        output_outcome = apply_filter_policy(completion, policy, state.config, is_output=True)
        state.safety_stats.record_output_check(deployment.name, output_outcome)
        if output_outcome.blocked:
            payload["output"][0]["content"][0]["text"] = ""
            payload["output_text"] = ""
            payload["status"] = "incomplete"
            payload["completed_at"] = None
            payload["output"][0]["status"] = "incomplete"
            payload["incomplete_details"] = {"reason": "content_filter"}
    if payload.get("store", True):
        state.responses.put(payload, input_items)
    if stream:
        return StreamingResponse(
            iter(engine.response_sse_chunks(payload)),
            media_type="text/event-stream",
            headers=headers,
        )
    return JSONResponse(content=payload, headers=headers)

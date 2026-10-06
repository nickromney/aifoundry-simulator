"""Deterministic model engine.

Completions echo the prompt with real ``usage`` numbers so gateway policies
and client SDKs can count tokens locally; embeddings come from the
feature-hash embedder. Token counts use the same ~4-characters-per-token
heuristic the sibling APIM simulator documents: good enough to exercise
limit and metering behaviour, wrong for billing prediction.
"""

from __future__ import annotations

import base64
import json
import math
import struct
import time
import uuid
from typing import Any

from app.embeddings import embed_text


def estimate_tokens(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    return max(1, math.ceil(len(stripped) / 4))


def prompt_text_from_messages(messages: list[Any]) -> str:
    """Flatten a chat message list to one role-labelled text.

    Used both for token estimation and as the semantic-cache key text, so a
    changed system prompt produces a different cache position.
    """
    chunks: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "user"))
        content = message.get("content")
        if isinstance(content, str):
            chunks.append(f"{role}: {content}")
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    chunks.append(f"{role}: {part['text']}")
                elif isinstance(part, str):
                    chunks.append(f"{role}: {part}")
    return "\n".join(chunks)


def last_user_message(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return prompt_text_from_messages([message])
    return ""


def _stop_sequences(stop: str | list[str] | None) -> tuple[str, ...]:
    if stop is None:
        return ()
    return (stop,) if isinstance(stop, str) else tuple(stop)


def completion_result(
    messages: list[Any],
    model: str,
    max_tokens: int | None,
    stop: str | list[str] | None = None,
) -> tuple[str, str]:
    prompt = last_user_message(messages).strip() or "(empty prompt)"
    text = (
        f"Simulated completion from '{model}'. You asked: \"{prompt}\". "
        "This deterministic reply exists so semantic caching and content safety can be exercised locally."
    )
    stop_positions = [text.find(sequence) for sequence in _stop_sequences(stop)]
    stop_at = min((position for position in stop_positions if position >= 0), default=-1)
    if stop_at >= 0 and (max_tokens is None or stop_at <= max_tokens * 4):
        return text[:stop_at], "stop"
    if max_tokens is not None and len(text) > max_tokens * 4:
        return text[: max_tokens * 4], "length"
    return text, "stop"


def completion_text(
    messages: list[Any],
    model: str,
    max_tokens: int | None,
    stop: str | list[str] | None = None,
) -> str:
    return completion_result(messages, model, max_tokens, stop)[0]


def chat_completion_payload(
    *,
    model: str,
    messages: list[Any],
    max_tokens: int | None,
    stop: str | list[str] | None = None,
) -> dict[str, Any]:
    completion, finish_reason = completion_result(messages, model, max_tokens, stop)
    prompt_tokens = estimate_tokens(prompt_text_from_messages(messages))
    completion_tokens = estimate_tokens(completion)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "system_fingerprint": "fp_aifoundry_simulator",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": completion},
                "finish_reason": finish_reason,
                "logprobs": None,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def responses_payload(
    *,
    model: str,
    messages: list[Any],
    max_output_tokens: int | None,
    instructions: str | None = None,
    metadata: dict[str, str] | None = None,
    previous_response_id: str | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    store: bool = True,
) -> dict[str, Any]:
    completion, finish_reason = completion_result(messages, model, max_output_tokens)
    input_tokens = estimate_tokens(prompt_text_from_messages(messages))
    output_tokens = estimate_tokens(completion)
    response_id = f"resp_{uuid.uuid4().hex[:24]}"
    message_id = f"msg_{uuid.uuid4().hex[:24]}"
    created_at = int(time.time())
    status = "incomplete" if finish_reason == "length" else "completed"
    output_item = {
        "type": "message",
        "id": message_id,
        "status": status,
        "role": "assistant",
        "content": [{"type": "output_text", "text": completion, "annotations": []}],
    }
    return {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "completed_at": created_at if status == "completed" else None,
        "error": None,
        "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        "instructions": instructions,
        "metadata": metadata or {},
        "max_output_tokens": max_output_tokens,
        "model": model,
        "output_text": completion,
        "parallel_tool_calls": True,
        "previous_response_id": previous_response_id,
        "reasoning": None,
        "status": status,
        "store": store,
        "output": [output_item],
        "temperature": temperature,
        "text": {"format": {"type": "text"}},
        "tool_choice": "none",
        "tools": [],
        "top_p": top_p,
        "truncation": "disabled",
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


def embeddings_payload(
    *,
    model: str,
    inputs: list[str],
    dimensions: int,
    encoding_format: str,
) -> dict[str, Any]:
    data: list[dict[str, Any]] = []
    total_tokens = 0
    for index, text in enumerate(inputs):
        vector = embed_text(text, dimensions)
        total_tokens += estimate_tokens(text)
        if encoding_format == "base64":
            packed = struct.pack(f"<{len(vector)}f", *vector)
            embedding: Any = base64.b64encode(packed).decode("ascii")
        else:
            embedding = vector
        data.append({"object": "embedding", "index": index, "embedding": embedding})
    return {
        "id": f"embd-{uuid.uuid4().hex[:24]}",
        "object": "list",
        "model": model,
        "data": data,
        "usage": {"prompt_tokens": total_tokens, "total_tokens": total_tokens},
    }


def sse_chunks(payload: dict[str, Any], *, include_usage: bool) -> list[str]:
    """Replay a full chat completion payload as OpenAI-shaped SSE chunks.

    Used for live streamed responses and for replaying semantic-cache hits
    to streaming clients.
    """
    completion = payload["choices"][0]["message"]["content"]
    base = {
        "id": payload["id"],
        "object": "chat.completion.chunk",
        "created": payload["created"],
        "model": payload["model"],
    }
    thirds = max(1, len(completion) // 3)
    pieces = [completion[i : i + thirds] for i in range(0, len(completion), thirds)] or [""]
    finish_reason = payload["choices"][0].get("finish_reason", "stop")
    chunks: list[dict[str, Any]] = [
        {
            **base,
            "system_fingerprint": payload.get("system_fingerprint"),
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            "usage": None,
        }
    ]
    chunks.extend(
        {
            **base,
            "system_fingerprint": payload.get("system_fingerprint"),
            "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
            "usage": None,
        }
        for piece in pieces
    )
    chunks.append(
        {
            **base,
            "system_fingerprint": payload.get("system_fingerprint"),
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
            "usage": None,
        }
    )
    if include_usage:
        chunks.append(
            {
                **base,
                "system_fingerprint": payload.get("system_fingerprint"),
                "choices": [],
                "usage": payload["usage"],
            }
        )
    return [f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n" for chunk in chunks] + ["data: [DONE]\n\n"]


def _response_event(event_type: str, sequence_number: int, **payload: Any) -> str:
    body = {"type": event_type, "sequence_number": sequence_number, **payload}
    encoded = json.dumps(body, separators=(",", ":"))
    return f"event: {event_type}\ndata: {encoded}\n\n"


def response_sse_chunks(payload: dict[str, Any]) -> list[str]:
    """Emit the text-only Responses streaming event family.

    Tools, background work, and multimodal events are deliberately absent:
    those are separate agent/runtime products, not model serving.  The event
    names and the fields used by text clients follow the Responses contract.
    """
    output_item = payload["output"][0]
    part = output_item["content"][0]
    text_value = part["text"]
    in_progress = dict(payload)
    in_progress.update(
        {
            "status": "in_progress",
            "completed_at": None,
            "incomplete_details": None,
            "output": [],
            "output_text": None,
            "usage": None,
        }
    )
    part_in_progress = {**part, "text": ""}
    item_in_progress = dict(output_item)
    item_in_progress["status"] = "in_progress"
    item_in_progress["content"] = []
    events = [_response_event("response.created", 0, response=in_progress)]
    events.append(_response_event("response.in_progress", 1, response=in_progress))
    events.append(_response_event("response.output_item.added", 2, output_index=0, item=item_in_progress))
    events.append(
        _response_event(
            "response.content_part.added",
            3,
            output_index=0,
            content_index=0,
            item_id=output_item["id"],
            part=part_in_progress,
        )
    )
    pieces = [
        text_value[i : i + max(1, len(text_value) // 3)]
        for i in range(0, len(text_value), max(1, len(text_value) // 3))
    ]
    for offset, piece in enumerate(pieces, start=3):
        events.append(
            _response_event(
                "response.output_text.delta",
                offset + 1,
                output_index=0,
                content_index=0,
                item_id=output_item["id"],
                delta=piece,
                logprobs=[],
            )
        )
    next_sequence = 4 + len(pieces)
    events.append(
        _response_event(
            "response.output_text.done",
            next_sequence,
            output_index=0,
            content_index=0,
            item_id=output_item["id"],
            text=text_value,
            logprobs=[],
        )
    )
    events.append(
        _response_event(
            "response.content_part.done",
            next_sequence + 1,
            output_index=0,
            content_index=0,
            item_id=output_item["id"],
            part=part,
        )
    )
    events.append(_response_event("response.output_item.done", next_sequence + 2, output_index=0, item=output_item))
    final_event = "response.incomplete" if payload["status"] == "incomplete" else "response.completed"
    events.append(_response_event(final_event, next_sequence + 3, response=payload))
    return events

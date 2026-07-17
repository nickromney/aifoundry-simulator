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


def completion_text(messages: list[Any], model: str, max_tokens: int | None) -> str:
    prompt = last_user_message(messages).strip() or "(empty prompt)"
    text = (
        f"Simulated completion from '{model}'. You asked: \"{prompt}\". "
        "This deterministic reply exists so semantic caching and content safety can be exercised locally."
    )
    if max_tokens is not None and max_tokens > 0:
        text = text[: max_tokens * 4]
    return text


def chat_completion_payload(
    *,
    model: str,
    messages: list[Any],
    max_tokens: int | None,
) -> dict[str, Any]:
    completion = completion_text(messages, model, max_tokens)
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
                "finish_reason": "stop",
                "logprobs": None,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def responses_payload(*, model: str, messages: list[Any], max_output_tokens: int | None) -> dict[str, Any]:
    completion = completion_text(messages, model, max_output_tokens)
    input_tokens = estimate_tokens(prompt_text_from_messages(messages))
    output_tokens = estimate_tokens(completion)
    return {
        "id": f"resp_{uuid.uuid4().hex[:24]}",
        "object": "response",
        "created_at": int(time.time()),
        "model": model,
        "status": "completed",
        "output": [
            {
                "type": "message",
                "id": f"msg_{uuid.uuid4().hex[:24]}",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": completion, "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
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
        {**base, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}
    ]
    chunks.extend(
        {**base, "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]} for piece in pieces
    )
    chunks.append({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]})
    if include_usage:
        chunks.append({**base, "choices": [], "usage": payload["usage"]})
    return [f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n" for chunk in chunks] + ["data: [DONE]\n\n"]

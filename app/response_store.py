"""In-memory storage for the model-serving Responses surface.

Azure's Responses API has a small resource lifecycle even when response
generation itself is synchronous: callers can retrieve a response, list its
input items, or delete it.  The simulator keeps that lifecycle in memory so it
can exercise client and gateway behaviour without pretending to be a durable
agent/conversation service.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StoredResponse:
    payload: dict[str, Any]
    input_items: list[dict[str, Any]]


class ResponseStore:
    """Thread-safe process-local response records."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._responses: dict[str, StoredResponse] = {}

    def put(self, payload: dict[str, Any], input_items: list[dict[str, Any]]) -> None:
        with self._lock:
            self._responses[str(payload["id"])] = StoredResponse(
                payload=dict(payload),
                input_items=[dict(item) for item in input_items],
            )

    def get(self, response_id: str) -> StoredResponse | None:
        with self._lock:
            record = self._responses.get(response_id)
            if record is None:
                return None
            return StoredResponse(payload=dict(record.payload), input_items=[dict(item) for item in record.input_items])

    def delete(self, response_id: str) -> bool:
        with self._lock:
            return self._responses.pop(response_id, None) is not None

    def count(self) -> int:
        with self._lock:
            return len(self._responses)

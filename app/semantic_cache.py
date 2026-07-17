"""In-memory semantic cache with real vector search semantics.

Entries are partitioned per deployment (the same partitioning the Azure APIM
``azure-openai-semantic-cache-*`` policies apply per backend), scored by
cosine distance against a configurable ``score_threshold`` (APIM semantics:
lower is stricter), aged out by TTL, and capped per partition with
oldest-first eviction. Counters and a small event ring make cache behaviour
inspectable through the management surface.

Like the rate-limit counters in the sibling APIM simulator, state is
in-memory only: a restart clears the cache, and there is no cross-instance
sharing. That trade-off is documented in docs/SEMANTIC-CACHE.md.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from app.embeddings import cosine_distance

EVENT_RING_SIZE = 50


@dataclass
class CacheEntry:
    entry_id: str
    vector: list[float]
    prompt_text: str
    payload: dict[str, Any]
    created_at: float
    hits: int = 0


@dataclass
class CacheLookupResult:
    entry: CacheEntry
    distance: float
    age_seconds: float


@dataclass
class _Partition:
    entries: list[CacheEntry] = field(default_factory=list)


class SemanticCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._partitions: dict[str, _Partition] = {}
        self._events: deque[dict[str, Any]] = deque(maxlen=EVENT_RING_SIZE)
        self.lookups = 0
        self.hits = 0
        self.misses = 0
        self.stores = 0
        self.evictions = 0
        self.expirations = 0

    def _prune_expired(self, partition: _Partition, ttl_seconds: int, now: float) -> None:
        keep = [entry for entry in partition.entries if now - entry.created_at < ttl_seconds]
        self.expirations += len(partition.entries) - len(keep)
        partition.entries = keep

    def lookup(
        self,
        partition_key: str,
        vector: list[float],
        *,
        score_threshold: float,
        ttl_seconds: int,
        prompt_text: str,
    ) -> CacheLookupResult | None:
        now = time.time()
        with self._lock:
            self.lookups += 1
            partition = self._partitions.get(partition_key)
            best: tuple[CacheEntry, float] | None = None
            if partition is not None:
                self._prune_expired(partition, ttl_seconds, now)
                for entry in partition.entries:
                    distance = cosine_distance(vector, entry.vector)
                    if best is None or distance < best[1]:
                        best = (entry, distance)
            if best is not None and best[1] <= score_threshold:
                entry, distance = best
                entry.hits += 1
                self.hits += 1
                self._events.append(
                    {
                        "type": "hit",
                        "partition": partition_key,
                        "prompt": prompt_text[:120],
                        "matched_prompt": entry.prompt_text[:120],
                        "distance": round(distance, 6),
                        "entry_id": entry.entry_id,
                        "at": now,
                    }
                )
                return CacheLookupResult(entry=entry, distance=distance, age_seconds=now - entry.created_at)
            self.misses += 1
            self._events.append(
                {
                    "type": "miss",
                    "partition": partition_key,
                    "prompt": prompt_text[:120],
                    "nearest_distance": round(best[1], 6) if best is not None else None,
                    "at": now,
                }
            )
            return None

    def store(
        self,
        partition_key: str,
        vector: list[float],
        prompt_text: str,
        payload: dict[str, Any],
        *,
        ttl_seconds: int,
        max_entries: int,
    ) -> str:
        now = time.time()
        with self._lock:
            partition = self._partitions.setdefault(partition_key, _Partition())
            self._prune_expired(partition, ttl_seconds, now)
            entry = CacheEntry(
                entry_id=f"sc_{uuid.uuid4().hex[:16]}",
                vector=vector,
                prompt_text=prompt_text,
                payload=payload,
                created_at=now,
            )
            partition.entries.append(entry)
            while len(partition.entries) > max_entries:
                partition.entries.pop(0)
                self.evictions += 1
            self.stores += 1
            self._events.append(
                {
                    "type": "store",
                    "partition": partition_key,
                    "prompt": prompt_text[:120],
                    "entry_id": entry.entry_id,
                    "at": now,
                }
            )
            return entry.entry_id

    def flush(self) -> int:
        with self._lock:
            cleared = sum(len(partition.entries) for partition in self._partitions.values())
            self._partitions.clear()
            self._events.append({"type": "flush", "cleared_entries": cleared, "at": time.time()})
            return cleared

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            partitions = {
                key: {
                    "entries": len(partition.entries),
                    "prompts": [entry.prompt_text[:120] for entry in partition.entries[-10:]],
                }
                for key, partition in self._partitions.items()
            }
            total_entries = sum(len(partition.entries) for partition in self._partitions.values())
            hit_rate = self.hits / self.lookups if self.lookups else None
            return {
                "entries": total_entries,
                "lookups": self.lookups,
                "hits": self.hits,
                "misses": self.misses,
                "stores": self.stores,
                "evictions": self.evictions,
                "expirations": self.expirations,
                "hit_rate": hit_rate,
                "partitions": partitions,
            }

    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)

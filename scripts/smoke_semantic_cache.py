"""Semantic cache smoke: miss, hit, threshold, streaming replay, stats.

The default config gives gpt-demo 400ms of simulated latency, so this smoke
also demonstrates the point of semantic caching: the hit comes back faster
than the miss because it skips the model entirely.
"""

from __future__ import annotations

import time

from smoke_common import ADMIN_HEADERS, chat, check_health, client, flush_cache, require, retry_call


def timed_chat(prompt: str, *, stream: bool = False) -> tuple[float, object]:
    start = time.perf_counter()
    response = chat(prompt, stream=stream)
    elapsed = time.perf_counter() - start
    require(response.status_code == 200, f"expected 200, got {response.status_code}: {response.text}")
    return elapsed, response


def main() -> int:
    retry_call(check_health)
    flush_cache()

    miss_elapsed, miss = timed_chat("What is the capital of France?")
    require(miss.headers.get("x-semantic-cache") == "miss", f"expected miss, got {dict(miss.headers)}")

    hit_elapsed, hit = timed_chat("What is the capital of France???")
    require(hit.headers.get("x-semantic-cache") == "hit", f"expected hit, got {dict(hit.headers)}")
    score = float(hit.headers["x-semantic-cache-score"])
    require(score < 0.1, f"expected distance under the 0.1 threshold, got {score}")
    require(hit.json()["id"] == miss.json()["id"], "expected the cached completion to be returned verbatim")
    require("age" in hit.headers, "expected age header on cache hit")
    require(hit_elapsed < miss_elapsed, f"expected hit ({hit_elapsed:.3f}s) faster than miss ({miss_elapsed:.3f}s)")

    _, other = timed_chat("How do I bake sourdough bread?")
    require(other.headers.get("x-semantic-cache") == "miss", "expected unrelated prompt to miss")

    _, spain = timed_chat("What is the capital of Spain?")
    require(
        spain.headers.get("x-semantic-cache") == "miss",
        "expected the Spain prompt to miss — returning the France answer would be the classic "
        "semantic-cache wrong-answer hazard",
    )

    stream_replay = chat("What is the capital of France?", stream=True)
    require(stream_replay.status_code == 200, f"expected 200 stream replay, got {stream_replay.status_code}")
    require(stream_replay.headers.get("x-semantic-cache") == "hit", "expected streaming request to hit the cache")
    require(stream_replay.text.rstrip().endswith("data: [DONE]"), "expected SSE replay to finish with [DONE]")

    with client() as http:
        stats = http.get("/foundry/management/semantic-cache", headers=ADMIN_HEADERS).json()
    require(stats["hits"] >= 2, f"expected at least 2 hits in stats, got {stats}")
    require(stats["misses"] >= 3, f"expected at least 3 misses in stats, got {stats}")
    require(stats["entries"] >= 3, f"expected stored entries in stats, got {stats}")

    flush_cache()

    print("semantic cache smoke passed")
    print(f"- miss took {miss_elapsed:.3f}s (simulated model latency), hit took {hit_elapsed:.3f}s")
    print(f"- near-duplicate hit at distance {score:.4f} (threshold 0.1), cached response returned verbatim")
    print("- unrelated and single-word-swap prompts both missed")
    print("- streaming request replayed the cached completion as SSE")
    print(f"- stats: {stats['lookups']} lookups, {stats['hits']} hits, {stats['misses']} misses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

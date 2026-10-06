"""Deterministic CPU workloads; excludes HTTP, startup and simulated latency."""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import math
import resource
import sys
import time
from pathlib import Path
from unittest.mock import patch

# Select source explicitly: editable installs can otherwise benchmark the wrong app.
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--app-root", type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument("--scenario", choices=["embeddings", "cache-hit", "cache-miss"], default="embeddings")
parser.add_argument("--requests", type=int, default=1000)
parser.add_argument("--entries", type=int, default=128)
parser.add_argument("--profile", type=Path)
parser.add_argument("--output", type=Path)
parser.add_argument("--golden", action="store_true", help="Print deterministic behavior oracle instead of timings")


def main() -> None:
    args = parser.parse_args()
    if args.requests < 1 or args.entries < 1:
        parser.error("requests and entries must be positive")
    root = args.app_root.resolve()
    sys.path.insert(0, str(root))
    import app
    from app.embeddings import cosine_distance, embed_text
    from app.semantic_cache import SemanticCache

    if Path(app.__file__).resolve().parent != root / "app":
        raise RuntimeError(f"Wrong source selected: {app.__file__}")

    if args.golden:
        texts = ["", "hello", "HELLO", "capital of France?", "capital of France???", "España 日本", "a  b\na"]
        vectors = [embed_text(text, dim) for dim in (8, 256) for text in texts]
        cache = SemanticCache()
        with (
            patch("app.semantic_cache.time.time", return_value=1000),
            patch("app.semantic_cache.uuid.uuid4") as identifier,
        ):
            identifier.return_value.hex = "0" * 32
            for name in ("first", "tie", "last"):
                cache.store("p", vectors[0], name, {"text": name}, ttl_seconds=10, max_entries=2)
            hit = cache.lookup("p", vectors[0], score_threshold=0, ttl_seconds=10, prompt_text="query")
            assert hit is not None
            before = cache.snapshot()
            with patch("app.semantic_cache.time.time", return_value=1010):
                assert cache.lookup("p", vectors[0], score_threshold=0, ttl_seconds=10, prompt_text="expired") is None
            oracle = {
                "vectors": vectors,
                "distances": [cosine_distance(vector, vectors[0 if len(vector) == 8 else 7]) for vector in vectors],
                "winner": hit.entry.prompt_text,
                "before": before,
                "after": cache.snapshot(),
                "events": cache.events(),
                "flush": cache.flush(),
            }
        print(json.dumps(oracle, sort_keys=True, separators=(",", ":")))
        return

    cache = SemanticCache()
    for index in range(args.entries):
        text = f"catalog item {index} details"
        cache.store("bench", embed_text(text), text, {"index": index}, ttl_seconds=86400, max_entries=args.entries)
    hit_vector = embed_text("catalog item 0 details")
    # The zero vector has distance exactly 1 from every stored vector: guaranteed miss.
    query = hit_vector if args.scenario == "cache-hit" else [0.0] * 256

    def operation(index: int) -> None:
        if args.scenario == "embeddings":
            vector = embed_text(f"catalog item {index} details with varying identifiers and punctuation!")
            assert len(vector) == 256
        else:
            result = cache.lookup("bench", query, score_threshold=0.1, ttl_seconds=86400, prompt_text="benchmark")
            assert (result is not None) == (args.scenario == "cache-hit")

    for index in range(20):
        operation(index)
    profiler = cProfile.Profile() if args.profile else None
    if profiler:
        profiler.enable()
    samples = []
    started = time.perf_counter()
    for index in range(args.requests):
        start = time.perf_counter()
        operation(index + 20)
        samples.append(time.perf_counter() - start)
    elapsed = time.perf_counter() - started
    if profiler:
        profiler.disable()
        profiler.dump_stats(args.profile)
    samples.sort()
    result = {
        "scenario": args.scenario,
        "requests": args.requests,
        "entries": args.entries if args.scenario != "embeddings" else 0,
        "source_root": str(root),
        "source_sha256": hashlib.sha256(
            (root / "app/embeddings.py").read_bytes() + (root / "app/semantic_cache.py").read_bytes()
        ).hexdigest(),
        "elapsed_seconds": elapsed,
        "operations_per_second": args.requests / elapsed,
        **{f"p{percent}_ms": samples[math.ceil(args.requests * percent / 100) - 1] * 1000 for percent in (50, 95, 99)},
        "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        / (1048576 if sys.platform == "darwin" else 1024),
    }
    output = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(output)
    print(output, end="")


if __name__ == "__main__":
    main()

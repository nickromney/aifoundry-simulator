# Foundry performance evidence and repeatable workflow

Measured 2026-10-05 on macOS arm64, CPython 3.13.16. The baseline is the
frozen `4ee7f51` app tree, loaded with `--app-root .run/baseline`; the candidate adds only bounded feature
hash memoization. APIM source `3adc88e` supplied the workflow lessons, not the
performance claims. Foundation commit `4ee7f51` contains separate correctness
fixes; those do not participate in this embeddings-only workload.

## Workloads and baseline

`scripts/benchmark_foundry.py` measures embeddings with varying numeric IDs,
or semantic-cache hits/misses against a populated deployment partition.
Twenty internal warmups are excluded. Imports, setup, cache seeding, HTTP,
Docker and simulated model latency are excluded from request-loop metrics.
Peak RSS is process-wide, including startup. Hyperfine measures the whole
process and has a separate three-process warmup followed by ten measured runs.

| 5,000 embeddings | Before | After |
| --- | ---: | ---: |
| Hyperfine process mean | 288.7 ms | 174.1 ms |
| Operations/second, single unprofiled loop | 24,713 | 46,755 |
| p50 | 0.0403 ms | 0.0214 ms |
| p95 | 0.0442 ms | 0.0237 ms |
| p99 | 0.0496 ms | 0.0285 ms |
| Process peak RSS | 30.14 MiB | 30.31 MiB |

Whole-process time fell 39.7% in this experiment; the individual loop sample
increased throughput about 89%. Tail figures are single-run observations, not
aggregate confidence intervals. The machine was also running Docker work;
repeat under controlled load for capacity decisions. Neither metric is an
Azure performance claim or an HTTP latency promise. Short words and trigrams
repeat even when full prompts vary; high-entropy unique words may benefit less.

The initial cProfile run put `_bucket_and_sign` first by internal time
(0.032 of 0.092 seconds; 72,940 calls). Afterward, only 1,848 calls required
hash computation; embedding assembly and vector normalization now lead.
A separate 128-entry miss profile is dominated by ordered cosine dot products
(0.269 of 0.280 seconds). No dot-product/vector-search change was made: numerical
ordering and threshold edges need their own measured experiment and proof.

| Opportunity | Impact | Confidence | Effort | Score | Decision |
| --- | ---: | ---: | ---: | ---: | --- |
| Memoize pure feature hash by feature and dimensions | 3 | 5 | 1 | 15 | Implemented |
| Change ordered cosine accumulation/search | 3 | 2 | 4 | 1.5 | Deferred |

## Change: bounded pure feature-hash memoization

- Ordering preserved: word/trigram traversal and vector accumulation are identical.
- Tie-breaking unchanged: semantic cache lookup iteration is unchanged by this lever.
- Floating-point: identical; the cache retains only the original integer bucket
  and `+1.0`/`-1.0` sign, never a vector or accumulated result.
- RNG seeds: N/A; BLAKE2b remains deterministic, independent of Python hash seeds.
- Golden outputs: byte-identical generated JSON; SHA-256 verification passed.
- Key dependencies: exact feature text and dimensions. Values are immutable tuples;
  each embedding still allocates a fresh vector. No prompt-level memoization.
- Retention: 1,024 keys; features longer than 128 characters bypass retention.
  This bounds retained text as well as entry count and avoids holding huge words.
- Rollback: revert the isolated optimization commit, leaving the foundation intact.

`tests/test_embedding_hash_cache.py` independently calculates the baseline hash,
exceeds cache capacity, revisits evicted keys, varies dimensions, checks long-key
bypass and verifies output vector ownership. The committed golden covers empty,
case, whitespace, Unicode, punctuation, multiple dimensions, cache ties,
eviction, TTL boundary, counters/events and flush. It supplements the full
HTTP contract suite; it does not replace safety or streaming tests.

Raw unprofiled metrics and all ten Hyperfine samples are saved in
[performance/](performance/). The profile timing numbers above are diagnostic;
only unprofiled runs support timing comparisons.

## Reproduce and extend

```sh
make benchmark
BENCHMARK_SCENARIO=cache-hit BENCHMARK_ENTRIES=512 make benchmark
BENCHMARK_SCENARIO=cache-miss make profile
uv run --extra dev python -c 'import pstats; pstats.Stats(".run/foundry.prof").strip_dirs().sort_stats("tottime").print_stats(15)'
make golden-check
hyperfine --warmup 3 --runs 10 'uv run --extra dev python scripts/benchmark_foundry.py --requests 5000'
```

To reproduce the committed baseline, run `mkdir -p .run/baseline` then
`git archive 4ee7f51 app | tar -x -C .run/baseline`, and add
`--app-root .run/baseline` to the same benchmark/golden command.

For the next optimization, capture a fresh baseline and profile first; score
opportunities and implement one lever per commit. Preserve the golden fixture;
do not regenerate it to make a candidate pass. Store temporary profiles/results
in ignored `.run/`. Golden generation freezes clock/IDs rather than stripping
fields that may matter. `--app-root /path/to/archived-source` inserts that source
before imports and verifies `app.__file__`, avoiding APIM's editable-install trap.
Archive all baseline helpers together and use the same harness for both sources.
Use separate processes for cold measurements and deliberately test cache churn,
misses and changed dimensions. Never multiply gains from different workloads.

## Operating context

Use the [agent operating model](AGENT-OPERATING-MODEL.md) to connect this
feature to configuration, runtime lifetime, neighboring request paths, and
the cheapest verification that establishes the intended claim.

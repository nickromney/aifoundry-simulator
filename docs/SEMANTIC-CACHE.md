# Semantic Cache

Semantic caching answers a repeated-or-rephrased prompt from a cache keyed
by *embedding similarity* instead of exact text match. In Azure, APIM's
`azure-openai-semantic-cache-lookup`/`-store` policies do this in front of
Azure OpenAI deployments, using an embeddings deployment plus a
RediSearch-backed vector store. This simulator implements the same
request-visible behaviour inside the Foundry container so you can exercise
it with zero cloud calls.

## Try it

```bash
make up
make smoke-cache
```

Or by hand — the second call answers from cache (note the headers and the
unchanged completion `id`), and returns visibly faster because `gpt-demo`
carries 400ms of simulated model latency the hit skips:

```bash
curl -si http://localhost:8020/openai/deployments/gpt-demo/chat/completions?api-version=2024-10-21 \
  -H "api-key: local-foundry-key-alpha" -H "content-type: application/json" \
  -d '{"messages":[{"role":"user","content":"What is the capital of France?"}]}' | grep -iE 'x-semantic|^HTTP'

curl -si http://localhost:8020/openai/deployments/gpt-demo/chat/completions?api-version=2024-10-21 \
  -H "api-key: local-foundry-key-alpha" -H "content-type: application/json" \
  -d '{"messages":[{"role":"user","content":"What is the capital of France???"}]}' | grep -iE 'x-semantic|age|^HTTP'
```

```text
x-semantic-cache: hit
x-semantic-cache-score: 0.012027
age: 2
```

Inspect and reset through the management surface (or `foundrysim
cache-stats` / `cache-events` / `cache-flush`):

```bash
curl -s -H "X-Foundry-Admin-Key: local-foundry-admin-key" \
  http://localhost:8020/foundry/management/semantic-cache | jq
curl -s -X DELETE -H "X-Foundry-Admin-Key: local-foundry-admin-key" \
  http://localhost:8020/foundry/management/semantic-cache
```

## Configuration

Resource-level defaults, overridable per chat deployment:

```json
"semantic_cache": {
  "enabled": true,
  "score_threshold": 0.1,
  "ttl_seconds": 300,
  "max_entries": 512,
  "embeddings_deployment": "text-embedding-demo"
}
```

- `score_threshold` follows the APIM policy's semantics: it is a **vector
  distance** (1 − cosine similarity), so **lower is stricter**. APIM's
  documented examples use values like 0.05; the simulator defaults to 0.1.
- `ttl_seconds` ages entries out; `max_entries` caps each partition with
  oldest-first eviction.
- `embeddings_deployment` must name an embeddings deployment in the same
  config — the same coupling the APIM policy declares with its
  `embeddings-backend-id` attribute.
- The cache key text is the role-labelled concatenation of all messages, so
  a changed system prompt occupies a different cache position.
- Entries are partitioned per deployment, and streamed requests both hit
  (replayed as SSE) and store like non-streamed ones. Output-filtered
  responses are never stored.

## How the simulator maps to the Azure pieces

| Azure | Simulator |
| --- | --- |
| `azure-openai-semantic-cache-lookup` `score-threshold` | `semantic_cache.score_threshold` (same distance semantics) |
| `embeddings-backend-id` | `semantic_cache.embeddings_deployment` |
| `azure-openai-semantic-cache-store` `duration` | `semantic_cache.ttl_seconds` |
| Azure Managed Redis + RediSearch vector store | in-process brute-force cosine search (resets on restart) |
| Cached response returned by APIM | stored completion returned verbatim, same `id`, plus `x-semantic-cache*` and `age` headers |

## What is honest and what is not

The *mechanics* are real: real embeddings API, real cosine-distance search,
real threshold/TTL/eviction behaviour, real headers. The *featurisation* is
not neural: vectors are deterministic feature hashes of word unigrams
(weighted 3×) and character trigrams, L2-normalised. Measured distances for
representative pairs against "What is the capital of France?":

| Compared prompt | Distance | At 0.1 threshold |
| --- | --- | --- |
| identical text | 0.000 | hit |
| `...France???` | 0.012 | hit |
| `What is the capital city of France?` | 0.087 | hit |
| `What's the capital of France?` | 0.194 | miss |
| **`What is the capital of Spain?`** | **0.252** | **miss** |
| `Tell me the capital of France` | 0.279 | miss |
| `How do I bake sourdough bread?` | 0.989 | miss |

Two lessons fall out of that table:

1. **The limitation:** similarity is lexical. A true paraphrase that shares
   little vocabulary ("Tell me...") misses even though a human — or a real
   embedding model — would call it the same question. Do not use this cache
   to evaluate real hit rates.
2. **The hazard it faithfully reproduces:** the Spain prompt sits *closer*
   to the cached France entry than the genuine paraphrase does. With real
   embeddings the same thing happens — topically-adjacent-but-different
   questions score high similarity — and a loose `score-threshold` will
   serve the France answer to the Spain question. That wrong-answer failure
   mode is precisely what threshold tuning manages in production, and you
   can demonstrate it here by raising `score_threshold` to 0.3 and watching
   `make smoke-cache` fail on the Spain assertion.

Other deliberate simplifications, all visible in [SCOPE.md](SCOPE.md):
in-memory only (restart clears it), chat completions only, brute-force
search (fine at local scale), no cross-instance sharing.

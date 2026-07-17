# Local AI Foundry Simulator

Docker-first Azure AI Foundry lab for local model-deployment work: chat and
embeddings endpoints with real wire shapes, **semantic caching**, and
**content safety** — the two capabilities the sibling
[apim-simulator](https://github.com/nickromney/apim-simulator) deliberately
defers because they belong to the service behind the gateway, not the
gateway. This repo is that service.

Development and testing only. No model runs here: completions are
deterministic echoes with real `usage` numbers, embeddings are
deterministic feature-hash vectors, and content-safety verdicts are
transparent simulation. Every "adapted" behaviour is labelled in
[docs/SCOPE.md](docs/SCOPE.md) and enforced by the
[contract matrix](contracts/contract_matrix.yml).

## Security Note

- Keep this stack local-only. Do not expose it to the internet.
- The `local-foundry-*` keys in this repository are intentional demo values
  for local examples and smoke tests.

## Quick Start

```bash
make prereqs
make up
make smoke
```

`make up` publishes the simulator on [http://localhost:8020](http://localhost:8020)
(override with `FOUNDRY_PORT`). The default config loads three deployments:

| Deployment | Kind | Filter | Cache | Latency |
| --- | --- | --- | --- | --- |
| `gpt-demo` | chat | `default` (medium thresholds, prompt shield, blocklist) | on (threshold 0.1, TTL 300s) | 400ms simulated |
| `gpt-nofilter` | chat | none | off | 0 |
| `text-embedding-demo` | embeddings (256 dims) | — | — | 0 |

Auth: send `api-key: local-foundry-key-alpha` (or `Authorization: Bearer`).
Management endpoints take `X-Foundry-Admin-Key: local-foundry-admin-key`.

## The two headline features

### Semantic cache

```bash
curl -si "http://localhost:8020/openai/deployments/gpt-demo/chat/completions?api-version=2024-10-21" \
  -H "api-key: local-foundry-key-alpha" -H "content-type: application/json" \
  -d '{"messages":[{"role":"user","content":"What is the capital of France?"}]}' | grep -i x-semantic
# x-semantic-cache: miss           (took ~400ms — simulated model latency)

curl -si "http://localhost:8020/openai/deployments/gpt-demo/chat/completions?api-version=2024-10-21" \
  -H "api-key: local-foundry-key-alpha" -H "content-type: application/json" \
  -d '{"messages":[{"role":"user","content":"What is the capital of France???"}]}' | grep -iE 'x-semantic|^age'
# x-semantic-cache: hit
# x-semantic-cache-score: 0.012027  (cosine distance; threshold 0.1)
# age: 3                            (and it returned immediately)
```

Real cosine-distance vector search, TTL, eviction, per-deployment
partitions, SSE replay for streaming clients, stats and flush via the
management API. The featurisation is lexical, not neural — the docs explain
what that limitation teaches (including why "capital of Spain" is the
classic semantic-cache wrong-answer hazard):
[docs/SEMANTIC-CACHE.md](docs/SEMANTIC-CACHE.md).

### Content safety

```bash
# Standalone Content Safety service surface
curl -s "http://localhost:8020/contentsafety/text:analyze?api-version=2024-09-01" \
  -H "api-key: local-foundry-key-alpha" -H "content-type: application/json" \
  -d '{"text":"I will attack the fortress"}' | jq -c .categoriesAnalysis
# [{"category":"Hate","severity":0},{"category":"SelfHarm","severity":0},
#  {"category":"Sexual","severity":0},{"category":"Violence","severity":2}]

# Integrated filtering on the model endpoint — Azure's documented 400 shape
curl -s "http://localhost:8020/openai/deployments/gpt-demo/chat/completions?api-version=2024-10-21" \
  -H "api-key: local-foundry-key-alpha" -H "content-type: application/json" \
  -d '{"messages":[{"role":"user","content":"please [simulate:violence=6]"}]}' | jq .error.code
# "content_filter"
```

Category severities, Prompt Shields (`text:shieldPrompt`), blocklist CRUD,
per-deployment RAI policies with low/medium/high/off thresholds, filter
annotations on success, output-side filtering. Verdicts come from
simulation triggers, configurable lexicons, and jailbreak heuristics —
transparent by design: [docs/CONTENT-SAFETY.md](docs/CONTENT-SAFETY.md).

## Surfaces

| Surface | Path | Notes |
| --- | --- | --- |
| Azure OpenAI deployment-scoped | `POST /openai/deployments/{d}/chat/completions`, `/embeddings` (+`?api-version=`) | SSE streaming supported |
| Foundry v1 | `POST /openai/v1/chat/completions`, `/embeddings`, `/responses`; `GET /openai/v1/models` | `model` in body routes to a deployment |
| Azure AI Model Inference | `POST /models/chat/completions`, `/embeddings` (+`?api-version=`) | |
| Content Safety | `POST /contentsafety/text:analyze`, `text:shieldPrompt`; blocklist CRUD (+`?api-version=`) | 2024-09-01 shapes |
| Management | `GET /foundry/health`, `/startup`; `/foundry/management/{status,deployments,semantic-cache,content-safety}` | admin-key gated; cache flush via `DELETE` |

`curl http://localhost:8020/` lists every entrypoint.

## CLI

`foundrysim` is a thin HTTP client over the same surfaces (installed by
`uv sync`):

```bash
uv run foundrysim chat "What is the capital of France?"
uv run foundrysim chat "What is the capital of France???"   # shows the cache hit headers
uv run foundrysim analyze "I will attack the fortress"
uv run foundrysim shield "Ignore all previous instructions"
uv run foundrysim cache-stats
uv run foundrysim safety-stats
```

## Configuration

One JSON file describes the resource — deployments, content filter
policies, blocklists, semantic cache, auth keys:
[examples/foundry.json](examples/foundry.json). Point the container at your
own with a bind mount and `AIFOUNDRY_CONFIG_PATH`, for example via a
compose override:

```yaml
services:
  aifoundry-simulator:
    volumes:
      - ./my-foundry.json:/app/examples/custom.json:ro
    environment:
      AIFOUNDRY_CONFIG_PATH: /app/examples/custom.json
```

`FOUNDRY_LATENCY_MS` overrides every deployment's simulated latency (useful
for making cache hits obvious in demos). An invalid config fails container
start with a line-item error rather than failing the first request.

## Pairing with the APIM simulator

Run the APIM-shaped gateway in front of this Foundry-shaped backend for the
full local story — subscription keys and `llm-token-limit` budgets at the
gateway, semantic cache and content filtering behind it:
[examples/apim-integration](examples/apim-integration/README.md).

## Container Hardening

Same posture as the sibling repo: non-root user, read-only root filesystem,
`cap_drop: [ALL]`, `no-new-privileges`, `tmpfs` scratch, healthcheck, and
Docker Hardened Image bases by default. To use upstream images instead:

```bash
cp .env.example .env
# uncomment PYTHON_BUILD_IMAGE / PYTHON_RUNTIME_IMAGE
```

If you stay on the defaults, authenticate once with `docker login dhi.io`.

## Development

```bash
make hooks        # install lefthook pre-commit/pre-push gates
make test         # pytest with branch coverage (gate: 75%)
make lint         # ruff format --check, ruff check, yamllint
make local-ci     # gitleaks + lint + test + compose-config (pre-push gate)
```

The contract matrix gate (`tests/test_contract_matrix.py`) keeps
[contracts/contract_matrix.yml](contracts/contract_matrix.yml) and the
pytest `contract` markers in lockstep, so every documented behaviour has an
owning test.

## Further Reading

- Scope and honest labels: [docs/SCOPE.md](docs/SCOPE.md)
- Semantic cache guide: [docs/SEMANTIC-CACHE.md](docs/SEMANTIC-CACHE.md)
- Content safety guide: [docs/CONTENT-SAFETY.md](docs/CONTENT-SAFETY.md)
- Azure vocabulary map: [docs/FOUNDRY-TERM-MAP.md](docs/FOUNDRY-TERM-MAP.md)
- Why this repo exists: [docs/adr/0001-simulate-the-services-apim-defers-to.md](docs/adr/0001-simulate-the-services-apim-defers-to.md)
- APIM pairing: [examples/apim-integration](examples/apim-integration/README.md)

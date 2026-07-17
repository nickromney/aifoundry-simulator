# Scope

The simulator is a local, Docker-first stand-in for the Azure AI Foundry
resource surface. It exists to exercise client code, gateway policies, and
operational behaviour — not to reproduce model quality or moderation
quality. Scope decisions and rationale: [ADR 0001](adr/0001-simulate-the-services-apim-defers-to.md).

Labels follow the sibling APIM simulator's discipline, enforced by
[contracts/contract_matrix.yml](../contracts/contract_matrix.yml) and
`tests/test_contract_matrix.py`:

- **supported** — the wire contract mirrors the documented Azure surface
- **adapted** — the wire contract mirrors Azure, but the content or verdict
  is deterministic simulation
- **deferred** — not implemented; listed so nobody discovers it the hard way

## Supported now

- Azure OpenAI deployment-scoped routes with `api-version` enforcement:
  `POST /openai/deployments/{deployment}/chat/completions`,
  `POST /openai/deployments/{deployment}/embeddings`
- Foundry v1 routes (model routed by body): `POST /openai/v1/chat/completions`,
  `/openai/v1/embeddings`, `/openai/v1/responses`, `GET /openai/v1/models`
- Azure AI Model Inference routes: `POST /models/chat/completions`,
  `/models/embeddings`
- Auth via `api-key` header or `Authorization: Bearer`, Azure-shaped 401/404
  error envelopes, `DeploymentNotFound`, `model_not_found`,
  `MissingApiVersionParameter`
- Content Safety service surface (2024-09-01 shapes): `text:analyze`,
  `text:shieldPrompt`, text blocklist CRUD and matching
- Integrated RAI content filtering per deployment: prompt-side 400
  `content_filter` with `ResponsibleAIPolicyViolation` innererror,
  success-path `prompt_filter_results`/`content_filter_results`
  annotations, output-side filtering with `finish_reason: "content_filter"`
- Semantic cache in front of chat deployments: cosine-distance lookup with
  `score_threshold` (distance; lower is stricter), TTL, per-deployment
  partitions, oldest-first eviction, hit/miss headers, SSE replay of cached
  hits, management stats/events/flush
- SSE streaming for chat completions, including `stream_options.include_usage`
- Config-driven everything via one JSON file; management surface under
  `/foundry/management/*`; `foundrysim` CLI as a thin HTTP client

## Adapted (deterministic simulation, real wire shapes)

- Completions echo the prompt deterministically; `usage` numbers use a
  ~4-characters-per-token heuristic (good for exercising token policies,
  wrong for billing)
- Embeddings are feature-hash vectors — similarity is lexical, not semantic
  (see [SEMANTIC-CACHE.md](SEMANTIC-CACHE.md))
- Content safety verdicts come from simulation triggers, configurable
  lexicons, and jailbreak pattern heuristics (see
  [CONTENT-SAFETY.md](CONTENT-SAFETY.md)); severity quality is not simulated
- `EightSeverityLevels` output returns the same 0–7 value the trigger or
  lexicon produced; lexicon bands only emit 2/4/6

## Deferred

- Image, multimodal, and audio surfaces (`image:analyze`, vision inputs)
- Protected material detection, groundedness detection, custom categories
- Streaming on the Responses surface; the Assistants surface
- Semantic caching for the Responses/embeddings surfaces (chat completions
  only, matching the APIM policy's target)
- External cache/vector stores (Redis/RediSearch) and cross-instance state —
  everything is in-memory and resets on restart
- Entra ID token *validation* (any Bearer value is checked against the
  configured key list, not decoded as a JWT); managed identity flows
- Deployment CRUD via the management surface — the config file is the
  single source of truth; restart to change deployments
- Gateway concerns (token rate limits, quotas, products, subscriptions) —
  that is the sibling APIM simulator's job; compose the two instead
  (see [examples/apim-integration](../examples/apim-integration/README.md))
- Model quality of any kind, including tokenizer-accurate counting

## Out of scope permanently

- Production use of any kind: no TLS, demo keys in the repo, in-memory state
- Real moderation quality — a lexicon is not a classifier, and the docs
  repeat this wherever a verdict appears

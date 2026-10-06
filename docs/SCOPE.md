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
  `/openai/v1/embeddings`, `/openai/v1/responses`, `GET /openai/v1/models`,
  and Responses retrieval, input-item listing, cancellation, and deletion
- Azure AI Model Inference routes: `POST /models/chat/completions`,
  `/models/embeddings` with the documented `2025-04-01` version (the legacy
  `2024-05-01-preview` version is accepted too) and
  `extra-parameters` request-header handling (`error`/`drop`; unsupported
  pass-through is reported rather than silently ignored)
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
- Text-only Responses streaming with the documented response event family;
  synchronous Responses are retained in an in-memory resource store when
  `store` is true
- Strict request validation for the locally implemented text model features:
  deployment names are the inference response `model`, unsupported parameters
  have explicit errors, and configured/runtime blocklist regexes are compiled
  before use
- Config-driven everything via one JSON file; management surface under
  `/foundry/management/*`; `foundrysim` CLI as a thin HTTP client

`foundrysim inspect` composes existing read-only endpoints into a versioned JSON
evidence envelope; partial failures produce exit 1. It is a non-atomic client
observation, with no new HTTP surface or hosted-agent capability. See the
[agent operating model](AGENT-OPERATING-MODEL.md).

## Adapted (deterministic simulation, real wire shapes)

- Completions echo the prompt deterministically; `usage` numbers use a
  ~4-characters-per-token heuristic (good for exercising token policies,
  wrong for billing)
- Embeddings are feature-hash vectors — similarity is lexical, not semantic
  (see [SEMANTIC-CACHE.md](SEMANTIC-CACHE.md))
- Model output is deterministic and text-only; accepted sampling and penalty
  fields are validated for wire compatibility but do not turn the simulator
  into a quality or randomness benchmark
- Content safety verdicts come from simulation triggers, configurable
  lexicons, and jailbreak pattern heuristics (see
  [CONTENT-SAFETY.md](CONTENT-SAFETY.md)); severity quality is not simulated
- `EightSeverityLevels` output returns the same 0–7 value the trigger or
  lexicon produced; lexicon bands only emit 2/4/6

## Deferred

- Image, multimodal, and audio surfaces (`image:analyze`, vision inputs)
- Protected material detection, groundedness detection, custom categories
- Tool/function calling, multimodal Responses input, background jobs,
  conversations, MCP, and agent hosting
- Embeddings formats beyond `float` and `base64`, token-array input, and
  model-specific embedding task modes
- Durable Responses state, pagination beyond the in-memory input list, and
  cross-instance response sharing
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

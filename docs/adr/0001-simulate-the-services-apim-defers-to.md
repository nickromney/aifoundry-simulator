# ADR 0001: Simulate the services the APIM simulator defers to

- Status: accepted
- Date: 2026-07-17
- Deciders: repo maintainer (goal set), implementation session (detail decisions)

## Context

The sibling [apim-simulator](https://github.com/nickromney/apim-simulator) is a
config-driven local Azure API Management gateway. Its AI-gateway iteration
(ADR 0001 in that repo, in flight on branch `chore/20260717-fable-review` at
the time of writing) implemented `llm-token-limit` and
`llm-emit-token-metric` natively, and explicitly **deferred** two Azure GenAI
gateway capabilities with the same reasoning:

- semantic caching (`llm-semantic-cache-lookup`/`-store`) — "needs an
  embeddings model + vector store; a fake similarity metric would teach the
  wrong mental model"
- content safety (`llm-content-safety`) — "proxies to the Azure AI Content
  Safety service; simulating moderation verdicts locally is misleading"

Both deferrals share a root cause: those policies do not describe gateway
behaviour. They describe *another Azure service* the gateway calls — the
model deployment surface and the Azure AI Content Safety service, both of
which live in Azure AI Foundry. The right home for that behaviour is a
second simulator that plays the Foundry side of the conversation.

## Decisions

### D1. Build the service side, not more gateway

This repository simulates the Azure AI Foundry resource surface in one local
container: model deployments (Azure OpenAI deployment-scoped, Foundry v1,
and Model Inference route shapes), the Content Safety service
(`text:analyze`, `text:shieldPrompt`, text blocklists), integrated RAI
content filtering on deployments, and semantic caching in front of the
simulated models. It deliberately does **not** implement gateway concerns —
token rate limits, quotas, subscription products — because the APIM
simulator already owns those. The two repos compose: APIM-shaped gateway in
front, Foundry-shaped services behind.

### D2. Semantic cache: real mechanics, transparent featurisation

The APIM repo's objection to a "fake similarity metric" is answered by
making everything except the neural network real:

- embeddings are deterministic feature-hash vectors (word unigrams weighted
  over character trigrams, L2-normalised) served by a real embeddings API;
- the cache does real cosine-distance vector search over those vectors with
  a configurable `score_threshold` (APIM semantics: a distance, lower is
  stricter), TTL expiry, per-deployment partitioning, and oldest-first
  eviction;
- hits return the stored completion verbatim with `x-semantic-cache`,
  `x-semantic-cache-score`, and `age` headers, and replay as SSE for
  streaming clients.

The honest limitation is documented rather than hidden: similarity is
lexical, so true paraphrases sharing no vocabulary miss. Usefully, the
approach reproduces the *real* production hazard — "What is the capital of
Spain?" lands closer to a cached "What is the capital of France?" than a
genuine reworded paraphrase does — which is exactly the wrong-answer risk
`score-threshold` tuning manages in Azure (see docs/SEMANTIC-CACHE.md).

### D3. Content safety: transparent verdicts, faithful wire shapes

The objection that simulated moderation verdicts mislead is answered by
making verdicts *inspectable* instead of pretend-neural, on three documented
mechanisms: simulation triggers (`[simulate:hate=6]`, `[simulate:jailbreak]`,
`[simulate:output-violence=4]`) for exact, repeatable verdicts; configurable
per-category severity lexicons (shipped deliberately naive — "kill the
process" tripping violence-low is a teaching point about lexical
classifiers); and pattern heuristics for Prompt Shields. Around those
verdicts, the wire contracts follow Azure: category names, four/eight-level
severity scales, blocklist CRUD, the 400 `content_filter` error with
`ResponsibleAIPolicyViolation` innererror, and
`prompt_filter_results`/`content_filter_results` annotations on success.

### D4. One container, config-driven, no external stores

The cache and blocklist state live in process memory (restart resets them,
matching the APIM simulator's in-memory counters). Redis/RediSearch — what
Azure actually uses behind APIM semantic caching — was rejected for v0.1:
it would triple the moving parts to demonstrate the same request-visible
behaviour. The single JSON config file describes deployments, RAI policies,
blocklists, and cache settings, mirroring the sibling repo's config-driven
discipline. A management surface (`/foundry/management/*`) and a thin
`foundrysim` CLI expose stats and events without becoming a second source
of truth.

### D5. Carry the sibling repo's delivery practices

Adopted wholesale, because they are the lessons learned: hardened non-root
read-only containers with DHI base images and upstream overrides via
`.env`; a contract matrix with `supported`/`adapted` labels enforced by a
test gate; smoke scripts with the `retry_call`/`require` idiom; lefthook
local gates plus `make local-ci` instead of push-triggered CI; uv with a
seven-day dependency cooldown; Makefile help as the front door.

## Consequences

- `make up && make smoke` demonstrates semantic caching and content safety
  end to end with zero cloud calls, and the APIM simulator gains a local
  backend that its deferred `llm-semantic-cache-*` and `llm-content-safety`
  policies could target later (see examples/apim-integration/).
- Verdict quality is explicitly not simulated: anyone using the lexicons to
  evaluate real moderation behaviour is holding it wrong, and the docs say
  so.
- Token-limit behaviour stays in the APIM repo; this repo's completions
  return real `usage` numbers so those gateway policies keep working when
  the two are composed.
- Deferred for now, documented in docs/SCOPE.md: image analysis, protected
  material detection, groundedness, streaming on the Responses surface, an
  external vector store, and Entra ID token validation.

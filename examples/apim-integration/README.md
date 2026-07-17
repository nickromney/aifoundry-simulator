# Fronting the Foundry simulator with the APIM simulator

The sibling [apim-simulator](https://github.com/nickromney/apim-simulator)
plays the gateway; this repo plays the Azure services behind it. Composed,
you get the full local story: subscription keys and token budgets at the
gateway, model deployments with semantic caching and content filtering
behind it — the same split Azure has.

> The APIM simulator's AI-gateway support (`llm-token-limit`,
> `llm-emit-token-metric`, the `make up-ai` stack) is in flight on its
> branch `chore/20260717-fable-review` at the time of writing; adjust paths
> if that work lands differently.

## 1. Start the Foundry simulator

```bash
make up          # in this repo — publishes localhost:8020, network name "aifoundry"
```

## 2. Point an APIM simulator API at it

Two network options:

- **Host port (simplest, works everywhere Docker Desktop runs):** use
  `http://host.docker.internal:8020` as the backend URL from the APIM
  container.
- **Shared network:** `docker network connect aifoundry <apim-container>`,
  then use `http://aifoundry-simulator:8000`.

[apim.json](apim.json) in this directory is a ready-made APIM simulator
config: an `ai-foundry` API proxying `/openai/*` to this simulator, with
subscription keys (`api-key` accepted), a `tokens-per-minute` budget, and
the backend set to `host.docker.internal:8020`.

The APIM container only sees files mounted from its own checkout (its
`examples/` directory appears as `/app/examples` in the container), and the
two repos need not live side by side on disk — so copy the config into
wherever your apim-simulator checkout is, then reference it by its
in-container path:

```bash
# in this repo
cp examples/apim-integration/apim.json <your-apim-simulator-checkout>/examples/ai-foundry-backend.json

# in the apim-simulator checkout
HELLO_APIM_CONFIG_PATH=/app/examples/ai-foundry-backend.json make up-hello
```

Then call the gateway, not the backend:

```bash
curl -s "http://localhost:8000/openai/deployments/gpt-demo/chat/completions?api-version=2024-10-21" \
  -H "api-key: ai-team-alpha-key" -H "content-type: application/json" \
  -d '{"messages":[{"role":"user","content":"What is the capital of France?"}]}' | jq .usage
```

What you can now demonstrate end to end, locally:

- gateway 401 (no subscription) vs backend 401 (no Foundry key) — the
  gateway config forwards a valid backend `api-key` via `set-header`
- `llm-token-limit` 429s at the gateway using the real `usage` numbers this
  simulator returns
- semantic-cache hits behind the gateway: repeat a prompt and watch
  `x-semantic-cache: hit` pass through with a faster response
- content filtering behind the gateway: `[simulate:violence=6]` returns the
  Azure 400 `content_filter` body through the gateway unchanged

## 3. The deferred-policy story

The APIM simulator's ADR 0001 defers `llm-semantic-cache-lookup/-store` and
`llm-content-safety` because they simulate other services. Those services
now exist locally:

- `llm-content-safety` proxies to a Content Safety endpoint — this
  simulator serves `/contentsafety/text:analyze` and `text:shieldPrompt`
  with the 2024-09-01 wire shapes, so a future APIM-side implementation can
  target `http://aifoundry-simulator:8000/contentsafety` (or
  `host.docker.internal:8020`) with no cloud dependency.
- `llm-semantic-cache-*` needs an embeddings backend — this simulator's
  `/openai/deployments/text-embedding-demo/embeddings` is deterministic and
  local, and in the meantime the cache built into this simulator gives the
  same request-visible behaviour (headers, verbatim replay, threshold
  semantics) service-side.

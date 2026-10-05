# Fronting the Foundry simulator with the APIM simulator

The sibling [apim-simulator](https://github.com/nickromney/apim-simulator)
plays the gateway; this repo plays the Azure services behind it. Composed,
you get the full local story: subscription keys and token budgets at the
gateway, model deployments with semantic caching and content filtering
behind it — the same split Azure has.

## Foundry-owned pairing lab

Build the gateway image once in your APIM checkout with
`docker compose -f compose.yml build apim-simulator`. After that, this lab uses
`apim-simulator:latest` and requires no sibling files at runtime. Start Foundry here, then the isolated gateway:

```bash
make up
make -C examples/apim-integration up
make -C examples/apim-integration smoke
```

The gateway listens on loopback port 8030; override with `APIM_FOUNDRY_PORT`.
Its read-only `apim.shared-network.json` fixture points to
`http://aifoundry-simulator:8000` on the external `aifoundry` network.
The image's standard non-root UID/GID (65532) is explicit, with a read-only
filesystem, tmpfs runtime configuration, dropped capabilities and
`no-new-privileges`. Runtime gateway edits disappear on recreation.

This fixture forwards deployment and v1 chat/embeddings, the v1 model list,
Responses create/retrieve/input-items/delete, Model Inference chat/embeddings,
and Content Safety analyze/shield. It injects the backend key after validating
the gateway subscription. Foundry management is not published through APIM.
It demonstrates forwarding and service ownership; it does not configure token
metering or claim Responses token-policy accounting. Use the sibling stack
below for the gateway token-budget demonstration.

The smoke verifies gateway/direct credential boundaries, cold-cache miss and
repeat hit, filter error forwarding, SSE usage and `[DONE]`, deterministic
embeddings, Content Safety and Responses lifecycle. **It flushes Foundry's
semantic cache** to establish the cold baseline and deletes the response it
creates. It targets the default demo deployments and keys; override direct
Foundry URL/keys with `SMOKE_FOUNDRY_BASE_URL`, `SMOKE_FOUNDRY_API_KEY`, and
`SMOKE_FOUNDRY_ADMIN_KEY`, and gateway URL/key with
`SMOKE_APIM_FOUNDRY_BASE_URL` and `SMOKE_APIM_FOUNDRY_KEY` when running
`uv run python scripts/smoke_apim_integration.py` directly. Changing the backend
key also requires updating the gateway fixture.

```bash
make -C examples/apim-integration down
make down
```

Stop the gateway first to release its network attachment. Validate the lab
without starting it with `make -C examples/apim-integration config`.

Verified on 2026-10-05 through published host ports using local APIM image
`f6e7cbc153c2` and Foundry image `014681e1e494`. The Foundry-owned smoke passed
against the rebuilt container. The sibling's `scripts/smoke_ai_foundry.py` also
passed against a temporary token-policy fixture: the gateway returned 429 after
four budget-filling requests, while the second subscription remained 200.
The forwarding fixture was restored and its smoke repeated afterward. These
results establish the exercised local contracts; Responses token metering
remains outside this lab's claims.

## Sibling-owned stack: `make up-ai-foundry`

The APIM simulator ships this integration as a first-class stack: its
`compose.ai-foundry.yml` overlay attaches the gateway to this repo's
`aifoundry` Docker network and loads a config
(`examples/ai-gateway/apim.foundry.json` in that repo) that proxies
`/openai` and `/contentsafety` to `http://aifoundry-simulator:8000`,
injecting the Foundry backend key via `set-header`.

```bash
# in this repo — publishes localhost:8020, creates the "aifoundry" network
make up

# in your apim-simulator checkout
make up-ai-foundry
make smoke-ai-foundry
```

`smoke-ai-foundry` asserts the whole story end to end through the gateway:
subscription 401s, completions with real `usage` numbers feeding
`llm-token-limit` (429 once the budget is spent), `x-semantic-cache`
miss-then-hit, the Azure 400 `content_filter` body passing through
unchanged, deterministic embeddings, and the Content Safety
`text:analyze` / `text:shieldPrompt` operations.

Tear down in the reverse order (gateway first) — this repo's `make down`
cannot remove the `aifoundry` network while the gateway is still attached.

## The manual way: any APIM config, host networking

[apim.json](apim.json) in this directory is the same gateway config using
`http://host.docker.internal:8020` as the backend URL, so it works without
the shared network (Docker Desktop resolves `host.docker.internal` out of
the box). The APIM container only sees files mounted from its own checkout
(its `examples/` directory appears as `/app/examples` in the container),
and the two repos need not live side by side on disk — so copy the config
into wherever your apim-simulator checkout is, then reference it by its
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

## The deferred-policy story

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

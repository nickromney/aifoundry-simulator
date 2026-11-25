# Azure AI Foundry vocabulary in simulator terms

How the words in the Azure portal and docs map onto this repository.

| Azure term | In the simulator | Notes |
| --- | --- | --- |
| AI Foundry resource (`*.services.ai.azure.com` / `*.openai.azure.com` / `*.cognitiveservices.azure.com`) | the one container on `localhost:8020` | Azure splits these hostnames by service; locally one origin serves all three route families |
| Project / hub | not modelled | one config file plays the whole resource |
| Model deployment | `deployments[]` entry in the config | name is the routing key for both URL path and v1 `model` body field |
| Deployed model (gpt-4o-mini etc.) | `model` field in management metadata | inference routes and response payloads use the deployment name, matching Azure's deployment-based routing |
| API key (resource key) | `auth.api_keys[]`, sent as `api-key` header | demo values ship in the repo, local use only |
| Entra ID bearer token | `Authorization: Bearer <jwt>` validated against `auth.entra` when enabled; otherwise the bearer value is checked as a key | RS256 only, issuer keys from JWKS or a PEM file — see [Keyless auth](#keyless-auth-managed-identity) |
| Azure RBAC data-plane role assignment | `auth.entra.role_assignments` (principal `oid`/`sub` → role names) | flat per-resource map; no scope inheritance, groups or deny assignments |
| `disableLocalAuth` resource property | `auth.disable_local_auth: true` | rejects `api-key`, `Ocp-Apim-Subscription-Key` and key-as-bearer; requires `auth.entra.enabled` |
| `api-version` query parameter | enforced on deployment-scoped, `/models`, and `/contentsafety` routes | `/models` implements `2025-04-01` plus the legacy `2024-05-01-preview`; absence returns Azure's `MissingApiVersionParameter` error |
| Azure OpenAI data-plane (`/openai/deployments/...`) | `app/routes/inference.py` azure_router | chat completions + embeddings |
| Foundry v1 API (`/openai/v1/...`) | v1_router | model in body routes to a deployment; no api-version required |
| Azure AI Model Inference (`/models/...`) | models_router | the route non-OpenAI models use in Foundry |
| Responses API | `POST /openai/v1/responses` plus retrieve/input-items/delete/cancel routes | text-only sync and SSE streaming; stored state is process-local and agent/tool features are rejected |
| Responsible AI (RAI) policy / content filter | `content_filters[]` entry | severity thresholds low/medium/high/off per category |
| Content filter categories | `hate`, `self_harm`, `sexual`, `violence` | annotation keys lowercase, Content Safety API names TitleCase, as in Azure |
| Prompt Shields (jailbreak / indirect attack) | `prompt_shield` toggle + `text:shieldPrompt` | pattern heuristics + `[simulate:jailbreak]` |
| Azure AI Content Safety service | `/contentsafety/*` routes | text surfaces only |
| Content Safety blocklists | `blocklists[]` config + runtime CRUD | exact and regex items |
| Severity levels (0-7 / FourSeverityLevels) | same numbers | lexicon verdicts emit 2/4/6; triggers can emit any |
| Annotations (`prompt_filter_results`, `content_filter_results`) | attached by `app/pipeline.py` when a filter policy applies | absent when `content_filter: null`, like a policy-removed deployment |
| Embeddings deployment | `kind: "embeddings"` deployment | feature-hash vectors, `dimensions` + `encoding_format: base64` supported |
| Model Inference `extra-parameters` | request header on `/models` routes | `error` and `drop` are implemented; pass-through is explicitly unsupported because there is no remote model adapter |
| APIM `azure-openai-semantic-cache-lookup` / `-store` | `semantic_cache` config on this side of the wire | same `score-threshold` distance semantics; see docs/SEMANTIC-CACHE.md |
| Azure Managed Redis vector store | in-process store | restart clears it |
| Token usage / billing meters | `usage` block with ~4 chars/token estimate | feeds the sibling APIM simulator's `llm-token-limit` correctly |
| Azure Monitor / App Insights | `/foundry/management/*` stats + events | admin-key gated |
| `x-ms-region` response header | always `local-simulator` | handy for spotting simulator traffic in captures |

## Keyless auth (managed identity)

Azure's recommended setup has APIM call Foundry with its managed identity
instead of an `api-key`. The simulator reproduces the resource side; the
sibling apim-simulator mints the tokens and serves its signing keys at
`/.well-known/workload-identity/jwks.json`. There is no fake IMDS or Entra
token endpoint here.

```json
"auth": {
  "api_keys": ["local-foundry-key-alpha"],
  "disable_local_auth": false,
  "entra": {
    "enabled": true,
    "trusted_issuers": [
      {
        "issuer": "http://apim-simulator:8000/.well-known/workload-identity",
        "jwks_uri": "http://apim-simulator:8000/.well-known/workload-identity/jwks.json"
      }
    ],
    "audiences": ["https://cognitiveservices.azure.com", "https://ai.azure.com"],
    "role_assignments": {"system-assigned": ["Cognitive Services OpenAI User"]}
  }
}
```

- `entra` is off by default; existing configs behave as before.
- `make up-keyless` starts the stack with
  [examples/foundry.keyless.json](../examples/foundry.keyless.json)
  (`AIFOUNDRY_CONFIG_PATH=/app/examples/foundry.keyless.json`). It is
  `examples/foundry.json` plus the `entra` block above, with the
  `system-assigned` identity granted `Cognitive Services User` so inference,
  Content Safety and Language all work keyless. `disable_local_auth` stays
  `false`, so key-based smoke tests still pass.
- Each trusted issuer sets exactly one of `jwks_uri` (fetched over HTTP,
  cached for `jwks_cache_ttl_seconds`, default 300) or `public_key_file`
  (a PEM RSA public key, for tests and standalone runs). An unknown `kid`
  triggers one JWKS refetch for key rotation, then fails closed.
- Tokens must be RS256, carry a trusted `iss`, an `aud` from `audiences`,
  and a valid `exp`/`nbf` (`clock_skew_seconds`, default 60).
- Roles per surface:

| Surface | Roles that grant access |
| --- | --- |
| Inference (chat, completions, embeddings, responses, models) | Cognitive Services OpenAI User, Cognitive Services OpenAI Contributor, Cognitive Services User, Azure AI User |
| Content Safety, Language | Cognitive Services User |
| `/foundry/management` | none: admin keys only (management plane) |

- Failures are all `401`, with distinct bodies: an invalid token returns
  code `401` with the Azure OpenAI "Access token is missing, invalid,
  audience is incorrect…" message; a valid token without a role returns
  code `PermissionDenied` naming the principal and the data action.
  Microsoft Learn's managed identity troubleshooting table lists a missing
  role as "403 Forbidden or PermissionDenied" and documents no body; the
  simulator follows the 401 observed on the wire. See the comments in
  `app/auth.py`.

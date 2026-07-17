# Azure AI Foundry vocabulary in simulator terms

How the words in the Azure portal and docs map onto this repository.

| Azure term | In the simulator | Notes |
| --- | --- | --- |
| AI Foundry resource (`*.services.ai.azure.com` / `*.openai.azure.com` / `*.cognitiveservices.azure.com`) | the one container on `localhost:8020` | Azure splits these hostnames by service; locally one origin serves all three route families |
| Project / hub | not modelled | one config file plays the whole resource |
| Model deployment | `deployments[]` entry in the config | name is the routing key for both URL path and v1 `model` body field |
| Deployed model (gpt-4o-mini etc.) | `model` field, echoed in responses | completions are deterministic echoes; no quality simulated |
| API key (resource key) | `auth.api_keys[]`, sent as `api-key` header | demo values ship in the repo, local use only |
| Entra ID bearer token | `Authorization: Bearer <key>` checked against the same key list | no JWT validation — see docs/SCOPE.md deferrals |
| `api-version` query parameter | enforced on deployment-scoped, `/models`, and `/contentsafety` routes | any value accepted; absence returns Azure's `MissingApiVersionParameter` error |
| Azure OpenAI data-plane (`/openai/deployments/...`) | `app/routes/inference.py` azure_router | chat completions + embeddings |
| Foundry v1 API (`/openai/v1/...`) | v1_router | model in body routes to a deployment; no api-version required |
| Azure AI Model Inference (`/models/...`) | models_router | the route non-OpenAI models use in Foundry |
| Responses API | `POST /openai/v1/responses` | non-streaming only |
| Responsible AI (RAI) policy / content filter | `content_filters[]` entry | severity thresholds low/medium/high/off per category |
| Content filter categories | `hate`, `self_harm`, `sexual`, `violence` | annotation keys lowercase, Content Safety API names TitleCase, as in Azure |
| Prompt Shields (jailbreak / indirect attack) | `prompt_shield` toggle + `text:shieldPrompt` | pattern heuristics + `[simulate:jailbreak]` |
| Azure AI Content Safety service | `/contentsafety/*` routes | text surfaces only |
| Content Safety blocklists | `blocklists[]` config + runtime CRUD | exact and regex items |
| Severity levels (0-7 / FourSeverityLevels) | same numbers | lexicon verdicts emit 2/4/6; triggers can emit any |
| Annotations (`prompt_filter_results`, `content_filter_results`) | attached by `app/pipeline.py` when a filter policy applies | absent when `content_filter: null`, like a policy-removed deployment |
| Embeddings deployment | `kind: "embeddings"` deployment | feature-hash vectors, `dimensions` + `encoding_format: base64` supported |
| APIM `azure-openai-semantic-cache-lookup` / `-store` | `semantic_cache` config on this side of the wire | same `score-threshold` distance semantics; see docs/SEMANTIC-CACHE.md |
| Azure Managed Redis vector store | in-process store | restart clears it |
| Token usage / billing meters | `usage` block with ~4 chars/token estimate | feeds the sibling APIM simulator's `llm-token-limit` correctly |
| Azure Monitor / App Insights | `/foundry/management/*` stats + events | admin-key gated |
| `x-ms-region` response header | always `local-simulator` | handy for spotting simulator traffic in captures |

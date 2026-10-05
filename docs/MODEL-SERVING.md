# Model serving

This simulator implements a deliberately small, honest slice of Azure AI
Foundry model serving. It is useful for testing clients and gateways that
need the route, validation, response, streaming, and error contracts. It is
not a model-quality benchmark and it does not host agents.

## Identity and routing

The JSON config has two distinct names:

```json
{
  "name": "gpt-demo",
  "model": "gpt-4o-mini",
  "kind": "chat"
}
```

`name` is the deployment identity. It is used in the deployment URL, the v1
request `model`, and every inference response `model`. The configured
underlying `model` is management metadata only. This matches Azure's
deployment-based routing and prevents a local configuration detail from
changing the client-visible contract.

## Implemented surfaces

| Surface | Implemented behavior |
| --- | --- |
| Azure OpenAI deployment routes | Text chat completions and text embeddings; `api-version` is required. |
| Foundry v1 | Chat completions, embeddings, text-only Responses, and model catalog; `api-version` is optional and accepts `v1` or `preview` when supplied. |
| Azure AI Model Inference | Chat and embeddings at `api-version=2025-04-01` (the legacy `2024-05-01-preview` version is also accepted); request `model` may be omitted only when exactly one compatible deployment exists. |

Chat supports text messages, stop sequences, token limits, deterministic
sampling-field validation, and OpenAI-style SSE. Embeddings support string or
string-array input, configured-dimension reductions, and `float` or `base64`
encoding. Token arrays and other embedding encodings are rejected explicitly.

## Responses

Responses supports text input, `instructions`, metadata, token limits,
`store`, and synchronous or streaming output. Stored responses can be:

- retrieved with `GET /openai/v1/responses/{id}`;
- inspected with `GET /openai/v1/responses/{id}/input_items`;
- deleted with `DELETE /openai/v1/responses/{id}`.

Cancellation is exposed for contract completeness. Since this simulator has
no background execution, a completed response returns
`response_not_in_progress`; `background` requests are rejected at creation.
Stored state is process-local and disappears on restart. Streaming emits the
text Responses event family (`response.created`, output-item/content-part
events, text deltas, and `response.completed`) and does not use chat's
`data: [DONE]` framing.

## Explicit boundaries

The following are not implemented and therefore return a structured
`parameter_not_supported` error rather than being silently ignored:

- tools, function calling, and tool choice;
- image, audio, and other multimodal content;
- background jobs, conversations, previous-response chaining, and MCP;
- agent hosting or orchestration;
- durable response storage and cross-instance state.

On Model Inference routes, the `extra-parameters` request header accepts
`error` (default) and `drop`. `pass-through` is rejected because there is no
remote model adapter to receive an unknown parameter. The Model Inference
validation envelope is distinct from OpenAI's `{"error": ...}` envelope and
reports `status`, `code`, `detail.loc`, and `detail.input`.

## Determinism

The local engine echoes the last user message and estimates tokens at roughly
four characters per token. Temperature, top-p, penalty, and seed fields are
type/range checked for client compatibility, but do not make the local reply
random. Embeddings are deterministic feature-hash vectors. These adaptations
are intentional and are kept separate from the wire-level fidelity claims.

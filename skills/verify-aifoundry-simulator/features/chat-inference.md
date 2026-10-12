# Chat inference

Deterministic chat completions (including SSE streaming), embeddings and the model list, with deployment names as routing identities. Management endpoints are admin-key gated.

## Sub-features

- `chat-basic` returns a completion with real `usage` numbers.
- `chat-stream` streams SSE and ends with `[DONE]`.
- `mgmt-gated` rejects `/foundry/...` management without `X-Foundry-Admin-Key`.

## How to get to it (user POV)

- An OpenAI or Azure SDK pointed at the base URL, or curl.

## Driving it with control.sh

Preconditions:

- `doctor` prints `OK`.

- **Smoke.** `control.sh --execute smoke` gives `smoke_foundry exit=0`, including "SSE streaming with [DONE]" and "management surface admin-gated" (2026-10-10).

## Gotchas

- `gpt-demo` adds 400 ms of simulated latency per call.

# ADR 0002: Runtime-writable management resources

- Status: accepted
- Date: 2026-11-27
- Deciders: repo maintainer

## Context

Until now the management API (`/foundry/management/*`) was read-only, apart
from flushing the semantic cache. The config file was the only way to change
deployments, content filters and cache settings, and a change meant
recreating the app. Content Safety blocklists were the one exception: the
Azure-shaped `/contentsafety/text/blocklists/*` routes edit them in memory.

The sibling apim-simulator now has a Terraform provider
(`terraform-provider-apimsim`). It manages a running simulator through
management routes that each give a resource one URL with PUT, GET and
DELETE. We want the same here, so that a Terraform configuration can declare
the deployments, content filters and blocklists a local Foundry should have,
and a plan shows drift.

A provider needs writable resources. With a read-only API, it could only
manage blocklists.

## Decision

1. **Four resources become writable** under `/foundry/management/`, each with
   one URL that takes PUT, GET and DELETE:

   | Resource | URL |
   | --- | --- |
   | Deployment | `deployments/{name}` |
   | Content filter | `content-filters/{name}` |
   | Blocklist | `blocklists/{name}` |
   | Blocklist item | `blocklists/{name}/items/{item_id}` |

2. **The config file sets the starting state.** It is still loaded and
   validated at startup. Runtime writes change the loaded state in place.
   They are process-local and lost when the app is recreated, as blocklist
   edits already were. Nothing is written back to the file.

3. **Request bodies use the config file's shape and defaults.** A PUT body is
   one entry of the config file's `deployments`, `content_filters` or
   `blocklists` list, parsed by the same functions. A PUT replaces the whole
   resource: a field it leaves out takes the config file's default. This
   differs from apim-simulator, where an omitted field is kept. Here every
   resource has a small, flat body, and full replacement is what Terraform
   needs.

4. **Writes keep the loaded state valid.** Each write is checked with the
   startup reference rules (`_validate_references`) against the state it
   would produce, and is applied only if that passes. A PUT that would break
   a reference returns 422. A DELETE that would leave a dangling reference
   returns 409. Unknown body fields return 422. A body may repeat its own
   path identifier (`name`, `item_id`), so GET, edit, PUT works.

5. **A deployment write flushes the semantic cache.** Cached answers belong to
   a deployment's previous model, settings and embeddings dimensions.

6. **The Terraform provider lives in `terraform-provider-foundrysim/`**, a Go
   module of its own. It copies apim-simulator's client, codec and generic
   resource rather than sharing a module, because the two repositories
   release separately. The codec gains nested-object attributes, for a
   content filter's `categories` and a deployment's `semantic_cache`.

## Consequences

- A Terraform configuration can manage a running simulator, and a plan shows
  drift when something changes it through the API.
- When the container restarts, runtime changes are lost. The next plan shows
  the missing resources and the next apply creates them again. A resource
  defined in the config file and in Terraform is created by both. Terraform
  then owns it after its first apply.
- Each worker process holds its own state. The image runs one uvicorn worker.
  Running several would make writes land on one worker only.
- Resource-level settings (`name`, `auth`, root `semantic_cache`,
  `content_safety`) stay in the config file and still need recreation.
- The contract tests in `tests/test_management_resources.py` cover the
  round-trip rules a declarative client needs.

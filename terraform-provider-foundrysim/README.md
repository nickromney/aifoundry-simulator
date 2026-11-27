# Terraform provider for the AI Foundry simulator (prototype)

Manage a running simulator's model deployments, content filters and
blocklists with Terraform or OpenTofu. Plans show drift when someone changes
the simulator through the management API, and `terraform destroy` removes
what the configuration created.

This is a prototype. It is not published to a registry: you build it from
this checkout and point Terraform at the build. It is adapted from
apim-simulator's `terraform-provider-apimsim`, and works the same way.

## What it changes, and for how long

The simulator holds every change in memory
([ADR 0002](../docs/adr/0002-runtime-writable-management-resources.md)).
When the container restarts, it is back to its config file, and the next
plan shows the Terraform-managed resources as missing. Run `apply` again to
put them back.

Resource-level settings (`auth`, the root `semantic_cache` defaults,
`content_safety`) stay in the config file.

## Before you start

You need:

- Go 1.27 or later (`go version`)
- Terraform or OpenTofu (`terraform version` or `tofu version`)
- `uv`, to run the simulator for the acceptance tests

## Try the example

```bash
make build
```

```bash
make example-plan
```

`example-plan` writes `bin/dev.tfrc`, a CLI config with a `dev_overrides`
entry for this build, and runs `terraform plan` on
[examples/main.tf](examples/main.tf). Expected result: a warning that provider
development overrides are in effect, then `Plan: 5 to add`. The plan needs no
running simulator; an apply does.

To apply, start the simulator from the repository root with `make up`, then:

```bash
TF_CLI_CONFIG_FILE="$PWD/bin/dev.tfrc" terraform -chdir=examples apply
```

OpenTofu reads the same file: replace `terraform` with `tofu`. Then call the
new deployment:

```bash
curl -s -H 'api-key: local-foundry-key-alpha' -H 'content-type: application/json' \
  'http://localhost:8020/openai/deployments/gpt-tf/chat/completions?api-version=2024-10-21' \
  -d '{"messages":[{"role":"user","content":"status of project-falcon"}]}'
```

Expected result: a 400 `content_filter` error, because the blocklist the
example creates matches `project-falcon`.

## Configure the provider

| Attribute | Environment variable | Default |
| --- | --- | --- |
| `endpoint` | `FOUNDRYSIM_ENDPOINT` | `http://localhost:8020` |
| `admin_key` | `FOUNDRYSIM_ADMIN_KEY` | none; sent as `X-Foundry-Admin-Key` |

## Resources

| Resource | Management URL | Import ID |
| --- | --- | --- |
| `foundrysim_deployment` | `deployments/{name}` | `{name}` |
| `foundrysim_content_filter` | `content-filters/{name}` | `{name}` |
| `foundrysim_blocklist` | `blocklists/{name}` | `{name}` |
| `foundrysim_blocklist_item` | `blocklists/{blocklist_name}/items/{item_id}` | `{blocklist_name}/{item_id}` |

Resources from the config file can be imported. Blocklist items from the
config file have the IDs `<blocklist>-<index>`:

```bash
terraform import foundrysim_blocklist_item.secret demo-blocklist/demo-blocklist-0
```

Attributes and defaults follow the config file, with two differences:

- **`content_filter` left out means no filter.** In the config file a
  deployment without `content_filter` gets `default`. Terraform cannot tell
  an omitted attribute from `null`, so the provider treats both as no
  filter. Set `content_filter = "default"` for the built-in policy.
- **Inherited cache settings.** Leave `semantic_cache` out and the
  deployment uses the config file's root defaults. Inside the block, an
  attribute you leave out takes the root default. Removing one attribute
  from the block keeps its last value until the deployment next changes.
  Remove the whole block to inherit all of them again.

A deployment write flushes the simulator's semantic cache.

## Develop

```bash
make check
```

Runs `gofmt`, `go vet` and the unit tests.

```bash
make testacc
```

Starts a throwaway simulator on a free loopback port, from
`examples/foundry.json` with its own admin key, and runs the acceptance
tests against it with a real `terraform` binary. Every apply step is
followed by a refresh and a plan that must be empty, which is the drift
check. The tests also change and delete a resource behind Terraform's back
and check that the next plan notices.

From the repository root, `make provider-check` and `make provider-testacc`
run the same targets.

### Adding a resource

First make the server side pass the round-trip contract in
[tests/test_management_resources.py](../tests/test_management_resources.py)
by adding the resource to `RESOURCES` there. Then add a model struct and a
`spec` entry in [internal/provider/resources.go](internal/provider/resources.go).
Struct tags say where each field goes:

```go
type blocklistItemModel struct {
    ID            types.String `tfsdk:"id"`                           // path params joined by "/"
    BlocklistName types.String `tfsdk:"blocklist_name" fdy:"path"`    // URL segment
    ItemID        types.String `tfsdk:"item_id" fdy:"path"`           // URL segment
    Text          types.String `tfsdk:"text"`                         // PUT body and GET
}
```

Fields can be strings, bools, integers, floats, lists of strings, and objects
of scalars (`types.Object`, typed from the schema). The `id` attribute and
the path-parameter attributes are added for you, and
`TestEveryResourceSchemaMatchesItsModel` fails if the schema and the model
disagree. Give every attribute the server defaults the same default in the
schema, so removing it from configuration resets it.

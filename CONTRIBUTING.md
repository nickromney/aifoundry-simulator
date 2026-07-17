# Contributing

This repo follows the same delivery discipline as the sibling
[apim-simulator](https://github.com/nickromney/apim-simulator).

## Setup

```bash
uv sync --extra dev
make hooks          # lefthook pre-commit (staged lint) + pre-push (local-ci)
```

## Before opening a PR

```bash
make local-ci       # gitleaks + lint + test + compose-config
```

There is no push-triggered CI; the pre-push hook runs the same gate
locally. Skip it only with a reason (`LEFTHOOK=0 git push`).

## Ground rules

- **Honest labels.** Any behaviour that simulates rather than mirrors Azure
  is labelled `adapted` in [contracts/contract_matrix.yml](contracts/contract_matrix.yml)
  and explained in [docs/SCOPE.md](docs/SCOPE.md). New surfaces need a
  matrix entry with an owning test — `tests/test_contract_matrix.py`
  enforces this.
- **Config is the source of truth.** The management surface stays
  read-only (except the cache flush); the CLI stays a thin HTTP client
  with no private back doors.
- **No offensive content.** Content-safety behaviour is exercised through
  simulation triggers and the mild demo lexicons; keep it that way.
- **Coverage gate.** `make test` enforces 75% branch coverage; keep new
  code tested at that bar or better.

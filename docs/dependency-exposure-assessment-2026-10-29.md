# Dependency exposure assessment — 2026-10-29

**Status: inventory and advisory comparison complete; remediation follow-up
remains.** This records the source evidence for
[issue #7](https://github.com/nickromney/aifoundry-simulator/issues/7). The
scan found affected locked versions; source reachability differs by finding.

## Revision and inputs

- Repository revision: `1b850d8e5cf01a730ffff2a10be9e8ddc118e51b` (`main`).
- Python requirement: `>=3.13`; `uv.lock` resolves for that selector.
- Resolved with CPython 3.13.18, `uv 0.13.6` (2026-10-26), and `pip-audit 2.10.1`.
- Advisory snapshot: OSV scan dated 2026-10-29, retained at
  `security-remediation-evidence/aifoundry-issue7-osv-20261029.json`.
- Package inputs: `pyproject.toml` and `uv.lock` (both tracked at the revision above).
- The lock resolves 28 third-party packages, all from the PyPI registry. The
  project itself is editable and is not counted as a third-party package.
- `tool.uv.exclude-newer-span = "P7D"` is the existing seven-day resolution
  cooldown. This assessment made no dependency changes and selected no new
  versions.

## Resolved packages and use

The runtime column includes the three direct application dependencies and their
transitive dependencies. `colorama` is conditional on `sys_platform ==
'win32'`. Development-only packages are the six declared `dev` extras and their
transitives. No package is Git- or URL-sourced.

| Scope | Locked packages |
| --- | --- |
| Runtime (17) | `annotated-doc==0.0.4`, `annotated-types==0.7.0`, `anyio==4.14.1`, `certifi==2026.6.17`, `click==8.4.2`, `colorama==0.4.6` (Windows only), `fastapi==0.139.0`, `h11==0.16.0`, `httpcore==1.0.9`, `httpx==0.28.1`, `idna==3.18`, `pydantic==2.13.4`, `pydantic-core==2.46.4`, `starlette==1.3.1`, `typing-extensions==4.16.0`, `typing-inspection==0.4.2`, `uvicorn==0.51.0` |
| Development only (11) | `coverage==7.15.0`, `iniconfig==2.3.0`, `packaging==26.2`, `pathspec==1.1.1`, `pluggy==1.6.0`, `pygments==2.20.0`, `pytest==9.1.1`, `pytest-cov==7.1.0`, `pyyaml==6.0.3`, `ruff==0.15.21`, `yamllint==1.38.0` |

The application declares `hatchling` as its PEP 517 build backend in
`pyproject.toml`, but it is not resolved in `uv.lock`. The container recipe uses
`uv sync --frozen --no-dev --no-install-project`, so that backend is not part of
the locked runtime container environment. Its version and build-isolation
dependencies remain unassessed. The container also selects tagged Docker Hardened
Python images and `astral-sh/uv:0.11.28`; this package-only assessment does not
inventory their OS packages or prove the digest or contents of a built image.

## Advisory comparison

The retained OSV result covers 27 third-party packages from the lock export for
the current macOS / CPython 3.13.18 marker selection. `colorama==0.4.6` is the
28th locked package, but its `sys_platform == 'win32'` marker excludes it from
this host's audit. The result contains eight advisory records, which reduce to
four distinct IDs because the report repeats AnyIO aliases and lists two fixed
certifi versions:

| Locked package | Advisory | Fixed version(s) in OSV result | Source applicability |
| --- | --- | --- | --- |
| `anyio==4.14.1` | [GHSA-3w57-8xmc-8v26 / CVE-2026-63349](https://github.com/advisories/GHSA-3w57-8xmc-8v26) — process launch ignores `extra_groups` | `4.14.2` | The upstream advisory affects exactly AnyIO `4.14.0`; `4.14.1` is outside its affected range. AnyIO remains transitive through Starlette and HTTPX. |
| `anyio==4.14.1` | [GHSA-5p39-cfhj-2xmp / CVE-2026-64847](https://github.com/advisories/GHSA-5p39-cfhj-2xmp) — process-pool workers may block on undrained stderr | `4.14.2` | The affected range is `<4.14.2`, which includes the locked version. No application source calls AnyIO process-pool APIs; this advisory-specific path was not found. |
| `anyio==4.14.1` | [GHSA-82r6-8w77-94w6 / CVE-2026-63374](https://github.com/advisories/GHSA-82r6-8w77-94w6) — TLSStream hostname encoding | `4.14.2` | The affected range is `<4.14.2`, which includes the locked version. No application source calls `TLSStream`; the CLI uses synchronous `httpx.Client`, not `AsyncClient`, so the affected API path was not found. |
| `certifi==2026.6.17` | [GHSA-6q48-6c7w-9gw2](https://github.com/advisories/GHSA-6q48-6c7w-9gw2) — bundled emSign roots and compromised intermediate keys | `2026.10.18` or `2026.10.19` | Certifi is a runtime dependency of HTTPX. `app/cli.py` creates `httpx.Client` and accepts an arbitrary `--base-url`; an HTTPS connection through this client uses the trust bundle. The default URL is local HTTP, so exposure depends on using an HTTPS target with a chain affected by the compromised roots. This is a plausible runtime path and should be remediated. |

The OSV export did not include severity or registry upload timestamps. I did not
classify severity from CVE numbering. The repository's seven-day policy is
`tool.uv.exclude-newer-span = "P7D"`. The fixed versions are not in the current
lock, and the retained scan does not prove their PyPI upload times. Because this
follow-up run had no network, I did not resolve candidates or edit `uv.lock`;
the seven-day cutoff must be enforced and its selected versions recorded by the
next locked-resolution step. No dependency was downloaded or installed here.

The local lock pins Starlette 1.3.1. The reviewed GitHub advisory
[GHSA-82w8-qh3p-5jfq / CVE-2026-54283](https://github.com/advisories/GHSA-82w8-qh3p-5jfq)
affects versions below 1.3.1 and names 1.3.1 as patched. This locked version is
therefore outside that advisory's affected range. Source search also found no
`request.form()` use in `app/` or `tests/`; this is additional reachability
context, not a substitute for the fixed-version comparison. The lock's Starlette
version is also above the 1.3.0 patched floor for
[GHSA-jp82-jpqv-5vv3 / CVE-2026-54282](https://github.com/advisories/GHSA-jp82-jpqv-5vv3).

The manual Starlette matches are already fixed in the assessed lock. The
OSV scan found the AnyIO and certifi versions above; the report's duplicate
entries are not separate vulnerabilities.

## Remediation follow-ups — 2026-10-29

- [Issue #8: Update locked certifi to remove compromised emSign roots](https://github.com/nickromney/aifoundry-simulator/issues/8)
  tracks the confirmed certifi advisory match and focused lock-only remediation.
- [Issue #9: Update locked AnyIO past affected process-pool and TLS ranges](https://github.com/nickromney/aifoundry-simulator/issues/9)
  tracks the two AnyIO advisories and the API reachability context.

The local `uv.lock` now contains a certifi-only candidate at `2026.10.19`
instead of the assessment baseline's `2026.6.17`. That candidate is local and
unpublished; GitHub `main` still has `certifi==2026.6.17`. This later local
candidate does not change the scan baseline or the advisory snapshot recorded
above. The local lock still contains `anyio==4.14.1`; AnyIO has not been
remediated. Issue #7's baseline finding remains valid, with #8 and #9 as its
independently testable remediation follow-ups.

## Coverage blocker and commands

The lock was exported without installing or updating packages:

```text
UV_CACHE_DIR=/private/tmp/aifoundry-uv-cache uv export --locked --offline \
  --format requirements.txt --no-emit-project --all-extras --no-annotate --no-header
Resolved 29 packages in 2ms
```

The initial local `pip-audit` attempt used the pinned export with dependency
resolution and pip disabled, but could not resolve `api.osv.dev`. A later
public-only OSV scan was run by the parent and retained at the path above. Its
result is treated as the advisory source for this assessment; the local blocked
attempt is not presented as a clean result.

```text
pip-audit --requirement /private/tmp/aifoundry-simulator-issue7-requirements.txt \
  --no-deps --disable-pip --vulnerability-service osv --format json
```

The shared OSV report contains no advisory severity field, so severity-based
ranking remains unknown. The parent reports the host-network audit exited 1
with eight records across two packages. The retained JSON records exact locked
package versions, IDs, aliases, descriptions and fixed versions for 27
applicable packages. The Windows-only `colorama` marker was not scanned, and
the report does not include the unpinned PEP 517 `hatchling` backend or
container image contents; those remain separate coverage blockers.

## Verification boundary

The source tree, dependency declarations, lock graph, Python selector and
container recipe were inspected. No package was installed or upgraded, no
container was built or launched, no service or production system was contacted,
and no deployment/image digest was inspected. This document proves only the
listed source facts at `1b850d8e5cf01a730ffff2a10be9e8ddc118e51b`.

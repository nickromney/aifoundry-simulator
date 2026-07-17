# Security

This project is a local development and testing tool. It is not a
production service and must not be exposed to the internet.

- All `local-foundry-*` keys in the repository are intentional demo values.
- State (semantic cache, runtime blocklists, stats) is in-memory only.
- The container runs non-root with a read-only root filesystem, dropped
  capabilities, and `no-new-privileges`.

To report a vulnerability in the simulator itself, open a GitHub issue or
contact the maintainer directly.

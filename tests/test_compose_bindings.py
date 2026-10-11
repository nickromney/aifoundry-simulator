"""Published-port bindings in every compose file must default to loopback.

Docker binds a bare ``"HOST:CONTAINER"`` port on all interfaces, which would
expose the demo-key simulator to the LAN. These tests resolve each short-syntax
port entry with its ``${VAR:-default}`` defaults (no Docker needed) and assert
the host address is 127.0.0.1.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from unittest import mock

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
LOOPBACK = "127.0.0.1"
_INTERPOLATION = re.compile(r"\$\{(\w+):-([^}]*)\}")


def _compose_files() -> list[Path]:
    skipped = {".venv", ".git"}
    return sorted(
        path for path in REPO_ROOT.rglob("compose*.yml") if not skipped.intersection(path.relative_to(REPO_ROOT).parts)
    )


def _resolve(value: str) -> str:
    """Resolve ``${VAR:-default}`` using the environment, then the default."""

    def replace(match: re.Match[str]) -> str:
        return os.environ.get(match.group(1)) or match.group(2)

    return _INTERPOLATION.sub(replace, value)


def _published_ports(compose_path: Path) -> list[tuple[str, str]]:
    """Return (host_ip, host_port) for each short-syntax port entry."""
    document = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    published: list[tuple[str, str]] = []
    for service in (document.get("services") or {}).values():
        for entry in service.get("ports") or []:
            if isinstance(entry, dict):
                published.append((str(entry.get("host_ip", "")), str(entry.get("published", ""))))
                continue
            parts = _resolve(str(entry)).split(":")
            assert len(parts) == 3, f"{compose_path}: unexpected port entry {entry!r}"
            published.append((parts[0], parts[1]))
    return published


def test_compose_files_are_discovered():
    names = {path.relative_to(REPO_ROOT).as_posix() for path in _compose_files()}
    assert "compose.yml" in names
    assert "examples/apim-integration/compose.yml" in names


@pytest.mark.parametrize(
    "compose_path",
    _compose_files(),
    ids=lambda path: path.relative_to(REPO_ROOT).as_posix(),
)
def test_published_ports_default_to_loopback(compose_path):
    with mock.patch.dict(os.environ, {}, clear=True):
        published = _published_ports(compose_path)
    assert published, f"{compose_path} publishes no ports to check"
    assert all(address == LOOPBACK for address, _ in published)


def test_foundry_port_stays_a_number_and_bind_address_is_overridable():
    compose_path = REPO_ROOT / "compose.yml"
    with mock.patch.dict(os.environ, {}, clear=True):
        assert _published_ports(compose_path) == [(LOOPBACK, "8020")]
    with mock.patch.dict(os.environ, {"FOUNDRY_PORT": "8120"}, clear=True):
        assert _published_ports(compose_path) == [(LOOPBACK, "8120")]
    with mock.patch.dict(os.environ, {"FOUNDRY_BIND_ADDRESS": "0.0.0.0"}, clear=True):
        assert _published_ports(compose_path) == [("0.0.0.0", "8020")]

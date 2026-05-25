"""Shared fixtures for component tests that need a running Netbox.

Component tests connect to a live Netbox instance instead of mocking it.
The pattern: read `NETBOX_URL` and a token (from `NETBOX_TOKEN` or from
`~/.host-config/netbox-token` written by the netbox-dev Ansible role).
If Netbox isn't reachable, every component test is skipped with a clear
message — so unit-only test runs still pass cleanly without a Netbox
container.

Test markers:

- `@pytest.mark.slow` — applied automatically to every test here via
  the `netbox_client` fixture; lets CI shard unit-only vs full runs.
- `@pytest.mark.requires_netbox` — explicit opt-in marker for tests
  that mean "skip me unless real Netbox is here." Also auto-applied.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pynetbox
import pytest
import requests

# The default location the netbox-dev role writes the API token to.
DEFAULT_TOKEN_FILE = Path.home() / ".host-config" / "netbox-token"
DEFAULT_NETBOX_URL = "http://127.0.0.1:8000"


def _resolve_netbox_url() -> str:
    """`NETBOX_URL` env override, else the local-dev default."""
    return os.environ.get("NETBOX_URL", DEFAULT_NETBOX_URL)


def _resolve_netbox_token() -> str | None:
    """Token resolution order: `NETBOX_TOKEN` env → ~/.host-config/netbox-token → None."""
    if env_token := os.environ.get("NETBOX_TOKEN"):
        return env_token
    if DEFAULT_TOKEN_FILE.exists():
        return DEFAULT_TOKEN_FILE.read_text().strip()
    return None


def _netbox_reachable(url: str, token: str | None) -> bool:
    """Quick reachability check before tests run.

    Approach:
        GET /api/ with the token; accept 200. Any other status (including
        timeouts, connection refused, auth failures) means "not ready
        for component tests" — skip rather than fail.
    """
    if not token:
        return False
    try:
        resp = requests.get(
            f"{url.rstrip('/')}/api/",
            headers={"Authorization": f"Token {token}"},
            timeout=2.0,
        )
    except requests.RequestException:
        return False
    return bool(resp.status_code == 200)


@pytest.fixture(scope="session")
def netbox_url() -> str:
    """Netbox URL the component tests should use."""
    return _resolve_netbox_url()


@pytest.fixture(scope="session")
def netbox_token() -> str:
    """Netbox API token; tests skip if not resolvable."""
    token = _resolve_netbox_token()
    if not token:
        pytest.skip(
            "no Netbox API token available; set NETBOX_TOKEN or run "
            "the netbox-dev Ansible role to write ~/.host-config/netbox-token"
        )
    return token


@pytest.fixture(scope="session")
def netbox_client(netbox_url: str, netbox_token: str) -> Iterator[pynetbox.api]:
    """A `pynetbox.api` client connected to a running Netbox.

    Skips the entire session of component tests if Netbox is unreachable
    or auth fails.
    """
    if not _netbox_reachable(netbox_url, netbox_token):
        pytest.skip(
            f"Netbox not reachable at {netbox_url}; "
            "bring it up with `ansible-playbook infra/ansible/playbooks/netbox-dev.yml`"
        )
    yield pynetbox.api(netbox_url, token=netbox_token)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-mark every test in tests/component/ as slow + requires_netbox.

    Approach:
        Pytest collects component tests with whatever explicit markers
        they carry; this hook adds the implicit ones so test selection
        works consistently (`pytest -m 'not slow'` excludes them, etc.).
    """
    component_root = Path(__file__).parent
    for item in items:
        item_path = Path(item.fspath)
        try:
            item_path.relative_to(component_root)
        except ValueError:
            continue  # not under tests/component/
        item.add_marker(pytest.mark.slow)
        item.add_marker(pytest.mark.requires_netbox)

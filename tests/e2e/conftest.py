"""Shared fixtures for e2e tests that require the full lab stack.

E2E tests require:
- KVM acceleration (/dev/kvm accessible)
- OVS bridge br-test configured (via ovs-harness Ansible role)
- QEMU/libvirt installed (via qemu-host Ansible role)
- A live Netbox with fixtures loaded (same prereqs as component tests)
- A running renderer service (reachable at RENDERER_URL)
- A running nginx-cache (reachable at SEED_SERVER_URL)
- A prepared Ubuntu 24.04 cloud image at E2E_IMAGE_PATH

All tests in this directory are auto-marked with @e2e and @requires_kvm
and are skipped if any prerequisite is absent.

Environment variables (all optional, sensible defaults):
    NETBOX_URL          Netbox base URL (default: http://127.0.0.1:8000)
    NETBOX_TOKEN        API token (default: ~/.host-config/netbox-token)
    RENDERER_URL        Renderer service URL (default: http://127.0.0.1:8080)
    SEED_SERVER_URL     nginx-cache URL (default: http://127.0.0.1:80)
    E2E_IMAGE_PATH      Path to prepared Ubuntu cloud image
    E2E_SSH_KEY_PATH    Path to SSH private key for VM access (default: ~/.ssh/id_rsa)
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import requests

if TYPE_CHECKING:
    import pynetbox

_DEFAULT_NETBOX_URL = "http://127.0.0.1:8000"
_DEFAULT_RENDERER_URL = "http://127.0.0.1:8080"
_DEFAULT_SEED_SERVER_URL = "http://127.0.0.1:80"
_DEFAULT_TOKEN_FILE = Path.home() / ".host-config" / "netbox-token"
# Default SSH key for authenticating into test VMs.  This is a test
# infrastructure key baked into the base image by prepare_image.py --prepare.
_DEFAULT_SSH_KEY = Path(__file__).parent / "fixtures" / "test_vm_key"
_DEFAULT_IMAGE_PATH = (
    Path(__file__).parents[2] / "fixtures" / "vms" / "images" / "ubuntu-noble-base.img"
)


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _check_kvm() -> bool:
    return Path("/dev/kvm").exists()


def _check_ovs_bridge(bridge: str = "br-test") -> bool:
    try:
        result = subprocess.run(  # noqa: S603
            ["ovs-vsctl", "br-exists", bridge],  # noqa: S607
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def _check_service(url: str) -> bool:
    try:
        return requests.get(url, timeout=3).status_code < 500
    except Exception:
        return False


def _resolve_netbox_token() -> str | None:
    if env := os.environ.get("NETBOX_TOKEN"):
        return env
    if _DEFAULT_TOKEN_FILE.exists():
        return _DEFAULT_TOKEN_FILE.read_text().strip()
    return None


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-mark all e2e tests."""
    for item in items:
        if "e2e" in str(item.fspath):
            item.add_marker(pytest.mark.e2e)
            item.add_marker(pytest.mark.requires_kvm)
            item.add_marker(pytest.mark.slow)


@pytest.fixture(scope="session")
def e2e_skip_reason() -> str | None:  # noqa: PLR0911
    """Return a skip reason string if any e2e prerequisite is absent; else None."""
    if not _check_kvm():
        return "/dev/kvm not found — KVM acceleration required for e2e tests"
    if not _check_ovs_bridge():
        return "OVS bridge br-test not found — run ovs-harness role first"
    token = _resolve_netbox_token()
    if not token:
        return "No Netbox token found — run netbox-dev role or set NETBOX_TOKEN"
    netbox_url = _env("NETBOX_URL", _DEFAULT_NETBOX_URL)
    if not _check_service(f"{netbox_url}/api/"):
        return f"Netbox not reachable at {netbox_url}"
    renderer_url = _env("RENDERER_URL", _DEFAULT_RENDERER_URL)
    if not _check_service(f"{renderer_url}/healthz"):
        return f"Renderer not reachable at {renderer_url}"
    seed_url = _env("SEED_SERVER_URL", _DEFAULT_SEED_SERVER_URL)
    if not _check_service(f"{seed_url}/healthz"):
        return f"nginx-cache not reachable at {seed_url}"
    image_path = Path(_env("E2E_IMAGE_PATH", str(_DEFAULT_IMAGE_PATH)))
    if not image_path.exists():
        return f"Cloud image not found at {image_path} — run: python -m fixtures.vms.prepare_image"
    return None


@pytest.fixture(scope="session")
def netbox_client(e2e_skip_reason: str | None) -> pynetbox.api:
    """Authenticated pynetbox client; skips if prerequisites are absent."""
    if e2e_skip_reason:
        pytest.skip(e2e_skip_reason)
    import pynetbox  # noqa: PLC0415 — deferred to skip early

    url = _env("NETBOX_URL", _DEFAULT_NETBOX_URL)
    token = _resolve_netbox_token()
    return pynetbox.api(url, token=token)


@pytest.fixture(scope="session")
def seed_server_url(e2e_skip_reason: str | None) -> str:
    """Base URL of the nginx-cache (seed server)."""
    if e2e_skip_reason:
        pytest.skip(e2e_skip_reason)
    return _env("SEED_SERVER_URL", _DEFAULT_SEED_SERVER_URL)


@pytest.fixture(scope="session")
def e2e_image_path(e2e_skip_reason: str | None) -> Path:
    """Path to the prepared Ubuntu cloud image."""
    if e2e_skip_reason:
        pytest.skip(e2e_skip_reason)
    return Path(_env("E2E_IMAGE_PATH", str(_DEFAULT_IMAGE_PATH)))


@pytest.fixture(scope="session")
def ssh_key_path() -> Path:
    return Path(_env("E2E_SSH_KEY_PATH", str(_DEFAULT_SSH_KEY)))

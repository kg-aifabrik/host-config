"""Component tests for `host_config.netbox.loader.load_host_intent`.

Runs against a real Netbox instance populated by the fixture loader.
Skips if Netbox is unreachable.

Acceptance per M2-2:
- `load_host_intent(client, asset_tag)` returns a fully validated
  `HostIntent` for both the CPU and gpu-b300 fixture hosts.
- An unknown asset tag raises `HostNotFoundError`.

The fixture data (asset tags, hostnames, MACs, IPs) lives in
`fixtures/netbox/data/{cpu-host,b300-host}.yaml`. Those files are the
source of truth — these tests reference values that must match.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fixtures.netbox.populate import load_fixture, populate

from host_config.models.intent import Role
from host_config.netbox.errors import HostNotFoundError
from host_config.netbox.loader import load_host_intent

if TYPE_CHECKING:
    import pynetbox

# Asset tags must match the YAML fixtures populated by
# `fixtures/netbox/populate.py`.
CPU_ASSET_TAG = "SN-CPU-001"
B300_ASSET_TAG = "SN-GPU-001"

FIXTURE_ROOT = Path(__file__).parents[3] / "fixtures" / "netbox" / "data"


@pytest.fixture(scope="module", autouse=True)
def _populate_fixtures(netbox_client: pynetbox.api) -> None:
    """Ensure the YAML fixtures are loaded before any loader test runs.

    Approach:
        Import the fixture loader and run it idempotently against the
        two YAML files. The populate script is idempotent (M1-3); running
        it twice in the same Netbox is a no-op the second time.
    """
    fixtures = [load_fixture(FIXTURE_ROOT / name) for name in ("cpu-host.yaml", "b300-host.yaml")]
    populate(netbox_client, fixtures)


def test_load_cpu_host(netbox_client: pynetbox.api) -> None:
    """The CPU fixture round-trips to a valid `HostIntent`.

    Approach:
        Call the loader with the CPU asset tag; assert role, hostname,
        bond/member shape, and the three VLAN children (mgmt/storage/
        ingress) are present.
    """
    intent = load_host_intent(netbox_client, CPU_ASSET_TAG)

    assert intent.role == Role.CPU
    assert intent.hostname.startswith("k8s-cp-01")
    assert {m.name for m in intent.ns_nics} == {"nsa", "nsb"}
    assert intent.bond.name == "bond0"
    assert {v.vlan_id for v in intent.vlans} == {100, 200, 300}
    # The mgmt VLAN must have a gateway derived from its prefix.
    mgmt = next(v for v in intent.vlans if v.vlan_id == 100)
    assert mgmt.gateway is not None


def test_load_b300_host(netbox_client: pynetbox.api) -> None:
    """The gpu-b300 fixture round-trips to a valid `HostIntent`.

    Approach:
        Call the loader with the B300 asset tag; assert the GPU role,
        eight RoCE underlays (gpu0..gpu7), and the same three VLAN
        children as the CPU host.
    """
    intent = load_host_intent(netbox_client, B300_ASSET_TAG)

    assert intent.role == Role.GPU_B300
    assert {n.name for n in intent.roce_underlays} == {f"gpu{i}" for i in range(8)}
    assert {v.vlan_id for v in intent.vlans} == {100, 200, 300}


def test_unknown_asset_tag_raises(netbox_client: pynetbox.api) -> None:
    """An asset tag not present in Netbox raises `HostNotFoundError`.

    Why:
        The caller (renderer service) needs an explicit, typed error so
        it can map to HTTP 404 instead of a 500 from a stray AttributeError.
    """
    with pytest.raises(HostNotFoundError):
        load_host_intent(netbox_client, "SN-DOES-NOT-EXIST")

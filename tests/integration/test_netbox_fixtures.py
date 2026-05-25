"""Integration test (M1.5 gate): Netbox fixture round-trip.

Brings a live Netbox to the state the rest of the test pyramid relies
on — schema applied, both fixture hosts (CPU + B300) populated — and
queries them back to assert the topology survives the round-trip.

Acceptance per M1.5-1:
- Apply schema + populate fixtures against a real Netbox.
- Query the B300 host back via pynetbox.
- Assert: 10 interfaces, correct VLANs, correct IPs, custom fields populated.
- Same assertions for the CPU host.
- Total runtime < 30 s on an already-running Netbox.

This is the canonical "is Netbox set up correctly for our renderer?" smoke test.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fixtures.netbox.populate import load_fixture, populate

from host_config.netbox.schema import apply_schema

if TYPE_CHECKING:
    import pynetbox


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE_DIR = REPO_ROOT / "fixtures" / "netbox" / "data"


@pytest.fixture(scope="module")
def populated_netbox(netbox_client: pynetbox.api) -> pynetbox.api:
    """Ensure the Netbox under test has the schema applied and fixtures loaded.

    Approach:
        Idempotent setup: apply schema (no-op on re-runs) then run the
        populator (no-op on re-runs). Returns the live client for the
        tests to query. Scoped at module level so all tests in this
        file share the setup cost.

    Raises (via populate / apply_schema):
        FixtureConflictError / NetboxQueryError / SchemaError if Netbox
        is in an unexpected state; the test stops rather than asserting
        on bad data.
    """
    apply_schema(netbox_client)

    yaml_files = sorted(FIXTURE_DIR.glob("*.yaml"))
    assert yaml_files, f"no fixtures under {FIXTURE_DIR}"
    fixtures = [load_fixture(p) for p in yaml_files]

    populate(netbox_client, fixtures)
    return netbox_client


class TestCpuHost:
    """Round-trip checks for the SN-CPU-001 host."""

    ASSET = "SN-CPU-001"

    @pytest.mark.fast
    def test_device_exists(self, populated_netbox: pynetbox.api) -> None:
        """The device exists and has the expected name + role."""
        dev = populated_netbox.dcim.devices.get(asset_tag=self.ASSET)
        assert dev is not None
        assert dev.name == "k8s-cp-01.pod07.site03.internal"
        assert dev.role.slug == "cpu"

    @pytest.mark.fast
    def test_six_interfaces_present(self, populated_netbox: pynetbox.api) -> None:
        """Two N-S NICs + bond + three VLAN children = 6 interfaces."""
        dev = populated_netbox.dcim.devices.get(asset_tag=self.ASSET)
        ifaces = list(populated_netbox.dcim.interfaces.filter(device_id=dev.id))
        names = {i.name for i in ifaces}
        assert names == {"nsa", "nsb", "bond0", "bond0.100", "bond0.200", "bond0.300"}

    @pytest.mark.fast
    def test_macs_match(self, populated_netbox: pynetbox.api) -> None:
        """The N-S NICs carry the deterministic MACs declared in YAML."""
        dev = populated_netbox.dcim.devices.get(asset_tag=self.ASSET)
        nsa = populated_netbox.dcim.interfaces.get(device_id=dev.id, name="nsa")
        nsb = populated_netbox.dcim.interfaces.get(device_id=dev.id, name="nsb")
        # Netbox 4.x returns MAC objects; normalize via str()
        assert _mac_str(nsa) == "aa:bb:cc:00:01:01"
        assert _mac_str(nsb) == "aa:bb:cc:00:01:02"

    @pytest.mark.fast
    def test_vlans_assigned_to_children(self, populated_netbox: pynetbox.api) -> None:
        """Each VLAN child interface points at the right VLAN VID."""
        dev = populated_netbox.dcim.devices.get(asset_tag=self.ASSET)
        for vlan_name, vid in [("bond0.100", 100), ("bond0.200", 200), ("bond0.300", 300)]:
            iface = populated_netbox.dcim.interfaces.get(device_id=dev.id, name=vlan_name)
            assert iface.untagged_vlan is not None, f"{vlan_name} missing untagged_vlan"
            assert iface.untagged_vlan.vid == vid

    @pytest.mark.fast
    def test_ips_assigned(self, populated_netbox: pynetbox.api) -> None:
        """Each VLAN child has the IP from the fixture."""
        dev = populated_netbox.dcim.devices.get(asset_tag=self.ASSET)
        # Map iface name → expected IP from cpu-host.yaml.
        expected = {
            "bond0.100": "10.42.10.11/24",
            "bond0.200": "10.42.20.11/24",
            "bond0.300": "10.42.30.11/24",
        }
        for name, expected_ip in expected.items():
            iface = populated_netbox.dcim.interfaces.get(device_id=dev.id, name=name)
            ips = list(populated_netbox.ipam.ip_addresses.filter(interface_id=iface.id))
            assert len(ips) == 1, f"{name}: expected 1 IP, got {len(ips)}"
            assert ips[0].address == expected_ip


class TestB300Host:
    """Round-trip checks for the SN-GPU-001 host (the canonical 10-NIC shape)."""

    ASSET = "SN-GPU-001"

    @pytest.mark.fast
    def test_device_exists_with_custom_field(self, populated_netbox: pynetbox.api) -> None:
        """The B300 device exists with bf3_mode custom field populated."""
        dev = populated_netbox.dcim.devices.get(asset_tag=self.ASSET)
        assert dev is not None
        assert dev.role.slug == "gpu-b300"
        assert dev.custom_fields.get("bf3_mode") == "nic"

    @pytest.mark.fast
    def test_ten_physical_nics_present(self, populated_netbox: pynetbox.api) -> None:
        """The B300 has exactly 10 physical NICs (2 N-S + 8 E-W)."""
        dev = populated_netbox.dcim.devices.get(asset_tag=self.ASSET)
        ifaces = list(populated_netbox.dcim.interfaces.filter(device_id=dev.id))
        physical = [i for i in ifaces if _mac_str(i)]
        assert len(physical) == 10
        names = {i.name for i in physical}
        assert names == {"nsa", "nsb", *{f"gpu{i}" for i in range(8)}}

    @pytest.mark.fast
    def test_east_west_custom_fields_populated(self, populated_netbox: pynetbox.api) -> None:
        """Every gpu0..gpu7 NIC has the expected custom-field values."""
        dev = populated_netbox.dcim.devices.get(asset_tag=self.ASSET)
        for i in range(8):
            iface = populated_netbox.dcim.interfaces.get(device_id=dev.id, name=f"gpu{i}")
            cf = iface.custom_fields
            assert cf.get("roce_tc") == 3
            assert cf.get("sriov_vfs") == 16
            assert cf.get("gpu_affinity") == f"GPU{i}"
            # numa_node: 0..3 for gpu0..3; 1 for gpu4..7
            expected_numa = 0 if i < 4 else 1
            assert cf.get("numa_node") == expected_numa

    @pytest.mark.fast
    def test_east_west_ips_assigned(self, populated_netbox: pynetbox.api) -> None:
        """Each east-west NIC has its underlay IP from 10.42.100..107.0/24."""
        dev = populated_netbox.dcim.devices.get(asset_tag=self.ASSET)
        for i in range(8):
            name = f"gpu{i}"
            iface = populated_netbox.dcim.interfaces.get(device_id=dev.id, name=name)
            ips = list(populated_netbox.ipam.ip_addresses.filter(interface_id=iface.id))
            assert len(ips) == 1
            assert ips[0].address == f"10.42.{100 + i}.23/24"

    @pytest.mark.fast
    def test_bond_lag_membership(self, populated_netbox: pynetbox.api) -> None:
        """bond0's two members (nsa, nsb) point at bond0 via their `lag` field."""
        dev = populated_netbox.dcim.devices.get(asset_tag=self.ASSET)
        bond = populated_netbox.dcim.interfaces.get(device_id=dev.id, name="bond0")
        for member_name in ("nsa", "nsb"):
            iface = populated_netbox.dcim.interfaces.get(device_id=dev.id, name=member_name)
            assert iface.lag is not None, f"{member_name} not in any LAG"
            assert iface.lag.id == bond.id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mac_str(iface: object) -> str:
    """Render an interface's primary MAC as a canonical lowercase string.

    In Netbox 4.2+, MACs live in a first-class `dcim.mac_addresses`
    endpoint. The interface's `primary_mac_address` field is a nested
    record with a `mac_address` string attribute we extract.
    """
    primary = getattr(iface, "primary_mac_address", None)
    if primary is None:
        return ""
    # pynetbox returns this as a Record; .mac_address is the string.
    value = getattr(primary, "mac_address", None) or str(primary)
    return str(value).lower()

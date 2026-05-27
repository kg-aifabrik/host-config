"""Tests for `host_config.netbox.loader`.

Unit-level. Mocked Netbox client throughout. Validates the boundary
behavior of `load_host_intent` — typed errors on every failure mode,
correct mapping from Netbox shape to `HostIntent` for happy paths.

The component-level test against a real Netbox (with both fixture
hosts populated) lives in `tests/component/netbox/test_loader.py`,
and the M2.5 gate verifies the full HTTP path.
"""

from __future__ import annotations

from ipaddress import IPv4Interface
from unittest.mock import MagicMock

import pytest

from host_config.models.intent import HostIntent, Role
from host_config.models.vlan import VlanRole
from host_config.netbox.errors import HostNotFoundError, NetboxQueryError
from host_config.netbox.loader import load_host_intent

# ---------------------------------------------------------------------------
# Mock factory — builds a pynetbox-like client with a controllable shape.
# ---------------------------------------------------------------------------


def _make_interface(
    *,
    iface_id: int,
    name: str,
    mac: str | None = None,
    mtu: int | None = None,
    untagged_vlan_vid: int | None = None,
    untagged_vlan_name: str | None = None,
    custom_fields: dict[str, object] | None = None,
) -> MagicMock:
    """Build a mock pynetbox interface record."""
    iface = MagicMock()
    iface.id = iface_id
    iface.name = name
    iface.mtu = mtu
    if mac:
        iface.primary_mac_address = MagicMock()
        iface.primary_mac_address.mac_address = mac
    else:
        iface.primary_mac_address = None
    if untagged_vlan_vid is not None:
        iface.untagged_vlan = MagicMock()
        iface.untagged_vlan.vid = untagged_vlan_vid
        iface.untagged_vlan.name = untagged_vlan_name
    else:
        iface.untagged_vlan = None
    iface.custom_fields = custom_fields or {}
    return iface


def _build_cpu_mock_client() -> MagicMock:
    """Build a mock client representing a fully-populated cpu host.

    The shape mirrors `fixtures/netbox/data/cpu-host.yaml` exactly so
    the loader's output can be cross-checked against the expected
    HostIntent.
    """
    client = MagicMock()

    # The device
    device = MagicMock()
    device.id = 1
    device.name = "k8s-cp-01"
    device.role.slug = "cpu"
    device.custom_fields = {}
    client.dcim.devices.get.return_value = device

    # Interfaces
    ifaces = [
        _make_interface(iface_id=10, name="nsa", mac="aa:bb:cc:00:01:01", mtu=9000),
        _make_interface(iface_id=11, name="nsb", mac="aa:bb:cc:00:01:02", mtu=9000),
        _make_interface(iface_id=12, name="bond0", mtu=9000),
        _make_interface(
            iface_id=13,
            name="bond0.100",
            mtu=1500,
            untagged_vlan_vid=100,
            untagged_vlan_name="mgmt",
        ),
        _make_interface(
            iface_id=14,
            name="bond0.200",
            mtu=9000,
            untagged_vlan_vid=200,
            untagged_vlan_name="storage",
        ),
        _make_interface(
            iface_id=15,
            name="bond0.300",
            mtu=1500,
            untagged_vlan_vid=300,
            untagged_vlan_name="ingress",
        ),
    ]
    client.dcim.interfaces.filter.return_value = ifaces

    # IP-address lookups by interface_id
    ip_by_iface_id = {
        13: "10.42.10.11/24",
        14: "10.42.20.11/24",
        15: "10.42.30.11/24",
    }

    def _ip_filter(*, interface_id: int) -> list[MagicMock]:
        ip = ip_by_iface_id.get(interface_id)
        if not ip:
            return []
        rec = MagicMock()
        rec.address = ip
        return [rec]

    client.ipam.ip_addresses.filter.side_effect = _ip_filter
    return client


def _build_b300_mock_client() -> MagicMock:
    """Build a mock client representing a fully-populated gpu-b300 host.

    Mirrors `fixtures/netbox/data/b300-host.yaml`. Used by the
    gpu-b300 happy-path test and a couple of error-injection variants.
    """
    client = _build_cpu_mock_client()
    # Override the device to be gpu-b300 role
    device = MagicMock()
    device.id = 2
    device.name = "gpu-b300-01"
    device.role.slug = "gpu-b300"
    device.custom_fields = {"bf3_mode": "nic"}
    client.dcim.devices.get.return_value = device

    # Update interfaces — same N-S + bond + VLAN shape, plus 8 east-west.
    ns_and_bond = [
        _make_interface(iface_id=10, name="nsa", mac="aa:bb:cc:00:00:01", mtu=9000),
        _make_interface(iface_id=11, name="nsb", mac="aa:bb:cc:00:00:02", mtu=9000),
        _make_interface(iface_id=12, name="bond0", mtu=9000),
        _make_interface(
            iface_id=13,
            name="bond0.100",
            mtu=1500,
            untagged_vlan_vid=100,
            untagged_vlan_name="mgmt",
        ),
        _make_interface(
            iface_id=14,
            name="bond0.200",
            mtu=9000,
            untagged_vlan_vid=200,
            untagged_vlan_name="storage",
        ),
        _make_interface(
            iface_id=15,
            name="bond0.300",
            mtu=1500,
            untagged_vlan_vid=300,
            untagged_vlan_name="ingress",
        ),
    ]
    gpu_ifaces = [
        _make_interface(
            iface_id=20 + i,
            name=f"gpu{i}",
            mac=f"aa:bb:cc:00:00:{0x10 + i:02x}",
            mtu=9000,
            custom_fields={
                "roce_tc": 3,
                "numa_node": 0 if i < 4 else 1,
                "sriov_vfs": 16,
                "gpu_affinity": f"GPU{i}",
            },
        )
        for i in range(8)
    ]
    client.dcim.interfaces.filter.return_value = ns_and_bond + gpu_ifaces

    # Extend IP map with east-west addresses
    ip_by_iface_id = {
        13: "10.42.10.23/24",
        14: "10.42.20.23/24",
        15: "10.42.30.23/24",
        **{20 + i: f"10.42.{100 + i}.23/24" for i in range(8)},
    }

    def _ip_filter(*, interface_id: int) -> list[MagicMock]:
        ip = ip_by_iface_id.get(interface_id)
        if not ip:
            return []
        rec = MagicMock()
        rec.address = ip
        return [rec]

    client.ipam.ip_addresses.filter.side_effect = _ip_filter
    return client


def _build_h200_mock_client() -> MagicMock:
    """Build a mock client representing a fully-populated gpu-h200 host.

    Mirrors `fixtures/netbox/data/h200-host.yaml`: same N-S + bond + VLAN
    shape, plus 8 east-west InfiniBand IPoIB underlays (ib0..ib7) instead
    of RoCE NICs. Exercises the loader's `_build_ib_underlays` path.
    """
    client = _build_cpu_mock_client()
    device = MagicMock()
    device.id = 3
    device.name = "gpu-h200-01"
    device.role.slug = "gpu-h200"
    device.custom_fields = {"bf3_mode": "nic"}
    client.dcim.devices.get.return_value = device

    ns_and_bond = [
        _make_interface(iface_id=10, name="nsa", mac="aa:bb:cc:00:02:01", mtu=9000),
        _make_interface(iface_id=11, name="nsb", mac="aa:bb:cc:00:02:02", mtu=9000),
        _make_interface(iface_id=12, name="bond0", mtu=9000),
        _make_interface(
            iface_id=13, name="bond0.100", mtu=1500,
            untagged_vlan_vid=100, untagged_vlan_name="mgmt",
        ),
        _make_interface(
            iface_id=14, name="bond0.200", mtu=9000,
            untagged_vlan_vid=200, untagged_vlan_name="storage",
        ),
        _make_interface(
            iface_id=15, name="bond0.300", mtu=1500,
            untagged_vlan_vid=300, untagged_vlan_name="ingress",
        ),
    ]
    ib_ifaces = [
        _make_interface(
            iface_id=30 + i,
            name=f"ib{i}",
            mac=f"aa:bb:cc:00:0e:{i:02x}",
            mtu=2044,
            custom_fields={"numa_node": 0 if i < 4 else 1, "gpu_affinity": f"GPU{i}"},
        )
        for i in range(8)
    ]
    client.dcim.interfaces.filter.return_value = ns_and_bond + ib_ifaces

    ip_by_iface_id = {
        13: "10.42.10.30/24",
        14: "10.42.20.30/24",
        15: "10.42.30.30/24",
        **{30 + i: f"10.42.{100 + i}.10/24" for i in range(8)},
    }

    def _ip_filter(*, interface_id: int) -> list[MagicMock]:
        ip = ip_by_iface_id.get(interface_id)
        if not ip:
            return []
        rec = MagicMock()
        rec.address = ip
        return [rec]

    client.ipam.ip_addresses.filter.side_effect = _ip_filter
    return client


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestHappyPaths:
    """End-to-end mappings from mocked Netbox to `HostIntent`.

    These tests pin the mapping rules at the loader's documented
    conventions (interface naming, VLAN role-by-name, gateway derivation).
    A future change to those conventions surfaces here first.
    """

    @pytest.mark.fast
    def test_cpu_host_maps_to_intent(self) -> None:
        """A cpu-role host with the canonical shape maps to a valid HostIntent."""
        client = _build_cpu_mock_client()

        intent = load_host_intent(client, "SN-CPU-001")

        assert isinstance(intent, HostIntent)
        assert intent.role is Role.CPU
        assert intent.hostname == "k8s-cp-01"
        assert {n.name for n in intent.ns_nics} == {"nsa", "nsb"}
        assert intent.bond.name == "bond0"
        assert intent.bond.members == ["nsa", "nsb"]
        assert len(intent.vlans) == 3
        assert intent.roce_underlays == []

    @pytest.mark.fast
    def test_mgmt_vlan_gateway_derived(self) -> None:
        """The mgmt VLAN's gateway is the first usable IP in its prefix.

        Why:
            Netbox doesn't model gateways natively. The loader uses the
            convention "gateway = .1 of the /24". This test pins the
            convention so a future change requires an ADR.
        """
        intent = load_host_intent(_build_cpu_mock_client(), "SN-CPU-001")

        mgmt = next(v for v in intent.vlans if v.role is VlanRole.MGMT)
        storage = next(v for v in intent.vlans if v.role is VlanRole.STORAGE)
        ingress = next(v for v in intent.vlans if v.role is VlanRole.INGRESS)

        assert str(mgmt.gateway) == "10.42.10.1"
        assert storage.gateway is None
        assert ingress.gateway is None

    @pytest.mark.fast
    def test_b300_host_maps_to_intent(self) -> None:
        """A gpu-b300 host with the canonical 10-NIC shape maps to a valid HostIntent."""
        intent = load_host_intent(_build_b300_mock_client(), "SN-GPU-001")

        assert intent.role is Role.GPU_B300
        assert len(intent.roce_underlays) == 8
        # Underlays sorted by name → gpu0..gpu7 ordering is deterministic
        assert [u.name for u in intent.roce_underlays] == [f"gpu{i}" for i in range(8)]
        # Each underlay has the IP from the mock
        for i, underlay in enumerate(intent.roce_underlays):
            assert underlay.address == IPv4Interface(f"10.42.{100 + i}.23/24")
            assert underlay.sriov_vfs == 16

    @pytest.mark.fast
    def test_h200_host_maps_to_intent(self) -> None:
        """A gpu-h200 host maps to a HostIntent with 8 InfiniBand underlays, no RoCE."""
        intent = load_host_intent(_build_h200_mock_client(), "SN-GPU-H200-001")

        assert intent.role is Role.GPU_H200
        assert intent.roce_underlays == []
        assert len(intent.ib_underlays) == 8
        # Underlays sorted by name → ib0..ib7 ordering is deterministic.
        assert [u.name for u in intent.ib_underlays] == [f"ib{i}" for i in range(8)]
        for i, underlay in enumerate(intent.ib_underlays):
            assert underlay.address == IPv4Interface(f"10.42.{100 + i}.10/24")
            assert underlay.mtu == 2044


# ---------------------------------------------------------------------------
# Error paths — one per documented failure mode.
# ---------------------------------------------------------------------------


class TestErrorPaths:
    """Every documented failure mode produces a typed, contextual error.

    Why pinned: the renderer's FastAPI exception handler translates
    these into HTTP status codes (404 for HostNotFoundError, 502 for
    NetboxQueryError, 422 for InvariantError). Changing the exception
    type breaks the HTTP contract.
    """

    @pytest.mark.fast
    def test_unknown_asset_tag_raises_host_not_found(self) -> None:
        """An asset_tag with no matching device raises `HostNotFoundError`."""
        client = MagicMock()
        client.dcim.devices.get.return_value = None

        with pytest.raises(HostNotFoundError) as exc:
            load_host_intent(client, "SN-MISSING")
        assert exc.value.asset_tag == "SN-MISSING"

    @pytest.mark.fast
    def test_transport_error_wraps_to_netbox_query_error(self) -> None:
        """A pynetbox transport exception is wrapped into `NetboxQueryError`.

        Why:
            Callers `except NetboxError` to catch retryable failures.
            Letting the raw `requests.ConnectionError` leak would force
            them to import requests too.
        """
        client = MagicMock()
        original = RuntimeError("connection refused")
        client.dcim.devices.get.side_effect = original

        with pytest.raises(NetboxQueryError) as exc:
            load_host_intent(client, "SN-CPU-001")
        assert exc.value.operation == "get_device"
        assert exc.value.cause is original

    @pytest.mark.fast
    def test_unknown_role_slug_raises(self) -> None:
        """A device with an unknown role slug raises `NetboxQueryError` listing accepted slugs."""
        client = _build_cpu_mock_client()
        client.dcim.devices.get.return_value.role.slug = "embedded"

        with pytest.raises(NetboxQueryError) as exc:
            load_host_intent(client, "SN-CPU-001")
        assert exc.value.operation == "parse_role"
        # The error message should help an operator fix the Netbox data.
        assert "embedded" in str(exc.value)

    @pytest.mark.fast
    def test_missing_nsa_raises(self) -> None:
        """Missing the `nsa` interface raises `NetboxQueryError`."""
        client = _build_cpu_mock_client()
        # Drop the nsa interface
        client.dcim.interfaces.filter.return_value = [
            i for i in client.dcim.interfaces.filter.return_value if i.name != "nsa"
        ]

        with pytest.raises(NetboxQueryError) as exc:
            load_host_intent(client, "SN-CPU-001")
        assert exc.value.operation == "find_ns_nic"

    @pytest.mark.fast
    def test_interface_without_mac_raises(self) -> None:
        """An interface with no primary_mac_address raises `NetboxQueryError`."""
        client = _build_cpu_mock_client()
        # Strip MAC from nsa
        for i in client.dcim.interfaces.filter.return_value:
            if i.name == "nsa":
                i.primary_mac_address = None

        with pytest.raises(NetboxQueryError) as exc:
            load_host_intent(client, "SN-CPU-001")
        assert exc.value.operation == "read_mac"

    @pytest.mark.fast
    def test_vlan_without_assignment_raises(self) -> None:
        """A bond0.NNN interface without an untagged_vlan raises."""
        client = _build_cpu_mock_client()
        for i in client.dcim.interfaces.filter.return_value:
            if i.name == "bond0.100":
                i.untagged_vlan = None

        with pytest.raises(NetboxQueryError) as exc:
            load_host_intent(client, "SN-CPU-001")
        assert exc.value.operation in {"read_vlan_id", "read_vlan_role"}

    @pytest.mark.fast
    def test_unknown_vlan_role_raises(self) -> None:
        """A VLAN with a name outside {mgmt, storage, ingress} raises."""
        client = _build_cpu_mock_client()
        for i in client.dcim.interfaces.filter.return_value:
            if i.name == "bond0.100":
                i.untagged_vlan.name = "telephony"

        with pytest.raises(NetboxQueryError) as exc:
            load_host_intent(client, "SN-CPU-001")
        assert exc.value.operation == "read_vlan_role"
        assert "telephony" in str(exc.value)

    @pytest.mark.fast
    def test_interface_with_zero_ips_raises(self) -> None:
        """A VLAN child with no assigned IP raises (we require exactly one)."""
        client = _build_cpu_mock_client()
        # Make all IP lookups return empty
        client.ipam.ip_addresses.filter.side_effect = lambda **_: []

        with pytest.raises(NetboxQueryError) as exc:
            load_host_intent(client, "SN-CPU-001")
        assert exc.value.operation == "list_ip_addresses"

    @pytest.mark.fast
    def test_interface_with_multiple_ips_raises(self) -> None:
        """A VLAN child with more than one IP raises (multi-IP unsupported in v1)."""
        client = _build_cpu_mock_client()

        def _multi(**_: object) -> list[MagicMock]:
            r1 = MagicMock()
            r1.address = "10.42.10.11/24"
            r2 = MagicMock()
            r2.address = "10.42.10.12/24"
            return [r1, r2]

        client.ipam.ip_addresses.filter.side_effect = _multi

        with pytest.raises(NetboxQueryError) as exc:
            load_host_intent(client, "SN-CPU-001")
        assert exc.value.operation == "list_ip_addresses"

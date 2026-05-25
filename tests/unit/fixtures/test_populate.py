"""Unit tests for `fixtures.netbox.populate`.

Mocked-Netbox tests: validate load_fixture parsing, populate idempotency
under controlled inputs, and conflict detection. Component tests against
a real Netbox container land in M1-4.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fixtures.netbox.errors import FixtureConflictError, FixtureLoadError
from fixtures.netbox.populate import (
    DeviceTypeSpec,
    HostFixture,
    InterfaceSpec,
    PopulateReport,
    VlanSpec,
    _build_parser,
    _slugify,
    load_fixture,
    populate,
)

# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------


class TestLoadFixture:
    @pytest.mark.fast
    def test_real_cpu_host_fixture_loads(self) -> None:
        """The committed cpu-host.yaml loads into a valid HostFixture."""
        path = Path(__file__).parent.parent.parent.parent / "fixtures/netbox/data/cpu-host.yaml"
        fx = load_fixture(path)
        assert fx.asset_tag == "SN-CPU-001"
        assert fx.device_role == "cpu"
        assert len(fx.interfaces) == 6  # nsa, nsb, bond0, bond0.100/.200/.300
        assert len(fx.vlans) == 3
        # nsa is the first physical NIC
        nsa = next(i for i in fx.interfaces if i.name == "nsa")
        assert nsa.mac == "aa:bb:cc:00:01:01"
        assert nsa.mtu == 9000

    @pytest.mark.fast
    def test_real_b300_host_fixture_loads(self) -> None:
        """The committed b300-host.yaml loads with all 10 NICs."""
        path = Path(__file__).parent.parent.parent.parent / "fixtures/netbox/data/b300-host.yaml"
        fx = load_fixture(path)
        assert fx.asset_tag == "SN-GPU-001"
        assert fx.device_role == "gpu-b300"
        assert fx.device_custom_fields == {"bf3_mode": "nic"}
        physical = [i for i in fx.interfaces if i.mac]
        assert len(physical) == 10  # 2 N-S + 8 E-W
        # Every east-west NIC has the expected custom fields
        ew = [i for i in fx.interfaces if i.name.startswith("gpu")]
        assert len(ew) == 8
        for i in ew:
            assert i.custom_fields["roce_tc"] == 3
            assert i.custom_fields["sriov_vfs"] == 16

    @pytest.mark.fast
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        """Loading a non-existent file raises FixtureLoadError."""
        path = tmp_path / "nonexistent.yaml"
        with pytest.raises(FixtureLoadError) as exc:
            load_fixture(path)
        assert "nonexistent.yaml" in exc.value.path

    @pytest.mark.fast
    def test_malformed_yaml_raises(self, tmp_path: Path) -> None:
        """Invalid YAML syntax raises FixtureLoadError."""
        path = tmp_path / "bad.yaml"
        path.write_text("key: : :: [")
        with pytest.raises(FixtureLoadError) as exc:
            load_fixture(path)
        assert "YAML parse error" in exc.value.detail

    @pytest.mark.fast
    def test_non_mapping_top_level_raises(self, tmp_path: Path) -> None:
        """Top-level list (not mapping) raises FixtureLoadError."""
        path = tmp_path / "list.yaml"
        path.write_text("- item1\n- item2\n")
        with pytest.raises(FixtureLoadError) as exc:
            load_fixture(path)
        assert "mapping" in exc.value.detail

    @pytest.mark.fast
    def test_missing_required_field_raises(self, tmp_path: Path) -> None:
        """Missing top-level required key raises FixtureLoadError naming it."""
        path = tmp_path / "incomplete.yaml"
        path.write_text(
            "hostname: foo\n"
            "device_role: cpu\n"
            "site: s\n"
            "device_type:\n"
            "  manufacturer: M\n"
            "  model: M1\n"
            "  slug: m1\n"
            "  u_height: 1\n"
            "interfaces: []\n"
        )
        with pytest.raises(FixtureLoadError) as exc:
            load_fixture(path)
        assert "asset_tag" in exc.value.detail

    @pytest.mark.fast
    def test_invalid_interface_shape_raises(self, tmp_path: Path) -> None:
        """An interface with unknown keys raises FixtureLoadError."""
        path = tmp_path / "bad-iface.yaml"
        path.write_text(
            "asset_tag: SN-X\n"
            "hostname: x\n"
            "device_role: cpu\n"
            "site: s\n"
            "device_type:\n"
            "  manufacturer: M\n"
            "  model: M1\n"
            "  slug: m1\n"
            "  u_height: 1\n"
            "interfaces:\n"
            "  - name: nsa\n"
            "    type: 200gbase-x-qsfp56\n"
            "    nonsense_field: yes\n"
        )
        with pytest.raises(FixtureLoadError):
            load_fixture(path)


# ---------------------------------------------------------------------------
# PopulateReport
# ---------------------------------------------------------------------------


class TestPopulateReport:
    @pytest.mark.fast
    def test_empty_is_no_op(self) -> None:
        assert PopulateReport().is_no_op is True

    @pytest.mark.fast
    def test_any_creation_breaks_no_op(self) -> None:
        r = PopulateReport()
        r.add_created("site")
        assert r.is_no_op is False

    @pytest.mark.fast
    def test_summary_shape(self) -> None:
        r = PopulateReport()
        r.add_created("device")
        r.add_created("device")
        r.add_created("interface")
        r.add_skipped("site")
        s = r.summary()
        assert "device=2" in s
        assert "interface=1" in s
        assert "skipped: 1" in s


# ---------------------------------------------------------------------------
# populate() — mocked Netbox client
# ---------------------------------------------------------------------------


def _minimal_cpu_fixture() -> HostFixture:
    """Build a minimal valid CPU host fixture for population tests.

    Mirrors `fixtures/netbox/data/cpu-host.yaml` in shape but with
    minimal contents to keep tests focused.
    """
    return HostFixture(
        asset_tag="SN-TEST-001",
        hostname="test-cpu-01",
        device_role="cpu",
        site="testsite",
        device_type=DeviceTypeSpec(
            manufacturer="TestMfr", model="TestModel", slug="testmodel", u_height=1
        ),
        vlans=[VlanSpec(vid=100, name="mgmt")],
        interfaces=[
            InterfaceSpec(name="nsa", type="200gbase-x-qsfp56", mac="aa:bb:cc:99:00:01", mtu=9000),
            InterfaceSpec(name="nsb", type="200gbase-x-qsfp56", mac="aa:bb:cc:99:00:02", mtu=9000),
            InterfaceSpec(name="bond0", type="lag", mtu=9000, lag_members=["nsa", "nsb"]),
            InterfaceSpec(
                name="bond0.100",
                type="virtual",
                mtu=1500,
                parent="bond0",
                untagged_vlan=100,
                ip="10.99.10.1/24",
            ),
        ],
    )


def _build_empty_netbox_client() -> MagicMock:
    """Build a pynetbox-like mock whose `get()` calls all return None
    (i.e., empty Netbox), and whose `create()` calls return a stub
    object with the next `id` value."""
    client = MagicMock()
    counter = {"id": 0}

    def _next_obj(payload: dict[str, object] | None = None) -> MagicMock:
        counter["id"] += 1
        obj = MagicMock()
        obj.id = counter["id"]
        if payload:
            for k, v in payload.items():
                setattr(obj, k, v)
        # Default attrs the populator checks.
        obj.lag = None
        obj.parent = None
        return obj

    # Every .get() returns None (nothing exists yet). MagicMock auto-creates
    # nested attributes, so we just touch each endpoint and set its return.
    client.dcim.sites.get.return_value = None
    client.dcim.manufacturers.get.return_value = None
    client.dcim.device_types.get.return_value = None
    client.dcim.device_roles.get.return_value = None
    client.dcim.devices.get.return_value = None
    client.dcim.interfaces.get.return_value = None
    client.dcim.mac_addresses.get.return_value = None
    client.ipam.vlans.get.return_value = None
    client.ipam.ip_addresses.get.return_value = None

    # .create() returns a fresh stub object with the next id.
    def _create_factory(payload: dict[str, object]) -> MagicMock:
        return _next_obj(payload)

    client.dcim.sites.create.side_effect = _create_factory
    client.dcim.manufacturers.create.side_effect = _create_factory
    client.dcim.device_types.create.side_effect = _create_factory
    client.dcim.device_roles.create.side_effect = _create_factory
    client.dcim.devices.create.side_effect = _create_factory
    client.dcim.interfaces.create.side_effect = _create_factory
    client.dcim.mac_addresses.create.side_effect = _create_factory
    client.ipam.vlans.create.side_effect = _create_factory
    client.ipam.ip_addresses.create.side_effect = _create_factory

    return client


class TestPopulate:
    @pytest.mark.fast
    def test_empty_netbox_creates_everything(self) -> None:
        """Against an empty Netbox, every required object is created."""
        fx = _minimal_cpu_fixture()
        client = _build_empty_netbox_client()

        report = populate(client, [fx])

        # We expect at least: 1 site, 1 manufacturer, 1 device type,
        # 1 device role, 1 VLAN, 1 device, 4 interfaces, 1 IP.
        assert report.created.get("site") == 1
        assert report.created.get("manufacturer") == 1
        assert report.created.get("device_type") == 1
        assert report.created.get("device_role") == 1
        assert report.created.get("vlan") == 1
        assert report.created.get("device") == 1
        assert report.created.get("interface") == 4
        assert report.created.get("ip_address") == 1
        assert report.is_no_op is False

    @pytest.mark.fast
    def test_conflicting_device_raises(self) -> None:
        """If an existing device has the same asset_tag but a different
        hostname, FixtureConflictError is raised."""
        fx = _minimal_cpu_fixture()
        client = _build_empty_netbox_client()
        # Make `devices.get(asset_tag=...)` return an object whose name
        # doesn't match the fixture.
        ghost = MagicMock()
        ghost.name = "ghost-hostname"
        ghost.id = 9999
        client.dcim.devices.get.return_value = ghost

        with pytest.raises(FixtureConflictError) as exc:
            populate(client, [fx])
        assert exc.value.kind == "device"
        assert exc.value.identifier == "SN-TEST-001"

    @pytest.mark.fast
    def test_conflicting_vlan_raises(self) -> None:
        """A pre-existing VLAN with the same VID but a different name
        raises FixtureConflictError."""
        fx = _minimal_cpu_fixture()
        client = _build_empty_netbox_client()
        ghost_vlan = MagicMock()
        ghost_vlan.name = "telephony"  # not 'mgmt'
        ghost_vlan.id = 9999
        client.ipam.vlans.get.return_value = ghost_vlan

        with pytest.raises(FixtureConflictError) as exc:
            populate(client, [fx])
        assert exc.value.kind == "vlan"


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------


class TestCLI:
    @pytest.mark.fast
    def test_parser_defaults(self) -> None:
        """Defaults: localhost URL, data-dir next to the populate module, INFO logging."""
        parser = _build_parser()
        args = parser.parse_args([])
        assert args.url == "http://127.0.0.1:8000"
        assert args.log_level == "INFO"
        assert args.data_dir.name == "data"

    @pytest.mark.fast
    def test_parser_accepts_token_and_url(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["--url", "http://nb.example.com", "--token", "abc"])
        assert args.url == "http://nb.example.com"
        assert args.token == "abc"  # noqa: S105  -- test fixture, not a real credential


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestSlugify:
    @pytest.mark.fast
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("NVIDIA", "nvidia"),
            ("Dell", "dell"),
            ("Super Micro", "super-micro"),
            ("  trimmed  ", "trimmed"),
            ("with_underscores", "with-underscores"),
        ],
    )
    def test_slugify_lowercases_and_normalizes(self, name: str, expected: str) -> None:
        assert _slugify(name) == expected

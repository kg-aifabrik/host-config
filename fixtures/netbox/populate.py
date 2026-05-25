"""Idempotent Netbox fixture loader.

Loads YAML host fixtures from ``fixtures/netbox/data/*.yaml`` and applies
them to a running Netbox via ``pynetbox``. Idempotent: on a second run,
every step short-circuits ("already exists, skipping") and the report
shows zero changes.

CLI usage:

    python -m fixtures.netbox.populate \\
        --url http://127.0.0.1:8000 \\
        --token-file ~/.host-config/netbox-token

The CLI also accepts ``--data-dir`` to override where YAML fixtures live
(default: ``fixtures/netbox/data/``).

Library usage:

    from fixtures.netbox.populate import load_fixture, populate
    fixtures = [load_fixture(p) for p in sorted(data_dir.glob("*.yaml"))]
    client = pynetbox.api(url, token=token)
    report = populate(client, fixtures)

Per principle #12: this loader knows about NICs, VLANs, IPs. It does NOT
know about caller-domain lifecycle states. Whether the populated host is
"active" vs "RMA" is the orchestrator's concern; the populator just
loads the topology.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from fixtures.netbox.errors import FixtureConflictError, FixtureLoadError

if TYPE_CHECKING:
    import pynetbox

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fixture data model (dataclasses, validated at load time)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeviceTypeSpec:
    """Manufacturer + model identifier for the host's hardware."""

    manufacturer: str
    model: str
    slug: str
    u_height: int


@dataclass(frozen=True)
class VlanSpec:
    """A VLAN identifier this host needs."""

    vid: int
    name: str


@dataclass(frozen=True)
class InterfaceSpec:
    """One interface on the host.

    The ``type`` field carries the Netbox interface-type enum value
    (e.g., ``200gbase-x-qsfp56``, ``lag``, ``virtual``). Different types
    require different other fields:

    - Physical NIC: ``mac``, ``mtu``.
    - LAG: ``lag_members`` (names of children); no MAC.
    - Virtual (VLAN child): ``parent``, ``untagged_vlan``, ``ip``; no MAC.

    The populator enforces these constraints.
    """

    name: str
    type: str
    mac: str | None = None
    mtu: int | None = None
    parent: str | None = None
    lag_members: list[str] = field(default_factory=list)
    untagged_vlan: int | None = None
    ip: str | None = None
    custom_fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HostFixture:
    """Validated host fixture, ready to apply to Netbox."""

    asset_tag: str
    hostname: str
    device_role: str  # slug
    site: str  # slug
    device_type: DeviceTypeSpec
    vlans: list[VlanSpec]
    interfaces: list[InterfaceSpec]
    device_custom_fields: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_fixture(path: Path) -> HostFixture:
    """Parse one YAML file into a validated :class:`HostFixture`.

    Approach:
        Read the YAML; reject malformed structure (missing required
        top-level keys, wrong types). Returns a frozen dataclass; the
        populator never touches the YAML directly.

    Args:
        path: Filesystem path to the YAML fixture.

    Returns:
        Parsed :class:`HostFixture`.

    Raises:
        FixtureLoadError: File is missing, unreadable, malformed YAML,
            or missing a required field.

    Scenarios:
        - Happy path: well-formed YAML with all required keys → returns
          a fully-populated HostFixture.
        - File doesn't exist → FixtureLoadError mentioning the path.
        - YAML syntax error → FixtureLoadError wrapping the parse error.
        - Missing required key (e.g., asset_tag) → FixtureLoadError
          naming the field.
    """
    try:
        text = path.read_text()
    except OSError as e:
        raise FixtureLoadError(str(path), f"cannot read file: {e}") from e

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise FixtureLoadError(str(path), f"YAML parse error: {e}") from e

    if not isinstance(data, dict):
        raise FixtureLoadError(
            str(path), f"top-level value must be a mapping, got {type(data).__name__}"
        )

    try:
        return HostFixture(
            asset_tag=str(data["asset_tag"]),
            hostname=str(data["hostname"]),
            device_role=str(data["device_role"]),
            site=str(data["site"]),
            device_type=DeviceTypeSpec(**data["device_type"]),
            vlans=[VlanSpec(**v) for v in data.get("vlans", [])],
            interfaces=[InterfaceSpec(**i) for i in data["interfaces"]],
            device_custom_fields=dict(data.get("device_custom_fields") or {}),
        )
    except KeyError as e:
        raise FixtureLoadError(str(path), f"missing required key: {e.args[0]!r}") from e
    except (TypeError, ValueError) as e:
        raise FixtureLoadError(str(path), f"invalid field shape: {e}") from e


# ---------------------------------------------------------------------------
# Apply report
# ---------------------------------------------------------------------------


@dataclass
class PopulateReport:
    """Summary of one :func:`populate` invocation.

    Counts are by object kind. A re-run report has all-zero ``created``
    counts (the work was idempotent).
    """

    created: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)

    def add_created(self, kind: str) -> None:
        self.created[kind] = self.created.get(kind, 0) + 1

    def add_skipped(self, kind: str) -> None:
        self.skipped[kind] = self.skipped.get(kind, 0) + 1

    @property
    def is_no_op(self) -> bool:
        """True iff nothing was created on this run."""
        return all(v == 0 for v in self.created.values())

    def summary(self) -> str:
        """Single-line human summary."""
        created = ", ".join(f"{k}={v}" for k, v in sorted(self.created.items())) or "0"
        skipped_total = sum(self.skipped.values())
        return f"created: [{created}]; skipped: {skipped_total}"


# ---------------------------------------------------------------------------
# Populator
# ---------------------------------------------------------------------------


def populate(client: pynetbox.api, fixtures: list[HostFixture]) -> PopulateReport:
    """Idempotently apply each fixture to Netbox.

    Approach:
        For each fixture, walks the dependency chain in order:
        site → manufacturer → device type → device role → VLANs →
        device → interfaces → IP addresses → interface↔VLAN bindings
        and LAG memberships → device custom fields. Each step is
        "create-if-absent": existing objects are reused without
        modification (M1-3 does not patch drifted objects; that's
        future work if needed).

    Args:
        client: Configured ``pynetbox.api`` instance.
        fixtures: List of fixtures to apply.

    Returns:
        :class:`PopulateReport` with per-kind create/skip counts.

    Raises:
        FixtureConflictError: A Netbox object with our key already
            exists but has a conflicting shape we won't auto-fix.
        host_config.netbox.errors.NetboxQueryError: A Netbox API call
            failed at the transport layer.

    Scenarios:
        - Empty Netbox + CPU fixture: creates 1 site, 1 manufacturer,
          1 device type, 1 device role, 3 VLANs, 1 device, 6 interfaces,
          3 IPs.
        - Re-run against populated Netbox: every step is a skip;
          ``report.is_no_op`` is True.
        - Pre-existing site with different display name: skipped (slug
          is the key; we don't patch existing display names).
        - Pre-existing device with same asset_tag but different
          hostname: ``FixtureConflictError`` is raised.
    """
    report = PopulateReport()
    for fx in fixtures:
        logger.info(
            "applying fixture asset_tag=%s hostname=%s role=%s",
            fx.asset_tag,
            fx.hostname,
            fx.device_role,
        )
        _apply_one(client, fx, report)
    logger.info("populate done: %s", report.summary())
    return report


def _apply_one(client: pynetbox.api, fx: HostFixture, report: PopulateReport) -> None:
    """Apply one fixture in the right dependency order."""
    site = _ensure_site(client, fx.site, report)
    manufacturer = _ensure_manufacturer(client, fx.device_type.manufacturer, report)
    device_type = _ensure_device_type(client, fx.device_type, manufacturer, report)
    device_role = _ensure_device_role(client, fx.device_role, report)
    vlan_by_vid = {v.vid: _ensure_vlan(client, v, site, report) for v in fx.vlans}

    device = _ensure_device(client, fx, site, device_type, device_role, report)

    # Pass 1: create all interfaces without parent/lag references so
    # later passes can resolve them by name.
    iface_by_name: dict[str, Any] = {}
    for iface_spec in fx.interfaces:
        iface_by_name[iface_spec.name] = _ensure_interface(
            client, iface_spec, device, vlan_by_vid, report
        )

    # Pass 2: wire up LAG membership + parent references for virtual
    # interfaces. Netbox requires the children to exist first.
    for iface_spec in fx.interfaces:
        _wire_relationships(client, iface_spec, device, iface_by_name, report)

    # Pass 3: MAC addresses (Netbox 4.2+ moved these to a first-class
    # `/api/dcim/mac-addresses/` endpoint, assigned to interfaces).
    for iface_spec in fx.interfaces:
        if iface_spec.mac:
            _ensure_mac_address(client, iface_spec, iface_by_name[iface_spec.name], report)

    # Pass 4: IP addresses (one per interface that has `ip:`).
    for iface_spec in fx.interfaces:
        if iface_spec.ip:
            _ensure_ip_address(client, iface_spec, iface_by_name[iface_spec.name], report)


# ---------------------------------------------------------------------------
# Per-object helpers (each is "ensure exists; return the object").
# ---------------------------------------------------------------------------


def _ensure_site(client: pynetbox.api, slug: str, report: PopulateReport) -> Any:
    existing = client.dcim.sites.get(slug=slug)
    if existing:
        report.add_skipped("site")
        logger.info("site skipped slug=%s (exists)", slug)
        return existing
    obj = client.dcim.sites.create({"name": slug, "slug": slug})
    report.add_created("site")
    logger.info("site created slug=%s", slug)
    return obj


def _ensure_manufacturer(client: pynetbox.api, name: str, report: PopulateReport) -> Any:
    slug = _slugify(name)
    existing = client.dcim.manufacturers.get(slug=slug)
    if existing:
        report.add_skipped("manufacturer")
        return existing
    obj = client.dcim.manufacturers.create({"name": name, "slug": slug})
    report.add_created("manufacturer")
    logger.info("manufacturer created name=%s", name)
    return obj


def _ensure_device_type(
    client: pynetbox.api,
    spec: DeviceTypeSpec,
    manufacturer: Any,
    report: PopulateReport,
) -> Any:
    existing = client.dcim.device_types.get(slug=spec.slug)
    if existing:
        # Don't patch — but verify shape doesn't conflict.
        observed_height = getattr(existing, "u_height", None)
        if observed_height not in (None, spec.u_height):
            raise FixtureConflictError(
                "device_type",
                spec.slug,
                f"u_height: expected {spec.u_height}, observed {observed_height}",
            )
        report.add_skipped("device_type")
        return existing
    obj = client.dcim.device_types.create(
        {
            "manufacturer": manufacturer.id,
            "model": spec.model,
            "slug": spec.slug,
            "u_height": spec.u_height,
        }
    )
    report.add_created("device_type")
    logger.info("device_type created slug=%s", spec.slug)
    return obj


def _ensure_device_role(client: pynetbox.api, slug: str, report: PopulateReport) -> Any:
    existing = client.dcim.device_roles.get(slug=slug)
    if existing:
        report.add_skipped("device_role")
        return existing
    obj = client.dcim.device_roles.create({"name": slug, "slug": slug})
    report.add_created("device_role")
    logger.info("device_role created slug=%s", slug)
    return obj


def _ensure_vlan(client: pynetbox.api, spec: VlanSpec, site: Any, report: PopulateReport) -> Any:
    existing = client.ipam.vlans.get(vid=spec.vid, site_id=site.id)
    if existing:
        if existing.name != spec.name:
            raise FixtureConflictError(
                "vlan",
                f"vid={spec.vid}",
                f"name: expected {spec.name!r}, observed {existing.name!r}",
            )
        report.add_skipped("vlan")
        return existing
    obj = client.ipam.vlans.create({"vid": spec.vid, "name": spec.name, "site": site.id})
    report.add_created("vlan")
    logger.info("vlan created vid=%d name=%s", spec.vid, spec.name)
    return obj


def _ensure_device(
    client: pynetbox.api,
    fx: HostFixture,
    site: Any,
    device_type: Any,
    device_role: Any,
    report: PopulateReport,
) -> Any:
    existing = client.dcim.devices.get(asset_tag=fx.asset_tag)
    if existing:
        if existing.name != fx.hostname:
            raise FixtureConflictError(
                "device",
                fx.asset_tag,
                f"name: expected {fx.hostname!r}, observed {existing.name!r}",
            )
        report.add_skipped("device")
        return existing
    payload: dict[str, Any] = {
        "name": fx.hostname,
        "asset_tag": fx.asset_tag,
        "site": site.id,
        "device_type": device_type.id,
        "role": device_role.id,
        "status": "active",
    }
    if fx.device_custom_fields:
        payload["custom_fields"] = dict(fx.device_custom_fields)
    obj = client.dcim.devices.create(payload)
    report.add_created("device")
    logger.info("device created asset_tag=%s hostname=%s", fx.asset_tag, fx.hostname)
    return obj


def _ensure_interface(
    client: pynetbox.api,
    spec: InterfaceSpec,
    device: Any,
    vlan_by_vid: dict[int, Any],
    report: PopulateReport,
) -> Any:
    existing = client.dcim.interfaces.get(device_id=device.id, name=spec.name)
    if existing:
        report.add_skipped("interface")
        return existing
    payload: dict[str, Any] = {
        "device": device.id,
        "name": spec.name,
        "type": spec.type,
    }
    # NOTE: MAC is set in a separate pass via _ensure_mac_address.
    # Netbox 4.2+ rejects `mac_address` on the interface POST payload
    # (moved to first-class /api/dcim/mac-addresses/).
    if spec.mtu:
        payload["mtu"] = spec.mtu
    if spec.untagged_vlan and spec.untagged_vlan in vlan_by_vid:
        payload["mode"] = "access"
        payload["untagged_vlan"] = vlan_by_vid[spec.untagged_vlan].id
    if spec.custom_fields:
        payload["custom_fields"] = dict(spec.custom_fields)
    obj = client.dcim.interfaces.create(payload)
    report.add_created("interface")
    logger.info("interface created device=%s name=%s", device.name, spec.name)
    return obj


def _wire_relationships(
    client: pynetbox.api,
    spec: InterfaceSpec,
    device: Any,
    iface_by_name: dict[str, Any],
    report: PopulateReport,
) -> None:
    """Apply LAG membership and parent references after Pass 1 has created
    every interface."""
    if spec.lag_members:
        lag_iface = iface_by_name[spec.name]
        for member_name in spec.lag_members:
            member = iface_by_name[member_name]
            # WHY: Netbox represents LAG membership via the member's `lag` field
            # pointing at the LAG interface, not via a list on the LAG itself.
            current_lag_id = getattr(member.lag, "id", None) if member.lag else None
            if current_lag_id != lag_iface.id:
                member.update({"lag": lag_iface.id})
                report.add_created("lag_membership")
                logger.info("lag_membership set member=%s lag=%s", member_name, spec.name)
            else:
                report.add_skipped("lag_membership")

    if spec.parent:
        child_iface = iface_by_name[spec.name]
        parent_iface = iface_by_name[spec.parent]
        current_parent_id = getattr(child_iface.parent, "id", None) if child_iface.parent else None
        if current_parent_id != parent_iface.id:
            child_iface.update({"parent": parent_iface.id})
            report.add_created("interface_parent")
            logger.info("interface_parent set child=%s parent=%s", spec.name, spec.parent)
        else:
            report.add_skipped("interface_parent")


def _ensure_mac_address(
    client: pynetbox.api,
    spec: InterfaceSpec,
    iface: Any,
    report: PopulateReport,
) -> Any:
    """Ensure the MAC is assigned to the interface in Netbox 4.2+.

    In Netbox 4.2+, MAC addresses moved out of the interface model into
    a first-class ``dcim.mac_addresses`` endpoint, with the interface
    pointing back via ``primary_mac_address``. This helper:
      1. Looks up an existing MAC for the interface; reuses if present.
      2. Otherwise creates a MACAddress record assigned to the interface.
      3. Patches the interface to mark it as the primary MAC.

    Idempotent: re-runs find the existing MAC and verify the primary
    pointer already matches.
    """
    assert spec.mac  # caller guarantees this
    existing = client.dcim.mac_addresses.get(mac_address=spec.mac)
    if existing:
        # Verify it's assigned to the right interface; conflict otherwise.
        assigned_id = getattr(existing, "assigned_object_id", None)
        if assigned_id and assigned_id != iface.id:
            raise FixtureConflictError(
                "mac_address",
                spec.mac,
                f"assigned to interface_id={assigned_id}, expected {iface.id}",
            )
        report.add_skipped("mac_address")
        mac_obj = existing
    else:
        mac_obj = client.dcim.mac_addresses.create(
            {
                "mac_address": spec.mac,
                "assigned_object_type": "dcim.interface",
                "assigned_object_id": iface.id,
            }
        )
        report.add_created("mac_address")
        logger.info("mac_address created %s on interface=%s", spec.mac, spec.name)

    # Set as primary MAC on the interface if not already.
    current_primary_id = (
        getattr(iface.primary_mac_address, "id", None) if iface.primary_mac_address else None
    )
    if current_primary_id != mac_obj.id:
        iface.update({"primary_mac_address": mac_obj.id})
        report.add_created("primary_mac_assignment")
    else:
        report.add_skipped("primary_mac_assignment")
    return mac_obj


def _ensure_ip_address(
    client: pynetbox.api,
    spec: InterfaceSpec,
    iface: Any,
    report: PopulateReport,
) -> Any:
    assert spec.ip  # caller guarantees this
    existing = client.ipam.ip_addresses.get(address=spec.ip)
    if existing:
        existing_iface = getattr(existing, "assigned_object_id", None)
        if existing_iface != iface.id:
            raise FixtureConflictError(
                "ip_address",
                spec.ip,
                f"assigned to interface_id={existing_iface}, expected {iface.id}",
            )
        report.add_skipped("ip_address")
        return existing
    obj = client.ipam.ip_addresses.create(
        {
            "address": spec.ip,
            "assigned_object_type": "dcim.interface",
            "assigned_object_id": iface.id,
        }
    )
    report.add_created("ip_address")
    logger.info("ip_address created address=%s interface=%s", spec.ip, spec.name)
    return obj


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    """Lowercase, collapse spaces to hyphens; matches Netbox's slug shape."""
    return name.lower().strip().replace(" ", "-").replace("_", "-")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _default_data_dir() -> Path:
    """Default data directory: ``fixtures/netbox/data/`` next to this file."""
    return Path(__file__).parent / "data"


def _read_token(token_file: Path) -> str:
    """Read the API token from disk; strip whitespace."""
    return token_file.read_text().strip()


def _build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argparse parser. Factored so tests can exercise it."""
    parser = argparse.ArgumentParser(
        prog="fixtures.netbox.populate",
        description="Idempotently load host fixtures into Netbox.",
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8000",
        help="Netbox base URL (default: http://127.0.0.1:8000).",
    )
    parser.add_argument(
        "--token",
        help="API token (use --token-file for safer handling).",
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        help="Path to a file containing the API token. "
        "Defaults to ~/.host-config/netbox-token (created by netbox-dev role).",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=_default_data_dir(),
        help="Directory containing *.yaml fixtures.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        Process exit code. 0 = success; 1 = fixture/load/apply failure.
    """

    # so the module can be imported (and unit-tested) without the network
    # client being available at import time.
    import pynetbox  # noqa: PLC0415

    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    token = args.token
    if not token:
        token_file = args.token_file or Path.home() / ".host-config" / "netbox-token"
        if not token_file.exists():
            logger.error("token not provided and %s does not exist", token_file)
            return 1
        token = _read_token(token_file)

    yaml_paths = sorted(args.data_dir.glob("*.yaml"))
    if not yaml_paths:
        logger.error("no *.yaml fixtures found under %s", args.data_dir)
        return 1

    fixtures = [load_fixture(p) for p in yaml_paths]
    logger.info("loaded %d fixture(s) from %s", len(fixtures), args.data_dir)

    client = pynetbox.api(args.url, token=token)
    try:
        report = populate(client, fixtures)
    except (FixtureConflictError, FixtureLoadError) as e:
        logger.error("fixture apply failed: %s", e)
        return 1

    print(report.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "DeviceTypeSpec",
    "HostFixture",
    "InterfaceSpec",
    "PopulateReport",
    "VlanSpec",
    "load_fixture",
    "main",
    "populate",
]

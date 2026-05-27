"""Netbox → `HostIntent` loader.

The bridge between Netbox state and the renderer's typed domain model.
Given an asset tag, queries Netbox for the device + its interfaces +
IPs + VLAN assignments + custom fields, then maps them to a fully
validated `HostIntent`. If Netbox returns shape we don't understand,
raises typed errors with context rather than letting a downstream
``AttributeError`` surface in confusing places.

Per principle #12: this module reads from Netbox and produces a
`HostIntent`. It does NOT know about lifecycle states, environments,
or any caller-domain concept. The renderer uses what comes out; what
the caller does with it is the caller's concern.

Conventions baked in (subject to ADR-bumping as we learn):

- **Role** comes from ``device.role.slug``; must match :class:`Role`.
- **Bond members** are interfaces named ``nsa`` and ``nsb``.
- **Bond** is the interface named ``bond0``.
- **VLAN children** are interfaces named ``bond0.<vid>``; their
  ``role`` is derived from the ``untagged_vlan.name`` (must be one
  of ``mgmt`` / ``storage`` / ``ingress``).
- **Gateway** is computed as the first usable IP in the VLAN child's
  IP prefix (e.g., for ``10.42.10.23/24``, gateway = ``10.42.10.1``).
  Only the mgmt VLAN gets a gateway populated.
- **RoCE underlays** (for gpu-b300 role) are interfaces named ``gpu0`` to
  ``gpu7``; their custom fields (``roce_tc``, ``numa_node``, etc.) are
  read but not all are used by `HostIntent` directly — they are
  carried in template-context dicts the renderer consumes.
- **DNS / search domain** are not yet modeled in Netbox; left None
  for v1. A future schema extension (ADR-pending) adds them.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from ipaddress import IPv4Address, IPv4Interface
from typing import TYPE_CHECKING, Any

from host_config.models.errors import InvariantError
from host_config.models.intent import HostIntent, Role
from host_config.models.interface import (
    Bond,
    BondMember,
    InfinibandUnderlay,
    RoceUnderlay,
)
from host_config.models.vlan import VlanChild, VlanRole
from host_config.netbox.errors import HostNotFoundError, NetboxQueryError

if TYPE_CHECKING:
    import pynetbox

logger = logging.getLogger(__name__)

# Interface-name conventions. Centralized so a future change (e.g.,
# renaming the bond) updates one location.
BOND_NAME = "bond0"
NS_NIC_NAMES = ("nsa", "nsb")
ROCE_NIC_PREFIX = "gpu"
IB_NIC_PREFIX = "ib"
VLAN_NAME_PREFIX = "bond0."


def load_host_intent(client: pynetbox.api, asset_tag: str) -> HostIntent:
    """Load a host's full intent from Netbox.

    Approach:
        1. Fetch the device by ``asset_tag``. Absent → `HostNotFoundError`.
        2. Fetch every interface on the device in one batched query.
        3. Partition interfaces by role (N-S NICs vs bond vs VLAN
           children vs RoCE underlays) based on the naming conventions
           documented at the module top.
        4. Construct the model tree bottom-up:
           ``BondMember`` → ``Bond`` → ``VlanChild`` (with gateway
           derivation on mgmt) → ``RoceUnderlay`` → ``HostIntent``.
        5. The final `HostIntent(...)` runs every cross-field validator
           in `validators.py`; if Netbox state is internally
           inconsistent (e.g., the mgmt VLAN's gateway IP is missing),
           an `InvariantError` propagates up.

    Args:
        client: Configured ``pynetbox.api`` instance.
        asset_tag: The Netbox device's ``asset_tag``. This is the
            stable identifier; ``name`` may change (renaming) but
            asset tag does not.

    Returns:
        Fully-validated :class:`HostIntent` for the host.

    Raises:
        HostNotFoundError: No device with the given asset tag exists.
        NetboxQueryError: A Netbox API call failed at the transport
            layer; the underlying exception is attached as ``cause``.
        InvariantError: Netbox state is internally inconsistent in a
            way that violates one of the `HostIntent` invariants.
            (Raised by Pydantic during final model construction.)

    Scenarios:
        - Happy path (cpu host): returns a HostIntent with role=cpu,
          2 N-S NICs, 1 bond, 3 VLAN children (one with gateway),
          empty roce_underlays.
        - Happy path (gpu-b300 host): returns a HostIntent with role
          gpu-b300, 2 N-S NICs, 1 bond, 3 VLAN children, 8 RoCE underlays.
        - Unknown asset_tag → HostNotFoundError naming the tag.
        - Netbox unreachable → NetboxQueryError wrapping the transport
          exception.
        - Device with unknown role.slug → NetboxQueryError describing
          which roles are accepted.
        - Device missing nsa or nsb → InvariantError ns-nic-count.
        - Mgmt VLAN missing its IPv4 address → NetboxQueryError
          describing which interface is missing data.
    """
    logger.info("load_host_intent.start asset_tag=%s", asset_tag)
    device = _fetch_device(client, asset_tag)
    interfaces = _fetch_interfaces(client, device, asset_tag)

    role = _parse_role(device, asset_tag)
    hostname = str(device.name)

    ns_nics = _build_ns_nics(interfaces, asset_tag)
    bond = _build_bond(interfaces, ns_nics, asset_tag)
    vlans = _build_vlans(client, interfaces, asset_tag)
    roce_underlays = _build_roce_underlays(client, interfaces, asset_tag)
    ib_underlays = _build_ib_underlays(client, interfaces, asset_tag)

    try:
        intent = HostIntent(
            asset_tag=str(asset_tag),
            hostname=hostname,
            role=role,
            ns_nics=ns_nics,
            bond=bond,
            vlans=vlans,
            roce_underlays=roce_underlays,
            ib_underlays=ib_underlays,
        )
    except InvariantError:
        # Re-raise unchanged; the invariant carries its own context.
        raise

    logger.info(
        "load_host_intent.done asset_tag=%s role=%s ns_nics=%d vlans=%d roce=%d ib=%d",
        asset_tag,
        role.value,
        len(ns_nics),
        len(vlans),
        len(roce_underlays),
        len(ib_underlays),
    )
    return intent


# ---------------------------------------------------------------------------
# Per-step helpers — each wraps a Netbox call with a typed error.
# ---------------------------------------------------------------------------


def _fetch_device(client: pynetbox.api, asset_tag: str) -> Any:
    """Fetch the device or raise."""
    try:
        device = client.dcim.devices.get(asset_tag=asset_tag)
    except Exception as e:  # pynetbox / requests exceptions
        raise NetboxQueryError(operation="get_device", cause=e, asset_tag=asset_tag) from e
    if device is None:
        raise HostNotFoundError(asset_tag)
    logger.debug("device fetched id=%d name=%s", device.id, device.name)
    return device


def _fetch_interfaces(client: pynetbox.api, device: Any, asset_tag: str) -> list[Any]:
    """Fetch all interfaces on the device in one batched query."""
    try:
        ifaces = list(client.dcim.interfaces.filter(device_id=device.id))
    except Exception as e:
        raise NetboxQueryError(operation="list_interfaces", cause=e, asset_tag=asset_tag) from e
    logger.debug("interfaces fetched count=%d", len(ifaces))
    return ifaces


def _parse_role(device: Any, asset_tag: str) -> Role:
    """Map the device's role.slug to our :class:`Role` enum."""
    role_slug = getattr(device.role, "slug", None) if device.role else None
    if not role_slug:
        raise NetboxQueryError(
            operation="parse_role",
            cause=ValueError(f"device has no role.slug: {device.name!r}"),
            asset_tag=asset_tag,
        )
    try:
        return Role(role_slug)
    except ValueError as e:
        raise NetboxQueryError(
            operation="parse_role",
            cause=ValueError(
                f"unknown role slug {role_slug!r}; accepted: {[r.value for r in Role]}"
            ),
            asset_tag=asset_tag,
        ) from e


def _build_ns_nics(interfaces: Iterable[Any], asset_tag: str) -> list[BondMember]:
    """Pick out nsa and nsb interfaces and build BondMember models."""
    by_name = {i.name: i for i in interfaces}
    members: list[BondMember] = []
    for name in NS_NIC_NAMES:
        iface = by_name.get(name)
        if iface is None:
            raise NetboxQueryError(
                operation="find_ns_nic",
                cause=ValueError(f"missing N-S interface {name!r}"),
                asset_tag=asset_tag,
            )
        mac = _read_mac(iface, asset_tag)
        mtu = _read_mtu(iface, asset_tag, default=9000)
        members.append(BondMember(name=name, mac=mac, mtu=mtu))
    return members


def _build_bond(interfaces: Iterable[Any], ns_nics: list[BondMember], asset_tag: str) -> Bond:
    """Build the Bond model from the bond0 interface + N-S NIC names.

    Note: the Bond model carries its own defaults for mode/lacp_rate/
    transmit_hash_policy; Netbox doesn't track these per-bond.
    """
    by_name = {i.name: i for i in interfaces}
    bond_iface = by_name.get(BOND_NAME)
    if bond_iface is None:
        raise NetboxQueryError(
            operation="find_bond",
            cause=ValueError(f"missing bond interface {BOND_NAME!r}"),
            asset_tag=asset_tag,
        )
    mtu = _read_mtu(bond_iface, asset_tag, default=9000)
    return Bond(name=BOND_NAME, members=[n.name for n in ns_nics], mtu=mtu)


def _build_vlans(
    client: pynetbox.api, interfaces: Iterable[Any], asset_tag: str
) -> list[VlanChild]:
    """Build VlanChild models for every bond0.NNN interface.

    Approach:
        For each VLAN child interface, read:
          - Its untagged VLAN (VID + name → role mapping)
          - Its IP address (single IPv4Interface — fixtures put one per
            VLAN child; multi-IP support is future work)
          - For mgmt role: derive gateway as the first usable IP in
            the IP's prefix (convention).
    """
    children: list[VlanChild] = []
    for iface in interfaces:
        if not iface.name.startswith(VLAN_NAME_PREFIX):
            continue

        vid = _read_vlan_id(iface, asset_tag)
        role = _read_vlan_role(iface, asset_tag)
        mtu = _read_mtu(iface, asset_tag, default=1500)
        address = _fetch_single_ip(client, iface, asset_tag)
        gateway = _derive_gateway(address) if role is VlanRole.MGMT else None

        children.append(
            VlanChild(
                name=iface.name,
                parent=BOND_NAME,
                vlan_id=vid,
                role=role,
                mtu=mtu,
                address=address,
                gateway=gateway,
            )
        )
    return children


def _build_roce_underlays(
    client: pynetbox.api, interfaces: Iterable[Any], asset_tag: str
) -> list[RoceUnderlay]:
    """Build RoceUnderlay models for every east-west NIC (gpu0..gpu7).

    Returns an empty list for cpu-role hosts (none of their interfaces
    match the gpu* naming convention). The HostIntent role-count
    invariant enforces "exactly 8" for gpu-b300.
    """
    underlays: list[RoceUnderlay] = []
    for iface in interfaces:
        if not iface.name.startswith(ROCE_NIC_PREFIX):
            continue
        # Skip interfaces whose name matches the prefix but isn't a
        # plain "gpuN" (defensive against accidentally matching e.g.
        # "gpu0-mgmt").
        if not iface.name[len(ROCE_NIC_PREFIX) :].isdigit():
            continue

        mac = _read_mac(iface, asset_tag)
        mtu = _read_mtu(iface, asset_tag, default=9000)
        address = _fetch_single_ip(client, iface, asset_tag)
        sriov_vfs = _read_sriov_vfs(iface, asset_tag, default=16)

        underlays.append(
            RoceUnderlay(
                name=iface.name,
                mac=mac,
                mtu=mtu,
                sriov_vfs=sriov_vfs,
                address=address,
            )
        )
    # Sort by name so gpu0..gpu7 ordering is stable for golden-file tests.
    underlays.sort(key=lambda u: u.name)
    return underlays


def _build_ib_underlays(
    client: pynetbox.api, interfaces: Iterable[Any], asset_tag: str
) -> list[InfinibandUnderlay]:
    """Build InfinibandUnderlay models for every IPoIB NIC (ib0..ib7).

    Returns an empty list for non-InfiniBand hosts (none of their
    interfaces match the ``ibN`` naming convention). The HostIntent
    role-count invariant enforces "exactly 8" for gpu-h200 and "zero"
    elsewhere.

    Unlike RoCE underlays, IB underlays carry no ``sriov_vfs`` — InfiniBand
    RDMA is native to the HCA and IB SR-IOV is configured out-of-band, not
    via first-boot Netplan.
    """
    underlays: list[InfinibandUnderlay] = []
    for iface in interfaces:
        if not iface.name.startswith(IB_NIC_PREFIX):
            continue
        # Only plain "ibN" (defensive against e.g. "ibsm0" or a bridge).
        if not iface.name[len(IB_NIC_PREFIX) :].isdigit():
            continue

        mac = _read_mac(iface, asset_tag)
        # IPoIB datagram-mode default MTU is 2044.
        mtu = _read_mtu(iface, asset_tag, default=2044)
        address = _fetch_single_ip(client, iface, asset_tag)

        underlays.append(
            InfinibandUnderlay(name=iface.name, mac=mac, mtu=mtu, address=address)
        )
    # Sort by name so ib0..ib7 ordering is stable for golden-file tests.
    underlays.sort(key=lambda u: u.name)
    return underlays


# ---------------------------------------------------------------------------
# Field readers
# ---------------------------------------------------------------------------


def _read_mac(iface: Any, asset_tag: str) -> str:
    """Read the interface's primary MAC.

    Netbox 4.2+ moved MAC addresses out of the interface model into a
    first-class ``dcim.mac_addresses`` endpoint; the interface points
    back via ``primary_mac_address``, which pynetbox returns as a
    nested record with a ``mac_address`` attribute.
    """
    primary = getattr(iface, "primary_mac_address", None)
    if primary is None:
        raise NetboxQueryError(
            operation="read_mac",
            cause=ValueError(f"interface {iface.name!r} has no primary_mac_address"),
            asset_tag=asset_tag,
        )
    raw = getattr(primary, "mac_address", None) or str(primary)
    return str(raw).lower()


def _read_mtu(iface: Any, asset_tag: str, default: int) -> int:
    """Read the interface's MTU or fall back to a role-appropriate default."""
    mtu = getattr(iface, "mtu", None)
    if mtu is None:
        return default
    return int(mtu)


def _read_vlan_id(iface: Any, asset_tag: str) -> int:
    """Read the VLAN ID from the interface's untagged_vlan."""
    untagged = getattr(iface, "untagged_vlan", None)
    if untagged is None:
        raise NetboxQueryError(
            operation="read_vlan_id",
            cause=ValueError(f"interface {iface.name!r} has no untagged_vlan assignment"),
            asset_tag=asset_tag,
        )
    return int(untagged.vid)


def _read_vlan_role(iface: Any, asset_tag: str) -> VlanRole:
    """Map the untagged VLAN's name to a `VlanRole`.

    Convention: VLANs are named exactly one of ``mgmt``, ``storage``,
    ``ingress``. Anything else is an error.
    """
    untagged = getattr(iface, "untagged_vlan", None)
    if untagged is None:
        raise NetboxQueryError(
            operation="read_vlan_role",
            cause=ValueError(f"interface {iface.name!r} has no untagged_vlan"),
            asset_tag=asset_tag,
        )
    name = str(getattr(untagged, "name", ""))
    try:
        return VlanRole(name)
    except ValueError as e:
        raise NetboxQueryError(
            operation="read_vlan_role",
            cause=ValueError(
                f"unknown VLAN name {name!r} on {iface.name!r}; "
                f"accepted: {[r.value for r in VlanRole]}"
            ),
            asset_tag=asset_tag,
        ) from e


def _read_sriov_vfs(iface: Any, asset_tag: str, default: int) -> int:
    """Read the `sriov_vfs` custom field; fall back to default if unset."""
    cf = getattr(iface, "custom_fields", None) or {}
    value = cf.get("sriov_vfs")
    if value is None:
        return default
    return int(value)


def _fetch_single_ip(client: pynetbox.api, iface: Any, asset_tag: str) -> IPv4Interface:
    """Fetch the single IPv4 address assigned to an interface.

    Raises:
        NetboxQueryError: zero or multiple IPs are assigned. We expect
            exactly one per VLAN child or RoCE underlay in our topology;
            multi-IP support is future work.
    """
    try:
        ips = list(client.ipam.ip_addresses.filter(interface_id=iface.id))
    except Exception as e:
        raise NetboxQueryError(operation="list_ip_addresses", cause=e, asset_tag=asset_tag) from e
    if len(ips) != 1:
        raise NetboxQueryError(
            operation="list_ip_addresses",
            cause=ValueError(f"interface {iface.name!r}: expected 1 IP assignment, got {len(ips)}"),
            asset_tag=asset_tag,
        )
    return IPv4Interface(str(ips[0].address))


def _derive_gateway(address: IPv4Interface) -> IPv4Address:
    """Compute the gateway as the first usable IP in the address's prefix.

    Convention (per ADR-pending future work; for v1 it's hardcoded
    here): the gateway is the .1 of the /24 (or equivalent for other
    prefix sizes — the first host address of the network).
    """
    network = address.network
    return next(iter(network.hosts()))


__all__ = ["load_host_intent"]

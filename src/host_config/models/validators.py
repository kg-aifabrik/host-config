"""Cross-field validators for `HostIntent`.

Pydantic's per-field validators cover type/value constraints. Anything that
requires looking at *multiple* fields together — "exactly one default
gateway", "bond members reference existing NICs", "parent MTU ≥ child MTU" —
lives here. The `HostIntent` model in `intent.py` composes these via
`model_validator(mode='after')`.

Each function raises `InvariantError` with a stable invariant ID so callers
can branch on the failure type if needed.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from host_config.models.errors import InvariantError
from host_config.models.interface import (
    Bond,
    BondMember,
    InfinibandUnderlay,
    RoceUnderlay,
)
from host_config.models.vlan import VlanChild, VlanRole

# Expected counts per role. First-class constants so the invariant code
# reads cleanly and `ruff PLR2004` doesn't fire on bare integer comparisons.
EXPECTED_NS_NIC_COUNT = 2

# East-west underlay counts per role. A role's expected count is looked up
# here; roles absent from a map default to 0. RoCE roles (gpu-b300/b200)
# carry 8 Ethernet underlays; the InfiniBand role (gpu-h200) carries 8 IPoIB
# underlays and zero RoCE. cpu carries neither.
_ROCE_COUNT_BY_ROLE: dict[str, int] = {
    "cpu": 0,
    "gpu-b300": 8,
    "gpu-b200": 8,
    "gpu-h200": 0,
}
_IB_COUNT_BY_ROLE: dict[str, int] = {
    "cpu": 0,
    "gpu-b300": 0,
    "gpu-b200": 0,
    "gpu-h200": 8,
}


def check_ns_nic_count(ns_nics: list[BondMember]) -> None:
    """Exactly two N-S NICs.

    Approach:
        We require exactly two bond-member NICs on every host (the
        canonical 2x BlueField-3 DPU pair). Different counts indicate
        a malformed intent.

    Args:
        ns_nics: The list of N-S bond-member NICs from the host intent.

    Raises:
        InvariantError("ns-nic-count"): when count != 2.

    Scenarios:
        - Happy path: 2 NICs → no raise.
        - 0 NICs → raises.
        - 1 NIC → raises.
        - 3+ NICs → raises.
    """
    if len(ns_nics) != EXPECTED_NS_NIC_COUNT:
        raise InvariantError(
            "ns-nic-count",
            f"expected exactly {EXPECTED_NS_NIC_COUNT} N-S NICs (nsa + nsb), got {len(ns_nics)}",
        )


def check_bond_references_ns_nics(bond: Bond, ns_nics: list[BondMember]) -> None:
    """Every name in `bond.members` corresponds to a real `ns_nics` entry.

    Approach:
        The bond is declared by name; we cross-check that each member
        string resolves to an actual NIC in `ns_nics`. Catches typos
        and stale references.

    Args:
        bond: The host's bond model.
        ns_nics: The candidate N-S NICs.

    Raises:
        InvariantError("bond-member-unknown"): when a bond member name
            doesn't appear in `ns_nics`.

    Scenarios:
        - Happy path: bond.members == [n.name for n in ns_nics] → no raise.
        - bond.members contains a name not in ns_nics → raises.
        - bond.members in different order than ns_nics → no raise.
    """
    nic_names = {n.name for n in ns_nics}
    unknown = [m for m in bond.members if m not in nic_names]
    if unknown:
        raise InvariantError(
            "bond-member-unknown",
            f"bond {bond.name!r} references unknown NIC(s): {unknown!r}; "
            f"known NICs: {sorted(nic_names)!r}",
        )


def check_vlan_roles_complete(vlans: list[VlanChild]) -> None:
    """The VLAN children cover exactly {mgmt, storage, ingress}, each once.

    Approach:
        Every host has one VLAN per traffic class. Missing or duplicate
        roles indicate a malformed intent.

    Args:
        vlans: The host's VLAN children.

    Raises:
        InvariantError("vlan-roles-incomplete"): when the role multiset
            differs from {mgmt, storage, ingress}.

    Scenarios:
        - Happy path: one VLAN per role → no raise.
        - Two mgmt VLANs → raises.
        - Missing storage VLAN → raises.
        - 4 VLANs → raises.
    """
    roles = Counter(v.role for v in vlans)
    expected = Counter([VlanRole.MGMT, VlanRole.STORAGE, VlanRole.INGRESS])
    if roles != expected:
        raise InvariantError(
            "vlan-roles-incomplete",
            f"expected one VLAN per role (mgmt, storage, ingress), got roles={dict(roles)}",
        )


def check_vlan_parents(vlans: list[VlanChild], bond: Bond) -> None:
    """Every VLAN's `parent` equals the bond's name.

    Approach:
        At v1 we have a single bond per host and all three VLANs sit
        on it. Catches the case where a VLAN's parent reference drifted
        (e.g., from a rename of the bond).

    Args:
        vlans: The host's VLAN children.
        bond: The host's bond model.

    Raises:
        InvariantError("vlan-parent-mismatch"): when a VLAN's parent
            doesn't equal `bond.name`.

    Scenarios:
        - Happy path: all VLAN parents == bond.name → no raise.
        - One VLAN parents a different bond name → raises.
    """
    bad = [(v.name, v.parent) for v in vlans if v.parent != bond.name]
    if bad:
        raise InvariantError(
            "vlan-parent-mismatch",
            f"all VLANs must parent {bond.name!r}, got mismatches: {bad!r}",
        )


def check_exactly_one_default_gateway(vlans: list[VlanChild]) -> None:
    """Exactly one VLAN carries the default gateway.

    Approach:
        Per the design (and Linux kernel reality), every host has exactly
        one default route. We enforce that exactly one VLAN child declares
        a `gateway`; the convention is for that to be the mgmt VLAN, but
        we don't enforce the role at this layer — that's a separate
        invariant.

    Args:
        vlans: The host's VLAN children.

    Raises:
        InvariantError("default-gateway-count"): when the number of VLANs
            with a gateway is not exactly 1.

    Scenarios:
        - Happy path: one VLAN has a gateway → no raise.
        - Zero VLANs have a gateway → raises.
        - Two VLANs have gateways → raises.
    """
    with_gw = [v.name for v in vlans if v.gateway is not None]
    if len(with_gw) != 1:
        raise InvariantError(
            "default-gateway-count",
            f"expected exactly 1 VLAN with a default gateway, got {len(with_gw)}: {with_gw!r}",
        )


def check_default_gateway_on_mgmt(vlans: list[VlanChild]) -> None:
    """The VLAN with the default gateway is the mgmt VLAN.

    Approach:
        Per the design, only the management VLAN reaches the upstream
        default route. Storage and ingress are link-local in our topology.

    Args:
        vlans: The host's VLAN children.

    Raises:
        InvariantError("default-gateway-role"): when a non-mgmt VLAN
            carries the gateway.

    Scenarios:
        - Happy path: mgmt VLAN has the gateway → no raise.
        - Storage VLAN has a gateway → raises.
        - Ingress VLAN has a gateway → raises.
    """
    for v in vlans:
        if v.gateway is not None and v.role is not VlanRole.MGMT:
            raise InvariantError(
                "default-gateway-role",
                f"only the mgmt VLAN may carry the default gateway; "
                f"{v.name!r} is role={v.role.value!r} but declares a gateway",
            )


def check_mtu_monotone(bond: Bond, vlans: list[VlanChild]) -> None:
    """The bond's MTU is at least the max MTU of its VLAN children.

    Approach:
        Linux requires the parent MTU to be >= each VLAN child's MTU;
        otherwise the kernel rejects the assignment. Catching this at
        intent-build time gives a clearer error than the kernel's.

    Args:
        bond: The host's bond.
        vlans: The VLAN children (assumed to all parent the bond, which
            `check_vlan_parents` enforces).

    Raises:
        InvariantError("mtu-non-monotone"): when bond.mtu < max(vlan.mtu).

    Scenarios:
        - Happy path: bond=9000, vlans=1500/9000/1500 → no raise.
        - bond=1500, storage VLAN=9000 → raises.
    """
    if not vlans:
        return
    max_child = max(v.mtu for v in vlans)
    if bond.mtu < max_child:
        raise InvariantError(
            "mtu-non-monotone",
            f"bond MTU ({bond.mtu}) must be ≥ max VLAN child MTU ({max_child})",
        )


def check_roce_count_for_role(role: str, roce: list[RoceUnderlay]) -> None:
    """RoCE underlay count matches the host role.

    Approach:
        Looks the expected count up in ``_ROCE_COUNT_BY_ROLE`` (default 0):
        - cpu / gpu-h200: zero RoCE underlays.
        - gpu-b300 / gpu-b200: exactly 8 (one per GPU).

    Args:
        role: The host role string (`Role.value`).
        roce: The RoCE underlay list from the intent.

    Raises:
        InvariantError("roce-count-<role>"): when the count for that role
            differs from the expected value (e.g. "roce-count-cpu",
            "roce-count-gpu-b300", "roce-count-gpu-b200").

    Scenarios:
        - cpu + 0 RoCE → no raise; cpu + 1 RoCE → raises.
        - gpu-b300 + 8 RoCE → no raise; gpu-b300 + 7 → raises.
        - gpu-b200 + 8 RoCE → no raise; gpu-b200 + 0 → raises.
        - gpu-h200 + 0 RoCE → no raise; gpu-h200 + 8 RoCE → raises.
    """
    expected = _ROCE_COUNT_BY_ROLE.get(role, 0)
    if len(roce) != expected:
        raise InvariantError(
            f"roce-count-{role}",
            f"{role} hosts require exactly {expected} RoCE underlay(s), "
            f"got {len(roce)}",
        )


def check_ib_count_for_role(role: str, ib: list[InfinibandUnderlay]) -> None:
    """InfiniBand (IPoIB) underlay count matches the host role.

    Approach:
        Looks the expected count up in ``_IB_COUNT_BY_ROLE`` (default 0):
        - gpu-h200: exactly 8 (one IPoIB rail per GPU).
        - all other roles: zero.

    Args:
        role: The host role string (`Role.value`).
        ib: The InfiniBand underlay list from the intent.

    Raises:
        InvariantError("ib-count-<role>"): when the count for that role
            differs from the expected value (e.g. "ib-count-gpu-h200",
            "ib-count-cpu", "ib-count-gpu-b300").

    Scenarios:
        - gpu-h200 + 8 IB → no raise; gpu-h200 + 7 → raises.
        - cpu + 0 IB → no raise; cpu + 1 IB → raises.
        - gpu-b300 + 1 IB → raises (RoCE role must carry no IPoIB rails).
    """
    expected = _IB_COUNT_BY_ROLE.get(role, 0)
    if len(ib) != expected:
        raise InvariantError(
            f"ib-count-{role}",
            f"{role} hosts require exactly {expected} InfiniBand underlay(s), "
            f"got {len(ib)}",
        )


def check_unique_ips(
    vlans: list[VlanChild],
    roce: list[RoceUnderlay],
    ib: list[InfinibandUnderlay],
) -> None:
    """No two interfaces on the host share an IP address.

    Approach:
        Aggregates every IP allocated across VLAN children, RoCE underlays,
        and InfiniBand underlays; raises if any address (ignoring prefix)
        appears more than once.

    Args:
        vlans: The host's VLAN children.
        roce: The host's RoCE underlays (Ethernet east-west).
        ib: The host's InfiniBand underlays (IPoIB east-west).

    Raises:
        InvariantError("duplicate-ip"): when any IP is declared twice.

    Scenarios:
        - All IPs distinct → no raise.
        - mgmt VLAN and storage VLAN share an IP → raises.
        - gpu0 and gpu1 (or ib0 and ib1) share an underlay IP → raises.
    """
    seen: dict[str, str] = {}
    addressed = (
        *((str(v.address.ip), v.name) for v in vlans),
        *((str(r.address.ip), r.name) for r in roce),
        *((str(i.address.ip), i.name) for i in ib),
    )
    for ip, name in addressed:
        if ip in seen:
            raise InvariantError(
                "duplicate-ip",
                f"IP {ip} appears on both {seen[ip]!r} and {name!r}",
            )
        seen[ip] = name


def check_unique_names(
    ns_nics: list[BondMember],
    roce: list[RoceUnderlay],
    vlans: list[VlanChild],
    ib: list[InfinibandUnderlay],
) -> None:
    """Every interface name in the host is distinct.

    Approach:
        Collects names across N-S NICs, RoCE NICs, InfiniBand NICs, and
        VLAN children. Names must be globally unique — they map 1:1 to
        kernel netdevs.

    Args:
        ns_nics: N-S NIC list.
        roce: RoCE NIC list.
        vlans: VLAN child list.
        ib: InfiniBand NIC list.

    Raises:
        InvariantError("duplicate-name"): when any name repeats.

    Scenarios:
        - All distinct → no raise.
        - Two NICs named "nsa" → raises.
        - A VLAN named the same as a NIC → raises.
    """
    names: Iterable[str] = (
        *(n.name for n in ns_nics),
        *(r.name for r in roce),
        *(i.name for i in ib),
        *(v.name for v in vlans),
    )
    counts = Counter(names)
    dupes = [n for n, c in counts.items() if c > 1]
    if dupes:
        raise InvariantError(
            "duplicate-name",
            f"interface names must be unique; duplicates: {dupes!r}",
        )


__all__ = [
    "check_bond_references_ns_nics",
    "check_default_gateway_on_mgmt",
    "check_exactly_one_default_gateway",
    "check_ib_count_for_role",
    "check_mtu_monotone",
    "check_ns_nic_count",
    "check_roce_count_for_role",
    "check_unique_ips",
    "check_unique_names",
    "check_vlan_parents",
    "check_vlan_roles_complete",
]

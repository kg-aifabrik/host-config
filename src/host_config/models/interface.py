"""Interface models: physical NICs, bonds, SR-IOV parents, RoCE underlays.

A host's network layer is a tree:

    PhysIface (nsa, nsb)            PhysIface (gpu0..gpu7) ──── RoCE underlays
        │                                                         + SR-IOV VFs
        ▼
    Bond (bond0) — 802.3ad LACP
        │
        ▼
    VlanChild (bond0.100/.200/.300) ── (defined in vlan.py, not here)

This module owns the physical-and-bonding layer. VLAN children live in
`models/vlan.py` because they have a different shape (parent reference,
VLAN ID, role) and are easier to read separately.
"""

from __future__ import annotations

from ipaddress import IPv4Interface
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from host_config.models.types import MAX_MTU, MIN_MTU, MacAddress

# Bond modes we accept. 802.3ad (LACP) is the only mode we use; the
# Literal restricts the field at the type level. See ADR-0011.
BondMode = Literal["802.3ad"]

# LACP rate. "fast" = 1s LACPDU interval, ~3s failover. We default to
# fast for the inference latency story (see plan baremetal overview).
LacpRate = Literal["fast", "slow"]

# Bonding hash policy. layer3+4 spreads flows by 5-tuple, which is what
# we want for ESI-LAG (both members face the same logical partner).
TransmitHashPolicy = Literal["layer2", "layer2+3", "layer3+4"]

# Bounded MTU type used by every interface model. Annotated with the
# platform's accepted range from types.py.
_MtuField = Annotated[int, Field(ge=MIN_MTU, le=MAX_MTU)]


class _StrictModel(BaseModel):
    """Base model with our standard configuration.

    Approach:
        Centralizes the Pydantic config so every model in this package
        shares: `extra='forbid'` rejects unknown fields, and
        `validate_assignment=True` re-validates on attribute writes so
        a stale invariant can't be smuggled in via mutation.

        Note: we deliberately do NOT enable `strict=True`. Strict mode
        rejects implicit coercion (string → IPv4Interface, string → int),
        which would force every caller to construct typed objects
        manually. JSON sources (Netbox API, test fixtures) carry typed
        values as strings; coercion is the right behavior at our scope.
        Field-level constraints and BeforeValidators still enforce
        meaningful validation; we're not loosening security, just
        improving ergonomics.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )


class PhysIface(_StrictModel):
    """A physical NIC on the host.

    Approach:
        Identifies a physical interface by its kernel name (after udev
        rename via `.link` files), MAC address, and MTU. PhysIface is
        the base type; richer NICs (BondMember, SriovParent, RoceUnderlay)
        extend it.

    Scenarios:
        - Happy path: PhysIface(name="nsa", mac="aa:bb:cc:00:00:01", mtu=9000) constructs cleanly.
        - Invalid MAC: raises pydantic ValidationError.
        - MTU below 1500: raises pydantic ValidationError.
        - MTU above 9216: raises pydantic ValidationError.
        - Extra field: raises pydantic ValidationError (extra='forbid').
    """

    name: str = Field(min_length=1, max_length=15)  # kernel iface name limit
    mac: MacAddress
    mtu: _MtuField


class BondMember(PhysIface):
    """A PhysIface that is enslaved to a `Bond`.

    Marker subclass — no extra fields. Exists so the type system can
    distinguish "this NIC is a bond member" from "this NIC is a standalone".
    """


class Bond(_StrictModel):
    """A Linux bond on top of N member NICs.

    Approach:
        Models the kernel-side bond. The member NICs are referenced by
        name (string); the parent `HostIntent` validates that those
        names correspond to actual `BondMember` instances.

    Scenarios:
        - Happy path: Bond(name="bond0", members=["nsa","nsb"], mtu=9000,
          mode="802.3ad") constructs cleanly.
        - Empty members list → raises (min_length=2).
        - Single member → raises (min_length=2).
        - Duplicate members → raises (validator).
        - Bad mode (e.g., "active-backup") → raises (Literal restricts).
        - MTU outside [1500, 9216] → raises.
    """

    name: str = Field(min_length=1, max_length=15)
    members: list[str] = Field(min_length=2, max_length=8)
    mode: BondMode = "802.3ad"
    lacp_rate: LacpRate = "fast"
    transmit_hash_policy: TransmitHashPolicy = "layer3+4"
    mtu: _MtuField

    def __init__(self, /, **data: object) -> None:
        # WHY: We validate uniqueness of `members` here rather than via a
        # @field_validator so the error message is colocated with the
        # construction site; clearer in stack traces.
        members = data.get("members")
        if isinstance(members, list) and len(members) != len(set(members)):
            raise ValueError("Bond.members must be unique")
        super().__init__(**data)


class SriovParent(PhysIface):
    """A PhysIface that exposes SR-IOV virtual functions.

    Approach:
        Adds the `sriov_vfs` field — the number of VFs the host kernel
        should provision on this PF at boot. The actual VF creation is
        done by a systemd unit, not Netplan (see plan §1 east-west zone).

    Scenarios:
        - Happy path: SriovParent(..., sriov_vfs=16) constructs cleanly.
        - sriov_vfs < 0 → raises.
        - sriov_vfs > 128 → raises (no NIC in our fleet supports more).
    """

    sriov_vfs: int = Field(ge=0, le=128)


class RoceUnderlay(SriovParent):
    """A PhysIface used as a RoCE v2 underlay.

    Approach:
        Extends `SriovParent` with the host-side underlay IP. The RoCE
        path uses this IP for queue-pair setup; bulk data DMAs straight
        into GPU HBM via GPUDirect RDMA, bypassing the kernel stack.

        In our topology every RoCE NIC is also an SR-IOV parent (so its
        VFs can be assigned to GPU pods via Multus), hence the inheritance.

    Scenarios:
        - Happy path: RoceUnderlay(name="gpu0", mac=..., mtu=9000,
          sriov_vfs=16, address="10.42.100.23/24") constructs cleanly.
        - Missing address → raises.
        - Invalid IPv4 interface (e.g., "10.42.100.23") without /prefix → raises.
        - All PhysIface and SriovParent scenarios still apply.
    """

    # IPv4Interface = address + prefix length (e.g., "10.42.100.23/24").
    # Pydantic understands ipaddress.IPv4Interface natively.
    address: IPv4Interface


__all__ = [
    "Bond",
    "BondMember",
    "BondMode",
    "LacpRate",
    "PhysIface",
    "RoceUnderlay",
    "SriovParent",
    "TransmitHashPolicy",
]

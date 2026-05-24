"""VLAN sub-interface models.

Three VLANs sit on every host's `bond0`, each playing a distinct role
(management / storage / ingress). This module models the VLAN children;
the parent bond lives in `interface.py`.
"""

from __future__ import annotations

from enum import StrEnum
from ipaddress import IPv4Address, IPv4Interface
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from host_config.models.types import MAX_MTU, MAX_VLAN_ID, MIN_MTU, MIN_VLAN_ID


class VlanRole(StrEnum):
    """The three VLAN traffic classes carried over the N-S bond.

    Each B300 host has exactly one VLAN child per role (mgmt, storage,
    ingress). See the systems-overview ADR and baremetal-network-overview
    in the research repo for the rationale.
    """

    MGMT = "mgmt"
    STORAGE = "storage"
    INGRESS = "ingress"


class VlanChild(BaseModel):
    """A VLAN sub-interface (e.g., `bond0.100`) on top of a bond.

    Approach:
        Models the kernel-level VLAN child. The parent is referenced by
        bond name (string); the parent `HostIntent` validates that the
        name corresponds to an actual `Bond`. The `role` carries the
        traffic class so renderer templates can pick the right defaults.

    Scenarios:
        - Happy path: VlanChild(name="bond0.100", parent="bond0",
          vlan_id=100, role="mgmt", mtu=1500, address="10.42.10.23/24",
          gateway="10.42.10.1") constructs cleanly.
        - vlan_id outside [1, 4094] → raises.
        - mtu outside [1500, 9216] → raises.
        - Storage VLAN with a gateway → allowed at this layer (cross-field
          rule "only mgmt may have a gateway" enforced by HostIntent).
        - Missing address → raises.
        - Extra field → raises (extra='forbid').
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )

    name: str = Field(min_length=1, max_length=15)
    parent: str = Field(min_length=1, max_length=15)
    vlan_id: Annotated[int, Field(ge=MIN_VLAN_ID, le=MAX_VLAN_ID)]
    role: VlanRole
    mtu: Annotated[int, Field(ge=MIN_MTU, le=MAX_MTU)]
    address: IPv4Interface
    gateway: IPv4Address | None = None
    dns: list[IPv4Address] = Field(default_factory=list)
    search_domain: str | None = None


__all__ = ["VlanChild", "VlanRole"]

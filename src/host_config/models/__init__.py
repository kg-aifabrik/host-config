"""Pydantic models for the host-configuration intent.

Public types contributors import from this package:

- `HostIntent`        — the top-level model: a host's complete declared
                        intent, ready to be rendered.
- `Role`              — enum of supported host roles.
- `PhysIface`         — a physical NIC.
- `BondMember`        — a `PhysIface` enslaved to a bond.
- `Bond`              — a Linux bond on top of N members.
- `VlanChild`         — a VLAN sub-interface on top of a bond.
- `VlanRole`          — enum: mgmt / storage / ingress.
- `SriovParent`       — a `PhysIface` exposing SR-IOV virtual functions.
- `RoceUnderlay`      — an SR-IOV-capable `PhysIface` carrying a RoCE
                        underlay IP (east-west traffic).
- `MacAddress`        — validated MAC string type.

Errors live in `models/errors.py` and re-export here for caller convenience.
"""

from __future__ import annotations

from host_config.models.errors import InvariantError, ModelError
from host_config.models.intent import HostIntent, Role
from host_config.models.interface import (
    Bond,
    BondMember,
    PhysIface,
    RoceUnderlay,
    SriovParent,
)
from host_config.models.types import MacAddress
from host_config.models.vlan import VlanChild, VlanRole

__all__ = [
    "Bond",
    "BondMember",
    "HostIntent",
    "InvariantError",
    "MacAddress",
    "ModelError",
    "PhysIface",
    "RoceUnderlay",
    "Role",
    "SriovParent",
    "VlanChild",
    "VlanRole",
]

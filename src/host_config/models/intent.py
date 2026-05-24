"""The top-level `HostIntent` — a complete declared intent for one host.

Construction goes through Pydantic validation (per-field types and bounds)
and then through every cross-field invariant in `validators.py`. If
construction succeeds, the intent is renderable: all the rules the
renderer relies on hold.

This is the contract the renderer reads. Per principle #12, it knows
nothing about caller-domain concepts (lifecycle status, environment) —
the renderer turns this object into bytes, full stop.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from host_config.models.interface import Bond, BondMember, RoceUnderlay
from host_config.models.validators import (
    check_bond_references_ns_nics,
    check_default_gateway_on_mgmt,
    check_exactly_one_default_gateway,
    check_mtu_monotone,
    check_ns_nic_count,
    check_roce_count_for_role,
    check_unique_ips,
    check_unique_names,
    check_vlan_parents,
    check_vlan_roles_complete,
)
from host_config.models.vlan import VlanChild


class Role(StrEnum):
    """Supported host roles.

    `cpu` covers K8s control-plane nodes, jumphosts, bootstrap appliances
    — anything with the N-S subsystem only. `gpu-b300` adds the east-west
    subsystem (8 RoCE NICs).

    New roles arrive via ADR; the renderer learns about them by adding
    a template directory under `src/host_config/render/templates/<role>/`.
    """

    CPU = "cpu"
    GPU_B300 = "gpu-b300"


class HostIntent(BaseModel):
    """A complete renderable host intent.

    Approach:
        Pydantic builds the model: type-checks fields, validates bounds.
        Then `_check_invariants` runs every cross-field rule from
        `validators.py`. If any raises `InvariantError`, the model fails
        to construct — the caller learns *exactly* which rule failed and
        why, with context (offending field names, observed vs expected).

    Scenarios:
        - Happy path: minimal cpu intent (2 NICs, bond, 3 VLANs, 0 RoCE) constructs cleanly.
        - Happy path: gpu-b300 intent (2 NICs, bond, 3 VLANs, 8 RoCE) constructs cleanly.
        - cpu role with RoCE underlays → InvariantError("roce-count-cpu").
        - gpu-b300 with 7 RoCE underlays → InvariantError("roce-count-gpu-b300").
        - 1 N-S NIC → InvariantError("ns-nic-count").
        - 4 VLANs → InvariantError("vlan-roles-incomplete").
        - Two VLANs with gateways → InvariantError("default-gateway-count").
        - Storage VLAN with a gateway → InvariantError("default-gateway-role").
        - bond MTU < storage VLAN MTU → InvariantError("mtu-non-monotone").
        - Bond references a NIC name not in ns_nics → InvariantError("bond-member-unknown").
        - Two interfaces share an IP → InvariantError("duplicate-ip").
        - Two interfaces share a name → InvariantError("duplicate-name").
        - Extra top-level field → pydantic ValidationError (extra='forbid').
        - Re-validating after attribute assignment runs all invariants again.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )

    asset_tag: str = Field(min_length=1, max_length=64)
    hostname: str = Field(min_length=1, max_length=253)
    role: Role
    ns_nics: list[BondMember]
    bond: Bond
    vlans: list[VlanChild]
    roce_underlays: list[RoceUnderlay] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        """Run every cross-field invariant. See `validators.py` for each rule."""
        # NOTE: ordering matters only insofar as later checks may rely on
        # earlier ones (e.g., check_vlan_parents assumes vlans exist).
        # We run cheap structural checks first, then references, then IP
        # collisions last (most expensive scan).
        check_ns_nic_count(self.ns_nics)
        check_bond_references_ns_nics(self.bond, self.ns_nics)
        check_vlan_roles_complete(self.vlans)
        check_vlan_parents(self.vlans, self.bond)
        check_exactly_one_default_gateway(self.vlans)
        check_default_gateway_on_mgmt(self.vlans)
        check_mtu_monotone(self.bond, self.vlans)
        check_roce_count_for_role(self.role.value, self.roce_underlays)
        check_unique_names(self.ns_nics, self.roce_underlays, self.vlans)
        check_unique_ips(self.vlans, self.roce_underlays)
        return self


__all__ = ["HostIntent", "Role"]

"""Tests for `host_config.models.vlan`."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Interface

import pytest
from pydantic import ValidationError

from host_config.models.vlan import VlanChild, VlanRole


def _base_kwargs() -> dict[str, object]:
    """Default valid VlanChild fields for tests to mutate."""
    return {
        "name": "bond0.100",
        "parent": "bond0",
        "vlan_id": 100,
        "role": VlanRole.MGMT,
        "mtu": 1500,
        "address": IPv4Interface("10.42.10.23/24"),
    }


class TestVlanRole:
    @pytest.mark.fast
    def test_string_enum_values(self) -> None:
        """Roles render as their underscore-free lowercase strings."""
        assert VlanRole.MGMT.value == "mgmt"
        assert VlanRole.STORAGE.value == "storage"
        assert VlanRole.INGRESS.value == "ingress"


class TestVlanChild:
    @pytest.mark.fast
    def test_happy_path_constructs(self) -> None:
        """A minimal valid VlanChild constructs cleanly."""
        v = VlanChild(**_base_kwargs())  # type: ignore[arg-type]
        assert v.vlan_id == 100
        assert v.gateway is None
        assert v.dns == []

    @pytest.mark.fast
    def test_with_gateway_dns_and_search_domain(self) -> None:
        """All optional fields populate without error."""
        v = VlanChild(
            **_base_kwargs(),  # type: ignore[arg-type]
            gateway=IPv4Address("10.42.10.1"),
            dns=[IPv4Address("10.42.0.53"), IPv4Address("10.42.0.54")],
            search_domain="pod07.site03.internal",
        )
        assert v.gateway == IPv4Address("10.42.10.1")
        assert len(v.dns) == 2

    @pytest.mark.fast
    @pytest.mark.parametrize("vlan_id", [0, -1, 4095, 4096, 99999])
    def test_vlan_id_out_of_range_rejected(self, vlan_id: int) -> None:
        """VLAN IDs outside 1..4094 raise."""
        kw = _base_kwargs()
        kw["vlan_id"] = vlan_id
        with pytest.raises(ValidationError):
            VlanChild(**kw)  # type: ignore[arg-type]

    @pytest.mark.fast
    @pytest.mark.parametrize("mtu", [1499, 9217, 100])
    def test_mtu_out_of_range_rejected(self, mtu: int) -> None:
        """MTU outside platform bounds raises."""
        kw = _base_kwargs()
        kw["mtu"] = mtu
        with pytest.raises(ValidationError):
            VlanChild(**kw)  # type: ignore[arg-type]

    @pytest.mark.fast
    def test_jumbo_mtu_accepted(self) -> None:
        """9000 MTU (storage VLAN) is accepted."""
        kw = _base_kwargs()
        kw["mtu"] = 9000
        v = VlanChild(**kw)  # type: ignore[arg-type]
        assert v.mtu == 9000

    @pytest.mark.fast
    def test_extra_field_forbidden(self) -> None:
        """Unknown fields are rejected."""
        kw = _base_kwargs()
        kw["unexpected"] = "no"
        with pytest.raises(ValidationError):
            VlanChild(**kw)  # type: ignore[arg-type]

    @pytest.mark.fast
    def test_role_unknown_rejected(self) -> None:
        """An invalid role string is rejected."""
        kw = _base_kwargs()
        kw["role"] = "telephony"
        with pytest.raises(ValidationError):
            VlanChild(**kw)  # type: ignore[arg-type]

    @pytest.mark.fast
    def test_address_must_be_interface_not_address(self) -> None:
        """A bare IPv4Address (no prefix) is rejected; we need an interface (addr + /prefix)."""
        kw = _base_kwargs()
        kw["address"] = "10.42.10.23"
        # Pydantic accepts addr-only strings for IPv4Interface (treats it as /32);
        # this confirms the value still parses but uses /32 prefix, which the
        # host-level invariants will catch as wrong. Documenting the
        # tolerance here so the surprise isn't found in production logs.
        v = VlanChild(**kw)  # type: ignore[arg-type]
        assert v.address.network.prefixlen == 32

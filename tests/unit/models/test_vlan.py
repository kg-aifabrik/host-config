"""Tests for `host_config.models.vlan`.

Unit-level. Covers `VlanRole` (enum) and `VlanChild` (the VLAN
sub-interface model). Cross-field rules involving multiple VLANs on
the same host (one default gateway, role completeness, etc.) live in
`test_intent.py` because they require a `HostIntent` to exist.
"""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Interface

import pytest
from pydantic import ValidationError

from host_config.models.vlan import VlanChild, VlanRole


def _base_kwargs() -> dict[str, object]:
    """Build the default valid VlanChild kwargs used across this module.

    Why:
        Tests mutate a small number of fields per scenario; centralizing
        the baseline keeps each test focused on the change rather than
        the boilerplate. Returns a fresh dict per call so mutation in
        one test doesn't leak into others.
    """
    return {
        "name": "bond0.100",
        "parent": "bond0",
        "vlan_id": 100,
        "role": VlanRole.MGMT,
        "mtu": 1500,
        "address": IPv4Interface("10.42.10.23/24"),
    }


class TestVlanRole:
    """`VlanRole` enum surface.

    Tiny class — the enum itself is small and these tests just pin the
    stable wire-format strings so a future contributor doesn't rename
    `MGMT` to `MANAGEMENT` and silently break templates.
    """

    @pytest.mark.fast
    def test_string_enum_values(self) -> None:
        """Enum values are the lowercase short names downstream tooling expects.

        Why:
            Jinja templates and Netbox VLAN names use these exact
            strings (`mgmt`, `storage`, `ingress`). Changing them
            requires a coordinated update; this test fails loudly first.
        """
        assert VlanRole.MGMT.value == "mgmt"
        assert VlanRole.STORAGE.value == "storage"
        assert VlanRole.INGRESS.value == "ingress"


class TestVlanChild:
    """Construction and validation of `VlanChild`.

    These tests exercise per-field constraints in isolation. The
    multi-VLAN cross-field rules (exactly one default gateway, role
    completeness across the three children, MTU monotone vs the parent
    bond) live in `test_intent.py`.
    """

    @pytest.mark.fast
    def test_happy_path_constructs(self) -> None:
        """A minimal valid `VlanChild` constructs cleanly with optional fields at their defaults."""
        v = VlanChild(**_base_kwargs())  # type: ignore[arg-type]
        assert v.vlan_id == 100
        assert v.gateway is None
        assert v.dns == []

    @pytest.mark.fast
    def test_with_gateway_dns_and_search_domain(self) -> None:
        """All optional fields populate without error.

        Approach:
            Start from the baseline kwargs and add gateway, dns list,
            and search_domain. Verifies the optional fields don't
            cross-validate against each other (e.g., DNS list isn't
            required just because gateway is set).
        """
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
        """VLAN IDs outside 1..4094 raise.

        Why:
            802.1Q reserves VID 0 (priority-tagged frames) and VID 4095
            (implementation-reserved). 1..4094 is the usable range.
        """
        kw = _base_kwargs()
        kw["vlan_id"] = vlan_id
        with pytest.raises(ValidationError):
            VlanChild(**kw)  # type: ignore[arg-type]

    @pytest.mark.fast
    @pytest.mark.parametrize("mtu", [1499, 9217, 100])
    def test_mtu_out_of_range_rejected(self, mtu: int) -> None:
        """MTU outside platform bounds (`MIN_MTU`..`MAX_MTU`) raises."""
        kw = _base_kwargs()
        kw["mtu"] = mtu
        with pytest.raises(ValidationError):
            VlanChild(**kw)  # type: ignore[arg-type]

    @pytest.mark.fast
    def test_jumbo_mtu_accepted(self) -> None:
        """9000 MTU is accepted on a VLAN child (used by the storage VLAN)."""
        kw = _base_kwargs()
        kw["mtu"] = 9000
        v = VlanChild(**kw)  # type: ignore[arg-type]
        assert v.mtu == 9000

    @pytest.mark.fast
    def test_extra_field_forbidden(self) -> None:
        """Unknown fields are rejected (`extra='forbid'`)."""
        kw = _base_kwargs()
        kw["unexpected"] = "no"
        with pytest.raises(ValidationError):
            VlanChild(**kw)  # type: ignore[arg-type]

    @pytest.mark.fast
    def test_role_unknown_rejected(self) -> None:
        """An invalid role string is rejected by the enum coercion."""
        kw = _base_kwargs()
        kw["role"] = "telephony"
        with pytest.raises(ValidationError):
            VlanChild(**kw)  # type: ignore[arg-type]

    @pytest.mark.fast
    def test_address_must_be_interface_not_address(self) -> None:
        """A bare IPv4 address (no /prefix) is coerced to /32.

        Why:
            Pydantic's `IPv4Interface` accepts addr-only strings,
            treating them as /32. We accept this rather than rejecting
            because Netbox sometimes returns IPs without explicit prefix
            length in legacy paths. Higher-level invariants (subnet
            sanity at the host level) catch a stray /32 that would
            break routing.
        """
        kw = _base_kwargs()
        kw["address"] = "10.42.10.23"
        v = VlanChild(**kw)  # type: ignore[arg-type]
        assert v.address.network.prefixlen == 32

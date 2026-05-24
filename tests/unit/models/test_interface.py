"""Tests for `host_config.models.interface`.

Covers PhysIface, BondMember, Bond, SriovParent, RoceUnderlay individually.
Cross-field behavior is tested in `test_intent.py` where it actually matters.
"""

from __future__ import annotations

from ipaddress import IPv4Interface

import pytest
from pydantic import ValidationError

from host_config.models.interface import (
    Bond,
    BondMember,
    PhysIface,
    RoceUnderlay,
    SriovParent,
)

VALID_MAC = "aa:bb:cc:00:00:01"


class TestPhysIface:
    """Construction and validation of PhysIface."""

    @pytest.mark.fast
    def test_happy_path_constructs(self) -> None:
        """A valid PhysIface constructs cleanly."""
        nic = PhysIface(name="nsa", mac=VALID_MAC, mtu=9000)
        assert nic.name == "nsa"
        assert nic.mtu == 9000

    @pytest.mark.fast
    def test_invalid_mac_raises(self) -> None:
        """A malformed MAC raises ValidationError."""
        with pytest.raises(ValidationError):
            PhysIface(name="nsa", mac="not-a-mac", mtu=9000)

    @pytest.mark.fast
    @pytest.mark.parametrize("mtu", [1499, 1000, 9217, 100000, 0, -1])
    def test_mtu_outside_bounds_raises(self, mtu: int) -> None:
        """MTU values outside [1500, 9216] are rejected."""
        with pytest.raises(ValidationError):
            PhysIface(name="nsa", mac=VALID_MAC, mtu=mtu)

    @pytest.mark.fast
    @pytest.mark.parametrize("mtu", [1500, 9000, 9216])
    def test_mtu_within_bounds_accepted(self, mtu: int) -> None:
        """MTU values inside the platform range are accepted."""
        nic = PhysIface(name="nsa", mac=VALID_MAC, mtu=mtu)
        assert nic.mtu == mtu

    @pytest.mark.fast
    def test_extra_field_forbidden(self) -> None:
        """Extra fields are rejected (extra='forbid' contract)."""
        with pytest.raises(ValidationError):
            PhysIface(  # type: ignore[call-arg]
                name="nsa", mac=VALID_MAC, mtu=9000, color="green"
            )

    @pytest.mark.fast
    def test_empty_name_rejected(self) -> None:
        """Empty interface name is rejected (kernel netdev names must be 1..15 chars)."""
        with pytest.raises(ValidationError):
            PhysIface(name="", mac=VALID_MAC, mtu=9000)

    @pytest.mark.fast
    def test_oversize_name_rejected(self) -> None:
        """Interface name >15 chars is rejected (Linux IFNAMSIZ limit)."""
        with pytest.raises(ValidationError):
            PhysIface(name="a" * 16, mac=VALID_MAC, mtu=9000)


class TestBondMember:
    """BondMember is a PhysIface subclass; should accept the same inputs."""

    @pytest.mark.fast
    def test_constructs_like_physiface(self) -> None:
        """BondMember accepts the same fields as PhysIface."""
        nic = BondMember(name="nsa", mac=VALID_MAC, mtu=9000)
        assert isinstance(nic, PhysIface)
        assert nic.name == "nsa"


class TestBond:
    """Bond model — independent of the referenced NICs (those checked at HostIntent layer)."""

    @pytest.mark.fast
    def test_happy_path_constructs(self) -> None:
        """A bond with two members and default 802.3ad config constructs cleanly."""
        b = Bond(name="bond0", members=["nsa", "nsb"], mtu=9000)
        assert b.mode == "802.3ad"
        assert b.lacp_rate == "fast"
        assert b.transmit_hash_policy == "layer3+4"

    @pytest.mark.fast
    def test_single_member_rejected(self) -> None:
        """A bond needs at least 2 members."""
        with pytest.raises(ValidationError):
            Bond(name="bond0", members=["nsa"], mtu=9000)

    @pytest.mark.fast
    def test_empty_members_rejected(self) -> None:
        """An empty member list is rejected."""
        with pytest.raises(ValidationError):
            Bond(name="bond0", members=[], mtu=9000)

    @pytest.mark.fast
    def test_duplicate_members_rejected(self) -> None:
        """A bond cannot enslave the same NIC twice."""
        with pytest.raises(ValueError, match="unique"):
            Bond(name="bond0", members=["nsa", "nsa"], mtu=9000)

    @pytest.mark.fast
    def test_invalid_mode_rejected(self) -> None:
        """Mode 'active-backup' is rejected (only 802.3ad accepted)."""
        with pytest.raises(ValidationError):
            Bond(
                name="bond0",
                members=["nsa", "nsb"],
                mode="active-backup",
                mtu=9000,
            )

    @pytest.mark.fast
    def test_mtu_out_of_range_rejected(self) -> None:
        """MTU 1000 is rejected."""
        with pytest.raises(ValidationError):
            Bond(name="bond0", members=["nsa", "nsb"], mtu=1000)


class TestSriovParent:
    """SriovParent adds sriov_vfs to PhysIface."""

    @pytest.mark.fast
    def test_happy_path_constructs(self) -> None:
        """Valid sriov_vfs constructs cleanly."""
        nic = SriovParent(name="gpu0", mac=VALID_MAC, mtu=9000, sriov_vfs=16)
        assert nic.sriov_vfs == 16

    @pytest.mark.fast
    @pytest.mark.parametrize("vfs", [-1, 129, 1000])
    def test_sriov_vfs_out_of_bounds_rejected(self, vfs: int) -> None:
        """sriov_vfs outside [0, 128] is rejected."""
        with pytest.raises(ValidationError):
            SriovParent(name="gpu0", mac=VALID_MAC, mtu=9000, sriov_vfs=vfs)

    @pytest.mark.fast
    def test_zero_sriov_vfs_accepted(self) -> None:
        """0 VFs is a legitimate (if unusual) configuration."""
        nic = SriovParent(name="gpu0", mac=VALID_MAC, mtu=9000, sriov_vfs=0)
        assert nic.sriov_vfs == 0


class TestRoceUnderlay:
    """RoceUnderlay extends SriovParent with address."""

    @pytest.mark.fast
    def test_happy_path_constructs(self) -> None:
        """A RoCE underlay with all fields constructs cleanly."""
        nic = RoceUnderlay(
            name="gpu0",
            mac=VALID_MAC,
            mtu=9000,
            sriov_vfs=16,
            address=IPv4Interface("10.42.100.23/24"),
        )
        assert str(nic.address) == "10.42.100.23/24"

    @pytest.mark.fast
    def test_string_address_coerced(self) -> None:
        """A string IPv4 interface is coerced to IPv4Interface.

        Coercion is intentional (non-strict mode); Pydantic accepts
        strings here. The `# type: ignore` is required because mypy
        sees only the declared type (`IPv4Interface`) and doesn't
        know about Pydantic's coercion at runtime.
        """
        nic = RoceUnderlay(
            name="gpu0",
            mac=VALID_MAC,
            mtu=9000,
            sriov_vfs=16,
            address="10.42.100.23/24",  # type: ignore[arg-type]
        )
        assert nic.address == IPv4Interface("10.42.100.23/24")

    @pytest.mark.fast
    def test_invalid_address_rejected(self) -> None:
        """A non-IP string in the address field is rejected."""
        with pytest.raises(ValidationError):
            RoceUnderlay(
                name="gpu0",
                mac=VALID_MAC,
                mtu=9000,
                sriov_vfs=16,
                address="not-an-ip",  # type: ignore[arg-type]
            )

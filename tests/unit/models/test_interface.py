"""Tests for `host_config.models.interface`.

Unit-level. Covers per-model construction and field validation for the
five interface types: `PhysIface`, `BondMember`, `Bond`, `SriovParent`,
`RoceUnderlay`. Each test exercises one constraint or boundary.

Cross-field behavior (e.g., bond members must exist in `ns_nics`, MTU
monotonicity across the parent/child relationship) is tested in
`test_intent.py` because it requires composing a full `HostIntent`.

See `src/host_config/models/interface.py` for the types and their
declared invariants.
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

# A canonical valid MAC used across the suite. Keeping it as a module
# constant means a future MAC-format change updates one location.
VALID_MAC = "aa:bb:cc:00:00:01"


class TestPhysIface:
    """Construction and validation of `PhysIface`.

    `PhysIface` is the base type every other interface model extends.
    Validations defined here (MAC, MTU, name length, extra-field-forbid)
    apply transitively to `BondMember`, `SriovParent`, and `RoceUnderlay`.
    """

    @pytest.mark.fast
    def test_happy_path_constructs(self) -> None:
        """A valid `PhysIface` constructs cleanly with all three required fields."""
        nic = PhysIface(name="nsa", mac=VALID_MAC, mtu=9000)
        assert nic.name == "nsa"
        assert nic.mtu == 9000

    @pytest.mark.fast
    def test_invalid_mac_raises(self) -> None:
        """A malformed MAC string is rejected.

        Approach:
            Pass a clearly non-MAC string; expect a Pydantic
            `ValidationError`. The detailed format validation is
            exercised in `test_types.py::TestMacAddressValidation`;
            this test confirms the `mac` field hooks the validator.
        """
        with pytest.raises(ValidationError):
            PhysIface(name="nsa", mac="not-a-mac", mtu=9000)

    @pytest.mark.fast
    @pytest.mark.parametrize("mtu", [1499, 1000, 9217, 100000, 0, -1])
    def test_mtu_outside_bounds_raises(self, mtu: int) -> None:
        """MTU values outside [1500, 9216] are rejected.

        Why:
            1500 is the Ethernet minimum we support; 9216 is the jumbo
            ceiling per `MAX_MTU` in `types.py`. Below 1500 breaks
            interop with non-jumbo paths; above 9216 exceeds hardware
            framers on our target NICs.
        """
        with pytest.raises(ValidationError):
            PhysIface(name="nsa", mac=VALID_MAC, mtu=mtu)

    @pytest.mark.fast
    @pytest.mark.parametrize("mtu", [1500, 9000, 9216])
    def test_mtu_within_bounds_accepted(self, mtu: int) -> None:
        """The three canonical platform MTUs (standard, jumbo, jumbo-ceiling) work."""
        nic = PhysIface(name="nsa", mac=VALID_MAC, mtu=mtu)
        assert nic.mtu == mtu

    @pytest.mark.fast
    def test_extra_field_forbidden(self) -> None:
        """Unknown keyword arguments are rejected (`extra='forbid'` contract).

        Why:
            Catches typos and accidental over-specification. The
            renderer relies on `HostIntent` having no surprise fields;
            silently accepting unknown keys would let drift accumulate.
        """
        with pytest.raises(ValidationError):
            PhysIface(  # type: ignore[call-arg]
                name="nsa", mac=VALID_MAC, mtu=9000, color="green"
            )

    @pytest.mark.fast
    def test_empty_name_rejected(self) -> None:
        """Empty interface name is rejected (kernel netdev names are 1..15 chars)."""
        with pytest.raises(ValidationError):
            PhysIface(name="", mac=VALID_MAC, mtu=9000)

    @pytest.mark.fast
    def test_oversize_name_rejected(self) -> None:
        """Interface name >15 chars is rejected (Linux IFNAMSIZ limit).

        Why:
            The kernel rejects names longer than IFNAMSIZ-1 = 15 chars.
            Catching this at the model layer beats a confusing
            `netlink: invalid argument` at apply time.
        """
        with pytest.raises(ValidationError):
            PhysIface(name="a" * 16, mac=VALID_MAC, mtu=9000)


class TestBondMember:
    """`BondMember` is a marker subclass of `PhysIface` with no extra fields.

    The class exists for type-system clarity — callers can require
    `BondMember` to mean "this NIC is enslaved to a bond." These tests
    confirm the subclass relationship without re-testing every PhysIface
    constraint.
    """

    @pytest.mark.fast
    def test_constructs_like_physiface(self) -> None:
        """`BondMember` accepts the same fields as `PhysIface` and is-a `PhysIface`."""
        nic = BondMember(name="nsa", mac=VALID_MAC, mtu=9000)
        assert isinstance(nic, PhysIface)
        assert nic.name == "nsa"


class TestBond:
    """Construction and validation of `Bond`.

    The bond itself is validated in isolation here; cross-field rules
    that involve other NICs (members must exist in `ns_nics`) are tested
    in `test_intent.py`.
    """

    @pytest.mark.fast
    def test_happy_path_constructs(self) -> None:
        """A bond with two members and the default 802.3ad config constructs cleanly.

        Approach:
            Construct without specifying `mode`/`lacp_rate`/
            `transmit_hash_policy`; verify defaults are the ones we
            want for ESI-LAG (802.3ad / fast / layer3+4).
        """
        b = Bond(name="bond0", members=["nsa", "nsb"], mtu=9000)
        assert b.mode == "802.3ad"
        assert b.lacp_rate == "fast"
        assert b.transmit_hash_policy == "layer3+4"

    @pytest.mark.fast
    def test_single_member_rejected(self) -> None:
        """A bond needs at least 2 members (Pydantic `min_length=2`)."""
        with pytest.raises(ValidationError):
            Bond(name="bond0", members=["nsa"], mtu=9000)

    @pytest.mark.fast
    def test_empty_members_rejected(self) -> None:
        """An empty member list is rejected."""
        with pytest.raises(ValidationError):
            Bond(name="bond0", members=[], mtu=9000)

    @pytest.mark.fast
    def test_duplicate_members_rejected(self) -> None:
        """A bond cannot enslave the same NIC twice.

        Why:
            Catches a foot-gun where a hand-written fixture lists the
            same NIC name twice in `members`. The bond's `__init__`
            validates uniqueness before delegating to Pydantic, so the
            error message is colocated with the construction site
            (clearer in stack traces than a downstream Pydantic error).
        """
        with pytest.raises(ValueError, match="unique"):
            Bond(name="bond0", members=["nsa", "nsa"], mtu=9000)

    @pytest.mark.fast
    def test_invalid_mode_rejected(self) -> None:
        """Mode `active-backup` is rejected — only `802.3ad` accepted.

        Why:
            The ESI-LAG fabric only speaks LACP. Allowing
            `active-backup` would let a misconfigured intent pass
            validation but produce a Netplan config that doesn't form
            a real LACP bond at boot.
        """
        with pytest.raises(ValidationError):
            Bond(
                name="bond0",
                members=["nsa", "nsb"],
                mode="active-backup",
                mtu=9000,
            )

    @pytest.mark.fast
    def test_mtu_out_of_range_rejected(self) -> None:
        """Bond MTU 1000 is rejected (below `MIN_MTU=1500`)."""
        with pytest.raises(ValidationError):
            Bond(name="bond0", members=["nsa", "nsb"], mtu=1000)


class TestSriovParent:
    """Construction and validation of `SriovParent` (adds `sriov_vfs` to `PhysIface`)."""

    @pytest.mark.fast
    def test_happy_path_constructs(self) -> None:
        """A valid `SriovParent` with `sriov_vfs=16` constructs cleanly."""
        nic = SriovParent(name="gpu0", mac=VALID_MAC, mtu=9000, sriov_vfs=16)
        assert nic.sriov_vfs == 16

    @pytest.mark.fast
    @pytest.mark.parametrize("vfs", [-1, 129, 1000])
    def test_sriov_vfs_out_of_bounds_rejected(self, vfs: int) -> None:
        """`sriov_vfs` outside [0, 128] is rejected.

        Why:
            0 is allowed (no VFs is a legitimate config); 128 is the
            ceiling — ConnectX-class NICs don't expose more.
        """
        with pytest.raises(ValidationError):
            SriovParent(name="gpu0", mac=VALID_MAC, mtu=9000, sriov_vfs=vfs)

    @pytest.mark.fast
    def test_zero_sriov_vfs_accepted(self) -> None:
        """0 VFs is a legitimate (if unusual) configuration — not rejected.

        Why:
            Future hosts may run without SR-IOV; the constraint isn't
            "must have VFs" — it's "if you have VFs, declare the count."
        """
        nic = SriovParent(name="gpu0", mac=VALID_MAC, mtu=9000, sriov_vfs=0)
        assert nic.sriov_vfs == 0


class TestRoceUnderlay:
    """Construction and validation of `RoceUnderlay` (extends `SriovParent` with `address`)."""

    @pytest.mark.fast
    def test_happy_path_constructs(self) -> None:
        """A `RoceUnderlay` with all fields (incl. `IPv4Interface` address) constructs cleanly."""
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
        """A string IPv4 interface is coerced to `IPv4Interface`.

        Why:
            JSON sources (Netbox API, YAML fixtures, test data) carry
            IPs as strings. We dropped Pydantic `strict=True` precisely
            so this coercion works at the boundary (see
            `_StrictModel` docstring). The `# type: ignore` is needed
            because mypy sees only the declared `IPv4Interface` type
            and doesn't know about Pydantic's runtime coercion.
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
        """A non-IP string in `address` is rejected by Pydantic.

        Approach:
            Pass `"not-an-ip"`; expect `ValidationError`. The model
            doesn't try to be clever about coercion — only valid
            IPv4-shaped strings pass.
        """
        with pytest.raises(ValidationError):
            RoceUnderlay(
                name="gpu0",
                mac=VALID_MAC,
                mtu=9000,
                sriov_vfs=16,
                address="not-an-ip",  # type: ignore[arg-type]
            )

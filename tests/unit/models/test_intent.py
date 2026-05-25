"""Tests for `host_config.models.intent` and the cross-field invariants in
`host_config.models.validators`.

Unit-level. This is the load-bearing test file for the renderer's domain
model: every cross-field invariant declared in `validators.py` is
exercised here against a deliberately broken `HostIntent`, plus the
happy-path constructions for both supported roles.

Structure:

- Fixture factories (`make_cpu_intent` / `make_b300_intent`) return
  fully-valid intents the per-invariant tests then mutate. Mutation
  happens via `_rebuild`, which round-trips through `model_dump()` so
  the model_validator runs on the mutated dict — letting us construct
  intentionally-broken intents that we couldn't construct via the
  Pydantic API directly (validators run on creation).
- `TestHappyPaths` — sanity checks the fixture factories produce
  valid intents.
- `TestCrossFieldInvariants` — one test per `InvariantError` code in
  `validators.py`. Each deliberately violates exactly one rule.
- `TestPydanticBoundary` — sanity checks that Pydantic-level
  rejections (extra fields, unknown enum values) fire before the
  cross-field validators run.
"""

from __future__ import annotations

from copy import deepcopy
from ipaddress import IPv4Address, IPv4Interface
from typing import Any, cast

import pytest
from pydantic import ValidationError

from host_config.models.errors import InvariantError
from host_config.models.intent import HostIntent, Role
from host_config.models.interface import Bond, BondMember, RoceUnderlay
from host_config.models.vlan import VlanChild, VlanRole

# ---------------------------------------------------------------------------
# Fixture factories — return fully valid intents that individual tests mutate.
# ---------------------------------------------------------------------------


def make_cpu_intent() -> HostIntent:
    """A valid `cpu`-role `HostIntent` mirroring the YAML fixture `cpu-host.yaml`.

    Returns a fresh instance per call so tests can mutate without
    cross-contaminating. All identifiers (hostname, MACs, IPs) must
    exactly match `fixtures/netbox/data/cpu-host.yaml` so that the
    goldens generated from this factory are byte-equal to what the
    pipeline produces from a live Netbox populated with those fixtures.

    If you change any value here, update the YAML fixture and regenerate
    the goldens (`src/host_config/render/golden/cpu/*`).
    """
    return HostIntent(
        asset_tag="SN-CPU-001",
        hostname="k8s-cp-01.pod07.site03.internal",
        role=Role.CPU,
        ns_nics=[
            BondMember(name="nsa", mac="aa:bb:cc:00:01:01", mtu=9000),
            BondMember(name="nsb", mac="aa:bb:cc:00:01:02", mtu=9000),
        ],
        bond=Bond(name="bond0", members=["nsa", "nsb"], mtu=9000),
        vlans=[
            VlanChild(
                name="bond0.100",
                parent="bond0",
                vlan_id=100,
                role=VlanRole.MGMT,
                mtu=1500,
                address=IPv4Interface("10.42.10.11/24"),
                gateway=IPv4Address("10.42.10.1"),
            ),
            VlanChild(
                name="bond0.200",
                parent="bond0",
                vlan_id=200,
                role=VlanRole.STORAGE,
                mtu=9000,
                address=IPv4Interface("10.42.20.11/24"),
            ),
            VlanChild(
                name="bond0.300",
                parent="bond0",
                vlan_id=300,
                role=VlanRole.INGRESS,
                mtu=1500,
                address=IPv4Interface("10.42.30.11/24"),
            ),
        ],
    )


def make_b300_intent() -> HostIntent:
    """A valid `gpu-b300`-role `HostIntent` mirroring the YAML fixture `b300-host.yaml`.

    All identifiers (hostname, MACs, IPs) must exactly match
    `fixtures/netbox/data/b300-host.yaml`. The N-S subsystem uses the
    B300 fixture's own MACs and IPs (distinct from the cpu fixture).
    The 8 RoCE underlays (gpu0..gpu7) use MACs aa:bb:cc:00:00:10..17
    and per-NIC IPs 10.42.100..107.23/24, as declared in the YAML.

    If you change any value here, update the YAML fixture and regenerate
    the goldens (`src/host_config/render/golden/gpu-b300/*`).
    """
    roce = [
        RoceUnderlay(
            name=f"gpu{i}",
            mac=f"aa:bb:cc:00:00:{0x10 + i:02x}",
            mtu=9000,
            sriov_vfs=16,
            address=IPv4Interface(f"10.42.{100 + i}.23/24"),
        )
        for i in range(8)
    ]
    return HostIntent(
        asset_tag="SN-GPU-001",
        hostname="gpu-b300-01.pod07.site03.internal",
        role=Role.GPU_B300,
        ns_nics=[
            BondMember(name="nsa", mac="aa:bb:cc:00:00:01", mtu=9000),
            BondMember(name="nsb", mac="aa:bb:cc:00:00:02", mtu=9000),
        ],
        bond=Bond(name="bond0", members=["nsa", "nsb"], mtu=9000),
        vlans=[
            VlanChild(
                name="bond0.100",
                parent="bond0",
                vlan_id=100,
                role=VlanRole.MGMT,
                mtu=1500,
                address=IPv4Interface("10.42.10.23/24"),
                gateway=IPv4Address("10.42.10.1"),
            ),
            VlanChild(
                name="bond0.200",
                parent="bond0",
                vlan_id=200,
                role=VlanRole.STORAGE,
                mtu=9000,
                address=IPv4Interface("10.42.20.23/24"),
            ),
            VlanChild(
                name="bond0.300",
                parent="bond0",
                vlan_id=300,
                role=VlanRole.INGRESS,
                mtu=1500,
                address=IPv4Interface("10.42.30.23/24"),
            ),
        ],
        roce_underlays=roce,
    )


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestHappyPaths:
    """Sanity checks that the two fixture factories produce valid intents.

    If these fail, every other test in this file is suspect — the
    fixtures are wrong and the per-invariant tests are mutating broken
    baselines. These are deliberately first.
    """

    @pytest.mark.fast
    def test_cpu_intent_constructs(self) -> None:
        """A clean cpu-role intent constructs and has the expected shape."""
        intent = make_cpu_intent()
        assert intent.role is Role.CPU
        assert len(intent.ns_nics) == 2
        assert len(intent.vlans) == 3
        assert intent.roce_underlays == []

    @pytest.mark.fast
    def test_b300_intent_constructs(self) -> None:
        """A clean gpu-b300-role intent with all 10 NICs constructs and has the expected shape."""
        intent = make_b300_intent()
        assert intent.role is Role.GPU_B300
        assert len(intent.roce_underlays) == 8


# ---------------------------------------------------------------------------
# Cross-field invariant violations — one test per InvariantError code.
# ---------------------------------------------------------------------------


def _rebuild(intent: HostIntent, **overrides: Any) -> HostIntent:
    """Round-trip an intent with field overrides through `model_validate`.

    Approach:
        1. `intent.model_dump()` → plain dict (preserves nested
           structures as dicts; IPv4Interface values stay as objects).
        2. Apply each keyword override at the top level.
        3. `HostIntent.model_validate(data)` → runs full Pydantic field
           validation AND the `model_validator(mode='after')` chain.

    Why:
        Pydantic's `validate_assignment=True` would let us mutate
        attributes in place and re-validate, but cross-field validators
        run *only* against the in-flight instance — broken combinations
        get rejected at construction. To test "VLAN role mismatch
        raises", we have to construct a broken intent from a dict, not
        via the Python API.
    """
    # Round-trip via model_dump to get a plain dict, mutate, re-construct.
    data = intent.model_dump()
    for k, v in overrides.items():
        data[k] = v
    return HostIntent.model_validate(data)


class TestCrossFieldInvariants:
    """One test per `InvariantError` code from `validators.py`.

    Each test deliberately violates exactly one rule and asserts:
        - The specific `InvariantError` was raised.
        - `exc.value.invariant` matches the documented invariant ID.

    Tests intentionally do NOT assert against the `detail` message
    contents — that's wording the developer of the validator should be
    free to tune. The invariant ID is the stable contract.
    """

    @pytest.mark.fast
    def test_one_ns_nic_raises_ns_nic_count(self) -> None:
        """1 N-S NIC → InvariantError ns-nic-count."""
        intent = make_cpu_intent()
        # Pydantic's model_dump uses dicts; we mutate the list directly.
        with pytest.raises(InvariantError) as exc:
            _rebuild(intent, ns_nics=intent.model_dump()["ns_nics"][:1])
        assert exc.value.invariant == "ns-nic-count"

    @pytest.mark.fast
    def test_three_ns_nics_raises_ns_nic_count(self) -> None:
        """3 N-S NICs → InvariantError ns-nic-count."""
        intent = make_cpu_intent()
        data = intent.model_dump()
        extra_nic = deepcopy(data["ns_nics"][0])
        extra_nic["name"] = "nsc"
        extra_nic["mac"] = "aa:bb:cc:00:00:03"
        data["ns_nics"] = [*data["ns_nics"], extra_nic]
        with pytest.raises(InvariantError) as exc:
            HostIntent.model_validate(data)
        assert exc.value.invariant == "ns-nic-count"

    @pytest.mark.fast
    def test_bond_references_unknown_nic_raises(self) -> None:
        """Bond.members contains a name not in ns_nics → InvariantError bond-member-unknown."""
        intent = make_cpu_intent()
        data = intent.model_dump()
        data["bond"]["members"] = ["nsa", "ghost"]
        with pytest.raises(InvariantError) as exc:
            HostIntent.model_validate(data)
        assert exc.value.invariant == "bond-member-unknown"

    @pytest.mark.fast
    def test_missing_vlan_role_raises(self) -> None:
        """Missing storage VLAN → InvariantError vlan-roles-incomplete."""
        intent = make_cpu_intent()
        data = intent.model_dump()
        data["vlans"] = [v for v in data["vlans"] if v["role"] != "storage"]
        with pytest.raises(InvariantError) as exc:
            HostIntent.model_validate(data)
        assert exc.value.invariant == "vlan-roles-incomplete"

    @pytest.mark.fast
    def test_duplicate_vlan_role_raises(self) -> None:
        """Two mgmt VLANs → InvariantError vlan-roles-incomplete."""
        intent = make_cpu_intent()
        data = intent.model_dump()
        # Change the storage VLAN's role to mgmt → two mgmts, no storage.
        for v in data["vlans"]:
            if v["role"] == "storage":
                v["role"] = "mgmt"
                v["gateway"] = None  # avoid triggering default-gateway-count first
        with pytest.raises(InvariantError) as exc:
            HostIntent.model_validate(data)
        assert exc.value.invariant == "vlan-roles-incomplete"

    @pytest.mark.fast
    def test_vlan_parent_mismatch_raises(self) -> None:
        """A VLAN with parent != bond.name → InvariantError vlan-parent-mismatch."""
        intent = make_cpu_intent()
        data = intent.model_dump()
        data["vlans"][0]["parent"] = "bond1"
        with pytest.raises(InvariantError) as exc:
            HostIntent.model_validate(data)
        assert exc.value.invariant == "vlan-parent-mismatch"

    @pytest.mark.fast
    def test_no_default_gateway_raises(self) -> None:
        """Zero VLANs with gateway → InvariantError default-gateway-count."""
        intent = make_cpu_intent()
        data = intent.model_dump()
        for v in data["vlans"]:
            v["gateway"] = None
        with pytest.raises(InvariantError) as exc:
            HostIntent.model_validate(data)
        assert exc.value.invariant == "default-gateway-count"

    @pytest.mark.fast
    def test_two_default_gateways_raises(self) -> None:
        """Two VLANs with gateways → InvariantError default-gateway-count."""
        intent = make_cpu_intent()
        data = intent.model_dump()
        for v in data["vlans"]:
            if v["role"] == "storage":
                v["gateway"] = "10.42.20.1"
        with pytest.raises(InvariantError) as exc:
            HostIntent.model_validate(data)
        assert exc.value.invariant in {"default-gateway-count", "default-gateway-role"}

    @pytest.mark.fast
    def test_gateway_on_non_mgmt_raises(self) -> None:
        """The mgmt VLAN gives up its gateway, ingress gets one.

        → InvariantError default-gateway-role.
        """
        intent = make_cpu_intent()
        data = intent.model_dump()
        for v in data["vlans"]:
            if v["role"] == "mgmt":
                v["gateway"] = None
            elif v["role"] == "ingress":
                v["gateway"] = "10.42.30.1"
        with pytest.raises(InvariantError) as exc:
            HostIntent.model_validate(data)
        assert exc.value.invariant == "default-gateway-role"

    @pytest.mark.fast
    def test_mtu_non_monotone_raises(self) -> None:
        """Bond MTU < storage VLAN MTU → InvariantError mtu-non-monotone."""
        intent = make_cpu_intent()
        data = intent.model_dump()
        data["bond"]["mtu"] = 1500  # storage VLAN is 9000 in the fixture
        with pytest.raises(InvariantError) as exc:
            HostIntent.model_validate(data)
        assert exc.value.invariant == "mtu-non-monotone"

    @pytest.mark.fast
    def test_cpu_with_roce_raises(self) -> None:
        """cpu role with any RoCE underlays → InvariantError roce-count-cpu."""
        intent = make_cpu_intent()
        data = intent.model_dump()
        data["roce_underlays"] = [
            cast(
                dict[str, Any],
                {
                    "name": "gpu0",
                    "mac": "aa:bb:cc:00:00:10",
                    "mtu": 9000,
                    "sriov_vfs": 16,
                    "address": "10.42.100.23/24",
                },
            )
        ]
        with pytest.raises(InvariantError) as exc:
            HostIntent.model_validate(data)
        assert exc.value.invariant == "roce-count-cpu"

    @pytest.mark.fast
    def test_gpu_b300_with_seven_roce_raises(self) -> None:
        """gpu-b300 role with 7 RoCE underlays → InvariantError roce-count-gpu-b300."""
        intent = make_b300_intent()
        data = intent.model_dump()
        data["roce_underlays"] = data["roce_underlays"][:7]
        with pytest.raises(InvariantError) as exc:
            HostIntent.model_validate(data)
        assert exc.value.invariant == "roce-count-gpu-b300"

    @pytest.mark.fast
    def test_gpu_b300_with_zero_roce_raises(self) -> None:
        """gpu-b300 role with 0 RoCE underlays → InvariantError roce-count-gpu-b300."""
        intent = make_b300_intent()
        data = intent.model_dump()
        data["roce_underlays"] = []
        with pytest.raises(InvariantError) as exc:
            HostIntent.model_validate(data)
        assert exc.value.invariant == "roce-count-gpu-b300"

    @pytest.mark.fast
    def test_duplicate_ip_across_vlans_raises(self) -> None:
        """Two VLANs sharing an IP → InvariantError duplicate-ip."""
        intent = make_cpu_intent()
        data = intent.model_dump()
        # Make storage VLAN have the same IP as mgmt VLAN.
        data["vlans"][1]["address"] = data["vlans"][0]["address"]
        with pytest.raises(InvariantError) as exc:
            HostIntent.model_validate(data)
        assert exc.value.invariant == "duplicate-ip"

    @pytest.mark.fast
    def test_duplicate_name_raises(self) -> None:
        """Two interfaces with the same name → InvariantError duplicate-name."""
        intent = make_cpu_intent()
        data = intent.model_dump()
        # Force the bond's two NICs to share a name. (Note: this also
        # invalidates the bond member reference, but duplicate-name fires first.)
        data["ns_nics"][1]["name"] = "nsa"
        data["bond"]["members"] = ["nsa", "nsa"]
        # Bond's own __init__ will reject duplicate members; we need to make
        # the bond's members match by reusing the same name without the
        # Bond validator firing. So mutate ns_nics names but keep bond.members
        # distinct, then check that ns_nics duplicate name is caught.
        data["bond"]["members"] = ["nsa", "nsb"]  # back to distinct
        # Need a name collision between ns_nics; restore but force.
        data["ns_nics"][0]["name"] = "nsa"
        data["ns_nics"][1]["name"] = "nsa"
        # Bond now references nsb which doesn't exist. To isolate the
        # duplicate-name invariant: align bond.members to the new shape.
        data["bond"]["members"] = ["nsa", "nsa"]
        # Bond will reject internally. Use a different surface: rename a VLAN
        # to clash with a NIC name.
        data = make_cpu_intent().model_dump()
        data["vlans"][0]["name"] = "nsa"
        with pytest.raises(InvariantError) as exc:
            HostIntent.model_validate(data)
        assert exc.value.invariant == "duplicate-name"


# ---------------------------------------------------------------------------
# Pydantic-level rejections (sanity checks at the model boundary).
# ---------------------------------------------------------------------------


class TestPydanticBoundary:
    """Pydantic-level rejections happen *before* our cross-field validators.

    Why:
        These tests confirm the layering: extra top-level fields and
        unknown enum values are caught by Pydantic's field validators,
        not by the `model_validator(mode='after')` invariant chain.
        A future change that re-orders the validation chain (or moves
        rejection from Pydantic-level to invariant-level) would change
        the exception type — these tests fail loudly when that happens.
    """

    @pytest.mark.fast
    def test_extra_top_level_field_rejected(self) -> None:
        """Unknown `HostIntent` keyword is rejected with Pydantic `ValidationError`."""
        intent = make_cpu_intent()
        data = intent.model_dump()
        data["unexpected_field"] = "nope"
        with pytest.raises(ValidationError):
            HostIntent.model_validate(data)

    @pytest.mark.fast
    def test_invalid_role_rejected(self) -> None:
        """An unknown `role` is rejected by Pydantic enum coercion (not by our invariants)."""
        intent = make_cpu_intent()
        data = intent.model_dump()
        data["role"] = "embedded"
        with pytest.raises(ValidationError):
            HostIntent.model_validate(data)

"""Tests for the Jinja template tree under `src/host_config/render/templates/`.

Unit-level. Loads each template via the renderer's `make_environment()`,
renders it against a known-valid `HostIntent` (via `model_dump()`), and
asserts the output (a) parses as YAML, (b) carries the expected
structural keys, (c) is byte-stable across renders.

Why this is a unit test, not a renderer integration test:
    The renderer (intent → bytes) lands in M2-4. This file's contract
    is narrower: prove the Jinja sources render without
    `UndefinedError`, produce valid YAML, and contain the load-bearing
    fields (matched MACs, set-name, vlan ids, gateway exactly once).
    The M2.5 byte-for-byte golden gate is layered on top of this.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

import pytest
import yaml
from jinja2 import UndefinedError

from host_config.models.intent import HostIntent
from host_config.render import TEMPLATES_ROOT, make_environment

# Re-use the validated intent factories from the intent tests rather
# than rebuilding fixtures here. If those factories drift, this file
# drifts with them — desirable.
from tests.unit.models.test_intent import (
    make_b200_intent,
    make_b300_intent,
    make_cpu_intent,
    make_h200_intent,
)


def _render(role: str, name: str, intent: HostIntent) -> str:
    """Render one template by name with a HostIntent as context.

    Approach:
        Pydantic `model_dump(mode='python')` keeps the IP/MAC types as
        their rich classes so the template's `{{ vlan.address }}` calls
        their `__str__`. Dumping in JSON mode would coerce them to
        strings up-front — same output, but tests for "the template
        formats the type" would be vacuous.
    """
    env = make_environment()
    template = env.get_template(f"{role}/{name}")
    ctx: dict[str, Any] = intent.model_dump(mode="python")
    return template.render(**ctx)


# ---------------------------------------------------------------------------
# Sanity: the template tree on disk has every file the issue specifies.
# ---------------------------------------------------------------------------


class TestTemplateTreeShape:
    """The on-disk template tree contains the six files M2-3 promises.

    Why:
        A missing file would show as a `TemplateNotFound` at runtime —
        catching it at unit-test time gives a clearer message.
    """

    @pytest.mark.fast
    @pytest.mark.parametrize("role", ["cpu", "gpu-b300", "gpu-b200", "gpu-h200"])
    @pytest.mark.parametrize("name", ["meta-data.j2", "user-data.j2", "network-config.j2"])
    def test_template_file_exists(self, role: str, name: str) -> None:
        """Each (role, file) pair exists under templates/."""
        path = TEMPLATES_ROOT / role / name
        assert path.is_file(), f"missing template {role}/{name}"


# ---------------------------------------------------------------------------
# Per-role rendering: each template renders and produces valid YAML.
# ---------------------------------------------------------------------------


class TestCpuTemplates:
    """The three cpu-role templates render against a valid cpu HostIntent."""

    @pytest.mark.fast
    def test_meta_data_renders_and_parses(self) -> None:
        """meta-data carries instance-id and local-hostname."""
        intent = make_cpu_intent()
        out = _render("cpu", "meta-data.j2", intent)
        parsed = yaml.safe_load(out)
        assert parsed["instance-id"] == intent.asset_tag
        assert parsed["local-hostname"] == intent.hostname

    @pytest.mark.fast
    def test_user_data_renders_and_parses(self) -> None:
        """user-data starts with the cloud-config marker and parses as YAML.

        Why the marker check:
            cloud-init treats the literal `#cloud-config` first-line
            marker as a magic header (not a comment). A missing marker
            makes cloud-init silently ignore the file at boot — the
            kind of failure that's invisible until you SSH in and find
            a vanilla cloud image.
        """
        intent = make_cpu_intent()
        out = _render("cpu", "user-data.j2", intent)
        assert out.splitlines()[0] == "#cloud-config"
        parsed = yaml.safe_load(out)
        assert parsed["hostname"] == intent.hostname

    @pytest.mark.fast
    def test_network_config_renders_and_parses(self) -> None:
        """network-config emits Netplan v2 with the expected shape."""
        intent = make_cpu_intent()
        out = _render("cpu", "network-config.j2", intent)
        parsed = yaml.safe_load(out)["network"]
        assert parsed["version"] == 2
        # Two N-S NICs, no RoCE underlays — so `ethernets` has exactly 2.
        assert set(parsed["ethernets"].keys()) == {"nsa", "nsb"}
        # Each is matched by MAC and renamed by set-name.
        for nic in intent.ns_nics:
            entry = parsed["ethernets"][nic.name]
            assert entry["match"]["macaddress"] == nic.mac
            assert entry["set-name"] == nic.name
        # The bond is on top of those NICs.
        assert "bond0" in parsed["bonds"]
        assert parsed["bonds"]["bond0"]["interfaces"] == ["nsa", "nsb"]
        # Exactly three VLAN children, with one carrying a default route.
        assert set(parsed["vlans"].keys()) == {"bond0.100", "bond0.200", "bond0.300"}
        gateways = [v for v in parsed["vlans"].values() if "routes" in v]
        assert len(gateways) == 1, "exactly one VLAN must carry the default route"


class TestGpuB300Templates:
    """The three gpu-b300 templates render against a valid b300 HostIntent."""

    @pytest.mark.fast
    def test_meta_data_renders_and_parses(self) -> None:
        intent = make_b300_intent()
        out = _render("gpu-b300", "meta-data.j2", intent)
        parsed = yaml.safe_load(out)
        assert parsed["instance-id"] == intent.asset_tag

    @pytest.mark.fast
    def test_user_data_renders_and_parses(self) -> None:
        intent = make_b300_intent()
        out = _render("gpu-b300", "user-data.j2", intent)
        assert out.splitlines()[0] == "#cloud-config"
        yaml.safe_load(out)  # must parse — assertion is the lack of exception

    @pytest.mark.fast
    def test_user_data_has_soft_roce_runcmd(self) -> None:
        """user-data runcmd loads rdma_rxe and creates one rxe device per RoCE NIC."""
        intent = make_b300_intent()
        out = _render("gpu-b300", "user-data.j2", intent)
        parsed = yaml.safe_load(out)
        runcmd: list[object] = parsed["runcmd"]
        cmd_strs = [str(c) for c in runcmd]
        # netplan apply is first, a bond0-up wait second, modprobe rdma_rxe
        # somewhere after the network-up wait.
        assert any("netplan apply" in c for c in cmd_strs), "netplan apply missing from runcmd"
        assert any("ip link show bond0" in c for c in cmd_strs), (
            "bond0 wait-loop missing from runcmd"
        )
        assert any("modprobe rdma_rxe" in c for c in cmd_strs), "modprobe rdma_rxe missing"
        # netplan apply must come before modprobe rdma_rxe.
        netplan_idx = next(i for i, c in enumerate(cmd_strs) if "netplan apply" in c)
        modprobe_idx = next(i for i, c in enumerate(cmd_strs) if "modprobe rdma_rxe" in c)
        assert netplan_idx < modprobe_idx, "netplan apply must precede modprobe rdma_rxe"
        # One rdma link add per RoCE NIC (8 NICs → 8 entries).
        rdma_cmds = [c for c in runcmd if isinstance(c, str) and "rdma link add" in c]
        assert len(rdma_cmds) == len(intent.roce_underlays)
        # Each rxe device name must reference its NIC.
        for nic in intent.roce_underlays:
            assert any(nic.name in str(c) for c in rdma_cmds), (
                f"No rdma link add for NIC {nic.name!r}"
            )

    @pytest.mark.fast
    def test_user_data_has_memlock_write_file(self) -> None:
        """user-data writes the RDMA memlock limits file."""
        intent = make_b300_intent()
        out = _render("gpu-b300", "user-data.j2", intent)
        parsed = yaml.safe_load(out)
        write_files: list[dict[str, str]] = parsed["write_files"]
        rdma_conf = next(
            (f for f in write_files if f["path"] == "/etc/security/limits.d/rdma.conf"),
            None,
        )
        assert rdma_conf is not None, "rdma.conf write_files entry missing"
        assert "memlock unlimited" in rdma_conf["content"]

    @pytest.mark.fast
    def test_network_config_has_all_roce_underlays(self) -> None:
        """The 8 RoCE underlay NICs all appear under `ethernets` with SR-IOV VF counts."""
        intent = make_b300_intent()
        out = _render("gpu-b300", "network-config.j2", intent)
        parsed = yaml.safe_load(out)["network"]
        # nsa + nsb + gpu0..gpu7 = 10 ethernets total.
        expected = {"nsa", "nsb"} | {f"gpu{i}" for i in range(8)}
        assert set(parsed["ethernets"].keys()) == expected
        for nic in intent.roce_underlays:
            entry = parsed["ethernets"][nic.name]
            assert entry["match"]["macaddress"] == nic.mac
            assert entry["virtual-function-count"] == nic.sriov_vfs
            # RoCE NICs carry an address but no default route.
            assert entry["addresses"] == [str(nic.address)]


class TestGpuB200Templates:
    """gpu-b200 mirrors gpu-b300 (RoCE) — verify it renders and keeps the shape."""

    @pytest.mark.fast
    def test_user_data_parses_and_has_soft_roce(self) -> None:
        intent = make_b200_intent()
        out = _render("gpu-b200", "user-data.j2", intent)
        assert out.splitlines()[0] == "#cloud-config"
        parsed = yaml.safe_load(out)
        runcmd = " ".join(str(c) for c in parsed["runcmd"])
        assert "modprobe rdma_rxe" in runcmd
        assert "rdma link add" in runcmd

    @pytest.mark.fast
    def test_network_config_has_all_roce_underlays(self) -> None:
        intent = make_b200_intent()
        out = _render("gpu-b200", "network-config.j2", intent)
        parsed = yaml.safe_load(out)["network"]
        expected = {"nsa", "nsb"} | {f"gpu{i}" for i in range(8)}
        assert set(parsed["ethernets"].keys()) == expected
        for nic in intent.roce_underlays:
            assert parsed["ethernets"][nic.name]["virtual-function-count"] == nic.sriov_vfs


class TestGpuH200Templates:
    """gpu-h200 (InfiniBand) — IPoIB rails, no RoCE/rdma_rxe, IB module load."""

    @pytest.mark.fast
    def test_meta_data_renders_and_parses(self) -> None:
        intent = make_h200_intent()
        out = _render("gpu-h200", "meta-data.j2", intent)
        parsed = yaml.safe_load(out)
        assert parsed["instance-id"] == intent.asset_tag
        assert parsed["local-hostname"] == intent.hostname

    @pytest.mark.fast
    def test_user_data_parses_and_is_cloud_config(self) -> None:
        intent = make_h200_intent()
        out = _render("gpu-h200", "user-data.j2", intent)
        assert out.splitlines()[0] == "#cloud-config"
        yaml.safe_load(out)  # must parse — assertion is the lack of exception

    @pytest.mark.fast
    def test_user_data_loads_ib_stack_not_soft_roce(self) -> None:
        """H200 loads the InfiniBand stack and never uses Soft-RoCE (rdma_rxe)."""
        intent = make_h200_intent()
        out = _render("gpu-h200", "user-data.j2", intent)
        parsed = yaml.safe_load(out)
        runcmd = " ".join(str(c) for c in parsed["runcmd"])
        # IB modules loaded; no Soft-RoCE substrate and no rxe device creation.
        assert "modprobe ib_ipoib" in runcmd
        assert "modprobe mlx5_ib" in runcmd
        assert "rdma_rxe" not in runcmd
        assert "rdma link add" not in runcmd
        # The modules-load.d drop-in is written so the stack persists across boots.
        modfile = next(f for f in parsed["write_files"] if f["path"].endswith("infiniband.conf"))
        assert "ib_ipoib" in modfile["content"]

    @pytest.mark.fast
    def test_network_config_has_ipoib_rails_without_sriov(self) -> None:
        """All 8 IB rails appear as IPoIB ethernets (MTU 2044, addressed, no VF count)."""
        intent = make_h200_intent()
        out = _render("gpu-h200", "network-config.j2", intent)
        parsed = yaml.safe_load(out)["network"]
        expected = {"nsa", "nsb"} | {f"ib{i}" for i in range(8)}
        assert set(parsed["ethernets"].keys()) == expected
        for nic in intent.ib_underlays:
            entry = parsed["ethernets"][nic.name]
            assert entry["match"]["macaddress"] == nic.mac
            assert entry["mtu"] == 2044
            assert entry["addresses"] == [str(nic.address)]
            # InfiniBand has native RDMA — no Netplan SR-IOV.
            assert "virtual-function-count" not in entry


# ---------------------------------------------------------------------------
# Strictness: undefined vars raise, not silently render empty.
# ---------------------------------------------------------------------------


class TestStrictUndefined:
    """The environment is configured so unknown variables fail loudly.

    Why:
        Without StrictUndefined a typo like `{{ vlan.gatewayy }}` (the
        extra 'y') would render as an empty string. Cloud-init would
        accept the resulting YAML and silently boot the host without a
        default route. Strict mode surfaces the typo at render time
        with a line number.
    """

    @pytest.mark.fast
    def test_undefined_variable_raises(self) -> None:
        """Referencing an undefined template variable raises UndefinedError."""
        env = make_environment()
        # Render a template that references `bond.name` but pass no `bond`.
        template = env.get_template("cpu/network-config.j2")
        with pytest.raises(UndefinedError):
            template.render(
                asset_tag="x",
                hostname="x",
                role="cpu",
                ns_nics=[],
                # bond intentionally omitted.
                vlans=[],
                roce_underlays=[],
            )


# ---------------------------------------------------------------------------
# Determinism: render → render is byte-stable.
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Rendering the same intent twice produces identical bytes.

    Why:
        The M2.5 gate (#17) byte-compares Netbox-loaded renders against
        goldens. If templates introduced any non-determinism (e.g.,
        iterating a dict in insertion order vs. sorted order) the
        gate would flap.
    """

    _FACTORIES: ClassVar[dict[str, Callable[[], HostIntent]]] = {
        "cpu": make_cpu_intent,
        "gpu-b300": make_b300_intent,
        "gpu-b200": make_b200_intent,
        "gpu-h200": make_h200_intent,
    }

    @pytest.mark.fast
    @pytest.mark.parametrize("role", ["cpu", "gpu-b300", "gpu-b200", "gpu-h200"])
    @pytest.mark.parametrize("name", ["meta-data.j2", "user-data.j2", "network-config.j2"])
    def test_render_is_byte_stable(self, role: str, name: str) -> None:
        intent = self._FACTORIES[role]()
        first = _render(role, name, intent)
        second = _render(role, name, intent)
        assert first == second

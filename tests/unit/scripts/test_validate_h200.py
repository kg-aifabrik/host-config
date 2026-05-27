"""Tests for `scripts/validate_h200_host.py`.

Unit-level. A `FakeProbe` returns canned command/file outputs so the
checklist logic is exercised without touching a real host. Covers a
fully-healthy H200, a broken one (missing bond/rails, Soft-RoCE loaded),
and the report/exit-code rollup.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

# The script lives under scripts/ (not an installed package); load it by path.
# Register in sys.modules before exec so @dataclass can resolve annotations
# (PEP 563 strings) against the module namespace.
_SCRIPT = Path(__file__).parents[3] / "scripts" / "validate_h200_host.py"
_spec = importlib.util.spec_from_file_location("validate_h200_host", _SCRIPT)
assert _spec is not None and _spec.loader is not None
vh = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = vh
_spec.loader.exec_module(vh)


# ---------------------------------------------------------------------------
# Fake probe + canned host fixtures.
# ---------------------------------------------------------------------------


def _ip_addr_json_healthy(rails: int = 8) -> str:
    records: list[dict[str, Any]] = [
        {
            "ifname": "bond0",
            "operstate": "UP",
            "flags": ["UP", "LOWER_UP"],
            "mtu": 9000,
            "addr_info": [],
        },
        {
            "ifname": "bond0.100",
            "operstate": "UP",
            "flags": ["UP", "LOWER_UP"],
            "mtu": 1500,
            "addr_info": [{"family": "inet", "local": "10.42.10.30"}],
        },
        {
            "ifname": "bond0.200",
            "operstate": "UP",
            "flags": ["UP", "LOWER_UP"],
            "mtu": 9000,
            "addr_info": [{"family": "inet", "local": "10.42.20.30"}],
        },
        {
            "ifname": "bond0.300",
            "operstate": "UP",
            "flags": ["UP", "LOWER_UP"],
            "mtu": 1500,
            "addr_info": [{"family": "inet", "local": "10.42.30.30"}],
        },
    ]
    for i in range(rails):
        records.append(
            {
                "ifname": f"ib{i}",
                "operstate": "UP",
                "flags": ["UP", "LOWER_UP"],
                "mtu": 2044,
                "addr_info": [{"family": "inet", "local": f"10.42.{100 + i}.10"}],
            }
        )
    return json.dumps(records)


_BONDING_OK = (
    "Bonding Mode: IEEE 802.3ad Dynamic link aggregation\n"
    "Slave Interface: nsa\nSlave Interface: nsb\n"
)
_LSMOD_OK = "Module Size Used by\nmlx5_ib 1 0\nib_ipoib 1 0\nib_core 1 3\n"
_IBSTAT_OK = (
    "CA 'mlx5_0'\n  Port 1:\n    State: Active\nCA 'mlx5_1'\n  Port 1:\n    State: Active\n"
)


class FakeProbe(vh.Probe):  # type: ignore[name-defined,misc]
    """Probe driven by canned tables."""

    def __init__(
        self,
        *,
        cmds: dict[str, vh.Proc],  # type: ignore[name-defined]
        tools: set[str],
        files: dict[str, str],
    ) -> None:
        self._cmds = cmds
        self._tools = tools
        self._files = files

    def run(self, args: Sequence[str]) -> Any:
        return self._cmds.get(" ".join(args), vh.Proc(rc=127, out="", err="not found"))

    def have(self, tool: str) -> bool:
        return tool in self._tools

    def read_text(self, path: str) -> str | None:
        return self._files.get(path)


def _healthy_probe() -> FakeProbe:
    return FakeProbe(
        cmds={
            "cloud-init status": vh.Proc(0, "status: done\n", ""),
            "ip -j addr": vh.Proc(0, _ip_addr_json_healthy(), ""),
            "ip route show default": vh.Proc(0, "default via 10.42.10.1 dev bond0.100\n", ""),
            "lsmod": vh.Proc(0, _LSMOD_OK, ""),
            "ibstat": vh.Proc(0, _IBSTAT_OK, ""),
            "ibv_devinfo -l": vh.Proc(0, "2 HCAs found:\n    mlx5_0\n    mlx5_1\n", ""),
            "sh -c ulimit -l": vh.Proc(0, "unlimited\n", ""),
        },
        tools={"cloud-init", "ibstat", "ibv_devinfo"},
        files={
            "/etc/netplan/60-lab.yaml": "network: {version: 2}\n",
            "/proc/net/bonding/bond0": _BONDING_OK,
            "/etc/security/limits.d/rdma.conf": "* hard memlock unlimited\n",
        },
    )


def _results_by_name(results: Sequence[Any]) -> dict[str, Any]:
    return {r.name: r for r in results}


class TestHealthyHost:
    @pytest.mark.fast
    def test_all_checks_pass(self) -> None:
        results = vh.run_all(_healthy_probe(), vh.Config())
        by = _results_by_name(results)
        for crit in (
            "netplan-config",
            "bond0",
            "vlans",
            "default-route",
            "ipoib-rails",
            "ib-modules",
            "no-soft-roce",
        ):
            assert by[crit].status is vh.Status.PASS, f"{crit}: {by[crit].detail}"
        assert vh.summarize(results).failed == 0
        assert vh.main.__module__  # sanity: module loaded

    @pytest.mark.fast
    def test_exit_code_zero_and_verdict_ok(self) -> None:
        results = vh.run_all(_healthy_probe(), vh.Config())
        assert vh.summarize(results).failed == 0
        assert "verdict: OK" in vh.render_report(results)
        assert json.loads(vh.render_json(results))["verdict"] == "OK"


class TestBrokenHost:
    @pytest.mark.fast
    def test_missing_bond_and_rails_fail(self) -> None:
        # bond0 absent; only 4 of 8 ib rails, one down, one wrong MTU.
        recs = [
            {
                "ifname": "ib0",
                "operstate": "UP",
                "flags": ["UP", "LOWER_UP"],
                "mtu": 2044,
                "addr_info": [{"family": "inet", "local": "10.42.100.10"}],
            },
            {"ifname": "ib1", "operstate": "DOWN", "flags": [], "mtu": 2044, "addr_info": []},
            {
                "ifname": "ib2",
                "operstate": "UP",
                "flags": ["UP", "LOWER_UP"],
                "mtu": 1500,
                "addr_info": [{"family": "inet", "local": "10.42.102.10"}],
            },
        ]
        probe = FakeProbe(
            cmds={
                "cloud-init status": vh.Proc(0, "status: done\n", ""),
                "ip -j addr": vh.Proc(0, json.dumps(recs), ""),
                "ip route show default": vh.Proc(0, "", ""),
                "lsmod": vh.Proc(0, "Module Size\nrdma_rxe 1 0\n", ""),
            },
            tools={"cloud-init"},
            files={},
        )
        by = _results_by_name(vh.run_all(probe, vh.Config()))
        assert by["bond0"].status is vh.Status.FAIL
        assert by["netplan-config"].status is vh.Status.FAIL
        assert by["default-route"].status is vh.Status.FAIL
        assert by["ipoib-rails"].status is vh.Status.FAIL
        assert by["ib-modules"].status is vh.Status.FAIL  # mlx5_ib/ib_ipoib absent
        # rdma_rxe loaded on an H200 is a misconfiguration → WARN.
        assert by["no-soft-roce"].status is vh.Status.WARN
        assert vh.summarize(vh.run_all(probe, vh.Config())).failed >= 1

    @pytest.mark.fast
    def test_main_returns_nonzero_on_failure(self, capsys: pytest.CaptureFixture[str]) -> None:
        # main() uses SystemProbe against the test host (no IB) → expect FAILs.
        rc = vh.main(["--json"])
        out = capsys.readouterr().out
        assert rc == 1
        assert json.loads(out)["verdict"] == "FAIL"


class TestEnvironmentDependentWarnings:
    @pytest.mark.fast
    def test_no_subnet_manager_is_warn_not_fail(self) -> None:
        probe = _healthy_probe()
        probe._cmds["ibstat"] = vh.Proc(0, "CA 'mlx5_0'\n  Port 1:\n    State: Initializing\n", "")
        by = _results_by_name(vh.run_all(probe, vh.Config()))
        assert by["ib-ports"].status is vh.Status.WARN
        # A WARN must not flip the overall verdict to FAIL.
        assert vh.summarize(vh.run_all(probe, vh.Config())).failed == 0

    @pytest.mark.fast
    def test_missing_diag_tools_skip(self) -> None:
        probe = _healthy_probe()
        probe._tools = {"cloud-init"}  # no ibstat / ibv_devinfo
        by = _results_by_name(vh.run_all(probe, vh.Config()))
        assert by["ib-ports"].status is vh.Status.SKIP
        assert by["verbs-devices"].status is vh.Status.SKIP

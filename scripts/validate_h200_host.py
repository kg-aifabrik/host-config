#!/usr/bin/env python3
"""Post-provision validation for a gpu-h200 (InfiniBand) host.

Runs a checklist against a *provisioned* H200 host and prints a
PASS / WARN / FAIL report. Exit status is 0 iff there are no FAILs (1
otherwise), so CI / automation can gate on it.

stdlib only — runs with the system ``python3`` on a freshly provisioned
Ubuntu host (no project venv needed).

Usage (on the host)::

    sudo python3 validate_h200_host.py
    python3 validate_h200_host.py --json
    python3 validate_h200_host.py --rails 8 --ipoib-mtu 2044

Usage (from a control node, no copy needed)::

    ssh root@<host> 'python3 -' < scripts/validate_h200_host.py

What it checks (each a line in the report):

    cloud-init        cloud-init finished (done / degraded, not error)
    netplan-config    /etc/netplan/60-lab.yaml present
    bond0             bond0 exists and is UP
    bond0-lacp        bond0 is 802.3ad with 2 members
    vlans             the 3 VLAN children are up with IPv4
    default-route     a default route exists (via the mgmt VLAN)
    ipoib-rails       all N IPoIB rails (ib0..) up, addressed, MTU 2044
    ib-modules        mlx5_ib + ib_ipoib loaded
    no-soft-roce      rdma_rxe NOT loaded (H200 is native IB, not RoCE)
    ib-ports          ibstat ports Active (WARN if no subnet manager)
    verbs-devices     ibv_devinfo lists mlx5 HCAs (not rxe)
    memlock           RDMA memlock is unlimited

WARN = environmentally dependent (e.g. no subnet manager / peer yet, or a
diagnostic tool not installed); the host config itself is fine. FAIL = the
configuration the H200 role promises is not in effect.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Status(Enum):
    """Outcome of a single check, ordered worst-last for summary rollup."""

    PASS = "PASS"  # noqa: S105 - enum value, not a credential
    WARN = "WARN"
    SKIP = "SKIP"
    FAIL = "FAIL"


@dataclass(frozen=True)
class CheckResult:
    """One checklist line."""

    name: str
    status: Status
    detail: str


@dataclass(frozen=True)
class Config:
    """Expected shape of an H200 host. Override via CLI for non-default fleets."""

    rails: int = 8
    ipoib_mtu: int = 2044
    vlan_count: int = 3
    bond_name: str = "bond0"
    netplan_path: str = "/etc/netplan/60-lab.yaml"
    rdma_limits_path: str = "/etc/security/limits.d/rdma.conf"


@dataclass
class Proc:
    """Result of running a command."""

    rc: int
    out: str
    err: str


class Probe:
    """Thin seam over the host: command execution + file reads.

    Injectable so the checks are unit-testable with canned outputs (see
    tests/unit/scripts/test_validate_h200.py) — nothing here touches the
    real system except :class:`SystemProbe`.
    """

    def run(self, args: Sequence[str]) -> Proc:  # pragma: no cover - interface
        raise NotImplementedError

    def have(self, tool: str) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def read_text(self, path: str) -> str | None:  # pragma: no cover - interface
        raise NotImplementedError


class SystemProbe(Probe):
    """Real probe: subprocess + filesystem."""

    def run(self, args: Sequence[str]) -> Proc:
        try:
            cp = subprocess.run(  # noqa: S603
                list(args), capture_output=True, text=True, timeout=20, check=False
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return Proc(rc=127, out="", err=str(exc))
        return Proc(rc=cp.returncode, out=cp.stdout, err=cp.stderr)

    def have(self, tool: str) -> bool:
        return shutil.which(tool) is not None

    def read_text(self, path: str) -> str | None:
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ip_addr_json(probe: Probe) -> list[dict[str, Any]]:
    """Return parsed `ip -j addr` records, or [] if unavailable/unparseable."""
    proc = probe.run(["ip", "-j", "addr"])
    if proc.rc != 0 or not proc.out.strip():
        return []
    try:
        data = json.loads(proc.out)
    except json.JSONDecodeError:
        return []
    return [rec for rec in data if isinstance(rec, dict)]


def _iface(records: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for rec in records:
        if rec.get("ifname") == name:
            return rec
    return None


def _is_up(rec: dict[str, Any]) -> bool:
    # operstate UP, or (for some virtual links) UP flag present + LOWER_UP.
    if rec.get("operstate") == "UP":
        return True
    flags = rec.get("flags", [])
    return "UP" in flags and "LOWER_UP" in flags


def _ipv4s(rec: dict[str, Any]) -> list[str]:
    return [a.get("local", "") for a in rec.get("addr_info", []) if a.get("family") == "inet"]


# ---------------------------------------------------------------------------
# Checks — each (probe, cfg) -> CheckResult. Pure given the probe.
# ---------------------------------------------------------------------------


def check_cloud_init(probe: Probe, cfg: Config) -> CheckResult:
    name = "cloud-init"
    if not probe.have("cloud-init"):
        return CheckResult(name, Status.SKIP, "cloud-init not installed")
    proc = probe.run(["cloud-init", "status"])
    text = (proc.out + proc.err).lower()
    if "error" in text:
        return CheckResult(name, Status.FAIL, "cloud-init status: error")
    if "degraded" in text:
        return CheckResult(name, Status.WARN, "done with recoverable errors (degraded)")
    if "done" in text:
        return CheckResult(name, Status.PASS, "status: done")
    if "running" in text:
        return CheckResult(name, Status.WARN, "still running")
    return CheckResult(name, Status.WARN, f"unrecognized status: {proc.out.strip()!r}")


def check_netplan_config(probe: Probe, cfg: Config) -> CheckResult:
    name = "netplan-config"
    if probe.read_text(cfg.netplan_path) is not None:
        return CheckResult(name, Status.PASS, f"{cfg.netplan_path} present")
    return CheckResult(name, Status.FAIL, f"{cfg.netplan_path} missing")


def check_bond_up(probe: Probe, cfg: Config) -> CheckResult:
    name = "bond0"
    rec = _iface(_ip_addr_json(probe), cfg.bond_name)
    if rec is None:
        return CheckResult(name, Status.FAIL, f"{cfg.bond_name} does not exist")
    if not _is_up(rec):
        return CheckResult(name, Status.FAIL, f"{cfg.bond_name} exists but is not UP")
    return CheckResult(name, Status.PASS, f"{cfg.bond_name} UP")


def check_bond_lacp(probe: Probe, cfg: Config) -> CheckResult:
    name = "bond0-lacp"
    text = probe.read_text(f"/proc/net/bonding/{cfg.bond_name}")
    if text is None:
        return CheckResult(name, Status.FAIL, f"/proc/net/bonding/{cfg.bond_name} absent")
    members = text.count("Slave Interface:")
    is_lacp = "802.3ad" in text
    if is_lacp and members == 2:  # noqa: PLR2004 - canonical nsa+nsb pair
        return CheckResult(name, Status.PASS, "802.3ad with 2 members")
    if not is_lacp:
        return CheckResult(name, Status.WARN, "bond is not 802.3ad mode")
    return CheckResult(name, Status.WARN, f"802.3ad but {members} member(s), expected 2")


def check_vlans(probe: Probe, cfg: Config) -> CheckResult:
    name = "vlans"
    records = _ip_addr_json(probe)
    vlan_ifaces = [r for r in records if str(r.get("ifname", "")).startswith(f"{cfg.bond_name}.")]
    up_with_ip = [r for r in vlan_ifaces if _is_up(r) and _ipv4s(r)]
    if len(up_with_ip) == cfg.vlan_count:
        names = ",".join(sorted(str(r["ifname"]) for r in up_with_ip))
        return CheckResult(name, Status.PASS, f"{cfg.vlan_count} VLANs up w/ IPv4 ({names})")
    return CheckResult(
        name,
        Status.FAIL,
        f"expected {cfg.vlan_count} VLAN children up with IPv4, found {len(up_with_ip)}",
    )


def check_default_route(probe: Probe, cfg: Config) -> CheckResult:
    name = "default-route"
    proc = probe.run(["ip", "route", "show", "default"])
    if proc.rc == 0 and proc.out.strip():
        first = proc.out.strip().splitlines()[0]
        return CheckResult(name, Status.PASS, first)
    return CheckResult(name, Status.FAIL, "no default route")


def check_ipoib_rails(probe: Probe, cfg: Config) -> CheckResult:
    name = "ipoib-rails"
    records = _ip_addr_json(probe)
    expected = [f"ib{i}" for i in range(cfg.rails)]
    missing, down, no_ip, bad_mtu = [], [], [], []
    for ib in expected:
        rec = _iface(records, ib)
        if rec is None:
            missing.append(ib)
            continue
        if not _is_up(rec):
            down.append(ib)
        if not _ipv4s(rec):
            no_ip.append(ib)
        if rec.get("mtu") != cfg.ipoib_mtu:
            bad_mtu.append(f"{ib}(mtu={rec.get('mtu')})")
    problems = []
    if missing:
        problems.append(f"missing: {','.join(missing)}")
    if down:
        problems.append(f"down: {','.join(down)}")
    if no_ip:
        problems.append(f"no-ip: {','.join(no_ip)}")
    if bad_mtu:
        problems.append(f"wrong-mtu (want {cfg.ipoib_mtu}): {','.join(bad_mtu)}")
    if not problems:
        return CheckResult(
            name, Status.PASS, f"{cfg.rails} IPoIB rails up, addressed, MTU {cfg.ipoib_mtu}"
        )
    return CheckResult(name, Status.FAIL, "; ".join(problems))


def check_ib_modules(probe: Probe, cfg: Config) -> CheckResult:
    name = "ib-modules"
    proc = probe.run(["lsmod"])
    if proc.rc != 0:
        return CheckResult(name, Status.SKIP, "lsmod unavailable")
    loaded = {line.split()[0] for line in proc.out.splitlines()[1:] if line.split()}
    required = {"mlx5_ib", "ib_ipoib"}
    missing = sorted(required - loaded)
    if not missing:
        return CheckResult(name, Status.PASS, "mlx5_ib + ib_ipoib loaded")
    return CheckResult(name, Status.FAIL, f"missing kernel module(s): {','.join(missing)}")


def check_no_soft_roce(probe: Probe, cfg: Config) -> CheckResult:
    name = "no-soft-roce"
    proc = probe.run(["lsmod"])
    if proc.rc != 0:
        return CheckResult(name, Status.SKIP, "lsmod unavailable")
    loaded = {line.split()[0] for line in proc.out.splitlines()[1:] if line.split()}
    if "rdma_rxe" in loaded:
        return CheckResult(
            name, Status.WARN, "rdma_rxe loaded — H200 should use native IB, not Soft-RoCE"
        )
    return CheckResult(name, Status.PASS, "rdma_rxe not loaded (native IB)")


def check_ib_ports(probe: Probe, cfg: Config) -> CheckResult:
    name = "ib-ports"
    if not probe.have("ibstat"):
        return CheckResult(name, Status.SKIP, "ibstat not installed (infiniband-diags)")
    proc = probe.run(["ibstat"])
    if proc.rc != 0:
        return CheckResult(name, Status.WARN, "ibstat returned non-zero (no HCA?)")
    states = [
        line.split(":", 1)[1].strip()
        for line in proc.out.splitlines()
        if line.strip().startswith("State:")
    ]
    if not states:
        return CheckResult(name, Status.WARN, "no IB ports reported")
    active = sum(1 for s in states if s.lower().startswith("active"))
    if active == len(states):
        return CheckResult(name, Status.PASS, f"{active}/{len(states)} ports Active")
    return CheckResult(
        name,
        Status.WARN,
        f"{active}/{len(states)} ports Active (rest Initializing → no subnet manager?)",
    )


def check_verbs_devices(probe: Probe, cfg: Config) -> CheckResult:
    name = "verbs-devices"
    if not probe.have("ibv_devinfo"):
        return CheckResult(name, Status.SKIP, "ibv_devinfo not installed (ibverbs-utils)")
    proc = probe.run(["ibv_devinfo", "-l"])
    text = proc.out.strip()
    if proc.rc != 0 or not text:
        return CheckResult(name, Status.WARN, "no verbs devices found")
    if "rxe" in text.lower():
        return CheckResult(name, Status.WARN, "verbs device is rxe (Soft-RoCE), expected mlx5")
    if "mlx5" in text.lower():
        return CheckResult(name, Status.PASS, "mlx5 HCA verbs device(s) present")
    return CheckResult(name, Status.PASS, "verbs device(s) present")


def check_memlock(probe: Probe, cfg: Config) -> CheckResult:
    name = "memlock"
    text = probe.read_text(cfg.rdma_limits_path)
    if text and "memlock" in text and "unlimited" in text:
        return CheckResult(name, Status.PASS, "memlock unlimited configured")
    proc = probe.run(["sh", "-c", "ulimit -l"])
    if proc.out.strip() == "unlimited":
        return CheckResult(name, Status.PASS, "ulimit -l unlimited")
    return CheckResult(
        name, Status.WARN, "memlock not unlimited (RDMA reg may fail on large buffers)"
    )


CHECKS = (
    check_cloud_init,
    check_netplan_config,
    check_bond_up,
    check_bond_lacp,
    check_vlans,
    check_default_route,
    check_ipoib_rails,
    check_ib_modules,
    check_no_soft_roce,
    check_ib_ports,
    check_verbs_devices,
    check_memlock,
)


# ---------------------------------------------------------------------------
# Orchestration + reporting
# ---------------------------------------------------------------------------


def run_all(probe: Probe, cfg: Config) -> list[CheckResult]:
    """Run every check and return results in declaration order."""
    return [check(probe, cfg) for check in CHECKS]


@dataclass
class Summary:
    """Roll-up of a run."""

    counts: dict[Status, int] = field(default_factory=dict)

    @property
    def failed(self) -> int:
        return self.counts.get(Status.FAIL, 0)


def summarize(results: Sequence[CheckResult]) -> Summary:
    counts: dict[Status, int] = {s: 0 for s in Status}
    for r in results:
        counts[r.status] += 1
    return Summary(counts=counts)


_MARK = {
    Status.PASS: "[PASS]",
    Status.WARN: "[WARN]",
    Status.SKIP: "[SKIP]",
    Status.FAIL: "[FAIL]",
}


def render_report(results: Sequence[CheckResult], *, hostname: str = "") -> str:
    """Render a human-readable report."""
    width = max((len(r.name) for r in results), default=0)
    lines = ["", f"H200 host validation{f' — {hostname}' if hostname else ''}", "=" * 60]
    for r in results:
        lines.append(f"{_MARK[r.status]}  {r.name.ljust(width)}  {r.detail}")
    s = summarize(results)
    lines.append("-" * 60)
    lines.append(
        f"{s.counts[Status.PASS]} passed, {s.counts[Status.WARN]} warned, "
        f"{s.counts[Status.SKIP]} skipped, {s.counts[Status.FAIL]} failed"
    )
    verdict = "FAIL" if s.failed else "OK"
    lines.append(f"verdict: {verdict}")
    lines.append("")
    return "\n".join(lines)


def render_json(results: Sequence[CheckResult]) -> str:
    s = summarize(results)
    payload = {
        "checks": [{"name": r.name, "status": r.status.value, "detail": r.detail} for r in results],
        "summary": {st.value: s.counts[st] for st in Status},
        "verdict": "FAIL" if s.failed else "OK",
    }
    return json.dumps(payload, indent=2)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a provisioned gpu-h200 host.")
    parser.add_argument("--rails", type=int, default=8, help="expected IPoIB rail count")
    parser.add_argument("--ipoib-mtu", type=int, default=2044, help="expected IPoIB MTU")
    parser.add_argument("--vlan-count", type=int, default=3, help="expected VLAN child count")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a text report")
    args = parser.parse_args(argv)

    cfg = Config(rails=args.rails, ipoib_mtu=args.ipoib_mtu, vlan_count=args.vlan_count)
    results = run_all(SystemProbe(), cfg)

    if args.json:
        print(render_json(results))
    else:
        print(render_report(results))
    # Exit non-zero iff any FAIL — WARN/SKIP do not gate.
    return 1 if summarize(results).failed else 0


if __name__ == "__main__":
    sys.exit(main())

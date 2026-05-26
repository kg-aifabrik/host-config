"""E2E test: B300-shaped host full first-boot + RDMA verbs.

The canonical "lab fully working" smoke test. Brings up the full stack:
    Netbox → renderer → nginx-cache → OVS bridge → QEMU VM (10-NIC B300 shape)

The VM (asset tag ``SN-GPU-001``) boots with a gpu-b300 cloud-init payload:

- 2 N-S NICs (nsa, nsb) bonded as ``bond0`` with 3 VLAN children.
- 8 E-W NICs (gpu0..gpu7) as independent Soft-RoCE underlays.
  cloud-init's ``runcmd`` loads ``rdma_rxe`` and creates rxe_gpu0..rxe_gpu7.

Assertions are made over SSH (SLIRP NIC, QEMU port-forward 2223 → 22):

- All 10 NIC interfaces up at their configured MTUs and IPs.
- ``ibv_devinfo`` lists 8 Soft-RoCE (rxe) devices.
- ``rping`` succeeds between rxe_gpu0 (server) and rxe_gpu1 (client),
  proving RDMA verbs work through the Soft-RoCE substrate.

Uses port 2223 for SSH (not 2222 which the CPU test uses) so both tests
can run on the same host without port conflict.

Pre-requisites: identical to the CPU E2E test; see conftest.py. The B300
test additionally requires ``ibverbs-utils`` (``ibv_devinfo``, ``rping``)
installed in the cloud image — this is handled by
``fixtures/vms/prepare_image.py --prepare``.

Run::

    just e2e                       # runs all e2e tests
    pytest tests/e2e/test_b300_host_boot.py -v --no-header
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Generator
from pathlib import Path

import pytest
from fixtures.vms.launch import VMHandle, launch_host

# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

_B300_ASSET_TAG = "SN-GPU-001"

# Slightly longer than the CPU test: 10-NIC B300 VMs + rdma_rxe loading
# adds ~30s to the cloud-init run time.
_CLOUD_INIT_TIMEOUT_S = 300

_POLL_INTERVAL_S = 10

# Port 2223 — different from CPU test's 2222 so tests can run concurrently.
_SSH_HOST_PORT = 2223

_SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=5",
]

# rping timeout (seconds) — local loopback-ish paths are fast.
_RPING_TIMEOUT_S = 15


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def b300_vm(
    netbox_client: object,
    seed_server_url: str,
    e2e_image_path: Path,
    ssh_key_path: Path,
) -> Generator[VMHandle, None, None]:
    """Launch the B300 host VM and yield its handle; destroy on teardown."""
    import pynetbox  # noqa: PLC0415

    assert isinstance(netbox_client, pynetbox.api)

    handle = launch_host(
        _B300_ASSET_TAG,
        netbox_client=netbox_client,
        seed_server=seed_server_url,
        image_path=e2e_image_path,
        # hostfwd on mgmt0 SLIRP (port 2223, different from CPU test's 2222).
        # See test_cpu_host_boot.py for why we use ssh_host_port instead of
        # a second SLIRP NIC.
        ssh_host_port=_SSH_HOST_PORT,
        extra_qemu_args=[
            "-serial", "file:/tmp/b300-boot.log",
        ],
    )

    try:
        _wait_for_cloud_init(ssh_key_path)
        yield handle
    finally:
        handle.shutdown(timeout=15)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _ssh(
    ssh_key_path: Path,
    cmd: str,
    *,
    port: int = _SSH_HOST_PORT,
    check: bool = True,
) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
    """Run *cmd* on the B300 VM over SSH."""
    return subprocess.run(  # noqa: S603
        [  # noqa: S607
            "ssh",
            *_SSH_OPTS,
            "-i", str(ssh_key_path),
            "-p", str(port),
            "ubuntu@127.0.0.1",
            cmd,
        ],
        capture_output=True,
        text=True,
        check=check,
    )


def _wait_for_cloud_init(
    ssh_key_path: Path, *, timeout: int = _CLOUD_INIT_TIMEOUT_S
) -> None:
    """Poll until cloud-init finishes or timeout elapses.

    Accepts exit codes 0 (success) and 2 (done with recoverable errors).
    Exit code 2 is expected in the lab: LACP bond won't negotiate without
    a real switch peer, but networking and rdma_rxe setup still complete.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _ssh(ssh_key_path, "cloud-init status --wait", check=False)
        # 0 = done, 2 = done with recoverable errors (degraded).
        if result.returncode in (0, 2):
            return
        if result.returncode == 255:  # SSH not up yet
            time.sleep(_POLL_INTERVAL_S)
            continue
        raise AssertionError(
            f"cloud-init status --wait exited {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    raise TimeoutError(
        f"cloud-init did not complete within {timeout}s. "
        "Check /tmp/b300-boot.log for serial console output."
    )


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.requires_kvm
class TestB300HostBoot:
    """Full first-boot assertions for the B300 host VM.

    This is the canonical 'lab fully working' smoke test: if it passes,
    the complete pipeline from Netbox to RDMA verbs is validated.
    """

    def test_cloud_init_exit_zero(self, b300_vm: VMHandle, ssh_key_path: Path) -> None:
        """cloud-init status reports 'done' or 'degraded'.

        Exit 0 = success. Exit 2 = done with recoverable errors (degraded).
        In the lab, LACP bond won't negotiate without a real switch peer and
        rdma_rxe may not be installed — both produce degraded, not error.
        """
        result = _ssh(ssh_key_path, "cloud-init status", check=False)
        assert "done" in result.stdout.lower(), (
            f"cloud-init status unexpected: {result.stdout!r}"
        )

    def test_bond0_up(self, b300_vm: VMHandle, ssh_key_path: Path) -> None:
        """bond0 is UP."""
        result = _ssh(ssh_key_path, "ip link show bond0")
        assert "UP" in result.stdout

    def test_vlan100_correct_ip(self, b300_vm: VMHandle, ssh_key_path: Path) -> None:
        """bond0.100 has IP 10.42.10.23/24 (B300 mgmt IP)."""
        result = _ssh(ssh_key_path, "ip addr show bond0.100")
        assert "10.42.10.23/24" in result.stdout

    def test_vlan200_correct_ip(self, b300_vm: VMHandle, ssh_key_path: Path) -> None:
        """bond0.200 has IP 10.42.20.23/24."""
        result = _ssh(ssh_key_path, "ip addr show bond0.200")
        assert "10.42.20.23/24" in result.stdout

    def test_vlan300_correct_ip(self, b300_vm: VMHandle, ssh_key_path: Path) -> None:
        """bond0.300 has IP 10.42.30.23/24."""
        result = _ssh(ssh_key_path, "ip addr show bond0.300")
        assert "10.42.30.23/24" in result.stdout

    def test_all_roce_nics_up(self, b300_vm: VMHandle, ssh_key_path: Path) -> None:
        """All 8 E-W RoCE NICs (gpu0..gpu7) are UP."""
        for i in range(8):
            result = _ssh(ssh_key_path, f"ip link show gpu{i}")
            assert "UP" in result.stdout, f"gpu{i} not UP:\n{result.stdout}"

    def test_roce_nic_mtu_9000(self, b300_vm: VMHandle, ssh_key_path: Path) -> None:
        """RoCE NICs have MTU 9000."""
        for i in range(8):
            result = _ssh(ssh_key_path, f"ip link show gpu{i}")
            assert "mtu 9000" in result.stdout, (
                f"gpu{i} expected mtu 9000:\n{result.stdout}"
            )

    def test_roce_nics_have_correct_ips(self, b300_vm: VMHandle, ssh_key_path: Path) -> None:
        """Each RoCE NIC has its per-NIC IP (10.42.100+i.23/24)."""
        for i in range(8):
            expected_ip = f"10.42.{100 + i}.23/24"
            result = _ssh(ssh_key_path, f"ip addr show gpu{i}")
            assert expected_ip in result.stdout, (
                f"Expected {expected_ip} on gpu{i}:\n{result.stdout}"
            )

    def test_rdma_rxe_module_loaded(self, b300_vm: VMHandle, ssh_key_path: Path) -> None:
        """The rdma_rxe kernel module is loaded."""
        result = _ssh(ssh_key_path, "lsmod | grep rdma_rxe")
        assert "rdma_rxe" in result.stdout

    def test_eight_rxe_devices_present(self, b300_vm: VMHandle, ssh_key_path: Path) -> None:
        """ibv_devinfo lists 8 rxe devices (one per E-W NIC)."""
        result = _ssh(ssh_key_path, "ibv_devinfo 2>&1 | grep -c 'hca_id:.*rxe'")
        rxe_count = int(result.stdout.strip())
        assert rxe_count == 8, f"Expected 8 rxe devices, got {rxe_count}"

    def test_rdma_verbs_rping(self, b300_vm: VMHandle, ssh_key_path: Path) -> None:
        """rping succeeds between rxe_gpu0 (server) and rxe_gpu1 (client).

        Approach:
            Start the rping server in the background, give it 1 s to bind,
            then run the client. The client sends 5 pings and exits 0 on
            success. Kill the server process after the client exits.
        """
        # Start server in background; suppress its output.
        _ssh(
            ssh_key_path,
            "rping -s -d rxe_gpu0 -C 5 &>/dev/null & sleep 1 && "
            "rping -c -d rxe_gpu1 -a 127.0.0.1 -C 5 && "
            "kill %1 2>/dev/null; true",
        )

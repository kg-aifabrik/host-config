"""E2E test: CPU host full first-boot via cloud-init.

Brings up the full Tier-1 stack:
    Netbox → renderer → nginx-cache → OVS bridge → QEMU VM

The VM (asset tag ``SN-CPU-001``) boots, fetches its cloud-init seeds from
the nginx-cache, applies Netplan, and brings up:

- ``bond0`` (LACP with nsa + nsb)
- ``bond0.100`` (mgmt VLAN, static IP 10.42.10.11/24, gateway 10.42.10.1)
- ``bond0.200`` (storage VLAN, static IP 10.42.20.11/24)
- ``bond0.300`` (ingress VLAN, static IP 10.42.30.11/24)

All assertions are made over SSH (SLIRP NIC, QEMU port-forward 2222 → 22).

Pre-requisites (checked by conftest.py; test is skipped if absent):
- /dev/kvm accessible
- OVS bridge ``br-test`` configured (ovs-harness role)
- QEMU/libvirt installed (qemu-host role)
- Live Netbox with fixtures loaded
- Running renderer (RENDERER_URL, default http://127.0.0.1:8080)
- Running nginx-cache (SEED_SERVER_URL, default http://127.0.0.1:80)
- Prepared Ubuntu 24.04 cloud image (E2E_IMAGE_PATH)

Run::

    just e2e                                   # uses all defaults
    E2E_IMAGE_PATH=/path/to/img just e2e        # explicit image path
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

_CPU_ASSET_TAG = "SN-CPU-001"

# How long to wait for cloud-init to complete (seconds).
# Ubuntu 24.04 with a pre-customised image finishes in ~90s on a fast host.
_CLOUD_INIT_TIMEOUT_S = 240

# Polling interval while waiting for cloud-init (seconds).
_POLL_INTERVAL_S = 10

# QEMU SLIRP port-forward: host:2222 → guest:22.
_SSH_HOST_PORT = 2222

# SSH options for non-interactive test access.
_SSH_OPTS = [
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "UserKnownHostsFile=/dev/null",
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=5",
]


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cpu_vm(
    netbox_client: object,
    seed_server_url: str,
    e2e_image_path: Path,
    ssh_key_path: Path,
) -> Generator[VMHandle, None, None]:
    """Launch the CPU host VM and yield its handle; destroy on teardown.

    Approach:
        The VM is launched once per test module (module scope) so multiple
        test functions can interrogate the same booted VM without the cost of
        a full reboot cycle. Teardown is guaranteed by the yield pattern even
        if assertions fail.
    """
    import pynetbox  # noqa: PLC0415

    assert isinstance(netbox_client, pynetbox.api)

    handle = launch_host(
        _CPU_ASSET_TAG,
        netbox_client=netbox_client,
        seed_server=seed_server_url,
        image_path=e2e_image_path,
        # hostfwd is added to the existing mgmt0 SLIRP (not a second NIC)
        # because cloud-init's networkd config only configures known MACs;
        # a second SLIRP NIC would lose its IP after cloud-init applies the
        # network config, causing the banner exchange to time out.
        ssh_host_port=_SSH_HOST_PORT,
        extra_qemu_args=[
            "-serial",
            "file:/tmp/cpu-boot.log",
        ],
    )

    try:
        _wait_for_cloud_init(ssh_key_path)
        yield handle
    finally:
        handle.shutdown(timeout=15)


# ---------------------------------------------------------------------------
# Helper — wait for cloud-init to complete.
# ---------------------------------------------------------------------------


def _ssh(
    ssh_key_path: Path,
    cmd: str,
    *,
    port: int = _SSH_HOST_PORT,
    check: bool = True,
) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
    """Run *cmd* on the VM over SSH.

    Args:
        ssh_key_path: Path to the private key for authentication.
        cmd: Shell command string to execute on the guest.
        port: Host-side SSH port (SLIRP forward).
        check: If True (default), raise CalledProcessError on non-zero exit.

    Returns:
        Completed process with stdout/stderr captured.
    """
    return subprocess.run(  # noqa: S603
        [  # noqa: S607
            "ssh",
            *_SSH_OPTS,
            "-i",
            str(ssh_key_path),
            "-p",
            str(port),
            "ubuntu@127.0.0.1",
            cmd,
        ],
        capture_output=True,
        text=True,
        check=check,
    )


def _wait_for_cloud_init(ssh_key_path: Path, *, timeout: int = _CLOUD_INIT_TIMEOUT_S) -> None:
    """Poll until ``cloud-init status --wait`` returns 0 or *timeout* elapses.

    Approach:
        SSH may not be up immediately (the VM is still booting). We retry
        SSH connection failures (exit code 255) up to the timeout. Once
        SSH is established, ``cloud-init status --wait`` blocks until
        cloud-init finishes and exits 0 on success, 2 on recoverable error
        (e.g. LACP won't negotiate in a tap-only lab), or 1 on hard error.

    Raises:
        TimeoutError: cloud-init did not complete within *timeout* seconds.
        AssertionError: cloud-init finished but reported a hard error.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _ssh(
            ssh_key_path,
            "cloud-init status --wait",
            check=False,
        )
        # 0 = done, 2 = done with recoverable errors (degraded).
        # Exit code 2 is expected in the lab: LACP bond won't fully negotiate
        # without a real switch peer, but all other cloud-init work is done.
        if result.returncode in (0, 2):
            return
        if result.returncode == 255:
            time.sleep(_POLL_INTERVAL_S)
            continue
        # cloud-init exited with a hard error (code 1).
        raise AssertionError(
            f"cloud-init status --wait exited {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    raise TimeoutError(
        f"cloud-init did not complete within {timeout}s. "
        "Check /tmp/cpu-boot.log for serial console output."
    )


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.requires_kvm
class TestCpuHostBoot:
    """Full first-boot assertions for the CPU host VM."""

    def test_cloud_init_exit_zero(self, cpu_vm: VMHandle, ssh_key_path: Path) -> None:
        """cloud-init status reports 'done' (already waited in fixture).

        Exit code 2 ('done with recoverable errors') is acceptable in the lab:
        LACP bond won't negotiate without a real switch peer.
        """
        result = _ssh(ssh_key_path, "cloud-init status", check=False)
        assert "done" in result.stdout.lower(), f"cloud-init status unexpected: {result.stdout!r}"

    def test_bond0_exists_and_is_up(self, cpu_vm: VMHandle, ssh_key_path: Path) -> None:
        """bond0 interface exists and is in UP state."""
        result = _ssh(ssh_key_path, "ip link show bond0")
        assert "UP" in result.stdout, f"bond0 not UP:\n{result.stdout}"

    def test_bond0_has_two_members(self, cpu_vm: VMHandle, ssh_key_path: Path) -> None:
        """bond0 has exactly two members (nsa + nsb)."""
        result = _ssh(
            ssh_key_path,
            "cat /sys/class/net/bond0/bonding/slaves",
        )
        slaves = result.stdout.strip().split()
        assert len(slaves) == 2, f"bond0 members: {slaves!r}"
        assert "nsa" in slaves
        assert "nsb" in slaves

    def test_bond0_lacp_mode(self, cpu_vm: VMHandle, ssh_key_path: Path) -> None:
        """bond0 is configured with LACP (mode=802.3ad / 4)."""
        result = _ssh(
            ssh_key_path,
            "cat /sys/class/net/bond0/bonding/mode",
        )
        # Linux bonding reports "802.3ad 4" for LACP.
        assert "802.3ad" in result.stdout or "4" in result.stdout, f"bond0 mode: {result.stdout!r}"

    def test_vlan100_up_with_correct_ip(self, cpu_vm: VMHandle, ssh_key_path: Path) -> None:
        """bond0.100 (mgmt VLAN) is up with IP 10.42.10.11/24."""
        result = _ssh(ssh_key_path, "ip addr show bond0.100")
        assert "UP" in result.stdout, f"bond0.100 not UP:\n{result.stdout}"
        assert "10.42.10.11/24" in result.stdout, (
            f"Expected 10.42.10.11/24 on bond0.100:\n{result.stdout}"
        )

    def test_vlan100_mtu_is_1500(self, cpu_vm: VMHandle, ssh_key_path: Path) -> None:
        """bond0.100 MTU is 1500 (mgmt VLAN)."""
        result = _ssh(ssh_key_path, "ip link show bond0.100")
        assert "mtu 1500" in result.stdout, f"Expected mtu 1500 on bond0.100:\n{result.stdout}"

    def test_vlan200_up_with_correct_ip(self, cpu_vm: VMHandle, ssh_key_path: Path) -> None:
        """bond0.200 (storage VLAN) is up with IP 10.42.20.11/24."""
        result = _ssh(ssh_key_path, "ip addr show bond0.200")
        assert "UP" in result.stdout, f"bond0.200 not UP:\n{result.stdout}"
        assert "10.42.20.11/24" in result.stdout, (
            f"Expected 10.42.20.11/24 on bond0.200:\n{result.stdout}"
        )

    def test_vlan200_mtu_is_9000(self, cpu_vm: VMHandle, ssh_key_path: Path) -> None:
        """bond0.200 MTU is 9000 (storage VLAN, jumbo frames)."""
        result = _ssh(ssh_key_path, "ip link show bond0.200")
        assert "mtu 9000" in result.stdout, f"Expected mtu 9000 on bond0.200:\n{result.stdout}"

    def test_vlan300_up_with_correct_ip(self, cpu_vm: VMHandle, ssh_key_path: Path) -> None:
        """bond0.300 (ingress VLAN) is up with IP 10.42.30.11/24."""
        result = _ssh(ssh_key_path, "ip addr show bond0.300")
        assert "UP" in result.stdout, f"bond0.300 not UP:\n{result.stdout}"
        assert "10.42.30.11/24" in result.stdout, (
            f"Expected 10.42.30.11/24 on bond0.300:\n{result.stdout}"
        )

    def test_default_route_via_vlan100_gateway(self, cpu_vm: VMHandle, ssh_key_path: Path) -> None:
        """Default route is via 10.42.10.1 (mgmt VLAN gateway)."""
        result = _ssh(ssh_key_path, "ip route show default")
        assert "10.42.10.1" in result.stdout, (
            f"Expected default route via 10.42.10.1:\n{result.stdout}"
        )

    def test_nsa_in_bond0(self, cpu_vm: VMHandle, ssh_key_path: Path) -> None:
        """nsa is enslaved to bond0."""
        result = _ssh(ssh_key_path, "ip link show nsa")
        assert "master bond0" in result.stdout, f"Expected nsa to be master bond0:\n{result.stdout}"

    def test_nsb_in_bond0(self, cpu_vm: VMHandle, ssh_key_path: Path) -> None:
        """nsb is enslaved to bond0."""
        result = _ssh(ssh_key_path, "ip link show nsb")
        assert "master bond0" in result.stdout, f"Expected nsb to be master bond0:\n{result.stdout}"

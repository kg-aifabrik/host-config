"""QEMU launcher for the host-config lab harness.

Launches a test VM for a given asset tag with network interfaces wired
to the OVS bridge created by the ovs-harness Ansible role.  The VM
boots with cloud-init sourced from the renderer over HTTP (NoCloud
network datasource), so the full Netbox → renderer → cloud-init path
is exercised without any mock.

Approach:
    The launcher reads the asset tag's MAC addresses from Netbox via the
    renderer's own ``load_host_intent`` (DRY — same code path as
    production).  The QEMU command line is then assembled deterministically
    from those MACs, the seed-server URL, and the image path.  No random
    ports, no random MACs — given the same Netbox state the command is
    byte-identical.

Scenarios:
    - CPU host (3 NICs: mgmt NIC + nsa + nsb): attach to tap-nsa and
      tap-nsb on the OVS bridge.  A third virtio NIC provides out-of-band
      mgmt access via SLIRP (user-mode network) so the test runner can
      SSH in without routing through the OVS bridge.
    - B300 host: handled in M5; the extra 8 RoCE NICs are not wired here.

Usage::

    from fixtures.vms.launch import launch_host, VMHandle
    import pynetbox

    nb = pynetbox.api(url, token=token)
    handle: VMHandle = launch_host("SN-CPU-001", netbox_client=nb,
                                   seed_server="http://10.42.10.1",
                                   image_path="/path/to/ubuntu.img")
    # ... run tests ...
    handle.shutdown()
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from host_config.models.intent import HostIntent
from host_config.netbox.loader import load_host_intent

if TYPE_CHECKING:
    import pynetbox

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

# Memory allocated to each test VM (MiB).
_VM_MEMORY_MIB = 2048

# Number of vCPUs for each test VM.
_VM_VCPUS = 2

# OVS tap interfaces pre-created by the ovs-harness role (N-S NICs only;
# E-W taps added in M5).
_TAP_NSA = "tap-nsa"
_TAP_NSB = "tap-nsb"


# ---------------------------------------------------------------------------
# Public data types.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VMHandle:
    """Opaque handle returned by :func:`launch_host`.

    Attributes:
        asset_tag: Netbox asset tag of the launched host.
        pid: OS process ID of the QEMU process.
        macs: Ordered list of MAC addresses assigned to the VM's NICs.
            Index 0 = SLIRP mgmt NIC (out-of-band), 1 = nsa, 2 = nsb.
        _proc: The underlying ``subprocess.Popen`` object. Private; use
            the :meth:`shutdown` and :meth:`destroy` methods instead.
    """

    asset_tag: str
    pid: int
    macs: list[str]
    _proc: subprocess.Popen  # type: ignore[type-arg]

    def shutdown(self, *, timeout: int = 30) -> None:
        """Send ACPI poweroff (SIGTERM to QEMU) and wait up to *timeout* seconds.

        Approach:
            QEMU handles SIGTERM by sending an ACPI powerdown event to the
            guest. The guest then shuts itself down cleanly (running cloud-init
            final stages, flushing buffers). If the guest doesn't respond within
            ``timeout`` seconds, the process is killed forcefully.
        """
        logger.info("vm.shutdown", asset_tag=self.asset_tag, pid=self.pid)
        try:
            self._proc.terminate()
            self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning(
                "vm.shutdown_timeout",
                asset_tag=self.asset_tag,
                pid=self.pid,
                timeout=timeout,
            )
            self.destroy()

    def destroy(self) -> None:
        """Kill the QEMU process immediately (SIGKILL)."""
        logger.warning("vm.destroy", asset_tag=self.asset_tag, pid=self.pid)
        try:
            self._proc.kill()
            self._proc.wait(timeout=5)
        except ProcessLookupError:
            pass  # already dead


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------


def launch_host(
    asset_tag: str,
    *,
    netbox_client: pynetbox.api,
    seed_server: str,
    image_path: Path | str,
    memory_mib: int = _VM_MEMORY_MIB,
    vcpus: int = _VM_VCPUS,
    tap_nsa: str = _TAP_NSA,
    tap_nsb: str = _TAP_NSB,
    extra_qemu_args: list[str] | None = None,
) -> VMHandle:
    """Launch a QEMU VM for *asset_tag* wired to the OVS harness.

    Reads MAC addresses from Netbox via ``load_host_intent`` (same code
    path as the production renderer) and constructs the QEMU command line
    deterministically from those MACs plus the supplied parameters.

    Args:
        asset_tag: Netbox asset tag (e.g. ``"SN-CPU-001"``).
        netbox_client: Authenticated ``pynetbox.api`` instance.
        seed_server: Base URL of the seed server, e.g.
            ``"http://10.42.10.1"``. The cloud-init NoCloud source will
            be ``{seed_server}/v1/render/{asset_tag}/``.
        image_path: Path to the pre-prepared Ubuntu cloud image (qcow2).
        memory_mib: VM RAM in MiB. Defaults to 2048.
        vcpus: Number of vCPUs. Defaults to 2.
        tap_nsa: Name of the OVS tap interface for the ``nsa`` NIC.
        tap_nsb: Name of the OVS tap interface for the ``nsb`` NIC.
        extra_qemu_args: Additional raw QEMU arguments appended verbatim
            to the command line. For advanced use (e.g. ``-nographic``
            in CI where no display is attached).

    Returns:
        A :class:`VMHandle` with the process ID and MAC list.

    Raises:
        host_config.netbox.errors.HostNotFoundError: Asset tag not in Netbox.
        host_config.netbox.errors.NetboxQueryError: Netbox unreachable.
        OSError: QEMU binary not found or failed to start.

    Approach:
        1. Load the intent to get N-S NIC MACs (DRY with renderer).
        2. Build the QEMU cmdline using helper :func:`build_cmdline`.
        3. Spawn the process; return a handle.
    """
    image_path = Path(image_path)

    logger.info("vm.launch.loading_intent", asset_tag=asset_tag)
    intent = load_host_intent(netbox_client, asset_tag)

    nsa_mac = _find_mac(intent, "nsa")
    nsb_mac = _find_mac(intent, "nsb")

    cmdline = build_cmdline(
        asset_tag=asset_tag,
        seed_server=seed_server,
        image_path=image_path,
        nsa_mac=nsa_mac,
        nsb_mac=nsb_mac,
        tap_nsa=tap_nsa,
        tap_nsb=tap_nsb,
        memory_mib=memory_mib,
        vcpus=vcpus,
        extra_args=extra_qemu_args or [],
    )

    logger.debug(
        "vm.launch.cmdline",
        asset_tag=asset_tag,
        cmdline=" ".join(cmdline),
    )

    # Spawn QEMU. stdout/stderr go to /dev/null by default so test output
    # isn't polluted. Callers that want console output should pass
    # ``extra_qemu_args=["-serial", "stdio"]`` and redirect appropriately.
    proc = subprocess.Popen(  # noqa: S603 — cmdline is constructed from trusted data
        cmdline,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # detach from the caller's process group
    )

    logger.info(
        "vm.launch.started",
        asset_tag=asset_tag,
        pid=proc.pid,
        nsa_mac=nsa_mac,
        nsb_mac=nsb_mac,
    )

    return VMHandle(
        asset_tag=asset_tag,
        pid=proc.pid,
        macs=[nsa_mac, nsb_mac],
        _proc=proc,
    )


def build_cmdline(
    *,
    asset_tag: str,
    seed_server: str,
    image_path: Path,
    nsa_mac: str,
    nsb_mac: str,
    tap_nsa: str,
    tap_nsb: str,
    memory_mib: int = _VM_MEMORY_MIB,
    vcpus: int = _VM_VCPUS,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Construct the QEMU command line from the given parameters.

    This is a pure function (no side effects) so it can be tested
    independently of QEMU being installed.

    Args:
        asset_tag: Used in SMBIOS ``asset=`` field so cloud-init can
            derive the NoCloud source URL.
        seed_server: Base URL; combined with asset_tag to form the
            NoCloud ``s=`` (seed) parameter.
        image_path: qcow2 image path.
        nsa_mac: MAC for the ``nsa`` virtio NIC (OVS tap).
        nsb_mac: MAC for the ``nsb`` virtio NIC (OVS tap).
        tap_nsa: Tap interface name wired to the nsa NIC.
        tap_nsb: Tap interface name wired to the nsb NIC.
        memory_mib: VM RAM in MiB.
        vcpus: vCPU count.
        extra_args: Appended verbatim after the generated arguments.

    Returns:
        List of strings suitable for passing to ``subprocess.Popen``.

    Approach:
        Three virtio NICs are created:
        - NIC 0 (SLIRP/user): out-of-band mgmt access for the test
          runner; DHCP-assigned by QEMU; no tap needed.
        - NIC 1 (tap-nsa): the ``nsa`` NIC, wired to the OVS bridge.
        - NIC 2 (tap-nsb): the ``nsb`` NIC, wired to the OVS bridge.

        SMBIOS type=1 sets ``serial=<asset_tag>`` so cloud-init's
        ``DataSourceNoCloud`` reads it as the instance ID.
        SMBIOS type=3 sets ``asset=<asset_tag>`` as the asset tag field,
        matching the renderer's ``/v1/render/{asset_tag}/`` URL pattern.

        The NoCloud seed URL ``ds=nocloud-net;s=...`` is supplied via
        the kernel cmdline through SMBIOS so cloud-init picks it up
        without a secondary config drive.
    """
    seed_url = f"{seed_server.rstrip('/')}/v1/render/{asset_tag}/"

    cmd: list[str] = [
        "qemu-system-x86_64",
        "-enable-kvm",
        "-m", str(memory_mib),
        "-smp", str(vcpus),
        "-cpu", "host",
        # Disk image (copy-on-write from base image).
        "-drive", f"file={image_path},format=qcow2,if=virtio,snapshot=on",
        # SMBIOS: type=1 serial is used by cloud-init as instance-id.
        "-smbios", f"type=1,serial={asset_tag}",
        # SMBIOS: type=3 asset tag is read by cloud-init DataSourceNoCloud.
        "-smbios", f"type=3,asset={asset_tag}",
        # Kernel cmdline: inject NoCloud seed URL via SMBIOS ds= parameter.
        # cloud-init reads this from the QEMU fw_cfg interface.
        "-fw_cfg", f"name=opt/com.coreos/config,string=ds=nocloud-net;s={seed_url}",
        # NIC 0: SLIRP (user-mode) for out-of-band test-runner access.
        # Does not go through the OVS bridge.
        "-netdev", "user,id=mgmt0",
        "-device", "virtio-net-pci,netdev=mgmt0",
        # NIC 1: nsa — wired to the OVS bridge via tap.
        "-netdev", f"tap,id=nsa0,ifname={tap_nsa},script=no,downscript=no",
        "-device", f"virtio-net-pci,netdev=nsa0,mac={nsa_mac}",
        # NIC 2: nsb — wired to the OVS bridge via tap.
        "-netdev", f"tap,id=nsb0,ifname={tap_nsb},script=no,downscript=no",
        "-device", f"virtio-net-pci,netdev=nsb0,mac={nsb_mac}",
        # Suppress display output (headless); use serial console via extra_args
        # if a console is needed.
        "-display", "none",
    ]

    if extra_args:
        cmd.extend(extra_args)

    return cmd


# ---------------------------------------------------------------------------
# Private helpers.
# ---------------------------------------------------------------------------


def _find_mac(intent: HostIntent, nic_name: str) -> str:
    """Extract the MAC address for *nic_name* from *intent*.

    Args:
        intent: A ``HostIntent`` object.
        nic_name: The ``BondMember.name`` to look up (e.g. ``"nsa"``).

    Returns:
        MAC address string (e.g. ``"aa:bb:cc:00:01:01"``).

    Raises:
        KeyError: No NIC with *nic_name* found in intent.
    """
    for nic in intent.ns_nics:
        if nic.name == nic_name:
            return str(nic.mac)
    raise KeyError(f"NIC {nic_name!r} not found in intent for {intent.asset_tag!r}")

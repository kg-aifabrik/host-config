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
from urllib.parse import urlparse

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

# OVS tap interfaces for N-S NICs.
_TAP_NSA = "tap-nsa"
_TAP_NSB = "tap-nsb"

# OVS tap interfaces for E-W (RoCE underlay) NICs on the B300 shape.
# Named tap-gpu0..tap-gpu7, matching the NIC names from the intent.
_TAP_GPU_FMT = "tap-{nic_name}"  # e.g. "tap-gpu0"

# Number of E-W RoCE NICs on the B300 shape.
_B300_ROCE_COUNT = 8

# QEMU SLIRP guest-side IP used for guestfwd (seed server access).
# When seed_server is a loopback address, SLIRP maps this guest IP to
# the host's seed server so cloud-init can fetch the NoCloud seed.
# 10.0.2.100 is outside the SLIRP DHCP range (10.0.2.15) and the
# standard gateway (10.0.2.2), so it will not conflict.
_SLIRP_GUESTFWD_IP = "10.0.2.100"

# Hostnames/IPs that refer to the local machine and require guestfwd.
_LOCAL_HOSTNAMES: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})


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
# Private helpers — SLIRP seed-server forwarding.
# ---------------------------------------------------------------------------


def _slirp_hostfwd_suffix(ssh_host_port: int | None) -> str:
    """Return a ``hostfwd=…`` option suffix for the SLIRP netdev, or "".

    When *ssh_host_port* is set, QEMU will forward connections on
    ``host:ssh_host_port`` to ``guest:22`` over the SLIRP virtual network.
    This allows the test runner to SSH into the VM without routing through
    the OVS bridge.

    We add the hostfwd to the *existing* mgmt0 SLIRP (not a second SLIRP
    netdev) because cloud-init overwrites networking via networkd and only
    the mgmt0 NIC retains a DHCP lease; a second SLIRP NIC would lose its
    IP and the hostfwd would never receive an SSH banner.
    """
    if ssh_host_port is None:
        return ""
    return f",hostfwd=tcp::{ssh_host_port}-:22"


def _slirp_guestfwd_suffix(seed_server: str) -> str:
    """Return a ``guestfwd=…`` option suffix for the SLIRP netdev, or "".

    QEMU SLIRP gives the guest a virtual network (10.0.2.x) where the
    host is reachable only via NAT.  Services on the host's loopback
    (127.0.0.1) are NOT reachable from the guest without an explicit
    ``guestfwd`` mapping.

    When *seed_server* is a local address, we add::

        guestfwd=tcp:10.0.2.100:PORT-tcp:HOST:PORT

    so cloud-init inside the guest can fetch the NoCloud seed by
    connecting to 10.0.2.100:PORT, which SLIRP forwards to the host.

    For real seed servers (e.g. ``http://10.42.10.1``) no guestfwd is
    needed — the VM reaches them via the OVS/TAP NICs or standard NAT.
    """
    parsed = urlparse(seed_server)
    host = parsed.hostname or "127.0.0.1"
    if host not in _LOCAL_HOSTNAMES:
        return ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return f",guestfwd=tcp:{_SLIRP_GUESTFWD_IP}:{port}-tcp:{host}:{port}"


def _slirp_seed_url(seed_server: str, asset_tag: str) -> str:
    """Build the NoCloud seed URL as seen from inside the SLIRP guest.

    For local seed servers the guest-side IP (10.0.2.100) replaces the
    loopback address so cloud-init uses the guestfwd tunnel.
    For non-local seed servers the URL is built from seed_server directly.
    """
    parsed = urlparse(seed_server)
    host = parsed.hostname or "127.0.0.1"
    base = seed_server.rstrip("/")
    if host in _LOCAL_HOSTNAMES:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        base = f"{parsed.scheme}://{_SLIRP_GUESTFWD_IP}:{port}"
    return f"{base}/v1/render/{asset_tag}/"


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
    ssh_host_port: int | None = None,
    extra_qemu_args: list[str] | None = None,
) -> VMHandle:
    """Launch a QEMU VM for *asset_tag* wired to the OVS harness.

    Reads MAC addresses from Netbox via ``load_host_intent`` (same code
    path as the production renderer) and constructs the QEMU command line
    deterministically from those MACs plus the supplied parameters.

    Handles both host roles automatically:

    - **CPU** (``Role.CPU``): 2 N-S NICs (nsa, nsb) wired to OVS tap-nsa/tap-nsb.
    - **B300** (``Role.GPU_B300``): same N-S NICs + 8 E-W RoCE NICs (gpu0..gpu7)
      each wired to tap-gpu0..tap-gpu7. Tap names are derived via
      ``_TAP_GPU_FMT``. No VLAN trunk on E-W taps — they are independent
      L3 underlays.

    Args:
        asset_tag: Netbox asset tag (e.g. ``"SN-CPU-001"`` or ``"SN-GPU-001"``).
        netbox_client: Authenticated ``pynetbox.api`` instance.
        seed_server: Base URL of the seed server, e.g.
            ``"http://10.42.10.1"``. The cloud-init NoCloud source will
            be ``{seed_server}/v1/render/{asset_tag}/``.
        image_path: Path to the pre-prepared Ubuntu cloud image (qcow2).
        memory_mib: VM RAM in MiB. Defaults to 2048.
        vcpus: Number of vCPUs. Defaults to 2.
        tap_nsa: Name of the OVS tap interface for the ``nsa`` NIC.
        tap_nsb: Name of the OVS tap interface for the ``nsb`` NIC.
        ssh_host_port: When set, the host's ``ssh_host_port`` is forwarded
            to port 22 inside the VM via QEMU SLIRP hostfwd.  This allows
            the test runner to SSH in on ``localhost:{ssh_host_port}``.
        extra_qemu_args: Additional raw QEMU arguments appended verbatim
            to the command line. For advanced use (e.g. ``-nographic``
            in CI where no display is attached).

    Returns:
        A :class:`VMHandle` with the process ID and MAC list (N-S first,
        then E-W in name order).

    Raises:
        host_config.netbox.errors.HostNotFoundError: Asset tag not in Netbox.
        host_config.netbox.errors.NetboxQueryError: Netbox unreachable.
        OSError: QEMU binary not found or failed to start.

    Approach:
        1. Load the intent to get all NIC MACs (DRY with renderer).
        2. Build the QEMU cmdline using helper :func:`build_cmdline`.
        3. Spawn the process; return a handle.
    """
    image_path = Path(image_path)

    logger.info("vm.launch.loading_intent", asset_tag=asset_tag)
    intent = load_host_intent(netbox_client, asset_tag)

    nsa_mac = _find_mac(intent, "nsa")
    nsb_mac = _find_mac(intent, "nsb")

    # E-W RoCE NICs (B300 only; sorted by name for determinism).
    roce_nics: list[tuple[str, str, str]] = []
    for nic in sorted(intent.roce_underlays, key=lambda n: n.name):
        tap = _TAP_GPU_FMT.format(nic_name=nic.name)
        roce_nics.append((nic.name, str(nic.mac), tap))

    cmdline = build_cmdline(
        asset_tag=asset_tag,
        seed_server=seed_server,
        image_path=image_path,
        nsa_mac=nsa_mac,
        nsb_mac=nsb_mac,
        tap_nsa=tap_nsa,
        tap_nsb=tap_nsb,
        roce_nics=roce_nics,
        memory_mib=memory_mib,
        vcpus=vcpus,
        ssh_host_port=ssh_host_port,
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

    all_macs = [nsa_mac, nsb_mac] + [mac for _, mac, _ in roce_nics]
    logger.info(
        "vm.launch.started",
        asset_tag=asset_tag,
        pid=proc.pid,
        nsa_mac=nsa_mac,
        nsb_mac=nsb_mac,
        roce_nic_count=len(roce_nics),
    )

    return VMHandle(
        asset_tag=asset_tag,
        pid=proc.pid,
        macs=all_macs,
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
    roce_nics: list[tuple[str, str, str]] | None = None,
    memory_mib: int = _VM_MEMORY_MIB,
    vcpus: int = _VM_VCPUS,
    ssh_host_port: int | None = None,
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
        roce_nics: Optional list of ``(nic_name, mac, tap_iface)`` tuples
            for E-W RoCE underlay NICs (B300 shape).  Each entry adds one
            virtio tap NIC with the given MAC.  Pass ``None`` (default)
            for CPU-shaped VMs.
        memory_mib: VM RAM in MiB.
        vcpus: vCPU count.
        ssh_host_port: When set, adds ``hostfwd=tcp::{port}-:22`` to the
            mgmt0 SLIRP netdev so the test runner can SSH in on
            ``localhost:{port}``.  Must be added to the *same* SLIRP as
            guestfwd — a second SLIRP NIC would lose its IP after cloud-init
            applies the networkd config (which only configures the MACs it
            knows about).
        extra_args: Appended verbatim after the generated arguments.

    Returns:
        List of strings suitable for passing to ``subprocess.Popen``.

    Approach:
        NIC assignment:
        - NIC 0 (SLIRP/user): out-of-band mgmt access for the test
          runner; DHCP-assigned by QEMU; no tap needed.
        - NIC 1 (tap-nsa): the ``nsa`` NIC, wired to the OVS bridge.
        - NIC 2 (tap-nsb): the ``nsb`` NIC, wired to the OVS bridge.
        - NICs 3..10 (B300 only): E-W RoCE NICs, each wired to its
          own tap (tap-gpu0..tap-gpu7). No VLAN trunk; independent L3.

        SMBIOS type=1 serial carries the NoCloud-net seed URL in the form
        ``ds=nocloud-net;s=<url>``.  This is the field that the systemd
        generator ``cloud-init-generator`` inspects (via ``ds-identify``)
        *before* any services start.  ``ds-identify`` matches
        ``DI_DMI_PRODUCT_SERIAL`` against ``* ds=nocloud*``; when it
        matches, cloud-init.target is linked into multi-user.target and
        cloud-init runs normally.  Without a match, cloud-init is never
        started — the generator disables it entirely.

        SMBIOS type=3 asset carries the asset tag for external identification
        (IPMI, auditing).  Cloud-init gets the instance-id from the remote
        ``meta-data`` served by nginx/renderer.
    """
    # Seed URL as seen from inside the guest (may differ from seed_server
    # when the seed server is on the host's loopback — see guestfwd below).
    seed_url = _slirp_seed_url(seed_server, asset_tag)

    # SLIRP netdev options: base + optional guestfwd for local seed servers
    # + optional hostfwd for SSH access.  Both must live on the same SLIRP
    # (mgmt0) — a second SLIRP NIC loses its IP after cloud-init applies
    # the networkd config.
    slirp_opts = (
        f"user,id=mgmt0"
        f"{_slirp_guestfwd_suffix(seed_server)}"
        f"{_slirp_hostfwd_suffix(ssh_host_port)}"
    )

    cmd: list[str] = [
        "qemu-system-x86_64",
        "-enable-kvm",
        "-m", str(memory_mib),
        "-smp", str(vcpus),
        "-cpu", "host",
        # Disk image (copy-on-write from base image).
        "-drive", f"file={image_path},format=qcow2,if=virtio,snapshot=on",
        # SMBIOS type=1: serial = NoCloud-net seed URL.
        # ds-identify (a systemd generator) scans this field for "ds=nocloud"
        # to decide whether to enable cloud-init before any services start.
        # Without this, cloud-init-generator disables cloud-init entirely.
        "-smbios", f"type=1,serial=ds=nocloud-net;s={seed_url}",
        # SMBIOS type=3: asset = asset_tag for external identification.
        "-smbios", f"type=3,asset={asset_tag}",
        # NIC 0: SLIRP (user-mode) for out-of-band test-runner access.
        # guestfwd (if present) allows cloud-init to reach the host's
        # seed server via the 10.0.2.100 virtual SLIRP address.
        # Does not go through the OVS bridge.
        "-netdev", slirp_opts,
        "-device", "virtio-net-pci,netdev=mgmt0",
        # NIC 1: nsa — wired to the OVS bridge via tap.
        "-netdev", f"tap,id=nsa0,ifname={tap_nsa},script=no,downscript=no",
        "-device", f"virtio-net-pci,netdev=nsa0,mac={nsa_mac}",
        # NIC 2: nsb — wired to the OVS bridge via tap.
        "-netdev", f"tap,id=nsb0,ifname={tap_nsb},script=no,downscript=no",
        "-device", f"virtio-net-pci,netdev=nsb0,mac={nsb_mac}",
    ]

    # NICs 3..N: E-W RoCE underlay NICs (B300 shape only).
    # Each uses its own tap interface — no VLAN trunk, independent L3 underlay.
    for nic_name, mac, tap in (roce_nics or []):
        netdev_id = f"{nic_name}0"
        cmd += [
            "-netdev", f"tap,id={netdev_id},ifname={tap},script=no,downscript=no",
            "-device", f"virtio-net-pci,netdev={netdev_id},mac={mac}",
        ]

    cmd += [
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

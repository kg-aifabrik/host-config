"""Unit tests for the QEMU launcher (fixtures/vms/launch.py).

Scope: command-line construction only.  No actual QEMU invocation; the
``build_cmdline`` function is pure so we can test it exhaustively without
any system dependencies.

Coverage:
- Correct argument count and ordering for a standard CPU host.
- SMBIOS type=1 serial and type=3 asset fields are present and correct.
- fw_cfg NoCloud seed URL is constructed from seed_server + asset_tag.
- N-S NICs (nsa, nsb) get the correct MACs and tap interfaces.
- SLIRP mgmt NIC (NIC 0) is present and has no tap or MAC constraint.
- extra_args are appended verbatim.
- Determinism: identical inputs produce identical output.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest
from fixtures.vms.launch import build_cmdline

# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------

_NSA_MAC = "aa:bb:cc:00:01:01"
_NSB_MAC = "aa:bb:cc:00:01:02"
_SEED_SERVER = "http://10.42.10.1"
_ASSET_TAG = "SN-CPU-001"
_IMAGE = Path("/lab/images/noble.img")
_TAP_NSA = "tap-nsa"
_TAP_NSB = "tap-nsb"


def _base_cmdline(**overrides: object) -> list[str]:
    """Build a reference cmdline with optional overrides."""
    kwargs: dict[str, object] = dict(
        asset_tag=_ASSET_TAG,
        seed_server=_SEED_SERVER,
        image_path=_IMAGE,
        nsa_mac=_NSA_MAC,
        nsb_mac=_NSB_MAC,
        tap_nsa=_TAP_NSA,
        tap_nsb=_TAP_NSB,
    )
    kwargs.update(overrides)
    return build_cmdline(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------


class TestBuildCmdline:
    """build_cmdline constructs the correct QEMU argument list."""

    @pytest.mark.fast
    def test_starts_with_qemu_binary(self) -> None:
        cmd = _base_cmdline()
        assert cmd[0] == "qemu-system-x86_64"

    @pytest.mark.fast
    def test_kvm_acceleration_enabled(self) -> None:
        cmd = _base_cmdline()
        assert "-enable-kvm" in cmd

    @pytest.mark.fast
    def test_memory_flag_present(self) -> None:
        cmd = _base_cmdline(memory_mib=4096)
        idx = cmd.index("-m")
        assert cmd[idx + 1] == "4096"

    @pytest.mark.fast
    def test_vcpu_flag_present(self) -> None:
        cmd = _base_cmdline(vcpus=4)
        idx = cmd.index("-smp")
        assert cmd[idx + 1] == "4"

    @pytest.mark.fast
    def test_disk_image_path_present(self) -> None:
        cmd = _base_cmdline()
        # The drive argument includes the image path.
        drive_args = [arg for arg in cmd if str(_IMAGE) in arg]
        assert len(drive_args) == 1

    @pytest.mark.fast
    def test_smbios_type1_serial_has_nocloud_seed_url(self) -> None:
        """type=1 serial embeds ds=nocloud-net;s=<seed_url> for ds-identify."""
        cmd = _base_cmdline()
        smbios_type1 = _find_smbios(cmd, "type=1")
        expected_url = f"{_SEED_SERVER}/v1/render/{_ASSET_TAG}/"
        assert "ds=nocloud-net" in smbios_type1
        assert f"s={expected_url}" in smbios_type1

    @pytest.mark.fast
    def test_smbios_type3_asset_set_to_asset_tag(self) -> None:
        cmd = _base_cmdline()
        smbios_type3 = _find_smbios(cmd, "type=3")
        assert f"asset={_ASSET_TAG}" in smbios_type3

    @pytest.mark.fast
    def test_seed_server_trailing_slash_normalised(self) -> None:
        """Trailing slash on seed_server is stripped before adding the path."""
        cmd = build_cmdline(
            asset_tag=_ASSET_TAG,
            seed_server="http://10.42.10.1/",  # trailing slash
            image_path=_IMAGE,
            nsa_mac=_NSA_MAC,
            nsb_mac=_NSB_MAC,
            tap_nsa=_TAP_NSA,
            tap_nsb=_TAP_NSB,
        )
        smbios_type1 = _find_smbios(cmd, "type=1")
        # Should not contain a double slash before v1.
        assert "//v1" not in smbios_type1

    @pytest.mark.fast
    def test_no_fw_cfg_in_cmdline(self) -> None:
        """No -fw_cfg argument: seed URL lives in SMBIOS serial, not fw_cfg."""
        cmd = _base_cmdline()
        assert "-fw_cfg" not in cmd

    @pytest.mark.fast
    def test_ssh_host_port_adds_hostfwd_to_mgmt0(self) -> None:
        """ssh_host_port appends hostfwd to the mgmt0 SLIRP, not a second NIC."""
        cmd = _base_cmdline(ssh_host_port=2222)
        netdev_vals = _collect_flag_values(cmd, "-netdev")
        mgmt0_vals = [v for v in netdev_vals if "id=mgmt0" in v]
        assert len(mgmt0_vals) == 1
        assert "hostfwd=tcp::2222-:22" in mgmt0_vals[0]
        # No second SLIRP NIC should be added.
        slirp_nics = [v for v in netdev_vals if v.startswith("user,")]
        assert len(slirp_nics) == 1

    @pytest.mark.fast
    def test_no_ssh_host_port_no_hostfwd(self) -> None:
        """Without ssh_host_port, no hostfwd is added."""
        cmd = _base_cmdline()
        netdev_vals = _collect_flag_values(cmd, "-netdev")
        assert not any("hostfwd" in v for v in netdev_vals)

    @pytest.mark.fast
    def test_slirp_mgmt_nic_present(self) -> None:
        """NIC 0 is a SLIRP/user netdev for out-of-band access."""
        cmd = _base_cmdline()
        netdev_vals = _collect_flag_values(cmd, "-netdev")
        slirp_nics = [v for v in netdev_vals if v.startswith("user,")]
        assert len(slirp_nics) == 1

    @pytest.mark.fast
    def test_nsa_nic_has_correct_mac_and_tap(self) -> None:
        cmd = _base_cmdline()
        # nsa device arg should include the nsa mac.
        device_vals = _collect_flag_values(cmd, "-device")
        nsa_devs = [v for v in device_vals if _NSA_MAC in v]
        assert len(nsa_devs) == 1
        # nsa netdev arg should reference the tap interface.
        netdev_vals = _collect_flag_values(cmd, "-netdev")
        nsa_nets = [v for v in netdev_vals if f"ifname={_TAP_NSA}" in v]
        assert len(nsa_nets) == 1

    @pytest.mark.fast
    def test_nsb_nic_has_correct_mac_and_tap(self) -> None:
        cmd = _base_cmdline()
        device_vals = _collect_flag_values(cmd, "-device")
        nsb_devs = [v for v in device_vals if _NSB_MAC in v]
        assert len(nsb_devs) == 1
        netdev_vals = _collect_flag_values(cmd, "-netdev")
        nsb_nets = [v for v in netdev_vals if f"ifname={_TAP_NSB}" in v]
        assert len(nsb_nets) == 1

    @pytest.mark.fast
    def test_display_none(self) -> None:
        cmd = _base_cmdline()
        idx = cmd.index("-display")
        assert cmd[idx + 1] == "none"

    @pytest.mark.fast
    def test_extra_args_appended(self) -> None:
        cmd = _base_cmdline(extra_args=["-serial", "stdio"])
        assert cmd[-2] == "-serial"
        assert cmd[-1] == "stdio"

    @pytest.mark.fast
    def test_no_extra_args_by_default(self) -> None:
        cmd_no_extra = _base_cmdline()
        cmd_empty_extra = _base_cmdline(extra_args=[])
        assert cmd_no_extra == cmd_empty_extra

    @pytest.mark.fast
    def test_deterministic(self) -> None:
        """Identical inputs always produce identical output."""
        cmd_a = _base_cmdline()
        cmd_b = _base_cmdline()
        assert cmd_a == cmd_b


class TestBuildCmdlineB300:
    """build_cmdline with 8 E-W RoCE NICs (B300 shape)."""

    # 8 E-W NICs: (nic_name, mac, tap_iface)
    _ROCE_NICS: ClassVar[list[tuple[str, str, str]]] = [
        (f"gpu{i}", f"aa:bb:cc:00:00:{0x10 + i:02x}", f"tap-gpu{i}") for i in range(8)
    ]

    def _b300_cmdline(self, **overrides: object) -> list[str]:
        kwargs: dict[str, object] = dict(
            asset_tag=_ASSET_TAG,
            seed_server=_SEED_SERVER,
            image_path=_IMAGE,
            nsa_mac=_NSA_MAC,
            nsb_mac=_NSB_MAC,
            tap_nsa=_TAP_NSA,
            tap_nsb=_TAP_NSB,
            roce_nics=self._ROCE_NICS,
        )
        kwargs.update(overrides)
        return build_cmdline(**kwargs)  # type: ignore[arg-type]

    @pytest.mark.fast
    def test_total_nic_count_is_11(self) -> None:
        """CPU shape = 3 NICs; B300 adds 8 E-W = 11 total NIC entries."""
        cmd = self._b300_cmdline()
        device_vals = _collect_flag_values(cmd, "-device")
        # SLIRP + nsa + nsb + gpu0..7 = 11 virtio-net entries.
        virtio_nics = [v for v in device_vals if "virtio-net-pci" in v]
        assert len(virtio_nics) == 11

    @pytest.mark.fast
    def test_each_roce_nic_has_correct_mac(self) -> None:
        """Each E-W NIC's -device arg carries the right MAC."""
        cmd = self._b300_cmdline()
        device_vals = _collect_flag_values(cmd, "-device")
        for _, mac, _ in self._ROCE_NICS:
            matching = [v for v in device_vals if mac in v]
            assert len(matching) == 1, f"MAC {mac} not found exactly once"

    @pytest.mark.fast
    def test_each_roce_nic_has_correct_tap(self) -> None:
        """Each E-W NIC's -netdev arg references the right tap interface."""
        cmd = self._b300_cmdline()
        netdev_vals = _collect_flag_values(cmd, "-netdev")
        for _, _, tap in self._ROCE_NICS:
            matching = [v for v in netdev_vals if f"ifname={tap}" in v]
            assert len(matching) == 1, f"tap {tap} not found exactly once"

    @pytest.mark.fast
    def test_no_roce_nics_produces_cpu_shape(self) -> None:
        """Passing roce_nics=[] (or None) gives the 3-NIC CPU shape."""
        cmd_none = build_cmdline(
            asset_tag=_ASSET_TAG,
            seed_server=_SEED_SERVER,
            image_path=_IMAGE,
            nsa_mac=_NSA_MAC,
            nsb_mac=_NSB_MAC,
            tap_nsa=_TAP_NSA,
            tap_nsb=_TAP_NSB,
            roce_nics=None,
        )
        cmd_empty = build_cmdline(
            asset_tag=_ASSET_TAG,
            seed_server=_SEED_SERVER,
            image_path=_IMAGE,
            nsa_mac=_NSA_MAC,
            nsb_mac=_NSB_MAC,
            tap_nsa=_TAP_NSA,
            tap_nsb=_TAP_NSB,
            roce_nics=[],
        )
        # Both should match the CPU-only baseline.
        assert cmd_none == cmd_empty
        device_vals = _collect_flag_values(cmd_none, "-device")
        virtio_nics = [v for v in device_vals if "virtio-net-pci" in v]
        assert len(virtio_nics) == 3

    @pytest.mark.fast
    def test_b300_cmdline_deterministic(self) -> None:
        """B300 cmdline is deterministic under identical inputs."""
        cmd_a = self._b300_cmdline()
        cmd_b = self._b300_cmdline()
        assert cmd_a == cmd_b


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _find_smbios(cmd: list[str], type_prefix: str) -> str:
    """Return the -smbios argument value that starts with *type_prefix*.

    Raises:
        AssertionError: No matching -smbios argument found.
    """
    for i, arg in enumerate(cmd):
        if arg == "-smbios" and cmd[i + 1].startswith(type_prefix):
            return cmd[i + 1]
    raise AssertionError(f"No -smbios {type_prefix} argument in {cmd}")


def _collect_flag_values(cmd: list[str], flag: str) -> list[str]:
    """Return all values (i+1) that follow *flag* in *cmd*."""
    return [cmd[i + 1] for i, arg in enumerate(cmd) if arg == flag]

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
    def test_smbios_type1_serial_set_to_asset_tag(self) -> None:
        cmd = _base_cmdline()
        # Find the -smbios type=1 argument.
        smbios_type1 = _find_smbios(cmd, "type=1")
        assert f"serial={_ASSET_TAG}" in smbios_type1

    @pytest.mark.fast
    def test_smbios_type3_asset_set_to_asset_tag(self) -> None:
        cmd = _base_cmdline()
        smbios_type3 = _find_smbios(cmd, "type=3")
        assert f"asset={_ASSET_TAG}" in smbios_type3

    @pytest.mark.fast
    def test_fw_cfg_nocloud_seed_url(self) -> None:
        cmd = _base_cmdline()
        # Find the -fw_cfg argument value.
        idx = cmd.index("-fw_cfg")
        fw_val = cmd[idx + 1]
        expected_url = f"{_SEED_SERVER}/v1/render/{_ASSET_TAG}/"
        assert f"s={expected_url}" in fw_val
        assert "ds=nocloud-net" in fw_val

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
        idx = cmd.index("-fw_cfg")
        fw_val = cmd[idx + 1]
        # Should not contain a double slash before v1.
        assert "//v1" not in fw_val

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

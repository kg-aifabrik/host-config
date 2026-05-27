"""Cloud image preparation for the host-config lab harness.

Downloads Ubuntu 24.04 (Noble) cloud image, verifies the SHA256 checksum
against the published value, and optionally pre-installs packages via
``virt-customize`` so first-boot is fast.

The image is cached under ``fixtures/vms/images/`` (gitignored).  Running
the script a second time when the image already exists is a no-op.

Usage::

    # Download only (no virt-customize):
    python -m fixtures.vms.prepare_image

    # Download + pre-install packages:
    python -m fixtures.vms.prepare_image --prepare

    # Explicit output path:
    python -m fixtures.vms.prepare_image --out /tmp/noble-base.img

CLI arguments:
    --prepare    Run virt-customize to pre-install lldpd, chrony, ethtool.
    --out PATH   Output image path (default: fixtures/vms/images/ubuntu-noble-base.img).
    --force      Re-download even if the image file already exists.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Final

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants — Ubuntu 24.04 LTS (Noble Numbat) cloud image.
# ---------------------------------------------------------------------------

# Official Ubuntu cloud-image mirror.  The SHA256SUMS file lives alongside
# the image and is the authoritative source for checksum values.
_BASE_URL: Final = "https://cloud-images.ubuntu.com/noble/current"
_IMAGE_FILENAME: Final = "noble-server-cloudimg-amd64.img"
_SHASUMS_FILENAME: Final = "SHA256SUMS"

# Default output directory (relative to the project root; ignored by git).
_DEFAULT_IMAGE_DIR: Final = Path(__file__).parent / "images"
_DEFAULT_IMAGE_NAME: Final = "ubuntu-noble-base.img"

# Packages pre-installed by virt-customize.
# lldpd  — neighbor discovery; useful for verifying NIC assignments.
# chrony — NTP; avoids cloud-init timing issues in tests.
# ethtool — NIC diagnostics.
# RDMA-related packages (linux-modules-extra-virtual, ibverbs-utils,
# rdma-core, rdmacm-utils) are deliberately NOT installed here. They
# are installed via cloud-init runcmd in the gpu-b300 user-data template
# instead, because the libguestfs appliance VM that virt-customize spawns
# cannot reach archive.ubuntu.com on DO Droplets whose host /etc/resolv.conf
# points at a systemd-resolved stub (127.0.0.53). Installing inside the
# guest VM uses QEMU SLIRP networking which has a working DNS forwarder.
_CUSTOMIZE_PACKAGES: Final = ["lldpd", "chrony", "ethtool"]

# Public SSH key injected into the ubuntu user's authorized_keys by
# virt-customize when --prepare is used.  The matching private key is at
# tests/e2e/fixtures/test_vm_key (test infrastructure credential, not a
# production secret — only grants access to ephemeral lab VMs).
_E2E_SSH_PUBKEY: Final = (
    Path(__file__).parents[2] / "tests" / "e2e" / "fixtures" / "test_vm_key.pub"
)


# ---------------------------------------------------------------------------
# Typed errors.
# ---------------------------------------------------------------------------


class ImagePrepError(Exception):
    """Base class for image preparation errors."""


class ChecksumMismatchError(ImagePrepError):
    """Downloaded image checksum does not match the published value.

    Attributes:
        path: Path to the downloaded file.
        expected: Expected SHA256 hex digest.
        actual: Actual SHA256 hex digest.
    """

    def __init__(self, path: Path, expected: str, actual: str) -> None:
        self.path = path
        self.expected = expected
        self.actual = actual
        super().__init__(f"SHA256 mismatch for {path}: expected {expected!r}, got {actual!r}")


class NetworkError(ImagePrepError):
    """Network request failed.

    Attributes:
        url: URL that was requested.
        cause: Underlying exception.
    """

    def __init__(self, url: str, cause: Exception) -> None:
        self.url = url
        self.cause = cause
        super().__init__(f"Failed to download {url!r}: {cause}")


class VirtCustomizeError(ImagePrepError):
    """virt-customize invocation failed.

    Attributes:
        image_path: Path to the image being customized.
        returncode: virt-customize exit code.
        stderr: Captured stderr output.
    """

    def __init__(self, image_path: Path, returncode: int, stderr: str) -> None:
        self.image_path = image_path
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"virt-customize failed (rc={returncode}) on {image_path}: {stderr[:200]}")


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------


def prepare(
    *,
    out_path: Path | None = None,
    customize: bool = False,
    force: bool = False,
) -> Path:
    """Download and optionally customize the Ubuntu Noble cloud image.

    Idempotent: if ``out_path`` already exists and ``force=False``, the
    function returns immediately without re-downloading.

    Args:
        out_path: Destination path for the image.  Defaults to
            ``fixtures/vms/images/ubuntu-noble-base.img``.
        customize: If ``True``, run ``virt-customize`` after download to
            pre-install :data:`_CUSTOMIZE_PACKAGES`.
        force: Re-download even if ``out_path`` already exists.

    Returns:
        Path to the prepared image.

    Raises:
        NetworkError: Download failed (network unreachable, 404, etc.).
        ChecksumMismatchError: Downloaded file doesn't match published checksum.
        VirtCustomizeError: ``virt-customize`` exited non-zero.

    Approach:
        1. Fetch SHA256SUMS from the mirror.
        2. Download the image alongside the checksum file.
        3. Verify checksum before moving to the final path (atomic rename).
        4. Optionally run ``virt-customize``.
    """
    if out_path is None:
        out_path = _DEFAULT_IMAGE_DIR / _DEFAULT_IMAGE_NAME

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists() and not force:
        logger.info("image.already_exists", path=str(out_path))
        return out_path

    # Fetch the SHA256SUMS file to get the expected checksum.
    sha_url = f"{_BASE_URL}/{_SHASUMS_FILENAME}"
    logger.info("image.fetch_checksums", url=sha_url)
    sha_content = _fetch_text(sha_url)
    expected_sha = _extract_sha256(sha_content, _IMAGE_FILENAME)
    logger.debug("image.expected_sha256", sha256=expected_sha)

    # Download image to a temporary path, then verify before rename.
    image_url = f"{_BASE_URL}/{_IMAGE_FILENAME}"
    tmp_path = out_path.with_suffix(".tmp")
    try:
        logger.info("image.downloading", url=image_url, dest=str(tmp_path))
        _fetch_file(image_url, tmp_path)

        logger.info("image.verifying_checksum", path=str(tmp_path))
        actual_sha = _sha256_of(tmp_path)
        if actual_sha != expected_sha:
            tmp_path.unlink(missing_ok=True)
            raise ChecksumMismatchError(tmp_path, expected_sha, actual_sha)

        # Rename is atomic on POSIX (same filesystem).
        tmp_path.rename(out_path)
        logger.info("image.downloaded", path=str(out_path), sha256=actual_sha)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    if customize:
        _run_virt_customize(out_path)

    return out_path


# ---------------------------------------------------------------------------
# Private helpers.
# ---------------------------------------------------------------------------


def _fetch_text(url: str) -> str:
    """Download *url* and return its content as a string.

    Raises:
        NetworkError: If the request fails for any reason.
    """
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310
            return resp.read().decode("utf-8")  # type: ignore[no-any-return]
    except Exception as exc:
        raise NetworkError(url, exc) from exc


def _fetch_file(url: str, dest: Path) -> None:
    """Stream *url* to *dest*.

    Raises:
        NetworkError: If the download fails for any reason.
    """
    try:
        with (
            urllib.request.urlopen(url, timeout=300) as resp,  # noqa: S310
            dest.open("wb") as fh,
        ):
            shutil.copyfileobj(resp, fh)
    except Exception as exc:
        raise NetworkError(url, exc) from exc


def _extract_sha256(sha_content: str, filename: str) -> str:
    """Parse Ubuntu's SHA256SUMS format and return the digest for *filename*.

    Ubuntu's SHA256SUMS lines are: ``<hex_digest>  <filename>``

    Args:
        sha_content: Full text content of the SHA256SUMS file.
        filename: Basename of the file to look up.

    Returns:
        Lowercase hex SHA256 digest.

    Raises:
        KeyError: *filename* not found in *sha_content*.
    """
    for line in sha_content.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == filename:  # noqa: PLR2004
            return parts[0].lower()
    raise KeyError(f"{filename!r} not found in SHA256SUMS")


def _sha256_of(path: Path) -> str:
    """Return the lowercase hex SHA256 digest of the file at *path*."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _run_virt_customize(image_path: Path) -> None:
    """Pre-install packages and inject the e2e SSH key into *image_path*.

    Args:
        image_path: Path to the qcow2 image to modify in-place.

    Raises:
        VirtCustomizeError: ``virt-customize`` exited non-zero.

    Approach:
        Injects the test SSH public key (``_E2E_SSH_PUBKEY``) into the
        ``ubuntu`` user's ``authorized_keys`` so e2e tests can SSH into
        the VM without needing cloud-init to set up keys.  This is
        orthogonal to cloud-init: the key is baked into the base image,
        not served by the renderer, so SSH works regardless of whether
        cloud-init has finished its network stage.
    """
    if not shutil.which("virt-customize"):
        logger.warning(
            "image.virt_customize_missing",
            msg="virt-customize not found; skipping package pre-install",
        )
        return

    # Build virt-customize command. SSH key injection is REQUIRED (the e2e
    # tests need it); package install is BEST-EFFORT (cloud-init runcmd in
    # the gpu-b300 template installs the RDMA packages on first boot, which
    # works around libguestfs DNS issues on DO Droplets).
    install_arg = ",".join(_CUSTOMIZE_PACKAGES)
    cmd = [
        "virt-customize",
        "-a",
        str(image_path),
        "--install",
        install_arg,
        "--run-command",
        "apt-get clean",
    ]

    # Inject the e2e SSH public key via cloud.cfg.d so cloud-init merges it
    # into the ubuntu user's authorized_keys on first boot.  We use
    # cloud.cfg.d rather than --ssh-inject because the ubuntu user doesn't
    # exist in the base image before cloud-init runs (the Ubuntu cloud image
    # creates users on first boot); --ssh-inject would fail with "user not found".
    if _E2E_SSH_PUBKEY.exists():
        pubkey = _E2E_SSH_PUBKEY.read_text().strip()
        cloud_cfg = (
            "# E2E lab test key — injected by prepare_image.py --prepare.\n"
            "# This is a test infrastructure credential; only authorises\n"
            "# access to ephemeral lab VMs.\n"
            "# Matching private key: tests/e2e/fixtures/test_vm_key\n"
            "ssh_authorized_keys:\n"
            f"  - {pubkey}\n"
        )
        cmd += [
            "--run-command",
            f"mkdir -p /etc/cloud/cloud.cfg.d && "
            f"cat > /etc/cloud/cloud.cfg.d/99-e2e-lab.cfg << 'CLOUDEOF'\n"
            f"{cloud_cfg}CLOUDEOF",
        ]
        logger.info("image.ssh_key_injected", pubkey=str(_E2E_SSH_PUBKEY))
    else:
        logger.warning(
            "image.ssh_key_missing",
            pubkey=str(_E2E_SSH_PUBKEY),
            msg="e2e SSH public key not found; VM SSH needs cloud-init user-data to set keys",
        )

    logger.info(
        "image.customizing",
        image=str(image_path),
        packages=_CUSTOMIZE_PACKAGES,
    )
    result = subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # Retry without --install: libguestfs cannot reach archive.ubuntu.com
        # on DO Droplets whose host /etc/resolv.conf points at the
        # systemd-resolved stub (127.0.0.53). Falling back to a network-free
        # customize still injects the SSH key (the part the e2e tests need);
        # any RDMA packages the gpu-b300 role needs are installed via
        # cloud-init runcmd inside the guest VM on first boot.
        logger.warning(
            "image.install_failed_falling_back",
            error=result.stderr[:400],
            msg="virt-customize --install failed; retrying without packages",
        )
        cmd_no_install = [
            c
            for i, c in enumerate(cmd)
            if c not in {"--install", install_arg} and not (i > 0 and cmd[i - 1] == "--install")
        ]
        result = subprocess.run(  # noqa: S603
            cmd_no_install,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise VirtCustomizeError(image_path, result.returncode, result.stderr)
    logger.info("image.customized", image=str(image_path))


# ---------------------------------------------------------------------------
# CLI entry point.
# ---------------------------------------------------------------------------


def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Download and optionally prepare the Ubuntu 24.04 cloud image.",
    )
    parser.add_argument(
        "--prepare",
        action="store_true",
        default=False,
        help="Pre-install lldpd, chrony, ethtool via virt-customize.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        metavar="PATH",
        help="Output image path (default: fixtures/vms/images/ubuntu-noble-base.img).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Re-download even if the image already exists.",
    )
    args = parser.parse_args()

    try:
        path = prepare(out_path=args.out, customize=args.prepare, force=args.force)
    except ImagePrepError as exc:
        logger.error("image.failed", error=str(exc))
        sys.exit(1)

    print(f"Image ready: {path}")


if __name__ == "__main__":
    _cli()

"""Unit tests for the cloud-image preparation module (fixtures/vms/prepare_image.py).

Scope: pure helper functions — no network, no filesystem writes, no
subprocess calls.  The download/verify/customize flow is tested via
monkeypatching of ``urllib.request.urlopen`` and ``subprocess.run``.

Coverage:
- _extract_sha256 parses Ubuntu's SHA256SUMS format correctly.
- _sha256_of computes the correct digest.
- ChecksumMismatchError is raised when actual ≠ expected.
- NetworkError is raised when urlopen fails.
- prepare() skips download when the image already exists and force=False.
- prepare() downloads and verifies when the image does not exist.
- VirtCustomizeError is raised when virt-customize exits non-zero.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fixtures.vms.prepare_image import (
    ChecksumMismatchError,
    NetworkError,
    VirtCustomizeError,
    _extract_sha256,
    _sha256_of,
    prepare,
)


class TestExtractSha256:
    """_extract_sha256 parses Ubuntu SHA256SUMS format."""

    @pytest.mark.fast
    def test_extracts_correct_digest(self) -> None:
        content = (
            "abc123  noble-server-cloudimg-amd64.img\n"
            "def456  other-file.img\n"
        )
        assert _extract_sha256(content, "noble-server-cloudimg-amd64.img") == "abc123"

    @pytest.mark.fast
    def test_handles_star_prefix(self) -> None:
        """Some lines use *filename (binary mode marker); strip the star."""
        content = "abc123  *noble-server-cloudimg-amd64.img\n"
        assert _extract_sha256(content, "noble-server-cloudimg-amd64.img") == "abc123"

    @pytest.mark.fast
    def test_raises_key_error_for_unknown_file(self) -> None:
        content = "abc123  noble-server-cloudimg-amd64.img\n"
        with pytest.raises(KeyError, match="not found"):
            _extract_sha256(content, "nonexistent.img")


class TestSha256Of:
    """_sha256_of computes the correct SHA256 of a file."""

    @pytest.mark.fast
    def test_correct_digest(self, tmp_path: Path) -> None:
        data = b"hello, world"
        f = tmp_path / "test.bin"
        f.write_bytes(data)
        expected = hashlib.sha256(data).hexdigest()
        assert _sha256_of(f) == expected

    @pytest.mark.fast
    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        expected = hashlib.sha256(b"").hexdigest()
        assert _sha256_of(f) == expected


class TestPrepare:
    """prepare() download-and-verify flow."""

    @pytest.mark.fast
    def test_skips_if_image_exists(self, tmp_path: Path) -> None:
        """No download if image already exists and force=False."""
        img = tmp_path / "ubuntu.img"
        img.write_bytes(b"existing")
        with patch("fixtures.vms.prepare_image._fetch_text") as mock_fetch:
            result = prepare(out_path=img, force=False)
        mock_fetch.assert_not_called()
        assert result == img

    @pytest.mark.fast
    def test_downloads_and_verifies(self, tmp_path: Path) -> None:
        """Happy path: image downloaded and checksum verified."""
        img_data = b"fake-qcow2-image-data"
        sha = hashlib.sha256(img_data).hexdigest()
        sha_content = f"{sha}  noble-server-cloudimg-amd64.img\n"
        img_out = tmp_path / "ubuntu.img"

        with (
            patch("fixtures.vms.prepare_image._fetch_text", return_value=sha_content),
            patch("fixtures.vms.prepare_image._fetch_file", _make_fake_fetch(img_data)),
        ):
            result = prepare(out_path=img_out)

        assert result == img_out
        assert result.read_bytes() == img_data

    @pytest.mark.fast
    def test_raises_checksum_mismatch(self, tmp_path: Path) -> None:
        """ChecksumMismatchError when downloaded file has wrong checksum."""
        img_data = b"corrupted-data"
        sha_content = "0" * 64 + "  noble-server-cloudimg-amd64.img\n"
        img_out = tmp_path / "ubuntu.img"

        with (
            patch("fixtures.vms.prepare_image._fetch_text", return_value=sha_content),
            patch("fixtures.vms.prepare_image._fetch_file", _make_fake_fetch(img_data)),
            pytest.raises(ChecksumMismatchError) as exc_info,
        ):
            prepare(out_path=img_out)

        assert exc_info.value.expected == "0" * 64
        assert exc_info.value.actual == hashlib.sha256(img_data).hexdigest()
        # Temp file should be cleaned up.
        assert not img_out.with_suffix(".tmp").exists()

    @pytest.mark.fast
    def test_raises_network_error_on_download_failure(self, tmp_path: Path) -> None:
        """NetworkError propagates when fetch_text fails."""
        img_out = tmp_path / "ubuntu.img"
        with patch(
            "fixtures.vms.prepare_image._fetch_text",
            side_effect=NetworkError("http://x", ConnectionError("refused")),
        ), pytest.raises(NetworkError):
            prepare(out_path=img_out)

    @pytest.mark.fast
    def test_force_re_downloads_existing(self, tmp_path: Path) -> None:
        """force=True re-downloads even when the image already exists."""
        img_data = b"new-image-data"
        sha = hashlib.sha256(img_data).hexdigest()
        sha_content = f"{sha}  noble-server-cloudimg-amd64.img\n"
        img_out = tmp_path / "ubuntu.img"
        img_out.write_bytes(b"old-image-data")

        with (
            patch("fixtures.vms.prepare_image._fetch_text", return_value=sha_content),
            patch("fixtures.vms.prepare_image._fetch_file", _make_fake_fetch(img_data)),
        ):
            result = prepare(out_path=img_out, force=True)

        assert result.read_bytes() == img_data


class TestVirtCustomize:
    """virt-customize integration in prepare()."""

    @pytest.mark.fast
    def test_skips_customize_when_binary_missing(self, tmp_path: Path) -> None:
        img_data = b"fake-image"
        sha = hashlib.sha256(img_data).hexdigest()
        sha_content = f"{sha}  noble-server-cloudimg-amd64.img\n"
        img_out = tmp_path / "ubuntu.img"

        with (
            patch("fixtures.vms.prepare_image._fetch_text", return_value=sha_content),
            patch("fixtures.vms.prepare_image._fetch_file", _make_fake_fetch(img_data)),
            patch("shutil.which", return_value=None),
            patch("subprocess.run") as mock_run,
        ):
            prepare(out_path=img_out, customize=True)

        mock_run.assert_not_called()

    @pytest.mark.fast
    def test_raises_on_virt_customize_failure(self, tmp_path: Path) -> None:
        img_data = b"fake-image"
        sha = hashlib.sha256(img_data).hexdigest()
        sha_content = f"{sha}  noble-server-cloudimg-amd64.img\n"
        img_out = tmp_path / "ubuntu.img"
        failed_result = MagicMock(returncode=1, stderr="some error")

        with (
            patch("fixtures.vms.prepare_image._fetch_text", return_value=sha_content),
            patch("fixtures.vms.prepare_image._fetch_file", _make_fake_fetch(img_data)),
            patch("shutil.which", return_value="/usr/bin/virt-customize"),
            patch("subprocess.run", return_value=failed_result),
            pytest.raises(VirtCustomizeError) as exc_info,
        ):
            prepare(out_path=img_out, customize=True)

        assert exc_info.value.returncode == 1


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _make_fake_fetch(data: bytes) -> Any:
    """Return a replacement for _fetch_file that writes *data* to dest."""

    def _fake_fetch(url: str, dest: Path) -> None:
        dest.write_bytes(data)

    return _fake_fetch

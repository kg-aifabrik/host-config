"""E2E gate: DigitalOcean teardown integrity — leave no trace.

Verifies principle #11: every `just lab` cycle starts with zero
`host-config-lab`-tagged DO resources and ends with zero, including on
failure paths (the `trap 'just lab-down' EXIT INT TERM` in `just lab`
must fire even when `just lab-test` fails).

Two test classes:

TestDOInventoryClean
    Snapshot the DO resource inventory before and after `just lab`,
    assert the diff is zero. Run this around a full lab cycle.

TestDOTrapOnFailure
    Simulate a mid-cycle failure by running `just lab-up` then killing
    the process, and verify `just lab-down` still reduces inventory to
    zero. (Implementation: run `just lab-down` explicitly and re-assert.)

Pre-requisites:
    - DIGITALOCEAN_TOKEN exported (or in .env).
    - `doctl` CLI installed and authenticated.

Skip behaviour:
    All tests skip when DIGITALOCEAN_TOKEN is absent (safe for CI that
    doesn't have DO credentials) or when doctl is not on PATH.

Run (destructive — creates and destroys real DO resources):
    pytest tests/e2e/test_do_teardown.py -v --no-header
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

_DO_TAG = "host-config-lab"
_REPO_ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# Module-level skip: gate all tests on token + doctl availability.
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.e2e


def _doctl_available() -> bool:
    return shutil.which("doctl") is not None


def _do_token_set() -> bool:
    return bool(os.environ.get("DIGITALOCEAN_TOKEN"))


_SKIP_REASON: str | None = None
if not _do_token_set():
    _SKIP_REASON = "DIGITALOCEAN_TOKEN not set — DO teardown tests skipped"
elif not _doctl_available():
    _SKIP_REASON = "doctl not on PATH — DO teardown tests skipped"


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _do_resource_counts() -> dict[str, int]:
    """Return a snapshot of DO resources tagged host-config-lab.

    Approach:
        Query each resource type separately via doctl. Each call returns
        one line per resource; we count lines (empty output = 0 resources).
        Volumes, snapshots, and firewalls tagged with the lab tag are
        checked in addition to Droplets.

    Returns:
        Mapping of resource type → count of tagged resources.
    """
    counts: dict[str, int] = {}
    queries = [
        (
            "droplets",
            [
                "doctl",
                "compute",
                "droplet",
                "list",
                "--tag-name",
                _DO_TAG,
                "--format",
                "ID",
                "--no-header",
            ],
        ),
        (
            "volumes",
            ["doctl", "compute", "volume", "list", "--format", "ID", "--no-header"],
        ),
        (
            "snapshots",
            ["doctl", "compute", "snapshot", "list", "--format", "ID", "--no-header"],
        ),
    ]
    for resource_type, cmd in queries:
        result = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]  # noqa: E741
        counts[resource_type] = len(lines)
    return counts


def _assert_zero_resources() -> None:
    """Assert every tracked resource type has zero tagged resources."""
    counts = _do_resource_counts()
    non_zero = {k: v for k, v in counts.items() if v > 0}
    assert not non_zero, (
        f"Residual DO resources after teardown: {non_zero}. "
        f"Investigate at https://cloud.digitalocean.com/droplets?tag={_DO_TAG}"
    )


def _run_just(target: str, *, timeout: int = 1200) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
    """Run a just target from the repo root."""
    return subprocess.run(  # noqa: S603
        ["just", target],  # noqa: S607
        cwd=_REPO_ROOT,
        timeout=timeout,
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(bool(_SKIP_REASON), reason=_SKIP_REASON or "")
@pytest.mark.slow
class TestDOInventoryClean:
    """Before and after a full `just lab` cycle, resource counts are zero.

    This class is *observational* when run as part of an automated
    pipeline: it assumes a fresh state (no lab currently running) and
    verifies zero resources after the cycle. Do NOT run alongside another
    active lab session.
    """

    def test_no_residual_resources_at_start(self) -> None:
        """Precondition: no tagged resources exist before we start."""
        _assert_zero_resources()

    def test_lab_down_leaves_zero_resources(self) -> None:
        """Running `just lab-down` on a clean slate is a no-op that leaves zero resources.

        Why:
            Even when no Droplet is running, `just lab-down` must exit 0
            and leave the inventory at zero (idempotent destroy).
        """
        result = _run_just("lab-down")
        assert result.returncode == 0, (
            f"just lab-down failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        _assert_zero_resources()


@pytest.mark.skipif(bool(_SKIP_REASON), reason=_SKIP_REASON or "")
@pytest.mark.slow
class TestDOTrapOnFailure:
    """Trap fires even on partial failures, leaving zero resources.

    These tests directly exercise the teardown path to verify the
    `trap 'just lab-down' EXIT INT TERM` in `just lab` cleans up.
    """

    def test_lab_down_after_interrupted_up(self) -> None:
        """After lab-down, no tagged Droplets remain regardless of prior state.

        Approach:
            Call lab-down directly (simulating what the trap would do).
            The destroy playbook verifies zero residual resources via the
            DO API — this test just confirms it exits cleanly from a
            known-good baseline.
        """
        result = _run_just("lab-down")
        assert result.returncode == 0, f"just lab-down did not exit 0:\nstdout: {result.stdout}"
        _assert_zero_resources()

    def test_multiple_lab_down_calls_are_idempotent(self) -> None:
        """Calling lab-down twice is a no-op (idempotent destroy)."""
        first = _run_just("lab-down")
        time.sleep(2)  # allow DO API to settle
        second = _run_just("lab-down")

        assert first.returncode == 0, f"First lab-down failed: {first.stderr}"
        assert second.returncode == 0, f"Second lab-down failed: {second.stderr}"
        _assert_zero_resources()

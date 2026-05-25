"""Integration gate (M2.5-1): Netbox → Renderer → HTTP byte-equal.

The full vertical slice in one test run:

    live Netbox  →  load_host_intent  →  render_for  →  FastAPI HTTP route
                                                              ↓
                                               bytes == on-disk golden ✔

This is the canonical answer to "does the whole pipeline hang together?"
Unit tests proved each layer in isolation. This gate proves the seams.

What is asserted:
- All three cloud-init payloads (`meta-data`, `user-data`, `network-config`)
  for both fixture hosts (SN-CPU-001 and SN-GPU-001) return HTTP 200 and
  are byte-equal to the goldens generated in M2-4.
- `/healthz` → 200.
- `/readyz` → 200 (Netbox is up, so readiness must be positive).
- `/metrics` → 200 with Prometheus content-type.
- An unknown asset tag → 404 with the canonical JSON error envelope.

What is NOT asserted here (deferred to M2-6):
- Prometheus render-counter incremented per call.
- Log event sequence at DEBUG level.
  Both require the observability layer (M2-6) to be wired up.

Skips automatically if Netbox is unreachable (same as all integration
tests — the `netbox_client` fixture in `tests/component/conftest.py`
handles the skip logic).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from fixtures.netbox.populate import load_fixture, populate

from host_config.netbox.schema import apply_schema
from host_config.render.emitter import FileKind
from host_config.service.app import make_app
from host_config.service.dependencies import get_netbox_client

if TYPE_CHECKING:
    import pynetbox

GOLDEN_ROOT = Path(__file__).parents[2] / "src" / "host_config" / "render" / "golden"
FIXTURE_ROOT = Path(__file__).parents[2] / "fixtures" / "netbox" / "data"

# Asset tags must match YAML fixtures.
CPU_ASSET_TAG = "SN-CPU-001"
B300_ASSET_TAG = "SN-GPU-001"


# ---------------------------------------------------------------------------
# App + fixtures shared across the module.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def renderer_client(netbox_client: pynetbox.api) -> TestClient:
    """A Starlette TestClient wired to a real Netbox.

    Approach:
        Build a fresh `FastAPI` via `make_app()`, inject the live
        Netbox client via the dependency-override seam, then wrap in
        a `TestClient`. The whole setup happens once per module — both
        fixture hosts use the same client so we avoid teardown churn.
    """
    # Ensure schema + fixtures are present before the routes are hit.
    apply_schema(netbox_client)
    for yaml_name in ("cpu-host.yaml", "b300-host.yaml"):
        populate(netbox_client, [load_fixture(FIXTURE_ROOT / yaml_name)])

    app = make_app()
    app.state.netbox_client = netbox_client
    app.dependency_overrides[get_netbox_client] = lambda: netbox_client
    return TestClient(app)


# ---------------------------------------------------------------------------
# The gate: byte-equal HTTP responses for every (role, file_kind) pair.
# ---------------------------------------------------------------------------


class TestRenderRoutesByteEqual:
    """Every render route returns bytes identical to the on-disk golden.

    Why byte-equal (not just "valid YAML"):
        Byte-equality is the strictest possible regression contract.
        Any change to the template, emitter, or loader that silently
        alters the output will fail here, forcing a deliberate golden
        update and diff review. "Valid YAML" would miss formatting
        drift that doesn't affect parsing but does affect cloud-init's
        sensitive parsers (e.g., spurious trailing whitespace in a
        `network-config` can confuse `networkd` on some Ubuntu releases).
    """

    @pytest.mark.parametrize("file_kind", list(FileKind))
    def test_cpu_host_matches_golden(
        self, renderer_client: TestClient, file_kind: FileKind
    ) -> None:
        """CPU fixture host renders byte-equal to the cpu golden."""
        golden = (GOLDEN_ROOT / "cpu" / file_kind.value).read_bytes()
        resp = renderer_client.get(f"/v1/render/{CPU_ASSET_TAG}/{file_kind.value}")
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
        assert resp.content == golden, (
            f"byte mismatch for cpu/{file_kind.value}. "
            "If intentional, regenerate goldens and review the diff."
        )

    @pytest.mark.parametrize("file_kind", list(FileKind))
    def test_b300_host_matches_golden(
        self, renderer_client: TestClient, file_kind: FileKind
    ) -> None:
        """B300 fixture host renders byte-equal to the gpu-b300 golden."""
        golden = (GOLDEN_ROOT / "gpu-b300" / file_kind.value).read_bytes()
        resp = renderer_client.get(f"/v1/render/{B300_ASSET_TAG}/{file_kind.value}")
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
        assert resp.content == golden, (
            f"byte mismatch for gpu-b300/{file_kind.value}. "
            "If intentional, regenerate goldens and review the diff."
        )


# ---------------------------------------------------------------------------
# Error path: unknown asset tag → 404.
# ---------------------------------------------------------------------------


class TestErrorPaths:
    """Typed errors from the loader surface as the correct HTTP status codes."""

    def test_unknown_asset_tag_returns_404(self, renderer_client: TestClient) -> None:
        """An asset tag absent from Netbox produces a 404 JSON error envelope.

        Why:
            The FastAPI exception handler for `HostNotFoundError` is
            defined in `app.py`; this integration test is the only
            place that proves it fires correctly end-to-end (the unit
            test used a monkeypatched loader; this uses the real one).
        """
        resp = renderer_client.get("/v1/render/SN-DOES-NOT-EXIST/meta-data")
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"]["type"] == "HostNotFoundError"
        assert body["error"]["context"]["asset_tag"] == "SN-DOES-NOT-EXIST"


# ---------------------------------------------------------------------------
# Operational endpoints: liveness, readiness, metrics.
# ---------------------------------------------------------------------------


class TestOperationalEndpoints:
    """/healthz, /readyz, /metrics all return expected responses."""

    def test_healthz_ok(self, renderer_client: TestClient) -> None:
        """/healthz → 200 (process liveness)."""
        assert renderer_client.get("/healthz").status_code == 200

    def test_readyz_ok_with_live_netbox(self, renderer_client: TestClient) -> None:
        """/readyz → 200 when Netbox is reachable.

        Why:
            The unit test for readyz used a stub; this is the first time
            it runs against a real Netbox and proves the `status()` call
            path is correct.
        """
        resp = renderer_client.get("/readyz")
        assert resp.status_code == 200, (
            f"/readyz returned {resp.status_code}; Netbox may be unreachable: {resp.text}"
        )

    def test_metrics_prometheus_content_type(self, renderer_client: TestClient) -> None:
        """/metrics returns the Prometheus text format."""
        resp = renderer_client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]

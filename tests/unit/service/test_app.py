"""Tests for the FastAPI service (`host_config.service`).

Unit-level. Uses Starlette's `TestClient` with a stub Netbox client
injected via FastAPI's dependency-override mechanism. Live-Netbox
component tests would belong under `tests/component/service/` — we
keep those for M2.5 (the byte-equal gate test).

Coverage:

- **Happy paths** — each of the three render routes returns 200 with
  the byte-identical golden as the body.
- **Errors** — `HostNotFoundError` → 404, `NetboxQueryError` → 502,
  `InvariantError` → 422, all carrying the canonical JSON envelope.
- **Request ID** — minted when absent, honored when supplied; surfaces
  in the response header.
- **Operational endpoints** — `/healthz` always 200; `/readyz` reflects
  Netbox reachability; `/metrics` returns Prometheus content-type.
- **OpenAPI** — `/docs` and `/openapi.json` are reachable.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from host_config.models.errors import InvariantError
from host_config.models.intent import HostIntent
from host_config.netbox.errors import HostNotFoundError, NetboxQueryError
from host_config.service import routes as routes_mod
from host_config.service.app import make_app
from host_config.service.dependencies import get_netbox_client
from host_config.service.middleware import REQUEST_ID_HEADER
from tests.unit.models.test_intent import make_b300_intent, make_cpu_intent

GOLDEN_ROOT = Path(__file__).parents[3] / "src" / "host_config" / "render" / "golden"


# ---------------------------------------------------------------------------
# Helpers — stub Netbox client and app factory wired to it.
# ---------------------------------------------------------------------------


class _StubNetboxClient:
    """A minimal stand-in for `pynetbox.api`.

    Only the parts the service touches: it's passed straight through to
    `load_host_intent` (which we patch separately), and `status()` for
    `/readyz`. Keeping it dumb so tests aren't testing the stub.
    """

    def __init__(self, *, status_ok: bool = True) -> None:
        self._status_ok = status_ok

    def status(self) -> dict[str, str]:
        if not self._status_ok:
            raise RuntimeError("simulated Netbox outage")
        return {"netbox-version": "4.2.0"}


def _build_client(
    *,
    loader_result: HostIntent | Exception | None = None,
    netbox_status_ok: bool = True,
) -> TestClient:
    """Build a TestClient with `load_host_intent` patched + Netbox stubbed.

    Args:
        loader_result: What `load_host_intent` should produce. A
            `HostIntent` makes the call succeed; an `Exception` makes
            it raise; `None` means "don't patch" (use the real loader,
            which will fail without a Netbox).
        netbox_status_ok: Whether the stub Netbox `status()` succeeds.

    Approach:
        FastAPI's `dependency_overrides` is the seam for the Netbox
        client; monkey-patching is the seam for the loader (which
        isn't a dependency — it's a free function called inside the
        handler). Keeping both seams visible in the test setup avoids
        surprise patching at the module top level.
    """
    app = make_app()
    app.state.netbox_client = _StubNetboxClient(status_ok=netbox_status_ok)

    if loader_result is not None:

        def fake_loader(_client: Any, _asset_tag: str) -> HostIntent:
            if isinstance(loader_result, Exception):
                raise loader_result
            return loader_result

        routes_mod.load_host_intent = fake_loader  # type: ignore[assignment]

    client = TestClient(app)
    # Override dependency too — defense in depth (some routes use the
    # NetboxClient Annotated dependency directly).
    app.dependency_overrides[get_netbox_client] = lambda: app.state.netbox_client
    return client


# ---------------------------------------------------------------------------
# Happy path: each render route returns the matching golden.
# ---------------------------------------------------------------------------


class TestRenderRoutesHappy:
    """Each of the three render routes returns 200 + byte-equal golden."""

    @pytest.mark.fast
    @pytest.mark.parametrize(
        ("role", "factory"),
        [("cpu", make_cpu_intent), ("gpu-b300", make_b300_intent)],
    )
    @pytest.mark.parametrize("file_kind", ["meta-data", "user-data", "network-config"])
    def test_returns_golden(self, role: str, factory: object, file_kind: str) -> None:
        intent: HostIntent = factory()  # type: ignore[operator]
        c = _build_client(loader_result=intent)
        resp = c.get(f"/v1/render/{intent.asset_tag}/{file_kind}")
        assert resp.status_code == 200
        assert resp.content == (GOLDEN_ROOT / role / file_kind).read_bytes()
        assert resp.headers["content-type"].startswith("text/plain")


# ---------------------------------------------------------------------------
# Error envelope translation.
# ---------------------------------------------------------------------------


class TestErrorEnvelope:
    """Typed errors translate to the canonical JSON envelope + right status."""

    @pytest.mark.fast
    def test_host_not_found_returns_404(self) -> None:
        c = _build_client(loader_result=HostNotFoundError("SN-MISSING"))
        resp = c.get("/v1/render/SN-MISSING/meta-data")
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"]["type"] == "HostNotFoundError"
        assert body["error"]["context"]["asset_tag"] == "SN-MISSING"

    @pytest.mark.fast
    def test_netbox_query_error_returns_502(self) -> None:
        exc = NetboxQueryError(
            "get_device", RuntimeError("connection refused"), asset_tag="SN-CPU-001"
        )
        c = _build_client(loader_result=exc)
        resp = c.get("/v1/render/SN-CPU-001/meta-data")
        assert resp.status_code == 502
        body = resp.json()
        assert body["error"]["type"] == "NetboxQueryError"
        assert body["error"]["context"]["operation"] == "get_device"

    @pytest.mark.fast
    def test_invariant_error_returns_422(self) -> None:
        c = _build_client(loader_result=InvariantError("ns-nic-count", "wrong count"))
        resp = c.get("/v1/render/SN-CPU-001/meta-data")
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"]["type"] == "InvariantError"


# ---------------------------------------------------------------------------
# Request ID middleware.
# ---------------------------------------------------------------------------


class TestRequestId:
    """Request-ID middleware stamps responses and honors caller IDs.

    Why both directions:
        New requests need a fresh ID; downstream callers (an API
        gateway, a tracing system) may have a correlation ID they
        want preserved across the hop.
    """

    @pytest.mark.fast
    def test_mints_request_id_when_absent(self) -> None:
        c = _build_client(loader_result=make_cpu_intent())
        resp = c.get("/v1/render/SN-CPU-001/meta-data")
        # Must be a UUID.
        uuid.UUID(resp.headers[REQUEST_ID_HEADER])

    @pytest.mark.fast
    def test_honors_inbound_uuid(self) -> None:
        c = _build_client(loader_result=make_cpu_intent())
        inbound = str(uuid.uuid4())
        resp = c.get(
            "/v1/render/SN-CPU-001/meta-data",
            headers={REQUEST_ID_HEADER: inbound},
        )
        assert resp.headers[REQUEST_ID_HEADER] == inbound

    @pytest.mark.fast
    def test_rejects_non_uuid_request_id(self) -> None:
        """A non-UUID inbound header is replaced with a fresh UUID.

        Why:
            Restricting to UUID shape keeps log fields parseable and
            prevents an attacker from injecting structured-log payloads
            via the header.
        """
        c = _build_client(loader_result=make_cpu_intent())
        resp = c.get(
            "/v1/render/SN-CPU-001/meta-data",
            headers={REQUEST_ID_HEADER: "not-a-uuid"},
        )
        # Should NOT be the inbound value; should be a real UUID.
        assert resp.headers[REQUEST_ID_HEADER] != "not-a-uuid"
        uuid.UUID(resp.headers[REQUEST_ID_HEADER])


# ---------------------------------------------------------------------------
# Operational endpoints.
# ---------------------------------------------------------------------------


class TestOperational:
    """/healthz, /readyz, /metrics behave per their contracts."""

    @pytest.mark.fast
    def test_healthz_is_always_ok(self) -> None:
        c = _build_client(loader_result=make_cpu_intent())
        assert c.get("/healthz").status_code == 200

    @pytest.mark.fast
    def test_readyz_ok_when_netbox_is_up(self) -> None:
        c = _build_client(loader_result=make_cpu_intent(), netbox_status_ok=True)
        assert c.get("/readyz").status_code == 200

    @pytest.mark.fast
    def test_readyz_503_when_netbox_is_down(self) -> None:
        c = _build_client(loader_result=make_cpu_intent(), netbox_status_ok=False)
        assert c.get("/readyz").status_code == 503

    @pytest.mark.fast
    def test_metrics_returns_prometheus_format(self) -> None:
        c = _build_client(loader_result=make_cpu_intent())
        resp = c.get("/metrics")
        assert resp.status_code == 200
        # Prometheus content-type per the exposition format spec.
        assert "text/plain" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# OpenAPI surface.
# ---------------------------------------------------------------------------


class TestOpenApi:
    """The auto-generated OpenAPI doc surface is reachable."""

    @pytest.mark.fast
    def test_openapi_json_is_served(self) -> None:
        c = _build_client(loader_result=make_cpu_intent())
        resp = c.get("/openapi.json")
        assert resp.status_code == 200
        spec = resp.json()
        # The three render routes are present.
        paths = spec["paths"]
        assert "/v1/render/{asset_tag}/meta-data" in paths
        assert "/v1/render/{asset_tag}/user-data" in paths
        assert "/v1/render/{asset_tag}/network-config" in paths

    @pytest.mark.fast
    def test_docs_ui_is_served(self) -> None:
        c = _build_client(loader_result=make_cpu_intent())
        resp = c.get("/docs")
        assert resp.status_code == 200

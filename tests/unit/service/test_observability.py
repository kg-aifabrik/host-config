"""Observability tests for the renderer service (M2-6, issue #16).

Two concerns:

- **Debug-level traceability** (plan §7.5): with logs captured, a single
  render request emits an ordered, reconstructable story — request
  received → Netbox query start/finish (with timing + record shape) →
  intent validated → template selected → render start/finish (with byte
  count + timing) → request completed.

- **Prometheus metrics** (plan §7.6): the render path increments
  ``host_config_renders_total`` with the right ``outcome`` label, observes
  the duration histograms, and the HTTP middleware counts requests under
  the *route template* (bounded cardinality), never the concrete path.

Unit-level: a stub Netbox client + a patched ``load_host_intent`` (the
same seams ``test_app.py`` uses). No live Netbox.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
import structlog
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from structlog.testing import capture_logs

from host_config.logging_config import configure_logging
from host_config.models.errors import InvariantError
from host_config.models.intent import HostIntent
from host_config.netbox.errors import HostNotFoundError, NetboxQueryError
from host_config.service import routes as routes_mod
from host_config.service.app import make_app
from host_config.service.dependencies import get_netbox_client
from tests.unit.models.test_intent import make_cpu_intent


class _StubNetboxClient:
    """Minimal pynetbox stand-in (only ``status()`` is touched, by /readyz)."""

    def status(self) -> dict[str, str]:
        return {"netbox-version": "4.2.0"}


def _build_client(*, loader_result: HostIntent | Exception) -> TestClient:
    """TestClient with ``load_host_intent`` patched + Netbox stubbed."""
    app = make_app()
    app.state.netbox_client = _StubNetboxClient()

    def fake_loader(_client: Any, _asset_tag: str) -> HostIntent:
        if isinstance(loader_result, Exception):
            raise loader_result
        return loader_result

    routes_mod.load_host_intent = fake_loader  # type: ignore[assignment]
    client = TestClient(app)
    app.dependency_overrides[get_netbox_client] = lambda: app.state.netbox_client
    return client


@pytest.fixture(autouse=True)
def _restore_loader() -> Iterator[None]:
    """Restore the patched loader after each test so it can't leak."""
    original = routes_mod.load_host_intent
    try:
        yield
    finally:
        routes_mod.load_host_intent = original


def _metric(name: str, labels: dict[str, str]) -> float:
    """Read a Prometheus sample value, treating absent series as 0.0."""
    return REGISTRY.get_sample_value(name, labels) or 0.0


# ---------------------------------------------------------------------------
# Debug-level traceability (plan §7.5).
# ---------------------------------------------------------------------------


class TestTraceability:
    """A single render request emits the ordered story §7.5 demands."""

    @pytest.mark.fast
    def test_successful_render_emits_ordered_events(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # §7.5 is explicitly "with LOG_LEVEL=DEBUG": the granular
        # netbox/template/render lines are DEBUG, so the filtering bound
        # logger must admit DEBUG. make_app() reads LOG_LEVEL at build time.
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        intent = make_cpu_intent()
        client = _build_client(loader_result=intent)

        with capture_logs() as logs:
            resp = client.get(f"/v1/render/{intent.asset_tag}/network-config")
        assert resp.status_code == 200

        events = [entry["event"] for entry in logs]
        # The story, in order. Assert as a subsequence so unrelated log
        # lines (if any are added later) don't make the test brittle.
        expected_order = [
            "request.received",
            "render.requested",
            "netbox.query.started",
            "netbox.query.completed",
            "intent.validated",
            "template.selected",
            "render.started",
            "render.completed",
            "request.completed",
        ]
        positions = [events.index(e) for e in expected_order if e in events]
        assert positions == sorted(positions), f"events out of order: {events}"
        for e in expected_order:
            assert e in events, f"missing required log event: {e!r}"

    @pytest.mark.fast
    def test_key_fields_present_for_reconstruction(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        intent = make_cpu_intent()
        client = _build_client(loader_result=intent)

        with capture_logs() as logs:
            client.get(f"/v1/render/{intent.asset_tag}/user-data")

        by_event = {entry["event"]: entry for entry in logs}
        # Netbox completion: timing + record shape (so an engineer can see
        # what came back without re-querying).
        nb = by_event["netbox.query.completed"]
        assert "duration_ms" in nb
        assert nb["role"] == intent.role.value
        assert nb["ns_nic_count"] == len(intent.ns_nics)
        # Render completion: byte count + timing.
        rc = by_event["render.completed"]
        assert rc["bytes"] > 0
        assert "duration_ms" in rc

    @pytest.mark.fast
    def test_request_id_in_response_header(self) -> None:
        """Correlation ID (plan §7.3) surfaces to the caller."""
        intent = make_cpu_intent()
        client = _build_client(loader_result=intent)
        resp = client.get(f"/v1/render/{intent.asset_tag}/meta-data")
        assert resp.headers.get("X-Request-Id")


# ---------------------------------------------------------------------------
# Metrics (plan §7.6).
# ---------------------------------------------------------------------------


class TestMetrics:
    """The render path and HTTP middleware emit the §7.6 metric set."""

    @pytest.mark.fast
    def test_successful_render_increments_renders_total_success(self) -> None:
        intent = make_cpu_intent()
        role = intent.role.value
        before = _metric("host_config_renders_total", {"role": role, "outcome": "success"})
        client = _build_client(loader_result=intent)
        client.get(f"/v1/render/{intent.asset_tag}/meta-data")
        after = _metric("host_config_renders_total", {"role": role, "outcome": "success"})
        assert after == before + 1

    @pytest.mark.fast
    def test_netbox_error_increments_netbox_error_outcome(self) -> None:
        before = _metric(
            "host_config_renders_total", {"role": "unknown", "outcome": "netbox_error"}
        )
        exc = NetboxQueryError("get_device", RuntimeError("boom"), asset_tag="SN-X")
        client = _build_client(loader_result=exc)
        resp = client.get("/v1/render/SN-X/meta-data")
        assert resp.status_code == 502
        after = _metric("host_config_renders_total", {"role": "unknown", "outcome": "netbox_error"})
        assert after == before + 1

    @pytest.mark.fast
    def test_host_not_found_increments_netbox_error_outcome(self) -> None:
        before = _metric(
            "host_config_renders_total", {"role": "unknown", "outcome": "netbox_error"}
        )
        client = _build_client(loader_result=HostNotFoundError("SN-MISSING"))
        resp = client.get("/v1/render/SN-MISSING/meta-data")
        assert resp.status_code == 404
        after = _metric("host_config_renders_total", {"role": "unknown", "outcome": "netbox_error"})
        assert after == before + 1

    @pytest.mark.fast
    def test_invariant_error_increments_validation_error_outcome(self) -> None:
        before = _metric(
            "host_config_renders_total", {"role": "unknown", "outcome": "validation_error"}
        )
        client = _build_client(loader_result=InvariantError("roce-count-cpu", "bad intent"))
        resp = client.get("/v1/render/SN-CPU-001/meta-data")
        assert resp.status_code == 422
        after = _metric(
            "host_config_renders_total", {"role": "unknown", "outcome": "validation_error"}
        )
        assert after == before + 1

    @pytest.mark.fast
    def test_requests_total_labelled_by_route_template_not_concrete_path(self) -> None:
        """Cardinality guard: the metric path label is the route template."""
        intent = make_cpu_intent()
        # The file-kind is a literal path segment (routes are registered per
        # FileKind), so the template keeps {asset_tag} as the only param --
        # bounded cardinality: 3 file kinds x methods x statuses.
        template = "/v1/render/{asset_tag}/meta-data"
        before = _metric(
            "host_config_requests_total",
            {"method": "GET", "path": template, "status": "200"},
        )
        client = _build_client(loader_result=intent)
        client.get(f"/v1/render/{intent.asset_tag}/meta-data")
        after = _metric(
            "host_config_requests_total",
            {"method": "GET", "path": template, "status": "200"},
        )
        assert after == before + 1
        # The concrete path (with the asset tag) must NOT appear as a label.
        concrete = _metric(
            "host_config_requests_total",
            {
                "method": "GET",
                "path": f"/v1/render/{intent.asset_tag}/meta-data",
                "status": "200",
            },
        )
        assert concrete == 0.0

    @pytest.mark.fast
    def test_render_duration_observed_on_success(self) -> None:
        intent = make_cpu_intent()
        role = intent.role.value
        before = _metric("host_config_render_duration_seconds_count", {"role": role})
        client = _build_client(loader_result=intent)
        client.get(f"/v1/render/{intent.asset_tag}/network-config")
        after = _metric("host_config_render_duration_seconds_count", {"role": role})
        assert after == before + 1

    @pytest.mark.fast
    def test_metrics_endpoint_exposes_render_series(self) -> None:
        intent = make_cpu_intent()
        client = _build_client(loader_result=intent)
        client.get(f"/v1/render/{intent.asset_tag}/meta-data")
        body = client.get("/metrics").text
        assert "host_config_renders_total" in body
        assert "host_config_requests_total" in body
        assert "host_config_request_duration_seconds" in body
        assert "host_config_active_requests" in body

    @pytest.mark.fast
    def test_active_requests_settles_to_zero_after_request(self) -> None:
        """The in-flight gauge is balanced (inc/dec) across a request."""
        intent = make_cpu_intent()
        client = _build_client(loader_result=intent)
        client.get(f"/v1/render/{intent.asset_tag}/meta-data")
        assert _metric("host_config_active_requests", {}) == 0.0


# ---------------------------------------------------------------------------
# logging_config single-source-of-truth (plan §7.1).
# ---------------------------------------------------------------------------


class TestLoggingConfig:
    @pytest.mark.fast
    def test_configure_logging_is_idempotent(self) -> None:
        """Repeated configure_logging() calls don't raise (tests call freely)."""
        configure_logging()
        configure_logging(level="DEBUG")
        # A logger still works after reconfiguration.
        structlog.get_logger(__name__).info("smoke")

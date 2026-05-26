"""Hybrid render gate (M3.5-1, issue #22).

The end-to-end seed-delivery path through the *real* deployed stack:

    requests → nginx-cache (:80) → renderer (:8080) → Netbox

Unlike the in-process integration test (M2.5-1, which wires a TestClient
straight to the renderer), this gate drives the actual nginx cache tier
over HTTP so it can assert the warm/cold split, cache-vs-render timing,
the Prometheus counters from M2-6, and the operational resilience that
matters: a host whose seed is already cached keeps booting even when
Netbox is down.

Asserted:
- **Cold path:** a never-seen key MISSes, renders fully, and is byte-equal
  to a direct renderer fetch.
- **Warm path:** the second read HITs, served from disk in < 50 ms.
- **Metrics (§7.6):** the cold path increments
  ``host_config_renders_total{role,outcome=success}``; the warm path does
  NOT (nginx serves it without ever reaching the renderer).
- **Correlation (§7.3):** responses carry ``X-Request-Id``.
- **Netbox-down resilience:** with Netbox stopped, an already-cached key
  still serves 200 (boot continues); a cold key fails fast (bounded blast
  radius). Guarded on local Docker access; always restarts Netbox.

This lives under ``tests/e2e/`` so ``just lab-test`` runs it against the
deployed Droplet stack, but it needs no KVM/VM — it skips only when the
seed server is unreachable.

Run::

    SEED_SERVER_URL=http://127.0.0.1:80 pytest tests/e2e/test_hybrid_render.py -v
"""

from __future__ import annotations

import os
import re
import subprocess
import time
import uuid
from collections.abc import Iterator

import pytest
import requests

_ASSET_TAG = "SN-CPU-001"
_RENDER_PATH = f"/v1/render/{_ASSET_TAG}/meta-data"
_DEFAULT_SEED_URL = "http://127.0.0.1:80"
_DEFAULT_RENDERER_URL = "http://127.0.0.1:8080"
_TIMEOUT = 5.0
_WARM_LATENCY_BUDGET_S = 0.05  # plan §M3.5: warm path < 50 ms


def _seed_url() -> str:
    return os.environ.get("SEED_SERVER_URL", _DEFAULT_SEED_URL).rstrip("/")


def _renderer_url() -> str:
    return os.environ.get("RENDERER_URL", _DEFAULT_RENDERER_URL).rstrip("/")


def _reachable(url: str) -> bool:
    try:
        return requests.get(f"{url}/healthz", timeout=2.0).status_code == 200
    except requests.RequestException:
        return False


@pytest.fixture(scope="module")
def seed_url() -> Iterator[str]:
    url = _seed_url()
    if not _reachable(url):
        pytest.skip(
            f"seed server (nginx-cache) not reachable at {url}; "
            "deploy the lab (just lab-up) or set SEED_SERVER_URL"
        )
    yield url


def _cb() -> str:
    """Unique query arg → a guaranteed-cold cache key per call."""
    return f"cb={uuid.uuid4().hex}"


def _get(
    url: str, path: str, *, query: str, headers: dict[str, str] | None = None
) -> requests.Response:
    return requests.get(f"{url}{path}?{query}", headers=headers or {}, timeout=_TIMEOUT)


def _scrape_counter(seed: str, metric: str, labels: dict[str, str]) -> float:
    """Return a Prometheus counter sample value from the live /metrics, or 0.0.

    Parses the text exposition format, matching the metric name and the
    exact label set (order-independent, since prometheus_client emits
    labels alphabetically but we don't want to depend on that).
    """
    body = requests.get(f"{seed}/metrics", timeout=_TIMEOUT).text
    for line in body.splitlines():
        if not line.startswith(metric + "{"):
            continue
        inner = line[len(metric) + 1 : line.rindex("}")]
        pairs = dict(re.findall(r'(\w+)="([^"]*)"', inner))
        if all(pairs.get(k) == v for k, v in labels.items()):
            return float(line.rsplit("}", 1)[1])
    return 0.0


class TestColdWarmPaths:
    @pytest.mark.slow
    def test_cold_path_miss_byte_equal_to_renderer(self, seed_url: str) -> None:
        q = _cb()
        cold = _get(seed_url, _RENDER_PATH, query=q)
        assert cold.status_code == 200
        assert cold.headers.get("X-Cache-Status") == "MISS"
        # Byte-equal to a direct renderer fetch (the cache must be transparent).
        direct = _get(_renderer_url(), _RENDER_PATH, query=_cb())
        assert direct.status_code == 200
        assert cold.content == direct.content

    @pytest.mark.slow
    def test_warm_path_hit_under_50ms(self, seed_url: str) -> None:
        q = _cb()
        _get(seed_url, _RENDER_PATH, query=q)  # MISS → populate
        start = time.perf_counter()
        warm = _get(seed_url, _RENDER_PATH, query=q)
        elapsed = time.perf_counter() - start
        assert warm.headers.get("X-Cache-Status") == "HIT"
        assert elapsed < _WARM_LATENCY_BUDGET_S, (
            f"warm path took {elapsed * 1000:.1f} ms (budget "
            f"{_WARM_LATENCY_BUDGET_S * 1000:.0f} ms)"
        )

    @pytest.mark.slow
    def test_response_carries_request_id(self, seed_url: str) -> None:
        cold = _get(seed_url, _RENDER_PATH, query=_cb())
        assert cold.headers.get("X-Request-Id")


class TestMetricsAcrossCacheTier:
    @pytest.mark.slow
    def test_cold_increments_renders_total_warm_does_not(self, seed_url: str) -> None:
        labels = {"role": "cpu", "outcome": "success"}
        q = _cb()

        before = _scrape_counter(seed_url, "host_config_renders_total", labels)
        cold = _get(seed_url, _RENDER_PATH, query=q)
        assert cold.headers.get("X-Cache-Status") == "MISS"
        after_cold = _scrape_counter(seed_url, "host_config_renders_total", labels)
        assert after_cold == before + 1, "cold path should reach the renderer"

        warm = _get(seed_url, _RENDER_PATH, query=q)
        assert warm.headers.get("X-Cache-Status") == "HIT"
        after_warm = _scrape_counter(seed_url, "host_config_renders_total", labels)
        assert after_warm == after_cold, (
            "warm path must be served by nginx and never reach the renderer"
        )

    @pytest.mark.slow
    def test_metrics_exposed_through_stack(self, seed_url: str) -> None:
        body = requests.get(f"{seed_url}/metrics", timeout=_TIMEOUT).text
        assert "host_config_renders_total" in body
        assert "host_config_request_duration_seconds" in body


# ---------------------------------------------------------------------------
# Netbox-down resilience. Guarded on local Docker access; always restarts.
# ---------------------------------------------------------------------------


def _netbox_container() -> str | None:
    """Resolve the running netbox app container name, or None if unavailable."""
    try:
        out = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    # The app container ends in `-netbox-1`; exclude the worker/housekeeping.
    for name in out.stdout.split():
        if name.endswith("-netbox-1") and "worker" not in name:
            return name
    return None


def _docker(*args: str) -> None:
    subprocess.run(["docker", *args], capture_output=True, timeout=60, check=False)  # noqa: S603,S607


class TestNetboxDownResilience:
    @pytest.mark.slow
    def test_cached_host_keeps_serving_when_netbox_down(self, seed_url: str) -> None:
        container = _netbox_container()
        if container is None:
            pytest.skip("no local Netbox container to stop; run on the lab Droplet")

        q = _cb()
        warm = _get(seed_url, _RENDER_PATH, query=q)  # MISS → populate
        assert warm.status_code == 200
        warm_body = warm.content

        _docker("stop", container)
        try:
            # An already-cached key must keep serving so in-flight boots
            # don't break when Netbox blips.
            cached = _get(seed_url, _RENDER_PATH, query=q)
            assert cached.status_code == 200
            assert cached.content == warm_body

            # A cold key has nothing to serve and nothing to render →
            # fails fast (bounded blast radius), not a hang.
            cold = _get(seed_url, _RENDER_PATH, query=_cb())
            assert cold.status_code in (502, 503, 504)
        finally:
            _docker("start", container)
            # Wait for Netbox to come back so later tests/sessions aren't
            # left with a downed dependency.
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if _reachable(seed_url) and requests.get(
                    f"{seed_url}/readyz", timeout=_TIMEOUT
                ).status_code == 200:
                    break
                time.sleep(3)

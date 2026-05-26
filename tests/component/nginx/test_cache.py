"""Cache behavior component tests (M3-2, issue #19).

Exercises the nginx-cache reverse proxy in front of the renderer against a
live stack. Tests the cache state machine via the ``X-Cache-Status``
response header:

- **Cold → warm:** first request for a key MISSes (proxied to the
  renderer), the second HITs (served from disk).
- **Manual refresh:** an ``X-Purge`` header bypasses the cached entry,
  re-fetches, and re-stores it (BYPASS), after which reads HIT again.
- **Operational endpoints** (``/healthz``, ``/readyz``, ``/metrics``) are
  never cached.
- **Freshness contract:** render responses carry ``Cache-Control:
  max-age=300`` + an ``ETag`` (the TTL window both layers agree on).

Determinism: each test appends a unique cache-buster query arg
(``?cb=<uuid>``). nginx's cache key includes the query string, so every
test run gets a guaranteed-cold key — no cross-run or cross-test cache
bleed. The renderer ignores the unknown arg, so the body is unchanged.

The TTL-*expiry* path (a warm entry going stale after 300 s) and the
Netbox-down stale-serving path (``proxy_cache_use_stale``) are exercised
by the M3.5-1 hybrid gate, where stopping the Netbox container is in
scope. Component tests here assert only the directly-observable,
non-destructive cache mechanics.

Skips cleanly unless the seed server (nginx-cache) is reachable, so
unit-only runs stay green.

Run::

    SEED_SERVER_URL=http://127.0.0.1:80 pytest tests/component/nginx -v
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
import requests

# Asset tag present in the loaded Netbox fixtures (fixtures/netbox/data).
_ASSET_TAG = "SN-CPU-001"
_RENDER_PATH = f"/v1/render/{_ASSET_TAG}/meta-data"
_DEFAULT_SEED_URL = "http://127.0.0.1:80"
_TIMEOUT = 5.0


def _seed_url() -> str:
    return os.environ.get("SEED_SERVER_URL", _DEFAULT_SEED_URL).rstrip("/")


def _reachable(url: str) -> bool:
    try:
        # /healthz passes through nginx to the renderer; proves both tiers up.
        return requests.get(f"{url}/healthz", timeout=2.0).status_code == 200
    except requests.RequestException:
        return False


@pytest.fixture(scope="module")
def seed_url() -> Iterator[str]:
    """Base URL of the nginx-cache; skip the module if it isn't reachable."""
    url = _seed_url()
    if not _reachable(url):
        pytest.skip(
            f"seed server (nginx-cache) not reachable at {url}; "
            "deploy the lab (just lab-up) or set SEED_SERVER_URL"
        )
    yield url


def _cache_buster() -> str:
    """A unique query string so each test gets a guaranteed-cold cache key."""
    return f"cb={uuid.uuid4().hex}"


def _get(
    url: str, path: str, *, query: str, headers: dict[str, str] | None = None
) -> requests.Response:
    return requests.get(f"{url}{path}?{query}", headers=headers or {}, timeout=_TIMEOUT)


class TestCacheStateMachine:
    """Cold/warm/bypass transitions via X-Cache-Status."""

    @pytest.mark.slow
    def test_cold_miss_then_warm_hit(self, seed_url: str) -> None:
        q = _cache_buster()
        first = _get(seed_url, _RENDER_PATH, query=q)
        second = _get(seed_url, _RENDER_PATH, query=q)

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.headers.get("X-Cache-Status") == "MISS"
        assert second.headers.get("X-Cache-Status") == "HIT"
        # Cache must not corrupt the payload.
        assert first.content == second.content

    @pytest.mark.slow
    def test_x_purge_header_bypasses_and_restores(self, seed_url: str) -> None:
        q = _cache_buster()
        _get(seed_url, _RENDER_PATH, query=q)  # MISS → populate
        warm = _get(seed_url, _RENDER_PATH, query=q)
        assert warm.headers.get("X-Cache-Status") == "HIT"

        bypass = _get(seed_url, _RENDER_PATH, query=q, headers={"X-Purge": "1"})
        assert bypass.status_code == 200
        assert bypass.headers.get("X-Cache-Status") == "BYPASS"

        # After the bypass re-stored the entry, reads HIT again with the
        # same (refreshed) body.
        rehit = _get(seed_url, _RENDER_PATH, query=q)
        assert rehit.headers.get("X-Cache-Status") == "HIT"
        assert rehit.content == bypass.content

    @pytest.mark.slow
    def test_warm_entry_is_byte_identical_across_reads(self, seed_url: str) -> None:
        q = _cache_buster()
        bodies = {_get(seed_url, _RENDER_PATH, query=q).content for _ in range(4)}
        assert len(bodies) == 1, "cache returned inconsistent bodies"


class TestNoCacheOnOperational:
    """Operational endpoints must never be cached."""

    @pytest.mark.slow
    @pytest.mark.parametrize("endpoint", ["/healthz", "/metrics"])
    def test_operational_endpoint_uncached(self, seed_url: str, endpoint: str) -> None:
        resp = requests.get(f"{seed_url}{endpoint}", timeout=_TIMEOUT)
        assert resp.status_code == 200
        # The X-Cache-Status header is added only in the cached /v1/ location;
        # operational locations run `proxy_cache off` and omit it entirely.
        assert "X-Cache-Status" not in resp.headers


class TestFreshnessContract:
    """The render response advertises the 300 s freshness window + ETag."""

    @pytest.mark.slow
    def test_render_carries_cache_control_and_etag(self, seed_url: str) -> None:
        resp = _get(seed_url, _RENDER_PATH, query=_cache_buster())
        assert resp.status_code == 200
        cache_control = resp.headers.get("Cache-Control", "")
        assert "max-age=300" in cache_control
        assert "public" in cache_control
        assert resp.headers.get("ETag")

    @pytest.mark.slow
    def test_etag_stable_across_cache_hits(self, seed_url: str) -> None:
        q = _cache_buster()
        first = _get(seed_url, _RENDER_PATH, query=q)
        second = _get(seed_url, _RENDER_PATH, query=q)
        assert first.headers.get("ETag") == second.headers.get("ETag")

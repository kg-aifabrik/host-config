"""HTTP routes for the renderer service.

Three render routes under `/v1/` (consumer API; ADR-0003 versioning
policy) and three unversioned operational routes (`/healthz`, `/readyz`,
`/metrics`) — operational endpoints intentionally sit outside the
versioning scheme since they're not part of the published consumer
contract.

Why route handlers are tiny:
    The bulk of the work — loading from Netbox, rendering — lives in
    `host_config.netbox.loader` and `host_config.render.emitter`. The
    handler is a translator: HTTP-shape → typed call → typed response.
    Errors are translated by exception handlers in `app.py`, not here,
    so the route stays linear and easy to read.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog
from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from host_config.netbox.loader import load_host_intent
from host_config.render.emitter import FileKind, render_for
from host_config.service.dependencies import NetboxClient

logger = structlog.get_logger(__name__)

# Consumer API — all routes under /v1/.
api = APIRouter(prefix="/v1")

# Operational API — unversioned by design.
ops = APIRouter()


def _render_endpoint(
    file_kind: FileKind,
) -> Callable[[str, NetboxClient], Awaitable[Response]]:
    """Build a handler that renders a single file kind for an asset tag.

    Approach:
        Three handlers share identical shape (load → render → respond
        with the right MIME type). Building them through a factory
        keeps the route module short and prevents accidental drift
        between the three near-duplicates.
    """

    async def handler(asset_tag: str, client: NetboxClient) -> Response:
        structlog.contextvars.bind_contextvars(asset_tag=asset_tag, file_kind=file_kind.value)
        logger.info("render.requested")
        intent = load_host_intent(client, asset_tag)
        body = render_for(intent, file_kind)
        logger.info("render.completed", bytes=len(body))
        # cloud-init reads plain text/YAML; we mark it as such. The
        # `text/plain` choice (not `application/yaml`) matches what
        # cloud-init's NoCloud HTTP fetcher expects.
        return Response(content=body, media_type="text/plain; charset=utf-8")

    return handler


# Register the three render routes. Using `add_api_route` (rather than
# decorators) so the factory output above can be plugged in by name.
for _kind in FileKind:
    api.add_api_route(
        f"/render/{{asset_tag}}/{_kind.value}",
        _render_endpoint(_kind),
        methods=["GET"],
        name=f"render_{_kind.value.replace('-', '_')}",
        summary=f"Render the {_kind.value} cloud-init payload for an asset tag.",
        response_class=Response,
    )


@ops.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    """Liveness probe — process is up and the event loop responds.

    Why split from /readyz:
        Liveness asks "is the process alive?"; readiness asks "is the
        process able to serve traffic?". A failing dependency (Netbox
        unreachable) should fail readiness, not liveness — otherwise
        the orchestrator restarts a healthy process in a futile loop.
    """
    return {"status": "ok"}


@ops.get("/readyz", include_in_schema=False)
async def readyz(client: NetboxClient) -> dict[str, str]:
    """Readiness probe — Netbox is reachable.

    Approach:
        Hit Netbox's status endpoint via the injected client. If it
        raises, FastAPI returns 500 by default; we'd rather return 503
        so the orchestrator removes us from the rotation.
    """
    try:
        # pynetbox's `status()` does a GET /api/status/; cheap.
        client.status()
    except Exception as exc:
        logger.warning("readyz.netbox_unreachable", error=str(exc))
        return Response(  # type: ignore[return-value]
            content='{"status":"unavailable"}',
            status_code=503,
            media_type="application/json",
        )
    return {"status": "ready"}


@ops.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Prometheus scrape endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


__all__ = ["api", "load_host_intent", "ops"]

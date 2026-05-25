"""HTTP middleware for the renderer service.

Three pieces of cross-cutting plumbing:

- **Request-ID middleware** — every request gets a UUIDv4 (or honors a
  caller-supplied ``X-Request-Id`` if it looks like a UUID, so an
  upstream gateway's correlation ID survives the hop). The ID goes
  into the response header *and* into the structlog contextvars so
  every log line in the request scope carries it.

- **Logging context bind** — happens inside the same middleware so log
  context is attached before the route handler runs and cleared in
  the same `finally` block. structlog's `bind_contextvars` /
  `clear_contextvars` are async-safe via contextvars.

- **Cache headers** — responses to `/v1/render/…` routes get
  ``Cache-Control: public, max-age=300``, ``ETag`` (SHA-256 of the
  body, hex-encoded), and ``Last-Modified`` (service start time,
  stable across requests for the same body). These headers let nginx's
  ``proxy_cache`` (M3-1) and any downstream HTTP cache revalidate
  efficiently without a round-trip to the renderer.

  Why SHA-256 (not BLAKE3):
      BLAKE3 is faster but requires a third-party package. SHA-256 is
      in stdlib, acceptable latency for ~1 KB payloads, and widely
      supported by HTTP intermediaries. Switch to BLAKE3 via ADR if
      benchmarking shows it matters.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Awaitable, Callable
from email.utils import formatdate

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

REQUEST_ID_HEADER = "X-Request-Id"

# The service start-time is used as `Last-Modified` for render responses.
# It's stable for the lifetime of the process: every host that renders a
# config during this run gets the same Last-Modified, so a `304 Not
# Modified` cache revalidation works correctly for a given service
# deployment.
_SERVICE_START_RFC_HTTP = formatdate(time.time(), usegmt=True)

# Only render routes get cache headers — operational endpoints (/healthz,
# /readyz, /metrics) must never be cached.
_CACHE_ROUTE_PREFIX = "/v1/render/"
_CACHE_MAX_AGE = 300  # seconds — matches nginx's proxy_cache_valid 200 5m

logger = structlog.get_logger(__name__)


def _coerce_request_id(raw: str | None) -> str:
    """Accept a caller-supplied request ID if it parses as a UUID; else mint one.

    Why the UUID check:
        An attacker could otherwise stuff arbitrary text into our logs
        via this header. Restricting to UUID shape keeps the log
        format predictable without dropping legitimate upstream IDs.
    """
    if raw:
        try:
            return str(uuid.UUID(raw))
        except (ValueError, TypeError):
            pass
    return str(uuid.uuid4())


def _inject_cache_headers(request: Request, response: Response) -> None:
    """Add cache-control headers to successful render responses.

    Only `/v1/render/…` routes get cache headers. The ETag is the
    SHA-256 of the body so nginx can do conditional GETs efficiently
    without re-rendering. Last-Modified is the service start time —
    stable per deployment, deterministic for the same body.

    Why not set Cache-Control on errors:
        A 404 or 502 could be transient (host not yet in Netbox, Netbox
        restarting). Caching errors would hide the recovery. Only 200
        responses carry public cache headers.
    """
    if not request.url.path.startswith(_CACHE_ROUTE_PREFIX):
        return
    if response.status_code != 200:  # noqa: PLR2004
        return
    # `Response.body` is the bytes set at construction time. This is
    # always available for our plain `Response(content=..., ...)` returns.
    # StreamingResponse wouldn't have `.body`, but our render routes never
    # stream (payloads are <4 KB). Using getattr defensively so the
    # middleware doesn't crash on unexpected response subclasses.
    raw_body: bytes = getattr(response, "body", b"")
    etag = hashlib.sha256(raw_body).hexdigest()
    response.headers["Cache-Control"] = f"public, max-age={_CACHE_MAX_AGE}"
    response.headers["ETag"] = f'"{etag}"'
    response.headers["Last-Modified"] = _SERVICE_START_RFC_HTTP


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Stamp every request with a request ID and bind it to log context.

    Approach:
        On dispatch:
          1. Resolve the request ID (honor inbound or mint).
          2. ``bind_contextvars`` it into structlog so every log line
             in the handler's scope carries it.
          3. Time the request.
          4. Stamp the ID into the response header.
          5. Always ``clear_contextvars`` in `finally` so the binding
             doesn't bleed across requests.

    Why a middleware (not a dependency):
        Dependencies run AFTER request parsing — too late for the
        access log. Middleware runs first, so we get logs for things
        like 422 validation failures with the request ID attached.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = _coerce_request_id(request.headers.get(REQUEST_ID_HEADER))
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            logger.exception("request.failed", elapsed_ms=round(elapsed_ms, 2))
            raise
        else:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            logger.info(
                "request.completed",
                status_code=response.status_code,
                elapsed_ms=round(elapsed_ms, 2),
            )
            response.headers[REQUEST_ID_HEADER] = request_id
            _inject_cache_headers(request, response)
            return response
        finally:
            structlog.contextvars.clear_contextvars()

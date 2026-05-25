"""HTTP middleware for the renderer service.

Two pieces of cross-cutting plumbing:

- **Request-ID middleware** — every request gets a UUIDv4 (or honors a
  caller-supplied ``X-Request-Id`` if it looks like a UUID, so an
  upstream gateway's correlation ID survives the hop). The ID goes
  into the response header *and* into the structlog contextvars so
  every log line in the request scope carries it.

- **Logging context bind** — happens inside the same middleware so log
  context is attached before the route handler runs and cleared in
  the same `finally` block. structlog's `bind_contextvars` /
  `clear_contextvars` are async-safe via contextvars.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

REQUEST_ID_HEADER = "X-Request-Id"
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
            return response
        finally:
            structlog.contextvars.clear_contextvars()

"""FastAPI application factory.

`make_app()` returns a fresh `FastAPI` per call. Tests can construct
multiple apps in the same process without state leaking; production
uses `host_config.service:app` (the module-level variant below).

Wires together:
    - the `RequestContextMiddleware` (request-id + structlog binding)
    - the typed error → JSON envelope handlers
    - the consumer (`/v1/…`) and operational routers

Error envelope shape (returned for every typed error):

    {
      "error": {
        "type":    "<exception class name>",
        "message": "<human-readable>",
        "context": { ... }
      }
    }

The shape is locked in here because the CLI and any other consumers
will parse it. A breaking change to this envelope is an API break.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from host_config.errors import HostConfigError
from host_config.models.errors import InvariantError
from host_config.netbox.errors import HostNotFoundError, NetboxQueryError
from host_config.service.dependencies import make_netbox_client
from host_config.service.middleware import RequestContextMiddleware
from host_config.service.routes import api, ops

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _configure_logging() -> None:
    """One-shot structlog config; idempotent so tests can call freely.

    Approach:
        Routes structlog through stdlib logging at INFO level by default
        (override with `LOG_LEVEL`). JSON renderer for production; the
        `KeyValueRenderer` for local dev is left as a future option
        (current scope: structured logs are JSON everywhere).
    """
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level, logging.INFO)),
        cache_logger_on_first_use=True,
    )


def _error_envelope(
    exc_type: str, message: str, context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build the canonical JSON error envelope."""
    return {
        "error": {
            "type": exc_type,
            "message": message,
            "context": context or {},
        }
    }


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Per-app lifecycle: build the Netbox client at startup, drop it at shutdown.

    Why a lifespan (not a global):
        Tests want to construct multiple apps with different (stubbed)
        clients. The lifespan + `app.state` pattern scopes the client
        to the app instance; nothing else can reach it.
    """
    # Allow tests to inject a pre-built client by setting it before
    # startup (via `app.state.netbox_client = stub` before TestClient).
    if not hasattr(app.state, "netbox_client") or app.state.netbox_client is None:
        app.state.netbox_client = make_netbox_client()
    yield
    # No close hook needed — pynetbox.api holds no persistent connection.


def make_app() -> FastAPI:
    """Build a fresh `FastAPI` instance wired with middleware, routes, handlers."""
    _configure_logging()

    app = FastAPI(
        title="host-config renderer",
        version="0.1.0",
        description=(
            "Renders cloud-init payloads (meta-data / user-data / "
            "network-config) for a given host asset tag, sourced from Netbox."
        ),
        lifespan=_lifespan,
    )

    app.add_middleware(RequestContextMiddleware)

    # Typed-error → HTTP translation. Order matters: more-specific
    # subclasses MUST come before their base classes, since FastAPI
    # picks the handler for the exception's exact class first, then
    # walks the MRO.
    @app.exception_handler(HostNotFoundError)
    async def _on_host_not_found(_: Request, exc: HostNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=_error_envelope(
                "HostNotFoundError",
                str(exc),
                {"asset_tag": exc.asset_tag},
            ),
        )

    @app.exception_handler(InvariantError)
    async def _on_invariant(_: Request, exc: InvariantError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_envelope(
                "InvariantError",
                str(exc),
                {"code": getattr(exc, "code", None)},
            ),
        )

    @app.exception_handler(NetboxQueryError)
    async def _on_netbox_query(_: Request, exc: NetboxQueryError) -> JSONResponse:
        # 502: we're a gateway to Netbox and Netbox failed us.
        return JSONResponse(
            status_code=502,
            content=_error_envelope(
                "NetboxQueryError",
                str(exc),
                {"asset_tag": exc.asset_tag, "operation": exc.operation},
            ),
        )

    @app.exception_handler(HostConfigError)
    async def _on_host_config(_: Request, exc: HostConfigError) -> JSONResponse:
        # Catch-all for any other typed error we add later.
        return JSONResponse(
            status_code=500,
            content=_error_envelope(type(exc).__name__, str(exc)),
        )

    app.include_router(api)
    app.include_router(ops)

    return app


# Module-level `app` for `uvicorn host_config.service:app`.
app = make_app()

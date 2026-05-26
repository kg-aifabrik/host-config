"""Single source of truth for structlog configuration.

Why a dedicated module (extracted from ``app.py``):
    CLI tools, test helpers, and the FastAPI service need identical
    logging configuration. A single call site means all three get
    consistent JSON output with the same processor chain.

Usage::

    from host_config.logging_config import configure_logging
    configure_logging()   # idempotent; safe to call multiple times

The configuration is intentionally minimal — one renderer (JSON
everywhere, including dev). A human-readable pretty-printer can be
toggled via ``LOG_LEVEL=DEBUG`` without changing the renderer. The
trade-off: slightly more verbose local debugging vs. zero per-env
configuration surface.
"""

from __future__ import annotations

import logging
import os

import structlog


def configure_logging(*, level: str | None = None) -> None:
    """Configure structlog for the renderer service.

    Approach:
        Reads ``LOG_LEVEL`` from the environment (default: INFO).
        Configures structlog with a JSON renderer, ISO timestamps,
        log-level injection, and contextvars merge (for per-request
        binding in the middleware).

        Idempotent: multiple calls with the same level are no-ops.

    Args:
        level: Override the log level. If ``None``, reads from the
            ``LOG_LEVEL`` env var. Case-insensitive ("DEBUG", "info").
    """
    resolved = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    numeric = getattr(logging, resolved, logging.INFO)

    logging.basicConfig(level=numeric, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer()
            if os.environ.get("LOG_FORMAT", "json").lower() == "console"
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric),
        # cache_logger_on_first_use=False so a reconfigure (a level change,
        # or a test switching to DEBUG) takes effect on the next log call
        # instead of being frozen at the level a module-level logger
        # captured on first use. The per-call construction cost is
        # negligible at this service's log volume.
        cache_logger_on_first_use=False,
    )

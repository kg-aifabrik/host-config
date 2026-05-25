"""FastAPI service package: HTTP boundary for the renderer.

`app.py` exposes `make_app()` — the application factory. Tests use it
directly with an injected Netbox client; production wires it through
`uvicorn host_config.service:app`.
"""

from __future__ import annotations

from host_config.service.app import make_app

__all__ = ["make_app"]

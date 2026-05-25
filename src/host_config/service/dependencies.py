"""FastAPI dependency providers.

The Netbox client is the one external resource the service needs. We
construct it once per app via the lifespan and expose it as a request-
scoped dependency. Tests override the dependency to inject a stub.

Why a dependency provider (vs. importing the client directly):
    FastAPI's `Depends` is the seam that lets component tests swap in
    a mock without monkeypatching. Importing the client at module top
    would make the seam invisible and force every test to patch the
    same module path.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, Request

DEFAULT_TOKEN_FILE = Path.home() / ".host-config" / "netbox-token"
DEFAULT_NETBOX_URL = "http://127.0.0.1:8000"


def _resolve_netbox_url() -> str:
    """Look up the Netbox base URL; env override beats default."""
    return os.environ.get("NETBOX_URL", DEFAULT_NETBOX_URL)


def _resolve_netbox_token() -> str | None:
    """Token resolution order: `NETBOX_TOKEN` env → token file → None.

    Why this order:
        Env var wins because deployments inject secrets that way. The
        file fallback exists for local dev (the netbox-dev Ansible
        role writes it).
    """
    if env_token := os.environ.get("NETBOX_TOKEN"):
        return env_token
    if DEFAULT_TOKEN_FILE.exists():
        return DEFAULT_TOKEN_FILE.read_text().strip()
    return None


def make_netbox_client() -> Any:
    """Construct a `pynetbox.api` client from the resolved URL + token.

    Approach:
        Imported lazily — pynetbox is the only place we touch the real
        Netbox API, and keeping the import inside the function lets
        tests run without pynetbox loaded (the dependency is overridden).
    """
    import pynetbox  # noqa: PLC0415 — lazy import, see docstring

    return pynetbox.api(_resolve_netbox_url(), token=_resolve_netbox_token())


def get_netbox_client(request: Request) -> Any:
    """Return the per-app Netbox client stored on `app.state`.

    Why on app.state (not a module-level singleton):
        `make_app()` builds a fresh state per call; tests run multiple
        apps in the same process. Hanging the client off `app.state`
        scopes it to the app instance.
    """
    return request.app.state.netbox_client


NetboxClient = Annotated[Any, Depends(get_netbox_client)]

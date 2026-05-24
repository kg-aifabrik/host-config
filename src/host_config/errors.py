"""Top-level error hierarchy for host-config.

This module defines the base exception every other host-config error inherits
from. Subpackages (`models`, `netbox`, `render`, `service`, ...) define their
own `errors.py` with package-specific exceptions that extend `HostConfigError`.

This package is NOT for catching third-party exceptions — those get wrapped
into typed host-config errors at module boundaries (see CODE_CONVENTIONS §6).
"""

from __future__ import annotations


class HostConfigError(Exception):
    """Root of the host-config exception hierarchy.

    All exceptions raised by host-config code inherit from this. Callers can
    use `except HostConfigError` to catch anything we raise without catching
    arbitrary third-party errors.
    """


__all__ = ["HostConfigError"]

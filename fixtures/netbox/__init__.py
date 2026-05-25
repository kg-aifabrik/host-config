"""Netbox fixture loader.

Populates a local Netbox with the two test hosts (one CPU, one B300)
the rest of the test pyramid relies on. Idempotent: re-running is a
no-op once the data is in place.

Public surface:

- ``HostFixture`` — validated host description loaded from YAML.
- ``load_fixture`` — parse one YAML file into a ``HostFixture``.
- ``populate`` — idempotently apply fixtures to a running Netbox.
- ``main`` — CLI entry point (``python -m fixtures.netbox.populate``).
- Errors (``FixtureError``, ``FixtureLoadError``, ``FixtureConflictError``).
"""

from __future__ import annotations

from fixtures.netbox.errors import (
    FixtureConflictError,
    FixtureError,
    FixtureLoadError,
)
from fixtures.netbox.populate import (
    HostFixture,
    PopulateReport,
    load_fixture,
    main,
    populate,
)

__all__ = [
    "FixtureConflictError",
    "FixtureError",
    "FixtureLoadError",
    "HostFixture",
    "PopulateReport",
    "load_fixture",
    "main",
    "populate",
]

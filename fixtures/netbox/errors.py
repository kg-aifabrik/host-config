"""Fixture-layer errors.

Raised by the fixture loader and populator when something is wrong with
the YAML inputs or with the observed Netbox state. Distinct from
``host_config.netbox.errors.*`` because fixtures are a different concern
(test data wrangling, not the renderer's runtime path).
"""

from __future__ import annotations

from host_config.errors import HostConfigError


class FixtureError(HostConfigError):
    """Base for every fixture-layer error."""


class FixtureLoadError(FixtureError):
    """A YAML fixture file is malformed, missing required fields, or invalid.

    Attributes:
        path: Path to the offending file.
        detail: Human-readable explanation.
    """

    def __init__(self, path: str, detail: str) -> None:
        super().__init__(f"failed to load fixture {path!r}: {detail}")
        self.path = path
        self.detail = detail


class FixtureConflictError(FixtureError):
    """Netbox holds an object with our name/key but a conflicting shape.

    Raised when the populator finds, for example, a Device with the
    target asset tag but a different name/role/site. The operator must
    either delete the conflicting object or update Netbox out-of-band.

    Attributes:
        kind: Netbox object type (e.g., ``"device"``, ``"interface"``).
        identifier: How the object was looked up (asset tag, name, etc.).
        detail: Description of the conflict.
    """

    def __init__(self, kind: str, identifier: str, detail: str) -> None:
        super().__init__(
            f"{kind} {identifier!r} already exists in Netbox with a conflicting shape: {detail}"
        )
        self.kind = kind
        self.identifier = identifier
        self.detail = detail


__all__ = ["FixtureConflictError", "FixtureError", "FixtureLoadError"]

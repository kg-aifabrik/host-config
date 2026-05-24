"""Model-layer errors.

Raised when a `HostIntent` cross-field invariant is violated. Pydantic's
own `ValidationError` covers single-field type/value validation; this module
covers the relationships *between* fields that Pydantic can't express
declaratively (e.g., "exactly one VLAN has a default gateway").

This module is NOT for catching Pydantic errors — those propagate up as
`pydantic.ValidationError`. Use `InvariantError` only for our own
cross-field rules.
"""

from __future__ import annotations

from host_config.errors import HostConfigError


class ModelError(HostConfigError):
    """Base class for all model-layer errors."""


class InvariantError(ModelError):
    """A cross-field invariant on a `HostIntent` was violated.

    Carries the invariant name and a human-readable detail. Callers can
    `except InvariantError` to handle invariant violations specifically.

    Attributes:
        invariant: Short label identifying which invariant fired
            (e.g., "exactly-one-default-gateway").
        detail: Human-readable explanation including the offending values.
    """

    def __init__(self, invariant: str, detail: str) -> None:
        super().__init__(f"[{invariant}] {detail}")
        self.invariant = invariant
        self.detail = detail


__all__ = ["InvariantError", "ModelError"]

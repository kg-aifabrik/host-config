"""Netbox-layer typed errors.

Every exception raised by `host_config.netbox.*` modules is one of these.
Callers `except NetboxError` to catch package-wide failures without
snagging arbitrary third-party (pynetbox / requests) errors.

Per CODE_CONVENTIONS §6: errors carry context (asset tag, operation,
cause) so an operator reading logs can reconstruct what was attempted
without re-running the failing code.
"""

from __future__ import annotations

from typing import Any

from host_config.errors import HostConfigError


class NetboxError(HostConfigError):
    """Base class for every Netbox-layer error."""


class NetboxQueryError(NetboxError):
    """A query against Netbox failed (transport, timeout, 5xx).

    Wraps the underlying exception (`requests.HTTPError`, `requests.Timeout`,
    pynetbox-internal errors) into our own typed surface.

    Attributes:
        asset_tag: The asset tag the query was for (or ``None`` if not asset-keyed).
        operation: Short verb identifying what was being attempted
            (e.g., ``"get_device"``, ``"list_custom_fields"``).
        cause: The original exception, attached as ``__cause__`` too.
    """

    def __init__(
        self,
        operation: str,
        cause: Exception,
        *,
        asset_tag: str | None = None,
    ) -> None:
        asset_part = f" asset_tag={asset_tag!r}" if asset_tag else ""
        super().__init__(f"Netbox query failed during operation={operation!r}{asset_part}: {cause}")
        self.operation = operation
        self.cause = cause
        self.asset_tag = asset_tag


class HostNotFoundError(NetboxError):
    """The asset tag is not present in Netbox.

    Distinct from `NetboxQueryError` because the action a caller takes
    differs: missing host = "fix the Netbox data"; query error = "retry
    or investigate Netbox health".
    """

    def __init__(self, asset_tag: str) -> None:
        super().__init__(f"no Netbox device found with asset_tag={asset_tag!r}")
        self.asset_tag = asset_tag


class SchemaError(NetboxError):
    """Schema apply detected an unrecoverable inconsistency.

    Raised when a custom field exists in Netbox with a shape that
    differs from our declarative spec in a way we don't auto-fix
    (e.g., type mismatch). Callers should fix the Netbox state and retry.
    """

    def __init__(self, field_name: str, detail: str, observed: dict[str, Any]) -> None:
        super().__init__(f"custom field {field_name!r} drift cannot be auto-fixed: {detail}")
        self.field_name = field_name
        self.detail = detail
        self.observed = observed


__all__ = [
    "HostNotFoundError",
    "NetboxError",
    "NetboxQueryError",
    "SchemaError",
]

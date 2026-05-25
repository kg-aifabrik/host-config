"""Netbox integration package.

Public surface:

- `CustomFieldSpec` — declarative spec for a Netbox custom field.
- `FieldType` — enum of supported custom-field types.
- `DEFAULT_FIELDS` — the schema this project depends on.
- `apply_schema` — idempotent apply against a running Netbox.
- Errors (re-exported from `errors.py`).
"""

from __future__ import annotations

from host_config.netbox.errors import (
    HostNotFoundError,
    NetboxError,
    NetboxQueryError,
    SchemaError,
)
from host_config.netbox.loader import load_host_intent
from host_config.netbox.schema import (
    DEFAULT_FIELDS,
    CustomFieldSpec,
    FieldType,
    SchemaApplyReport,
    apply_schema,
)

__all__ = [
    "DEFAULT_FIELDS",
    "CustomFieldSpec",
    "FieldType",
    "HostNotFoundError",
    "NetboxError",
    "NetboxQueryError",
    "SchemaApplyReport",
    "SchemaError",
    "apply_schema",
    "load_host_intent",
]

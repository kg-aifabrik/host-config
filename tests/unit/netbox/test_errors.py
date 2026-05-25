"""Tests for `host_config.netbox.errors`.

Unit-level. Verifies the Netbox-layer error hierarchy:

- All classes inherit from `HostConfigError` so callers can catch the
  package-wide base.
- Each error carries the contextual attributes documented in its
  class (so log messages and FastAPI error envelopes can surface them).

See `src/host_config/netbox/errors.py` for the types under test.
"""

from __future__ import annotations

import pytest

from host_config.errors import HostConfigError
from host_config.netbox.errors import (
    HostNotFoundError,
    NetboxError,
    NetboxQueryError,
    SchemaError,
)


class TestHierarchy:
    """Inheritance relationships across the Netbox-error hierarchy.

    These tests guard against an accidental refactor that breaks
    `except HostConfigError` or `except NetboxError` catching the
    package's errors. The renderer's FastAPI exception handler relies
    on this layering.
    """

    @pytest.mark.fast
    def test_all_extend_host_config_error(self) -> None:
        """Every Netbox error is catchable as both `NetboxError` and `HostConfigError`."""
        assert issubclass(NetboxError, HostConfigError)
        assert issubclass(NetboxQueryError, NetboxError)
        assert issubclass(HostNotFoundError, NetboxError)
        assert issubclass(SchemaError, NetboxError)


class TestNetboxQueryError:
    """Construction and surface of `NetboxQueryError`.

    Used as the boundary type when wrapping pynetbox / requests
    exceptions. Carries the operation name and the underlying cause
    so debuggers can see exactly what failed at the transport layer.
    """

    @pytest.mark.fast
    def test_carries_operation_and_cause(self) -> None:
        """The error exposes `operation`, `cause`, and `asset_tag` attributes.

        Why:
            Callers (logs, FastAPI error handlers) read these attributes
            to construct structured error envelopes. The `cause` is the
            original exception preserving the traceback.
        """
        cause = RuntimeError("boom")
        err = NetboxQueryError(operation="get_device", cause=cause, asset_tag="SN-001")
        assert err.operation == "get_device"
        assert err.cause is cause
        assert err.asset_tag == "SN-001"
        assert "SN-001" in str(err)
        assert "get_device" in str(err)

    @pytest.mark.fast
    def test_optional_asset_tag(self) -> None:
        """`asset_tag` is optional; non-asset-keyed operations omit it from the message.

        Why:
            Some operations (`list_custom_fields`, `apply_schema`) aren't
            scoped to a single host. The message should not say
            "asset_tag=None" — it should simply omit the field.
        """
        err = NetboxQueryError(operation="list_x", cause=RuntimeError("e"))
        assert err.asset_tag is None
        assert "asset_tag" not in str(err)


class TestHostNotFoundError:
    """`HostNotFoundError` — distinct from `NetboxQueryError` because the
    caller's response differs (fix Netbox data vs. retry the request)."""

    @pytest.mark.fast
    def test_carries_asset_tag(self) -> None:
        """The error carries the asset tag and renders it in `str()`."""
        err = HostNotFoundError("SN-001")
        assert err.asset_tag == "SN-001"
        assert "SN-001" in str(err)


class TestSchemaError:
    """`SchemaError` — raised when `apply_schema` detects unrecoverable drift.

    The `observed` payload captures what Netbox actually has so an
    operator can decide how to reconcile (manual edit vs. ADR-blessed
    schema bump).
    """

    @pytest.mark.fast
    def test_carries_field_and_detail(self) -> None:
        """The error carries `field_name`, `detail`, and `observed` payload."""
        err = SchemaError(
            field_name="bf3_mode",
            detail="type changed",
            observed={"name": "bf3_mode", "type": "text"},
        )
        assert err.field_name == "bf3_mode"
        assert err.detail == "type changed"
        assert err.observed == {"name": "bf3_mode", "type": "text"}
        assert "bf3_mode" in str(err)

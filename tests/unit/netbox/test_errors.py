"""Tests for `host_config.netbox.errors`."""

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
    @pytest.mark.fast
    def test_all_extend_host_config_error(self) -> None:
        assert issubclass(NetboxError, HostConfigError)
        assert issubclass(NetboxQueryError, NetboxError)
        assert issubclass(HostNotFoundError, NetboxError)
        assert issubclass(SchemaError, NetboxError)


class TestNetboxQueryError:
    @pytest.mark.fast
    def test_carries_operation_and_cause(self) -> None:
        cause = RuntimeError("boom")
        err = NetboxQueryError(operation="get_device", cause=cause, asset_tag="SN-001")
        assert err.operation == "get_device"
        assert err.cause is cause
        assert err.asset_tag == "SN-001"
        assert "SN-001" in str(err)
        assert "get_device" in str(err)

    @pytest.mark.fast
    def test_optional_asset_tag(self) -> None:
        err = NetboxQueryError(operation="list_x", cause=RuntimeError("e"))
        assert err.asset_tag is None
        assert "asset_tag" not in str(err)


class TestHostNotFoundError:
    @pytest.mark.fast
    def test_carries_asset_tag(self) -> None:
        err = HostNotFoundError("SN-001")
        assert err.asset_tag == "SN-001"
        assert "SN-001" in str(err)


class TestSchemaError:
    @pytest.mark.fast
    def test_carries_field_and_detail(self) -> None:
        err = SchemaError(
            field_name="bf3_mode",
            detail="type changed",
            observed={"name": "bf3_mode", "type": "text"},
        )
        assert err.field_name == "bf3_mode"
        assert err.detail == "type changed"
        assert err.observed == {"name": "bf3_mode", "type": "text"}
        assert "bf3_mode" in str(err)

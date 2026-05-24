"""Tests for the error hierarchy."""

from __future__ import annotations

import pytest

from host_config.errors import HostConfigError
from host_config.models.errors import InvariantError, ModelError


class TestHierarchy:
    @pytest.mark.fast
    def test_model_error_extends_host_config_error(self) -> None:
        """ModelError is catchable as HostConfigError."""
        assert issubclass(ModelError, HostConfigError)

    @pytest.mark.fast
    def test_invariant_error_extends_model_error(self) -> None:
        """InvariantError is catchable as ModelError (and HostConfigError)."""
        assert issubclass(InvariantError, ModelError)
        assert issubclass(InvariantError, HostConfigError)


class TestInvariantError:
    @pytest.mark.fast
    def test_carries_invariant_and_detail(self) -> None:
        """The raised exception exposes both attributes for programmatic handling."""
        err = InvariantError("ns-nic-count", "expected 2, got 1")
        assert err.invariant == "ns-nic-count"
        assert err.detail == "expected 2, got 1"

    @pytest.mark.fast
    def test_str_includes_invariant_id(self) -> None:
        """str() of the exception names the invariant for log-grep usefulness."""
        err = InvariantError("default-gateway-count", "got 2")
        s = str(err)
        assert "default-gateway-count" in s
        assert "got 2" in s

    @pytest.mark.fast
    def test_catchable_as_host_config_error(self) -> None:
        """`except HostConfigError` catches every model error variant."""
        with pytest.raises(HostConfigError):
            raise InvariantError("test-invariant", "test detail")

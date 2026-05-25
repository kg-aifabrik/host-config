"""Tests for the model-layer error hierarchy in `host_config.models.errors`.

Unit-level. Verifies that:

- The class hierarchy is set up correctly so callers can catch at the
  level of granularity they need (everything model-related; everything
  host-config-related; or one specific kind).
- `InvariantError` carries its `invariant` and `detail` attributes so
  callers can branch on the invariant ID programmatically (used by
  fixtures and the renderer's error-translation middleware in M2-5).

See `src/host_config/models/errors.py` for the types under test.
"""

from __future__ import annotations

import pytest

from host_config.errors import HostConfigError
from host_config.models.errors import InvariantError, ModelError


class TestHierarchy:
    """Inheritance relationships across the model-error hierarchy.

    These tests guard against an accidental refactor that breaks
    `except HostConfigError` catching all our errors (which the
    renderer's FastAPI exception handler relies on).
    """

    @pytest.mark.fast
    def test_model_error_extends_host_config_error(self) -> None:
        """`ModelError` is catchable as `HostConfigError`.

        Why:
            Code in modules that don't import `ModelError` should still
            be able to catch our errors via the package-wide base class.
        """
        assert issubclass(ModelError, HostConfigError)

    @pytest.mark.fast
    def test_invariant_error_extends_model_error(self) -> None:
        """`InvariantError` is catchable as `ModelError` (and `HostConfigError`).

        Why:
            Two-level catchability — operators of the FastAPI service
            translate `ModelError` to a 422 envelope; the lower-level
            `InvariantError` carries the offending invariant ID.
        """
        assert issubclass(InvariantError, ModelError)
        assert issubclass(InvariantError, HostConfigError)


class TestInvariantError:
    """Construction and surface of `InvariantError`.

    The class is small but its attributes are part of the contract the
    renderer relies on; these tests pin them down.
    """

    @pytest.mark.fast
    def test_carries_invariant_and_detail(self) -> None:
        """Both attributes are exposed for programmatic handling.

        Approach:
            Construct with two distinct strings; read them back via the
            instance attributes (not just the message).
        """
        err = InvariantError("ns-nic-count", "expected 2, got 1")
        assert err.invariant == "ns-nic-count"
        assert err.detail == "expected 2, got 1"

    @pytest.mark.fast
    def test_str_includes_invariant_id(self) -> None:
        """`str(err)` includes the invariant ID and the detail.

        Why:
            Logs use `str(err)` for the rendered message. A reader
            grepping for "default-gateway-count" should find the line
            without having to dump the full exception object.
        """
        err = InvariantError("default-gateway-count", "got 2")
        s = str(err)
        assert "default-gateway-count" in s
        assert "got 2" in s

    @pytest.mark.fast
    def test_catchable_as_host_config_error(self) -> None:
        """`raise InvariantError(...)` is caught by `except HostConfigError`.

        Why:
            End-to-end check on the inheritance chain — `pytest.raises`
            uses isinstance() under the hood, so this verifies the same
            mechanism a real caller would use.
        """
        with pytest.raises(HostConfigError):
            raise InvariantError("test-invariant", "test detail")

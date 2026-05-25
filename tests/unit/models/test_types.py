"""Tests for `host_config.models.types`.

Unit-level. Covers the `MacAddress` validated string type and the
module-level numeric constants (MTU bounds, VLAN ID bounds).

The MAC validator is exercised here via a tiny throwaway Pydantic model
(`_MacHolder`) because `Annotated` types only run their validators when
they're a field on a `BaseModel` — not when used standalone.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from host_config.models.types import (
    MAX_MTU,
    MAX_VLAN_ID,
    MIN_MTU,
    MIN_VLAN_ID,
    MacAddress,
)


class _MacHolder(BaseModel):
    """Wrapper model for testing the `MacAddress` type in isolation.

    Why:
        Pydantic's `Annotated[str, BeforeValidator(...)]` only runs the
        validator when used as a field. Constructing `MacAddress(...)`
        directly doesn't trigger validation. This helper gives us a
        minimal model field to drive the validator under test.
    """

    mac: MacAddress


class TestMacAddressValidation:
    """Boundary behavior of the `MacAddress` validated string type.

    The canonical form we accept is lowercase colon-separated 6-octet
    hex (e.g., `aa:bb:cc:00:00:01`). Other common formats (hyphenated,
    Cisco-dotted, uppercase) are rejected at the boundary so the rest
    of the codebase can rely on one canonical shape.
    """

    @pytest.mark.fast
    @pytest.mark.parametrize(
        "value",
        [
            "aa:bb:cc:00:00:01",
            "00:00:00:00:00:00",
            "ff:ff:ff:ff:ff:ff",
            "12:34:56:78:9a:bc",
        ],
    )
    def test_canonical_lowercase_accepted(self, value: str) -> None:
        """A canonical lowercase MAC passes through unchanged.

        Approach:
            Each parametrized value exercises a different corner of the
            valid space (alphabetic, all-zero, all-one, mixed alphanum).
        """
        h = _MacHolder(mac=value)
        assert h.mac == value

    @pytest.mark.fast
    def test_uppercase_normalized_to_lowercase(self) -> None:
        """Uppercase MACs are lowercased; the model field stores the canonical form.

        Why:
            Netbox returns MACs in mixed case in some API paths; the
            renderer compares MACs as strings, so we normalize at the
            boundary to avoid case-mismatch surprises downstream.
        """
        h = _MacHolder(mac="AA:BB:CC:00:00:01")
        assert h.mac == "aa:bb:cc:00:00:01"

    @pytest.mark.fast
    def test_surrounding_whitespace_stripped(self) -> None:
        """Leading/trailing whitespace is stripped before validation.

        Why:
            Common when MACs come from CSV imports, OEM manifests, or
            hand-edited YAML; stripping in the validator means callers
            don't have to remember to.
        """
        h = _MacHolder(mac="  aa:bb:cc:00:00:01  ")
        assert h.mac == "aa:bb:cc:00:00:01"

    @pytest.mark.fast
    @pytest.mark.parametrize(
        "value",
        [
            "aa-bb-cc-00-00-01",  # hyphen-separated (Windows / IEEE alt form)
            "aabb.ccdd.eeff",  # Cisco dotted-pair form
            "aa:bb:cc:00:00",  # 5 octets — short
            "aa:bb:cc:00:00:01:02",  # 7 octets — long
            "gg:bb:cc:00:00:01",  # non-hex character ('g')
            "",  # empty
            "not a mac",  # arbitrary text
        ],
    )
    def test_malformed_string_raises(self, value: str) -> None:
        """Non-canonical formats and gibberish raise a clean validation error.

        Approach:
            Each parametrized value exercises a different way to be
            wrong. The validator's error message mentions either "MAC"
            or "invalid" — assertion is loose enough to survive minor
            wording tweaks.
        """
        with pytest.raises(ValidationError) as exc_info:
            _MacHolder(mac=value)
        assert "MAC" in str(exc_info.value) or "invalid" in str(exc_info.value).lower()

    @pytest.mark.fast
    @pytest.mark.parametrize(
        "value",
        [12345, None, 1.5, ["aa:bb:cc:00:00:01"], b"aa:bb:cc:00:00:01"],
    )
    def test_non_string_raises(self, value: object) -> None:
        """Non-string inputs (int, None, float, list, bytes) are rejected.

        Why:
            The validator raises `ValueError` (which Pydantic wraps into
            `ValidationError`) rather than `TypeError` because Pydantic
            v2 handles `ValueError` more uniformly in error envelopes.
            See `_validate_mac` in `types.py`.
        """
        with pytest.raises(ValidationError):
            _MacHolder(mac=value)  # type: ignore[arg-type]


class TestConstants:
    """Sanity-check the platform-level numeric constants.

    These are tiny tests — they exist mainly so a future contributor
    who reduces `MAX_MTU` below `MIN_MTU` gets a red test, not a
    confusing downstream Pydantic error.
    """

    @pytest.mark.fast
    def test_mtu_bounds_sensible(self) -> None:
        """MTU range covers Ethernet 1500 to jumbo 9216 and is non-empty."""
        assert MIN_MTU == 1500
        assert MAX_MTU == 9216
        assert MIN_MTU < MAX_MTU

    @pytest.mark.fast
    def test_vlan_id_bounds_sensible(self) -> None:
        """VLAN ID range is 1..4094 per 802.1Q (excludes reserved 0 and 4095)."""
        assert MIN_VLAN_ID == 1
        assert MAX_VLAN_ID == 4094

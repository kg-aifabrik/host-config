"""Tests for `host_config.models.types`.

Covers the `MacAddress` validated string and the module-level constants.
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


# Helper model wrapping the type — Pydantic can't validate Annotated types
# in isolation; they must be a field on a BaseModel for validators to fire.
class _MacHolder(BaseModel):
    mac: MacAddress


class TestMacAddressValidation:
    """Boundary behavior of the MacAddress type."""

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
        """Canonical lowercase MAC strings pass through unchanged."""
        h = _MacHolder(mac=value)
        assert h.mac == value

    @pytest.mark.fast
    def test_uppercase_normalized_to_lowercase(self) -> None:
        """Uppercase letters are lowercased by the validator."""
        h = _MacHolder(mac="AA:BB:CC:00:00:01")
        assert h.mac == "aa:bb:cc:00:00:01"

    @pytest.mark.fast
    def test_surrounding_whitespace_stripped(self) -> None:
        """Surrounding whitespace is stripped."""
        h = _MacHolder(mac="  aa:bb:cc:00:00:01  ")
        assert h.mac == "aa:bb:cc:00:00:01"

    @pytest.mark.fast
    @pytest.mark.parametrize(
        "value",
        [
            "aa-bb-cc-00-00-01",  # hyphen-separated
            "aabb.ccdd.eeff",  # Cisco dotted
            "aa:bb:cc:00:00",  # 5 octets
            "aa:bb:cc:00:00:01:02",  # 7 octets
            "gg:bb:cc:00:00:01",  # non-hex
            "",
            "not a mac",
        ],
    )
    def test_malformed_string_raises(self, value: str) -> None:
        """Non-canonical formats and gibberish raise a clean validation error."""
        with pytest.raises(ValidationError) as exc_info:
            _MacHolder(mac=value)
        assert "MAC" in str(exc_info.value) or "invalid" in str(exc_info.value).lower()

    @pytest.mark.fast
    @pytest.mark.parametrize(
        "value",
        [12345, None, 1.5, ["aa:bb:cc:00:00:01"], b"aa:bb:cc:00:00:01"],
    )
    def test_non_string_raises(self, value: object) -> None:
        """Non-string inputs raise a validation error."""
        with pytest.raises(ValidationError):
            _MacHolder(mac=value)  # type: ignore[arg-type]


class TestConstants:
    """The platform-level numeric constants."""

    @pytest.mark.fast
    def test_mtu_bounds_sensible(self) -> None:
        """MTU range covers Ethernet 1500 to jumbo 9216."""
        assert MIN_MTU == 1500
        assert MAX_MTU == 9216
        assert MIN_MTU < MAX_MTU

    @pytest.mark.fast
    def test_vlan_id_bounds_sensible(self) -> None:
        """VLAN ID range is 1..4094 per 802.1Q."""
        assert MIN_VLAN_ID == 1
        assert MAX_VLAN_ID == 4094

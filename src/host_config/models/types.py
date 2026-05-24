"""Shared scalar types used across the model package.

This module is NOT for domain models (`HostIntent`, `Bond`, etc.) — those
live in their own files. Only narrow, reusable scalar types belong here.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BeforeValidator

# IEEE 802 MAC address in canonical lowercase colon-separated form,
# e.g. "aa:bb:cc:00:00:01". We deliberately reject other common formats
# (hyphen-separated, dotted, uppercase) at the boundary so the rest of
# the codebase can rely on one shape.
_MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")


def _validate_mac(v: object) -> str:
    """Coerce input into a canonical lowercase MAC string or raise.

    Approach:
        Accepts a string. Lowercases and strips whitespace. Asserts the
        canonical 6-octet colon-separated form. Anything else raises
        `ValueError` so Pydantic wraps it into `ValidationError` cleanly.
        Non-string inputs also raise `ValueError` (not `TypeError`) for
        the same reason — Pydantic's exception wrapping is friendliest
        to `ValueError`.

    Args:
        v: Candidate value from the user/Netbox.

    Returns:
        The canonical lowercase MAC string.

    Raises:
        ValueError: `v` is not a string or does not match the canonical format.

    Scenarios:
        - Happy path: "aa:bb:cc:00:00:01" → unchanged
        - Happy path: "AA:BB:CC:00:00:01" → lowercased
        - Happy path: "  aa:bb:cc:00:00:01  " → stripped
        - Reject: "aa-bb-cc-00-00-01" (hyphen-separated)
        - Reject: "aabb.ccdd.eeff" (Cisco dotted)
        - Reject: "not a mac"
        - Reject: 12345 (not a string)
    """
    if not isinstance(v, str):
        raise ValueError(f"MAC address must be a string, got {type(v).__name__}")
    v = v.lower().strip()
    if not _MAC_RE.match(v):
        raise ValueError(
            f"invalid MAC address {v!r}; expected canonical form like 'aa:bb:cc:00:00:01'"
        )
    return v


#: A validated MAC string. Use this anywhere a MAC is expected so Pydantic
#: enforces the canonical form at construction time.
MacAddress = Annotated[str, BeforeValidator(_validate_mac)]


# Standard MTU bounds for Ethernet on this platform. The lower bound is
# Ethernet's classic 1500; the upper bound covers jumbo frames with
# typical hardware headroom (9216 = 9000 payload + headers).
MIN_MTU = 1500
MAX_MTU = 9216

# 802.1Q VLAN ID range. ID 0 and 4095 are reserved; we accept 1..4094.
MIN_VLAN_ID = 1
MAX_VLAN_ID = 4094


__all__ = ["MAX_MTU", "MAX_VLAN_ID", "MIN_MTU", "MIN_VLAN_ID", "MacAddress"]

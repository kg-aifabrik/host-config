"""Shared fixtures for integration tests.

Integration tests share the same "live Netbox or skip" mechanism as
component tests. We re-export the fixtures here so pytest discovery
finds them without import gymnastics.
"""

from __future__ import annotations

from tests.component.conftest import (  # noqa: F401  -- re-exported as fixtures
    netbox_client,
    netbox_token,
    netbox_url,
    pytest_collection_modifyitems,
)

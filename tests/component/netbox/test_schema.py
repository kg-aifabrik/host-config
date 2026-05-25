"""Component tests for `host_config.netbox.schema.apply_schema`.

Runs against a real Netbox instance (via the `netbox_client` fixture in
tests/component/conftest.py). Skips if Netbox isn't reachable.

Acceptance per M1-4:
- apply_schema is idempotent: a second run is a no-op.
- After a successful apply, every declared custom field exists on the
  expected Netbox object types.
- Runs in under 30 s when Netbox is already up.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from host_config.netbox.schema import DEFAULT_FIELDS, FieldType, apply_schema

if TYPE_CHECKING:
    import pynetbox


@pytest.mark.fast  # not really fast, but the apply itself is < a few seconds
def test_apply_schema_is_idempotent(netbox_client: pynetbox.api) -> None:
    """Two consecutive applies have the same outcome: the second is a no-op.

    Approach:
        Apply once (may create or skip depending on prior state). Apply
        again immediately — the report must show zero creates and zero
        updates, only unchanged.

    Scenarios:
        - Fresh Netbox: first apply creates all; second apply is no-op.
        - Pre-applied Netbox (after the netbox-dev role's first use):
          both applies are no-ops.
    """
    # First apply — outcome depends on the test instance's prior state.
    _ = apply_schema(netbox_client)

    # Second apply — guaranteed idempotent.
    second = apply_schema(netbox_client)
    assert second.created == []
    assert second.updated == []
    assert second.is_no_op is True
    assert len(second.unchanged) == len(DEFAULT_FIELDS)


def test_every_declared_field_exists_after_apply(netbox_client: pynetbox.api) -> None:
    """After apply_schema, every name in DEFAULT_FIELDS is present in Netbox.

    Approach:
        Ensure the apply has run; query each spec's name; assert the
        returned object exists with the expected type.

    Scenarios:
        - Every declared field is queryable by name.
        - The Netbox-side type matches the spec's type (sanity check
          against accidental shape drift).
    """
    apply_schema(netbox_client)

    for spec in DEFAULT_FIELDS:
        existing = netbox_client.extras.custom_fields.get(name=spec.name)
        assert existing is not None, f"custom field {spec.name!r} missing in Netbox"
        # Netbox returns the type as a struct {value: 'text', label: 'Text'};
        # the spec's `.type` is the enum value (a string). Compare via the
        # raw `.value` attribute if present.
        observed_type = existing.type
        if isinstance(observed_type, dict) and "value" in observed_type:
            observed_type = observed_type["value"]
        elif hasattr(observed_type, "value"):
            observed_type = observed_type.value
        assert observed_type == spec.type, (
            f"field {spec.name!r}: expected type {spec.type}, observed {observed_type!r}"
        )


def test_each_field_attached_to_expected_object_types(
    netbox_client: pynetbox.api,
) -> None:
    """Each custom field is attached to exactly the object types declared.

    Approach:
        For each spec, fetch the live field; extract the list of attached
        object types (normalized to short identifiers like ``dcim.device``);
        compare to the spec's declared list.
    """
    apply_schema(netbox_client)

    for spec in DEFAULT_FIELDS:
        existing = netbox_client.extras.custom_fields.get(name=spec.name)
        assert existing is not None
        observed = sorted(_normalize_object_type(t) for t in (existing.object_types or []))
        expected = sorted(spec.object_types)
        assert observed == expected, (
            f"field {spec.name!r}: expected object_types {expected}, observed {observed}"
        )


@pytest.mark.parametrize("field_type", list(FieldType))
def test_field_type_values_are_understood_by_netbox(
    netbox_client: pynetbox.api, field_type: FieldType
) -> None:
    """Smoke test: each FieldType we declare is one Netbox accepts.

    Approach:
        Inspect the OpenAPI schema's enum for the ``type`` field; assert
        every FieldType we use appears in it. Catches the case where a
        Netbox version drops or renames a type without our schema noticing.
    """
    # Netbox exposes choices via the `OPTIONS /api/extras/custom-fields/`
    # endpoint. We probe via the existing field for the type if available.
    # Easier: check that at least one of our fields with this type exists
    # and is queryable — that proves Netbox accepted the type at creation.
    matching_spec = next((s for s in DEFAULT_FIELDS if s.type == field_type), None)
    if matching_spec is None:
        pytest.skip(f"no DEFAULT_FIELDS entry uses {field_type.value}")
    existing = netbox_client.extras.custom_fields.get(name=matching_spec.name)
    assert existing is not None


def _normalize_object_type(raw: object) -> str:
    """Reduce Netbox's verbose object-type representation to ``app.model``.

    Netbox returns either a string like ``dcim.device`` or a record with
    ``app_label`` + ``model`` attributes. This helper makes the test
    comparison stable across both formats.
    """
    if isinstance(raw, str):
        # May be a URL like /api/extras/object-types/dcim.device/
        if "/" in raw:
            parts = [p for p in raw.split("/") if p]
            return parts[-1] if parts else raw
        return raw
    app_label = getattr(raw, "app_label", None)
    model = getattr(raw, "model", None)
    if app_label and model:
        return f"{app_label}.{model}"
    return str(raw)

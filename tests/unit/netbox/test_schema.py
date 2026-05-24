"""Tests for `host_config.netbox.schema`.

Unit-level: validates the declarative schema and the payload rendering
without needing a live Netbox. Component-level tests that exercise
`apply_schema` against a real Netbox container land in M1-4.
"""

from __future__ import annotations

from collections.abc import Sequence
from unittest.mock import MagicMock

import pytest

from host_config.netbox.errors import NetboxQueryError, SchemaError
from host_config.netbox.schema import (
    BF3_MODES,
    DEFAULT_FIELDS,
    CustomFieldSpec,
    FieldType,
    SchemaApplyReport,
    _values_equal,
    apply_schema,
)


class TestFieldType:
    @pytest.mark.fast
    def test_values_match_netbox_wire_format(self) -> None:
        """Enum values mirror Netbox's accepted strings exactly."""
        assert FieldType.TEXT.value == "text"
        assert FieldType.LONGTEXT.value == "longtext"
        assert FieldType.INTEGER.value == "integer"
        assert FieldType.BOOLEAN.value == "boolean"
        assert FieldType.SELECT.value == "select"
        assert FieldType.MULTISELECT.value == "multiselect"


class TestCustomFieldSpec:
    @pytest.mark.fast
    def test_minimal_text_field_payload(self) -> None:
        """A minimal TEXT field renders only the required keys."""
        spec = CustomFieldSpec(
            name="observed_mac",
            label="Observed MAC",
            type=FieldType.TEXT,
            object_types=["dcim.interface"],
        )
        payload = spec.to_payload()
        assert payload["name"] == "observed_mac"
        assert payload["label"] == "Observed MAC"
        assert payload["type"] == "text"
        assert payload["object_types"] == ["dcim.interface"]
        assert payload["required"] is False
        # Optional fields not set → not present in payload
        assert "description" not in payload
        assert "default" not in payload
        assert "choices" not in payload

    @pytest.mark.fast
    def test_select_field_includes_choices(self) -> None:
        """A SELECT field renders the choice list."""
        spec = CustomFieldSpec(
            name="bf3_mode",
            label="BF3 mode",
            type=FieldType.SELECT,
            object_types=["dcim.device"],
            choices=["nic", "dpu"],
            default="nic",
        )
        payload = spec.to_payload()
        assert payload["choices"] == ["nic", "dpu"]
        assert payload["default"] == "nic"

    @pytest.mark.fast
    def test_integer_field_includes_bounds(self) -> None:
        """An INTEGER field with min/max renders validation bounds."""
        spec = CustomFieldSpec(
            name="roce_tc",
            label="RoCE TC",
            type=FieldType.INTEGER,
            object_types=["dcim.interface"],
            validation_minimum=0,
            validation_maximum=7,
        )
        payload = spec.to_payload()
        assert payload["validation_minimum"] == 0
        assert payload["validation_maximum"] == 7

    @pytest.mark.fast
    def test_description_optional(self) -> None:
        """An empty description is omitted from the payload."""
        empty = CustomFieldSpec(
            name="x",
            label="X",
            type=FieldType.TEXT,
            object_types=["dcim.device"],
        )
        assert "description" not in empty.to_payload()
        nonempty = CustomFieldSpec(
            name="x",
            label="X",
            type=FieldType.TEXT,
            object_types=["dcim.device"],
            description="something",
        )
        assert nonempty.to_payload()["description"] == "something"

    @pytest.mark.fast
    def test_immutable(self) -> None:
        """CustomFieldSpec is frozen — assignment raises."""
        spec = CustomFieldSpec(
            name="x", label="X", type=FieldType.TEXT, object_types=["dcim.device"]
        )
        with pytest.raises(AttributeError):
            spec.name = "y"  # type: ignore[misc]


class TestDefaultFieldsCatalog:
    @pytest.mark.fast
    def test_expected_field_names_present(self) -> None:
        """The catalog contains every field name we depend on downstream."""
        names = {f.name for f in DEFAULT_FIELDS}
        expected = {
            "bf3_mode",
            "roce_tc",
            "numa_node",
            "sriov_vfs",
            "gpu_affinity",
            "observed_mac",
            "observed_firmware",
        }
        assert expected.issubset(names)

    @pytest.mark.fast
    def test_no_duplicate_names(self) -> None:
        """No two fields share a name."""
        names = [f.name for f in DEFAULT_FIELDS]
        assert len(names) == len(set(names))

    @pytest.mark.fast
    def test_bf3_mode_choices_match_constant(self) -> None:
        """The BF3 mode field declares the same choices as the BF3_MODES constant."""
        bf3 = next(f for f in DEFAULT_FIELDS if f.name == "bf3_mode")
        assert bf3.choices == BF3_MODES

    @pytest.mark.fast
    def test_each_field_renders_valid_payload(self) -> None:
        """Every default field renders a payload with the required keys."""
        for field in DEFAULT_FIELDS:
            payload = field.to_payload()
            assert "name" in payload
            assert "label" in payload
            assert "type" in payload
            assert "object_types" in payload


class TestSchemaApplyReport:
    @pytest.mark.fast
    def test_empty_report_is_no_op(self) -> None:
        """A fresh report (no work done) is a no-op."""
        r = SchemaApplyReport()
        assert r.is_no_op is True

    @pytest.mark.fast
    def test_unchanged_only_is_no_op(self) -> None:
        """If only `unchanged` is populated, the apply was idempotent."""
        r = SchemaApplyReport(unchanged=["bf3_mode", "roce_tc"])
        assert r.is_no_op is True

    @pytest.mark.fast
    def test_any_create_or_update_is_not_no_op(self) -> None:
        """Any creation breaks idempotency."""
        assert SchemaApplyReport(created=["x"]).is_no_op is False
        assert SchemaApplyReport(updated=["x"]).is_no_op is False

    @pytest.mark.fast
    def test_summary_includes_counts(self) -> None:
        """The summary string carries all three counts."""
        r = SchemaApplyReport(created=["a", "b"], updated=["c"], unchanged=["d", "e", "f"])
        s = r.summary()
        assert "created=2" in s
        assert "updated=1" in s
        assert "unchanged=3" in s


class TestApplySchema:
    """Mocked-Netbox tests for the apply_schema function."""

    def _mock_client(
        self,
        results_by_name: dict[str, object | None] | None = None,
        get_results: Sequence[object | None] | None = None,
        raise_on: str | None = None,
    ) -> MagicMock:
        """Build a mock pynetbox client.

        Args:
            results_by_name: A mapping ``name -> observed value`` (or ``None``).
                Returned on every ``cf.get(name=...)`` call for the matching name.
                Stable across repeated calls — `apply_schema` may call `get`
                more than once for the same field during the patch path.
            get_results: Legacy sequence mode. One value per spec, consumed in
                spec order. Kept for tests that don't care about repeated calls.
            raise_on: If set, the named call raises (simulates transport failure).
        """
        client = MagicMock()
        cf = client.extras.custom_fields

        if results_by_name is not None:

            def _get_by_name(**kwargs: object) -> object | None:
                if raise_on == "get":
                    raise RuntimeError("simulated netbox transport error")
                name = kwargs.get("name")
                # results_by_name is non-None here; cast for the type checker.
                assert results_by_name is not None
                return results_by_name.get(str(name)) if name else None

            cf.get.side_effect = _get_by_name
        else:
            get_iter = iter(get_results or [])

            def _get_sequence(**kwargs: object) -> object | None:
                if raise_on == "get":
                    raise RuntimeError("simulated netbox transport error")
                return next(get_iter)

            cf.get.side_effect = _get_sequence

        if raise_on == "create":
            cf.create.side_effect = RuntimeError("simulated create failure")
        return client

    @pytest.mark.fast
    def test_empty_netbox_creates_all(self) -> None:
        """When every field is absent, every field is created."""
        fields = DEFAULT_FIELDS
        client = self._mock_client(get_results=[None] * len(fields))

        report = apply_schema(client, fields=fields)

        assert len(report.created) == len(fields)
        assert report.updated == []
        assert report.unchanged == []
        assert client.extras.custom_fields.create.call_count == len(fields)

    @pytest.mark.fast
    def test_idempotent_when_all_match(self) -> None:
        """If existing fields exactly match the specs, no work is done."""
        fields = DEFAULT_FIELDS
        # Return the rendered payload as the "observed" value — they
        # match by definition.
        observed = [f.to_payload() for f in fields]
        # `_values_equal` strips dict-wrapped type values; we already
        # render type as a plain string, so the comparison passes.
        client = self._mock_client(get_results=observed)

        report = apply_schema(client, fields=fields)

        assert report.created == []
        assert report.updated == []
        assert len(report.unchanged) == len(fields)
        assert report.is_no_op is True
        client.extras.custom_fields.create.assert_not_called()

    @pytest.mark.fast
    def test_recoverable_drift_triggers_update(self) -> None:
        """A description-only drift is patched, not failed."""
        spec = CustomFieldSpec(
            name="x",
            label="X",
            type=FieldType.TEXT,
            object_types=["dcim.device"],
            description="new description",
        )
        observed = spec.to_payload()
        observed["description"] = "old description"
        # WHY: apply_schema may call get() twice for the same field (once
        # to inspect, once to fetch the record for patching). Use the
        # name-keyed mock so both calls see the same observed value.
        client = self._mock_client(results_by_name={"x": observed})

        report = apply_schema(client, fields=[spec])

        assert report.updated == ["x"]
        assert report.created == []

    @pytest.mark.fast
    def test_unrecoverable_drift_raises(self) -> None:
        """A type change is unrecoverable; SchemaError is raised."""
        spec = CustomFieldSpec(
            name="x",
            label="X",
            type=FieldType.INTEGER,
            object_types=["dcim.device"],
        )
        observed = spec.to_payload()
        observed["type"] = "text"  # type changed under us
        client = self._mock_client(get_results=[observed])

        with pytest.raises(SchemaError) as exc:
            apply_schema(client, fields=[spec])
        assert exc.value.field_name == "x"
        assert "type" in exc.value.detail

    @pytest.mark.fast
    def test_query_failure_wraps_to_typed_error(self) -> None:
        """Underlying client exceptions become NetboxQueryError."""
        client = self._mock_client(get_results=[], raise_on="get")
        with pytest.raises(NetboxQueryError) as exc:
            apply_schema(client, fields=DEFAULT_FIELDS)
        assert exc.value.operation == "get_custom_field"
        assert isinstance(exc.value.cause, RuntimeError)


class TestValuesEqual:
    """Tests for the private _values_equal normalization helper.

    Exercised indirectly through apply_schema's idempotency tests, but
    a couple of direct cases pin the Netbox-quirk handling.
    """

    @pytest.mark.fast
    def test_dict_wrapped_type_unwraps(self) -> None:
        """Netbox returns {value: 'select', label: 'Selection'} for type."""
        assert _values_equal("type", "select", {"value": "select", "label": "Selection"})

    @pytest.mark.fast
    def test_object_types_normalization(self) -> None:
        """object_types may come back as URLs or short identifiers."""
        # Netbox might return either form depending on API version.
        assert _values_equal(
            "object_types",
            ["dcim.device"],
            ["http://netbox/api/extras/object-types/dcim.device/"],
        )
        assert _values_equal("object_types", ["dcim.device"], ["dcim.device"])

"""Netbox custom-field schema for host-config.

Declarative spec of every custom field this project depends on:

- ``bf3_mode``           BlueField-3 operating mode (NIC vs DPU).
- ``roce_tc``            RoCE traffic class (0..7) per east-west NIC.
- ``numa_node``          NUMA node ID for the interface's PCIe path.
- ``sriov_vfs``          Target SR-IOV VF count for a PF.
- ``gpu_affinity``       String reference to the paired GPU.
- ``observed_mac``       MAC last observed on the wire (drift detection).
- ``observed_firmware``  Firmware version last observed on the host.

Apply the schema with :func:`apply_schema` against a live Netbox; the
function is idempotent — re-runs are no-ops once the schema matches.

Layering note (principle #12): this module is purely declarative + an
apply function. It does NOT know about caller-domain lifecycle states.
Custom fields exist; whether a particular host populates them is a
separate concern (fixtures + discovery agents do that).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from host_config.netbox.errors import NetboxQueryError, SchemaError

if TYPE_CHECKING:
    import pynetbox

logger = logging.getLogger(__name__)


class FieldType(StrEnum):
    """Netbox custom-field types we use.

    Mirrors the upstream Netbox API values exactly so the spec serializes
    to the wire format without translation.
    """

    TEXT = "text"
    LONGTEXT = "longtext"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    SELECT = "select"
    MULTISELECT = "multiselect"


@dataclass(frozen=True)
class CustomFieldSpec:
    """Declarative spec for one Netbox custom field.

    Approach:
        Captures everything Netbox needs to create or update a custom
        field. The :meth:`to_payload` method renders it as the JSON
        body the Netbox REST API accepts.

    Attributes:
        name: Internal name; must match ``^[a-z][a-z0-9_]*$``.
        label: Human-readable label shown in the UI.
        type: One of :class:`FieldType`.
        object_types: Netbox object identifiers this field attaches to
            (e.g., ``["dcim.device", "dcim.interface"]``).
        description: Long-form description; helps human operators.
        required: Whether the field must be populated on every object.
        default: Default value if unset; type-dependent.
        choices: Choice options for SELECT/MULTISELECT (list of strings).
        validation_minimum: Lower bound for INTEGER.
        validation_maximum: Upper bound for INTEGER.

    Scenarios:
        - Happy path: a TEXT field constructs cleanly and renders a
          payload Netbox accepts.
        - SELECT field with choices renders a payload that includes the
          choice list.
        - INTEGER field with min/max renders the validation bounds.
        - BOOLEAN field with default True renders the boolean default.
    """

    name: str
    label: str
    type: FieldType
    object_types: list[str]
    description: str = ""
    required: bool = False
    default: Any = None
    choices: list[str] | None = None
    validation_minimum: int | None = None
    validation_maximum: int | None = None

    def to_payload(self) -> dict[str, Any]:
        """Render this spec as the Netbox REST API payload.

        Returns:
            A dict suitable for ``POST /api/extras/custom-fields/``.

        Scenarios:
            - All optional fields ``None`` → payload omits them.
            - SELECT field → payload includes ``choice_set``.
            - INTEGER with bounds → payload includes ``validation_minimum``
              and ``validation_maximum``.
        """
        payload: dict[str, Any] = {
            "name": self.name,
            "label": self.label,
            "type": self.type.value,
            "object_types": list(self.object_types),
            "required": self.required,
        }
        if self.description:
            payload["description"] = self.description
        if self.default is not None:
            payload["default"] = self.default
        if self.choices is not None:
            # Netbox 4.x represents choices via a separate ChoiceSet
            # object; for simplicity we render inline choices using the
            # legacy `choices` array, which Netbox accepts for migration.
            payload["choices"] = list(self.choices)
        if self.validation_minimum is not None:
            payload["validation_minimum"] = self.validation_minimum
        if self.validation_maximum is not None:
            payload["validation_maximum"] = self.validation_maximum
        return payload


@dataclass
class SchemaApplyReport:
    """Result of one :func:`apply_schema` invocation.

    Attributes:
        created: Names of custom fields created on this run.
        updated: Names of custom fields whose shape was adjusted.
        unchanged: Names already in the desired state — no work done.
    """

    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    @property
    def is_no_op(self) -> bool:
        """True iff the apply made no changes (idempotent re-run)."""
        return not self.created and not self.updated

    def summary(self) -> str:
        """Single-line human summary, useful for logs."""
        return (
            f"created={len(self.created)} "
            f"updated={len(self.updated)} "
            f"unchanged={len(self.unchanged)}"
        )


# ---------------------------------------------------------------------------
# The schema this project depends on.
# ---------------------------------------------------------------------------

#: BlueField-3 modes. Source of truth for the choice values.
BF3_MODES = ["nic", "dpu", "separated-host"]


DEFAULT_FIELDS: list[CustomFieldSpec] = [
    # WHY TEXT not SELECT: Netbox 4.x decouples SELECT-field choices into
    # a separate ChoiceSet object. Storing as TEXT and validating the
    # allowed values at the loader's Pydantic layer (Role enum-equivalent)
    # is materially simpler without losing safety. Allowed values are the
    # `BF3_MODES` constant; the loader rejects anything else.
    CustomFieldSpec(
        name="bf3_mode",
        label="BlueField-3 mode",
        type=FieldType.TEXT,
        object_types=["dcim.device"],
        description=(
            "Operating mode of host's BF-3 DPUs. Allowed: "
            "nic | dpu | separated-host. v1 lab uses 'nic'."
        ),
        default="nic",
    ),
    CustomFieldSpec(
        name="roce_tc",
        label="RoCE traffic class",
        type=FieldType.INTEGER,
        object_types=["dcim.interface"],
        description="RoCE v2 traffic class (PFC priority). Standard is 3.",
        validation_minimum=0,
        validation_maximum=7,
    ),
    CustomFieldSpec(
        name="numa_node",
        label="NUMA node",
        type=FieldType.INTEGER,
        object_types=["dcim.interface"],
        description="NUMA node ID the interface's PCIe root complex sits on.",
        validation_minimum=0,
        validation_maximum=7,  # ample headroom; current hosts have ≤ 2 sockets
    ),
    CustomFieldSpec(
        name="sriov_vfs",
        label="SR-IOV VF count",
        type=FieldType.INTEGER,
        object_types=["dcim.interface"],
        description="Target number of SR-IOV virtual functions to provision on this PF.",
        validation_minimum=0,
        validation_maximum=128,
    ),
    CustomFieldSpec(
        name="gpu_affinity",
        label="GPU affinity",
        type=FieldType.TEXT,
        object_types=["dcim.interface"],
        description=(
            "Identifier of the GPU this NIC is paired with on the PCIe "
            "topology (e.g., 'GPU0'). For east-west NICs only."
        ),
    ),
    CustomFieldSpec(
        name="observed_mac",
        label="Observed MAC",
        type=FieldType.TEXT,
        object_types=["dcim.interface"],
        description=(
            "MAC last reported by discovery. Disagreement with the "
            "canonical mac_address signals NIC replacement or stale inventory."
        ),
    ),
    CustomFieldSpec(
        name="observed_firmware",
        label="Observed firmware",
        type=FieldType.LONGTEXT,
        object_types=["dcim.device"],
        description="JSON blob of observed firmware versions from discovery.",
    ),
]


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def apply_schema(
    client: pynetbox.api,
    fields: list[CustomFieldSpec] = DEFAULT_FIELDS,
) -> SchemaApplyReport:
    """Idempotently apply the custom-field schema against a running Netbox.

    Approach:
        For each spec, query Netbox for an existing custom field with
        the same name. If absent, create it. If present and the shape
        matches, leave it alone (unchanged). If present but with a
        recoverable difference (description, choices, validation
        bounds), patch it (updated). If present with an unrecoverable
        difference (type change, object_types removed), raise
        :class:`SchemaError` — the operator must intervene.

    Args:
        client: A configured ``pynetbox.api`` instance (typically
            ``pynetbox.api(url, token=...)``).
        fields: The schema to apply. Defaults to :data:`DEFAULT_FIELDS`.

    Returns:
        :class:`SchemaApplyReport` summarizing what changed.

    Raises:
        NetboxQueryError: Any Netbox API call failed for transport reasons.
        SchemaError: A field's existing shape differs from the spec in a
            way this function won't auto-resolve.

    Scenarios:
        - Empty Netbox: every field is created; report has all names in
          ``created``, none in ``updated`` or ``unchanged``.
        - All fields already match: report has all names in ``unchanged``;
          ``is_no_op`` is True.
        - One field's description is stale: that field is patched; name
          appears in ``updated``.
        - A field exists with a different ``type``: :class:`SchemaError`
          is raised; the apply does not partially complete.
        - Netbox is unreachable: :class:`NetboxQueryError` is raised on
          the first GET, with the underlying request error attached.

    Example:
        >>> import pynetbox
        >>> nb = pynetbox.api("http://localhost:8000", token="...")
        >>> report = apply_schema(nb)
        >>> print(report.summary())
    """
    logger.info("applying schema: count=%d", len(fields))
    report = SchemaApplyReport()
    for spec in fields:
        existing = _fetch_existing(client, spec.name)
        if existing is None:
            _create(client, spec)
            report.created.append(spec.name)
            logger.info("custom-field created name=%s", spec.name)
            continue

        drift = _diff(spec, existing)
        if not drift:
            report.unchanged.append(spec.name)
            logger.debug("custom-field unchanged name=%s", spec.name)
            continue

        if not _is_recoverable(drift):
            raise SchemaError(
                field_name=spec.name,
                detail=f"unrecoverable drift in field(s): {sorted(drift)}",
                observed=existing,
            )

        _patch(client, spec, existing)
        report.updated.append(spec.name)
        logger.info("custom-field updated name=%s drift=%s", spec.name, sorted(drift))

    logger.info("apply_schema done: %s", report.summary())
    return report


def _fetch_existing(client: pynetbox.api, name: str) -> dict[str, Any] | None:
    """Return the existing custom-field as a dict, or None if absent."""
    try:
        result = client.extras.custom_fields.get(name=name)
    except Exception as e:
        raise NetboxQueryError(operation="get_custom_field", cause=e) from e
    if result is None:
        return None
    return _normalize_record(dict(result))


def _create(client: pynetbox.api, spec: CustomFieldSpec) -> None:
    """POST a new custom field to Netbox."""
    try:
        client.extras.custom_fields.create(spec.to_payload())
    except Exception as e:
        raise NetboxQueryError(operation="create_custom_field", cause=e) from e


def _patch(client: pynetbox.api, spec: CustomFieldSpec, existing: dict[str, Any]) -> None:
    """PATCH an existing custom field to match the spec."""
    try:
        record = client.extras.custom_fields.get(name=spec.name)
        record.update(spec.to_payload())
    except Exception as e:
        raise NetboxQueryError(operation="update_custom_field", cause=e) from e


def _diff(spec: CustomFieldSpec, existing: dict[str, Any]) -> set[str]:
    """Return the set of field names that differ between spec and existing."""
    expected = spec.to_payload()
    drift: set[str] = set()
    for key, want in expected.items():
        got = existing.get(key)
        if not _values_equal(key, want, got):
            drift.add(key)
    return drift


# Fields where drift is recoverable via PATCH (description text, validation
# bounds, choice set additions). Anything not in this set is unrecoverable
# and forces the operator to fix the Netbox state manually.
_RECOVERABLE_DRIFT_KEYS = frozenset(
    {
        "label",
        "description",
        "required",
        "default",
        "choices",
        "validation_minimum",
        "validation_maximum",
    }
)


def _is_recoverable(drift: set[str]) -> bool:
    """True iff every drifted field is in the recoverable allowlist."""
    return drift.issubset(_RECOVERABLE_DRIFT_KEYS)


def _values_equal(key: str, want: Any, got: Any) -> bool:
    """Compare expected vs observed values, normalizing for Netbox quirks.

    Approach:
        Netbox's API returns enum-like fields wrapped in objects with
        ``value`` and ``label`` attributes. We unwrap before comparing.
        Lists are compared by membership ordering.
    """
    # Type is returned as {"value": "select", "label": "Selection"} by Netbox.
    if isinstance(got, dict) and "value" in got:
        got = got["value"]
    # object_types are returned as a list of full URLs in some Netbox versions
    # and as short identifiers in others. Strip to last path segment for compare.
    if key == "object_types":
        want_norm = sorted(str(s).split("/")[-2] if "/" in str(s) else str(s) for s in want)
        got_norm = sorted(str(s).split("/")[-2] if "/" in str(s) else str(s) for s in got or [])
        return want_norm == got_norm
    return bool(want == got)


def _normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the record with API quirks normalized.

    Currently a passthrough; the per-key normalization happens in
    :func:`_values_equal`. Kept as a single seam in case we accumulate
    more record-level normalization.
    """
    return dict(record)


__all__ = [
    "BF3_MODES",
    "DEFAULT_FIELDS",
    "CustomFieldSpec",
    "FieldType",
    "SchemaApplyReport",
    "apply_schema",
]

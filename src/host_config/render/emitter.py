"""Renderer emitter: `HostIntent` → bytes.

Pure function that turns a validated `HostIntent` plus a file kind into
the on-the-wire bytes that cloud-init reads from the NoCloud datasource.

Determinism contract:
    Given the same `(intent, file_kind, now)` triple, `render_for`
    returns byte-identical output. The renderer enforces determinism
    by:

    - **Sorting** list-shaped intent fields by name before rendering.
      Upstream callers (the loader pulling from Netbox) make no
      promises about iteration order; pynetbox can return interfaces
      in any order. The renderer canonicalizes here so byte-equal
      goldens hold regardless of upstream order.
    - **Strict Jinja** — every variable the templates reference is
      provided. Missing variables raise `UndefinedError` instead of
      silently emitting empty strings.
    - **Newline-stable templates** — `trim_blocks` + `lstrip_blocks` +
      `keep_trailing_newline` are configured in `environment.py`.

The `now` parameter is injectable so tests pin time-dependent fields
(none exist today; threaded through for forward-compat with future
``generated-at`` markers that an SRE-style audit trail would carry).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from host_config.models.intent import HostIntent
from host_config.render.environment import make_environment


class FileKind(StrEnum):
    """The three cloud-init payloads cloud-init's NoCloud datasource reads.

    These names match the on-disk template filenames (sans ``.j2``) and
    the on-the-wire file names cloud-init expects. Keeping them in an
    enum closes the door on typos like ``"metadata"`` or ``"userdata"``
    that would silently render the wrong template — or worse, miss the
    template entirely and raise a confusing ``TemplateNotFound`` deep
    in the stack.
    """

    META_DATA = "meta-data"
    USER_DATA = "user-data"
    NETWORK_CONFIG = "network-config"


def _default_now() -> datetime:
    """The default ``now`` provider — wall-clock UTC.

    Approach:
        Pulled into a module-level function so tests can monkeypatch
        the entire renderer's clock if they ever need to. The function
        is *also* injectable per-call (the `now` parameter on
        `render_for`); the indirection lets blanket-patching coexist
        with per-test overrides.
    """
    return datetime.now(UTC)


def _canonicalize(intent: HostIntent) -> dict[str, Any]:
    """Project an intent to the template context, sorted for determinism.

    Approach:
        1. ``model_dump(mode='python')`` — preserves rich types
           (IPv4Interface, MacAddress) so the templates' ``{{ x }}``
           expressions get the type's `__str__`, not a pre-serialized
           form that might lose information.
        2. Sort list fields by ``name``. The renderer is the canonical
           order. Tests assume this order. Goldens depend on it.
    """
    ctx: dict[str, Any] = intent.model_dump(mode="python")
    ctx["ns_nics"] = sorted(ctx["ns_nics"], key=lambda n: n["name"])
    ctx["vlans"] = sorted(ctx["vlans"], key=lambda v: v["vlan_id"])
    ctx["roce_underlays"] = sorted(ctx["roce_underlays"], key=lambda n: n["name"])
    ctx["ib_underlays"] = sorted(ctx["ib_underlays"], key=lambda n: n["name"])
    # Bond.members is a list of strings — sorting keeps the emitted
    # `interfaces:` array stable even if upstream order shifts.
    ctx["bond"]["members"] = sorted(ctx["bond"]["members"])
    return ctx


def render_for(
    intent: HostIntent,
    file_kind: FileKind | str,
    *,
    now: datetime | None = None,
) -> bytes:
    """Render one cloud-init payload for the given intent.

    Args:
        intent: A fully validated `HostIntent`. The renderer trusts
            Pydantic / cross-field invariants — it does not re-validate.
        file_kind: Which of the three NoCloud files to emit. Accepts
            either the ``FileKind`` enum or its string value.
        now: Injected clock for tests. Defaults to UTC wall-clock.

    Returns:
        UTF-8 encoded bytes ready to write to disk or stream over HTTP.
        Always ends in a trailing newline (POSIX file convention; see
        ``keep_trailing_newline=True`` in `environment.py`).

    Raises:
        jinja2.UndefinedError: A template referenced a variable that
            wasn't supplied. Surfaces a bug in either the template or
            the model layer's serialization.
        jinja2.TemplateNotFound: The role + file_kind combination has
            no template on disk. Should be impossible for the two
            supported roles; would indicate a packaging bug.

    Why this signature:
        We return bytes (not str) because the next layer is an HTTP
        body. Encoding once here, at the boundary, keeps the
        FastAPI handler purely about wire concerns (status code,
        content-type) rather than re-encoding.
    """
    kind = FileKind(file_kind) if not isinstance(file_kind, FileKind) else file_kind
    when = now if now is not None else _default_now()

    env = make_environment()
    template = env.get_template(f"{intent.role.value}/{kind.value}.j2")

    ctx = _canonicalize(intent)
    # `now` is exposed to templates so a future audit-trail variant
    # can stamp `generated-at: {{ now.isoformat() }}`. Current
    # templates don't reference it — kept in the context unconditionally
    # so a template can opt in without an emitter change.
    ctx["now"] = when

    rendered = template.render(**ctx)
    return rendered.encode("utf-8")


__all__ = ["FileKind", "render_for"]

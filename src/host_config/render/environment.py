"""Jinja2 `Environment` factory for the renderer.

Centralizes every Jinja knob in one place so the renderer (M2-4) and
the tests share identical behavior. The only knob worth calling out:

- `undefined=StrictUndefined` — any reference to a variable the caller
  didn't pass raises `UndefinedError` at render time instead of
  silently emitting an empty string. This is load-bearing: a typo in a
  template would otherwise produce a syntactically valid but
  semantically wrong cloud-init payload (e.g., a missing gateway), and
  the cloud-init runtime would only catch it at boot. Strict undefined
  surfaces the bug at render time, where the stack trace points at the
  template line.

Other knobs we deliberately keep as Jinja defaults:

- `autoescape=False` — we emit YAML, not HTML. HTML autoescape would
  corrupt YAML special characters.
- `trim_blocks=True`, `lstrip_blocks=True` — strip the newline after a
  block tag and leading whitespace before a block tag. Without these,
  Jinja's `{% if %}` / `{% endif %}` blocks leave stray blank lines
  that break YAML indentation in subtle ways.
- `keep_trailing_newline=True` — POSIX files end in a newline. Our
  goldens (M2.5) compare byte-for-byte; this avoids a final-newline
  diff every test run.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

# The on-disk root of the per-role template tree. Resolved at import
# time so callers don't pay a filesystem walk per render.
TEMPLATES_ROOT: Path = Path(__file__).parent / "templates"


def make_environment(templates_root: Path | None = None) -> Environment:
    """Return a fresh Jinja2 ``Environment`` configured for the renderer.

    Args:
        templates_root: Override the on-disk root. Defaults to
            ``TEMPLATES_ROOT``. Tests use the override to point at
            fixture trees.

    Returns:
        A configured ``Environment`` with ``StrictUndefined`` and the
        whitespace-trimming knobs the renderer relies on.

    Approach:
        Returns a *new* `Environment` per call. Jinja's `Environment`
        caches parsed templates internally; sharing one across requests
        is fine, but creating one per render is cheap (microseconds)
        and avoids any concern about cross-request state.
    """
    root = templates_root if templates_root is not None else TEMPLATES_ROOT
    return Environment(
        loader=FileSystemLoader(str(root)),
        undefined=StrictUndefined,
        autoescape=False,  # noqa: S701 — emitting YAML, not HTML; see module docstring
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


__all__ = ["TEMPLATES_ROOT", "make_environment"]

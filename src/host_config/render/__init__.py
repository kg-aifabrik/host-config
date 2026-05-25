"""Renderer package: Jinja templates + environment factory.

Templates live under `templates/<role>/` — one directory per host role.
Each role directory carries the three cloud-init payloads cloud-init
expects to find at a NoCloud datasource: ``meta-data``, ``user-data``,
``network-config``.

The actual renderer (intent → bytes) lands in M2-4; this package
currently exposes only the environment factory + template tree.
"""

from __future__ import annotations

from host_config.render.environment import TEMPLATES_ROOT, make_environment

__all__ = ["TEMPLATES_ROOT", "make_environment"]

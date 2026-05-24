"""Smoke tests — the bare minimum that proves the package is importable.

Real functional tests land alongside the code they cover starting in M1+.
This file exists to satisfy `pytest` (exit code 5 on empty collection)
and to give CI something to verify from day one.
"""

from __future__ import annotations

import pytest

import host_config


@pytest.mark.fast
def test_package_imports() -> None:
    """The `host_config` package imports cleanly and exposes `__version__`.

    Approach:
        Asserts `__version__` is a non-empty string. The import at the
        top of the file is itself the load-bearing test — if the package
        couldn't import, pytest would have failed at collection time.

    Scenarios:
        - Happy path: package imports; `__version__` is a non-empty string.
    """
    assert isinstance(host_config.__version__, str)
    assert host_config.__version__  # non-empty

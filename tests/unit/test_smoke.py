"""Smoke tests — the bare minimum that proves the package is importable.

Unit-level. Real functional tests live alongside the code they cover
in `tests/unit/<package>/test_<module>.py`. This file exists to satisfy
`pytest` (which exits with code 5 if it can't collect any tests) and
to give CI something to assert from the very first commit.

Why kept: in addition to fixing the empty-collection issue, this test
catches the "the package metadata is broken" failure mode (e.g., a bad
import in `__init__.py`) that would otherwise surface as a confusing
collection error in every other test file.
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

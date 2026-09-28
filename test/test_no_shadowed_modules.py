"""Guards against module/package shadowing regressions.

A module and a package with the same dotted name cannot coexist: Python
silently prefers the package, so the module becomes dead code that still
looks live to editors, linters, and humans. This happened once with
``moka_code/ui/commands.py`` (1641 lines) shadowed by ``moka_code/ui/commands/``.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "moka_code"


def _iter_python_modules():
    """Yield (module_path, package_dir) pairs where both exist."""
    for path in PACKAGE_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path.name == "__init__.py":
            continue
        sibling_package = path.with_suffix("")
        if sibling_package.is_dir():
            yield path, sibling_package


def test_no_module_shadowed_by_package():
    """No ``foo.py`` may sit next to a ``foo/`` package."""
    shadowed = [
        f"{module.relative_to(PACKAGE_ROOT)} is shadowed by "
        f"{package.relative_to(PACKAGE_ROOT)}/"
        for module, package in _iter_python_modules()
    ]
    assert not shadowed, (
        "Module(s) shadowed by a same-named package (dead code):\n  "
        + "\n  ".join(shadowed)
    )


@pytest.mark.parametrize(
    "dotted_name",
    [
        "moka_code.ui.commands",
    ],
)
def test_public_module_resolves_to_package(dotted_name):
    """The importable name must resolve to the package, not a stray module."""
    module = importlib.import_module(dotted_name)
    assert hasattr(module, "__path__"), (
        f"{dotted_name} resolved to {module.__file__!r} instead of a package"
    )

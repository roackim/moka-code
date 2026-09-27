"""Guards the import graph of the command package.

The command package used to be a web of circular imports: ``builtins``
re-exported from ``debug``/``server``/``roles``/…, and those modules imported
back into ``builtins`` mid-file. The rule now is:

    domain modules  ->  base only
    registry        ->  every domain module
    __init__        ->  base, core, registry

This test enforces that shape so the cycle cannot silently return.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

COMMANDS_DIR = Path(__file__).resolve().parent.parent / "moka_chat" / "ui" / "commands"

# Modules that may import sibling domain modules.
ASSEMBLERS = {"registry.py", "__init__.py"}

# The shared contract module every domain module may import.
BASE = "base"


def _relative_imports(path: Path) -> set[str]:
    """Return the set of sibling module names imported by ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            imported.add(node.module.split(".")[0])
    return imported


def _domain_modules() -> list[Path]:
    return [
        path
        for path in sorted(COMMANDS_DIR.glob("*.py"))
        if path.name not in ASSEMBLERS and path.name != "__init__.py"
    ]


@pytest.mark.parametrize("path", _domain_modules(), ids=lambda p: p.name)
def test_domain_module_imports_only_base(path: Path):
    """A domain module must not import a sibling domain module."""
    siblings = _relative_imports(path) - {BASE}
    assert not siblings, (
        f"{path.name} imports sibling module(s) {sorted(siblings)}; "
        f"domain modules may only import '{BASE}'. Move shared code into "
        f"base.py or assemble in registry.py."
    )


def test_no_import_cycles():
    """The relative-import graph of the package must be acyclic."""
    graph = {
        path.stem: _relative_imports(path)
        for path in sorted(COMMANDS_DIR.glob("*.py"))
    }

    visiting: set[str] = set()
    visited: set[str] = set()
    cycles: list[list[str]] = []

    def visit(node: str, stack: list[str]) -> None:
        if node in visited:
            return
        if node in visiting:
            cycles.append(stack[stack.index(node):] + [node])
            return
        visiting.add(node)
        for neighbour in graph.get(node, set()):
            if neighbour in graph:
                visit(neighbour, stack + [neighbour])
        visiting.discard(node)
        visited.add(node)

    for module in graph:
        visit(module, [module])

    assert not cycles, "Import cycle(s) in commands package: " + "; ".join(
        " -> ".join(cycle) for cycle in cycles
    )

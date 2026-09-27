"""R9 guard: the harness (core) must not import the UI layer.

The hard core/UI seam keeps the door open for replacing the TUI. ``main.py``
is the composition root and is allowed to import both.
"""

import ast
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parents[1] / "moka_chat" / "harness"


def _imported_modules(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                yield node.module
                for alias in node.names:
                    yield f"{node.module}.{alias.name}"


def _is_ui(module: str) -> bool:
    return module == "moka_chat.ui" or module.startswith("moka_chat.ui.")


def test_harness_does_not_import_ui():
    violations = []
    for path in sorted(CORE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module in _imported_modules(tree):
            if _is_ui(module):
                violations.append(f"{path.relative_to(CORE_ROOT)} imports {module}")

    assert not violations, "core must not import ui:\n" + "\n".join(violations)

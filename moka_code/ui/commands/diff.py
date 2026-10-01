"""``/diff``: the workspace's uncommitted changes, shown in ``$EDITOR``.

Git only, read-only (``harness.changes``). A bare ``/diff`` opens a picker of the
changed files; ``/diff <file>`` opens that file's diff at once. The diff goes to a
read-only temp ``.diff`` file in the cache, removed when the editor closes.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Dict, List, Optional

from moka_code import settings
from moka_code.harness import changes, images
from moka_code.harness.harness import current_workspace
from moka_code.ui.tui.colors import theme
from moka_code.ui.tui.components.menu import Cell, cell_text
from moka_code.ui.tui.msg_types import SysMsg, SysMsgError

from .base import ChatUIProtocol, pick

ALL = "(all changes)"


def rows(found: List[changes.Change]) -> Dict[str, Cell]:
    """Path -> one aligned, coloured line: ``modified   +25 −1``. The status is a
    word (``new`` for a file git does not know yet), numbers are right-aligned
    in columns, a zero count is muted."""
    if not found:
        return {}
    words = {"??": "new", "A": "new", "M": "modified", "D": "deleted"}
    colors = {"new": theme.SUCCESS, "modified": theme.WARNING, "deleted": theme.ERROR}
    add_w = max(len(str(c.added)) for c in found if c.added is not None) if any(
        c.added is not None for c in found) else 1
    rem_w = max(len(str(c.removed)) for c in found if c.removed is not None) if any(
        c.removed is not None for c in found) else 1
    word_w = max(len(words[c.status]) for c in found)
    cells: Dict[str, Cell] = {}
    for change in found:
        word = words[change.status]
        cell: Cell = [(word.ljust(word_w), colors[word]), ("  ", None)]
        if change.added is None or change.removed is None:
            cell.append(("binary", theme.MUTED))
        else:
            cell += [(f"+{change.added}".rjust(add_w + 1), theme.SUCCESS if change.added else theme.MUTED),
                     (" ", None),
                     (f"−{change.removed}".rjust(rem_w + 1), theme.ERROR if change.removed else theme.MUTED)]
        cells[change.path] = cell
    return cells


def changed_files() -> List[str]:
    """The changed files, for ``/diff <file>`` suggestions."""
    return [change.path for change in changes.cached_changes(current_workspace()) or []]


def changed_descriptions() -> Dict[str, Cell]:
    return rows(changes.cached_changes(current_workspace()) or [])


def live_description() -> str:
    """The ``/`` menu's line for ``/diff``: how many files are changed right now."""
    count = len(changes.cached_changes(current_workspace()) or [])
    return (f"Review changes · {count} file{'s' if count != 1 else ''}" if count
            else "Review uncommitted changes (opens a picker)")


def _diff_dir() -> Path:
    return images.cache_dir().parent / "diff"


async def _show(ui: ChatUIProtocol, workspace: str, path: Optional[str]) -> None:
    from moka_code.ui.external_editor import open_editor

    text = changes.diff_text(workspace, path, settings.config.context_diff_context)
    if not text.strip():
        ui.chat_history_panel.add_message(
            f"No changes in {path}." if path else "No changes.", msg_type=SysMsg(), title="diff")
        return
    folder = _diff_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (("all" if path is None else path.replace("/", "__")) + ".diff")
    target.unlink(missing_ok=True)
    target.write_text(text, encoding="utf-8")
    target.chmod(0o444)                                  # read-only: it is a view
    try:
        await open_editor(ui, target)
    finally:
        target.unlink(missing_ok=True)


async def diff_command(ui: ChatUIProtocol, args: List[str]) -> None:
    from moka_code.ui.external_editor import resolve_editor

    workspace = getattr(getattr(ui, "agent", None), "workspace", None) or os.getcwd()
    found = changes.changes(workspace)
    if found is None:
        ui.chat_history_panel.add_message(
            "Not a git repository (or git is not installed).", msg_type=SysMsgError(), title="diff")
        return
    if not found:
        ui.chat_history_panel.add_message("No uncommitted changes.", msg_type=SysMsg(), title="diff")
        return
    if not resolve_editor():
        ui.chat_history_panel.add_message(
            "No editor found. Set $VISUAL or $EDITOR.", msg_type=SysMsgError(), title="diff")
        return
    if args:
        if args[0] not in {change.path for change in found}:
            ui.chat_history_panel.add_message(
                f"No changes in {args[0]}.", msg_type=SysMsgError(), title="diff")
            return
        await _show(ui, workspace, args[0])
        return

    cells = rows(found)

    def _as_text() -> None:
        ui.chat_history_panel.add_message(
            "\n".join(f"{path.ljust(40)} {cell_text(cell)}" for path, cell in cells.items()),
            msg_type=SysMsg(), title="diff")

    pick(ui, "Changes", [ALL, *cells],
         lambda item: asyncio.ensure_future(_show(ui, workspace, None if item == ALL else item)),
         descriptions={ALL: f"{len(found)} file{'s' if len(found) != 1 else ''}", **cells},
         ordered=True, headless=_as_text)


__all__ = ["diff_command", "changed_files", "changed_descriptions", "live_description"]

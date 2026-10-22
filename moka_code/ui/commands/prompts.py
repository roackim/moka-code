"""``/prompt``: edit, create, locate or remove a reusable prompt file.

The prompts themselves (``!name args``) are ``moka_code/prompts.py``; this is only
the command that manages their files. They are read when used, so nothing reloads.
"""

from __future__ import annotations

import asyncio
import os
from typing import List

from moka_code import prompts as prompt_files
from moka_code.ui.tui.msg_types import SysMsg, SysMsgError

from .base import (
    ChatUIProtocol,
    Command,
    Param,
    confirm_remove,
    pick,
    prompt_descriptions,
    prompt_name_completions,
    show_path,
)


def _workspace(ui: ChatUIProtocol) -> str:
    return getattr(ui.agent, "workspace", None) or os.getcwd()


def _error(ui: ChatUIProtocol, text: str) -> None:
    ui.chat_history_panel.add_message(text, msg_type=SysMsgError(), title="prompt")


def _find(ui: ChatUIProtocol, name: str):
    """The prompt called ``name`` (a local one shadows a global one), or None
    after saying so."""
    prompt = prompt_files.list_prompts(_workspace(ui)).get(name)
    if prompt is None:
        _error(ui, f"Prompt not found: {name}. /prompt create {name} makes it.")
    return prompt


def _pick_prompt(ui: ChatUIProtocol, title: str, then) -> None:
    """A picker over the prompts; ``then(name)`` runs for the chosen one."""
    existing = prompt_files.list_prompts(_workspace(ui))
    if not existing:
        ui.chat_history_panel.add_message(
            "No prompts yet. /prompt create <name> makes one; use it as !<name>.",
            msg_type=SysMsg(), title="prompt")
        return
    descriptions = {name: f"{prompt.description} [{prompt.scope}]" for name, prompt in existing.items()}

    def _as_text() -> None:
        ui.chat_history_panel.add_message(
            "\n".join(f"!{name.ljust(16)} {text}" for name, text in descriptions.items()),
            msg_type=SysMsg(), title="prompt")

    pick(ui, title, list(existing), lambda name: asyncio.ensure_future(then(ui, [name])),
         descriptions=descriptions, headless=_as_text)


async def _open(ui: ChatUIProtocol, path) -> None:
    from moka_code.ui.external_editor import open_editor, resolve_editor

    if not resolve_editor():
        _error(ui, "No editor found. Set $VISUAL or $EDITOR.")
        return
    await open_editor(ui, path)


async def prompt_edit(ui: ChatUIProtocol, args: List[str]) -> None:
    """``/prompt edit [name]``: open an existing prompt's file."""
    if not args:
        _pick_prompt(ui, "Edit prompt", prompt_edit)
        return
    prompt = _find(ui, args[0])
    if prompt is not None:
        await _open(ui, prompt.path)


async def prompt_create(ui: ChatUIProtocol, args: List[str]) -> None:
    """``/prompt create <name> [global|local]``: write a starter prompt file
    (global by default) and open it. A local prompt may shadow a global one."""
    if not 1 <= len(args) <= 2 or (len(args) == 2 and args[1] not in prompt_files.SCOPES):
        _error(ui, "Usage: /prompt create <name> [global|local]")
        return
    name, scope = args[0], (args[1] if len(args) == 2 else "global")
    workspace = _workspace(ui)
    existing = prompt_files.list_prompts(workspace).get(name)
    if existing is not None and existing.scope == scope:
        _error(ui, f"Prompt '{name}' already exists ({scope}). /prompt edit {name} opens it.")
        return
    try:
        path = prompt_files.ensure_prompt_file(name, scope, workspace)
    except (OSError, ValueError) as exc:
        _error(ui, str(exc))
        return
    await _open(ui, path)


async def prompt_path(ui: ChatUIProtocol, args: List[str]) -> None:
    """``/prompt path [name]``: print and copy the file a prompt resolves to."""
    if not args:
        _pick_prompt(ui, "Prompt path", prompt_path)
        return
    prompt = _find(ui, args[0])
    if prompt is not None:
        show_path(ui, "prompt", f"Prompt '{prompt.name}' file ({prompt.scope})", prompt.path)


async def prompt_remove(ui: ChatUIProtocol, args: List[str]) -> None:
    """``/prompt remove <name>``: delete the file the prompt resolves to (the
    local one when both exist) after Remove / Cancel."""
    if len(args) != 1:
        _error(ui, "Usage: /prompt remove <name>")
        return
    prompt = _find(ui, args[0])
    if prompt is None:
        return

    def _remove() -> None:
        try:
            prompt.path.unlink()
        except OSError as exc:
            _error(ui, str(exc))
            return
        message = f"Removed {prompt.scope} prompt: {prompt.name}"
        if prompt.name in prompt_files.list_prompts(_workspace(ui)):
            message += " (the global one is used now)"
        ui.chat_history_panel.add_message(message, msg_type=SysMsg(), title="prompt")

    confirm_remove(ui, f"Remove {prompt.scope} prompt '{prompt.name}'?", _remove)


async def prompt_command(ui: ChatUIProtocol, args: List[str]):
    """Bare ``/prompt`` picks a prompt to edit; anything else is not a subcommand."""
    if args:
        _error(ui, f"Unknown subcommand '{args[0]}'. Valid: {', '.join(PROMPT_SUBCOMMANDS)}")
        return
    _pick_prompt(ui, "Prompts", prompt_edit)


def _name(required: bool) -> Param:
    return Param("NAME", required=required, completions=prompt_name_completions,
                 descriptions=prompt_descriptions)


PROMPT_SUBCOMMANDS = {
    "edit": Command("prompt edit", "Edit a prompt's file (picker when no name given)",
                    handler=prompt_edit, params=[_name(False)]),
    "create": Command("prompt create", "Create a prompt file from a starter",
                      handler=prompt_create,
                      params=[Param("NAME", required=True),
                              Param("SCOPE", completions=list(prompt_files.SCOPES), ordered=True)]),
    "path": Command("prompt path", "Print (and copy) a prompt's file path",
                    handler=prompt_path, params=[_name(False)]),
    "remove": Command("prompt remove", "Remove a prompt's file (asks to confirm)",
                      handler=prompt_remove, params=[_name(True)]),
}


__all__ = ["PROMPT_SUBCOMMANDS", "prompt_command"]

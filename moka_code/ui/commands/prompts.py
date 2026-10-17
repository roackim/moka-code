"""``/prompt``: create or edit a reusable prompt file in ``$EDITOR``.

The prompts themselves (``!name args``) are ``moka_code/prompts.py``; this is only
the command that opens their files. They are read when used, so nothing reloads.
"""

from __future__ import annotations

import asyncio
import os
from typing import List

from moka_code import prompts as prompt_files
from moka_code.ui.tui.msg_types import SysMsg, SysMsgError

from .base import ChatUIProtocol, pick


def _workspace(ui: ChatUIProtocol) -> str:
    return getattr(ui.agent, "workspace", None) or os.getcwd()


async def _open(ui: ChatUIProtocol, name: str, scope: str | None) -> None:
    """Open a prompt's file, creating it from a starter when missing. An existing
    prompt opens where it lives; a new one is global unless ``scope`` says."""
    from moka_code.ui.external_editor import open_editor, resolve_editor

    workspace = _workspace(ui)
    existing = prompt_files.list_prompts(workspace)
    scope = scope or (existing[name].scope if name in existing else "global")
    if not resolve_editor():
        ui.chat_history_panel.add_message(
            "No editor found. Set $VISUAL or $EDITOR.", msg_type=SysMsgError(), title="prompt")
        return
    try:
        path = prompt_files.ensure_prompt_file(name, scope, workspace)
    except (OSError, ValueError) as exc:
        ui.chat_history_panel.add_message(str(exc), msg_type=SysMsgError(), title="prompt")
        return
    await open_editor(ui, path)


async def prompt_command(ui: ChatUIProtocol, args: List[str]):
    """``/prompt`` picks among the prompts; ``/prompt <name> [global|local]`` opens one."""
    existing = prompt_files.list_prompts(_workspace(ui))
    if len(args) > 2 or (len(args) == 2 and args[1] not in prompt_files.SCOPES):
        ui.chat_history_panel.add_message(
            "Usage: /prompt <name> [global|local]", msg_type=SysMsgError(), title="prompt")
        return
    if args:
        await _open(ui, args[0], args[1] if len(args) == 2 else None)
        return
    if not existing:
        ui.chat_history_panel.add_message(
            "No prompts yet. /prompt <name> creates one; use it as !<name>.",
            msg_type=SysMsg(), title="prompt")
        return
    descriptions = {name: f"{prompt.description} [{prompt.scope}]" for name, prompt in existing.items()}

    def _as_text() -> None:
        ui.chat_history_panel.add_message(
            "\n".join(f"!{name.ljust(16)} {text}" for name, text in descriptions.items()),
            msg_type=SysMsg(), title="prompt")

    pick(ui, "Prompts", list(existing),
         lambda name: asyncio.ensure_future(_open(ui, name, None)),
         descriptions=descriptions, headless=_as_text)


__all__ = ["prompt_command"]

"""Core chat and application commands.

These operate on the conversation itself or the application lifecycle,
independent of any subsystem (servers, models, roles, …).

Handlers are plain functions; :mod:`registry` attaches them to ``Command``
objects together with their descriptions and params. Classes are reserved for
commands that own a subcommand tree.
"""

from __future__ import annotations

import logging
import os
from typing import List

from moka_code.ui.tui.components.menu import sort_items
from moka_code.ui.tui.msg_types import SysMsg, SysMsgError

from .base import (
    ChatUIProtocol,
    Command,
    open_shell,
    pick,
    reapply_endpoint,
    stop_before,
)

logger = logging.getLogger(__name__)


async def cmd_help(ui: ChatUIProtocol, args: List[str], commands):
    """List every registered command.

    ``commands`` is injected by the registry (``registry._help``) so this
    module never imports the registry itself.
    """
    help_lines = []
    for cmd in sorted(commands.values(), key=lambda x: x.name):
        if not cmd.name.startswith("_"):
            help_lines.append(f"/{cmd.name.ljust(8)} {cmd.description}")
    ui.show_popup("help", "\n".join(help_lines))


async def cmd_private(ui: ChatUIProtocol, args: List[str]):
    """Stop saving this conversation; /clear ends the mode."""
    if getattr(ui.agent, "private", False):
        ui.chat_history_panel.add_message(
            "Already private. /clear leaves private mode.", msg_type=SysMsg())
        return
    ui.agent.set_private(True)
    text = "Private: nothing is saved or logged from now on. /clear leaves private mode."
    saved = getattr(ui, "session_path", None)
    if saved is not None and saved.exists():
        text += f" Earlier turns remain in {saved}."
    ui.chat_history_panel.add_message(text, msg_type=SysMsg())
    if hasattr(ui, "refresh_status_bar"):
        ui.refresh_status_bar()


async def cmd_clear(ui: ChatUIProtocol, args: List[str]):
    if not await stop_before(ui, "/clear"):
        return
    ui.chat_history_panel.clear()
    if hasattr(ui.agent, "set_private"):
        ui.agent.set_private(False)
    if hasattr(ui.agent, "clear_history"):
        ui.agent.clear_history()
    # The cleared conversation stays saved; what follows is a new session.
    new_session = getattr(ui, "new_session", None)
    if callable(new_session):
        new_session()
    ui.chat_history_panel.add_message("Conversation cleared.", msg_type=SysMsg())
    if hasattr(ui, "refresh_status_bar"):
        ui.refresh_status_bar()


def _apply_theme(ui=None) -> None:
    """Apply the effective theme after a config reload (best effort)."""
    from moka_code import settings
    from moka_code.ui.tui.colors import set_theme

    try:
        set_theme(settings.config.get_active_theme())
        refresh = getattr(ui, "refresh_theme", None)
        if callable(refresh):
            refresh()
    except Exception:  # pragma: no cover - theme application is best effort
        logger.warning("Failed to apply theme after reload", exc_info=True)


def _reapply_role(ui: ChatUIProtocol) -> List[str]:
    """Load the active role's file again, so an edit applies now (not only after
    a ``/role`` switch). A file that no longer loads keeps the running role in
    memory (``validate_roles`` reports a bad one, this a missing one); so does a
    response still being written. Without any role file the conversation starts
    on the first one that loads. Returns what could not be applied."""
    from moka_code.harness import roles

    current = getattr(getattr(ui, "agent", None), "role", None)
    if current is None:
        return []
    if current.name == roles.NO_ROLE:
        fresh = roles.default_role()
        if fresh.name != roles.NO_ROLE:
            ui.agent.set_role(fresh)
        return []
    try:
        fresh = roles.load_role(current.name)
    except KeyError:
        return [f"role {current.name}: roles/{current.name}.toml is gone, "
                "the conversation keeps the role it has"]
    except (OSError, ValueError):
        return []
    if fresh == current:
        return []
    is_generating = getattr(ui, "is_generating", None)
    if callable(is_generating) and is_generating():
        return [f"role {current.name} changed on disk; run /reload once the response is done"]
    ui.agent.set_role(fresh)
    return []


def reload_and_apply(ui: ChatUIProtocol, title: str | None = None) -> None:
    """Reload everything hand-edited and apply it: config files, roles (the
    active one too), theme, endpoint; then report. The one path behind
    ``/reload`` and every ``/config`` edit."""
    from moka_code import projects, settings
    from moka_code.harness import roles

    from moka_code.ui.tui.colors import theme_problems

    workspace = getattr(getattr(ui, "agent", None), "workspace", None) or os.getcwd()
    errors = (settings.reload_config() + roles.validate_roles()
              + projects.validate_sandboxes(workspace) + theme_problems())
    _apply_theme(ui)
    errors += _reapply_role(ui)
    errors += _reapply_sandbox(ui)
    reapply_endpoint(ui)
    _report_reload(ui, errors, title)


def _reapply_sandbox(ui: ChatUIProtocol) -> List[str]:
    """Resolve the project's active sandbox again, so an edit to its file (or its
    deletion) applies now. A response still being written keeps the running one.
    Returns what could not be applied."""
    from moka_code import projects

    agent = getattr(ui, "agent", None)
    if agent is None or not hasattr(agent, "set_sandbox"):
        return []
    project = projects.load_project(getattr(agent, "workspace", None) or os.getcwd())
    fresh = projects.active_spec(project)
    if fresh == getattr(getattr(agent, "transport", None), "spec", None):
        track = getattr(agent, "track_sandbox", None)
        if callable(track):
            track(project.active)       # a touched file is not a difference; an image may be rebuilt
        return []
    is_generating = getattr(ui, "is_generating", None)
    if callable(is_generating) and is_generating():
        return ["the sandbox changed on disk; run /reload once the response is done"]
    agent.set_sandbox(fresh, project.active)
    return []


async def cmd_reload(ui: ChatUIProtocol, args: List[str]):
    """Reload hand-edited configuration from disk (explicit, no watcher)."""
    reload_and_apply(ui)


def _report_reload(ui: ChatUIProtocol, errors: List[str], title: str | None = None) -> None:
    """Say how a reload went, then refresh the banner's setup notes and the
    status bar (both read the reloaded config)."""
    if errors:
        ui.chat_history_panel.add_message(
            "Config reloaded with errors:\n" + "\n".join(errors),
            msg_type=SysMsgError(), title=title)
    else:
        ui.chat_history_panel.add_message("Config reloaded.", msg_type=SysMsg(), title=title)
    for refresh in ("refresh_setup_notes", "refresh_status_bar"):
        if hasattr(ui, refresh):
            getattr(ui, refresh)()


def _no_editor(ui: ChatUIProtocol) -> bool:
    """Say so when there is no editor; True when there is none."""
    from moka_code.ui.external_editor import resolve_editor

    if resolve_editor():
        return False
    ui.chat_history_panel.add_message(
        "No editor found. Set $VISUAL or $EDITOR.", msg_type=SysMsgError(), title="config")
    return True


def _config_flat(section: str):
    """The handler of a flat section (``ui``, ``servers``…): open its file, then reload."""
    async def handler(ui: ChatUIProtocol, args: List[str]):
        from moka_code import settings
        from moka_code.ui.external_editor import open_editor

        if _no_editor(ui):
            return
        await open_editor(ui, settings.config.ensure_section_file(section))
        reload_and_apply(ui, "config")

    return handler


def config_command() -> Command:
    """``/config``: one subcommand per file or kind of file, each declaring its own
    parameters, so completion, descriptions and hints all come from the tree.
    Roles, themes, prompts and sandboxes are edited from their own commands."""
    import asyncio

    from moka_code import settings

    subcommands = {
        section: Command(f"config {section}", f"Edit {filename} and reload",
                         handler=_config_flat(section))
        for section, filename in settings.CONFIG_FILES.items()
    }

    async def config_help(ui: ChatUIProtocol, args: List[str]):
        """Bare ``/config``: a picker over the subcommands. Anything else is not one."""
        if args:
            ui.chat_history_panel.add_message(
                f"Unknown section '{args[0]}'. Valid: {', '.join(command.subcommands)}",
                msg_type=SysMsgError(), title="config")
            return
        sections = list(command.subcommands)
        descriptions = {name: sub.description for name, sub in command.subcommands.items()}

        def _as_text() -> None:
            lines = [f"{s.ljust(12)} {descriptions[s]}" for s in sort_items(sections)]
            ui.chat_history_panel.add_message("\n".join(lines), msg_type=SysMsg(), title="config")

        pick(ui, "Config", sections,
             lambda section: asyncio.ensure_future(command.execute(ui, [section])),
             descriptions=descriptions, headless=_as_text)

    command = Command("config", "Edit a config file in $EDITOR and reload it",
                      handler=config_help, subcommands=subcommands)
    return command


async def cmd_edit(ui: ChatUIProtocol, args: List[str]):
    """Open an arbitrary file in the user's editor."""
    from pathlib import Path

    from moka_code.ui.external_editor import open_editor, resolve_editor

    if not args:
        ui.chat_history_panel.add_message(
            "Usage: /edit <file>. For config files use /config <section>.",
            msg_type=SysMsgError(), title="edit")
        return
    if not resolve_editor():
        ui.chat_history_panel.add_message(
            "No editor found. Set $VISUAL or $EDITOR.",
            msg_type=SysMsgError(), title="edit")
        return
    path = Path(args[0]).expanduser()
    await open_editor(ui, path)


async def cmd_compact(ui: ChatUIProtocol, args: List[str]):
    if args:
        ui.chat_history_panel.add_message("Usage: /compact", msg_type=SysMsgError())
        return
    if not hasattr(ui.agent, "compact_history"):
        ui.chat_history_panel.add_message(
            "Compaction is not supported by this agent.", msg_type=SysMsgError())
        return

    placeholder = ui.chat_history_panel.add_message(
        "Compacting history...", msg_type=SysMsg(), title="compact")
    try:
        result = await ui.agent.compact_history()
        if not result.get("ok"):
            compact_msg = ui.chat_history_panel.new_message(
                result.get("message", "Compaction skipped."),
                msg_type=SysMsg(), title="compact")
            ui.chat_history_panel.replace_message(placeholder, compact_msg)
            return
        # The transcript's compaction line says what happened (unfold: the summary).
        ui.chat_history_panel.remove_message(placeholder)
        ui.chat_history_panel.add_compaction(ui.agent.history, result["message_id"])
        if hasattr(ui, "refresh_status_bar"):
            ui.refresh_status_bar()
    except Exception as exc:
        error_msg = ui.chat_history_panel.new_message(
            f"Compaction failed: {exc}", msg_type=SysMsgError(), title="compact")
        ui.chat_history_panel.replace_message(placeholder, error_msg)


async def cmd_exit(ui: ChatUIProtocol, args: List[str]):
    if ui.compositor:
        ui.compositor.running = False


async def cmd_terminal(ui: ChatUIProtocol, args: List[str]):
    """Open a shell on the host, in the workspace; ``exit`` returns to moka.

    The sandbox shell is ``/sandbox terminal``.
    """
    import os

    if args:
        ui.chat_history_panel.add_message(
            "Usage: /terminal (host shell). The sandbox shell is /sandbox terminal.",
            msg_type=SysMsgError(), title="terminal")
        return
    await open_shell(ui, [os.environ.get("SHELL") or "/bin/sh"], "host")


async def cmd_stop(ui: ChatUIProtocol, args: List[str]):
    if hasattr(ui, "stop_generation"):
        if ui.stop_generation():
            # Message is already appended by the cancelled task handler
            pass
        else:
            ui.chat_history_panel.add_message(
                "No active generation to stop.", msg_type=SysMsg())
    else:
        ui.chat_history_panel.add_message(
            "Stop command not supported by this UI.", msg_type=SysMsg())


async def cmd_debug(ui: ChatUIProtocol, args: List[str]):
    if hasattr(ui, "show_debug"):
        ui.show_debug()
    else:
        ui.chat_history_panel.add_message(
            "Debug view not supported by this UI.", msg_type=SysMsg())


async def cmd_activity(ui: ChatUIProtocol, args: List[str]):
    if hasattr(ui, "toggle_activity"):
        ui.toggle_activity()
    else:
        ui.chat_history_panel.add_message(
            "Activity overlay not supported by this UI.", msg_type=SysMsg())


__all__ = [
    "cmd_debug", "cmd_help", "cmd_clear", "cmd_reload", "config_command", "cmd_edit",
    "cmd_compact", "cmd_exit", "cmd_stop", "cmd_activity",
]

"""Core chat and application commands.

These operate on the conversation itself or the application lifecycle,
independent of any subsystem (servers, models, roles, …).

Handlers are plain functions; :mod:`registry` attaches them to ``Command``
objects together with their descriptions and params. Classes are reserved for
commands that own a subcommand tree.
"""

from __future__ import annotations

import logging
from typing import List

from moka_code.ui.tui.components.menu import sort_items
from moka_code.ui.tui.msg_types import SysMsg, SysMsgError, SysMsgWarning

from .base import (
    ChatUIProtocol,
    Command,
    Param,
    config_section_completions,
    open_project_sandbox,
    open_shell,
    pick,
    reapply_endpoint,
    role_descriptions,
    role_name_completions,
    theme_descriptions,
    theme_name_completions,
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
    from moka_code import settings
    from moka_code.harness import roles

    from moka_code.ui.tui.colors import theme_problems

    errors = settings.reload_config() + roles.validate_roles() + theme_problems()
    _apply_theme(ui)
    errors += _reapply_role(ui)
    reapply_endpoint(ui)
    _report_reload(ui, errors, title)


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


async def cmd_config(ui: ChatUIProtocol, args: List[str]):
    """Open one config section file in the user's editor, then reload it."""
    from moka_code import settings
    from moka_code.ui.external_editor import open_editor, resolve_editor

    if not args:
        # The same list as ``/config <section>`` completion; picking one
        # opens it.
        import asyncio

        descriptions = {
            **settings.CONFIG_FILES,
            "sandbox": "projects/<name>.toml  (per-project sandboxes)",
            "role": "roles/<name>.toml  (create/edit a role)",
        }
        sections = config_section_completions()

        def _as_text() -> None:
            lines = [f"{s.ljust(10)} {descriptions[s]}" for s in sort_items(sections)]
            ui.chat_history_panel.add_message("\n".join(lines), msg_type=SysMsg(), title="config")

        pick(ui, "Config", sections,
             lambda section: asyncio.ensure_future(cmd_config(ui, [section])),
             descriptions=descriptions, headless=_as_text)
        return

    section = args[0].lower()
    if section == "sandbox":
        await _config_sandbox(ui)
        return
    if section == "role":
        await _config_role(ui, args[1:])
        return
    if section == "theme":
        await _config_theme(ui, args[1:])
        return
    if section not in settings.CONFIG_FILES:
        ui.chat_history_panel.add_message(
            f"Unknown section '{section}'. Valid: "
            + ", ".join(settings.CONFIG_FILES),
            msg_type=SysMsgError(), title="config")
        return

    if not resolve_editor():
        ui.chat_history_panel.add_message(
            "No editor found. Set $VISUAL or $EDITOR.",
            msg_type=SysMsgError(), title="config")
        return
    path = settings.config.ensure_section_file(section)
    await open_editor(ui, path)
    reload_and_apply(ui, "config")


async def _config_sandbox(ui: ChatUIProtocol):
    """Open the current project's sandbox file and apply its active entry."""
    await open_project_sandbox(ui)


class ConfigCommand(Command):
    """``/config [section]``; offers role/theme ids after the matching section."""

    def __init__(self):
        super().__init__(
            "config",
            "Edit a config file in $EDITOR and reload it",
            handler=cmd_config,
            params=[Param("SECTION", completions=config_section_completions)],
        )

    def get_completions(self, arg_index, prior_args=()):
        if arg_index == 1 and prior_args:
            section = prior_args[0].lower()
            if section == "role":
                return role_name_completions()
            if section == "theme":
                return theme_name_completions()
        return super().get_completions(arg_index, prior_args)

    def get_descriptions(self, arg_index, prior_args=()):
        if arg_index == 1 and prior_args:
            section = prior_args[0].lower()
            if section == "role":
                return role_descriptions()
            if section == "theme":
                return theme_descriptions()
        return super().get_descriptions(arg_index, prior_args)


async def _config_role(ui: ChatUIProtocol, args: List[str]):
    """Create/edit a role file, then reload. ``delete`` requires confirmation."""
    from moka_code import settings
    from moka_code.harness import roles
    from moka_code.ui.external_editor import open_editor, resolve_editor

    if not args:
        names = ", ".join(roles.list_roles())
        ui.chat_history_panel.add_message(
            f"Usage: /config role <name>  |  /config role delete <name>\n"
            f"Roles: {names}",
            msg_type=SysMsg(), title="config")
        return

    if args[0] == "delete":
        if len(args) < 2:
            ui.chat_history_panel.add_message(
                "Usage: /config role delete <name>", msg_type=SysMsgError(), title="config")
            return
        name = args[1]
        if len(args) == 2:
            ui.chat_history_panel.add_message(
                f"This deletes roles/{name}.toml. Re-run to confirm:\n"
                f"/config role delete {name} confirm",
                msg_type=SysMsgWarning(), title="config")
            return
        if args[2] != "confirm":
            ui.chat_history_panel.add_message(
                "Confirmation token must be 'confirm'.", msg_type=SysMsgError(), title="config")
            return
        try:
            roles.delete_role(name)
        except (KeyError, OSError, ValueError) as exc:
            ui.chat_history_panel.add_message(str(exc), msg_type=SysMsgError(), title="config")
            return
        ui.chat_history_panel.add_message(f"Deleted role: {name}", msg_type=SysMsg(), title="config")
        return

    if not resolve_editor():
        ui.chat_history_panel.add_message(
            "No editor found. Set $VISUAL or $EDITOR.",
            msg_type=SysMsgError(), title="config")
        return
    try:
        path = roles.ensure_role_file(args[0])
    except (OSError, ValueError) as exc:
        ui.chat_history_panel.add_message(str(exc), msg_type=SysMsgError(), title="config")
        return
    await open_editor(ui, path)
    reload_and_apply(ui, "config")


async def _config_theme(ui: ChatUIProtocol, args: List[str]):
    """Open ``themes.toml``; with an id, materialize that theme's section first."""
    from moka_code import settings
    from moka_code.ui.external_editor import open_editor, resolve_editor
    from moka_code.ui.tui.colors import theme_toml_section

    if not resolve_editor():
        ui.chat_history_panel.add_message(
            "No editor found. Set $VISUAL or $EDITOR.",
            msg_type=SysMsgError(), title="config")
        return

    path = settings.config.ensure_section_file("theme")
    if args:
        name = args[0]
        block = theme_toml_section(name)
        if block is None:
            ui.chat_history_panel.add_message(
                f"Unknown theme '{name}'. Use /theme to list the available themes.",
                msg_type=SysMsgError(), title="config")
            return
        text = path.read_text(encoding="utf-8")
        # Add an override section for this theme if it is not there yet (so the
        # user edits the palette rather than an empty file).
        header = f"[themes.{name}]"
        if not any(line.strip() == header for line in text.splitlines()):
            path.write_text(text.rstrip() + "\n\n" + block, encoding="utf-8")

    await open_editor(ui, path)
    reload_and_apply(ui, "config")


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
        compact_msg = ui.chat_history_panel.new_message(
            (
                f"Compaction complete: {result['compacted_messages']} messages summarized\n"
                f"Inserted marker: {result['message_id']}\n"
                f"Summary size: {result['summary_chars']:,} chars"
            ),
            msg_type=SysMsg(), title="compact")
        ui.chat_history_panel.replace_message(placeholder, compact_msg)
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
    "cmd_debug", "cmd_help", "cmd_clear", "cmd_reload", "cmd_config", "cmd_edit",
    "cmd_compact", "cmd_exit", "cmd_stop", "cmd_activity", "ConfigCommand",
]

"""Shared command contracts and completion helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, Union

from moka_code import settings


CompletionSource = Union[List[str], Callable[[], List[str]]]
DescriptionSource = Union[Dict[str, str], Callable[[], Dict[str, str]]]
CommandHandler = Callable[["ChatUIProtocol", List[str]], Awaitable[None]]


@dataclass
class Param:
    """Defines a command parameter for hints and autocomplete."""

    name: str
    completions: Optional[CompletionSource] = None
    descriptions: Optional[DescriptionSource] = None
    path: bool = False
    required: bool = False


class ChatUIProtocol(Protocol):
    agent: Any
    chat_history_panel: Any
    input_panel: Any
    compositor: Any

    def show_popup(self, title: str, content: str, content_padding: int = 1) -> None: ...


class Command:
    """A slash command: metadata plus a handler and/or a subcommand tree.

    A leaf command has a ``handler``. A command tree has ``subcommands`` and an
    optional fallback ``handler`` (shown when no subcommand matches, e.g. the
    bare ``/cmd`` help). :meth:`execute` walks the tree and dispatches to the
    resolved leaf, so callers never parse subcommand names themselves.
    """

    def __init__(self, name: str, description: str,
                 handler: Optional[CommandHandler] = None,
                 subcommands: Optional[Dict[str, "Command"]] = None,
                 params: Optional[List[Param]] = None):
        self.name = name
        self.description = description
        self.handler = handler
        self.subcommands = subcommands or {}
        self.params = params or []

    async def execute(self, ui: ChatUIProtocol, args: List[str]):
        if self.has_subcommands():
            cmd, offset = self.resolve_command(args)
            if cmd is not self:
                if cmd.handler is None:
                    raise NotImplementedError(f"command '{cmd.name}' has no handler")
                await cmd.handler(ui, args[offset:])
                return
        if self.handler is None:
            raise NotImplementedError(f"command '{self.name}' has no handler")
        await self.handler(ui, args)

    def has_subcommands(self) -> bool:
        return bool(self.subcommands)

    def resolve_command(self, parts: List[str]) -> tuple["Command", int]:
        cmd = self
        offset = 0
        while cmd.has_subcommands() and offset < len(parts):
            sub_name = parts[offset]
            if sub_name not in cmd.subcommands:
                break
            cmd = cmd.subcommands[sub_name]
            offset += 1
        return cmd, offset

    def get_completions(self, arg_index: int, prior_args: tuple[str, ...] = ()) -> List[str]:
        if self.has_subcommands():
            return sorted(self.subcommands.keys()) if arg_index == 0 else []
        if arg_index < 0 or arg_index >= len(self.params):
            return []
        parameter = self.params[arg_index]
        if parameter.path:
            return self._scan_dirs(parameter.completions)
        if parameter.completions is None:
            return []
        return (parameter.completions() if callable(parameter.completions)
                else list(parameter.completions))

    def get_descriptions(self, arg_index: int, prior_args: tuple[str, ...] = ()) -> Dict[str, str]:
        """Return value -> one-line description for an argument position."""
        if arg_index < 0 or arg_index >= len(self.params):
            return {}
        source = self.params[arg_index].descriptions
        if source is None:
            return {}
        return dict(source() if callable(source) else source)

    @staticmethod
    def _scan_dirs(workspace: Any = None) -> List[str]:
        base = workspace() if callable(workspace) else workspace
        try:
            entries = []
            with os.scandir(base or ".") as directory:
                for entry in sorted(directory, key=lambda item: item.name.lower()):
                    if entry.name.startswith("."):
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=True):
                            entries.append(entry.name + "/")
                    except OSError:
                        pass
            return entries
        except OSError:
            return []


def config_section_completions() -> List[str]:
    """Return the editable config targets (for ``/config <section>``)."""
    return [*settings.CONFIG_FILES, "sandbox", "role"]


def role_name_completions() -> List[str]:
    """Return the available role names (for ``/role <name>``)."""
    from moka_code.harness import roles

    return roles.list_roles()


def role_descriptions() -> Dict[str, str]:
    """Return role name -> description (for completion menus)."""
    from moka_code.harness import roles

    descriptions: Dict[str, str] = {}
    for name in roles.list_roles():
        try:
            descriptions[name] = roles.load_role(name).description
        except (KeyError, OSError, ValueError):
            descriptions[name] = ""
    return descriptions


def theme_name_completions() -> List[str]:
    """Return the selectable color theme names (for ``/config theme <id>``)."""
    from moka_code.ui.tui.colors import theme_names

    return theme_names()


def theme_descriptions() -> Dict[str, str]:
    """Return theme name -> description (for completion menus)."""
    custom = set(getattr(settings.config, "themes", {}))
    return {
        name: ("custom (themes.toml)" if name in custom else "built-in")
        for name in theme_name_completions()
    }


async def open_project_sandbox(ui: ChatUIProtocol) -> None:
    """Open the current project's sandbox file and apply its active entry.

    Shared by ``/config sandbox`` and ``/sandbox config``.
    """
    from moka_code import projects
    from moka_code.ui.external_editor import open_editor, resolve_editor
    from moka_code.ui.tui.msg_types import SysMsg, SysMsgError

    agent = getattr(ui, "agent", None)
    workspace = getattr(agent, "workspace", None) or os.getcwd()

    if not resolve_editor():
        ui.chat_history_panel.add_message(
            "No editor found. Set $VISUAL or $EDITOR.",
            msg_type=SysMsgError(), title="config")
        return

    path = projects.ensure_project_file(workspace)
    await open_editor(ui, path)

    errors: list[str] = []
    project = projects.load_project(workspace, errors)
    if not errors and agent is not None and hasattr(agent, "set_sandbox"):
        agent.set_sandbox(projects.active_spec(project))

    if errors:
        ui.chat_history_panel.add_message(
            "Sandbox config reloaded with errors:\n" + "\n".join(errors),
            msg_type=SysMsgError(), title="config")
    else:
        ui.chat_history_panel.add_message(
            f"Sandbox config reloaded (active: {project.active or 'none'}).",
            msg_type=SysMsg(), title="config")
    if hasattr(ui, "refresh_status_bar"):
        ui.refresh_status_bar()


def activate_endpoint(ui: ChatUIProtocol, endpoint) -> None:
    """Make ``endpoint`` the agent's live endpoint and probe it in the background.

    Shared by ``/model`` and the config reloads. The status bar refreshes now
    and again once the probe resolves the served model and context window.
    """
    import asyncio
    from moka_code.harness.endpoint import prewarm_local_resolution

    ui.agent.switch_server(endpoint)
    prewarm_local_resolution(endpoint._original_base_url)

    async def _prewarm():
        await endpoint.prewarm_model_name()
        if hasattr(ui, "refresh_status_bar"):
            ui.refresh_status_bar()

    asyncio.ensure_future(_prewarm())
    if hasattr(ui, "refresh_status_bar"):
        ui.refresh_status_bar()


def reapply_endpoint(ui: ChatUIProtocol) -> None:
    """Apply a reloaded ``servers.toml``/state to the live endpoint.

    The endpoint is rebuilt only when its server, definition or selected model
    changed, so an unrelated reload keeps the connection and usage display.
    The model catalog is rediscovered in the background either way.
    """
    import asyncio
    from moka_code.harness.endpoint import get_active_endpoint, refresh_catalog

    async def _refresh():
        await refresh_catalog()
        if hasattr(ui, "refresh_status_bar"):
            ui.refresh_status_bar()

    asyncio.ensure_future(_refresh())

    agent = getattr(ui, "agent", None)
    current = getattr(agent, "endpoint", None)
    if current is None or not hasattr(agent, "switch_server"):
        return
    fresh = get_active_endpoint()
    if (fresh.name, fresh.source) == (current.name, getattr(current, "source", None)):
        return
    activate_endpoint(ui, fresh)


async def open_shell(ui: ChatUIProtocol, argv: List[str], where: str) -> None:
    """Hand the terminal to an interactive shell; ``exit`` returns to moka.

    Shared by ``/terminal`` (host) and ``/sandbox terminal``. The shell is
    marked with ``MOKA_TERMINAL`` so a moka started in it refuses to nest
    (``main.py``); the conversation keeps running meanwhile.
    """
    from moka_code.ui.external_editor import run_in_foreground
    from moka_code.ui.tui.msg_types import SysMsg, SysMsgError

    agent = getattr(ui, "agent", None)
    workspace = getattr(agent, "workspace", None) or os.getcwd()
    env = {**os.environ, "MOKA_TERMINAL": str(os.getpid())}
    try:
        await run_in_foreground(ui, argv, cwd=workspace, clear_screen=True, env=env)
    except OSError as exc:
        ui.chat_history_panel.add_message(
            f"Could not open a {where} shell: {exc}", msg_type=SysMsgError(), title="terminal")
        return
    ui.chat_history_panel.add_message(f"Back from the {where} shell.", msg_type=SysMsg())

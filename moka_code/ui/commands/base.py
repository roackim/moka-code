"""Shared command contracts and completion helpers."""

from __future__ import annotations

import asyncio
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
    # The completions' order is meaningful (e.g. effort levels); otherwise
    # the menu sorts them.
    ordered: bool = False


class ChatUIProtocol(Protocol):
    agent: Any
    chat_history_panel: Any
    compositor: Any

    def show_popup(self, title: str, content: str, content_padding: int = 1, **options) -> None: ...


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
                 params: Optional[List[Param]] = None,
                 live_description: Optional[Callable[[], str]] = None):
        self.name = name
        # A description computed when the ``/`` menu opens (e.g. a count).
        self.live_description = live_description
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
            return list(self.subcommands) if arg_index == 0 else []
        if arg_index < 0 or arg_index >= len(self.params):
            return []
        parameter = self.params[arg_index]
        if parameter.completions is None:
            return []
        return (parameter.completions() if callable(parameter.completions)
                else list(parameter.completions))

    def takes_path(self, arg_index: int) -> bool:
        """Whether an argument is a workspace file (completed from the file list
        the ``@`` menu uses, not from ``Param.completions``)."""
        return (not self.has_subcommands() and 0 <= arg_index < len(self.params)
                and self.params[arg_index].path)

    def completions_ordered(self, arg_index: int) -> bool:
        """Whether an argument's completions keep their own order."""
        return (not self.has_subcommands() and 0 <= arg_index < len(self.params)
                and self.params[arg_index].ordered)

    def get_descriptions(self, arg_index: int, prior_args: tuple[str, ...] = ()) -> Dict[str, str]:
        """Return value -> one-line description for an argument position."""
        if arg_index < 0 or arg_index >= len(self.params):
            return {}
        source = self.params[arg_index].descriptions
        if source is None:
            return {}
        return dict(source() if callable(source) else source)



def pick(ui: ChatUIProtocol, title: str, items: List[str], on_accept: Callable[[str], None],
         descriptions: Optional[Dict[str, str]] = None, footers: Optional[Dict[str, str]] = None,
         ordered: bool = False, headless: Optional[Callable[[], None]] = None) -> None:
    """The one way a command offers a choice: a type-to-filter picker over
    ``items`` (the same values and descriptions the ``/cmd <arg>`` suggestions
    use). ``headless`` shows them as text when there is no compositor."""
    show = getattr(ui, "show_search_modal", None)
    modal = show(title, items, descriptions=descriptions, footers=footers,
                 on_accept=on_accept, ordered=ordered) if callable(show) else None
    if modal is None and headless is not None:
        headless()


REMOVE = "Remove"
CANCEL = "Cancel"


def confirm_remove(ui: ChatUIProtocol, title: str, on_yes: Callable[[], None]) -> None:
    """Ask Remove / Cancel in the picker; ``on_yes`` runs on Remove. Without a
    picker nothing is removed, and the notice says so."""
    from moka_code.ui.tui.msg_types import SysMsgError

    show = getattr(ui, "show_search_modal", None)
    modal = show(title, [REMOVE, CANCEL], on_accept=lambda choice: choice == REMOVE and on_yes(),
                 ordered=True) if callable(show) else None
    if modal is None:
        ui.chat_history_panel.add_message(
            f"{title}: needs the confirmation picker; nothing removed.",
            msg_type=SysMsgError(), title="remove")


def show_path(ui: ChatUIProtocol, title: str, label: str, path) -> None:
    """Print a file's path and copy it, so it can be edited outside the TUI."""
    from moka_code.ui.tui.msg_types import SysMsg

    message = f"{label}: {path}"
    try:
        from moka_code.ui.clipboard import copy_to_clipboard

        method = copy_to_clipboard(str(path))
        if method:
            message += f"\n(copied path via {method})"
    except Exception:  # pragma: no cover - clipboard is best effort
        pass
    ui.chat_history_panel.add_message(message, msg_type=SysMsg(), title=title)


def prompt_name_completions() -> List[str]:
    """Return the available prompt names (for ``/prompt edit <name>``)."""
    from moka_code import prompts
    from moka_code.harness.harness import current_workspace

    return list(prompts.list_prompts(current_workspace()))


def prompt_descriptions() -> Dict[str, str]:
    """Map prompt name -> its first line and scope (for ``/prompt edit <name>``)."""
    from moka_code import prompts
    from moka_code.harness.harness import current_workspace

    return {name: f"{prompt.description} [{prompt.scope}]"
            for name, prompt in prompts.list_prompts(current_workspace()).items()}


def role_name_completions() -> List[str]:
    """Return the available role names (for ``/role select <name>``)."""
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
    """Return the selectable color theme names (for ``/theme select <name>``)."""
    from moka_code.ui.tui.colors import theme_names

    return theme_names()


def theme_file_completions() -> List[str]:
    """Return the themes that have a file (for ``/theme path`` and ``remove``)."""
    return sorted(getattr(settings.config, "themes", {}))


def theme_descriptions() -> Dict[str, str]:
    """Return theme name -> description (for completion menus)."""
    from moka_code.ui.tui.colors import BUILTIN_THEMES

    custom = set(getattr(settings.config, "themes", {}))
    labels = {(False, True): "built-in", (True, True): "built-in, edited", (True, False): "custom"}
    return {name: labels[(name in custom, name in BUILTIN_THEMES)]
            for name in theme_name_completions()}


def show_subcommands(ui: ChatUIProtocol, command: "Command", args: List[str]) -> None:
    """Bare ``/cmd`` lists its subcommands; a word that is not one says so."""
    from moka_code.ui.tui.msg_types import SysMsgError

    if args:
        ui.chat_history_panel.add_message(
            f"Unknown subcommand '{args[0]}'. Valid: {', '.join(command.subcommands)}",
            msg_type=SysMsgError(), title=command.name)
        return
    lines = [f"{name.ljust(8)} {sub.description}" for name, sub in command.subcommands.items()]
    ui.show_popup(command.name, "Subcommands:\n" + "\n".join(lines), content_padding=0)


async def open_project_sandbox(ui: ChatUIProtocol, name: Optional[str] = None) -> None:
    """Open one sandbox's file (a picker when ``name`` is None) and re-apply the
    active sandbox.

    Behind ``/sandbox edit`` and ``/sandbox create``.
    """
    import asyncio

    from moka_code import projects
    from moka_code.ui.commands.sandbox import describe_sandbox
    from moka_code.ui.external_editor import open_editor, resolve_editor
    from moka_code.ui.tui.msg_types import SysMsg, SysMsgError

    agent = getattr(ui, "agent", None)
    workspace = getattr(agent, "workspace", None) or os.getcwd()

    if not resolve_editor():
        ui.chat_history_panel.add_message(
            "No editor found. Set $VISUAL or $EDITOR.",
            msg_type=SysMsgError(), title="config")
        return

    project = projects.load_project(workspace)
    if name is None:
        if not project.sandboxes:
            ui.chat_history_panel.add_message(
                "No sandboxes yet → /sandbox create <name> <type> [global|local]",
                msg_type=SysMsgError(), title="config")
            return
        descriptions = {n: describe_sandbox(e) for n, e in project.sandboxes.items()}

        def _as_text() -> None:
            ui.chat_history_panel.add_message(
                "\n".join(f"{n.ljust(14)} {d}" for n, d in sorted(descriptions.items())),
                msg_type=SysMsg(), title="config")

        pick(ui, "Sandbox", sorted(project.sandboxes),
             lambda choice: asyncio.ensure_future(open_project_sandbox(ui, choice)),
             descriptions=descriptions, headless=_as_text)
        return

    entry = project.sandboxes.get(name)
    if entry is None or entry.file is None:
        ui.chat_history_panel.add_message(
            f"Unknown sandbox '{name}'.", msg_type=SysMsgError(), title="config")
        return
    await open_editor(ui, entry.file)

    errors: list[str] = []
    project = projects.load_project(workspace, errors)
    if not errors and agent is not None and hasattr(agent, "set_sandbox"):
        agent.set_sandbox(projects.active_spec(project), project.active)

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
    and again once the connection check finishes.
    """
    import asyncio
    from moka_code.harness.endpoint import prewarm_local_resolution

    ui.agent.switch_server(endpoint)
    prewarm_local_resolution(endpoint._original_base_url)

    async def _prewarm():
        await endpoint.prewarm_connection()
        if hasattr(ui, "refresh_status_bar"):
            ui.refresh_status_bar()

    asyncio.ensure_future(_prewarm())
    if hasattr(ui, "refresh_status_bar"):
        ui.refresh_status_bar()


def auto_select(ui: ChatUIProtocol) -> None:
    """Keep the previous selection when it is available; otherwise select the
    first available model (the active server's own first, else the top of
    ``/model``) and say why.

    "Not available" means the saved server is not in ``servers.toml``, or its
    fresh listing no longer has the model, or nothing was ever selected. When
    the server cannot be listed (down, stale, never answered) the selection is
    kept: a network blip must not switch you to another server (the notice band
    says what is wrong).
    """
    from moka_code.harness.endpoint import get_endpoint, unserved_model
    from moka_code.ui.tui.msg_types import SysMsg

    config = settings.config
    active = config.active_server
    previous = config.model_selection.get(active) if active else None
    if active is None:
        reason = "first available"
    elif active not in config.servers:
        reason = f"server '{active}' is not in servers.toml"
    elif previous is None:
        reason = "no model was selected"
    elif unserved_model(active, previous) is not None:
        reason = f"{previous} is no longer served by {active}"
    else:
        return                              # available, or cannot tell: keep
    # Only servers that answered: a stale or never-discovered one is not a choice.
    pairs = [(server, model["id"]) for server in config.servers
             if server not in config.stale_servers
             for model in config.models_by_server.get(server) or []]
    if not pairs:
        return
    own = [pair for pair in pairs if pair[0] == active]
    server, model = min(own or pairs)
    config.save_model_selection(server, model)
    config.set_active_server(server)
    endpoint = get_endpoint(server)
    if endpoint is not None:
        activate_endpoint(ui, endpoint)
    ui.chat_history_panel.add_message(
        f"Selected {model} on {server} ({reason}; /model to change).",
        msg_type=SysMsg(), title="model")


def reapply_endpoint(ui: ChatUIProtocol) -> None:
    """Apply a reloaded ``servers.toml``/state to the live endpoint, and
    rediscover every server's models in the background (every reload:
    ``/reload`` and each ``/config`` edit).

    The endpoint is rebuilt only when its server, definition or selected model
    changed, so an unrelated reload keeps the connection and usage display.
    """
    import asyncio
    from moka_code.harness.endpoint import get_active_endpoint, refresh_catalog

    async def _refresh():
        await refresh_catalog()
        auto_select(ui)
        if hasattr(ui, "refresh_status_bar"):
            ui.refresh_status_bar()

    asyncio.ensure_future(_refresh())

    agent = getattr(ui, "agent", None)
    if not hasattr(agent, "switch_server"):
        return
    current = getattr(agent, "endpoint", None)
    fresh = get_active_endpoint()
    if fresh is None:
        # The selected server is gone (or none is selected): no endpoint.
        if current is not None:
            agent.switch_server(None)
        if hasattr(ui, "refresh_status_bar"):
            ui.refresh_status_bar()
        return
    if current is not None and (fresh.name, fresh.source) == (
            current.name, getattr(current, "source", None)):
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
        await run_in_foreground(ui, argv, cwd=workspace, env=env)
    except OSError as exc:
        ui.chat_history_panel.add_message(
            f"Could not open a {where} shell: {exc}", msg_type=SysMsgError(), title="terminal")
        return
    ui.chat_history_panel.add_message(f"Back from the {where} shell.", msg_type=SysMsg())


async def stop_before(ui: ChatUIProtocol, command: str) -> bool:
    """A command that replaces the history (``/import``, ``/clear``) implies
    ``/stop``: cancel a running turn and wait for its worker to finish (its
    ``finally`` autosaves) before the history changes, so the two never race on
    the session file. False when the turn would not stop in time (the caller must
    not touch the history)."""
    from moka_code.ui.tui.msg_types import SysMsgError

    if not getattr(ui, "is_generating", lambda: False)():
        return True
    stop = getattr(ui, "stop_generation", None)
    if callable(stop):
        stop()
    for _ in range(400):  # bounded (~2 s): give the turn time to unwind (tool abort)
        if getattr(ui, "current_generation_task", None) is None:
            return True
        await asyncio.sleep(0.005)
    ui.chat_history_panel.add_message(
        f"Could not stop the current response; {command} was not applied. Try again.",
        msg_type=SysMsgError(), title="conversation")
    return False

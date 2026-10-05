"""Per-project sandbox lifecycle and image build (``/sandbox``).

Declared as a command tree in the registry, so ``/sandbox <sub>`` dispatches
and completes positionally (subcommands, then each subcommand's own params)
without any manual arg parsing here.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Dict, List, Optional

from moka_code.ui.tui.msg_types import SysMsg, SysMsgError, SysMsgWarning

from .base import ChatUIProtocol, open_project_sandbox, open_shell, pick


SUBCOMMANDS = ("config", "new", "copy", "build", "start", "stop", "terminal", "init")

_SUBCOMMAND_DESCRIPTIONS = {
    "config": "Edit a sandbox's file (picker when no name given)",
    "new": "Create a sandbox: new <global|local> <type> [name]",
    "copy": "Copy a sandbox: copy <name> <global|local> <new-name>",
    "build": "Build a sandbox image from its dockerfile",
    "start": "Activate a sandbox (picker when no name given)",
    "stop": "Deactivate and run tools in-process",
    "terminal": "Open a shell inside the active sandbox",
    "init": "Write a starter Containerfile / Dockerfile next to a sandbox",
}

_BUILD_NOW = "Build now"
_CANCEL = "Cancel"


# --- completion sources ----------------------------------------------------

def _project():
    from moka_code import projects

    return projects.load_project(os.getcwd())


def describe_sandbox(entry) -> str:
    """``<scope> · <description or type>``: scope first, as in the picker and
    the completion menu."""
    return f"{entry.scope} · {entry.description or entry.type}"


def sandbox_id_completions() -> List[str]:
    """Sandbox names visible to the current project (global + local)."""
    return list(_project().sandboxes)


def sandbox_id_descriptions() -> Dict[str, str]:
    """Sandbox name -> ``scope · description`` for the completion menu."""
    return {name: describe_sandbox(entry) for name, entry in _project().sandboxes.items()}


def sandbox_scope_completions() -> List[str]:
    """Scopes a sandbox file can live in."""
    from moka_code import projects

    return list(projects.SCOPES)


def sandbox_type_completions() -> List[str]:
    """Sandbox types ``new`` can create."""
    from moka_code import projects

    return list(projects.SANDBOX_TYPES)


def sandbox_base_completions() -> List[str]:
    """Starter base images for ``init``."""
    from moka_code import sandbox

    return list(sandbox.CONTAINERFILE_BASES)


# --- shared helpers --------------------------------------------------------

def _workspace(ui: ChatUIProtocol) -> str:
    agent = getattr(ui, "agent", None)
    return getattr(agent, "workspace", None) or os.getcwd()


def _activity(ui: ChatUIProtocol, text: str) -> None:
    activity = getattr(ui, "activity", None)
    if callable(activity):
        activity(text)


def _warn_if_busy(ui: ChatUIProtocol) -> bool:
    if getattr(ui, "is_generating", lambda: False)():
        ui.chat_history_panel.add_message(
            "Sandbox changes apply after the current response finishes.",
            msg_type=SysMsgError(), title="sandbox")
        return False
    return True


def _activate(ui: ChatUIProtocol, workspace: str, entry, sandbox_id: str) -> bool:
    """Apply a sandbox entry (or None) to the harness and persist it."""
    from moka_code import projects

    if not _warn_if_busy(ui):
        return False

    agent = getattr(ui, "agent", None)
    if agent is None or not hasattr(agent, "set_sandbox"):
        ui.chat_history_panel.add_message(
            "Sandbox switching is unavailable.", msg_type=SysMsgError(), title="sandbox")
        return False

    agent.set_sandbox(entry.to_spec() if entry is not None else None,
                      sandbox_id if entry is not None else None)
    projects.set_active(workspace, sandbox_id if entry is not None else None)
    active = sandbox_id if entry is not None else "none"
    ui.chat_history_panel.add_message(f"Active sandbox: {active}", msg_type=SysMsg(), title="sandbox")
    if hasattr(ui, "refresh_status_bar"):
        ui.refresh_status_bar()
    return True


def _list_sandboxes(ui: ChatUIProtocol, project) -> None:
    lines = [f"active: {project.active or 'none'}"]
    for name in sorted(project.sandboxes):
        entry = project.sandboxes[name]
        description = f" — {entry.description}" if entry.description else ""
        lines.append(f"{entry.scope.ljust(6)} · {name.ljust(14)} {entry.type}{description}")
    if not project.sandboxes:
        lines.append("(none defined — /sandbox new <global|local> <type>)")
    ui.show_popup("sandboxes", "\n".join(lines), content_padding=0)


async def _do_build(ui: ChatUIProtocol, project, workspace: str, sandbox_id: str) -> bool:
    """Build a sandbox's image; return True on success. Never activates."""
    from moka_code import sandbox

    entry = project.sandboxes.get(sandbox_id)
    if entry is None:
        ui.chat_history_panel.add_message(
            f"Unknown sandbox '{sandbox_id}'.", msg_type=SysMsgError(), title="sandbox")
        return False

    spec = entry.to_spec()
    command = sandbox.build_command(spec, workspace)
    if command is None:
        ui.chat_history_panel.add_message(
            f"Sandbox '{sandbox_id}' has no image/dockerfile to build.",
            msg_type=SysMsgError(), title="sandbox")
        return False
    if not sandbox.runtime_available(spec):
        ui.chat_history_panel.add_message(
            f"Runtime '{spec.runtime}' not found on PATH.",
            msg_type=SysMsgError(), title="sandbox")
        return False

    _activity(ui, f"Building image: {' '.join(command)}")
    code = await sandbox.run_build(
        command, cwd=workspace, on_output=lambda line: _activity(ui, line))
    if code != 0:
        ui.chat_history_panel.add_message(
            f"Image build failed (exit {code}).", msg_type=SysMsgError(), title="sandbox")
        return False

    ui.chat_history_panel.add_message(
        f"Built image '{spec.image}'.", msg_type=SysMsg(), title="sandbox")
    agent = getattr(ui, "agent", None)
    track = getattr(agent, "track_sandbox", None)
    if callable(track) and getattr(agent, "sandbox_name", None) == sandbox_id:
        track(sandbox_id)           # the running sandbox's image is fresh now
    return True


def _offer_build(ui: ChatUIProtocol, project, workspace: str, sandbox_id: str, entry, spec) -> None:
    """Warn that the image is missing and, if possible, ask to build it."""
    from moka_code import sandbox

    command = sandbox.build_command(spec, workspace)
    modal = getattr(ui, "show_search_modal", None)
    if command is not None and callable(modal):
        def _on_accept(choice: str) -> None:
            if choice == _BUILD_NOW:
                asyncio.ensure_future(_build_then_start(ui, workspace, sandbox_id))
        modal(
            f"Image '{spec.image}' not built",
            [_BUILD_NOW, _CANCEL],
            on_accept=_on_accept,
            ordered=True,
        )
        return

    if command is not None:
        message = (
            f"Image '{spec.image}' not built. Build it with:\n  {' '.join(command)}\n"
            f"or run /sandbox build {sandbox_id}."
        )
    else:
        message = (
            f"Image '{spec.image}' not found. Set image/dockerfile in /sandbox config."
        )
    ui.chat_history_panel.add_message(message, msg_type=SysMsgWarning(), title="sandbox")


async def _build_then_start(ui: ChatUIProtocol, workspace: str, sandbox_id: str) -> None:
    from moka_code import projects

    errors: list[str] = []
    project = projects.load_project(workspace, errors)
    if await _do_build(ui, project, workspace, sandbox_id):
        entry = project.sandboxes.get(sandbox_id)
        if entry is not None:
            _activate(ui, workspace, entry, sandbox_id)


def next_sandbox_command(ui: ChatUIProtocol) -> Optional[str]:
    """``/sandbox`` command selecting the sandbox after the active one, then
    none (Shift+Tab). ``None`` when the project defines no sandbox."""
    from moka_code import projects

    project = projects.load_project(_workspace(ui))
    if not project.sandboxes:
        return None
    cycle = [None, *sorted(project.sandboxes)]
    index = cycle.index(project.active) if project.active in cycle else 0
    target = cycle[(index + 1) % len(cycle)]
    return f"/sandbox start {target}" if target else "/sandbox stop"


# --- subcommand handlers ---------------------------------------------------

async def sandbox_help(ui: ChatUIProtocol, args: List[str]) -> None:
    """Bare ``/sandbox`` opens the picker (like ``/model``, ``/role``); with no
    sandbox to pick, or an unknown subcommand, it lists the subcommands."""
    from moka_code import projects

    if not args and projects.load_project(_workspace(ui)).sandboxes:
        await sandbox_start(ui, [])
        return
    lines = [f"{name.ljust(8)} {_SUBCOMMAND_DESCRIPTIONS[name]}" for name in SUBCOMMANDS]
    ui.show_popup("sandbox", "Subcommands:\n" + "\n".join(lines), content_padding=0)


async def sandbox_config(ui: ChatUIProtocol, args: List[str]) -> None:
    await open_project_sandbox(ui, args[0] if args else None)


def _report_errors(ui: ChatUIProtocol, errors: List[str]) -> None:
    if errors:
        ui.chat_history_panel.add_message(
            "Sandbox config errors:\n" + "\n".join(errors),
            msg_type=SysMsgError(), title="sandbox")


async def sandbox_new(ui: ChatUIProtocol, args: List[str]) -> None:
    from moka_code import projects

    if len(args) < 2:
        ui.chat_history_panel.add_message(
            "Usage: /sandbox new <global|local> <type> [name]  "
            f"(type: {', '.join(projects.SANDBOX_TYPES)}; name defaults to the type)",
            msg_type=SysMsgError(), title="sandbox")
        return
    scope, stype = args[0].lower(), args[1].lower()
    name = args[2] if len(args) > 2 else stype
    if scope not in projects.SCOPES or stype not in projects.SANDBOX_TYPES:
        ui.chat_history_panel.add_message(
            f"Scope must be {' or '.join(projects.SCOPES)}; "
            f"type one of {', '.join(projects.SANDBOX_TYPES)}.",
            msg_type=SysMsgError(), title="sandbox")
        return
    try:
        path = projects.create_sandbox(_workspace(ui), scope, stype, name)
    except ValueError as exc:
        ui.chat_history_panel.add_message(str(exc), msg_type=SysMsgError(), title="sandbox")
        return
    ui.chat_history_panel.add_message(
        f"Created {scope} sandbox '{path.stem}': {path}", msg_type=SysMsg(), title="sandbox")
    await open_project_sandbox(ui, path.stem)


async def sandbox_copy(ui: ChatUIProtocol, args: List[str]) -> None:
    from moka_code import projects

    if len(args) != 3:
        ui.chat_history_panel.add_message(
            "Usage: /sandbox copy <name> <global|local> <new-name>",
            msg_type=SysMsgError(), title="sandbox")
        return
    source, scope, name = args[0], args[1].lower(), args[2]
    if scope not in projects.SCOPES:
        ui.chat_history_panel.add_message(
            f"Scope must be {' or '.join(projects.SCOPES)}.",
            msg_type=SysMsgError(), title="sandbox")
        return
    try:
        path = projects.copy_sandbox(_workspace(ui), source, scope, name)
    except (KeyError, ValueError) as exc:
        ui.chat_history_panel.add_message(
            str(exc.args[0]) if isinstance(exc, KeyError) else str(exc),
            msg_type=SysMsgError(), title="sandbox")
        return
    ui.chat_history_panel.add_message(
        f"Copied '{source}' to {scope} sandbox '{path.stem}': {path}",
        msg_type=SysMsg(), title="sandbox")


async def sandbox_stop(ui: ChatUIProtocol, _args: List[str]) -> None:
    _activate(ui, _workspace(ui), None, "none")


async def sandbox_terminal(ui: ChatUIProtocol, _args: List[str]) -> None:
    """A shell in the active sandbox: same mounts, network and limits as tools."""
    from moka_code.sandbox import shell_argv

    spec = getattr(getattr(ui.agent, "transport", None), "spec", None)
    if spec is None:
        ui.chat_history_panel.add_message(
            "No active sandbox. Start one with /sandbox start <id> "
            "(or use /terminal for a host shell).",
            msg_type=SysMsgError(), title="sandbox")
        return
    await open_shell(ui, shell_argv(spec, _workspace(ui)), f"{spec.runtime} sandbox")


async def sandbox_start(ui: ChatUIProtocol, args: List[str]) -> None:
    from moka_code import projects, sandbox

    workspace = _workspace(ui)
    errors: list[str] = []
    project = projects.load_project(workspace, errors)
    if errors:
        ui.chat_history_panel.add_message(
            "Sandbox config errors:\n" + "\n".join(errors),
            msg_type=SysMsgError(), title="sandbox")

    if not args:
        if not project.sandboxes:
            _list_sandboxes(ui, project)
            return
        pick(ui, "Sandboxes", sorted(project.sandboxes),
             lambda choice: asyncio.ensure_future(sandbox_start(ui, [choice])),
             descriptions={name: describe_sandbox(e) for name, e in project.sandboxes.items()},
             footers={project.active: "active"} if project.active else None,
             headless=lambda: _list_sandboxes(ui, project))
        return

    sandbox_id = args[0]
    entry = project.sandboxes.get(sandbox_id)
    if entry is None:
        available = ", ".join(sorted(project.sandboxes)) or "(none)"
        ui.chat_history_panel.add_message(
            f"Unknown sandbox '{sandbox_id}'. Available: {available}",
            msg_type=SysMsgError(), title="sandbox")
        return

    spec = entry.to_spec()
    if not sandbox.runtime_available(spec):
        ui.chat_history_panel.add_message(
            f"Runtime '{spec.runtime}' not found on PATH. Install it first.",
            msg_type=SysMsgError(), title="sandbox")
        return
    if not sandbox.image_present(spec):
        _offer_build(ui, project, workspace, sandbox_id, entry, spec)
        return

    _activate(ui, workspace, entry, sandbox_id)


async def sandbox_build(ui: ChatUIProtocol, args: List[str]) -> None:
    from moka_code import projects

    workspace = _workspace(ui)
    errors: list[str] = []
    project = projects.load_project(workspace, errors)
    if errors:
        ui.chat_history_panel.add_message(
            "Sandbox config errors:\n" + "\n".join(errors),
            msg_type=SysMsgError(), title="sandbox")
    if not args:
        ui.chat_history_panel.add_message(
            "Usage: /sandbox build <id>", msg_type=SysMsgError(), title="sandbox")
        return
    await _do_build(ui, project, workspace, args[0])


async def sandbox_init(ui: ChatUIProtocol, args: List[str]) -> None:
    from moka_code import projects, sandbox

    if not args:
        ui.chat_history_panel.add_message(
            "Usage: /sandbox init <name> [base]", msg_type=SysMsgError(), title="sandbox")
        return
    errors: list[str] = []
    project = projects.load_project(_workspace(ui), errors)
    _report_errors(ui, errors)
    name = args[0]
    entry = project.sandboxes.get(name)
    if entry is None:
        ui.chat_history_panel.add_message(
            f"Unknown sandbox '{name}'.", msg_type=SysMsgError(), title="sandbox")
        return
    if entry.type not in sandbox.CONTAINER_RUNTIMES:
        ui.chat_history_panel.add_message(
            f"/sandbox init needs a {' or '.join(sandbox.CONTAINER_RUNTIMES)} sandbox "
            "(bubblewrap needs no image).",
            msg_type=SysMsgError(), title="sandbox")
        return

    base = args[1] if len(args) > 1 else sandbox.CONTAINERFILE_BASES[0]
    path = entry.file.parent / f"{name}.{sandbox.containerfile_name(entry.type)}"
    if path.exists():
        ui.chat_history_panel.add_message(
            f"{path.name} already exists — edit it or remove it first.",
            msg_type=SysMsgWarning(), title="sandbox")
        return

    path.write_text(sandbox.containerfile_starter(base), encoding="utf-8")

    message = (
        f"Wrote {path.name} (FROM {sandbox.resolve_base(base)}).\n"
        f"Path: {path}\n"
        f'Set `dockerfile = "{path.name}"` and `image` in /sandbox config {name}, '
        f"then /sandbox build {name}."
    )
    try:
        from moka_code.ui.clipboard import copy_to_clipboard

        method = copy_to_clipboard(str(path))
        if method:
            message += f"\n(copied path via {method})"
    except Exception:  # pragma: no cover - clipboard is best effort
        pass

    ui.chat_history_panel.add_message(message, msg_type=SysMsg(), title="sandbox")


__all__ = [
    "SUBCOMMANDS",
    "sandbox_id_completions",
    "sandbox_id_descriptions",
    "sandbox_scope_completions",
    "sandbox_type_completions",
    "sandbox_base_completions",
    "describe_sandbox",
    "sandbox_help",
    "sandbox_config",
    "sandbox_new",
    "sandbox_copy",
    "sandbox_start",
    "sandbox_build",
    "sandbox_init",
    "sandbox_stop",
    "sandbox_terminal",
]

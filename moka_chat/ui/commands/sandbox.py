"""Per-project sandbox lifecycle and image build (``/sandbox``).

Declared as a command tree in the registry, so ``/sandbox <sub>`` dispatches
and completes positionally (subcommands, then each subcommand's own params)
without any manual arg parsing here.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Dict, List

from moka_chat.ui.tui.msg_types import SysMsg, SysMsgError, SysMsgWarning

from .base import ChatUIProtocol, open_project_sandbox, open_shell


SUBCOMMANDS = ("config", "build", "start", "stop", "terminal", "init")

_SUBCOMMAND_DESCRIPTIONS = {
    "config": "Edit this project's sandbox file",
    "build": "Build a sandbox image from its dockerfile",
    "start": "Activate a sandbox (list when no id given)",
    "stop": "Deactivate and run tools in-process",
    "terminal": "Open a shell inside the active sandbox",
    "init": "Write a starter Containerfile / Dockerfile",
}

_BUILD_NOW = "Build now"
_CANCEL = "Cancel"


# --- completion sources ----------------------------------------------------

def _project():
    from moka_chat import projects

    return projects.load_project(os.getcwd())


def sandbox_id_completions() -> List[str]:
    """Sandbox ids defined in the current project."""
    return sorted(_project().sandboxes)


def sandbox_id_descriptions() -> Dict[str, str]:
    """Sandbox id -> description/type for the completion menu."""
    return {
        sandbox_id: (entry.description or entry.type)
        for sandbox_id, entry in _project().sandboxes.items()
    }


def sandbox_runtime_completions() -> List[str]:
    """Container runtimes ``init`` can scaffold."""
    from moka_chat import sandbox

    return list(sandbox.CONTAINER_RUNTIMES)


def sandbox_base_completions() -> List[str]:
    """Friendly base names for ``init`` (python / debian / ubuntu)."""
    from moka_chat import sandbox

    return list(sandbox.CONTAINERFILE_BASES)


def sandbox_base_completions() -> List[str]:
    """Starter base images for ``init``."""
    from moka_chat import sandbox

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
    from moka_chat import projects

    if not _warn_if_busy(ui):
        return False

    agent = getattr(ui, "agent", None)
    if agent is None or not hasattr(agent, "set_sandbox"):
        ui.chat_history_panel.add_message(
            "Sandbox switching is unavailable.", msg_type=SysMsgError(), title="sandbox")
        return False

    agent.set_sandbox(entry.to_spec() if entry is not None else None)
    projects.set_active(workspace, sandbox_id if entry is not None else None)
    active = sandbox_id if entry is not None else "none"
    ui.chat_history_panel.add_message(f"Active sandbox: {active}", msg_type=SysMsg(), title="sandbox")
    if hasattr(ui, "refresh_status_bar"):
        ui.refresh_status_bar()
    return True


def _list_sandboxes(ui: ChatUIProtocol, project) -> None:
    lines = [f"active: {project.active or 'none'}"]
    for sandbox_id in sorted(project.sandboxes):
        entry = project.sandboxes[sandbox_id]
        description = f" — {entry.description}" if entry.description else ""
        lines.append(f"{sandbox_id.ljust(14)} {entry.type}{description}")
    if not project.sandboxes:
        lines.append("(none defined — /sandbox init then /sandbox config)")
    ui.show_popup("sandboxes", "\n".join(lines), content_padding=0)


async def _do_build(ui: ChatUIProtocol, project, workspace: str, sandbox_id: str) -> bool:
    """Build a sandbox's image; return True on success. Never activates."""
    from moka_chat import sandbox

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
    return True


def _offer_build(ui: ChatUIProtocol, project, workspace: str, sandbox_id: str, entry, spec) -> None:
    """Warn that the image is missing and, if possible, ask to build it."""
    from moka_chat import sandbox

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
    from moka_chat import projects

    errors: list[str] = []
    project = projects.load_project(workspace, errors)
    if await _do_build(ui, project, workspace, sandbox_id):
        entry = project.sandboxes.get(sandbox_id)
        if entry is not None:
            _activate(ui, workspace, entry, sandbox_id)


# --- subcommand handlers ---------------------------------------------------

async def sandbox_help(ui: ChatUIProtocol, _args: List[str]) -> None:
    lines = [f"{name.ljust(8)} {_SUBCOMMAND_DESCRIPTIONS[name]}" for name in SUBCOMMANDS]
    ui.show_popup("sandbox", "Subcommands:\n" + "\n".join(lines), content_padding=0)


async def sandbox_config(ui: ChatUIProtocol, _args: List[str]) -> None:
    await open_project_sandbox(ui)


async def sandbox_stop(ui: ChatUIProtocol, _args: List[str]) -> None:
    _activate(ui, _workspace(ui), None, "none")


async def sandbox_terminal(ui: ChatUIProtocol, _args: List[str]) -> None:
    """A shell in the active sandbox: same mounts, network and limits as tools."""
    from moka_chat.sandbox import shell_argv

    spec = getattr(getattr(ui.agent, "transport", None), "spec", None)
    if spec is None:
        ui.chat_history_panel.add_message(
            "No active sandbox. Start one with /sandbox start <id> "
            "(or use /terminal for a host shell).",
            msg_type=SysMsgError(), title="sandbox")
        return
    await open_shell(ui, shell_argv(spec, _workspace(ui)), f"{spec.runtime} sandbox")


async def sandbox_start(ui: ChatUIProtocol, args: List[str]) -> None:
    from moka_chat import projects, sandbox

    workspace = _workspace(ui)
    errors: list[str] = []
    project = projects.load_project(workspace, errors)
    if errors:
        ui.chat_history_panel.add_message(
            "Sandbox config errors:\n" + "\n".join(errors),
            msg_type=SysMsgError(), title="sandbox")

    if not args:
        _list_sandboxes(ui, project)
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
    from moka_chat import projects

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
    from moka_chat import sandbox

    if not args:
        ui.chat_history_panel.add_message(
            "Usage: /sandbox init podman|docker [base]",
            msg_type=SysMsgError(), title="sandbox")
        return
    runtime = args[0].lower()
    if runtime not in sandbox.CONTAINER_RUNTIMES:
        ui.chat_history_panel.add_message(
            f"/sandbox init supports {', '.join(sandbox.CONTAINER_RUNTIMES)} "
            "(bubblewrap needs no image).",
            msg_type=SysMsgError(), title="sandbox")
        return

    workspace = _workspace(ui)
    base = args[1] if len(args) > 1 else sandbox.CONTAINERFILE_BASES[0]
    path = Path(workspace) / sandbox.containerfile_name(runtime)
    if path.exists():
        ui.chat_history_panel.add_message(
            f"{path.name} already exists — edit it or remove it first.",
            msg_type=SysMsgWarning(), title="sandbox")
        return

    path.write_text(sandbox.containerfile_starter(base), encoding="utf-8")

    message = (
        f"Wrote {path.name} (FROM {sandbox.resolve_base(base)}).\n"
        f"Path: {path}\n"
        f'Set `dockerfile = "{path.name}"` in /sandbox config, then /sandbox start <id>.'
    )
    try:
        from moka_chat.ui.clipboard import copy_to_clipboard

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
    "sandbox_runtime_completions",
    "sandbox_base_completions",
    "sandbox_help",
    "sandbox_config",
    "sandbox_start",
    "sandbox_build",
    "sandbox_init",
    "sandbox_stop",
    "sandbox_terminal",
]

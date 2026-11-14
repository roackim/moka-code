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

from .base import ChatUIProtocol, confirm_remove, open_project_sandbox, open_shell, pick, show_path


_BUILD_NOW = "Build now"
_CANCEL = "Cancel"


# --- completion sources ----------------------------------------------------

def _project():
    from moka_code import projects
    from moka_code.harness.harness import current_workspace

    return projects.load_project(current_workspace())


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
    """Sandbox types ``create`` can write."""
    from moka_code import projects

    return list(projects.SANDBOX_TYPES)


#: The files ``edit``/``path`` open: how the sandbox runs, how its image is built.
SANDBOX_FILES = ("runcfg", "image")


def sandbox_file_completions() -> List[str]:
    return list(SANDBOX_FILES)


def sandbox_file_descriptions() -> Dict[str, str]:
    return {"runcfg": "its .toml: how it runs",
            "image": "its Containerfile: how its image is built (podman/docker)"}


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
        lines.append("(none defined — /sandbox create <name> <type> [global|local])")
    ui.show_popup("sandboxes", "\n".join(lines), content_padding=0)


#: Build output kept for the failure message (its full log is in /activity).
_BUILD_TAIL = 40


#: The runtimes' closing summary of a failed build (podman, docker BuildKit):
#: it only says that a step failed, the line before it says why.
_BUILD_SUMMARIES = ("error: building at step", "error: failed to solve")


def _failure_line(lines) -> str:
    """The build output line that most likely says why it failed: the last one
    mentioning an error that is not the runtime's closing summary, else that
    summary, else the last non-empty line (a heuristic: runtimes and tools word
    their errors differently)."""
    lines = [line.strip() for line in lines if line.strip()]
    errors = [line for line in lines if "error" in line.lower()]
    causes = [line for line in errors if not line.lower().startswith(_BUILD_SUMMARIES)]
    return (causes or errors or lines or ["no output"])[-1]


def _runtime_missing(name: str, runtime: str) -> str:
    return f"{runtime} not found on PATH → install it, or /sandbox edit {name}"


def _unknown(project, name: str) -> str:
    have = ", ".join(sorted(project.sandboxes)) or "none"
    return f"Unknown sandbox '{name}' (have: {have}) → /sandbox create {name} <type>"


async def _do_build(ui: ChatUIProtocol, project, workspace: str, sandbox_id: str) -> bool:
    """Build a sandbox's image; return True on success. Never activates.

    Shows a job line on the notice band while it runs (spinner, clock, latest
    output line); the output streams to /activity. A failure posts the line
    that says why, with the end of the log behind a click."""
    import time
    from collections import deque

    from moka_code import sandbox
    from moka_code.ui.notice_feed import elapsed
    from moka_code.ui.tui.msg_types import SysMsgSuccess

    entry = project.sandboxes.get(sandbox_id)
    if entry is None:
        ui.chat_history_panel.add_message(
            _unknown(project, sandbox_id), msg_type=SysMsgError(), title="sandbox")
        return False

    spec = entry.to_spec()
    command = sandbox.build_command(spec, workspace)
    if command is None:
        what = ("it is bubblewrap: no image" if spec.runtime not in sandbox.CONTAINER_RUNTIMES
                else "set image and dockerfile in /sandbox edit " + sandbox_id)
        ui.chat_history_panel.add_message(
            f"sandbox {sandbox_id} has nothing to build → {what}",
            msg_type=SysMsgError(), title="sandbox")
        return False
    if not sandbox.runtime_available(spec):
        ui.chat_history_panel.add_message(
            _runtime_missing(sandbox_id, spec.runtime), msg_type=SysMsgError(), title="sandbox")
        return False

    feed = getattr(ui, "notice_feed", None)
    label = f"building {sandbox_id}"
    if feed is not None and feed.running(label):
        ui.chat_history_panel.add_message(
            f"sandbox {sandbox_id} is already building (see the band, or /activity)",
            msg_type=SysMsgWarning(), title="sandbox")
        return False
    start_job = getattr(ui, "start_job", None)
    job = start_job(label) if callable(start_job) else None
    tail: deque = deque(maxlen=_BUILD_TAIL)

    def _output(line: str) -> None:
        _activity(ui, line)
        tail.append(line)
        if job is not None and line.strip():
            job.last = line.strip()

    _activity(ui, f"Building image: {' '.join(command)}")
    started = time.monotonic()
    try:
        code = await sandbox.run_build(command, cwd=workspace, on_output=_output)
    except OSError as exc:
        code, tail = None, deque([str(exc)])
    finally:
        if job is not None:
            ui.end_job(job)
    if code != 0:
        status = f"exit {code}" if code is not None else "did not start"
        # The fix is the file; once it is saved, the band's stale-image notice
        # says to /sandbox build.
        text = (f"build {sandbox_id} failed ({status}): {_failure_line(tail)} "
                f"→ /sandbox edit {sandbox_id} image")
        if tail:
            text += (f"\n\nLast {len(tail)} lines of the build (all of it is in /activity):\n"
                     + "\n".join(tail))
        ui.chat_history_panel.add_message(text, msg_type=SysMsgError(), title="sandbox")
        return False

    ui.chat_history_panel.add_message(
        f"built {sandbox_id} in {elapsed(time.monotonic() - started)} (image {spec.image})",
        msg_type=SysMsgSuccess(), title="sandbox")
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
            f"Image '{spec.image}' not found. Set image/dockerfile in /sandbox edit."
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

async def sandbox_help(ui: ChatUIProtocol, args: List[str], command) -> None:
    """Bare ``/sandbox`` opens the picker (like ``/model``); with no
    sandbox to pick, or an unknown subcommand, it lists the subcommands."""
    from moka_code import projects

    if not args and projects.load_project(_workspace(ui)).sandboxes:
        await sandbox_start(ui, [])
        return
    lines = [f"{name.ljust(8)} {sub.description}" for name, sub in command.subcommands.items()]
    ui.show_popup("sandbox", "Subcommands:\n" + "\n".join(lines), content_padding=0)


def _which_file(ui: ChatUIProtocol, args: List[str], verb: str) -> Optional[str]:
    """The ``runcfg``/``image`` word after the name (``runcfg`` when absent);
    None (reported) when it is something else."""
    which = args[1].lower() if len(args) > 1 else "runcfg"
    if len(args) > 2 or which not in SANDBOX_FILES:
        ui.chat_history_panel.add_message(
            f"Usage: /sandbox {verb} [name] [{'|'.join(SANDBOX_FILES)}]",
            msg_type=SysMsgError(), title="sandbox")
        return None
    return which


async def sandbox_edit(ui: ChatUIProtocol, args: List[str]) -> None:
    """``/sandbox edit [name] [runcfg|image]``: the sandbox's ``.toml`` (how it
    runs) or its Containerfile (how its image is built)."""
    which = _which_file(ui, args, "edit")
    if which is not None:
        await open_project_sandbox(ui, args[0] if args else None, which)


async def sandbox_path(ui: ChatUIProtocol, args: List[str]) -> None:
    """Print (and copy) a sandbox's file path (``runcfg``, or ``image``: its
    build file), so it can be edited outside the TUI while the session keeps
    running. Defaults to the active sandbox."""
    from moka_code import projects

    which = _which_file(ui, args, "path")
    if which is None:
        return
    workspace = _workspace(ui)
    errors: list[str] = []
    project = projects.load_project(workspace, errors)
    _report_errors(ui, errors)

    name = args[0] if args else project.active
    if not name:
        ui.chat_history_panel.add_message(
            "No active sandbox. Usage: /sandbox path [name] [runcfg|image].",
            msg_type=SysMsgError(), title="sandbox")
        return
    entry = project.sandboxes.get(name)
    if entry is None or entry.file is None:
        ui.chat_history_panel.add_message(
            _unknown(project, name), msg_type=SysMsgError(), title="sandbox")
        return

    if which == "image":
        path = entry.build_file()
        if path is None:
            ui.chat_history_panel.add_message(
                f"Sandbox '{name}' is {entry.type}: it has no image.",
                msg_type=SysMsgError(), title="sandbox")
            return
        show_path(ui, "sandbox", f"Sandbox '{name}' image build file", path)
        return
    show_path(ui, "sandbox", f"Sandbox '{name}' file ({entry.scope})", entry.file)


async def sandbox_remove(ui: ChatUIProtocol, args: List[str]) -> None:
    """``/sandbox remove <name>``: delete a sandbox's file after Remove / Cancel
    (a dockerfile next to it stays). Removing the active one stops it."""
    from moka_code import projects

    if len(args) != 1:
        ui.chat_history_panel.add_message(
            "Usage: /sandbox remove <name>", msg_type=SysMsgError(), title="sandbox")
        return
    workspace = _workspace(ui)
    project = projects.load_project(workspace)
    name = args[0]
    entry = project.sandboxes.get(name)
    if entry is None or entry.file is None:
        ui.chat_history_panel.add_message(
            _unknown(project, name), msg_type=SysMsgError(), title="sandbox")
        return

    def _remove() -> None:
        if name == project.active and not _activate(ui, workspace, None, "none"):
            return
        try:
            entry.file.unlink()
        except OSError as exc:
            ui.chat_history_panel.add_message(str(exc), msg_type=SysMsgError(), title="sandbox")
            return
        ui.chat_history_panel.add_message(
            f"Removed {entry.scope} sandbox: {name}", msg_type=SysMsg(), title="sandbox")

    confirm_remove(ui, f"Remove {entry.scope} sandbox '{name}'?", _remove)


def _report_errors(ui: ChatUIProtocol, errors: List[str]) -> None:
    """One notice per sandbox file error (a single message would show only its
    first line on the band)."""
    for error in errors:
        ui.chat_history_panel.add_message(
            f"{error} → /sandbox edit", msg_type=SysMsgError(), title="sandbox")


async def sandbox_create(ui: ChatUIProtocol, args: List[str]) -> None:
    """``/sandbox create <name> <type> [global|local]``: write a starter sandbox
    file (global by default), and for a container its starter Containerfile
    next to it, then open the sandbox file."""
    from moka_code import projects, sandbox

    if not 2 <= len(args) <= 3:
        ui.chat_history_panel.add_message(
            "Usage: /sandbox create <name> <type> [global|local]  "
            f"(type: {', '.join(projects.SANDBOX_TYPES)})",
            msg_type=SysMsgError(), title="sandbox")
        return
    name, stype = args[0], args[1].lower()
    scope = args[2].lower() if len(args) == 3 else "global"
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
    text = f"Created {scope} sandbox '{path.stem}': {path}"
    if stype in sandbox.CONTAINER_RUNTIMES:
        text += (f"\nIts image builds from {projects.default_containerfile(stype, path.stem)} "
                 f"→ /sandbox edit {path.stem} image, then /sandbox build {path.stem}")
    ui.chat_history_panel.add_message(text, msg_type=SysMsg(), title="sandbox")
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


async def sandbox_restart(ui: ChatUIProtocol, args: List[str]) -> None:
    """Restart the active sandbox from its file and its current image (never a
    rebuild: that is ``/sandbox build``). Checked like ``start`` first, so a
    missing runtime or image leaves the running one alone; then the old worker
    is shut down and waited for (its ``--rm`` container exits) before the fresh
    one is applied."""
    from moka_code import projects, sandbox

    if args:
        ui.chat_history_panel.add_message(
            "Usage: /sandbox restart", msg_type=SysMsgError(), title="sandbox")
        return
    if not _warn_if_busy(ui):
        return
    workspace = _workspace(ui)
    errors: list[str] = []
    project = projects.load_project(workspace, errors)
    _report_errors(ui, errors)
    name = project.active
    entry = project.sandboxes.get(name) if name else None
    if entry is None:
        ui.chat_history_panel.add_message(
            "No active sandbox to restart." if not name else
            f"Active sandbox '{name}' is no longer defined; nothing restarted.",
            msg_type=SysMsgError(), title="sandbox")
        return
    spec = entry.to_spec()
    if not sandbox.runtime_available(spec):
        ui.chat_history_panel.add_message(
            _runtime_missing(name, spec.runtime) + " (nothing restarted)",
            msg_type=SysMsgError(), title="sandbox")
        return
    if not sandbox.image_present(spec):
        _offer_build(ui, project, workspace, name, entry, spec)
        return

    stop = getattr(getattr(ui.agent, "transport", None), "stop", None)
    if callable(stop):
        await stop()
    if _activate(ui, workspace, entry, name):
        ui.chat_history_panel.add_message(
            f"Sandbox '{name}' restarted ({spec.runtime}"
            + (f", image {spec.image}" if spec.image else "") + ").",
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
    _report_errors(ui, errors)

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
        ui.chat_history_panel.add_message(
            _unknown(project, sandbox_id), msg_type=SysMsgError(), title="sandbox")
        return

    spec = entry.to_spec()
    if not sandbox.runtime_available(spec):
        ui.chat_history_panel.add_message(
            _runtime_missing(sandbox_id, spec.runtime), msg_type=SysMsgError(), title="sandbox")
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
    _report_errors(ui, errors)
    if not args:
        ui.chat_history_panel.add_message(
            "Usage: /sandbox build <id>", msg_type=SysMsgError(), title="sandbox")
        return
    # In the background: other commands keep working while it builds (the
    # band shows it running).
    asyncio.ensure_future(_do_build(ui, project, workspace, args[0]))


__all__ = [
    "SANDBOX_FILES",
    "sandbox_id_completions",
    "sandbox_id_descriptions",
    "sandbox_scope_completions",
    "sandbox_type_completions",
    "sandbox_file_completions",
    "sandbox_file_descriptions",
    "describe_sandbox",
    "sandbox_help",
    "sandbox_edit",
    "sandbox_path",
    "sandbox_create",
    "sandbox_remove",
    "sandbox_copy",
    "sandbox_start",
    "sandbox_restart",
    "sandbox_build",
    "sandbox_stop",
    "sandbox_terminal",
]

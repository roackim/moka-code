"""Per-project sandbox selection (``/sandbox``)."""

from __future__ import annotations

import os
from typing import List

from pico_chat.ui.tui.msg_types import SysMsg, SysMsgError

from .base import ChatUIProtocol


def _workspace(ui: ChatUIProtocol) -> str:
    agent = getattr(ui, "agent", None)
    return getattr(agent, "workspace", None) or os.getcwd()


def sandbox_name_completions() -> List[str]:
    """Sandbox ids defined in the current project (plus ``none``)."""
    from pico_chat import projects

    project = projects.load_project(os.getcwd())
    return ["none", *sorted(project.sandboxes)]


async def cmd_sandbox(ui: ChatUIProtocol, args: List[str]):
    from pico_chat import projects

    workspace = _workspace(ui)
    errors: list[str] = []
    project = projects.load_project(workspace, errors)
    if errors:
        ui.chat_history_panel.add_message(
            "Sandbox config errors:\n" + "\n".join(errors),
            msg_type=SysMsgError(), title="sandbox")

    if not args:
        lines = [f"active: {project.active or 'none'}"]
        for sandbox_id in sorted(project.sandboxes):
            lines.append(f"{sandbox_id.ljust(16)} {project.sandboxes[sandbox_id].type}")
        if not project.sandboxes:
            lines.append("(no sandboxes defined — run /config sandbox)")
        ui.show_popup("sandboxes", "\n".join(lines), content_padding=0)
        return

    sandbox_id = args[0]
    if sandbox_id in ("none", "-"):
        entry = None
    else:
        entry = project.sandboxes.get(sandbox_id)
        if entry is None:
            available = ", ".join(sorted(project.sandboxes)) or "(none)"
            ui.chat_history_panel.add_message(
                f"Unknown sandbox '{sandbox_id}'. Available: {available}",
                msg_type=SysMsgError(), title="sandbox")
            return

    if getattr(ui, "is_generating", lambda: False)():
        ui.chat_history_panel.add_message(
            "Sandbox changes apply after the current response finishes.",
            msg_type=SysMsgError(), title="sandbox")
        return

    agent = getattr(ui, "agent", None)
    if agent is None or not hasattr(agent, "set_sandbox"):
        ui.chat_history_panel.add_message(
            "Sandbox switching is unavailable.", msg_type=SysMsgError(), title="sandbox")
        return

    agent.set_sandbox(entry.to_spec() if entry is not None else None)
    projects.set_active(workspace, sandbox_id if entry is not None else None)

    active = sandbox_id if entry is not None else "none"
    ui.chat_history_panel.add_message(f"Active sandbox: {active}", msg_type=SysMsg(), title="sandbox")
    if hasattr(ui, "refresh_status_bar"):
        ui.refresh_status_bar()


__all__ = ["cmd_sandbox", "sandbox_name_completions"]

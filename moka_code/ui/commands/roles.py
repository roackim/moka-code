"""Role listing and switching slash command."""

from __future__ import annotations

from typing import List

from moka_code.ui.tui.msg_types import SysMsg, SysMsgError

from .base import ChatUIProtocol


async def cmd_role(ui: ChatUIProtocol, args: List[str]):
    from moka_code.harness import roles

    if not args:
        active = getattr(getattr(ui, "agent", None), "role", None)
        active_name = active.name if active else "agent"
        lines = [f"active: {active_name}"]
        for name in roles.list_roles():
            role = roles.load_role(name)
            lines.append(f"{name.ljust(14)} {role.description}")
        ui.show_popup("roles", "\n".join(lines), content_padding=0)
        return

    name = args[0]
    try:
        role = roles.load_role(name)
        ui.switch_role(role)
    except (KeyError, OSError, ValueError, RuntimeError) as exc:
        ui.chat_history_panel.add_message(str(exc), msg_type=SysMsgError())
        return
    ui.chat_history_panel.add_message(f"Active role: {role.name}", msg_type=SysMsg())
    if hasattr(ui, "refresh_status_bar"):
        ui.refresh_status_bar()


__all__ = ["cmd_role"]

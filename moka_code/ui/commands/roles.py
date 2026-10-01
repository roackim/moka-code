"""Role listing and switching slash command."""

from __future__ import annotations

from typing import List

from moka_code.ui.tui.msg_types import SysMsg, SysMsgError

from .base import ChatUIProtocol, pick, role_descriptions


def _switch(ui: ChatUIProtocol, name: str) -> None:
    from moka_code.harness import roles

    try:
        role = roles.load_role(name)
        ui.switch_role(role)
    except (KeyError, OSError, ValueError, RuntimeError) as exc:
        ui.chat_history_panel.add_message(str(exc), msg_type=SysMsgError())
        return
    ui.chat_history_panel.add_message(f"Active role: {role.name}", msg_type=SysMsg())
    if hasattr(ui, "refresh_status_bar"):
        ui.refresh_status_bar()


async def cmd_role(ui: ChatUIProtocol, args: List[str]):
    """``/role`` opens the picker; ``/role <name>`` switches directly."""
    from moka_code.harness import roles

    if args:
        _switch(ui, args[0])
        return
    active = getattr(getattr(ui, "agent", None), "role", None)
    active_name = active.name if active else "agent"
    descriptions = role_descriptions()

    def _as_text() -> None:
        lines = [f"active: {active_name}"]
        lines += [f"{name.ljust(14)} {descriptions.get(name, '')}" for name in roles.list_roles()]
        ui.show_popup("roles", "\n".join(lines), content_padding=0)

    pick(ui, "Roles", roles.list_roles(), lambda name: _switch(ui, name),
         descriptions=descriptions, footers={active_name: "active"}, headless=_as_text)


def next_role_command(ui: ChatUIProtocol) -> str:
    """``/role`` command selecting the role after the active one (Tab)."""
    from moka_code.harness import roles

    names = roles.list_roles()
    current = getattr(getattr(ui, "agent", None), "role", None)
    index = names.index(current.name) if current and current.name in names else -1
    return f"/role {names[(index + 1) % len(names)]}"


__all__ = ["cmd_role", "next_role_command"]

"""``/role``: switch the active role, or edit, create, locate or remove a role
file (``roles/<name>.toml``)."""

from __future__ import annotations

import asyncio
from typing import Callable, Dict, List, Optional

from moka_code.ui.tui.msg_types import SysMsg, SysMsgError

from .base import (
    ChatUIProtocol,
    Command,
    Param,
    confirm_remove,
    pick,
    role_descriptions,
    role_name_completions,
    show_path,
)

# Reloads hand-edited files and applies them (``core.reload_and_apply``).
Reload = Callable[[ChatUIProtocol, Optional[str]], None]


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


def _error(ui: ChatUIProtocol, text: str) -> None:
    ui.chat_history_panel.add_message(text, msg_type=SysMsgError(), title="role")


def _pick_role(ui: ChatUIProtocol, title: str, then) -> None:
    """A picker over the roles; ``then(name)`` runs for the chosen one."""
    from moka_code.harness import roles

    descriptions = role_descriptions()

    def _as_text() -> None:
        ui.chat_history_panel.add_message(
            "\n".join(f"{name.ljust(14)} {descriptions.get(name, '')}" for name in roles.list_roles()),
            msg_type=SysMsg(), title="role")

    pick(ui, title, roles.list_roles(), lambda name: asyncio.ensure_future(then(ui, [name])),
         descriptions=descriptions, headless=_as_text)


def _known(ui: ChatUIProtocol, name: str) -> bool:
    from moka_code.harness import roles

    if name in roles.list_roles():
        return True
    _error(ui, f"Role not found: {name}. /role create {name} makes it.")
    return False


async def _open_and_reload(ui: ChatUIProtocol, path, reload: Reload) -> None:
    from moka_code.ui.external_editor import open_editor, resolve_editor

    if not resolve_editor():
        _error(ui, "No editor found. Set $VISUAL or $EDITOR.")
        return
    await open_editor(ui, path)
    reload(ui, "role")


def _role_edit(reload: Reload):
    async def role_edit(ui: ChatUIProtocol, args: List[str]) -> None:
        """``/role edit [name]``: open an existing role's file, then reload."""
        from moka_code.harness import roles

        if not args:
            _pick_role(ui, "Edit role", role_edit)
            return
        if _known(ui, args[0]):
            await _open_and_reload(ui, roles.role_file(args[0]), reload)

    return role_edit


def _role_create(reload: Reload):
    async def role_create(ui: ChatUIProtocol, args: List[str]) -> None:
        """``/role create <name>``: write a starter role file and open it."""
        from moka_code.harness import roles

        if len(args) != 1:
            _error(ui, "Usage: /role create <name>")
            return
        name = args[0]
        if name in roles.list_roles():
            _error(ui, f"Role '{name}' already exists. /role edit {name} opens it.")
            return
        if name in VERBS:
            _error(ui, f"'{name}' is a /role subcommand; pick another name.")
            return
        try:
            path = roles.ensure_role_file(name)
        except (OSError, ValueError) as exc:
            _error(ui, str(exc))
            return
        await _open_and_reload(ui, path, reload)

    return role_create


async def role_path(ui: ChatUIProtocol, args: List[str]) -> None:
    """``/role path [name]``: print and copy a role's file path (the active role
    when no name is given)."""
    from moka_code.harness import roles

    active = getattr(getattr(ui, "agent", None), "role", None)
    name = args[0] if args else (active.name if active else None)
    if not name:
        _error(ui, "No active role. Usage: /role path [name].")
        return
    if _known(ui, name):
        show_path(ui, "role", f"Role '{name}' file", roles.role_file(name))


async def role_remove(ui: ChatUIProtocol, args: List[str]) -> None:
    """``/role remove <name>``: delete a role's file after Remove / Cancel. A
    running conversation keeps the role in memory."""
    from moka_code.harness import roles

    if len(args) != 1:
        _error(ui, "Usage: /role remove <name>")
        return
    name = args[0]
    if not _known(ui, name):
        return

    def _remove() -> None:
        try:
            roles.delete_role(name)
        except (KeyError, OSError, ValueError) as exc:
            _error(ui, str(exc))
            return
        ui.chat_history_panel.add_message(f"Removed role: {name}", msg_type=SysMsg(), title="role")

    confirm_remove(ui, f"Remove roles/{name}.toml?", _remove)


def _name(required: bool) -> Param:
    return Param("NAME", required=required, completions=role_name_completions,
                 descriptions=role_descriptions)


VERBS = ("edit", "create", "path", "remove")


def role_subcommands(reload: Reload) -> Dict[str, Command]:
    """``/role``'s subcommands; ``reload`` applies an edited role file (the
    registry passes ``reload_and_apply``)."""
    return {
        "edit": Command("role edit", "Edit a role's file (picker when no name given)",
                        handler=_role_edit(reload), params=[_name(False)]),
        "create": Command("role create", "Create a role file from a starter",
                          handler=_role_create(reload), params=[Param("NAME", required=True)]),
        "path": Command("role path", "Print (and copy) a role's file path",
                        handler=role_path, params=[_name(False)]),
        "remove": Command("role remove", "Remove a role's file (asks to confirm)",
                          handler=role_remove, params=[_name(True)]),
    }


def next_role_command(ui: ChatUIProtocol) -> str:
    """``/role`` command selecting the role after the active one (Tab)."""
    from moka_code.harness import roles

    names = roles.list_roles()
    current = getattr(getattr(ui, "agent", None), "role", None)
    index = names.index(current.name) if current and current.name in names else -1
    return f"/role {names[(index + 1) % len(names)]}"


__all__ = ["cmd_role", "next_role_command", "role_subcommands"]

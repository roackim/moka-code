"""``/theme``: select the color theme, or edit, create, locate or remove a theme
file (``themes/<name>.toml``)."""

from __future__ import annotations

import asyncio
from typing import Callable, Dict, List, Optional

from moka_code import settings
from moka_code.ui.tui.msg_types import SysMsg, SysMsgError

from .base import (
    ChatUIProtocol,
    Command,
    Param,
    confirm_remove,
    pick,
    show_path,
    show_subcommands,
    theme_descriptions,
    theme_file_completions,
    theme_name_completions,
)

# Reloads hand-edited files and applies them (``core.reload_and_apply``).
Reload = Callable[[ChatUIProtocol, Optional[str]], None]


def _error(ui: ChatUIProtocol, text: str) -> None:
    ui.chat_history_panel.add_message(text, msg_type=SysMsgError(), title="theme")


def _select(ui: ChatUIProtocol, name: str) -> None:
    from moka_code.ui.tui.colors import set_theme

    set_theme(name)
    settings.config.save_active_theme(name)
    refresh = getattr(ui, "refresh_theme", None)
    if callable(refresh):
        refresh()
    ui.chat_history_panel.add_message(f"Theme: {name}", msg_type=SysMsg(), title="theme")
    if hasattr(ui, "refresh_status_bar"):
        ui.refresh_status_bar()


async def _open_picker(ui: ChatUIProtocol) -> None:
    from moka_code.ui.tui.colors import set_theme, theme
    from moka_code.ui.tui.components.menu import sort_items

    names = theme_name_completions()
    descriptions = theme_descriptions()
    previous = settings.config.get_active_theme()
    footers = {previous: "active"} if previous in names else {}
    initial = names.index(previous) if previous in names else 0
    modal = None

    # Palette overview shown while browsing (registers as an overlay).
    preview = None
    compositor = getattr(ui, "compositor", None)
    if compositor is not None:
        from moka_code.ui.tui.components.theme_preview import ThemePreview

        preview = ThemePreview()
        preview.set_compositor(compositor)
        preview.show()

    def _close_preview():
        if preview is not None:
            preview.hide()

    def _restyle():
        # The picker itself is transient, so refresh it manually as the palette
        # changes; chatTUI.refresh_theme() only knows about long-lived chrome.
        if modal is not None:
            apply_theme = getattr(modal, "apply_theme", None)
            if callable(apply_theme):
                apply_theme()
            modal.frame_color = theme.FOCUSED
            modal.content_color = theme.DEFAULT

    def _apply(name: str, persist: bool) -> None:
        if persist:
            _select(ui, name)
            return
        set_theme(name)
        refresh = getattr(ui, "refresh_theme", None)
        if callable(refresh):
            refresh()
        _restyle()

    def _accept(item):
        _close_preview()
        _apply(item, persist=True)

    def _cancel():
        # Revert to the configured theme: reload config, then re-apply it.
        _close_preview()
        settings.reload_config()
        set_theme(settings.config.get_active_theme())
        refresh = getattr(ui, "refresh_theme", None)
        if callable(refresh):
            refresh()
        _restyle()

    show = getattr(ui, "show_search_modal", None)
    if show is None:
        _close_preview()
        lines = [f"{name.ljust(12)} {descriptions[name]}" for name in sort_items(names)]
        ui.chat_history_panel.add_message("\n".join(lines), msg_type=SysMsg(), title="theme")
        return
    modal = show("Themes", names, descriptions=descriptions, footers=footers,
                 on_accept=_accept,
                 on_cancel=_cancel,
                 on_highlight=lambda item: _apply(item, persist=False),
                 initial_index=initial)
    # Keep the picker short so the top overview strip and the bottom-anchored
    # picker never overlap on a normal terminal.
    if modal is not None and hasattr(modal, "max_height"):
        modal.max_height = 8


async def theme_select(ui: ChatUIProtocol, args: List[str]) -> None:
    """``/theme select`` opens the picker (live preview while browsing);
    ``/theme select <name>`` selects directly."""
    if not args:
        await _open_picker(ui)
        return
    if args[0] not in theme_name_completions():
        _error(ui, f"Theme not found: {args[0]}. /theme create {args[0]} makes it.")
        return
    _select(ui, args[0])


async def _open_and_reload(ui: ChatUIProtocol, path, reload: Reload) -> None:
    from moka_code.ui.external_editor import open_editor, resolve_editor

    if not resolve_editor():
        _error(ui, "No editor found. Set $VISUAL or $EDITOR.")
        return
    await open_editor(ui, path)
    reload(ui, "theme")


def _theme_edit(reload: Reload):
    async def theme_edit(ui: ChatUIProtocol, args: List[str]) -> None:
        """``/theme edit [name]``: open a theme's file, then reload. A built-in
        without a file is first written out with its palette."""
        from moka_code.ui.tui.colors import write_theme_file

        if not args:
            pick(ui, "Edit theme", theme_name_completions(),
                 lambda name: asyncio.ensure_future(theme_edit(ui, [name])),
                 descriptions=theme_descriptions())
            return
        name = args[0]
        if name not in theme_name_completions():
            _error(ui, f"Theme not found: {name}. /theme create {name} makes it.")
            return
        path = settings.config.theme_file(name)
        if not path.exists():
            try:
                path = write_theme_file(name, name)
            except (OSError, ValueError, KeyError) as exc:
                _error(ui, str(exc))
                return
        await _open_and_reload(ui, path, reload)

    return theme_edit


def _theme_create(reload: Reload):
    async def theme_create(ui: ChatUIProtocol, args: List[str]) -> None:
        """``/theme create <name>``: write a theme file from the active palette
        and open it."""
        from moka_code.ui.tui.colors import BUILTIN_THEMES, write_theme_file

        if len(args) != 1:
            _error(ui, "Usage: /theme create <name>")
            return
        name = args[0]
        if name in BUILTIN_THEMES:
            _error(ui, f"'{name}' is a built-in theme. /theme edit {name} writes an editable copy.")
            return
        if settings.config.theme_file(name).exists():
            _error(ui, f"Theme '{name}' already exists. /theme edit {name} opens it.")
            return
        try:
            path = write_theme_file(name, settings.config.get_active_theme())
        except (OSError, ValueError, KeyError) as exc:
            _error(ui, str(exc))
            return
        await _open_and_reload(ui, path, reload)

    return theme_create


def _file_of(ui: ChatUIProtocol, name: str):
    """The theme's file, or None after saying why there is none."""
    path = settings.config.theme_file(name)
    if path.exists():
        return path
    if name in theme_name_completions():
        _error(ui, f"Theme '{name}' is built-in and has no file. /theme edit {name} writes one.")
    else:
        _error(ui, f"Theme not found: {name}. /theme create {name} makes it.")
    return None


async def theme_path(ui: ChatUIProtocol, args: List[str]) -> None:
    """``/theme path [name]``: print and copy a theme's file path (the active
    theme when no name is given)."""
    name = args[0] if args else settings.config.get_active_theme()
    path = _file_of(ui, name)
    if path is not None:
        show_path(ui, "theme", f"Theme '{name}' file", path)


def _theme_remove(reload: Reload):
    async def theme_remove(ui: ChatUIProtocol, args: List[str]) -> None:
        """``/theme remove <name>``: delete a theme's file after Remove / Cancel,
        then reload (a built-in falls back to its own palette)."""
        if len(args) != 1:
            _error(ui, "Usage: /theme remove <name>")
            return
        name = args[0]
        path = _file_of(ui, name)
        if path is None:
            return

        def _remove() -> None:
            try:
                path.unlink()
            except OSError as exc:
                _error(ui, str(exc))
                return
            ui.chat_history_panel.add_message(f"Removed theme: {name}", msg_type=SysMsg(),
                                              title="theme")
            reload(ui, "theme")

        confirm_remove(ui, f"Remove themes/{name}.toml?", _remove)

    return theme_remove


def theme_command(reload: Reload) -> Command:
    """``/theme`` and its subcommands; ``reload`` applies an edited theme file
    (the registry passes ``reload_and_apply``). Bare ``/theme`` lists the
    subcommands."""
    async def theme_help(ui: ChatUIProtocol, args: List[str]) -> None:
        show_subcommands(ui, command, args)

    def _name(required: bool, completions=theme_name_completions) -> Param:
        return Param("NAME", required=required, completions=completions,
                     descriptions=theme_descriptions)

    subcommands: Dict[str, Command] = {
        "select": Command("theme select", "Select the color theme (picker with live preview when no name given)",
                          handler=theme_select, params=[_name(False)]),
        "edit": Command("theme edit", "Edit a theme's file; a built-in is copied out first",
                        handler=_theme_edit(reload), params=[_name(False)]),
        "create": Command("theme create", "Create a theme file from the active palette",
                          handler=_theme_create(reload), params=[Param("NAME", required=True)]),
        "path": Command("theme path", "Print (and copy) a theme's file path",
                        handler=theme_path, params=[_name(False, theme_file_completions)]),
        "remove": Command("theme remove", "Remove a theme's file (asks to confirm)",
                          handler=_theme_remove(reload),
                          params=[_name(True, theme_file_completions)]),
    }
    command = Command("theme", "Themes: select, edit, create, path, remove",
                      handler=theme_help, subcommands=subcommands)
    return command


__all__ = ["theme_command"]

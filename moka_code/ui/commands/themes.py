"""Color theme selection (``/theme``)."""

from __future__ import annotations

from typing import List

from moka_code import settings
from moka_code.ui.tui.msg_types import SysMsg, SysMsgError

from .base import ChatUIProtocol


def _valid_names() -> set[str]:
    from moka_code.ui.tui.colors import available_themes

    return set(available_themes())


def _description(name: str) -> str:
    if name in getattr(settings.config, "themes", {}):
        return "custom (themes.toml)"
    return "built-in"


def _select(ui: ChatUIProtocol, name: str) -> None:
    from moka_code.ui.tui.colors import set_theme

    if name not in _valid_names():
        ui.chat_history_panel.add_message(
            f"Unknown theme '{name}'. Available: {', '.join(sorted(_valid_names()))}",
            msg_type=SysMsgError(), title="theme")
        return
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

    names = sorted(_valid_names())
    descriptions = {name: _description(name) for name in names}
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
            modal.frame_color = theme.USER
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
        lines = [f"{name.ljust(12)} {descriptions[name]}" for name in names]
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


async def theme_command(ui: ChatUIProtocol, args: List[str]):
    """``/theme`` opens the picker; ``/theme <name>`` selects directly."""
    if not args:
        await _open_picker(ui)
        return
    _select(ui, args[0])


__all__ = ["theme_command"]

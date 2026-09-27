"""Tests for the /theme command and theme completion."""

import asyncio

import moka_chat.settings as settings
from moka_chat.ui.commands.themes import theme_command
from moka_chat.ui.tui import colors


class _Panel:
    def __init__(self):
        self.messages = []

    def add_message(self, text, msg_type=None, title=None):
        self.messages.append(text)


class _UI:
    def __init__(self):
        self.chat_history_panel = _Panel()
        self.refreshed = 0
        self.modals = []

    def refresh_status_bar(self):
        self.refreshed += 1

    def show_search_modal(self, title, items, descriptions=None, footers=None,
                          on_accept=None, on_cancel=None, on_highlight=None,
                          initial_index=0):
        self.modals.append((title, items, descriptions, footers, on_accept,
                            on_cancel, on_highlight))
        return type("M", (), {"is_visible": True})()


def test_theme_names_include_builtins():
    names = colors.theme_names()

    assert {"terminal", "pastel", "nord", "dracula", "gruvbox", "solarized",
            "one-dark", "catppuccin", "tokyo-night", "rose-pine", "everforest",
            "monokai", "ayu-dark", "kanagawa"} <= set(names)
    assert "default" not in names


def test_theme_picker_previews_and_restores(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "get_state_path", lambda: tmp_path / "state.toml")
    original = colors.theme.name
    original_active = settings.config.active_theme
    try:
        colors.set_theme("terminal")
        settings.config.active_theme = "terminal"
        ui = _UI()

        asyncio.run(theme_command(ui, []))
        _title, _items, _desc, _footers, _accept, cancel, highlight = ui.modals[0]

        highlight("nord")
        assert colors.theme.name == "nord"
        # Preview must not persist the selection.
        assert settings.config.active_theme == "terminal"

        cancel()
        assert colors.theme.name == "terminal"
    finally:
        settings.config.active_theme = original_active
        colors.set_theme(original)


def test_theme_command_selects_and_persists(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "get_state_path", lambda: tmp_path / "state.toml")
    original_theme = colors.theme.name
    original_active = settings.config.active_theme
    try:
        ui = _UI()
        asyncio.run(theme_command(ui, ["pastel"]))

        assert colors.theme.name == "pastel"
        assert settings.config.active_theme == "pastel"
        assert any("Theme: pastel" in m for m in ui.chat_history_panel.messages)
    finally:
        settings.config.active_theme = original_active
        colors.set_theme(original_theme)


def test_theme_command_unknown_reports_error():
    ui = _UI()

    asyncio.run(theme_command(ui, ["nope"]))

    assert any("Unknown theme" in m for m in ui.chat_history_panel.messages)


def test_theme_command_opens_picker(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "get_state_path", lambda: tmp_path / "state.toml")
    ui = _UI()

    asyncio.run(theme_command(ui, []))

    assert ui.modals and ui.modals[0][0] == "Themes"
    _title, items, descriptions, footers, _accept, _cancel, _highlight = ui.modals[0]
    assert "terminal" in items
    assert descriptions["terminal"] == "built-in"
    assert isinstance(footers, dict)
    assert callable(_highlight)


def test_refresh_theme_recolors_existing_message():
    from moka_chat.ui.chat_message import Message
    from moka_chat.ui.tui.msg_types import AssistantMsg

    original = colors.theme.name
    try:
        colors.set_theme("terminal")
        message = Message("hi", msg_type=AssistantMsg())
        colors.set_theme("pastel")
        message.refresh_theme()
        assert message.frame_color == colors.theme.ASSISTANT
        assert message.box.fg == colors.theme.ASSISTANT
    finally:
        colors.set_theme(original)


def test_refresh_theme_forces_full_redraw():
    from moka_chat.ui.app import chatTUI
    from conftest import StubAgent

    ui = chatTUI(StubAgent())
    calls = []
    ui.compositor = type("_C", (), {"request_full_redraw": lambda self: calls.append(True)})()
    original = colors.theme.name
    try:
        colors.set_theme("pastel")
        ui.refresh_theme()
    finally:
        colors.set_theme(original)
    assert calls


def test_modal_refresh_theme():
    from moka_chat.ui.tui.components.popup import Popup
    from moka_chat.ui.tui.components.menu import SelectionMenu

    popup = Popup()
    menu = SelectionMenu()
    original = colors.theme.name
    try:
        colors.set_theme("terminal")
        colors.set_theme("pastel")
        popup.refresh_theme()
        menu.apply_theme()
        assert popup.frame_color == colors.theme.DEFAULT
        assert popup._box.fg == colors.theme.DEFAULT
        assert menu.frame_color == colors.theme.DEFAULT
        assert menu.highlight_color == colors.theme.USER
        assert menu.bg == colors.theme.get_bg()
    finally:
        colors.set_theme(original)


def test_completion_menu_refresh_theme():
    from moka_chat.ui.commands.base import Command, Param
    from moka_chat.ui.tui.components.input.completion import ArgumentCompletion
    from moka_chat.ui.tui.components.menu import SelectionMenu

    comp = ArgumentCompletion(SelectionMenu(), {
        "demo": Command("demo", "d", params=[Param("A", completions=["x"])]),
    })
    original = colors.theme.name
    try:
        colors.set_theme("pastel")
        comp.refresh_theme()
        assert comp.menu.frame_color == colors.theme.USER
        assert comp.menu.bg == colors.theme.get_bg()
        assert comp.menu.fill_width is True
    finally:
        colors.set_theme(original)


def test_refresh_theme_recolors_status_server_model():
    from types import SimpleNamespace
    from moka_chat.ui.app import chatTUI

    endpoint = SimpleNamespace(
        name="local",
        model="qwen",
        selected_model="qwen",
        _cached_model_name="qwen",
        _connection_state="ok",
        _cached_context_window=32768,
        max_context=32768,
        _model_name_pending=False,
        _original_base_url="http://localhost:8080/v1",
    )
    agent = SimpleNamespace(endpoint=endpoint, _last_usage=None, role=None,
                            state=None, workspace=".")
    ui = chatTUI(agent)
    ui.compositor = type("_C", (), {"request_full_redraw": lambda self: None})()

    original = colors.theme.name
    try:
        colors.set_theme("pastel")
        ui.refresh_theme()
        assert ui.status_bar.field_colors["endpoint_model"] == colors.theme.SUCCESS

        colors.set_theme("terminal")
        ui.refresh_theme()
        assert ui.status_bar.field_colors["endpoint_model"] == colors.theme.SUCCESS
    finally:
        colors.set_theme(original)


def test_theme_picker_registers_preview_overlay(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "get_state_path", lambda: tmp_path / "state.toml")
    from moka_chat.ui.tui.components.theme_preview import ThemePreview

    class _Compositor:
        def __init__(self):
            self.overlays = []

        def add_overlay(self, component):
            self.overlays.append(component)

        def remove_overlay(self, component):
            self.overlays.remove(component)

        def request_render(self):
            pass

    ui = _UI()
    ui.compositor = _Compositor()
    original = colors.theme.name
    original_active = settings.config.active_theme
    try:
        asyncio.run(theme_command(ui, []))
        assert any(isinstance(o, ThemePreview) for o in ui.compositor.overlays)

        # Cancelling removes the preview overlay.
        cancel = ui.modals[0][5]
        cancel()
        assert not ui.compositor.overlays
    finally:
        settings.config.active_theme = original_active
        colors.set_theme(original)


def test_theme_preview_stays_in_top_strip():
    from moka_chat.ui.tui.buffer import Buffer
    from moka_chat.ui.tui.components.theme_preview import ThemePreview

    preview = ThemePreview()
    preview.is_visible = True
    buffer = Buffer(80, 24)
    preview.render(buffer)

    # Compact: nothing is drawn below the top strip (rows 1..4).
    for y in range(6, 24):
        assert all(cell.char == " " for cell in buffer.cells[y])


def test_theme_preview_registers_as_overlay():
    from moka_chat.ui.tui.components.theme_preview import ThemePreview

    class _Compositor:
        def __init__(self):
            self.overlays = []
            self.renders = 0

        def add_overlay(self, component):
            self.overlays.append(component)

        def remove_overlay(self, component):
            self.overlays.remove(component)

        def request_render(self):
            self.renders += 1

    compositor = _Compositor()
    preview = ThemePreview()
    preview.set_compositor(compositor)

    preview.show()
    assert preview in compositor.overlays

    preview.hide()
    assert preview not in compositor.overlays


def test_theme_preview_renders_palette_swatches():
    from moka_chat.ui.tui.buffer import Buffer
    from moka_chat.ui.tui.components.theme_preview import ThemePreview

    original = colors.theme.name
    try:
        colors.set_theme("pastel")
        preview = ThemePreview()
        preview.is_visible = True
        buffer = Buffer(60, 24)
        preview.render(buffer)

        rendered_fgs = {cell.fg for row in buffer.cells for cell in row if cell.fg}
        error = colors.theme.ERROR
        assert (error.r, error.g, error.b) in rendered_fgs
    finally:
        colors.set_theme(original)




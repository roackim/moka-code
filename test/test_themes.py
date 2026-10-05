import pytest
"""Tests for the /theme command and theme completion."""

import asyncio

import moka_code.settings as settings
from moka_code.ui.commands.themes import theme_command
from moka_code.ui.tui import colors


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

    assert {"terminal", "moka", "nord", "dracula", "gruvbox", "tokyo-night"} <= set(names)
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


def test_picking_a_theme_selects_and_persists(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "get_state_path", lambda: tmp_path / "state.toml")
    original_theme = colors.theme.name
    original_active = settings.config.active_theme
    try:
        ui = _UI()
        asyncio.run(theme_command(ui, []))
        accept = ui.modals[0][4]

        accept("moka")

        assert colors.theme.name == "moka"
        assert settings.config.active_theme == "moka"
        assert any("Theme: moka" in m for m in ui.chat_history_panel.messages)
    finally:
        settings.config.active_theme = original_active
        colors.set_theme(original_theme)


def test_theme_takes_no_argument_enter_opens_the_picker(monkeypatch, tmp_path):
    """The live preview is the point of choosing: ``/theme`` has no argument and
    no argument suggestions; any text after it is ignored."""
    from moka_code.ui.commands.registry import COMMANDS

    monkeypatch.setattr(settings, "get_state_path", lambda: tmp_path / "state.toml")
    original = colors.theme.name
    try:
        assert COMMANDS["theme"].params == []
        ui = _UI()
        asyncio.run(theme_command(ui, ["moka"]))
        assert ui.modals and colors.theme.name == original
    finally:
        colors.set_theme(original)


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
    from moka_code.ui.chat_message import Message
    from moka_code.ui.tui.msg_types import AssistantMsg

    original = colors.theme.name
    try:
        colors.set_theme("terminal")
        message = Message("hi", msg_type=AssistantMsg())
        colors.set_theme("moka")
        message.refresh_theme()
        assert message.frame_color == colors.theme.ASSISTANT
        assert message.box.fg == colors.theme.ASSISTANT
    finally:
        colors.set_theme(original)


def test_refresh_theme_recolors_markdown_segments():
    """Markdown element colors are resolved at parse time; a theme switch must
    re-resolve them, not just the message's default fg/bg."""
    from moka_code.ui.chat_message import Message
    from moka_code.ui.tui.msg_types import AssistantMsg

    original = colors.theme.name
    try:
        colors.set_theme("terminal")
        message = Message("# Title\nplain **bold** `code`\n",
                          msg_type=AssistantMsg(), render_markdown=True)
        seg = {s.text: s for s in message.component._parsed_lines[0]}
        assert seg["T"].fg is colors.terminal.HEADING

        colors.set_theme("moka")
        message.refresh_theme()
        seg = {s.text: s for s in message.component._parsed_lines[0]}
        assert seg["T"].fg is colors.theme.HEADING            # heading follows theme
        assert seg["T"].fg is not colors.terminal.HEADING
    finally:
        colors.set_theme(original)


def test_markdown_component_refresh_theme_keeps_layout():
    """Re-resolving colors re-parses but must not change the wrapping."""
    from moka_code.ui.tui.components.markdown import MarkdownComponent

    original = colors.theme.name
    try:
        colors.set_theme("terminal")
        comp = MarkdownComponent("# Title\n" + "word " * 40)
        comp.set_layout(0, 0, 30, 20)
        before = [[s.text for s in line] for line in comp._wrapped_lines]
        colors.set_theme("moka")
        comp.refresh_theme()
        after = [[s.text for s in line] for line in comp._wrapped_lines]
        assert after == before
    finally:
        colors.set_theme(original)


def test_refresh_theme_recolors_input_text():
    """The typed text is drawn with the cached content color, which the theme
    switch must re-resolve (the input is refreshed like the debug panels)."""
    from moka_code.ui.app import chatTUI
    from conftest import StubAgent

    original = colors.theme.name
    try:
        colors.set_theme("terminal")
        ui = chatTUI(StubAgent())                      # built under terminal
        colors.set_theme("moka")
        ui.refresh_theme()
        assert ui.input_component.content_color is colors.theme.DEFAULT
        assert ui.input_component.bg is colors.theme.get_bg()
    finally:
        colors.set_theme(original)


def test_refresh_theme_forces_full_redraw():
    from moka_code.ui.app import chatTUI
    from conftest import StubAgent

    ui = chatTUI(StubAgent())
    calls = []
    ui.compositor = type("_C", (), {"request_full_redraw": lambda self: calls.append(True),
                                  "request_render": lambda self: None})()
    original = colors.theme.name
    try:
        colors.set_theme("moka")
        ui.refresh_theme()
    finally:
        colors.set_theme(original)
    assert calls


def test_modal_refresh_theme():
    from moka_code.ui.tui.components.popup import Popup
    from moka_code.ui.tui.components.menu import SelectionMenu

    popup = Popup()
    menu = SelectionMenu()
    original = colors.theme.name
    try:
        colors.set_theme("terminal")
        colors.set_theme("moka")
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
    from moka_code.ui.commands.base import Command, Param
    from moka_code.ui.tui.components.input.completion import ArgumentCompletion
    from moka_code.ui.tui.components.menu import SelectionMenu

    comp = ArgumentCompletion(SelectionMenu(), {
        "demo": Command("demo", "d", params=[Param("A", completions=["x"])]),
    })
    original = colors.theme.name
    try:
        colors.set_theme("moka")
        comp.refresh_theme()
        assert comp.menu.frame_color == colors.theme.USER
        assert comp.menu.bg == colors.theme.get_bg()
        assert comp.menu.fill_width is True
    finally:
        colors.set_theme(original)


def test_refresh_theme_recolors_status_server_model(monkeypatch):
    from types import SimpleNamespace
    from moka_code import settings
    from moka_code.ui.app import chatTUI

    # Its own server, not whatever the real servers.toml holds (no notices).
    monkeypatch.setattr(settings.config, "servers", {"local": {"type": "llamacpp"}})
    monkeypatch.setattr(settings.config, "models_by_server", {})
    endpoint = SimpleNamespace(
        name="local",
        model="qwen",
        selected_model="qwen",
        _connection_state="ok",
        _original_base_url="http://localhost:8080/v1",
    )
    agent = SimpleNamespace(endpoint=endpoint, _last_usage=None, role=None,
                            state=None, workspace=".")
    ui = chatTUI(agent)
    ui.compositor = type("_C", (), {"request_full_redraw": lambda self: None})()

    original = colors.theme.name
    try:
        colors.set_theme("moka")
        ui.refresh_theme()
        assert ui.status_bar.field_colors["endpoint_model"] == colors.theme.SUCCESS

        colors.set_theme("terminal")
        ui.refresh_theme()
        assert ui.status_bar.field_colors["endpoint_model"] == colors.theme.SUCCESS
    finally:
        colors.set_theme(original)


def test_theme_picker_registers_preview_overlay(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "get_state_path", lambda: tmp_path / "state.toml")
    from moka_code.ui.tui.components.theme_preview import ThemePreview

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
    from moka_code.ui.tui.buffer import Buffer
    from moka_code.ui.tui.components.theme_preview import ThemePreview

    preview = ThemePreview()
    preview.is_visible = True
    buffer = Buffer(80, 24)
    preview.render(buffer)

    # Compact: nothing is drawn below the top strip (rows 1..4).
    for y in range(6, 24):
        assert all(cell.char == " " for cell in buffer.cells[y])


def test_theme_preview_registers_as_overlay():
    from moka_code.ui.tui.components.theme_preview import ThemePreview

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


def test_theme_preview_aligns_each_swatch_with_its_label():
    """Each color box sits over the word that names it (2026-10-01), with a gap
    between columns."""
    from moka_code.ui.tui.buffer import Buffer
    from moka_code.ui.tui.components import theme_preview as module

    original = colors.theme.name
    try:
        colors.set_theme("moka")
        preview = module.ThemePreview()
        preview.is_visible = True
        buffer = Buffer(100, 24)
        preview.render(buffer)

        swatch_row, label_row = buffer.cells[2], buffer.cells[3]
        text = "".join(cell.char for cell in label_row)
        for field, label, _start, _offset in preview._columns():
            at = text.index(label)
            swatch_x = [i for i, cell in enumerate(swatch_row) if cell.char == "█"
                        and at <= i < at + len(label)]
            assert swatch_x and len(swatch_x) == 2
            assert swatch_x[0] - at == (len(label) - 2) // 2      # centered over the label
            color = getattr(colors.theme, field)
            assert tuple(swatch_row[swatch_x[0]].fg[:3]) == (color.r, color.g, color.b)
    finally:
        colors.set_theme(original)


def test_theme_preview_renders_palette_swatches():
    from moka_code.ui.tui.buffer import Buffer
    from moka_code.ui.tui.components.theme_preview import ThemePreview

    original = colors.theme.name
    try:
        colors.set_theme("moka")
        preview = ThemePreview()
        preview.is_visible = True
        buffer = Buffer(60, 24)
        preview.render(buffer)

        rendered_fgs = {cell.fg for row in buffer.cells for cell in row if cell.fg}
        error = colors.theme.ERROR
        assert (error.r, error.g, error.b) in rendered_fgs
    finally:
        colors.set_theme(original)





# ---------------------------------------------------------------------------
# Built-in palettes stay coherent: semantic colors keep their meaning and
# everything stays legible, whatever the accents.
# ---------------------------------------------------------------------------

def _luminance(color):
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(color.r) + 0.7152 * channel(color.g) + 0.0722 * channel(color.b)


def _contrast(a, b):
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _hue(color):
    import colorsys
    return colorsys.rgb_to_hls(color.r / 255, color.g / 255, color.b / 255)[0] * 360


RGB_THEMES = [t for t in colors.BUILTIN_THEMES.values() if hasattr(t.DEFAULT, "r")]


@pytest.mark.parametrize("palette", RGB_THEMES, ids=lambda t: t.name)
def test_builtin_palettes_are_legible_and_coherent(palette):
    # Text is drawn on the theme background, or on the terminal's (black).
    backgrounds = (palette.BACKGROUND, colors.RGB("#000000"))

    def worst(color):
        return min(_contrast(color, bg) for bg in backgrounds)

    assert worst(palette.DEFAULT) >= 7                       # body text
    for key in ("ERROR", "WARNING", "SUCCESS", "HEADING", "EMPHASIS", "CODE"):
        assert worst(getattr(palette, key)) >= 4.5, key      # readable text
    assert 3 <= worst(palette.MUTED) < worst(palette.DEFAULT)   # muted, not gone
    for key in ("PERMISSION", "TOOL", "USER", "ASSISTANT", "FOCUSED"):
        assert worst(getattr(palette, key)) >= 3, key        # bars and markers

    error, warning, success = _hue(palette.ERROR), _hue(palette.WARNING), _hue(palette.SUCCESS)
    assert error >= 340 or error <= 20                        # red
    assert 25 <= warning <= 66                                # amber / yellow
    assert 70 <= success <= 170                               # green


def test_refresh_theme_keeps_user_text_in_the_normal_color():
    from moka_code.ui.chat_message import Message
    from moka_code.ui.tui.msg_types import UserMsg

    original = colors.theme.name
    try:
        colors.set_theme("terminal")
        message = Message("hi", msg_type=UserMsg())
        colors.set_theme("moka")
        message.refresh_theme()
        assert message.component.fg == colors.theme.DEFAULT
    finally:
        colors.set_theme(original)

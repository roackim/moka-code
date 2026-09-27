"""The moka banner: shown in an empty transcript, degrading with the width."""

from moka_chat import settings
from moka_chat.ui.banner import banner_lines
from moka_chat.ui.chat_history_panel import ChatHistoryPanel
from moka_chat.ui.tui.buffer import Buffer
from moka_chat.ui.tui.layout_utils import display_width
from moka_chat.ui.tui.msg_types import UserMsg


def _width(lines):
    return max(display_width(line) for line in lines)


def test_variants_degrade_with_the_width():
    full = banner_lines(200)
    letters = banner_lines(_width(full) - 1)
    assert _width(letters) < _width(full) and len(letters) < len(full)  # cup dropped
    assert banner_lines(_width(letters) - 1) == ["moka"]
    assert banner_lines(3) == []
    for width in (200, 70, 46, 10):
        assert _width(banner_lines(width) or [""]) <= width


def _screen(panel, width=90, height=16):
    panel.set_layout(0, 0, width, height)
    buffer = Buffer(width, height)
    panel.render(buffer)
    return "\n".join("".join(cell.char for cell in row) for row in buffer.cells)


def test_banner_only_in_an_empty_transcript(monkeypatch):
    monkeypatch.setattr(settings.config, "ui_show_banner", True)
    panel = ChatHistoryPanel()
    assert "█" in _screen(panel)
    panel.add_message("hello", msg_type=UserMsg())
    assert "█" not in _screen(panel)
    panel.clear()
    assert "█" in _screen(panel)
    assert panel.messages == []  # drawn, never a message (not in history/exports)


def test_banner_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(settings.config, "ui_show_banner", False)
    assert "█" not in _screen(ChatHistoryPanel())

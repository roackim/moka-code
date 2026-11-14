"""The transcript follows its end exactly when the view is at the bottom, and a
pill says when content arrived below a view that left it."""
from moka_code.ui.chat_history_panel import ChatHistoryPanel
from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.events import MouseEvent

W, H = 60, 10
UP, DOWN = 64, 65


def _panel(messages=30):
    panel = ChatHistoryPanel()
    panel.set_layout(0, 0, W, H)
    for i in range(messages):
        panel.add_message(f"msg {i}")
    buffer = Buffer(W, H)
    panel.render(buffer)
    return panel, buffer


def _wheel(panel, buffer, button):
    panel.handle_input(MouseEvent(5, 5, button, True))
    panel.render(buffer)


def test_wheel_up_on_a_transcript_that_fits_keeps_following():
    panel, buffer = _panel(messages=1)
    _wheel(panel, buffer, UP)
    assert panel.auto_scroll


def test_wheel_up_stops_following_and_back_down_resumes():
    panel, buffer = _panel()
    _wheel(panel, buffer, UP)
    assert not panel.auto_scroll
    for _ in range(20):
        if panel.auto_scroll:
            break
        _wheel(panel, buffer, DOWN)
    assert panel.auto_scroll


def test_pill_counts_new_messages_and_click_goes_to_the_end():
    panel, buffer = _panel()
    _wheel(panel, buffer, UP)
    assert panel._pill_rect is None         # nothing new yet
    panel.add_message("new A")
    panel.add_message("new B")
    panel.render(buffer)
    x, y, width = panel._pill_rect
    row = "".join(buffer.cells[y][i].char for i in range(x, x + width))
    assert "↓ 2 new · [^PgDn] end" in row
    assert panel.click_pill(MouseEvent(x + 1, y, 0, True))
    panel.render(buffer)
    assert panel.auto_scroll and panel._pill_rect is None


def test_ctrl_pgup_pgdn_go_to_start_and_end():
    panel, buffer = _panel()
    panel.scroll_to_start()
    assert not panel.auto_scroll and panel.anchored_start_y == 0
    panel.scroll_to_end()
    assert panel.auto_scroll


def test_showing_the_last_message_keeps_following():
    panel, buffer = _panel()
    panel._scroll_to_show_message(3)
    assert not panel.auto_scroll
    panel._scroll_to_show_message(len(panel.messages) - 1, prefer_top=False)
    assert panel.auto_scroll


def test_focusing_a_message_does_not_touch_following():
    panel, buffer = _panel()
    panel.set_focused_message(len(panel.messages) - 3)
    assert panel.auto_scroll

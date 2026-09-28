"""Mouse text selection in the transcript: cells in, the same cells out.

A drag leaves a selection; ``c`` copies it; moving focus cancels it."""

import pytest

from moka_code.ui.chat_history_panel import ChatHistoryPanel
from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.components.markdown import CODE_INDENT
from moka_code.ui.tui.events import MouseEvent
from moka_code.ui.tui.msg_types import AssistantMsg, UserMsg

W, H = 60, 20


_copied = []


@pytest.fixture
def copied():
    _copied.clear()
    return _copied


def _panel(*messages):
    panel = ChatHistoryPanel()
    panel.on_copy = _copied.append
    panel.set_keyboard_focus(True)
    panel.set_layout(0, 0, W, H)
    for text, kind in messages:
        msg = panel.add_message(text, msg_type=kind())
        msg.finalize()
    buffer = Buffer(W, H)
    panel.render(buffer)
    return panel, buffer


def _row_of(buffer, text):
    for y in range(H):
        row = "".join(cell.char for cell in buffer.cells[y])
        if text in row:
            return y, row.index(text)
    raise AssertionError(f"{text!r} not on screen")


def _drag(panel, start, *moves, copy=True):
    """Drag from ``start`` through ``moves``, release, then press ``c``."""
    x, y = start
    panel.handle_input(MouseEvent(x, y, 0, True, False))
    for x, y in moves:
        panel.handle_input(MouseEvent(x, y, 0, True, True))
    panel.handle_input(MouseEvent(x, y, 0, False, False))
    if copy:
        panel.handle_input("c")


def _render(panel):
    buffer = Buffer(W, H)
    panel.render(buffer)
    return buffer


def test_drag_copies_exactly_the_cells_under_the_pointer(copied):
    panel, buffer = _panel(("first line\nsecond line", UserMsg))
    y, x = _row_of(buffer, "second line")

    panel.handle_input(MouseEvent(x, y, 0, True, False))
    panel.handle_input(MouseEvent(x + 5, y, 0, True, True))
    buffer = _render(panel)                      # highlighted while dragging
    assert [buffer.cells[y][c].reverse for c in range(x - 1, x + 8)] == \
        [False] + [True] * 6 + [False, False]

    panel.handle_input(MouseEvent(x + 5, y, 0, False, False))
    assert copied == []                          # release keeps the selection
    assert _render(panel).cells[y][x].reverse

    panel.handle_input("c")
    assert copied == ["second"]
    buffer = _render(panel)                      # c copies and clears it
    assert not any(cell.reverse for cell in buffer.cells[y])


def test_first_row_of_a_message_is_selectable(copied):
    panel, buffer = _panel(("only row", UserMsg))
    y, x = _row_of(buffer, "only row")

    _drag(panel, (x, y), (x + 3, y))

    assert copied == ["only"]


def test_plain_click_copies_nothing(copied):
    panel, buffer = _panel(("hello", UserMsg))
    y, x = _row_of(buffer, "hello")

    _drag(panel, (x, y), copy=False)

    assert not panel.selection.has_selection
    assert panel.focused_message_index == 0  # the click still focuses


def test_every_drag_event_counts(copied):
    """No throttle: the last position before release is the selection end."""
    panel, buffer = _panel(("abcdefghij", UserMsg))
    y, x = _row_of(buffer, "abcdefghij")

    _drag(panel, (x, y), (x + 1, y), (x + 2, y), (x + 9, y))

    assert copied == ["abcdefghij"]


def test_selection_crosses_messages_and_skips_gutter(copied):
    panel, buffer = _panel(("question", UserMsg), ("the answer", AssistantMsg))
    qy, qx = _row_of(buffer, "question")
    ay, ax = _row_of(buffer, "the answer")

    _drag(panel, (0, qy), (ax + 2, ay))   # from the gutter column

    lines = copied[0].split("\n")
    assert lines[0] == "question"
    assert lines[-1] == "the"
    assert all(line == "" for line in lines[1:-1])  # the gap between messages


def test_release_outside_the_panel_ends_the_drag(copied):
    panel, buffer = _panel(("keep me", UserMsg))
    y, x = _row_of(buffer, "keep me")

    panel.handle_input(MouseEvent(x, y, 0, True, False))
    panel.handle_input(MouseEvent(x + 200, y, 0, True, True))   # past the edge
    panel.handle_input(MouseEvent(x + 200, H + 5, 0, False, False))
    panel.handle_input("c")

    assert not panel.selection.dragging
    assert copied == ["keep me"]


def test_wide_characters_copy_as_displayed(copied):
    panel, buffer = _panel(("日本語 ok", UserMsg))
    y, x = _row_of(buffer, "日")

    _drag(panel, (x, y), (x + 9, y))

    assert copied == ["日本語 ok"]


def test_release_confirms_the_copy(monkeypatch):
    from moka_code.ui.app import chatTUI
    from conftest import StubAgent

    monkeypatch.setattr("moka_code.ui.chat_action_handlers.copy_to_clipboard", lambda text: "xclip")
    ui = chatTUI(StubAgent())
    panel = ui.chat_history_panel
    panel.set_layout(0, 0, W, H)
    panel.add_message("confirm me", msg_type=UserMsg()).finalize()
    buffer = Buffer(W, H)
    panel.render(buffer)
    y, x = _row_of(buffer, "confirm me")

    ui._set_app_focus("history")
    _drag(panel, (x, y), (x + 6, y))

    assert ui.action_bar.hint == "copied ✓"


@pytest.mark.parametrize("move", [
    lambda panel: panel.handle_input("\x1b[A"),        # arrow to another message
    lambda panel: panel.handle_input("\x1b"),          # Esc
    lambda panel: panel.set_keyboard_focus(False),     # focus goes to the input
])
def test_moving_focus_cancels_the_selection(copied, move):
    panel, buffer = _panel(("above", UserMsg), ("below", UserMsg))
    y, x = _row_of(buffer, "below")
    _drag(panel, (x, y), (x + 3, y), copy=False)
    assert panel.selection.has_selection

    move(panel)

    assert not panel.selection.has_selection
    panel.handle_input("c")
    assert copied == []


def test_click_elsewhere_replaces_the_selection(copied):
    panel, buffer = _panel(("first", UserMsg), ("second", UserMsg))
    y1, x1 = _row_of(buffer, "first")
    y2, x2 = _row_of(buffer, "second")
    _drag(panel, (x1, y1), (x1 + 4, y1), copy=False)

    _drag(panel, (x2, y2), copy=False)                 # a plain click

    assert not panel.selection.has_selection


def test_code_block_copies_without_its_indent_or_blank_rows(copied):
    """A split code block is drawn indented between blank rows; neither is copied."""
    panel = ChatHistoryPanel()
    panel.on_copy = _copied.append
    panel.set_keyboard_focus(True)
    panel.set_layout(0, 0, W, H)
    msg = panel.add_message("Intro:\n```python\ndef a():\n    return 1\n```\nDone.",
                            msg_type=AssistantMsg())
    msg.finalize()
    panel.split_answer(msg)
    buffer = _render(panel)

    y_intro, x_intro = _row_of(buffer, "Intro:")
    y_def, x_def = _row_of(buffer, "def a():")
    y_ret, _ = _row_of(buffer, "return 1")
    y_done, _ = _row_of(buffer, "Done.")
    assert y_def == y_intro + 2 and y_done == y_ret + 2   # blank row around the code
    assert x_def == x_intro + len(CODE_INDENT)             # drawn indented

    _drag(panel, (0, y_def), (W - 1, y_ret))               # from the gutter across both lines
    assert copied == ["def a():\n    return 1"]

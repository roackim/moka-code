"""Transcript settings: thought/tool gaps and thought colors."""

from moka_code import settings
from moka_code.ui.chat_history_panel import _gap_before
from moka_code.ui.chat_message import Message
from moka_code.ui.tui.colors import theme
from moka_code.ui.tui.msg_types import AssistantMsg, ThinkingMsg, ToolCallMsg, UserMsg


def _gaps(messages):
    return [_gap_before(messages, i) for i in range(len(messages))]


def _turn():
    return [Message("q", msg_type=UserMsg()), Message("t", msg_type=ThinkingMsg()),
            Message("a", msg_type=ToolCallMsg()), Message("b", msg_type=ToolCallMsg()),
            Message("done", msg_type=AssistantMsg())]


def test_activity_stacks_without_gaps(monkeypatch):
    monkeypatch.setattr(settings.config, "ui_tool_gap", False)
    monkeypatch.setattr(settings.config, "ui_thought_gap", False)
    # The gap before the thought belongs to the tool line it sits on.
    assert _gaps(_turn()) == [False, True, False, False, True]


def test_thought_gap_sets_a_thought_apart(monkeypatch):
    monkeypatch.setattr(settings.config, "ui_tool_gap", False)
    monkeypatch.setattr(settings.config, "ui_thought_gap", True)
    monkeypatch.setattr(settings.config, "ui_stack_activity", False)
    assert _gaps(_turn()) == [False, True, True, False, True]


def test_stack_activity_keeps_thoughts_on_tool_lines(monkeypatch):
    """2026-10-29: ``stack_activity`` (on by default) drops the blank line between
    a thought and a tool line, either way round; the answer stays apart."""
    monkeypatch.setattr(settings.config, "ui_tool_gap", True)
    monkeypatch.setattr(settings.config, "ui_thought_gap", True)
    monkeypatch.setattr(settings.config, "ui_stack_activity", True)
    turn = _turn()
    turn.insert(4, Message("t2", msg_type=ThinkingMsg()))
    # user, thought, tool, tool, thought, answer
    assert _gaps(turn) == [False, True, False, True, False, True]


def test_tool_gap_sets_tool_lines_apart(monkeypatch):
    monkeypatch.setattr(settings.config, "ui_tool_gap", True)
    monkeypatch.setattr(settings.config, "ui_thought_gap", False)
    assert _gaps(_turn()) == [False, True, False, True, True]


def test_thought_colors_follow_the_fold(monkeypatch):
    monkeypatch.setattr(settings.config, "ui_thought_color", "WARNING")
    monkeypatch.setattr(settings.config, "ui_thought_expanded_color", "#112233")
    rgb = lambda color: (color.r, color.g, color.b)
    msg = Message("reasoning", msg_type=ThinkingMsg())
    assert rgb(msg.component.fg) == (0x11, 0x22, 0x33)
    assert rgb(msg.box.gutter_color) == (0x11, 0x22, 0x33)

    msg.set_collapsed(True)
    assert msg.box.gutter_color is theme.WARNING
    assert rgb(msg.component.fg) == (0x11, 0x22, 0x33)


def _first_column(msg):
    from moka_code.ui.tui.buffer import Buffer
    box = msg.get_component()
    height = box.get_preferred_height(12)
    box.set_layout(0, 0, 12, height)
    buf = Buffer(12, height)
    box.render(buf)
    return [row[0].char for row in buf.cells]


def test_bullet_stands_for_the_bar_until_focused(monkeypatch):
    """2026-10-30: unfocused, a message shows ``ui.bullet`` on its first row, or
    nothing with its ``ui.<type>_bullet`` off; the bar is for the focused one."""
    monkeypatch.setattr(settings.config, "ui_bullet", "*")
    monkeypatch.setattr(settings.config, "ui_answer_bullet", True)
    monkeypatch.setattr(settings.config, "ui_user_bullet", False)
    answer = Message("one two three four five six", msg_type=AssistantMsg(), max_width=10)
    user = Message("q", msg_type=UserMsg(), max_width=10)
    monkeypatch.setattr(settings.config, "ui_wall_char", "")
    answer.refresh_theme()
    assert _first_column(answer) == ["*", " ", " "]
    assert _first_column(user) == [" "]

    answer.set_focused(True)
    assert _first_column(answer) == ["▌"] * 3


def test_contiguous_answer_blocks_bullet_only_the_first():
    """2026-10-30: the segments of a split answer touch; only the first shows
    ``ui.bullet``. An answer after a gap gets its own."""
    from moka_code.ui.chat_history_panel import _continues_answer

    split = [Message("a", msg_type=AssistantMsg()), Message("b", msg_type=AssistantMsg())]
    split[0].group = split[1].group = object()
    turn = [Message("q", msg_type=UserMsg()), *split,
            Message("t", msg_type=ToolCallMsg()), Message("c", msg_type=AssistantMsg())]
    assert [_continues_answer(turn, i) for i in range(len(turn))] == [
        False, False, True, False, False]


def test_a_muted_wall_runs_below_the_bullet(monkeypatch):
    """2026-10-30: ``ui.wall_char`` draws a muted line below the bullet, and
    down the whole of a continued answer block; "" or " " turns it off."""
    monkeypatch.setattr(settings.config, "ui_bullet", "*")
    monkeypatch.setattr(settings.config, "ui_answer_bullet", True)
    monkeypatch.setattr(settings.config, "ui_wall_char", "|")
    answer = Message("one two three four five six", msg_type=AssistantMsg(), max_width=10)
    assert _first_column(answer) == ["*", "|", "|"]

    answer.box.bullet_continued = True
    answer.box.mark_changed()
    assert _first_column(answer) == ["|", "|", "|"]

    for off in ("", " "):
        monkeypatch.setattr(settings.config, "ui_wall_char", off)
        answer.refresh_theme()
        answer.box.bullet_continued = False
        answer.box.mark_changed()
        assert _first_column(answer) == ["*", " ", " "]

    answer.set_focused(True)
    assert _first_column(answer) == ["▌"] * 3


def test_focus_draws_the_thick_wall_on_every_row(monkeypatch):
    """2026-10-30: focused, ``ui.focus_wall_char`` replaces the bullet too, so a
    one-line message still reads as selected."""
    from moka_code.ui.chat_history_panel import ChatHistoryPanel
    from moka_code.ui.tui.buffer import Buffer

    monkeypatch.setattr(settings.config, "ui_bullet", "*")
    monkeypatch.setattr(settings.config, "ui_answer_bullet", True)
    monkeypatch.setattr(settings.config, "ui_focus_wall_char", "#")
    panel = ChatHistoryPanel()
    panel.set_layout(0, 0, 12, 8)
    panel.add_message("one line", msg_type=AssistantMsg())
    panel.set_focused_message(0)
    buf = Buffer(12, 8)
    panel.render(buf)
    column = [row[0].char for row in buf.cells]
    assert "*" not in column and column.count("#") == 1
    assert buf.cells[column.index("#")][0].fg is theme.FOCUSED

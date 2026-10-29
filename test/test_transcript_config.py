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

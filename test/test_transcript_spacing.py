"""Transcript spacing: with ``ui.group_thoughts_and_calls`` thoughts and tool
calls touch; the answer, user turns and notices keep ``ui_msg_v_margin``
blank lines unless ``ui.group_reasoning_and_answers`` joins the answer.
"""

from moka_code import settings
from moka_code.ui.chat_history_panel import ChatHistoryPanel
from moka_code.ui.tui.msg_types import AssistantMsg, ThinkingMsg, ToolCallMsg, UserMsg


def _panel():
    panel = ChatHistoryPanel()
    panel.width = 80
    return panel


def test_thoughts_and_tools_clamp_but_the_answer_does_not(monkeypatch):
    monkeypatch.setattr(settings.config, "ui_group_thoughts_and_calls", True)
    monkeypatch.setattr(settings.config, "ui_group_reasoning_and_answers", False)
    panel = _panel()
    panel.add_message("can you ls ?", msg_type=UserMsg())
    panel.add_message("", msg_type=ThinkingMsg())
    panel.add_message("", msg_type=ToolCallMsg())
    panel.add_message("answer", msg_type=AssistantMsg())

    starts, ends, _ = panel._row_index()
    gap = settings.config.ui_msg_v_margin

    # A gap separates the user turn from the assistant block...
    assert starts[1] - ends[0] == gap
    # ...thought -> tool is clamped...
    assert starts[2] == ends[1]
    # ...but the final answer keeps its gap.
    assert starts[3] - ends[2] == gap


def test_gap_separates_consecutive_user_turns():
    panel = _panel()
    panel.add_message("first", msg_type=UserMsg())
    panel.add_message("second", msg_type=UserMsg())

    starts, ends, _ = panel._row_index()
    assert starts[1] - ends[0] == settings.config.ui_msg_v_margin


def test_empty_thought_stays_a_summary_when_focused():
    """Focus expands a thought only when it has reasoning to reveal."""
    panel = _panel()
    thought = panel.add_message("", msg_type=ThinkingMsg())
    thought.set_collapsed(True)
    thought.finalize()

    panel.set_focused_message(0)

    assert thought.collapsed is True
    assert thought._thinking_label() == "thought"

def test_a_thought_unfolds_with_o_not_on_focus():
    """2026-10-30: focusing a thought only selects it; ``o`` unfolds it, like
    a tool's output, and leaving folds it back."""
    from types import SimpleNamespace
    from moka_code.ui.chat_action_handlers import ChatActionHandlers
    from moka_code.ui.tui.msg_types import MsgAction

    panel = _panel()
    thought = panel.add_message("reasoning text", msg_type=ThinkingMsg())
    panel.add_message("answer", msg_type=AssistantMsg())
    thought.set_collapsed(True)

    panel.set_focused_message(0)
    assert thought.collapsed is True
    assert MsgAction.EXPAND in thought.get_active_actions()

    ChatActionHandlers.handle_thought_action(SimpleNamespace(chat_history_panel=panel, _update_action_strip=lambda: None), thought)
    assert thought.collapsed is False
    assert MsgAction.COLLAPSE in thought.get_active_actions()

    panel.set_focused_message(1)
    assert thought.collapsed is True


def _gaps(*types):
    """Blank rows before each message after the first, for a type sequence."""
    panel = _panel()
    for msg_type in types:
        panel.add_message("x", msg_type=msg_type())
    starts, ends, _ = panel._row_index()
    return [starts[i] - ends[i - 1] for i in range(1, len(types))]


def test_both_groupings_render_one_block_after_the_question(monkeypatch):
    monkeypatch.setattr(settings.config, "ui_group_thoughts_and_calls", True)
    monkeypatch.setattr(settings.config, "ui_group_reasoning_and_answers", True)
    gap = settings.config.ui_msg_v_margin
    assert _gaps(UserMsg, ThinkingMsg, ToolCallMsg, AssistantMsg) == [gap, 0, 0]
    # prose, thought, tool: activity after prose keeps its gap.
    assert _gaps(AssistantMsg, ThinkingMsg, ToolCallMsg) == [gap, 0]

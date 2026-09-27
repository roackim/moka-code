"""Transcript spacing: thoughts and tool calls clamp; answers do not.

Adjacent clamped messages (thinking lines, tool calls) render with no
inter-message gap. Everything else — the final answer, user turns, notices —
keeps the configured ``ui_msg_v_margin`` blank lines.
"""

from moka_chat import settings
from moka_chat.ui.chat_history_panel import ChatHistoryPanel
from moka_chat.ui.tui.msg_types import AssistantMsg, ThinkingMsg, ToolCallMsg, UserMsg


def _panel():
    panel = ChatHistoryPanel()
    panel.width = 80
    return panel


def test_thoughts_and_tools_clamp_but_the_answer_does_not():
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

def test_nonempty_thought_expands_when_focused():
    panel = _panel()
    thought = panel.add_message("reasoning text", msg_type=ThinkingMsg())
    thought.set_collapsed(True)

    panel.set_focused_message(0)

    assert thought.collapsed is False


def _gaps(*types):
    """Blank rows before each message after the first, for a type sequence."""
    panel = _panel()
    for msg_type in types:
        panel.add_message("x", msg_type=msg_type())
    starts, ends, _ = panel._row_index()
    return [starts[i] - ends[i - 1] for i in range(1, len(types))]


def test_a_thought_sits_on_the_message_it_precedes():
    gap = settings.config.ui_msg_v_margin
    # tool, thought, tool: one activity block, no gaps.
    assert _gaps(ToolCallMsg, ThinkingMsg, ToolCallMsg) == [0, 0]
    # tool, thought, prose: the prose needs a gap, so it goes above the thought.
    assert _gaps(ToolCallMsg, ThinkingMsg, AssistantMsg) == [gap, 0]
    # user, thought, prose: same; the thought hugs the answer.
    assert _gaps(UserMsg, ThinkingMsg, AssistantMsg) == [gap, 0]
    # prose, thought, tool: the tool block needs a gap after prose.
    assert _gaps(AssistantMsg, ThinkingMsg, ToolCallMsg) == [gap, 0]

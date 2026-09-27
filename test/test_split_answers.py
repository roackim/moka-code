"""Split answers: prose / code block / table segments, two selection levels."""

import asyncio

from moka_chat import settings
from moka_chat.harness import events
from moka_chat.ui.answer_split import split_answer
from moka_chat.ui.chat_history_panel import ChatHistoryPanel
from moka_chat.ui.tui.msg_types import AssistantMsg, UserMsg

ANSWER = """Here is the fix:

```python
def a():
    return 1
```

And the results:

| name | value |
|------|-------|
| a    | 1     |

Done."""


def test_split_answer_segments_and_copy_text():
    segments = split_answer(ANSWER)
    assert [s.kind for s in segments] == ["text", "code", "text", "table", "text"]
    assert segments[0].text == "Here is the fix:"
    assert segments[1].text.startswith("```python") and segments[1].text.endswith("```")
    assert segments[1].copy == "def a():\n    return 1"  # no fences
    assert segments[3].copy == segments[3].text.strip()
    assert segments[4].text == "Done."


def test_only_top_level_blocks_split():
    in_list = "Steps:\n\n1. run\n   ```\n   make\n   ```\n2. check"
    assert [s.kind for s in split_answer(in_list)] == ["text"]
    no_separator = "| a | b |\n| c | d |"
    assert [s.kind for s in split_answer(no_separator)] == ["text"]


def _panel_with_answer():
    panel = ChatHistoryPanel()
    panel.width = 80
    panel.add_message("question", msg_type=UserMsg())
    answer = panel.add_message(ANSWER, msg_type=AssistantMsg())
    answer.finalize()
    panel.split_answer(answer)
    panel.add_message("next question", msg_type=UserMsg())
    return panel


def test_split_replaces_the_answer_with_touching_segments():
    panel = _panel_with_answer()
    assert len(panel.messages) == 7
    group = panel.messages[1].group
    assert group is not None and all(m.group is group for m in panel.messages[1:6])
    starts, ends, _ = panel._row_index()
    assert all(starts[i] == ends[i - 1] for i in range(2, 6))  # no gap inside
    assert starts[1] > ends[0] and starts[6] > ends[5]          # gaps around


def test_up_down_treat_an_answer_as_one_message():
    panel = _panel_with_answer()
    panel.move_focus_up()                    # last message
    assert panel.focused_message_index == 6
    panel.move_focus_up()                    # the whole answer
    assert panel.focused_message_index == 1 and not panel.inside_group
    panel.move_focus_up()
    assert panel.focused_message_index == 0
    panel.move_focus_down()
    panel.move_focus_down()
    assert panel.focused_message_index == 6


def test_right_enters_left_leaves_and_arrows_stop_at_the_edges():
    panel = _panel_with_answer()
    panel.has_keyboard_focus = True
    panel.set_focused_message(3)             # snaps to the answer's start
    assert panel.focused_message_index == 1
    assert panel.enter_group()
    assert panel.inside_group and panel.segment_label() == "text 1/5"
    assert panel.move_focus_up() is False     # stops at the first segment
    panel.move_focus_down()
    assert panel.segment_label() == "code 2/5"
    for _ in range(5):
        panel.move_focus_down()
    assert panel.segment_label() == "text 5/5"  # stops at the last one
    assert panel.handle_input("\x1b[D") and not panel.inside_group
    assert panel.focused_message_index == 1


def test_copy_is_the_whole_answer_or_the_selected_segment():
    panel = _panel_with_answer()
    panel.set_focused_message(1)
    assert panel.copy_text_for(panel.messages[1]) == ANSWER
    panel.enter_group()
    panel.move_focus_down()
    code = panel.messages[panel.focused_message_index]
    assert panel.copy_text_for(code) == "def a():\n    return 1"


def test_esc_leaves_the_segment_before_clearing_the_selection():
    panel = _panel_with_answer()
    panel.has_keyboard_focus = True
    panel.set_focused_message(1)
    panel.enter_group()
    panel.handle_input("\x1b")
    assert panel.focused_message_index == 1 and not panel.inside_group
    panel.handle_input("\x1b")
    assert panel.focused_message_index is None


def test_right_on_a_plain_message_only_hints():
    panel = _panel_with_answer()
    hints = []
    panel.on_hint = hints.append
    panel.set_focused_message(0)
    assert panel.enter_group() and not panel.inside_group
    assert hints == ["single block"]


def test_selected_segment_gets_the_wide_marker():
    from moka_chat.ui.tui.buffer import Buffer

    panel = _panel_with_answer()
    panel.set_layout(0, 0, 60, 40)
    panel.set_focused_message(1)
    panel.enter_group()
    panel.move_focus_down()
    buffer = Buffer(60, 40)
    panel.render(buffer)
    column = [row[0].char for row in buffer.cells]
    assert "█" in column


def test_answers_split_when_the_generation_ends(monkeypatch):
    from conftest import StubAgent
    from moka_chat.ui.app import chatTUI

    monkeypatch.setattr(settings.config, "ui_stream_smoothing", False)
    ui = chatTUI(StubAgent())

    async def chat(_, attached=None):
        yield events.Start(message_id="a", role="assistant")
        yield events.Token(text=ANSWER)
        yield events.Done()

    ui.agent.chat = chat
    asyncio.run(ui._process_generation("q", ui.chat_history_panel.add_message("q")))
    kinds = [m.segment_kind for m in ui.chat_history_panel.messages if m.group is not None]
    assert kinds == ["text", "code", "text", "table", "text"]


def test_click_selects_the_answer_first_then_the_part(monkeypatch):
    from moka_chat.ui.tui.buffer import Buffer
    from moka_chat.ui.tui.events import MouseEvent

    panel = _panel_with_answer()
    panel.set_layout(0, 0, 60, 40)
    panel.render(Buffer(60, 40))
    starts, _, total = panel._row_index()
    offset = max(0, total - panel.height)       # auto-scroll: bottom anchored

    def click_on(index):
        y = starts[index] - offset
        panel.handle_input(MouseEvent(5, y, 0, True, False))
        panel.handle_input(MouseEvent(5, y, 0, False, False))

    click_on(2)                                  # the code block, answer unselected
    assert panel.focused_message_index == 1 and not panel.inside_group
    click_on(2)                                  # same answer again: the part
    assert panel.focused_message_index == 2 and panel.inside_group
    click_on(4)                                  # another part of it
    assert panel.focused_message_index == 4 and panel.inside_group
    click_on(0)                                  # elsewhere, then back
    click_on(4)
    assert panel.focused_message_index == 1 and not panel.inside_group

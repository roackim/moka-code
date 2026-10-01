from moka_code.ui.chat_message import Message
from moka_code.ui.tui.msg_types import ToolCallMsg


def test_focusing_compact_single_line_message_invalidates_height_cache():
    message = Message("tool", msg_type=ToolCallMsg(), max_width=40)
    component = message.get_component()

    # In thread mode there are no borders. Unfocused, a single-line message is
    # one row tall.
    assert component.get_preferred_height(40) == 1

    message.set_focused(True)

    assert message.layout_revision == 1
    # Actions are no longer rendered inline (the app shows them in the mode
    # line), so focusing does not change the message height.
    assert component.get_preferred_height(40) == 1


def test_thread_mode_uses_role_gutter():
    from moka_code.ui.tui.msg_types import UserMsg, AssistantMsg

    user = Message("hi", msg_type=UserMsg(), max_width=40)
    moka = Message("hello", msg_type=AssistantMsg(), max_width=40)

    assert user.box.thread_mode is True
    assert user.box.gutter == "▌"
    assert moka.box.gutter == "▌"


def test_user_message_content_is_normal_but_gutter_is_user_colored():
    """User message text is normal color; the prefix bar keeps the USER color."""
    from moka_code.ui.tui.msg_types import UserMsg
    from moka_code.ui.tui.colors import theme

    msg = Message("hello", msg_type=UserMsg(), max_width=40)
    assert msg.component.fg == theme.DEFAULT  # normal text color
    assert msg.box.gutter_color == theme.USER


def test_assistant_message_gutter_is_gray():
    from moka_code.ui.tui.msg_types import AssistantMsg
    from moka_code.ui.tui.colors import theme

    msg = Message("hello", msg_type=AssistantMsg(), max_width=40)
    assert msg.box.gutter_color == theme.MUTED


def test_append_strips_leading_whitespace_on_first_chunk():
    """Streamed assistant content often opens with a space; drop it once."""
    from moka_code.ui.tui.msg_types import AssistantMsg

    msg = Message("", msg_type=AssistantMsg(), max_width=40, render_markdown=True)
    msg.append(" Hello.")
    assert msg.base_text == "Hello."

    msg.append(" More text.")
    assert msg.base_text == "Hello. More text."


def test_thinking_message_is_collapsible():
    from moka_code.ui.tui.msg_types import ThinkingMsg

    msg = Message("deep reasoning", msg_type=ThinkingMsg(), max_width=40)

    assert msg.collapsible is True
    assert msg.collapsed is False

    msg.set_collapsed(True)
    assert msg.collapsed is True
    # Collapsed messages render a single row.
    assert msg.get_component().get_preferred_height(40) == 1


def test_thinking_line_ticks_and_previews_then_summarizes(monkeypatch):
    """Live: ``thinking Ns`` plus the reasoning tail; done: ``thought for Xs``."""
    import moka_code.ui.chat_message as cm
    from moka_code.ui.tui.buffer import Buffer
    from moka_code.ui.tui.msg_types import ThinkingMsg

    clock = [100.0]
    monkeypatch.setattr(cm.time, "perf_counter", lambda: clock[0])
    msg = Message("", msg_type=ThinkingMsg(), max_width=60)
    msg.set_collapsed(True)
    msg.begin_phase("thinking")
    msg.append("first idea\nnow checking the parser")
    box = msg.get_component()
    box.set_layout(0, 0, 60, 1)

    def row():
        buf = Buffer(60, 1)
        box.render(buf)
        return "".join(c.char for c in buf.cells[0])

    clock[0] += 3.2
    live = row()
    assert "thinking 3s" in live
    assert "now checking the parser" in live
    assert not any(glyph in live for glyph in "⠋⠙✓")

    clock[0] += 1.0
    msg.finalize()
    done = row()
    assert "thought for 4.2s" in done
    assert "parser" not in done
    assert "▌" in done

def test_empty_message_keeps_minimum_row():
    """An empty message must not collapse to zero rows.

    A thought with no exposed reasoning is empty; focusing it used to un-collapse
    it to a zero-height box, so the message and its prefix vanished.
    """
    from moka_code.ui.tui.msg_types import ThinkingMsg

    msg = Message("", msg_type=ThinkingMsg(), max_width=40)
    assert msg.get_component().get_preferred_height(40) == 1


def test_waiting_label_before_any_reasoning(monkeypatch):
    """No reasoning yet: ``waiting``; a slow pre-request phase: ``preparing``."""
    import moka_code.ui.chat_message as cm
    from moka_code.ui.tui.msg_types import ThinkingMsg

    clock = [0.0]
    monkeypatch.setattr(cm.time, "perf_counter", lambda: clock[0])
    msg = Message("", msg_type=ThinkingMsg(), max_width=40)
    msg.begin_phase("processing")
    assert msg._thinking_label() == "waiting 0s"
    clock[0] = 0.7
    assert msg._thinking_label() == "preparing 0s"
    msg.begin_phase("thinking")
    clock[0] = 2.1
    assert msg._thinking_label() == "waiting 2s"

def _render_rows(msg, width, height):
    from moka_code.ui.tui.buffer import Buffer

    box = msg.get_component()
    box.set_layout(0, 0, width, height)
    buf = Buffer(width, height)
    box.render(buf)
    return ["".join(c.char for c in row) for row in buf.cells]


def test_thread_render_smoke_text_message():
    """Thread-mode render draws the gutter and the content (regression guard)."""
    from moka_code.ui.tui.msg_types import AssistantMsg

    msg = Message("hello world", msg_type=AssistantMsg(), max_width=20)
    msg.finalize()
    rows = _render_rows(msg, 20, 4)

    joined = "\n".join(rows)
    assert "hello world" in joined
    assert "▌" in joined  # heavy prefix bar (unfocused)


def test_thread_render_smoke_markdown_message():
    from moka_code.ui.tui.msg_types import UserMsg

    msg = Message("a **bold** line", msg_type=UserMsg(), max_width=24,
                  render_markdown=True)
    msg.finalize()
    rows = _render_rows(msg, 24, 4)

    joined = "\n".join(rows)
    assert "bold" in joined
    assert "▌" in joined  # heavy prefix bar (unfocused)

def test_expanded_thought_ends_on_a_blank_row_without_touching_its_text():
    """Layout only (2026-10-01): one extra row when expanded, none when folded;
    the message text is exactly what the model produced."""
    from moka_code.ui.tui.msg_types import AssistantMsg, ThinkingMsg

    thought = Message("line one\nline two", msg_type=ThinkingMsg(), max_width=40)
    answer = Message("line one\nline two", msg_type=AssistantMsg(), max_width=40)
    component = thought.get_component()

    assert component.get_preferred_height(40) == answer.get_component().get_preferred_height(40) + 1
    assert thought.base_text == "line one\nline two"
    thought.set_collapsed(True)
    assert component.get_preferred_height(40) == 1
    thought.set_collapsed(False)
    assert component.get_preferred_height(40) == answer.get_component().get_preferred_height(40) + 1
